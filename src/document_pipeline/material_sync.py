from __future__ import annotations

from pathlib import Path

from control_plane import WorkspaceContext
from utils import write_json

from .contracts import InputRole, RequirementKind
from .input_manifest import InputManifestService, V3_ROOT
from .project_model import load_promoted_project_model
from .requirement_ledger import load_promoted_requirement_ledger


MATERIAL_REQUIREMENTS_PATH = V3_ROOT / "materials" / "requirements.json"


class MaterialRequirementsSynchronizer:
    """Derive the V3 evidence/material checklist without consulting legacy state."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def sync(self) -> dict[str, object]:
        ledger = load_promoted_requirement_ledger(self.context)
        model = load_promoted_project_model(self.context)
        manifest = InputManifestService(self.context).load()
        company_supplied = any(item.active and item.role is InputRole.COMPANY for item in manifest.inputs)

        items = [
            {
                "requirement_id": item.requirement_id,
                "requirement": item.normalized_requirement,
                "severity": item.severity,
                "source_anchor": item.source_anchor.model_dump(mode="json"),
                "requested_role": InputRole.COMPANY.value,
                "status": "provided" if company_supplied else "missing",
            }
            for item in ledger.requirements
            if item.kind is RequirementKind.QUALIFICATION
        ]
        report: dict[str, object] = {
            "schema_version": "v3",
            "revision": max(ledger.revision, model.revision),
            "company_material_supplied": company_supplied,
            "items": items,
            "summary": {
                "total": len(items),
                "provided": sum(1 for item in items if item["status"] == "provided"),
                "missing": sum(1 for item in items if item["status"] == "missing"),
                "project_unknowns": list(model.unknowns),
            },
        }
        write_json(self.root / MATERIAL_REQUIREMENTS_PATH, report)
        return report
