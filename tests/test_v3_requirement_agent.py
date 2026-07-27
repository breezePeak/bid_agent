import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext
from document_pipeline.contracts import (
    InputItem,
    InputManifest,
    InputRole,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    SourceAnchor,
    SourceBlock,
)
from document_pipeline.requirement_agent import RequirementAgent
from document_pipeline.requirement_ledger import audit_reverse_coverage



class TestV3RequirementAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.context = WorkspaceContext(root=self.root, workspace_id="ws_test")


    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_structured_extraction(self) -> None:
        agent = RequirementAgent(self.context)
        manifest = InputManifest(
            inputs=[
                InputItem(
                    input_id="in-tender",
                    role=InputRole.TENDER,
                    filename="tender.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="abc123hash",
                    version=1,
                )
            ]
        )
        anchor = SourceAnchor(source_input_id="in-tender", chunk_id="chk-1", location="p:1")
        blocks = [
            SourceBlock(
                block_id="b-1",
                input_id="in-tender",
                input_role=InputRole.TENDER,
                block_kind="heading",
                ordinal=0,
                content="三、资质要求",
                source_anchor=anchor,
                content_hash="h1",
            ),
            SourceBlock(
                block_id="b-2",
                input_id="in-tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=1,
                content="1.1 投标人须具备ISO9001认证证书；在合同签署后10天内提交，工期为30天。",
                source_anchor=anchor,
                content_hash="h2",
            ),
        ]

        items = agent.extract_requirements(blocks, manifest)
        self.assertGreaterEqual(len(items), 1)
        item0 = items[0]
        self.assertEqual(item0.kind, RequirementKind.QUALIFICATION)
        self.assertEqual(item0.parent_clause_id, "三、资质要求")
        self.assertIsNotNone(item0.clause_id)
        self.assertEqual(item0.subject, "投标人")
        self.assertEqual(item0.action, "具备")

        # Statement 2 contains quantitative metrics
        item1 = items[1] if len(items) > 1 else items[0]
        self.assertIn("天", item1.quantitative_metrics)
        self.assertEqual(item1.quantitative_metrics["天"], 30)


    def test_amendment_reconciliation(self) -> None:
        agent = RequirementAgent(self.context)
        manifest = InputManifest(
            inputs=[
                InputItem(
                    input_id="in-tender",
                    role=InputRole.TENDER,
                    filename="tender.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="abc123hash",
                    version=1,
                ),
                InputItem(
                    input_id="in-amd",
                    role=InputRole.AMENDMENT,
                    filename="amd1.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="def456hash",
                    version=1,
                    issued_at="2026-07-27",
                    supersedes_input_ids=["in-tender"],
                ),
            ]
        )
        anchor_old = SourceAnchor(source_input_id="in-tender", chunk_id="chk-1", location="p:1")
        item_old = RequirementItem(
            requirement_id="R-old",
            kind=RequirementKind.MANDATORY,
            source_anchor=anchor_old,
            original_text="旧要求",
            normalized_requirement="旧要求",
            response_type="mandatory_response",
            evidence_policy="tender_traceable",
        )

        reconciled = agent.reconcile_amendments([item_old], manifest)
        self.assertEqual(reconciled[0].status, "waived")
        self.assertEqual(reconciled[0].superseded_by_input_id, "in-amd")

    def test_amendment_reconciliation_ordering(self) -> None:
        agent = RequirementAgent(self.context)
        manifest = InputManifest(
            inputs=[
                InputItem(
                    input_id="in-tender",
                    role=InputRole.TENDER,
                    filename="tender.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="abc123hash",
                    version=1,
                ),
                InputItem(
                    input_id="in-amd-1",
                    role=InputRole.AMENDMENT,
                    filename="amd1.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="def456hash",
                    version=1,
                    issued_at="2026-07-20",
                    supersedes_input_ids=["in-tender"],
                ),
                InputItem(
                    input_id="in-amd-2",
                    role=InputRole.AMENDMENT,
                    filename="amd2.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="789xyzhash",
                    version=1,
                    issued_at="2026-07-25",
                    supersedes_input_ids=["in-tender"],
                ),
            ]
        )
        anchor_old = SourceAnchor(source_input_id="in-tender", chunk_id="chk-1", location="p:1")
        item_old = RequirementItem(
            requirement_id="R-old",
            kind=RequirementKind.MANDATORY,
            source_anchor=anchor_old,
            original_text="旧要求",
            normalized_requirement="旧要求",
            response_type="mandatory_response",
            evidence_policy="tender_traceable",
        )

        reconciled = agent.reconcile_amendments([item_old], manifest)
        self.assertEqual(reconciled[0].status, "waived")
        # Must be precise amd-2 since issued_at is later
        self.assertEqual(reconciled[0].superseded_by_input_id, "in-amd-2")

    def test_proposal_envelope_generation(self) -> None:
        agent = RequirementAgent(self.context)
        anchor = SourceAnchor(source_input_id="in-tender", chunk_id="chk-1", location="p:1")
        item = RequirementItem(
            requirement_id="R-1",
            kind=RequirementKind.MANDATORY,
            source_anchor=anchor,
            original_text="必须满足安全标准",
            normalized_requirement="必须满足安全标准",
            response_type="mandatory_response",
            evidence_policy="tender_traceable",
        )

        proposal = agent.create_extraction_proposal([item], base_revision=1, operation_id="op-req-1")
        self.assertEqual(proposal.artifact_kind, "RequirementLedger")
        self.assertEqual(proposal.producer_role, "requirement_agent")
        self.assertEqual(proposal.operation_id, "op-req-1")
        self.assertIn("in-tender", proposal.cited_source_ids)
        self.assertEqual(len(proposal.payload["requirements"]), 1)

    def test_full_agent_proposal_promotion_pipeline(self) -> None:
        from control_plane import ControlStore
        from document_pipeline.input_manifest import MANIFEST_PATH, V3_ROOT
        from document_pipeline.source_normalizer import SOURCE_INDEX_PATH
        from document_pipeline.stage_runner import V3StageRunner
        from utils import write_json

        (self.root / V3_ROOT).mkdir(parents=True, exist_ok=True)
        manifest = InputManifest(
            inputs=[
                InputItem(
                    input_id="in-tender",
                    role=InputRole.TENDER,
                    filename="tender.docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    sha256="hash123",
                    version=1,
                )
            ]
        )
        write_json(self.root / MANIFEST_PATH, manifest.model_dump(mode="json"))

        anchor = {"source_input_id": "in-tender", "chunk_id": "c1", "location": "p:1"}
        source_index = {
            "revision": 1,
            "source_hashes": {"in-tender": "hash123"},
            "blocks": [
                {
                    "block_id": "b1",
                    "input_id": "in-tender",
                    "input_role": "tender",
                    "block_kind": "paragraph",
                    "ordinal": 0,
                    "content": "投标人须具有良好信誉。",
                    "source_anchor": anchor,
                    "content_hash": "ch1",
                }
            ],
            "by_role": {"tender": [{"chunk_id": "c1", "content": "投标人须具有良好信誉。", "input_id": "in-tender", "source_anchor": anchor}]},
        }
        write_json(self.root / SOURCE_INDEX_PATH, source_index)

        runner = V3StageRunner(self.context)
        ledger = runner.run("build_requirement_ledger")

        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.revision, 1)

        store = ControlStore(self.context)
        active = store.v3_active_artifact("RequirementLedger")
        self.assertIsNotNone(active)
        self.assertEqual(active["revision"], 1)

        promoted_proposal = store.v3_proposal(active["proposal_id"])
        self.assertIsNotNone(promoted_proposal)
        self.assertEqual(promoted_proposal["producer_role"], "requirement_agent")
        self.assertEqual(promoted_proposal["status"], "promoted")
        self.assertFalse((self.root / V3_ROOT / "requirement_ledger.json").exists())

        repeated = runner.run("build_requirement_ledger")
        self.assertEqual(repeated.revision, 1)
        self.assertEqual(store.v3_active_artifact("RequirementLedger")["revision"], 1)


if __name__ == "__main__":
    unittest.main()

