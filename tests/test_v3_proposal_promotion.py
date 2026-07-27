from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.capability_registry import CapabilityDenied  # noqa: E402
from agent.bid_master import BidMaster  # noqa: E402
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import (  # noqa: E402
    AgentProposalSandbox,
    ArtifactPromotionService,
    GateService,
    validate_and_record,
)
from document_pipeline.proposals import ProposalEnvelope, dependency_fingerprint  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


class V3ProposalPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        runs = Path(self.temp.name) / "runs"
        (runs / "alpha").mkdir(parents=True)
        self.context = WorkspaceContext.resolve(runs, "alpha")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def proposal(self, *, operation_id: str = "operation-1", base_revision: int = 0) -> ProposalEnvelope:
        return ProposalEnvelope(
            artifact_kind="RequirementLedger",
            producer_role="requirement_agent",
            operation_id=operation_id,
            base_revision=base_revision,
            dependency_fingerprint=dependency_fingerprint("source-index", 1),
            payload={"requirements": []},
            prompt_version="requirement-v1",
            model_fingerprint="fixture-model",
        )

    def submit_validate_gate(self, proposal: ProposalEnvelope):
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        self.assertTrue(validate_and_record(self.context, proposal).passed)
        return GateService(self.context).evaluate(proposal.proposal_id, gate_id="G1")

    def test_only_promotion_service_can_create_canonical_artifact(self) -> None:
        proposal = self.proposal()
        with self.assertRaises(CapabilityDenied):
            AgentProposalSandbox(self.context, "requirement_agent").submit(
                proposal.model_copy(update={"artifact_kind": "IntegratedDocument"})
            )
        self.assertIsNone(ControlStore(self.context).v3_active_artifact("RequirementLedger"))

    def test_gate_receipt_is_required(self) -> None:
        proposal = self.proposal()
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        validate_and_record(self.context, proposal)
        with self.assertRaisesRegex(ControlPlaneError, "GateReceipt"):
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, [])
        self.assertIsNone(ControlStore(self.context).v3_active_artifact("RequirementLedger"))

    def test_stale_base_revision_cannot_promote(self) -> None:
        first = self.proposal(operation_id="operation-1")
        second = self.proposal(operation_id="operation-2")
        first_gate = self.submit_validate_gate(first)
        second_gate = self.submit_validate_gate(second)
        ArtifactPromotionService(self.context).promote(first.proposal_id, [first_gate.receipt_id])
        with self.assertRaisesRegex(ControlPlaneError, "base_revision 已过期"):
            ArtifactPromotionService(self.context).promote(second.proposal_id, [second_gate.receipt_id])
        active = ControlStore(self.context).v3_active_artifact("RequirementLedger")
        self.assertEqual(active["revision"], 1)
        self.assertEqual(active["proposal_id"], first.proposal_id)

    def test_operation_is_idempotent_and_creates_one_revision(self) -> None:
        first = self.proposal(operation_id="operation-1")
        gate = self.submit_validate_gate(first)
        service = ArtifactPromotionService(self.context)
        receipt = service.promote(first.proposal_id, [gate.receipt_id])
        repeated = service.promote(first.proposal_id, [gate.receipt_id])
        self.assertEqual(receipt.receipt_id, repeated.receipt_id)
        self.assertEqual(ControlStore(self.context).v3_active_artifact("RequirementLedger")["revision"], 1)

    def test_workspace_snapshot_projects_only_promoted_artifacts(self) -> None:
        proposal = self.proposal()
        gate = self.submit_validate_gate(proposal)
        ArtifactPromotionService(self.context).promote(proposal.proposal_id, [gate.receipt_id])
        snapshot = V3WorkspaceSnapshotBuilder(self.context).build()
        self.assertEqual([item["artifact_kind"] for item in snapshot["promoted_artifacts"]], ["RequirementLedger"])
        self.assertIsNone(snapshot["project_model"])

    def test_failed_promotion_leaves_no_half_revision(self) -> None:
        proposal = self.proposal()
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        with self.assertRaisesRegex(ControlPlaneError, "GateReceipt"):
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, ["missing-receipt"])
        self.assertEqual(ControlStore(self.context).v3_promoted_artifacts(), [])

    def test_bid_master_is_a_thin_proposal_coordinator(self) -> None:
        proposal = self.proposal()
        master = BidMaster(self.context)
        master.submit_candidate(proposal)
        self.assertTrue(master.validate_candidate(proposal).passed)
        receipt = master.gate_and_promote(proposal, gate_id="G1")
        self.assertEqual(receipt.artifact_kind, "RequirementLedger")


if __name__ == "__main__":
    unittest.main()
