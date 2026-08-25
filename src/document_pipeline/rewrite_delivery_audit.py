"""Last-mile contamination audit for bid-rewrite deliveries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .chapter_rewrite_plan import _POLLUTION_PATTERNS, ChapterRewritePlanService
from .chapter_workspace import ChapterWorkspaceService
from .global_project_context import GlobalProjectContextService
from .input_manifest import V3_ROOT


_INTERNAL_MARKERS = re.compile(
    r"(?:rewrite_context|selected_legacy_sources|rewrite_schema|pollution_receipt|"
    r"plan_hash|plan_revision|pollution:[0-9a-f]{8,})",
    re.IGNORECASE,
)


class RewriteDeliveryAuditService:
    """Audit delivery text without adding rewrite internals to the document."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def require_clean(self, composed: dict[str, Any], *, delivery_kind: str) -> dict[str, Any]:
        if self.store.workspace_profile().get("project_mode") != "bid_rewrite":
            return {"status": "not_applicable", "findings": []}
        report = self.audit(composed, delivery_kind=delivery_kind)
        self._write_report(report)
        if report["findings"]:
            raise ControlPlaneError(
                "REWRITE_DELIVERY_AUDIT_FAILED",
                "最终交付仍包含旧标书污染或改写内部标记，已阻断导出",
                status_code=409,
                details={"audit": report},
            )
        return report

    def audit(self, composed: dict[str, Any], *, delivery_kind: str) -> dict[str, Any]:
        text = self._document_text(composed)
        allowed = json.dumps(GlobalProjectContextService(self.context).load(), ensure_ascii=False)
        findings: list[dict[str, str]] = []
        legacy = (self.store.v3_active_artifact("LegacyBidIndex") or {}).get("payload") or {}
        for block in legacy.get("blocks") or []:
            source = str(block.get("content") or "") if isinstance(block, dict) else ""
            for category, pattern in _POLLUTION_PATTERNS:
                for match in pattern.finditer(source):
                    value = str(match.group(0) or "").strip()
                    if value and value in text and value not in allowed:
                        findings.append({"type": category, "source_text": value})
        if _INTERNAL_MARKERS.search(text):
            findings.append({"type": "改写内部标记", "source_text": _INTERNAL_MARKERS.search(text).group(0)})
        unique = {(item["type"], item["source_text"]): item for item in findings}
        strategies = self._strategies()
        return {
            "schema_version": "v3.rewrite-delivery-audit.v1",
            "delivery_kind": delivery_kind,
            "status": "blocked" if unique else "passed",
            "document_hash": str(composed.get("document_hash") or ""),
            "chapter_strategies": strategies,
            "findings": list(unique.values()),
        }

    @staticmethod
    def _document_text(composed: dict[str, Any]) -> str:
        blocks = composed.get("blocks") if isinstance(composed.get("blocks"), list) else []
        if not blocks:
            blocks = [
                block
                for chapter in composed.get("chapters") or []
                if isinstance(chapter, dict)
                for block in chapter.get("blocks") or []
                if isinstance(block, dict)
            ]
        return "\n".join(str(block.get("content") or "") for block in blocks if isinstance(block, dict))

    def _strategies(self) -> list[dict[str, str]]:
        service = ChapterRewritePlanService(self.context)
        rows: list[dict[str, str]] = []
        for chapter in ChapterWorkspaceService(self.context).list_chapters(include_archived=False).get("items") or []:
            chapter_id = str(chapter.get("chapter_id") or "") if isinstance(chapter, dict) else ""
            if not chapter_id:
                continue
            try:
                plan = service.get(chapter_id)
            except ControlPlaneError:
                continue
            rows.append({
                "chapter_id": chapter_id,
                "plan_revision": str(plan.get("plan_revision") or ""),
                "plan_hash": str(plan.get("plan_hash") or ""),
                "strategy": str(plan.get("strategy") or "new_write"),
            })
        return rows

    def _write_report(self, report: dict[str, Any]) -> None:
        path = self.context.root / V3_ROOT / "rewrite_delivery_audit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["RewriteDeliveryAuditService"]
