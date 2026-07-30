from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json

from .contracts import ContentUnit
from .document_planner import CONTENT_UNITS_PATH
from .writer_policy import assess_content_unit


class ContentUnitScheduler:
    """Schedule immutable content units; writers never mutate shared plans."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        deterministic_test: bool = False,
    ) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)
        self.deterministic_test = bool(deterministic_test)

    def units(self) -> list[ContentUnit]:
        data = read_json(self.root / CONTENT_UNITS_PATH)
        rows = data.get("units") if isinstance(data, dict) else []
        return [ContentUnit.model_validate(row) for row in rows if isinstance(row, dict)]

    def initialize(self) -> list[ContentUnit]:
        units = self.units()
        for unit in units:
            current = self.store.content_unit_state(unit.unit_id) or {}
            if str(current.get("state") or "") == "completed":
                assessment = assess_content_unit(
                    self.context,
                    unit.model_dump(mode="json"),
                    current,
                    deterministic_test=self.deterministic_test,
                )
                if assessment["fresh"]:
                    continue
                stale_reason = str(
                    assessment.get("stale_reason")
                    or "写作指纹不匹配，必须重新生成。"
                )
            else:
                stale_reason = str(current.get("stale_reason") or "")
            self.store.upsert_content_unit_state(
                {
                    "unit_id": unit.unit_id,
                    "contract_revision": unit.contract_revision,
                    "state": "queued",
                    "attempt": int(current.get("attempt") or 0),
                    "evidence_snapshot_hash": self._evidence_snapshot_hash(unit),
                    "writer_fingerprint": "",
                    "output_artifact_id": None,
                    "stale_reason": stale_reason,
                    "current_chapter_id": "",
                    "current_chapter_title": "",
                    "progress_phase": "",
                    "draft_preview": "",
                }
            )
        return units

    def mark_running(self, unit: ContentUnit) -> dict:
        current = self.store.content_unit_state(unit.unit_id) or {}
        return self.store.upsert_content_unit_state(
            {
                "unit_id": unit.unit_id,
                "contract_revision": unit.contract_revision,
                "state": "running",
                "attempt": int(current.get("attempt") or 0) + 1,
                "evidence_snapshot_hash": str(
                    current.get("evidence_snapshot_hash")
                    or self._evidence_snapshot_hash(unit)
                ),
                "writer_fingerprint": "",
                "output_artifact_id": None,
                "stale_reason": str(current.get("stale_reason") or ""),
                "current_chapter_id": "",
                "current_chapter_title": "",
                "progress_phase": "",
                "draft_preview": "",
            }
        )

    def mark_failed(self, unit: ContentUnit, exc: Exception) -> dict:
        current = self.store.content_unit_state(unit.unit_id) or {}
        code = str(getattr(exc, "code", "") or "")
        phase = (
            "model_output_invalid"
            if code == "WRITER_MODEL_ACTION_REQUIRED"
            else "failed"
        )
        return self.store.upsert_content_unit_state(
            {
                "unit_id": unit.unit_id,
                "contract_revision": unit.contract_revision,
                "state": "failed",
                "attempt": int(current.get("attempt") or 1),
                "evidence_snapshot_hash": str(
                    current.get("evidence_snapshot_hash")
                    or self._evidence_snapshot_hash(unit)
                ),
                "writer_fingerprint": "",
                "output_artifact_id": None,
                "invalidation_reason": str(exc)[:2000],
                "stale_reason": str(current.get("stale_reason") or ""),
                "current_chapter_id": str(current.get("current_chapter_id") or ""),
                "current_chapter_title": str(current.get("current_chapter_title") or ""),
                "progress_phase": phase,
                "draft_preview": str(current.get("draft_preview") or ""),
            }
        )

    def mark_blocked(self, unit: ContentUnit, exc: Exception) -> dict:
        current = self.store.content_unit_state(unit.unit_id) or {}
        code = str(getattr(exc, "code", "") or "")
        if code == "WRITER_MODEL_ACTION_REQUIRED":
            phase = "model_output_invalid"
        elif code == "WRITER_RESEARCH_ACTION_REQUIRED":
            phase = "research_blocked"
        else:
            phase = "paused"
        message = str(getattr(exc, "message", "") or exc)
        return self.store.upsert_content_unit_state(
            {
                "unit_id": unit.unit_id,
                "contract_revision": unit.contract_revision,
                "state": "blocked_human",
                "attempt": int(current.get("attempt") or 1),
                "evidence_snapshot_hash": str(
                    current.get("evidence_snapshot_hash")
                    or self._evidence_snapshot_hash(unit)
                ),
                "writer_fingerprint": "",
                "output_artifact_id": None,
                "invalidation_reason": message[:2000],
                "stale_reason": str(current.get("stale_reason") or ""),
                "current_chapter_id": str(current.get("current_chapter_id") or ""),
                "current_chapter_title": str(current.get("current_chapter_title") or ""),
                "progress_phase": phase,
                "draft_preview": str(current.get("draft_preview") or ""),
            }
        )

    def ready_units(self, completed_unit_ids: set[str]) -> list[ContentUnit]:
        return [unit for unit in self.units() if set(unit.upstream_unit_ids).issubset(completed_unit_ids)]

    @staticmethod
    def _evidence_snapshot_hash(unit: ContentUnit) -> str:
        return hashlib.sha256("|".join(sorted(unit.node_ids)).encode("utf-8")).hexdigest()
