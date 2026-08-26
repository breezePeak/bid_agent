from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.chapter_rewrite_match import (  # noqa: E402
    ChapterRewriteMatchService,
    project_rewrite_coverage,
)
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.legacy_bid_source import LegacyBidSourceService  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


class V3ChapterRewriteMatchTests(unittest.TestCase):
    def prepare(self, base: Path) -> tuple[WorkspaceContext, LegacyBidSourceService]:
        runs = base / "runs"
        (runs / "alpha" / "workspace" / "v3").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        store = ControlStore(context)
        store.initialize_workspace_profile("bid_rewrite")
        tender = base / "new-tender.md"
        tender.write_text(
            "# 项目需求\n投标人须提供云平台实施方案、迁移计划和服务保障。\n\n"
            "# 评分办法\n云平台实施方案完整、迁移步骤明确、服务保障可执行，满分20分。",
            encoding="utf-8",
        )
        old_bid = base / "old-bid.md"
        old_bid.write_text(
            "# 云平台实施方案\n本项目采用分阶段实施，包含现状调研、平台部署和上线验证。\n\n"
            "## 迁移计划\n迁移工作包括数据盘点、试迁移、正式迁移和回退验证。\n\n"
            "## 服务保障\n提供服务台、响应时限和问题闭环机制。",
            encoding="utf-8",
        )
        InputManifestService(context).register_local_file(tender, InputRole.TENDER)
        legacy = LegacyBidSourceService(context)
        legacy.register_local_file(old_bid, old_bid.name)
        controller = V3ExecutionController.for_deterministic_tests(context)
        receipt = CommandGateway(context, controller.handlers()).submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.prepare_outline",
                    "expected_revision": store.revision(),
                    "idempotency_key": "prepare-rewrite-outline",
                },
                workspace_id=context.workspace_id,
            )
        )
        self.assertEqual(receipt.status, "accepted", receipt.message)
        store.grant_workspace_access("owner")
        source_confirmation = CommandGateway(context, controller.handlers()).submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.confirm_planning",
                    "payload": {
                        "decision": "confirm",
                        "planning_snapshot": HumanGateService(context).planning_snapshot(),
                    },
                    "actor": {"type": "user", "id": "owner"},
                    "expected_revision": store.revision(),
                    "idempotency_key": "confirm-rewrite-source-outline",
                },
                workspace_id=context.workspace_id,
            )
        )
        self.assertEqual(source_confirmation.status, "accepted", source_confirmation.message)
        self.assertEqual(source_confirmation.result["operation_status"], "blocked_human")
        return context, legacy

    @staticmethod
    def leaf_and_parent(store: ControlStore) -> tuple[str, str | None]:
        nodes = (store.v3_active_artifact("ChapterBlueprint")["payload"])["nodes"]
        parents = {
            str(item.get("parent_chapter_id") or "")
            for item in nodes
            if item.get("parent_chapter_id")
        }
        leaves = [item for item in nodes if str(item["chapter_id"]) not in parents]
        leaf = str(next((item for item in leaves if item.get("legacy_sources")), leaves[0])["chapter_id"])
        parent = next((str(item["chapter_id"]) for item in nodes if str(item["chapter_id"]) in parents), None)
        return leaf, parent

    def test_h1_leaf_exact_refs_command_and_staleness(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            context, legacy_service = self.prepare(base)
            store = ControlStore(context)
            leaf, parent = self.leaf_and_parent(store)
            service = ChapterRewriteMatchService(context)

            with self.assertRaises(ControlPlaneError) as blocked:
                service.generate(leaf)
            self.assertEqual(blocked.exception.code, "PLANNING_CONFIRM_REQUIRED")

            store.grant_workspace_access("owner")
            gate = HumanGateService(context)
            gate.confirm_planning(
                principal_id="owner",
                submitted_snapshot=gate.planning_snapshot(),
                nonce="rewrite-match-h1",
            )
            if parent:
                with self.assertRaises(ControlPlaneError) as structural:
                    service.generate(parent)
                self.assertEqual(
                    structural.exception.code,
                    "CHAPTER_REWRITE_MATCH_LEAF_REQUIRED",
                )

            result = service.generate(leaf)
            self.assertTrue(result["read_only"])
            self.assertTrue(result["matches"])
            self.assertIn(
                result["recommendation"]["strategy"],
                {"copy", "light_edit", "restructure", "new_write"},
            )
            self.assertTrue(
                all(
                    row["status"]
                    in {"fully_covered", "partially_covered", "not_covered", "conflicted"}
                    for row in result["coverage"]
                )
            )
            legacy = store.v3_active_artifact("LegacyBidIndex")["payload"]
            sections = {item["section_id"] for item in legacy["sections"]}
            blocks = {item["block_id"]: item for item in legacy["blocks"]}
            for match in result["matches"]:
                self.assertIn(match["section_id"], sections)
                self.assertIn(match["block_id"], blocks)
                self.assertEqual(match["content"], blocks[match["block_id"]]["content"])
                self.assertEqual(match["content_hash"], blocks[match["block_id"]]["content_hash"])
            projected = V3WorkspaceSnapshotBuilder(context).build()["chapters"]["items"]
            leaf_projection = next(item for item in projected if item["chapter_id"] == leaf)
            self.assertFalse(leaf_projection["rewrite_match"]["stale"])

            controller = V3ExecutionController.for_deterministic_tests(context)
            receipt = CommandGateway(context, controller.handlers()).submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "bid_rewrite.match.generate",
                        "payload": {"chapter_id": leaf},
                        "expected_revision": store.revision(),
                        "idempotency_key": "rewrite-match-command",
                    },
                    workspace_id=context.workspace_id,
                )
            )
            self.assertEqual(receipt.status, "accepted", receipt.message)
            self.assertEqual(receipt.result["rewrite_match"]["chapter_id"], leaf)

            replacement = base / "replacement.md"
            replacement.write_text("# 替换旧标书\n另一套完全不同的内容。", encoding="utf-8")
            legacy_service.register_local_file(replacement, replacement.name)
            with self.assertRaises(ControlPlaneError) as stale:
                service.latest(leaf)
            self.assertEqual(stale.exception.code, "CHAPTER_REWRITE_MATCH_STALE")

    def test_match_uses_only_blueprint_legacy_references(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, _ = self.prepare(Path(temporary))
            store = ControlStore(context)
            leaf, _ = self.leaf_and_parent(store)
            store.grant_workspace_access("owner")
            gate = HumanGateService(context)
            gate.confirm_planning(
                principal_id="owner",
                submitted_snapshot=gate.planning_snapshot(),
                nonce="rewrite-match-forged-h1",
            )
            result = ChapterRewriteMatchService(context).generate(leaf)
            self.assertEqual(result["reranker"]["provider_id"], "planning.rewrite_outline_merge")
            blueprint = store.v3_active_artifact("ChapterBlueprint")["payload"]
            node = next(item for item in blueprint["nodes"] if item["chapter_id"] == leaf)
            self.assertEqual(
                {item["block_id"] for item in result["matches"]},
                {item["block_id"] for item in node["legacy_sources"]},
            )

    def test_strategy_projects_coverage_without_similarity_thresholds(self) -> None:
        writing_plan = {
            "blocks": [
                {"block_id": "W1", "heading": "方案", "must_answer": "实施"},
                {"block_id": "W2", "heading": "保障", "must_answer": "服务"},
            ]
        }
        sources = [{"block_id": "B1"}, {"block_id": "B2"}]
        expected = {
            "copy": "fully_covered",
            "light_edit": "partially_covered",
            "restructure": "partially_covered",
            "new_write": "not_covered",
        }
        for strategy, status in expected.items():
            coverage = project_rewrite_coverage(
                writing_plan,
                strategy,
                sources,
                ["按新要求修改"],
            )
            self.assertEqual({item["status"] for item in coverage}, {status})
            expected_ids = [] if strategy == "new_write" else ["B1", "B2"]
            self.assertTrue(
                all(item["matched_block_ids"] == expected_ids for item in coverage)
            )


if __name__ == "__main__":
    unittest.main()
