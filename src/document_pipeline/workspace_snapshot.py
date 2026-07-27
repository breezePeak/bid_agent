from __future__ import annotations

from typing import Any

from control_plane import ControlStore, WorkspaceContext


class V3WorkspaceSnapshotBuilder:
    """Read-only projection of V3 artifacts and control-plane execution evidence."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> dict[str, Any]:
        control = ControlStore(self.context)
        artifacts = {item["artifact_kind"]: item for item in control.v3_promoted_artifacts()}

        def payload(kind: str) -> dict[str, Any] | None:
            value = artifacts.get(kind, {}).get("payload")
            return value if isinstance(value, dict) else None

        project_model = payload("ProjectModel")
        contract = payload("DocumentContract") or payload("TemplateStructureContract")
        plan = payload("ChapterBlueprint")
        content_blocks = payload("ContentBlock")
        quality = payload("AuditReport")
        delivery = payload("DeliveryReceipt")
        return {
            "schema_version": "v3",
            "workspace_id": self.context.workspace_id,
            "workspace_revision": control.revision(),
            # Files in workspace/v3 may be drafts or legacy compatibility
            # outputs. They are intentionally invisible here until a Receipt
            # promotes an artifact through ArtifactPromotionService.
            "inputs": payload("InputManifest"),
            "promoted_artifacts": list(artifacts.values()),
            "document": {
                "mode": (contract or {}).get("mode"),
                "contract": contract,
                "plan": plan,
                "integrated": payload("IntegratedDocument"),
                "delivery": delivery,
            },
            "project_model": project_model,
            "evidence_needs": (project_model or {}).get("evidence_needs", []),
            "materials": payload("EvidenceRepository"),
            "content_units": (content_blocks or {}).get("units", []),
            "quality": {
                "coverage": (quality or {}).get("coverage"),
                "report": quality,
                "gates": control.latest_gate_evaluations(),
            },
        }
