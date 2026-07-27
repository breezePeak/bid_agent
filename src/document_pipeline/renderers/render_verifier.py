from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from ..quality import CONTENT_QUALITY_PATH


RENDER_QUALITY_PATH = Path("workspace/v3/reports/render_quality.json")
RENDER_OUTPUT_PATH = Path("outputs/v3/final.docx")


class DeliveryVerifier:
    """Validate the V3-rendered DOCX and record a delivery gate decision."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def verify(self) -> dict[str, object]:
        quality = read_json(self.root / CONTENT_QUALITY_PATH)
        if quality.get("verdict") != "pass":
            raise ValueError("DELIVERY_BLOCKED: 内容质量门禁未通过")

        output = self.root / RENDER_OUTPUT_PATH
        if not output.is_file() or output.stat().st_size <= 0:
            raise ValueError("DELIVERY_BLOCKED: V3 DOCX 不存在或为空")
        try:
            document = Document(str(output))
        except Exception as exc:
            raise ValueError(f"DELIVERY_BLOCKED: V3 DOCX 无法打开: {exc}") from exc

        visible_content = sum(1 for paragraph in document.paragraphs if paragraph.text.strip())
        if visible_content == 0 and not document.tables:
            raise ValueError("DELIVERY_BLOCKED: V3 DOCX 不包含可见内容")

        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        report: dict[str, object] = {
            "schema_version": "v3",
            "status": "ready",
            "artifact_path": RENDER_OUTPUT_PATH.as_posix(),
            "artifact_sha256": digest,
            "visible_paragraphs": visible_content,
            "table_count": len(document.tables),
        }
        write_json(self.root / RENDER_QUALITY_PATH, report)
        ControlStore(self.context).record_gate_evaluation(
            command="verify_delivery",
            verdict="pass",
            input_fingerprint=digest,
            findings=[],
            source="v3.render_verifier",
        )
        return report
