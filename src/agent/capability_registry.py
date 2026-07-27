"""Explicit V3 agent permissions. Agents can submit proposals, never promote facts."""

from __future__ import annotations

from dataclasses import dataclass


class CapabilityDenied(PermissionError):
    pass


@dataclass(frozen=True)
class RoleCapability:
    role: str
    proposal_kinds: frozenset[str]
    allowed_tools: frozenset[str] = frozenset()


class CapabilityRegistry:
    """The one allow-list for proposal-producing V3 roles.

    ArtifactPromotionService and GateService are services, intentionally absent
    from this registry. Adding a model provider does not alter this map.
    """

    _CAPABILITIES = {
        "requirement_agent": RoleCapability("requirement_agent", frozenset({"RequirementLedger"})),
        "score_agent": RoleCapability("score_agent", frozenset({"ScoreModel"})),
        "planning_agent": RoleCapability("planning_agent", frozenset({"ProjectModel", "ResponseTopicGraph", "ChapterBlueprint"})),
        "writer_agent": RoleCapability("writer_agent", frozenset({"ContentBlock"})),
        "integration_agent": RoleCapability("integration_agent", frozenset({"IntegrationProposal", "RepairRequest"})),
        "quality_audit_agent": RoleCapability("quality_audit_agent", frozenset({"AuditReport"})),
        "source_service": RoleCapability("source_service", frozenset({"InputManifest", "SourceIndex", "TemplateStructureContract"})),
        "evidence_service": RoleCapability("evidence_service", frozenset({"EvidenceRepository", "EvidenceSnapshot"})),
        "bundle_service": RoleCapability("bundle_service", frozenset({"WriterInputBundle"})),
        "document_integration_service": RoleCapability("document_integration_service", frozenset({"IntegratedDocument"})),
    }

    def capability_for(self, role: str) -> RoleCapability:
        capability = self._CAPABILITIES.get(str(role))
        if capability is None:
            raise CapabilityDenied(f"V3_CAPABILITY_DENIED: 未注册角色 {role}")
        return capability

    def authorize_proposal(self, role: str, artifact_kind: str) -> None:
        capability = self.capability_for(role)
        if artifact_kind not in capability.proposal_kinds:
            raise CapabilityDenied(f"V3_CAPABILITY_DENIED: {role} 不得提议 {artifact_kind}")

    def authorize_tool(self, role: str, tool_name: str) -> None:
        capability = self.capability_for(role)
        if tool_name not in capability.allowed_tools:
            raise CapabilityDenied(f"V3_TOOL_DENIED: {role} 不得调用 {tool_name}")
