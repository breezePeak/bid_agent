from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import LegacyBidSource, LegacyBidSourceManifest
from .input_manifest import V3_ROOT
from .legacy_bid_index import LegacyBidIndexService
from .source_artifacts import promote_source_artifact
from .source_normalizer import NORMALIZABLE_EXTENSIONS


class LegacyBidSourceService:
    """Own old-bid files without touching InputManifest or SourceIndex."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def _require_rewrite_mode(self) -> None:
        if self.store.workspace_profile()["project_mode"] != "bid_rewrite":
            raise ControlPlaneError(
                "LEGACY_BID_MODE_REQUIRED",
                "仅标书改写工作空间可以上传旧投标书。",
                status_code=409,
            )

    def manifest(self) -> LegacyBidSourceManifest:
        active = self.store.v3_active_artifact("LegacyBidSourceManifest")
        if active is None:
            return LegacyBidSourceManifest()
        return LegacyBidSourceManifest.model_validate(active["payload"])

    def register_local_file(self, path: Path, filename: str) -> LegacyBidSource:
        self._require_rewrite_mode()
        suffix = Path(filename).suffix.lower()
        if suffix not in NORMALIZABLE_EXTENSIONS:
            raise ControlPlaneError(
                "LEGACY_BID_TYPE_UNSUPPORTED",
                "旧投标书仅支持 .docx、.pdf、.md、.txt。",
                status_code=400,
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        current = self.manifest()
        active_source = next((item for item in current.sources if item.active), None)
        if active_source is not None and active_source.sha256 == digest:
            active_index = self.store.v3_active_artifact("LegacyBidIndex")
            active_manifest = self.store.v3_active_artifact("LegacyBidSourceManifest")
            index_payload = (active_index or {}).get("payload") or {}
            if (
                not active_index
                or str(index_payload.get("legacy_bid_id") or "")
                != active_source.legacy_bid_id
                or int(index_payload.get("source_manifest_revision") or 0)
                != int((active_manifest or {}).get("revision") or 0)
            ):
                self.store.update_legacy_bid_state(
                    "parsing", active_id=active_source.legacy_bid_id
                )
                try:
                    LegacyBidIndexService(self.context).build(active_source)
                except Exception as exc:
                    self.store.update_legacy_bid_state(
                        "failed",
                        active_id=active_source.legacy_bid_id,
                        error=str(exc),
                    )
                    raise
                self.store.update_legacy_bid_state(
                    "ready", active_id=active_source.legacy_bid_id
                )
            return active_source
        version = (active_source.version + 1) if active_source else 1
        matching_source = next(
            (item for item in current.sources if item.sha256 == digest),
            None,
        )
        legacy_bid_id = (
            matching_source.legacy_bid_id
            if matching_source is not None
            else f"legacy-{digest[:20]}"
        )
        safe_name = Path(filename).name
        relative = V3_ROOT / "legacy_bid_sources" / legacy_bid_id / safe_name
        destination = self.context.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        source = LegacyBidSource(
            legacy_bid_id=legacy_bid_id,
            filename=safe_name,
            mime_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=digest,
            version=version,
            active=True,
            stored_path=relative.as_posix(),
        )
        sources = [
            item.model_copy(update={"active": False})
            for item in current.sources
            if item.legacy_bid_id != legacy_bid_id
        ]
        sources.append(source)
        active_manifest = self.store.v3_active_artifact("LegacyBidSourceManifest")
        revision = int(active_manifest["revision"]) + 1 if active_manifest else 1
        manifest = LegacyBidSourceManifest(
            revision=revision,
            source_hashes={source.legacy_bid_id: source.sha256},
            sources=sources,
        )
        promote_source_artifact(
            self.context,
            artifact_kind="LegacyBidSourceManifest",
            payload=manifest.model_dump(mode="json"),
            operation_id=f"legacy-bid-source:{legacy_bid_id}:{version}",
            gate_id="G0_LEGACY_BID_SOURCE_INTEGRITY",
        )
        self.store.update_legacy_bid_state("parsing", active_id=legacy_bid_id)
        try:
            LegacyBidIndexService(self.context).build(source)
        except Exception as exc:
            self.store.update_legacy_bid_state(
                "failed", active_id=legacy_bid_id, error=str(exc)
            )
            raise
        self.store.update_legacy_bid_state("ready", active_id=legacy_bid_id)
        return source

    def list_sources(self) -> list[LegacyBidSource]:
        self._require_rewrite_mode()
        return self.manifest().sources

    def index(self, legacy_bid_id: str):
        self._require_rewrite_mode()
        active = self.store.v3_active_artifact("LegacyBidIndex")
        if active is None:
            raise ControlPlaneError(
                "LEGACY_BID_INDEX_NOT_FOUND",
                "旧投标书尚未完成解析。",
                status_code=404,
            )
        payload = active.get("payload") or {}
        if str(payload.get("legacy_bid_id") or "") != str(legacy_bid_id):
            raise ControlPlaneError(
                "LEGACY_BID_INDEX_NOT_FOUND",
                "旧投标书索引不存在或已被替换。",
                status_code=404,
            )
        from .contracts import LegacyBidIndex

        return LegacyBidIndex.model_validate(payload)
