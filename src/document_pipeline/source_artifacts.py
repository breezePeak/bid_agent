"""Load and promote canonical Source artifacts through the trusted kernel."""

from __future__ import annotations

from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import write_json

from .artifact_promotion import (
    AgentProposalSandbox,
    ArtifactPromotionService,
    GateService,
    build_declared_dependency_fingerprint,
    validate_and_record,
)
from .artifact_registry import ARTIFACT_REGISTRY
from .contracts import InputManifest, SourceIndex, TemplateStructureContract
from .input_manifest import MANIFEST_PATH, V3_ROOT
from .proposals import DependencyRef, ProposalEnvelope

SOURCE_INDEX_PATH = V3_ROOT / "source_index.json"
TEMPLATE_STRUCTURE_PATH = V3_ROOT / "contracts" / "template_structure.json"

SOURCE_PRODUCER = "source_service"
SOURCE_PROMPT_VERSION = "v3_source_service_v1.0"
SOURCE_MODEL_FINGERPRINT = "deterministic_v3_source_service"


def load_promoted_input_manifest(context: WorkspaceContext) -> InputManifest | None:
    active = ControlStore(context).v3_active_artifact("InputManifest")
    if active is None:
        return None
    return InputManifest.model_validate(active["payload"])


def load_promoted_source_index(context: WorkspaceContext) -> SourceIndex | None:
    active = ControlStore(context).v3_active_artifact("SourceIndex")
    if active is None:
        return None
    return SourceIndex.model_validate(active["payload"])


def load_promoted_template_structure(context: WorkspaceContext) -> TemplateStructureContract | None:
    active = ControlStore(context).v3_active_artifact("TemplateStructureContract")
    if active is None:
        return None
    return TemplateStructureContract.model_validate(active["payload"])


def require_promoted_source_index(context: WorkspaceContext) -> SourceIndex:
    index = load_promoted_source_index(context)
    if index is None:
        raise ControlPlaneError(
            "V3_SOURCE_NOT_PROMOTED",
            "SourceIndex 尚未经可信内核晋级，下游不得消费普通 source_index.json。",
            status_code=409,
        )
    return index


def derive_by_role_view(index: SourceIndex) -> dict[str, list[dict[str, Any]]]:
    """Read-only derived view. Never an authoritative write path."""
    by_role: dict[str, list[dict[str, Any]]] = {}
    for block in index.blocks:
        if block.block_kind == "ocr_gap":
            continue
        role = block.input_role.value
        by_role.setdefault(role, []).append(
            {
                "chunk_id": block.block_id,
                "input_id": block.input_id,
                "role": role,
                "ordinal": block.ordinal,
                "content": block.content,
                "source_anchor": block.source_anchor.model_dump(mode="json"),
            }
        )
    return by_role


def write_source_projection(context: WorkspaceContext, index: SourceIndex) -> None:
    """Rebuild disk projection from promoted SourceIndex (not authority)."""
    payload = index.model_dump(mode="json")
    # Explicitly non-authoritative compatibility view for legacy readers.
    payload["by_role_view"] = derive_by_role_view(index)
    payload["authority"] = "promoted_artifact_projection"
    write_json(context.root / SOURCE_INDEX_PATH, payload)


def write_manifest_projection(context: WorkspaceContext, manifest: InputManifest) -> None:
    payload = manifest.model_dump(mode="json")
    payload["authority"] = "promoted_artifact_projection"
    write_json(context.root / MANIFEST_PATH, payload)


def write_template_structure_projection(context: WorkspaceContext, structure: TemplateStructureContract) -> None:
    payload = structure.model_dump(mode="json")
    payload["authority"] = "promoted_artifact_projection"
    write_json(context.root / TEMPLATE_STRUCTURE_PATH, payload)


def _resolved_deps(context: WorkspaceContext, artifact_kind: str) -> tuple[dict[str, Any], list[DependencyRef]]:
    registration = ARTIFACT_REGISTRY.get(artifact_kind)
    store = ControlStore(context)
    resolved: dict[str, Any] = {}
    declared: list[DependencyRef] = []
    for kind in registration.dependency_kinds:
        active = store.v3_active_artifact(kind)
        if active is None:
            continue
        entry = {
            "artifact_kind": kind,
            "artifact_id": str(active["artifact_id"]),
            "revision": int(active["revision"]),
            "artifact_hash": str(active["artifact_hash"]),
        }
        resolved[kind] = entry
        declared.append(
            DependencyRef(
                artifact_kind=kind,
                expected_revision=int(active["revision"]),
                expected_hash=str(active["artifact_hash"]),
            )
        )
    return resolved, declared


def promote_source_artifact(
    context: WorkspaceContext,
    *,
    artifact_kind: str,
    payload: dict[str, Any],
    operation_id: str,
    gate_id: str,
    cited_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Submit → validate → gate → promote for source_service producers."""
    store = ControlStore(context)
    active = store.v3_active_artifact(artifact_kind)
    base_revision = int(active["revision"]) if active is not None else 0
    resolved, declared = _resolved_deps(context, artifact_kind)
    # Fail closed when required upstreams are missing.
    registration = ARTIFACT_REGISTRY.get(artifact_kind)
    for kind in registration.dependency_kinds:
        if kind not in resolved:
            raise ControlPlaneError(
                "V3_SOURCE_DEPENDENCY_MISSING",
                f"{artifact_kind} 缺少已晋级依赖 {kind}",
                status_code=409,
            )
    dep_fp = build_declared_dependency_fingerprint(
        resolved_dependency_snapshot=resolved,
        artifact_kind=artifact_kind,
        prompt_version=SOURCE_PROMPT_VERSION,
        model_fingerprint=SOURCE_MODEL_FINGERPRINT,
    )
    if active is not None and str(active.get("dependency_fingerprint") or "") == dep_fp:
        # Same upstream fingerprint: still compare payload hash for content changes.
        from .canonicalization import canonical_payload_hash

        if str(active.get("artifact_hash") or "") == canonical_payload_hash(payload):
            return active

    proposal = ProposalEnvelope(
        workspace_id=context.workspace_id,
        artifact_kind=artifact_kind,
        producer_role=SOURCE_PRODUCER,
        operation_id=operation_id,
        base_revision=base_revision,
        declared_dependencies=declared,
        dependency_fingerprint=dep_fp,
        payload=payload,
        cited_source_ids=list(cited_source_ids or []),
        prompt_version=SOURCE_PROMPT_VERSION,
        model_fingerprint=SOURCE_MODEL_FINGERPRINT,
    )
    stored = AgentProposalSandbox(context, SOURCE_PRODUCER).submit(proposal)
    proposal_id = str(stored["proposal_id"])
    report = validate_and_record(context, proposal_id)
    if not report.passed:
        raise ControlPlaneError(
            "V3_SOURCE_PROPOSAL_INVALID",
            f"{artifact_kind} Proposal 验证未通过: {report.findings}",
            status_code=409,
        )
    receipt = GateService(context).evaluate(proposal_id, gate_id=gate_id)
    if receipt.verdict != "pass":
        raise ControlPlaneError(
            "V3_SOURCE_GATE_BLOCKED",
            f"{artifact_kind} 门禁阻断: {receipt.findings}",
            status_code=409,
        )
    return ArtifactPromotionService(context).promote(proposal_id, [receipt.receipt_id]).model_dump(mode="json")
