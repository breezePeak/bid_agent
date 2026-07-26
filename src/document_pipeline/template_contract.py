from __future__ import annotations

import hashlib
import re
from pathlib import Path

from control_plane import WorkspaceContext
from docx import Document
from docx.text.paragraph import Paragraph
from utils import read_json

from .contracts import ContractNode, InputItem, RequirementLedger, TemplateContract, TemplateSlot
from .input_manifest import V3_ROOT
from .requirement_ledger import LEDGER_PATH


_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}|【([^】]+)】|\[([^\[\]]+)\]")
_HEADING = re.compile(r"(?:heading|标题)\s*([1-9])", re.IGNORECASE)


class TemplateContractCompiler:
    """Read the original OOXML document; never reconstruct its structure."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def compile(self, template: InputItem) -> TemplateContract:
        path = self.root / V3_ROOT / "sources" / template.input_id / template.filename
        try:
            document = Document(str(path))
        except Exception as exc:  # python-docx normalizes malformed/package errors
            raise ValueError(f"TEMPLATE_INVALID: 无法读取活动模板: {exc}") from exc
        ledger = RequirementLedger.model_validate(read_json(self.root / LEDGER_PATH))
        nodes, paragraph_node = self._nodes(document)
        if not nodes:
            raise ValueError("TEMPLATE_INVALID: 未能可靠识别模板标题结构")
        slots = self._slots(document, paragraph_node)
        blocking_gaps = self._coverage_gaps(nodes, ledger)
        return TemplateContract(
            revision=ledger.revision,
            source_hashes={**ledger.source_hashes, template.input_id: template.sha256},
            template_hash=template.sha256,
            structural_fingerprint=self._fingerprint(path),
            nodes=nodes,
            slots=slots,
            warnings=[] if slots else ["模板未声明可写 slot；仅允许在后续人工确认的 flow_slot 内填充。"],
            blocking_gaps=blocking_gaps,
        )

    @staticmethod
    def _fingerprint(path: Path) -> str:
        document = Document(str(path))
        shape = {
            "paragraphs": [
                {"style": paragraph.style.style_id if paragraph.style else ""}
                for paragraph in document.paragraphs
            ],
            "tables": [
                {"rows": len(table.rows), "columns": len(table.columns)}
                for table in document.tables
            ],
            "sections": len(document.sections),
        }
        return hashlib.sha256(repr(shape).encode("utf-8")).hexdigest()

    def _nodes(self, document: Document) -> tuple[list[ContractNode], dict[int, str]]:
        nodes: list[ContractNode] = []
        paragraph_node: dict[int, str] = {}
        parents: dict[int, str] = {}
        current_node: str | None = None
        for index, paragraph in enumerate(document.paragraphs):
            text = paragraph.text.strip()
            level = self._heading_level(paragraph)
            if not text:
                continue
            if level is None:
                if current_node:
                    paragraph_node[index] = current_node
                continue
            node_id = f"p-{index + 1}"
            parent = next((parents[candidate] for candidate in range(level - 1, 0, -1) if candidate in parents), None)
            parents[level] = node_id
            for candidate in list(parents):
                if candidate > level:
                    del parents[candidate]
            nodes.append(
                ContractNode(
                    node_id=node_id,
                    parent_node_id=parent,
                    order=len(nodes),
                    writable_target=f"paragraph:{index + 1}",
                    title=text,
                )
            )
            paragraph_node[index] = node_id
            current_node = node_id
        return nodes, paragraph_node

    @staticmethod
    def _heading_level(paragraph: Paragraph) -> int | None:
        style = paragraph.style
        style_tokens = f"{style.name} {style.style_id}" if style else ""
        matched = _HEADING.search(style_tokens)
        if matched:
            return int(matched.group(1))
        ppr = paragraph._p.pPr
        if ppr is not None and ppr.outlineLvl is not None:
            try:
                return int(ppr.outlineLvl.val) + 1
            except (TypeError, ValueError):
                return None
        return None

    def _slots(self, document: Document, paragraph_node: dict[int, str]) -> list[TemplateSlot]:
        slots: list[TemplateSlot] = []
        for index, paragraph in enumerate(document.paragraphs):
            node_id = paragraph_node.get(index)
            if not node_id:
                continue
            for match_index, match in enumerate(_PLACEHOLDER.finditer(paragraph.text)):
                slots.append(
                    TemplateSlot(
                        slot_id=f"text-p-{index + 1}-{match_index + 1}",
                        node_id=node_id,
                        kind="text_slot",
                        anchor=f"paragraph:{index + 1}:placeholder:{match.group(0)}",
                    )
                )
        for table_index, table in enumerate(document.tables):
            nearest_node = next(reversed(paragraph_node.values()), "p-1")
            for row_index, row in enumerate(table.rows):
                for cell_index, cell in enumerate(row.cells):
                    if _PLACEHOLDER.search(cell.text):
                        slots.append(
                            TemplateSlot(
                                slot_id=f"cell-t-{table_index + 1}-{row_index + 1}-{cell_index + 1}",
                                node_id=nearest_node,
                                kind="cell_slot",
                                anchor=f"table:{table_index + 1}:row:{row_index + 1}:cell:{cell_index + 1}",
                            )
                        )
        return slots

    @staticmethod
    def _coverage_gaps(nodes: list[ContractNode], ledger: RequirementLedger) -> list[str]:
        titles = " ".join(node.title.lower() for node in nodes)
        gaps: list[str] = []
        for requirement in ledger.requirements:
            terms = [term for term in re.split(r"[^\w\u4e00-\u9fff]+", requirement.normalized_requirement.lower()) if len(term) >= 2]
            if terms and not any(term in titles for term in terms):
                gaps.append(f"TEMPLATE_COVERAGE_GAP:{requirement.requirement_id}")
        return gaps
