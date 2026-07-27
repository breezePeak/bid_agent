from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from control_plane import WorkspaceContext
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from utils import write_json

from .contracts import InputItem, InputRole, NormalizedChunk, SourceAnchor, SourceBlock
from .input_manifest import InputManifestService, V3_ROOT


SOURCE_INDEX_PATH = V3_ROOT / "source_index.json"
NORMALIZABLE_EXTENSIONS = frozenset({".md", ".txt", ".docx", ".pdf"})
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+] |\d+[.)] )")


class SourceNormalizer:
    """Recover source order and anchors before any semantic Agent is invoked."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.inputs = InputManifestService(context)

    def normalize_active_inputs(self) -> dict[str, object]:
        manifest = self.inputs.load()
        by_role: dict[str, list[dict[str, object]]] = defaultdict(list)
        blocks: list[dict[str, object]] = []
        input_status: list[dict[str, object]] = []
        for item in manifest.inputs:
            if not item.active:
                continue
            source = self.root / V3_ROOT / "sources" / item.input_id / item.filename
            try:
                source_blocks = self._blocks_for(item, source)
            except ValueError as exc:
                input_status.append({"input_id": item.input_id, "status": "blocked", "reason": str(exc)})
                continue
            input_status.append({"input_id": item.input_id, "status": "processed", "block_count": len(source_blocks)})
            for block in source_blocks:
                blocks.append(block.model_dump(mode="json"))
                # Compatibility view for the existing deterministic PR-1 ledger.
                chunk = NormalizedChunk(
                    chunk_id=block.block_id,
                    input_id=block.input_id,
                    role=block.input_role,
                    ordinal=block.ordinal,
                    content=block.content,
                    source_anchor=block.source_anchor,
                )
                by_role[item.role.value].append(chunk.model_dump(mode="json"))
        blocked = [item for item in input_status if item["status"] == "blocked"]
        index: dict[str, object] = {
            "schema_version": "v3",
            "revision": manifest.revision,
            "source_hashes": manifest.source_hashes,
            "blocks": blocks,
            "by_role": dict(by_role),
            "input_status": input_status,
            "amendments": self._amendments(manifest.inputs),
        }
        write_json(self.root / SOURCE_INDEX_PATH, index)
        if blocked:
            messages = "; ".join(str(item["reason"]) for item in blocked)
            raise ValueError(f"V3_SOURCE_NORMALIZATION_BLOCKED: {messages}")
        return index

    @staticmethod
    def _amendments(items: list[InputItem]) -> list[dict[str, object]]:
        return [
            {
                "input_id": item.input_id,
                "issued_at": item.issued_at,
                "supersedes_input_ids": item.supersedes_input_ids,
            }
            for item in sorted(
                (entry for entry in items if entry.active and entry.role is InputRole.AMENDMENT),
                key=lambda entry: (str(entry.issued_at), entry.input_id),
            )
        ]

    def _blocks_for(self, item: InputItem, source: Path) -> list[SourceBlock]:
        suffix = source.suffix.lower()
        if suffix not in NORMALIZABLE_EXTENSIONS:
            supported = ", ".join(sorted(NORMALIZABLE_EXTENSIONS))
            raise ValueError(f"V3_SOURCE_FORMAT_UNSUPPORTED: {suffix or '<无扩展名>'}；支持的格式: {supported}")
        if suffix in {".md", ".txt"}:
            return self._markdown_blocks(item, source)
        if suffix == ".docx":
            return self._docx_blocks(item, source)
        return self._pdf_blocks(item, source)

    def _markdown_blocks(self, item: InputItem, source: Path) -> list[SourceBlock]:
        headings: list[str] = []
        blocks: list[SourceBlock] = []
        for paragraph_index, line in enumerate(source.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()):
            content = line.strip()
            if not content:
                continue
            heading = _MARKDOWN_HEADING.match(content)
            if heading:
                level, title = len(heading.group(1)), heading.group(2).strip()
                headings = headings[: level - 1]
                headings.append(title)
                kind = "heading"
                content = title
            else:
                kind = "list_item" if _LIST_ITEM.match(content) else "paragraph"
            blocks.append(self._block(item, source, kind, len(blocks), content, headings, paragraph_index=paragraph_index))
        return blocks

    def _docx_blocks(self, item: InputItem, source: Path) -> list[SourceBlock]:
        document = Document(str(source))
        blocks: list[SourceBlock] = []
        headings: list[str] = []
        paragraph_index = table_index = 0
        paragraphs = iter(document.paragraphs)
        tables = iter(document.tables)
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph = next(paragraphs)
                text = paragraph.text.strip()
                current_index = paragraph_index
                paragraph_index += 1
                if not text:
                    continue
                level = self._docx_heading_level(paragraph)
                if level is not None:
                    headings = headings[: level - 1]
                    headings.append(text)
                    kind = "heading"
                else:
                    kind = "list_item" if bool(paragraph.style and "list" in paragraph.style.name.lower()) else "paragraph"
                blocks.append(self._block(item, source, kind, len(blocks), text, headings, paragraph_index=current_index))
            elif child.tag.endswith("}tbl"):
                table = next(tables)
                current_table = table_index
                table_index += 1
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row.cells):
                        content = cell.text.strip()
                        if content:
                            blocks.append(self._block(
                                item, source, "table_cell", len(blocks), content, headings,
                                table_index=current_table, row_index=row_index, column_index=column_index,
                            ))
        return blocks

    @staticmethod
    def _docx_heading_level(paragraph: Paragraph) -> int | None:
        style = paragraph.style
        tokens = f"{style.name} {style.style_id}".lower() if style else ""
        matched = re.search(r"(?:heading|标题)\s*([1-9])", tokens)
        if matched:
            return int(matched.group(1))
        ppr = paragraph._p.pPr
        if ppr is not None and ppr.outlineLvl is not None:
            try:
                return int(ppr.outlineLvl.val) + 1
            except (TypeError, ValueError):
                return None
        return None

    def _pdf_blocks(self, item: InputItem, source: Path) -> list[SourceBlock]:
        try:
            import pdfplumber
        except ImportError as exc:
            raise ValueError("V3_SOURCE_PDF_UNAVAILABLE: 缺少 pdfplumber，无法可靠解析 PDF。") from exc
        blocks: list[SourceBlock] = []
        with pdfplumber.open(str(source)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if not text:
                    raise ValueError(f"V3_SOURCE_OCR_BLOCKED: PDF 第 {page_index} 页没有可提取文本。")
                for line_index, line in enumerate(text.splitlines()):
                    content = line.strip()
                    if content:
                        blocks.append(self._block(
                            item, source, "pdf_text", len(blocks), content, [], page=page_index, paragraph_index=line_index,
                        ))
                for table_index, table in enumerate(page.extract_tables() or []):
                    for row_index, row in enumerate(table):
                        for column_index, value in enumerate(row or []):
                            content = str(value or "").strip()
                            if content:
                                blocks.append(self._block(
                                    item, source, "pdf_table_cell", len(blocks), content, [], page=page_index,
                                    table_index=table_index, row_index=row_index, column_index=column_index,
                                ))
        return blocks

    @staticmethod
    def _block(
        item: InputItem,
        source: Path,
        kind: str,
        ordinal: int,
        content: str,
        heading_path: list[str],
        **coordinates: Any,
    ) -> SourceBlock:
        identity = f"{item.sha256}:{kind}:{ordinal}:{coordinates}"
        block_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        location = SourceNormalizer._location(kind, ordinal, coordinates)
        return SourceBlock(
            block_id=block_id,
            input_id=item.input_id,
            input_role=item.role,
            block_kind=kind,
            ordinal=ordinal,
            content=content,
            heading_path=list(heading_path),
            source_anchor=SourceAnchor(
                source_input_id=item.input_id,
                chunk_id=block_id,
                page=coordinates.get("page"),
                location=location,
            ),
            content_hash=content_hash,
            **coordinates,
        )

    @staticmethod
    def _location(kind: str, ordinal: int, coordinates: dict[str, Any]) -> str:
        if coordinates.get("table_index") is not None:
            return "table:{table}:row:{row}:cell:{column}".format(
                table=int(coordinates["table_index"]) + 1,
                row=int(coordinates.get("row_index") or 0) + 1,
                column=int(coordinates.get("column_index") or 0) + 1,
            )
        if coordinates.get("page") is not None:
            return f"page:{coordinates['page']}:block:{ordinal + 1}"
        return f"paragraph:{int(coordinates.get('paragraph_index') or 0) + 1}:{kind}"
