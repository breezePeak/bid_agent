from __future__ import annotations

from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import RequirementLedger


def load_promoted_requirement_ledger(context: WorkspaceContext) -> RequirementLedger:
    """Return the only runtime RequirementLedger: the active promoted revision."""
    artifact = ControlStore(context).v3_active_artifact("RequirementLedger")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "RequirementLedger 尚未晋级。", status_code=409)
    ledger = RequirementLedger.model_validate(artifact["payload"])
    if ledger.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "RequirementLedger revision 与晋级记录不一致。", status_code=409)
    return ledger


def audit_reverse_coverage(ledger: RequirementLedger, source_index: dict) -> dict[str, Any]:
    """Audit RequirementLedger against SourceIndex to ensure 100% critical source coverage."""
    covered_chunk_ids = {req.source_anchor.chunk_id for req in ledger.requirements}
    blocks = source_index.get("blocks") if isinstance(source_index.get("blocks"), list) else None
    by_role = source_index.get("by_role") if isinstance(source_index.get("by_role"), dict) else {}

    total_critical_chunks = 0
    missing_chunks: list[str] = []

    candidates: list[dict[str, Any]] = []
    if blocks is not None:
        candidates = [
            block for block in blocks
            if isinstance(block, dict)
            and block.get("input_role") in {"tender", "amendment"}
            and block.get("block_kind") != "heading"
        ]
    else:
        for role_name in ("tender", "amendment"):
            chunks = by_role.get(role_name, [])
            if isinstance(chunks, list):
                candidates.extend(chunk for chunk in chunks if isinstance(chunk, dict))
    for chunk in candidates:
        anchor = chunk.get("source_anchor") if isinstance(chunk.get("source_anchor"), dict) else {}
        chunk_id = str(anchor.get("chunk_id") or chunk.get("chunk_id") or chunk.get("block_id") or "")
        if not chunk_id:
            continue
        total_critical_chunks += 1
        if chunk_id not in covered_chunk_ids:
            missing_chunks.append(chunk_id)

    coverage_rate = 1.0 if total_critical_chunks == 0 else (total_critical_chunks - len(missing_chunks)) / total_critical_chunks

    return {
        "total_critical_chunks": total_critical_chunks,
        "covered_chunks": total_critical_chunks - len(missing_chunks),
        "missing_chunk_ids": missing_chunks,
        "coverage_rate": coverage_rate,
        "passed": coverage_rate >= 1.0,
    }
