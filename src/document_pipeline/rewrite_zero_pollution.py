from __future__ import annotations

import json
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext


CORE_ARTIFACT_KINDS = (
    "RequirementLedger",
    "ScoreModel",
    "ProjectModel",
)


def audit_rewrite_zero_pollution(context: WorkspaceContext) -> dict[str, Any]:
    """Fail closed if isolated legacy identifiers enter planning artifacts/inputs."""

    store = ControlStore(context)
    if store.workspace_profile().get("project_mode") != "bid_rewrite":
        return {"status": "not_applicable", "checked": []}
    legacy = store.v3_active_artifact("LegacyBidIndex")
    if not legacy:
        raise ControlPlaneError(
            "REWRITE_LEGACY_BID_REQUIRED",
            "旧投标书尚未完成解析，无法执行零污染审计。",
            status_code=409,
        )
    legacy_payload = legacy.get("payload") or {}
    forbidden = {
        str(legacy_payload.get("legacy_bid_id") or ""),
        str(legacy_payload.get("file_hash") or ""),
        *(
            str(item.get("block_id") or "")
            for item in (legacy_payload.get("blocks") or [])
            if isinstance(item, dict)
        ),
    }
    forbidden.discard("")
    findings: list[dict[str, str]] = []
    checked: list[str] = []
    for kind in CORE_ARTIFACT_KINDS:
        artifact = store.v3_active_artifact(kind)
        if not artifact:
            continue
        checked.append(kind)
        _scan(kind, artifact.get("payload") or {}, forbidden, findings)
        proposal = store.v3_proposal(str(artifact.get("proposal_id") or "")) or {}
        for ref in proposal.get("inference_receipt_refs") or []:
            if not isinstance(ref, dict):
                continue
            receipt = store.v3_inference_receipt(str(ref.get("receipt_id") or ""))
            if receipt:
                _scan(f"{kind}.provider_input", receipt.get("input_snapshot") or "", forbidden, findings)
    if findings:
        raise ControlPlaneError(
            "REWRITE_LEGACY_BID_POLLUTION",
            "旧投标书标识进入了新目录规划链，已停止生成。",
            status_code=409,
            details={"findings": findings},
        )
    return {
        "status": "pass",
        "checked": checked,
        "forbidden_identifier_count": len(forbidden),
    }


def _scan(
    surface: str,
    value: Any,
    forbidden: set[str],
    findings: list[dict[str, str]],
) -> None:
    serialized = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    for identifier in forbidden:
        if identifier in serialized:
            findings.append({"surface": surface, "identifier": identifier})
