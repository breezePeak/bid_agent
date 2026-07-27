"""Agent-callable V3 tool for resolving one declared EvidenceNeed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from control_plane import WorkspaceContext
from utils import read_json

from .contracts import EvidenceNeed
from .input_manifest import InputManifestService, V3_ROOT
from .project_model import PROJECT_MODEL_PATH
from .research_adapters import ResearchProviderAdapter, create_research_adapter
from .research_service import ResearchService


class V3ResearchTool:
    """Resolve exactly one V3 EvidenceNeed through a configured provider adapter."""

    def __init__(self, context: WorkspaceContext, provider: ResearchProviderAdapter | None = None) -> None:
        self.context = context
        self.provider = provider

    def invoke(
        self,
        need_id: str,
        *,
        provider_id: str | None = None,
        attachment_input_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        need = self._need(need_id)
        attachment_ids, attachment_paths = self._attachments(attachment_input_ids or [])
        if self.provider is not None and attachment_paths:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        provider = self.provider or create_research_adapter(
            provider_id,
            attachment_paths=attachment_paths,
        )
        batch = ResearchService(self.context, provider).resolve(need)
        return {
            "provider_id": provider.provider_id,
            "need_id": need.need_id,
            "attachment_input_ids": attachment_ids,
            "batch": batch.model_dump(mode="json"),
        }

    def _need(self, need_id: str) -> EvidenceNeed:
        model = read_json(self.context.root / PROJECT_MODEL_PATH)
        for raw in model.get("evidence_needs", []) if isinstance(model, dict) else []:
            if isinstance(raw, dict) and str(raw.get("need_id") or "") == need_id:
                return EvidenceNeed.model_validate(raw)
        raise ValueError(f"V3_UNKNOWN_EVIDENCE_NEED: {need_id}")

    def _attachments(self, raw_input_ids: list[str]) -> tuple[list[str], list[Path]]:
        if not isinstance(raw_input_ids, list):
            raise ValueError("V3_RESEARCH_ATTACHMENT_INPUT_IDS_INVALID")
        input_ids: list[str] = []
        seen: set[str] = set()
        for raw_input_id in raw_input_ids:
            if not isinstance(raw_input_id, str) or not raw_input_id.strip():
                raise ValueError("V3_RESEARCH_ATTACHMENT_INPUT_IDS_INVALID")
            input_id = raw_input_id.strip()
            if input_id not in seen:
                seen.add(input_id)
                input_ids.append(input_id)
        if not input_ids:
            return [], []

        manifest = InputManifestService(self.context).load()
        active_inputs = {item.input_id: item for item in manifest.inputs if item.active}
        source_root = (self.context.root / V3_ROOT / "sources").resolve()
        paths: list[Path] = []
        for input_id in input_ids:
            item = active_inputs.get(input_id)
            if item is None:
                raise ValueError(f"V3_RESEARCH_ATTACHMENT_NOT_ACTIVE: {input_id}")
            path = (source_root / item.input_id / item.filename).resolve()
            try:
                path.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"V3_RESEARCH_ATTACHMENT_PATH_INVALID: {input_id}") from exc
            if not path.is_file():
                raise ValueError(f"V3_RESEARCH_ATTACHMENT_MISSING: {input_id}")
            if _sha256(path) != item.sha256:
                raise ValueError(f"V3_RESEARCH_ATTACHMENT_HASH_MISMATCH: {input_id}")
            paths.append(path)
        return input_ids, paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
