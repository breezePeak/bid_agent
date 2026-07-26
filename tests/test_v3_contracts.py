from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    ArtifactManifest,
    ContentBlock,
    DocumentPlan,
    DocumentNodePlan,
    InputItem,
    InputManifest,
    InputRole,
    OutlineContract,
    TemplateContract,
    TemplateSlot,
    ContractNode,
    document_contract_json_schema,
)


class V3ContractTests(unittest.TestCase):
    def test_input_manifest_rejects_multiple_active_templates(self) -> None:
        with self.assertRaises(ValidationError):
            InputManifest(
                inputs=[
                    InputItem(input_id="t1", role=InputRole.TEMPLATE, filename="a.docx", mime_type="application/docx", sha256="a", version=1),
                    InputItem(input_id="t2", role=InputRole.TEMPLATE, filename="b.docx", mime_type="application/docx", sha256="b", version=1),
                ]
            )

    def test_document_contract_rejects_dangling_references(self) -> None:
        with self.assertRaisesRegex(ValidationError, "悬空父节点"):
            OutlineContract(nodes=[ContractNode(node_id="n1", parent_node_id="missing", order=0, writable_target="n1", title="第一章")])
        with self.assertRaisesRegex(ValidationError, "未知节点"):
            TemplateContract(
                template_hash="hash",
                structural_fingerprint="fingerprint",
                nodes=[ContractNode(node_id="n1", order=0, writable_target="n1", title="第一章")],
                slots=[TemplateSlot(slot_id="s1", node_id="missing", kind="text_slot", anchor="p:1")],
            )

    def test_document_plan_requires_one_primary_owner(self) -> None:
        with self.assertRaisesRegex(ValidationError, "只能有一个 primary_owner"):
            DocumentPlan(
                contract_revision=1,
                nodes=[
                    DocumentNodePlan(node_id="n1", primary_requirement_ids=["R1"]),
                    DocumentNodePlan(node_id="n2", primary_requirement_ids=["R1"]),
                ],
            )

    def test_critical_content_claim_needs_evidence_or_fact(self) -> None:
        with self.assertRaisesRegex(ValidationError, "关键 Claim"):
            ContentBlock(
                block_id="b1",
                target_node_id="n1",
                type="paragraph",
                content="承诺 30 天完成。",
                confidence=0.8,
                critical_claims=["工期"],
            )

    def test_v3_artifacts_cannot_use_legacy_namespace(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(artifact_id="a1", artifact_path="workspace/outline.json", producer="test", dependency_fingerprint="hash")
        manifest = ArtifactManifest(artifact_id="a1", artifact_path="workspace/v3/document_plan.json", producer="test", dependency_fingerprint="hash")
        self.assertEqual(manifest.schema_version, "v3")

    def test_document_contract_schema_exposes_discriminated_modes(self) -> None:
        schema = document_contract_json_schema()
        self.assertIn("oneOf", schema)
        self.assertIn("discriminator", schema)

    def test_control_store_creates_v3_schema_tables(self) -> None:
        # Windows can retain the SQLite WAL handle briefly after ControlStore
        # initialization; cleanup is unrelated to the schema assertion.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            with sqlite3.connect(store.path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                version = connection.execute("SELECT value FROM control_meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertTrue({"document_state", "evidence_needs", "content_unit_states", "dependency_edges", "change_sets", "content_locks"}.issubset(tables))
            self.assertEqual(version, str(ControlStore.SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
