"""Process-local, read-only snapshots of the active inference Provider policy.

The stage runner is the sole publisher in the normal execution path.  Consumers
receive immutable copies, so validation and H1 staleness checks use the exact
metadata of injected Providers instead of silently constructing default ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from control_plane import WorkspaceContext


@dataclass(frozen=True, slots=True)
class InferenceRuntimeMetadata:
    runtime_mode: str
    capability_id: str
    capability_version: str
    prompt_version: str
    prompt_hash: str
    provider_fingerprint: str
    model_fingerprint: str
    output_schema_version: str
    temperature: float

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_mode",
            "capability_id",
            "capability_version",
            "prompt_version",
            "prompt_hash",
            "provider_fingerprint",
            "model_fingerprint",
            "output_schema_version",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"InferenceRuntimeMetadata.{field_name} 不能为空")
        if not 0 <= float(self.temperature) <= 2:
            raise ValueError("InferenceRuntimeMetadata.temperature 超出允许范围")

    def as_dict(self) -> dict[str, str | float]:
        return asdict(self)


class InferenceRuntimeMetadataRegistry:
    """Thread-safe publisher with immutable read views scoped to a workspace."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_workspace: dict[
            str,
            dict[str, InferenceRuntimeMetadata],
        ] = {}

    @staticmethod
    def _workspace_key(context: WorkspaceContext) -> str:
        return str(context.root.resolve())

    def publish(
        self,
        context: WorkspaceContext,
        artifact_kind: str,
        metadata: InferenceRuntimeMetadata,
    ) -> None:
        kind = str(artifact_kind or "").strip()
        if not kind:
            raise ValueError("artifact_kind 不能为空")
        key = self._workspace_key(context)
        with self._lock:
            self._by_workspace.setdefault(key, {})[kind] = metadata

    def snapshot(
        self,
        context: WorkspaceContext,
    ) -> Mapping[str, Mapping[str, str | float]]:
        key = self._workspace_key(context)
        with self._lock:
            values = {
                kind: MappingProxyType(metadata.as_dict())
                for kind, metadata in self._by_workspace.get(key, {}).items()
            }
        return MappingProxyType(values)

    def metadata(
        self,
        context: WorkspaceContext,
        artifact_kind: str,
    ) -> Mapping[str, str | float] | None:
        return self.snapshot(context).get(artifact_kind)

    def clear(self, context: WorkspaceContext | None = None) -> None:
        """Invalidate cached Provider policy after a live config change."""

        with self._lock:
            if context is None:
                self._by_workspace.clear()
            else:
                self._by_workspace.pop(self._workspace_key(context), None)


INFERENCE_RUNTIME_REGISTRY = InferenceRuntimeMetadataRegistry()


def metadata_from_provider(
    provider: Any,
    *,
    capability_id: str | None = None,
    runtime_mode: str = "llm",
) -> InferenceRuntimeMetadata:
    resolved_capability = capability_id or getattr(
        provider,
        "capability_id",
        getattr(provider, "skill_id", ""),
    )
    return InferenceRuntimeMetadata(
        runtime_mode=runtime_mode,
        capability_id=str(resolved_capability),
        capability_version=str(provider.capability_version),
        prompt_version=str(provider.prompt_version),
        prompt_hash=str(provider.prompt_hash),
        provider_fingerprint=str(provider.provider_fingerprint),
        model_fingerprint=str(provider.model_fingerprint),
        output_schema_version=str(provider.schema_version),
        temperature=float(provider.temperature),
    )
