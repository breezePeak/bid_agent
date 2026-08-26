from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.bid_rewrite_execution import BidRewriteExecutionService  # noqa: E402
from document_pipeline.bid_rewrite_execution import _CopyWriter  # noqa: E402
from document_pipeline.chapter_batch import ChapterBatchService  # noqa: E402
from document_pipeline.chapter_rewrite_plan import ChapterRewritePlanService  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.global_project_context import GlobalProjectContextService  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.legacy_bid_source import LegacyBidSourceService  # noqa: E402


class _ResearchSuccess:
    def invoke(self, need_id, *, provider_id=None):
        return {
            "provider_id": provider_id or "test",
            "need_id": need_id,
            "batch": {
                "batch_id": "EB-test",
                "need_id": need_id,
                "status": "published",
                "items": [{"evidence_id": "E-search-1", "content": "公开标准原文"}],
            },
        }


class _ResearchFailure:
    def invoke(self, need_id, *, provider_id=None):
        del need_id, provider_id
        raise RuntimeError("provider unavailable")


class V3ChapterRewritePlanTests(unittest.TestCase):
    def prepare(self, base: Path, mode: str = "bid_rewrite") -> tuple[WorkspaceContext, str]:
        runs = base / "runs"
        (runs / "alpha" / "workspace" / "v3").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        store = ControlStore(context)
        store.initialize_workspace_profile(mode)
        tender = base / "new-tender.md"
        tender.write_text(
            "# 新项目需求\n项目名称：新城云平台项目。采购人：新城采购中心。"
            "投标人须提供云平台实施、迁移和服务方案。\n\n"
            "# 评分办法\n实施步骤完整、服务保障明确，满分20分。",
            encoding="utf-8",
        )
        InputManifestService(context).register_local_file(tender, InputRole.TENDER)
        if mode == "full_write":
            company = base / "company.md"
            company.write_text("# 公司资料\n测试公司。", encoding="utf-8")
            InputManifestService(context).register_local_file(company, InputRole.COMPANY)
            return context, ""
        old_bid = base / "old-bid.md"
        old_bid.write_text(
            "# 云平台实施方案\n项目名称：旧城平台项目。采购人：旧城采购中心。"
            "本方案于2022年实施，工期30天，采用旧城业务系统，确保全部上线。\n\n"
            "## 迁移服务\n执行数据盘点、试迁移、正式迁移和验证。",
            encoding="utf-8",
        )
        LegacyBidSourceService(context).register_local_file(old_bid, old_bid.name)
        controller = V3ExecutionController.for_deterministic_tests(context)
        receipt = CommandGateway(context, controller.handlers()).submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.prepare_outline",
                    "expected_revision": store.revision(),
                    "idempotency_key": "rewrite-plan-outline",
                },
                workspace_id=context.workspace_id,
            )
        )
        self.assertEqual(receipt.status, "accepted", receipt.message)
        store.grant_workspace_access("owner")
        gateway = CommandGateway(context, controller.handlers())
        source_confirmation = gateway.submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.confirm_planning",
                    "payload": {
                        "decision": "confirm",
                        "planning_snapshot": HumanGateService(context).planning_snapshot(),
                    },
                    "actor": {"type": "user", "id": "owner"},
                    "expected_revision": store.revision(),
                    "idempotency_key": "rewrite-plan-source-confirmation",
                },
                workspace_id=context.workspace_id,
            )
        )
        self.assertEqual(source_confirmation.status, "accepted", source_confirmation.message)
        final_confirmation = gateway.submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.confirm_planning",
                    "payload": {
                        "decision": "confirm",
                        "planning_snapshot": HumanGateService(context).planning_snapshot(),
                    },
                    "actor": {"type": "user", "id": "owner"},
                    "expected_revision": store.revision(),
                    "idempotency_key": "rewrite-plan-final-confirmation",
                },
                workspace_id=context.workspace_id,
            )
        )
        self.assertEqual(final_confirmation.status, "accepted", final_confirmation.message)
        nodes = store.v3_active_artifact("ChapterBlueprint")["payload"]["nodes"]
        parents = {str(item.get("parent_chapter_id") or "") for item in nodes if item.get("parent_chapter_id")}
        leaves = [item for item in nodes if str(item["chapter_id"]) not in parents]
        leaf = str(next((item for item in leaves if item.get("legacy_sources")), leaves[0])["chapter_id"])
        return context, leaf

    @staticmethod
    def update(service, plan, operations):
        return service.update(
            plan["chapter_id"],
            expected_plan_revision=plan["plan_revision"],
            expected_plan_hash=plan["plan_hash"],
            operations=operations,
            actor={"type": "user", "id": "owner"},
        )

    @staticmethod
    def service(context, *, research_tool=None):
        global_context = GlobalProjectContextService(
            context
        ).load_for_deterministic_tests()
        global_context["confirmed_facts"] = [
            *list(global_context.get("confirmed_facts") or []),
            {
                "fact_id": "F-new-project-source",
                "statement": "新城云平台项目，采购人为新城采购中心，以新招标要求为准。",
            },
        ]
        return ChapterRewritePlanService(
            context,
            research_tool=research_tool,
            global_context_override=global_context,
        )

    def test_structured_edits_cas_pollution_confirmation_and_stale_recovery(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, leaf = self.prepare(Path(temporary))
            controller = V3ExecutionController.for_deterministic_tests(context)
            self.assertTrue(
                {
                    "bid_rewrite.plan.generate",
                    "bid_rewrite.plan.update",
                    "bid_rewrite.plan.search",
                    "bid_rewrite.plan.confirm",
                    "bid_rewrite.plan.reopen",
                    "bid_rewrite.chapter.execute",
                }.issubset(controller.handlers())
            )
            service = self.service(context)
            plan = service.generate(leaf, actor={"type": "user", "id": "owner"})
            self.assertEqual(plan["status"], "draft")
            self.assertTrue(plan["selected_legacy_blocks"])
            legacy = ControlStore(context).v3_active_artifact("LegacyBidIndex")["payload"]
            pollution_block = next(item for item in legacy["blocks"] if "2022" in item.get("content", ""))
            pollution_section = next(
                item
                for item in legacy["sections"]
                if pollution_block["block_id"]
                in [item.get("heading_block_id"), *(item.get("content_block_ids") or [])]
            )
            selected = {
                "section_id": pollution_section["section_id"],
                "block_id": pollution_block["block_id"],
                "content_hash": pollution_block["content_hash"],
                "usage": "light_edit",
                "instruction": "",
            }
            for bad_operation in (
                {
                    "op": "select_legacy_block",
                    "section_id": "unknown-section",
                    "block_id": pollution_block["block_id"],
                    "content_hash": pollution_block["content_hash"],
                },
                {
                    "op": "select_legacy_block",
                    "section_id": pollution_section["section_id"],
                    "block_id": pollution_block["block_id"],
                    "content_hash": "stale-content-hash",
                },
            ):
                with self.assertRaises(ControlPlaneError) as invalid_reference:
                    service.update(
                        leaf,
                        expected_plan_revision=plan["plan_revision"],
                        expected_plan_hash=plan["plan_hash"],
                        operations=[bad_operation],
                    )
                self.assertEqual(
                    invalid_reference.exception.code,
                    "CHAPTER_REWRITE_LEGACY_REFERENCE_INVALID",
                )

            with self.assertRaises(ControlPlaneError) as conflict:
                service.update(
                    leaf,
                    expected_plan_revision=plan["plan_revision"] - 1,
                    expected_plan_hash=plan["plan_hash"],
                    operations=[{"op": "set_strategy", "strategy": "new_write"}],
                )
            self.assertEqual(conflict.exception.code, "CHAPTER_REWRITE_PLAN_CONFLICT")

            plan = self.update(
                service,
                plan,
                [
                    {"op": "unselect_legacy_block", "block_id": item["block_id"]}
                    for item in plan["selected_legacy_blocks"]
                ],
            )
            self.assertIsInstance(plan["warnings"], list)
            plan = self.update(
                service,
                plan,
                [
                    {"op": "select_legacy_block", **selected},
                    {"op": "change_block_usage", "block_id": selected["block_id"], "usage": "restructure"},
                    {"op": "update_instruction", "block_id": selected["block_id"], "instruction": "删除旧项目信息后重组"},
                    {"op": "update_instruction", "instruction": "优先满足新招标要求"},
                    {"op": "set_strategy", "strategy": "restructure"},
                ],
            )
            self.assertEqual(plan["strategy"], "restructure")
            self.assertEqual(plan["instruction"], "优先满足新招标要求")
            self.assertEqual(plan["new_content_items"], [])

            unresolved = [item for item in plan["pollution_findings"] if item["status"] != "resolved"]
            self.assertTrue(unresolved)
            with self.assertRaises(ControlPlaneError) as polluted:
                service.confirm(
                    leaf,
                    expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                    plan_revision=plan["plan_revision"],
                    plan_hash=plan["plan_hash"],
                    principal_id="owner",
                )
            self.assertEqual(polluted.exception.code, "CHAPTER_REWRITE_POLLUTION_UNRESOLVED")
            with self.assertRaises(ControlPlaneError) as invalid_replacement:
                self.update(
                    service,
                    plan,
                    [{
                        "op": "resolve_pollution",
                        "finding_id": unresolved[0]["finding_id"],
                        "replacement_fact_id": "F-unconfirmed",
                    }],
                )
            self.assertEqual(
                invalid_replacement.exception.code,
                "CHAPTER_REWRITE_REPLACEMENT_INVALID",
            )
            plan = self.update(
                service,
                plan,
                [
                    {
                        "op": "resolve_pollution",
                        "finding_id": finding["finding_id"],
                        "replacement_fact_id": "F-new-project-source",
                    }
                    for finding in unresolved
                ],
            )
            self.assertTrue(all(item["status"] == "resolved" for item in plan["pollution_findings"]))
            confirmation = service.confirm(
                leaf,
                expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                plan_revision=plan["plan_revision"],
                plan_hash=plan["plan_hash"],
                principal_id="owner",
            )
            self.assertEqual(confirmation["plan_hash"], plan["plan_hash"])
            self.assertEqual(service.get(leaf)["status"], "confirmed")

            plan = service.reopen(
                leaf,
                expected_plan_revision=plan["plan_revision"],
                expected_plan_hash=plan["plan_hash"],
                actor={"type": "user", "id": "owner"},
            )
            self.assertEqual(plan["status"], "draft")
            chapter = ChapterWorkspaceService(context).get_chapter(leaf)
            items = list((chapter.get("context") or {}).get("items") or [])
            items.append({"item_id": "user:rewrite-stale", "kind": "GOAL", "title": "新增要求", "body": "增加验收说明", "order": len(items), "source": "USER", "origin_ref": None})
            ControlStore(context).append_chapter_context_revision(
                chapter_id=leaf,
                expected_chapter_revision=int(chapter["chapter_revision"]),
                items=items,
                seeded_from_blueprint=False,
                actor={"type": "user", "id": "owner"},
            )
            self.assertTrue(service.get(leaf)["stale"])
            self.assertIn("chapter_context_hash", service.get(leaf)["stale_reasons"])
            self.assertGreaterEqual(len(ControlStore(context).chapter_rewrite_events(leaf)), 1)

    def test_search_success_failure_and_forbidden_targets(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, leaf = self.prepare(Path(temporary))
            service = self.service(context)
            plan = service.generate(leaf)
            plan = self.update(
                service,
                plan,
                [{"op": "set_strategy", "strategy": "new_write"}],
            )
            search_item_id = plan["new_content_items"][0]["item_id"]
            with self.assertRaises(ControlPlaneError) as forbidden:
                service.search(
                    leaf,
                    expected_plan_revision=plan["plan_revision"],
                    expected_plan_hash=plan["plan_hash"],
                    item_id=search_item_id,
                    query="搜索本公司人员业绩承诺",
                )
            self.assertEqual(forbidden.exception.code, "CHAPTER_REWRITE_SEARCH_FORBIDDEN")

            success_service = self.service(context, research_tool=_ResearchSuccess())
            success = success_service.search(
                leaf,
                expected_plan_revision=plan["plan_revision"],
                expected_plan_hash=plan["plan_hash"],
                item_id=search_item_id,
                query="查询云平台数据迁移国家标准",
                actor={"type": "user", "id": "owner"},
            )
            item = next(value for value in success["new_content_items"] if value["item_id"] == search_item_id)
            self.assertIn("E-search-1", item["evidence_ids"])
            before_revision = success["plan_revision"]
            with self.assertRaises(ControlPlaneError) as failed:
                self.service(context, research_tool=_ResearchFailure()).search(
                    leaf,
                    expected_plan_revision=success["plan_revision"],
                    expected_plan_hash=success["plan_hash"],
                    item_id=search_item_id,
                    query="查询另一个公开技术标准",
                )
            self.assertEqual(failed.exception.code, "CHAPTER_REWRITE_SEARCH_FAILED")
            self.assertEqual(service.get(leaf)["plan_revision"], before_revision)

    def test_full_write_rejects_rewrite_plan_commands(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, _ = self.prepare(Path(temporary), "full_write")
            with self.assertRaises(ControlPlaneError) as blocked:
                ChapterRewritePlanService(context).generate("chapter-x")
            self.assertEqual(blocked.exception.code, "REWRITE_MODE_REQUIRED")

    def test_copy_writer_applies_confirmed_replacements_without_model_writer(self) -> None:
        class Bundle:
            bundle_id = "bundle-copy"
            bundle_hash = "hash-copy"
            chapter_id = "CH-1"
            document_target_constraints = [{"output_target": "CH-1"}]

        blocks = _CopyWriter(
            {
                "selected_legacy_sources": [{"content": "采购人：旧城采购中心，工期30天"}],
                "replacement_map": [
                    {"source_text": "旧城采购中心", "replacement_text": "新城采购中心"},
                    {"source_text": "工期30天", "replacement_text": "按新招标要求执行"},
                ],
            }
        ).stream_bundle(Bundle())

        self.assertEqual(len(blocks), 1)
        self.assertIn("新城采购中心", blocks[0].content)
        self.assertNotIn("旧城采购中心", blocks[0].content)
        self.assertNotIn("工期30天", blocks[0].content)

    def test_execution_requires_confirmation_and_freezes_approved_rewrite_context(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, leaf = self.prepare(Path(temporary))
            plan_service = self.service(context)
            plan = plan_service.generate(leaf, actor={"type": "user", "id": "owner"})
            execution = BidRewriteExecutionService(context, plan_service=plan_service)
            with self.assertRaises(ControlPlaneError) as incomplete:
                execution.build_request(
                    leaf,
                    operation_id="rewrite-execution-test",
                    expected_workspace_revision=ControlStore(context).revision(),
                    expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                    actor={"type": "user", "id": "owner"},
                )
            self.assertEqual(incomplete.exception.code, "REWRITE_PLAN_INCOMPLETE")

            operations = [{"op": "set_strategy", "strategy": "copy"}]
            operations.extend(
                {
                    "op": "resolve_pollution",
                    "finding_id": finding["finding_id"],
                    "replacement_fact_id": "F-new-project-source",
                }
                for finding in plan["pollution_findings"]
                if finding["status"] != "resolved"
            )
            plan = self.update(plan_service, plan, operations)
            plan_service.confirm(
                leaf,
                expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                plan_revision=plan["plan_revision"],
                plan_hash=plan["plan_hash"],
                principal_id="owner",
            )
            request = execution.build_request(
                leaf,
                operation_id="rewrite-execution-test",
                expected_workspace_revision=ControlStore(context).revision(),
                expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                actor={"type": "user", "id": "owner"},
            )
            rewrite_context = request.chapter_writing_plan["rewrite_context"]
            self.assertEqual(request.operation, "rewrite")
            self.assertFalse(request.run_research)
            self.assertEqual(rewrite_context["rewrite_strategy"], "copy")
            self.assertTrue(rewrite_context["selected_legacy_sources"])
            self.assertEqual(
                bool(rewrite_context["replacement_map"]),
                bool(plan["pollution_findings"]),
            )

    def test_batch_snapshots_the_confirmed_rewrite_plan(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context, leaf = self.prepare(Path(temporary))
            plan_service = self.service(context)
            plan = plan_service.generate(leaf, actor={"type": "user", "id": "owner"})
            plan = self.update(
                plan_service,
                plan,
                [
                    {"op": "set_strategy", "strategy": "copy"},
                    *[
                        {
                            "op": "resolve_pollution",
                            "finding_id": finding["finding_id"],
                            "replacement_fact_id": "F-new-project-source",
                        }
                        for finding in plan["pollution_findings"]
                        if finding["status"] != "resolved"
                    ],
                ],
            )
            plan_service.confirm(
                leaf,
                expected_chapter_revision=plan["dependencies"]["chapter_revision"],
                plan_revision=plan["plan_revision"],
                plan_hash=plan["plan_hash"],
                principal_id="owner",
            )
            batch_service = ChapterBatchService(context)
            with mock.patch("document_pipeline.chapter_rewrite_plan.ChapterRewritePlanService") as plans:
                plans.return_value.get.return_value = plan_service.get(leaf)
                frozen = batch_service._rewrite_plan_ref(leaf)
            self.assertEqual(frozen["plan_revision"], plan["plan_revision"])
            self.assertEqual(frozen["plan_hash"], plan["plan_hash"])
            self.assertEqual(frozen["strategy"], "copy")


if __name__ == "__main__":
    unittest.main()
