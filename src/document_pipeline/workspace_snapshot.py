from __future__ import annotations

from pathlib import Path
from typing import Any

from control_plane import ControlStore, WorkspaceContext
from utils import read_json

from .document_contract import DOCUMENT_CONTRACT_PATH
from .document_planner import DOCUMENT_PLAN_PATH
from .input_manifest import MANIFEST_PATH, V3_ROOT
from .integrator import INTEGRATED_DOCUMENT_PATH
from .material_sync import MATERIAL_REQUIREMENTS_PATH
from .project_model import PROJECT_MODEL_PATH
from .quality import CONTENT_QUALITY_PATH, FINAL_COVERAGE_PATH
from .renderers.render_verifier import RENDER_QUALITY_PATH


def _read_optional(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.is_file() else None


class V3WorkspaceSnapshotBuilder:
    """Read-only projection of V3 artifacts and control-plane execution evidence."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> dict[str, Any]:
        project_model = _read_optional(self.root / PROJECT_MODEL_PATH)
        contract = _read_optional(self.root / DOCUMENT_CONTRACT_PATH)
        plan = _read_optional(self.root / DOCUMENT_PLAN_PATH)
        units = [
            read_json(path)
            for path in sorted((self.root / V3_ROOT / "content_units").glob("*.json"))
            if path.is_file()
        ]
        control = ControlStore(self.context)
        return {
            "schema_version": "v3",
            "workspace_id": self.context.workspace_id,
            "workspace_revision": control.revision(),
            "inputs": _read_optional(self.root / MANIFEST_PATH),
            "document": {
                "mode": (contract or {}).get("mode"),
                "contract": contract,
                "plan": plan,
                "integrated": _read_optional(self.root / INTEGRATED_DOCUMENT_PATH),
                "delivery": _read_optional(self.root / RENDER_QUALITY_PATH),
            },
            "project_model": project_model,
            "evidence_needs": (project_model or {}).get("evidence_needs", []),
            "materials": _read_optional(self.root / MATERIAL_REQUIREMENTS_PATH),
            "content_units": units,
            "quality": {
                "coverage": _read_optional(self.root / FINAL_COVERAGE_PATH),
                "report": _read_optional(self.root / CONTENT_QUALITY_PATH),
                "gates": control.latest_gate_evaluations(),
            },
        }
