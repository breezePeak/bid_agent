from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json

from .quality import CONTENT_QUALITY_PATH
from .contracts import DOCUMENT_CONTRACT_ADAPTER
from .document_contract import DOCUMENT_CONTRACT_PATH
from .renderers.render_verifier import (
    RENDER_MARKDOWN_PATH,
    RENDER_OUTPUT_PATH,
    RENDER_QUALITY_PATH,
)
from .writer_policy import require_all_content_units_fresh


FINAL_MARKDOWN_PATH = RENDER_MARKDOWN_PATH
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table_cells(line: str) -> list[str]:
    text = line.strip().strip("|")
    return [cell.strip().replace(r"\|", "|") for cell in text.split("|")]


def parse_markdown_preview(markdown: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    toc: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    section_stack: list[dict[str, Any]] = []
    current_section_id = ""
    lines = markdown.splitlines()
    index = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        blocks.append(
            {
                "id": f"block-{len(blocks) + 1}",
                "type": "paragraph",
                "section_id": current_section_id,
                "text": "\n".join(paragraph).strip(),
            }
        )
        paragraph.clear()

    while index < len(lines):
        line = lines[index]
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            section_id = f"section-{len(toc) + 1}"
            while section_stack and int(section_stack[-1]["level"]) >= level:
                section_stack.pop()
            parent_id = (
                str(section_stack[-1]["id"]) if section_stack else None
            )
            entry = {
                "id": section_id,
                "title": title,
                "level": level,
                "parent_id": parent_id,
            }
            toc.append(entry)
            section_stack.append(entry)
            current_section_id = section_id
            blocks.append(
                {
                    "id": section_id,
                    "type": "heading",
                    "section_id": section_id,
                    "level": level,
                    "text": title,
                }
            )
            index += 1
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            flush_paragraph()
            rows = [_table_cells(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_table_cells(lines[index]))
                index += 1
            blocks.append(
                {
                    "id": f"block-{len(blocks) + 1}",
                    "type": "table",
                    "section_id": current_section_id,
                    "rows": rows,
                }
            )
            continue
        list_match = _LIST_RE.match(line)
        if list_match:
            flush_paragraph()
            items: list[str] = []
            while index < len(lines):
                matched = _LIST_RE.match(lines[index])
                if not matched:
                    break
                items.append(matched.group(1).strip())
                index += 1
            blocks.append(
                {
                    "id": f"block-{len(blocks) + 1}",
                    "type": "list",
                    "section_id": current_section_id,
                    "items": items,
                }
            )
            continue
        if not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
        index += 1
    flush_paragraph()
    if not toc and blocks:
        toc.append(
            {
                "id": "document-start",
                "title": "标书全文",
                "level": 1,
                "parent_id": None,
            }
        )
        blocks.insert(
            0,
            {
                "id": "document-start",
                "type": "heading",
                "section_id": "document-start",
                "level": 1,
                "text": "标书全文",
            },
        )
    return toc, blocks


class DocumentPreviewService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def build(self) -> dict[str, Any]:
        require_all_content_units_fresh(
            self.context,
            code="DOCUMENT_PREVIEW_STALE",
        )
        markdown_path = self.root / FINAL_MARKDOWN_PATH
        docx_path = self.root / RENDER_OUTPUT_PATH
        report_path = self.root / RENDER_QUALITY_PATH
        report = read_json(report_path) if report_path.is_file() else {}
        if (
            str(report.get("status") or "") != "ready"
            or not markdown_path.is_file()
            or not docx_path.is_file()
        ):
            raise ControlPlaneError(
                "DOCUMENT_PREVIEW_NOT_READY",
                "完整标书尚未完成 Word 渲染和交付验证。",
                status_code=409,
            )
        operation_id = self._latest_generation_operation_id()
        report_operation_id = str(report.get("operation_id") or "")
        if (
            operation_id
            and report_operation_id
            and report_operation_id != operation_id
        ):
            raise ControlPlaneError(
                "DOCUMENT_PREVIEW_STALE",
                "交付验证记录不属于当前生成任务。",
                status_code=409,
            )
        if operation_id:
            stage_runs = self.store.stage_runs(operation_id)
            current_runs = {}
            for stage_id in ("render_document", "verify_delivery"):
                attempts = [
                    item
                    for item in stage_runs
                    if str(item.get("stage_command") or "") == stage_id
                ]
                current_runs[stage_id] = (
                    max(
                        attempts,
                        key=lambda item: int(item.get("attempt") or 0),
                    )
                    if attempts
                    else None
                )
            if any(
                not item
                or str(item.get("status") or "")
                not in {"succeeded", "reused"}
                for item in current_runs.values()
            ):
                raise ControlPlaneError(
                    "DOCUMENT_PREVIEW_STALE",
                    "当前生成任务尚未完成 Word 渲染与交付验证，旧标书不会作为本次结果展示。",
                    status_code=409,
                )
        expected_docx_hash = str(report.get("artifact_sha256") or "")
        actual_docx_hash = _sha256(docx_path)
        if expected_docx_hash and expected_docx_hash != actual_docx_hash:
            raise ControlPlaneError(
                "DOCUMENT_PREVIEW_HASH_MISMATCH",
                "Word 文件与交付验证记录不一致。",
                status_code=409,
            )
        expected_markdown_hash = str(report.get("markdown_sha256") or "")
        actual_markdown_hash = _sha256(markdown_path)
        if (
            expected_markdown_hash
            and expected_markdown_hash != actual_markdown_hash
        ):
            raise ControlPlaneError(
                "DOCUMENT_PREVIEW_HASH_MISMATCH",
                "Markdown 文件与交付验证记录不一致。",
                status_code=409,
            )
        markdown = markdown_path.read_text(encoding="utf-8")
        toc, blocks = parse_markdown_preview(markdown)
        contract_payload = (
            read_json(self.root / DOCUMENT_CONTRACT_PATH)
            if (self.root / DOCUMENT_CONTRACT_PATH).is_file()
            else {}
        )
        try:
            contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(
                contract_payload
            )
            contract_nodes = list(contract.nodes)
        except Exception:
            contract_nodes = []
        for entry, node in zip(toc, contract_nodes):
            entry["chapter_id"] = node.node_id
            entry["section_domain"] = node.section_domain
            entry["content_policy"] = node.content_policy
            entry["deferred_reason"] = node.deferred_reason
        quality_path = self.root / CONTENT_QUALITY_PATH
        quality = read_json(quality_path) if quality_path.is_file() else {}
        warnings = [
            item
            for item in (
                report.get("warnings")
                or quality.get("findings")
                or []
            )
            if isinstance(item, dict)
        ]
        return {
            "status": str(report.get("status") or "ready"),
            "operation_id": operation_id,
            "mode": self._document_mode(),
            "markdown_sha256": actual_markdown_hash,
            "docx_sha256": actual_docx_hash,
            "warning_count": len(warnings),
            "warnings": warnings,
            "toc": toc,
            "blocks": blocks,
        }

    def _latest_generation_operation_id(self) -> str:
        commands = [
            item
            for item in (self.store.snapshot().get("commands") or [])
            if isinstance(item, dict)
            and str(item.get("kind") or "") == "document.run_pipeline"
        ]
        if not commands:
            return ""
        latest = max(
            commands,
            key=lambda item: str(
                item.get("updated_at") or item.get("created_at") or ""
            ),
        )
        return str(latest.get("operation_id") or "")

    def _document_mode(self) -> str:
        artifact = self.store.v3_active_artifact("DocumentContract")
        payload = artifact.get("payload") if isinstance(artifact, dict) else {}
        return str((payload or {}).get("mode") or "")
