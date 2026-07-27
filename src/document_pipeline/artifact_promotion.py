"""The only V3 path from a candidate proposal to a promoted artifact."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.capability_registry import CapabilityRegistry
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .proposals import GateReceipt, PromotionReceipt, ProposalEnvelope, ValidationFinding, ValidationReport


class ProposalValidator:
    def __init__(self, context: WorkspaceContext, registry: CapabilityRegistry | None = None) -> None:
        self.store = ControlStore(context)
        self.registry = registry or CapabilityRegistry()

    def validate(
        self,
        proposal: ProposalEnvelope,
        *,
        reference_checker: Callable[[str], bool] | None = None,
        expected_dependency_fingerprint: str | None = None,
    ) -> ValidationReport:
        findings: list[ValidationFinding] = []
        schema_valid = self._payload_is_valid(proposal, findings)
        try:
            self.registry.authorize_proposal(proposal.producer_role, proposal.artifact_kind)
            authority_policy_valid = True
        except PermissionError as exc:
            authority_policy_valid = False
            findings.append(ValidationFinding(code="CAPABILITY_DENIED", message=str(exc)))
        references_valid = all((reference_checker(source_id) if reference_checker else bool(source_id)) for source_id in proposal.cited_source_ids)
        if not references_valid:
            findings.append(ValidationFinding(code="REFERENCE_INVALID", message="Proposal 引用了不存在或不可访问的 Source。"))
        active = self.store.v3_active_artifact(proposal.artifact_kind)
        active_revision = int(active["revision"]) if active is not None else 0
        dependency_current = proposal.base_revision == active_revision and (
            expected_dependency_fingerprint is None or proposal.dependency_fingerprint == expected_dependency_fingerprint
        )
        if not dependency_current:
            findings.append(ValidationFinding(code="DEPENDENCY_STALE", message="base_revision 或 dependency fingerprint 已失效。"))
        return ValidationReport(
            proposal_id=proposal.proposal_id,
            schema_valid=schema_valid,
            references_valid=references_valid,
            authority_policy_valid=authority_policy_valid,
            dependency_current=dependency_current,
            findings=findings,
        )

    @staticmethod
    def _payload_is_valid(proposal: ProposalEnvelope, findings: list[ValidationFinding]) -> bool:
        """Validate artifact-specific payloads before a Gate can bind a proposal."""
        try:
            if proposal.artifact_kind == "RequirementLedger":
                from .contracts import RequirementLedger

                RequirementLedger.model_validate(proposal.payload)
            elif proposal.artifact_kind == "ScoreModel":
                from .contracts import ScoreModel

                ScoreModel.model_validate(proposal.payload)
        except ValueError as exc:
            findings.append(ValidationFinding(code="PAYLOAD_SCHEMA_INVALID", message=str(exc)))
            return False
        return True


class AgentProposalSandbox:
    """Narrow agent facade: submit candidates only; no database or promotion API."""

    def __init__(self, context: WorkspaceContext, role: str, registry: CapabilityRegistry | None = None) -> None:
        self.store = ControlStore(context)
        self.role = role
        self.registry = registry or CapabilityRegistry()

    def submit(self, proposal: ProposalEnvelope) -> dict[str, Any]:
        if proposal.producer_role != self.role:
            raise PermissionError("V3_CAPABILITY_DENIED: Agent 不得冒用其他角色。")
        self.registry.authorize_proposal(self.role, proposal.artifact_kind)
        return self.store.append_v3_proposal(proposal.storage_record())


class GateService:
    """Deterministic gate issuer. A passing receipt binds exact proposal content."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.store = ControlStore(context)

    def evaluate(self, proposal_id: str, *, gate_id: str, reviewer: str = "system") -> GateReceipt:
        proposal = self.store.v3_proposal(proposal_id)
        if proposal is None:
            raise ControlPlaneError("V3_PROPOSAL_NOT_FOUND", "Proposal 不存在。", status_code=404)
        report_raw = self._validation_report(proposal_id)
        report = ValidationReport.model_validate(report_raw)
        verdict = "pass" if report.passed else "block"
        receipt = GateReceipt(
            proposal_id=proposal_id,
            proposal_hash=str(proposal["proposal_hash"]),
            gate_id=gate_id,
            verdict=verdict,
            findings=report.findings,
            reviewer=reviewer,
            reviewed_revision=int(proposal["base_revision"]),
        )
        self.store.issue_v3_gate_receipt(receipt.model_dump(mode="json"))
        return receipt

    def _validation_report(self, proposal_id: str) -> dict[str, Any]:
        report = self.store.v3_validation_report(proposal_id)
        if report is None:
            raise ControlPlaneError("V3_GATE_FORBIDDEN", "Proposal 尚未完成验证。", status_code=409)
        return report


class ArtifactPromotionService:
    """Service-owned CAS promotion; agents cannot obtain this capability."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.store = ControlStore(context)

    def promote(self, proposal_id: str, gate_receipt_ids: list[str]) -> PromotionReceipt:
        return PromotionReceipt.model_validate(
            self.store.promote_v3_proposal(proposal_id=proposal_id, gate_receipt_ids=gate_receipt_ids)
        )


def validate_and_record(
    context: WorkspaceContext,
    proposal: ProposalEnvelope,
    *,
    reference_checker: Callable[[str], bool] | None = None,
    expected_dependency_fingerprint: str | None = None,
) -> ValidationReport:
    """Small service entry point used by Bid Master after an agent submits."""
    report = ProposalValidator(context).validate(
        proposal,
        reference_checker=reference_checker,
        expected_dependency_fingerprint=expected_dependency_fingerprint,
    )
    ControlStore(context).record_v3_validation_report(proposal.proposal_id, report.model_dump(mode="json"))
    return report
