from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.legacy_bid_source import LegacyBidSourceService  # noqa: E402
from document_pipeline.canonicalization import canonical_json  # noqa: E402
from document_pipeline.planning_inference import (  # noqa: E402
    REWRITE_OUTLINE_CAPABILITY_VERSION,
    REWRITE_OUTLINE_PROMPT_FILE,
    REWRITE_OUTLINE_PROMPT_VERSION,
    REWRITE_OUTLINE_SCHEMA_VERSION,
    REWRITE_OUTLINE_SKILL_ID,
    StructuredInferenceResult,
    planning_prompt_hash,
    rewrite_outline_prompt_hash,
)
from document_pipeline.rewrite_outline_merge_skill import (  # noqa: E402
    InitialOutlineCard,
    LegacyBlockCard,
    LegacySectionCard,
    LLMRewriteOutlineMergeProvider,
    RewriteOutlineAlignment,
    RewriteOutlineMergeInput,
    RewriteOutlineMergeCandidate,
)
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.rewrite_zero_pollution import (  # noqa: E402
    CORE_ARTIFACT_KINDS,
    audit_rewrite_zero_pollution,
)
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


class V3BidRewriteOutlineTests(unittest.TestCase):
    def context(self, base: Path, mode: str = "bid_rewrite") -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha" / "workspace" / "v3").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        ControlStore(context).initialize_workspace_profile(mode)
        return context

    @staticmethod
    def command(context: WorkspaceContext, key: str):
        controller = V3ExecutionController.for_deterministic_tests(context)
        return CommandGateway(context, controller.handlers()).submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.prepare_outline",
                    "expected_revision": ControlStore(context).revision(),
                    "idempotency_key": key,
                },
                workspace_id=context.workspace_id,
            )
        )

    def test_rewrite_readiness_requires_new_tender_and_ready_legacy_bid(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            missing_both = self.context(base)
            receipt = self.command(missing_both, "rewrite-missing-tender")
            self.assertEqual(receipt.status, "rejected")
            self.assertEqual(receipt.error["code"], "REWRITE_TENDER_REQUIRED")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            context = self.context(base)
            tender = base / "tender.md"
            tender.write_text("# 新招标需求\n实施方案，满分10分。", encoding="utf-8")
            InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            receipt = self.command(context, "rewrite-missing-legacy")
            self.assertEqual(receipt.status, "rejected")
            self.assertEqual(receipt.error["code"], "REWRITE_LEGACY_BID_REQUIRED")

    def test_new_outline_is_zero_pollution_and_legacy_replacement_stales_blueprint_h1(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            context = self.context(base)
            tender = base / "new-tender.md"
            tender.write_text(
                "# 新项目需求\n投标人须提供云平台实施方案。\n\n"
                "# 评标办法\n云平台实施方案完整性，满分10分。",
                encoding="utf-8",
            )
            old_bid = base / "old-bid.md"
            old_bid.write_text(
                "# 完全不同的旧目录\nLEGACY_ONLY_MARKER_92731\n旧系统迁移方案。",
                encoding="utf-8",
            )
            InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            legacy_service = LegacyBidSourceService(context)
            first_legacy = legacy_service.register_local_file(old_bid, old_bid.name)

            before = V3WorkspaceSnapshotBuilder(context).build()
            self.assertTrue(before["material_readiness"]["ready"])
            self.assertFalse(before["material_readiness"]["items"]["company"]["required"])
            receipt = self.command(context, "rewrite-outline")
            self.assertEqual(receipt.status, "accepted", receipt.message)
            self.assertEqual(receipt.result["operation_status"], "blocked_human")
            initial_stages = {
                item["stage_id"]: item
                for item in V3WorkspaceSnapshotBuilder(context).build()["analysis"]["pipeline"]["stages"]
            }
            self.assertEqual(
                initial_stages["compile_source_outline"]["label"],
                "根据招标文件生成原始目录",
            )
            self.assertEqual(initial_stages["confirm_source_outline"]["status"], "blocked_human")
            self.assertEqual(initial_stages["merge_rewrite_outline"]["status"], "pending")
            self.assertEqual(initial_stages["confirm_planning"]["status"], "pending")
            self.assertEqual(audit_rewrite_zero_pollution(context)["status"], "pass")

            store = ControlStore(context)
            legacy_index = legacy_service.index(first_legacy.legacy_bid_id)
            forbidden = {
                first_legacy.legacy_bid_id,
                legacy_index.file_hash,
                "LEGACY_ONLY_MARKER_92731",
                *[block.block_id for block in legacy_index.blocks],
            }
            core_before = {
                kind: (
                    store.v3_active_artifact(kind)["revision"],
                    store.v3_active_artifact(kind)["artifact_hash"],
                )
                for kind in CORE_ARTIFACT_KINDS
            }
            serialized = json.dumps(
                {
                    kind: store.v3_active_artifact(kind)["payload"]
                    for kind in CORE_ARTIFACT_KINDS
                },
                ensure_ascii=False,
            )
            self.assertTrue(all(value not in serialized for value in forbidden))

            store.grant_workspace_access("owner")
            gate = HumanGateService(context)
            confirmation_controller = V3ExecutionController.for_deterministic_tests(context)
            confirmation_gateway = CommandGateway(
                context,
                confirmation_controller.handlers(),
            )
            source_confirmation = confirmation_gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "document.confirm_planning",
                        "payload": {
                            "decision": "confirm",
                            "planning_snapshot": gate.planning_snapshot(),
                        },
                        "actor": {"type": "user", "id": "owner"},
                        "expected_revision": store.revision(),
                        "idempotency_key": "rewrite-source-outline-confirmation",
                    },
                    workspace_id=context.workspace_id,
                )
            )
            self.assertEqual(source_confirmation.status, "accepted", source_confirmation.as_dict())
            self.assertEqual(
                store.v3_active_artifact("ChapterBlueprint")["payload"]["planning_model"],
                "rewrite_merge",
                source_confirmation.as_dict(),
            )
            self.assertEqual(
                source_confirmation.result.get("operation_status"),
                "blocked_human",
                source_confirmation.as_dict(),
            )
            merged_stages = {
                item["stage_id"]: item
                for item in V3WorkspaceSnapshotBuilder(context).build()["analysis"]["pipeline"]["stages"]
            }
            self.assertEqual(merged_stages["confirm_source_outline"]["status"], "succeeded")
            self.assertEqual(merged_stages["merge_rewrite_outline"]["status"], "succeeded")
            self.assertEqual(merged_stages["confirm_planning"]["status"], "blocked_human")
            self.assertEqual(
                store.v3_active_artifact("ChapterBlueprint")["payload"]["planning_model"],
                "rewrite_merge",
            )
            final_confirmation = confirmation_gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "document.confirm_planning",
                        "payload": {
                            "decision": "confirm",
                            "planning_snapshot": gate.planning_snapshot(),
                        },
                        "actor": {"type": "user", "id": "owner"},
                        "expected_revision": store.revision(),
                        "idempotency_key": "rewrite-final-outline-confirmation",
                    },
                    workspace_id=context.workspace_id,
                )
            )
            self.assertEqual(final_confirmation.status, "accepted")
            self.assertEqual(
                final_confirmation.result["operation_status"],
                "succeeded",
            )

            replacement = base / "replacement-old-bid.md"
            replacement.write_text(
                "# 另一份旧目录\nSECOND_LEGACY_ONLY_MARKER_66318",
                encoding="utf-8",
            )
            legacy_service.register_local_file(replacement, replacement.name)
            after = V3WorkspaceSnapshotBuilder(context).build()
            self.assertTrue(after["analysis"]["stale"])
            self.assertEqual(after["planning"]["status"], "outdated")
            with self.assertRaises(ControlPlaneError) as stale_h1:
                gate.require_current_confirmation()
            self.assertEqual(stale_h1.exception.code, "PLANNING_CONFIRM_STALE")
            self.assertEqual(
                core_before,
                {
                    kind: (
                        store.v3_active_artifact(kind)["revision"],
                        store.v3_active_artifact(kind)["artifact_hash"],
                    )
                    for kind in CORE_ARTIFACT_KINDS
                },
            )

            updated_tender = base / "updated-tender.md"
            updated_tender.write_text(
                "# 新项目需求\n替换后的招标要求。\n\n# 评标办法\n服务保障，满分20分。",
                encoding="utf-8",
            )
            InputManifestService(context).register_local_file(
                updated_tender,
                InputRole.TENDER,
            )
            stale = V3WorkspaceSnapshotBuilder(context).build()
            self.assertTrue(stale["analysis"]["stale"])
            self.assertEqual(stale["planning"]["status"], "outdated")

    def test_full_write_readiness_projection_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            context = self.context(base, "full_write")
            tender = base / "tender.md"
            company = base / "company.md"
            tender.write_text("# 招标需求", encoding="utf-8")
            company.write_text("# 公司资料", encoding="utf-8")
            inputs = InputManifestService(context)
            inputs.register_local_file(tender, InputRole.TENDER)
            self.assertFalse(
                V3WorkspaceSnapshotBuilder(context).build()["material_readiness"]["ready"]
            )
            inputs.register_local_file(company, InputRole.COMPANY)
            readiness = V3WorkspaceSnapshotBuilder(context).build()["material_readiness"]
            self.assertTrue(readiness["ready"])
            self.assertEqual(readiness["required"], ["tender", "company"])

    def test_feedback_and_explicit_regeneration_call_merge_provider(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            context = self.context(base)
            tender = base / "new-tender.md"
            tender.write_text(
                "# 项目需求\n投标人须提供云平台实施方案、迁移计划和服务保障。\n\n"
                "# 评分办法\n云平台实施方案完整、迁移步骤明确、服务保障可执行，满分20分。",
                encoding="utf-8",
            )
            old_bid = base / "old-bid.md"
            old_bid.write_text(
                "# 云平台实施方案\n本项目采用分阶段实施，包含现状调研、平台部署和上线验证。\n\n"
                "## 迁移计划\n迁移工作包括数据盘点、试迁移、正式迁移和回退验证。",
                encoding="utf-8",
            )
            InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            LegacyBidSourceService(context).register_local_file(old_bid, old_bid.name)
            initial = self.command(context, "rewrite-outline-provider-seed")
            self.assertEqual(initial.status, "accepted", initial.message)

            store = ControlStore(context)
            class Provider:
                skill_id = REWRITE_OUTLINE_SKILL_ID
                capability_version = REWRITE_OUTLINE_CAPABILITY_VERSION
                prompt_version = REWRITE_OUTLINE_PROMPT_VERSION
                prompt_hash = rewrite_outline_prompt_hash()
                schema_version = REWRITE_OUTLINE_SCHEMA_VERSION
                provider_fingerprint = "test-rewrite-provider-v2"
                model_fingerprint = "test-rewrite-model-v2"
                temperature = 0.1

                def __init__(self):
                    self.requests = []

                def merge(self, request):
                    self.requests.append(request)
                    targets = {item.node_id: item for item in request.initial_outline}
                    used_targets = set()
                    alignments = []
                    for section in request.legacy_sections:
                        target = targets[section.candidate_target_ids[0]]
                        response_ids = (
                            target.direct_response_unit_ids
                            or target.subtree_response_unit_ids[:1]
                        )
                        condition_ids = (
                            target.direct_condition_ids
                            or target.subtree_condition_ids[:1]
                        )
                        requirement_ids = (
                            target.direct_requirement_ids
                            or target.subtree_requirement_ids[:1]
                        )
                        if not (response_ids or condition_ids or requirement_ids):
                            alignments.append(RewriteOutlineAlignment(
                                legacy_section_id=section.section_id,
                                placement="ignore",
                                confidence=1.0,
                            ))
                            continue
                        placement = (
                            "child_detail"
                            if target.node_id in used_targets
                            else "same_scope"
                        )
                        used_targets.add(target.node_id)
                        sources = [
                            {
                                "section_id": section.section_id,
                                "block_id": block.block_id,
                                "content_hash": block.content_hash,
                            }
                            for block in section.blocks
                        ]
                        alignments.append(RewriteOutlineAlignment(
                            legacy_section_id=section.section_id,
                            target_node_id=target.node_id,
                            placement=placement,
                            matched_response_unit_ids=response_ids,
                            matched_condition_ids=condition_ids,
                            matched_requirement_ids=requirement_ids,
                            rewrite_mode="light_edit" if sources else "new_write",
                            legacy_sources=sources,
                            reason="测试 Provider",
                            required_changes=["按新要求修改"] if sources else [],
                            confidence=1.0,
                        ))
                    candidate = RewriteOutlineMergeCandidate(
                        alignments=alignments,
                        review_status="draft",
                    )
                    normalized = canonical_json(candidate.model_dump(mode="json"))
                    return StructuredInferenceResult(
                        candidate=candidate,
                        raw_output=normalized,
                        normalized_output=normalized,
                        input_snapshot=canonical_json(request.model_dump(mode="json")),
                        attempt_count=1,
                        capability_id=self.skill_id,
                        prompt_version=self.prompt_version,
                        prompt_hash=self.prompt_hash,
                        schema_version=self.schema_version,
                        provider_fingerprint=self.provider_fingerprint,
                        model_fingerprint=self.model_fingerprint,
                        temperature=self.temperature,
                        reasoning="",
                        normalized_reference_count=0,
                        validation_errors=(),
                    )

            provider = Provider()
            runner = V3StageRunner.for_deterministic_tests(
                context,
                rewrite_outline_merge_provider=provider,
            )
            feedback = "不要采用旧标书中的培训章节"
            runner.request_outline_revision(feedback)
            runner.request_inference_regeneration([REWRITE_OUTLINE_SKILL_ID])
            runner.run("compile_chapter_blueprint", operation_id="feedback-run")
            self.assertEqual(provider.requests[-1].review_feedback, feedback)
            self.assertEqual(
                store.v3_active_artifact("ChapterBlueprint")["payload"]["review_feedback"],
                feedback,
            )

            runner.run("compile_chapter_blueprint", operation_id="reuse-run")
            self.assertEqual(len(provider.requests), 1)
            runner.request_inference_regeneration([REWRITE_OUTLINE_SKILL_ID])
            runner.run("compile_chapter_blueprint", operation_id="regenerate-run")
            self.assertEqual(len(provider.requests), 2)

    def test_rewrite_merge_provider_matches_complete_outline_without_body(self) -> None:
        calls = []

        def chat(messages, *, temperature):
            del temperature
            calls.append(messages)
            content = messages[-1]["content"]
            request = json.loads(content[content.index("{"):])
            self.assertIn("legacy_outline", request)
            self.assertNotIn("legacy_sections", request)
            return json.dumps({
                "alignments": [
                    {
                        "legacy_section_id": section["section_id"],
                        "placement": "ignore",
                        "confidence": 1.0,
                    }
                    for section in request["legacy_outline"]
                ],
                "supplemental_nodes": [],
                "review_status": "draft",
            })

        target = InitialOutlineCard(
            node_id="chapter-1",
            path=["实施方案"],
            depth=1,
            title="实施方案",
            purpose="响应实施要求",
            subtree_requirement_ids=["REQ-1"],
            requirements=[{"requirement_id": "REQ-1", "text": "实施要求"}],
        )
        sections = [
            LegacySectionCard(
                section_id=f"legacy-{index}",
                path=[f"旧章节 {index}"],
                depth=1,
                order=index,
                title=f"旧章节 {index}",
                direct_content="旧正文" * 3_000,
                blocks=[LegacyBlockCard(
                    block_id=f"block-{index}",
                    content_hash=f"hash-{index}",
                    content="旧正文块" * 3_000,
                )],
                candidate_target_ids=["chapter-1"],
            )
            for index in range(15)
        ]
        request = RewriteOutlineMergeInput(
            requirement_ledger={"requirements": [{"requirement_id": "REQ-1"}]},
            score_model={"points": []},
            project_model={"scope": ["实施"]},
            initial_outline=[target],
            legacy_sections=sections,
        )
        provider = LLMRewriteOutlineMergeProvider(
            chat_callable=chat,
            model_fingerprint="test-model",
            provider_fingerprint="test-provider",
        )

        result = provider.merge(request)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.candidate.alignments), len(sections))
        structure_payload = json.loads(
            calls[0][-1]["content"][calls[0][-1]["content"].index("{"):]
        )
        self.assertEqual(len(structure_payload["legacy_outline"]), len(sections))
        serialized = json.dumps(structure_payload, ensure_ascii=False)
        self.assertNotIn("direct_content", serialized)
        self.assertNotIn("blocks", serialized)
        self.assertNotIn("candidate_target_ids", serialized)
        self.assertNotIn("旧正文块", serialized)


if __name__ == "__main__":
    unittest.main()
