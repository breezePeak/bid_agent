from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json

from .contracts import ContentUnit
from .document_planner import CONTENT_UNITS_PATH


class ContentUnitScheduler:
    """Schedule immutable content units; writers never mutate shared plans."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def units(self) -> list[ContentUnit]:
        data = read_json(self.root / CONTENT_UNITS_PATH)
        rows = data.get("units") if isinstance(data, dict) else []
        return [ContentUnit.model_validate(row) for row in rows if isinstance(row, dict)]

    def initialize(self) -> list[ContentUnit]:
        units = self.units()
        for unit in units:
            self.store.upsert_content_unit_state(
                {
                    "unit_id": unit.unit_id,
                    "contract_revision": unit.contract_revision,
                    "state": "queued",
                    "evidence_snapshot_hash": self._evidence_snapshot_hash(unit),
                }
            )
        return units

    def ready_units(self, completed_unit_ids: set[str]) -> list[ContentUnit]:
        return [unit for unit in self.units() if set(unit.upstream_unit_ids).issubset(completed_unit_ids)]

    @staticmethod
    def _evidence_snapshot_hash(unit: ContentUnit) -> str:
        return hashlib.sha256("|".join(sorted(unit.node_ids)).encode("utf-8")).hexdigest()
