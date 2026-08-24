from __future__ import annotations

from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import InputRole
from .input_manifest import InputManifestService


class MaterialReadinessService:
    """Project-mode aware readiness without joining legacy bids to InputManifest."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def snapshot(self) -> dict[str, Any]:
        mode = str(self.store.workspace_profile().get("project_mode") or "full_write")
        manifest = InputManifestService(self.context).load()
        active_roles = {
            item.role
            for item in manifest.inputs
            if item.active
        }
        tender_ready = InputRole.TENDER in active_roles
        company_ready = InputRole.COMPANY in active_roles
        legacy_ready = self._legacy_bid_ready()
        if mode == "bid_rewrite":
            required = ("tender", "legacy_bid")
            ready = tender_ready and legacy_ready
        else:
            # Preserve the existing full-write UI readiness contract.
            required = ("tender", "company")
            ready = tender_ready and company_ready
        return {
            "project_mode": mode,
            "ready": ready,
            "required": list(required),
            "items": {
                "tender": {"required": True, "ready": tender_ready},
                "legacy_bid": {
                    "required": mode == "bid_rewrite",
                    "ready": legacy_ready,
                },
                "company": {
                    "required": mode == "full_write",
                    "ready": company_ready,
                },
            },
        }

    def require_outline_ready(self) -> dict[str, Any]:
        state = self.snapshot()
        if state["project_mode"] != "bid_rewrite":
            return state
        if not state["items"]["tender"]["ready"]:
            raise ControlPlaneError(
                "REWRITE_TENDER_REQUIRED",
                "标书改写需要先上传一份活动的新招标书。",
                status_code=409,
            )
        if not state["items"]["legacy_bid"]["ready"]:
            raise ControlPlaneError(
                "REWRITE_LEGACY_BID_REQUIRED",
                "标书改写需要先上传并完成解析旧投标书。",
                status_code=409,
            )
        return state

    def _legacy_bid_ready(self) -> bool:
        state = self.store.legacy_bid_state()
        if str(state.get("status") or "") != "ready":
            return False
        manifest = self.store.v3_active_artifact("LegacyBidSourceManifest")
        index = self.store.v3_active_artifact("LegacyBidIndex")
        if not manifest or not index:
            return False
        sources = (manifest.get("payload") or {}).get("sources") or []
        active = next(
            (item for item in sources if isinstance(item, dict) and item.get("active")),
            None,
        )
        payload = index.get("payload") or {}
        return bool(
            active
            and str(payload.get("legacy_bid_id") or "")
            == str(active.get("legacy_bid_id") or "")
            and int(payload.get("source_manifest_revision") or 0)
            == int(manifest.get("revision") or 0)
        )
