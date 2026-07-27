"""Versioned registry of V3 artifact kinds that may enter the trusted write path.

Only kinds marked enabled+promotable with a real payload schema and Gate policy
may promote. Unknown, disabled, or schema-less kinds fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .contracts import (
    ChapterBlueprint,
    InputManifest,
    ProjectModel,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreModel,
    SourceIndex,
    TemplateStructureContract,
)

ARTIFACT_REGISTRY_VERSION = "v3-artifact-registry-2"


@dataclass(frozen=True)
class ArtifactKindRegistration:
    kind: str
    payload_model: type[BaseModel]
    legal_producers: frozenset[str]
    dependency_kinds: tuple[str, ...]
    enabled: bool
    promotable: bool
    payload_schema_version: str = "v3"
    notes: str = ""

    def is_promotable(self) -> bool:
        return self.enabled and self.promotable


class ArtifactKindRegistry:
    """Authoritative list of currently registered artifact kinds."""

    VERSION = ARTIFACT_REGISTRY_VERSION

    def __init__(self) -> None:
        self._kinds: dict[str, ArtifactKindRegistration] = {
            "InputManifest": ArtifactKindRegistration(
                kind="InputManifest",
                payload_model=InputManifest,
                legal_producers=frozenset({"source_service"}),
                dependency_kinds=(),
                enabled=True,
                promotable=True,
                notes="PR-16.1 canonical Source root.",
            ),
            "SourceIndex": ArtifactKindRegistration(
                kind="SourceIndex",
                payload_model=SourceIndex,
                legal_producers=frozenset({"source_service"}),
                dependency_kinds=("InputManifest",),
                enabled=True,
                promotable=True,
                notes="PR-16.1 structured recovery of frozen inputs.",
            ),
            "TemplateStructureContract": ArtifactKindRegistration(
                kind="TemplateStructureContract",
                payload_model=TemplateStructureContract,
                legal_producers=frozenset({"source_service"}),
                dependency_kinds=("InputManifest",),
                enabled=True,
                promotable=True,
                notes="PR-16.1 optional strict-template topology.",
            ),
            "RequirementLedger": ArtifactKindRegistration(
                kind="RequirementLedger",
                payload_model=RequirementLedger,
                legal_producers=frozenset({"requirement_agent"}),
                # StageRunner enforces promoted SourceIndex before extraction.
                # Fingerprint still binds parser/source via agent-declared claim inputs.
                dependency_kinds=(),
                enabled=True,
                promotable=True,
                notes="Consumers must read promoted SourceIndex; hard dep remains stage-enforced.",
            ),
            "ScoreModel": ArtifactKindRegistration(
                kind="ScoreModel",
                payload_model=ScoreModel,
                legal_producers=frozenset({"score_agent"}),
                dependency_kinds=("RequirementLedger",),
                enabled=True,
                promotable=True,
            ),
            "ProjectModel": ArtifactKindRegistration(
                kind="ProjectModel",
                payload_model=ProjectModel,
                legal_producers=frozenset({"planning_agent"}),
                dependency_kinds=("RequirementLedger", "ScoreModel"),
                enabled=True,
                promotable=True,
            ),
            "ResponseTopicGraph": ArtifactKindRegistration(
                kind="ResponseTopicGraph",
                payload_model=ResponseTopicGraph,
                legal_producers=frozenset({"planning_agent"}),
                dependency_kinds=("RequirementLedger", "ScoreModel", "ProjectModel"),
                enabled=True,
                promotable=True,
            ),
            "ChapterBlueprint": ArtifactKindRegistration(
                kind="ChapterBlueprint",
                payload_model=ChapterBlueprint,
                legal_producers=frozenset({"planning_agent"}),
                dependency_kinds=("ResponseTopicGraph",),
                enabled=True,
                promotable=True,
            ),
        }

    def get(self, kind: str) -> ArtifactKindRegistration:
        registration = self._kinds.get(str(kind))
        if registration is None:
            raise KeyError(f"V3_ARTIFACT_KIND_UNKNOWN: {kind}")
        return registration

    def require_promotable(self, kind: str) -> ArtifactKindRegistration:
        registration = self.get(kind)
        if not registration.is_promotable():
            raise PermissionError(f"V3_ARTIFACT_KIND_DISABLED: {kind} 未启用或不可晋级")
        return registration

    def validate_payload(self, kind: str, payload: dict[str, Any]) -> BaseModel:
        registration = self.require_promotable(kind)
        if not isinstance(payload, dict):
            raise ValueError(f"{kind} payload 必须是对象")
        if payload == {}:
            raise ValueError(f"{kind} payload 不能是空对象 {{}}")
        return registration.payload_model.model_validate(payload)

    def enabled_promotable_kinds(self) -> list[str]:
        return sorted(kind for kind, reg in self._kinds.items() if reg.is_promotable())

    def all_kinds(self) -> list[str]:
        return sorted(self._kinds)


ARTIFACT_REGISTRY = ArtifactKindRegistry()
