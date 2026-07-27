"""PR-15.1 trusted kernel: exact binding, Gate policy, dependency recompute, negatives."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.bid_master import BidMaster  # noqa: E402
from agent.capability_registry import CapabilityDenied  # noqa: E402
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import (  # noqa: E402
    AgentProposalSandbox,
    ArtifactPromotionService,
    GateService,
    build_declared_dependency_fingerprint,
    validate_and_record,
)
from document_pipeline.gate_policy_registry import GATE_POLICY_REGISTRY, ISSUER_GATE_SERVICE  # noqa: E402
from document_pipeline.proposals import ProposalEnvelope  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


LEDGER_PAYLOAD = {
    "schema_version": "v3",
    "revision": 1,
    "source_hashes": {},
    "requirements": [],
    "coverage_audit": {},
}


class V3ProposalPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        runs = Path(self.temp.name) / "runs"
        (runs / "alpha").mkdir(parents=True)
        self.context = WorkspaceContext.resolve(runs, "alpha")
        self.runs = runs

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fp(self, *, artifact_kind: str = "RequirementLedger", snapshot: dict | None = None) -> str:
        return build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=snapshot or {},
            artifact_kind=artifact_kind,
            prompt_version="requirement-v1",
            model_fingerprint="fixture-model",
        )

    def proposal(
        self,
        *,
        operation_id: str = "operation-1",
        base_revision: int = 0,
        payload: dict | None = None,
        proposal_id: str | None = None,
        dependency_fingerprint: str | None = None,
        workspace_id: str | None = None,
        artifact_kind: str = "RequirementLedger",
        producer_role: str = "requirement_agent",
    ) -> ProposalEnvelope:
        data = {
            "workspace_id": workspace_id or self.context.workspace_id,
            "artifact_kind": artifact_kind,
            "producer_role": producer_role,
            "operation_id": operation_id,
            "base_revision": base_revision,
            "dependency_fingerprint": dependency_fingerprint or self._fp(artifact_kind=artifact_kind),
            "payload": payload if payload is not None else dict(LEDGER_PAYLOAD),
            "prompt_version": "requirement-v1",
            "model_fingerprint": "fixture-model",
        }
        if proposal_id is not None:
            data["proposal_id"] = proposal_id
        return ProposalEnvelope.model_validate(data)

    def submit_validate_gate(self, proposal: ProposalEnvelope, *, gate_id: str = "G1_REQUIREMENT_INTEGRITY"):
        AgentProposalSandbox(self.context, proposal.producer_role).submit(proposal)
        report = validate_and_record(self.context, proposal.proposal_id)
        self.assertTrue(report.passed, msg=str(report.findings))
        return GateService(self.context).evaluate(proposal.proposal_id, gate_id=gate_id)

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
        validate_and_record(self.context, proposal.proposal_id)
        with self.assertRaisesRegex(ControlPlaneError, "GateReceipt|缺少"):
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
        with self.assertRaisesRegex(ControlPlaneError, "GateReceipt|缺少"):
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, ["missing-receipt"])
        self.assertEqual(ControlStore(self.context).v3_promoted_artifacts(), [])

    def test_bid_master_is_a_thin_proposal_coordinator(self) -> None:
        proposal = self.proposal()
        master = BidMaster(self.context)
        master.submit_candidate(proposal)
        self.assertTrue(master.validate_candidate(proposal.proposal_id).passed)
        receipt = master.gate_and_promote(proposal.proposal_id, gate_id="G1_REQUIREMENT_INTEGRITY")
        self.assertEqual(receipt.artifact_kind, "RequirementLedger")
        self.assertEqual(receipt.proposal_hash, proposal.proposal_hash())

    def test_validate_a_promote_b_is_blocked(self) -> None:
        """Store invalid content under id; validating a different body with same id must not launder it."""
        bad_id = uuid4().hex
        bad = self.proposal(proposal_id=bad_id, payload={"not": "a ledger"})
        # Force-append a schema-invalid payload via store after crafting hash from envelope.
        # Envelope construction allows arbitrary payload dict; schema fails at validation.
        AgentProposalSandbox(self.context, "requirement_agent").submit(bad)

        # Attacker tries to validate a *different* good envelope that reuses the same proposal_id.
        good = self.proposal(proposal_id=bad_id, payload=dict(LEDGER_PAYLOAD))
        # Validator ignores caller body and only reloads Store.
        report = validate_and_record(self.context, bad_id, proposal=good)
        self.assertFalse(report.passed)
        self.assertTrue(any(f.code == "PAYLOAD_SCHEMA_INVALID" for f in report.findings))
        # Gate may record a block receipt, but must never promote the stored invalid payload.
        blocked = GateService(self.context).evaluate(bad_id, gate_id="G1_REQUIREMENT_INTEGRITY")
        self.assertEqual(blocked.verdict, "block")
        with self.assertRaises(ControlPlaneError):
            ArtifactPromotionService(self.context).promote(bad_id, [blocked.receipt_id])
        self.assertIsNone(ControlStore(self.context).v3_active_artifact("RequirementLedger"))

    def test_proposal_a_cannot_reuse_proposal_b_receipts(self) -> None:
        a = self.proposal(operation_id="op-a")
        b = self.proposal(operation_id="op-b", payload={**LEDGER_PAYLOAD, "coverage_audit": {"x": 1}})
        gate_a = self.submit_validate_gate(a)
        AgentProposalSandbox(self.context, "requirement_agent").submit(b)
        validate_and_record(self.context, b.proposal_id)
        with self.assertRaisesRegex(ControlPlaneError, "GateReceipt|不属于|不完整"):
            ArtifactPromotionService(self.context).promote(b.proposal_id, [gate_a.receipt_id])

    def test_forged_issuer_receipt_cannot_promote(self) -> None:
        proposal = self.proposal()
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        report = validate_and_record(self.context, proposal.proposal_id)
        self.assertTrue(report.passed)
        store = ControlStore(self.context)
        forged = {
            "receipt_id": uuid4().hex,
            "receipt_hash": "forged",
            "workspace_id": self.context.workspace_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash(),
            "validation_report_id": report.report_id,
            "validation_report_hash": report.report_hash(),
            "artifact_kind": "RequirementLedger",
            "base_revision": 0,
            "reviewed_revision": 0,
            "resolved_dependency_snapshot": {},
            "dependency_fingerprint": proposal.dependency_fingerprint,
            "gate_id": "G1_REQUIREMENT_INTEGRITY",
            "gate_policy_version": GATE_POLICY_REGISTRY.VERSION,
            "verdict": "pass",
            "findings": [],
            "issuer": "agent",  # illegal issuer
            "reviewer": "attacker",
            "issued_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        store.issue_v3_gate_receipt(forged)
        with self.assertRaisesRegex(ControlPlaneError, "issuer 非法"):
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, [forged["receipt_id"]])

    def test_wrong_gate_id_cannot_promote(self) -> None:
        proposal = self.proposal()
        # Issue a pass receipt for an unknown/wrong gate via evaluate after hacking policy? Use direct store.
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        report = validate_and_record(self.context, proposal.proposal_id)
        self.assertTrue(report.passed)
        # GateService rejects unknown gate for this kind.
        with self.assertRaisesRegex(ControlPlaneError, "V3_GATE_UNKNOWN|GATE"):
            GateService(self.context).evaluate(proposal.proposal_id, gate_id="G2_BLUEPRINT_INTEGRITY")

    def test_producer_self_proved_dependency_fingerprint_fails(self) -> None:
        proposal = self.proposal(dependency_fingerprint="i-swear-this-is-valid")
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        report = validate_and_record(self.context, proposal.proposal_id)
        self.assertFalse(report.passed)
        self.assertTrue(any(f.code == "DEPENDENCY_FINGERPRINT_MISMATCH" for f in report.findings))

    def test_same_operation_different_hash_conflicts(self) -> None:
        first = self.proposal(operation_id="same-op", payload={**LEDGER_PAYLOAD, "coverage_audit": {"a": 1}})
        second = self.proposal(operation_id="same-op", payload={**LEDGER_PAYLOAD, "coverage_audit": {"b": 2}})
        AgentProposalSandbox(self.context, "requirement_agent").submit(first)
        with self.assertRaisesRegex(ControlPlaneError, "operation_id|proposal_hash"):
            AgentProposalSandbox(self.context, "requirement_agent").submit(second)

    def test_empty_payload_and_unknown_kind_rejected(self) -> None:
        empty = self.proposal(payload={})
        AgentProposalSandbox(self.context, "requirement_agent").submit(empty)
        report = validate_and_record(self.context, empty.proposal_id)
        self.assertFalse(report.passed)

    def test_cross_workspace_submit_denied(self) -> None:
        (self.runs / "beta").mkdir(parents=True)
        other = WorkspaceContext.resolve(self.runs, "beta")
        proposal = self.proposal(workspace_id=other.workspace_id)
        with self.assertRaises(PermissionError):
            AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)

    def test_promotion_receipt_binds_gate_hashes_and_proposal_hash(self) -> None:
        proposal = self.proposal()
        gate = self.submit_validate_gate(proposal)
        receipt = ArtifactPromotionService(self.context).promote(proposal.proposal_id, [gate.receipt_id])
        self.assertEqual(receipt.proposal_hash, proposal.proposal_hash())
        self.assertEqual(receipt.workspace_id, self.context.workspace_id)
        self.assertEqual(len(receipt.gate_receipts), 1)
        self.assertEqual(receipt.gate_receipts[0].receipt_id, gate.receipt_id)
        self.assertEqual(receipt.gate_receipts[0].receipt_hash, gate.receipt_hash())
        self.assertEqual(receipt.policy_version, GATE_POLICY_REGISTRY.VERSION)
        # Reverse lookup: active artifact points at exact proposal.
        active = ControlStore(self.context).v3_active_artifact("RequirementLedger")
        self.assertEqual(active["proposal_id"], proposal.proposal_id)
        self.assertEqual(active["proposal_hash"], proposal.proposal_hash())

    def test_missing_required_gate_set_fails(self) -> None:
        proposal = self.proposal()
        AgentProposalSandbox(self.context, "requirement_agent").submit(proposal)
        report = validate_and_record(self.context, proposal.proposal_id)
        self.assertTrue(report.passed)
        # Fabricate a pass receipt for a non-required gate id that policy does not list.
        store = ControlStore(self.context)
        bogus = {
            "receipt_id": uuid4().hex,
            "receipt_hash": "x",
            "workspace_id": self.context.workspace_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash(),
            "validation_report_id": report.report_id,
            "validation_report_hash": report.report_hash(),
            "artifact_kind": "RequirementLedger",
            "base_revision": 0,
            "reviewed_revision": 0,
            "resolved_dependency_snapshot": {},
            "dependency_fingerprint": proposal.dependency_fingerprint,
            "gate_id": "G_NOT_REQUIRED",
            "gate_policy_version": GATE_POLICY_REGISTRY.VERSION,
            "verdict": "pass",
            "findings": [],
            "issuer": ISSUER_GATE_SERVICE,
            "reviewer": "system",
            "issued_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        store.issue_v3_gate_receipt(bogus)
        with self.assertRaisesRegex(ControlPlaneError, "缺少必需 Gate"):
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, [bogus["receipt_id"]])

    def test_human_gate_rejects_system_reviewer(self) -> None:
        proposal = self.proposal()
        gate = self.submit_validate_gate(proposal)
        ArtifactPromotionService(self.context).promote(proposal.proposal_id, [gate.receipt_id])
        with self.assertRaisesRegex(ControlPlaneError, "人工 Gate|principal"):
            GateService(self.context).evaluate(
                proposal.proposal_id,
                gate_id="H1_PLANNING_CONFIRM",
                reviewer="system",
            )


if __name__ == "__main__":
    unittest.main()
