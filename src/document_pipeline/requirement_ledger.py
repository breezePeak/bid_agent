from __future__ import annotations

import re
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import RequirementLedger

_OBLIGATION_MARKERS = (
    "应当", "必须", "须", "应", "不得", "禁止", "需要", "要求", "提供", "具备", "保证", "确保", "提交",
)


def load_promoted_requirement_ledger(context: WorkspaceContext) -> RequirementLedger:
    """Return the only runtime RequirementLedger: the active promoted revision."""
    artifact = ControlStore(context).v3_active_artifact("RequirementLedger")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "RequirementLedger 尚未晋级。", status_code=409)
    ledger = RequirementLedger.model_validate(artifact["payload"])
    if ledger.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "RequirementLedger revision 与晋级记录不一致。", status_code=409)
    return ledger


def _atomic_statements(content: str) -> list[str]:
    normalized = content.replace("；", "。").replace(";", "。")
    parts: list[str] = []
    for line in normalized.splitlines():
        for piece in re.split(r"[。！？]", line):
            text = piece.strip(" -•\t")
            if len(text) >= 2:
                parts.append(text)
    return parts


def _looks_like_obligation(statement: str) -> bool:
    if any(marker in statement for marker in _OBLIGATION_MARKERS):
        return True
    if re.search(r"\d", statement) and any(unit in statement for unit in ("天", "日", "月", "%", "分", "人")):
        return True
    return False


def audit_reverse_coverage(ledger: RequirementLedger, source_index: dict) -> dict[str, Any]:
    """Detect missing obligations inside blocks; not satisfied by one Requirement per block."""
    requirements = [
        req
        for req in ledger.requirements
        if req.status != "waived"
    ]
    req_texts = [re.sub(r"\s+", "", req.normalized_requirement) for req in requirements]
    covered_chunk_ids = {req.source_anchor.chunk_id for req in requirements}

    blocks = source_index.get("blocks") if isinstance(source_index.get("blocks"), list) else []
    candidates = [
        block
        for block in blocks
        if isinstance(block, dict)
        and block.get("input_role") in {"tender", "amendment"}
        and block.get("block_kind") not in {"heading", "ocr_gap", "table"}
    ]

    missing_chunk_ids: list[str] = []
    missing_obligations: list[dict[str, Any]] = []
    total_obligation_statements = 0
    covered_obligation_statements = 0

    for block in candidates:
        content = str(block.get("content") or "")
        block_id = str(block.get("block_id") or "")
        anchor = block.get("source_anchor") if isinstance(block.get("source_anchor"), dict) else {}
        chunk_id = str(anchor.get("chunk_id") or block.get("chunk_id") or block_id or "")
        statements = [stmt for stmt in _atomic_statements(content) if _looks_like_obligation(stmt)]
        if not statements:
            continue

        block_has_any_req = chunk_id in covered_chunk_ids or block_id in covered_chunk_ids
        if not block_has_any_req:
            missing_chunk_ids.append(chunk_id or block_id)

        for ordinal, stmt in enumerate(statements):
            total_obligation_statements += 1
            compact = re.sub(r"\s+", "", stmt)
            matched = any(compact in req_text or req_text in compact or _token_overlap(compact, req_text) >= 0.5 for req_text in req_texts)
            if matched:
                covered_obligation_statements += 1
            else:
                missing_obligations.append(
                    {
                        "block_id": block_id,
                        "chunk_id": chunk_id,
                        "ordinal": ordinal,
                        "statement": stmt[:160],
                    }
                )

    statement_coverage = (
        1.0
        if total_obligation_statements == 0
        else covered_obligation_statements / total_obligation_statements
    )
    # Pass requires: every critical block with obligations has >=1 requirement AND no missed obligation statements.
    passed = not missing_chunk_ids and not missing_obligations

    return {
        "total_critical_chunks": len(candidates),
        "covered_chunks": len(candidates) - len(missing_chunk_ids),
        "missing_chunk_ids": missing_chunk_ids,
        "total_obligation_statements": total_obligation_statements,
        "covered_obligation_statements": covered_obligation_statements,
        "missing_obligations": missing_obligations,
        "coverage_rate": statement_coverage,
        "passed": passed,
        "policy": "statement_level_v1",
    }


def _token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa = {a[i : i + 2] for i in range(max(len(a) - 1, 1))}
    sb = {b[i : i + 2] for i in range(max(len(b) - 1, 1))}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
