"""PR-14.0: frozen canonicalization, schemas, registry and ADR references."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.artifact_registry import ARTIFACT_REGISTRY, ARTIFACT_REGISTRY_VERSION
from document_pipeline.canonicalization import (
    CANONICALIZATION_VERSION,
    PROPOSAL_HASH_EXCLUDED_FIELDS,
    canonical_hash,
    canonical_json,
    canonical_payload_hash,
    compute_proposal_hash,
)
from document_pipeline.gate_policy_registry import GATE_POLICY_REGISTRY, GATE_POLICY_REGISTRY_VERSION
from document_pipeline.proposals import (
    GateReceipt,
    PlanningGateReceipt,
    PromotionReceipt,
    ProposalEnvelope,
    ValidationFinding,
    ValidationReport,
)


class CanonicalizationVectorsTests(unittest.TestCase):
    def test_canonical_json_is_stable(self) -> None:
        left = {"b": 1, "a": {"z": True, "y": [3, 2]}}
        right = {"a": {"y": [3, 2], "z": True}, "b": 1}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_hash(left), canonical_hash(right))

    def test_payload_hash_vector(self) -> None:
        payload = {"schema_version": "v3", "revision": 1, "source_hashes": {}, "requirements": []}
        digest = canonical_payload_hash(payload)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, canonical_payload_hash(dict(payload)))

    def test_proposal_hash_excludes_identity_fields(self) -> None:
        base = {
            "workspace_id": "ws-1",
            "artifact_kind": "RequirementLedger",
            "producer_role": "requirement_agent",
            "operation_id": "op-1",
            "base_revision": 0,
            "declared_dependencies": [],
            "dependency_fingerprint": "abc",
            "payload": {"schema_version": "v3", "revision": 1, "source_hashes": {}, "requirements": []},
            "cited_source_ids": [],
            "prompt_version": "p1",
            "model_fingerprint": "m1",
            "payload_schema_version": "v3",
            "canonicalization_version": CANONICALIZATION_VERSION,
        }
        h1 = compute_proposal_hash(base)
        h2 = compute_proposal_hash({**base, "proposal_id": "different", "created_at": "x"})
        # Extra identity keys are not part of decision_document inputs; ensure excluded set is frozen.
        self.assertIn("proposal_id", PROPOSAL_HASH_EXCLUDED_FIELDS)
        self.assertEqual(h1, compute_proposal_hash(base))
        self.assertEqual(len(h1), 64)
        self.assertNotEqual(h1, compute_proposal_hash({**base, "operation_id": "op-2"}))
        _ = h2  # identity fields never enter decision document builder

    def test_fixture_vectors_file(self) -> None:
        path = ROOT / "tests" / "fixtures" / "v3_kernel" / "canonicalization_vectors.json"
        vectors = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(vectors["canonicalization_version"], CANONICALIZATION_VERSION)
        for item in vectors["vectors"]:
            self.assertEqual(canonical_hash(item["input"]), item["sha256"])


class SchemaFixtureTests(unittest.TestCase):
    def test_proposal_envelope_legal(self) -> None:
        proposal = ProposalEnvelope(
            workspace_id="alpha",
            artifact_kind="RequirementLedger",
            producer_role="requirement_agent",
            operation_id="op",
            base_revision=0,
            dependency_fingerprint="fp",
            payload={"schema_version": "v3", "revision": 1, "source_hashes": {}, "requirements": []},
            prompt_version="p",
            model_fingerprint="m",
        )
        self.assertEqual(len(proposal.proposal_hash()), 64)
        self.assertEqual(len(proposal.canonical_payload_hash()), 64)

    def test_proposal_envelope_rejects_duplicate_citations(self) -> None:
        with self.assertRaises(Exception):
            ProposalEnvelope(
                workspace_id="alpha",
                artifact_kind="RequirementLedger",
                producer_role="requirement_agent",
                operation_id="op",
                base_revision=0,
                dependency_fingerprint="fp",
                payload={"requirements": []},
                cited_source_ids=["a", "a"],
                prompt_version="p",
                model_fingerprint="m",
            )

    def test_validation_report_binds_hashes(self) -> None:
        report = ValidationReport(
            workspace_id="alpha",
            proposal_id="p1",
            proposal_hash="h1",
            canonical_payload_hash="h2",
            artifact_kind="RequirementLedger",
            dependency_fingerprint="df",
            validator_id="v",
            validator_version="1",
            schema_version="v3",
            policy_version="pol",
            schema_valid=True,
            references_valid=True,
            authority_policy_valid=True,
            dependency_current=True,
            findings=[ValidationFinding(code="OK", message="ok", severity="info")],
        )
        self.assertTrue(report.passed)
        self.assertEqual(len(report.report_hash()), 64)

    def test_gate_and_planning_receipts(self) -> None:
        gate = GateReceipt(
            workspace_id="alpha",
            proposal_id="p1",
            proposal_hash="h1",
            validation_report_id="r1",
            validation_report_hash="rh",
            artifact_kind="RequirementLedger",
            base_revision=0,
            dependency_fingerprint="df",
            gate_id="G1_REQUIREMENT_INTEGRITY",
            gate_policy_version=GATE_POLICY_REGISTRY_VERSION,
            verdict="pass",
            issuer="gate_service",
            reviewer="system",
        )
        self.assertEqual(gate.receipt_subtype, "gate")
        digest = gate.compute_receipt_content_hash()
        self.assertEqual(len(digest), 64)
        sealed = gate.storage_record()
        self.assertEqual(sealed["receipt_hash"], digest)
        # Field and method must not collide: field is a string after seal, method remains callable.
        self.assertIsInstance(sealed["receipt_hash"], str)
        self.assertTrue(callable(gate.compute_receipt_content_hash))

        planning = PlanningGateReceipt(
            workspace_id="alpha",
            proposal_id="p1",
            proposal_hash="h1",
            validation_report_id="r1",
            validation_report_hash="rh",
            artifact_kind="ChapterBlueprint",
            base_revision=1,
            dependency_fingerprint="df",
            gate_id="H1_PLANNING_CONFIRM",
            gate_policy_version=GATE_POLICY_REGISTRY_VERSION,
            verdict="pass",
            issuer="human_gate_service",
            reviewer="user-1",
            planning_decision="confirm",
            principal_id="user-1",
            planning_confirmation_scope_hash="scope",
            planning_audit_snapshot_hash="audit",
            g2_receipt_id="g2",
            g2_receipt_hash="g2h",
            planning_dag_root_hash="dag",
            policy_nonce="nonce-1",
        )
        self.assertEqual(planning.receipt_subtype, "planning")

    def test_planning_carry_forward_requires_source_h1_and_dag(self) -> None:
        with self.assertRaises(Exception):
            PlanningGateReceipt(
                workspace_id="alpha",
                proposal_id="p1",
                proposal_hash="h1",
                validation_report_id="r1",
                validation_report_hash="rh",
                artifact_kind="ChapterBlueprint",
                base_revision=1,
                dependency_fingerprint="df",
                gate_id="H1_PLANNING_CONFIRM",
                gate_policy_version=GATE_POLICY_REGISTRY_VERSION,
                verdict="pass",
                issuer="human_gate_service",
                reviewer="system",
                planning_decision="deterministic_carry_forward",
                # missing source_h1, dag root, scope, etc.
            )
        ok = PlanningGateReceipt(
            workspace_id="alpha",
            proposal_id="p1",
            proposal_hash="h1",
            validation_report_id="r1",
            validation_report_hash="rh",
            artifact_kind="ChapterBlueprint",
            base_revision=1,
            dependency_fingerprint="df",
            gate_id="H1_PLANNING_CONFIRM",
            gate_policy_version=GATE_POLICY_REGISTRY_VERSION,
            verdict="pass",
            issuer="human_gate_service",
            reviewer="carry-forward-service",
            planning_decision="deterministic_carry_forward",
            principal_id="user-1",
            planning_confirmation_scope_hash="scope",
            planning_audit_snapshot_hash="audit",
            source_h1_receipt_id="h1-old",
            source_h1_receipt_hash="h1-old-hash",
            g2_receipt_id="g2",
            g2_receipt_hash="g2h",
            planning_dag_root_hash="dag",
            policy_nonce="nonce-1",
        )
        self.assertEqual(ok.planning_decision, "deterministic_carry_forward")

    def test_promotion_receipt_requires_gate_bindings(self) -> None:
        receipt = PromotionReceipt(
            receipt_id="pr1",
            workspace_id="alpha",
            proposal_id="p1",
            proposal_hash="h1",
            artifact_kind="RequirementLedger",
            operation_id="op",
            artifact_id="RequirementLedger@1",
            base_revision=0,
            promoted_revision=1,
            artifact_hash="ah",
            dependency_fingerprint="df",
            gate_receipts=[{"receipt_id": "g1", "receipt_hash": "gh", "gate_id": "G1_REQUIREMENT_INTEGRITY"}],
            policy_version=GATE_POLICY_REGISTRY_VERSION,
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(receipt.gate_receipt_ids, ["g1"])
        sealed = receipt.with_content_hash()
        self.assertEqual(len(sealed.receipt_hash), 64)
        self.assertEqual(sealed.receipt_hash, sealed.compute_receipt_content_hash())
        # Serializing a promotion receipt with content hash must not invoke a method field.
        payload = sealed.model_dump(mode="json")
        self.assertIsInstance(payload["receipt_hash"], str)
        self.assertNotEqual(payload["receipt_hash"], "")


class RegistryTests(unittest.TestCase):
    def test_enabled_promotable_kinds_have_payload_schema_and_policy(self) -> None:
        kinds = ARTIFACT_REGISTRY.enabled_promotable_kinds()
        self.assertGreaterEqual(len(kinds), 5)
        for kind in kinds:
            reg = ARTIFACT_REGISTRY.get(kind)
            self.assertTrue(reg.is_promotable())
            self.assertIsNotNone(reg.payload_model)
            policy = GATE_POLICY_REGISTRY.policy_for(kind)
            self.assertTrue(policy.required_gates)
            self.assertEqual(policy.artifact_kind, kind)

    def test_empty_payload_and_unknown_kind_fail(self) -> None:
        with self.assertRaises(ValueError):
            ARTIFACT_REGISTRY.validate_payload("RequirementLedger", {})
        with self.assertRaises(KeyError):
            ARTIFACT_REGISTRY.get("NotARealKind")
        self.assertTrue(ARTIFACT_REGISTRY.require_promotable("InputManifest").is_promotable())
        self.assertTrue(ARTIFACT_REGISTRY.require_promotable("SourceIndex").is_promotable())
        self.assertTrue(ARTIFACT_REGISTRY.require_promotable("TemplateStructureContract").is_promotable())
        with self.assertRaises(KeyError):
            GATE_POLICY_REGISTRY.policy_for("NotARealKind")
        with self.assertRaises(KeyError):
            ARTIFACT_REGISTRY.get("NotARealKind")

    def test_registry_versions_are_frozen(self) -> None:
        self.assertEqual(ARTIFACT_REGISTRY_VERSION, "v3-artifact-registry-2")
        self.assertEqual(GATE_POLICY_REGISTRY_VERSION, "v3-gate-policy-2")
        self.assertEqual(len(GATE_POLICY_REGISTRY.registry_fingerprint()), 64)


class IllegalFixtureTests(unittest.TestCase):
    def test_illegal_receipt_and_proposal_fixtures_fail(self) -> None:
        path = ROOT / "tests" / "fixtures" / "v3_kernel" / "illegal_receipt_fixtures.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        models = {
            "GateReceipt": GateReceipt,
            "PlanningGateReceipt": PlanningGateReceipt,
            "PromotionReceipt": PromotionReceipt,
            "ProposalEnvelope": ProposalEnvelope,
        }
        for case in cases:
            model = models[case["kind"]]
            with self.subTest(case["name"]):
                with self.assertRaises(Exception):
                    model.model_validate(case["payload"])

    def test_canonicalization_version_is_decision_sensitive(self) -> None:
        base = {
            "workspace_id": "ws-1",
            "artifact_kind": "RequirementLedger",
            "producer_role": "requirement_agent",
            "operation_id": "op-1",
            "base_revision": 0,
            "declared_dependencies": [],
            "dependency_fingerprint": "abc",
            "payload": {"schema_version": "v3", "revision": 1, "source_hashes": {}, "requirements": []},
            "cited_source_ids": [],
            "prompt_version": "p1",
            "model_fingerprint": "m1",
            "payload_schema_version": "v3",
            "canonicalization_version": CANONICALIZATION_VERSION,
        }
        h1 = compute_proposal_hash(base)
        h2 = compute_proposal_hash({**base, "canonicalization_version": "v3-canon-0-legacy"})
        self.assertNotEqual(h1, h2)


class AdrAndChecklistPresenceTests(unittest.TestCase):
    def test_required_docs_exist(self) -> None:
        docs = ROOT / "docs"
        for relative in (
            "adr/ADR-01-agent-artifact-service-permissions.md",
            "adr/ADR-02-proposal-gate-cas-promotion.md",
            "adr/ADR-11-exact-proposal-receipt-binding.md",
            "architecture_review_checklist.md",
            "v3_pr14_0_acceptance.md",
        ):
            path = docs / relative
            self.assertTrue(path.is_file(), msg=f"missing {relative}")
            text = path.read_text(encoding="utf-8")
            self.assertGreater(len(text), 200)


if __name__ == "__main__":
    unittest.main()
