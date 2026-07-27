"""Thin V3 orchestration facade over the existing CommandGateway/ControlStore.

It deliberately has no loop, hidden memory, database write access or artifact
write API. Workflow scheduling remains owned by V3StageRunner; this facade only
coordinates the trusted proposal services when a V3 Command invokes them.
"""

from __future__ import annotations

from control_plane import WorkspaceContext
from document_pipeline.artifact_promotion import (
    AgentProposalSandbox,
    ArtifactPromotionService,
    GateService,
    validate_and_record,
)
from document_pipeline.proposals import PromotionReceipt, ProposalEnvelope, ValidationReport


class BidMaster:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def submit_candidate(self, proposal: ProposalEnvelope) -> dict:
        """Accept an agent candidate without making it a runtime artifact."""
        if not proposal.workspace_id:
            proposal = proposal.model_copy(update={"workspace_id": self.context.workspace_id})
        return AgentProposalSandbox(self.context, proposal.producer_role).submit(proposal)

    def validate_candidate(self, proposal_id: str | ProposalEnvelope) -> ValidationReport:
        """Validate only by proposal_id. Envelope argument is accepted for migration
        but its body is never used as validation authority."""
        if isinstance(proposal_id, ProposalEnvelope):
            target = proposal_id.proposal_id
        else:
            target = str(proposal_id)
        return validate_and_record(self.context, target)

    def gate_and_promote(
        self,
        proposal_id: str | ProposalEnvelope,
        *,
        gate_id: str,
        reviewer: str = "system",
    ) -> PromotionReceipt:
        """Use the single Gate → Promotion path after validation has passed."""
        if isinstance(proposal_id, ProposalEnvelope):
            target = proposal_id.proposal_id
        else:
            target = str(proposal_id)
        gate = GateService(self.context).evaluate(target, gate_id=gate_id, reviewer=reviewer)
        return ArtifactPromotionService(self.context).promote(target, [gate.receipt_id])
