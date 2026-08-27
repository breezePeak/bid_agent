from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.chapter_writing_service import (  # noqa: E402
    ChapterWritingRequest,
    ChapterWritingService,
)
from document_pipeline.contracts import ChapterBlueprint, InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService, V3_ROOT  # noqa: E402
from document_pipeline.legacy_bid_source import LegacyBidSourceService  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.writer_bundle import WriterInputBundleAssembler  # noqa: E402


def _prepare_workspace(base: Path, *, rewrite: bool = False):
    runs = base / "runs"
    (runs / "alpha").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "alpha")
    store = ControlStore(context)
    if rewrite:
        store.initialize_workspace_profile("bid_rewrite")

    tender = base / "tender.md"
    tender.write_text(
        "# 项目概况\n项目名称：测试项目。\n\n"
        "# 项目需求\n投标人须提供实施方案，并在30日内完成交付和验收。\n\n"
        "# 评标办法\n实施方案完整、措施可行，满分10分。",
        encoding="utf-8",
    )
    InputManifestService(context).register_local_file(tender, InputRole.TENDER)

    legacy_index = None
    if rewrite:
        old_bid = base / "old-bid.md"
        old_bid.write_text(
            "# 原实施方案\n采用分阶段实施，完成部署、联调和验收。",
            encoding="utf-8",
        )
        source = LegacyBidSourceService(context).register_local_file(
            old_bid, old_bid.name
        )
        legacy_index = LegacyBidSourceService(context).index(source.legacy_bid_id)

    runner = V3StageRunner.for_deterministic_tests(context)
    for stage in (
        "normalize_sources",
        "build_requirement_ledger",
        "analyze_scores",
        "plan_response",
        "compile_chapter_blueprint",
    ):
        runner.run(stage)
    store.grant_workspace_access("owner")
    gate = HumanGateService(context)
    gate.confirm_planning(
        principal_id="owner",
        submitted_snapshot=gate.planning_snapshot(),
        nonce="writer-bundle-blueprint-authority",
    )
    blueprint = ChapterBlueprint.model_validate(
        store.v3_active_artifact("ChapterBlueprint")["payload"]
    )
    return context, blueprint, legacy_index


def _leaf_nodes(blueprint: ChapterBlueprint):
    parent_ids = {
        node.parent_chapter_id
        for node in blueprint.nodes
        if node.parent_chapter_id is not None
    }
    return [
        node
        for node in blueprint.nodes
        if node.chapter_id not in parent_ids and node.content_policy == "full"
    ]


def _rewrite_blueprint(
    blueprint: ChapterBlueprint,
    *,
    chapter_id: str,
    mode: str,
    section_id: str,
    block_id: str,
    content_hash: str,
) -> ChapterBlueprint:
    parent_ids = {
        node.parent_chapter_id
        for node in blueprint.nodes
        if node.parent_chapter_id is not None
    }
    payload = blueprint.model_dump(mode="json")
    payload["planning_model"] = "rewrite_merge"
    for node in payload["nodes"]:
        is_leaf = node["chapter_id"] not in parent_ids
        node["rewrite_mode"] = "new_write" if is_leaf else None
        node["legacy_section_ids"] = []
        node["legacy_sources"] = []
        node["required_changes"] = []
        if node["chapter_id"] != chapter_id:
            continue
        node["rewrite_mode"] = mode
        node["legacy_section_ids"] = [section_id]
        node["legacy_sources"] = [
            {
                "section_id": section_id,
                "block_id": block_id,
                "content_hash": content_hash,
            }
        ]
        if mode in {"light_edit", "restructure"}:
            node["required_changes"] = ["按新招标要求更新实施表述"]
    return ChapterBlueprint.model_validate(payload)


class WriterBundleBlueprintAuthorityTests(unittest.TestCase):
    def test_score_direct_without_document_contract_writes_draft_revision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, blueprint, _ = _prepare_workspace(Path(tmp))
            chapter = _leaf_nodes(blueprint)[0]
            contract_path = context.root / V3_ROOT / "contracts" / "document_contract.json"
            self.assertFalse(contract_path.exists())

            bundle = WriterInputBundleAssembler(
                context, deterministic_test=True
            ).assemble("full-write-unit", [chapter.chapter_id])
            target = bundle.document_target_constraints[0]
            self.assertEqual(bundle.effective_generation_mode, "new_write")
            self.assertEqual(target["node_id"], chapter.chapter_id)
            self.assertEqual(target["target"], chapter.chapter_id)
            self.assertEqual(target["output_target"], chapter.chapter_id)

            ChapterWorkspaceService(context).ensure_all(
                actor={"type": "test", "id": "owner"}
            )
            store = ControlStore(context)
            workspace = store.chapter_workspace(chapter.chapter_id)

            def commit_draft(
                chapter_id,
                expected_revision,
                text,
                actor,
                _global_ref,
                _chapter_ref,
                _evidence_batches,
                _overwrite_locked,
            ):
                return store.append_chapter_content_revision(
                    chapter_id=chapter_id,
                    expected_chapter_revision=expected_revision,
                    blocks=[
                        {
                            "block_id": f"draft-{chapter_id}",
                            "target_node_id": chapter_id,
                            "type": "paragraph",
                            "content": text,
                            "confidence": 1,
                        }
                    ],
                    source="ai_draft",
                    approval_policy={"confirmation_required": True},
                    actor=actor,
                    approval_status="draft",
                )

            result = ChapterWritingService(
                context,
                deterministic_test=True,
                draft_committer=commit_draft,
            ).write(
                ChapterWritingRequest(
                    unit_id="real-single-chapter",
                    node_ids=(chapter.chapter_id,),
                    chapter_id=chapter.chapter_id,
                    expected_chapter_revision=int(workspace["chapter_revision"]),
                    actor={"type": "user", "id": "owner"},
                    run_research=False,
                )
            )
            self.assertIn(chapter.chapter_id, result.draft_revisions)
            self.assertIsNotNone(store.chapter_content_head(chapter.chapter_id))
            self.assertFalse(contract_path.exists())

    def test_rewrite_modes_resolve_real_legacy_content_without_contract(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, blueprint, legacy_index = _prepare_workspace(
                Path(tmp), rewrite=True
            )
            chapter = _leaf_nodes(blueprint)[0]
            section = next(
                item for item in legacy_index.sections if item.content_block_ids
            )
            block = next(
                item
                for item in legacy_index.blocks
                if item.block_id in section.content_block_ids
            )
            self.assertFalse(
                (context.root / V3_ROOT / "contracts" / "document_contract.json").exists()
            )
            for mode in ("copy", "light_edit", "restructure"):
                with self.subTest(mode=mode):
                    rewritten = _rewrite_blueprint(
                        blueprint,
                        chapter_id=chapter.chapter_id,
                        mode=mode,
                        section_id=section.section_id,
                        block_id=block.block_id,
                        content_hash=block.content_hash,
                    )
                    with mock.patch(
                        "document_pipeline.writer_bundle.load_promoted_chapter_blueprint",
                        return_value=rewritten,
                    ):
                        bundle = WriterInputBundleAssembler(
                            context, deterministic_test=True
                        ).assemble(f"rewrite-{mode}", [chapter.chapter_id])
                    source = bundle.blueprint_slice[0]["legacy_sources"][0]
                    self.assertEqual(bundle.effective_generation_mode, mode)
                    self.assertEqual(source["content"], block.content)
                    self.assertEqual(
                        bundle.blueprint_slice[0]["required_changes"],
                        [] if mode == "copy" else ["按新招标要求更新实施表述"],
                    )

    def test_stale_legacy_source_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, blueprint, legacy_index = _prepare_workspace(
                Path(tmp), rewrite=True
            )
            chapter = _leaf_nodes(blueprint)[0]
            section = next(
                item for item in legacy_index.sections if item.content_block_ids
            )
            block = next(
                item
                for item in legacy_index.blocks
                if item.block_id in section.content_block_ids
            )
            rewritten = _rewrite_blueprint(
                blueprint,
                chapter_id=chapter.chapter_id,
                mode="copy",
                section_id=section.section_id,
                block_id=block.block_id,
                content_hash="stale-content-hash",
            )
            with (
                mock.patch(
                    "document_pipeline.writer_bundle.load_promoted_chapter_blueprint",
                    return_value=rewritten,
                ),
                self.assertRaises(ControlPlaneError) as raised,
            ):
                WriterInputBundleAssembler(
                    context, deterministic_test=True
                ).assemble("rewrite-stale", [chapter.chapter_id])
            self.assertEqual(raised.exception.code, "LEGACY_SOURCE_STALE")
            self.assertEqual(raised.exception.details["chapter_id"], chapter.chapter_id)
            self.assertEqual(raised.exception.details["block_id"], block.block_id)

    def test_parent_chapter_is_rejected_instead_of_empty_bundle(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, blueprint, _ = _prepare_workspace(Path(tmp))
            parent_ids = {
                node.parent_chapter_id
                for node in blueprint.nodes
                if node.parent_chapter_id is not None
            }
            if not parent_ids:
                payload = blueprint.model_dump(mode="json")
                child = payload["nodes"][0]
                child["parent_chapter_id"] = "test-parent"
                payload["nodes"].insert(
                    0,
                    {
                        **child,
                        "chapter_id": "test-parent",
                        "parent_chapter_id": None,
                        "order": 0,
                        "title": "测试父章节",
                        "purpose": "仅组织下级章节",
                        "writing_objectives": [],
                        "primary_response_unit_ids": [],
                        "supporting_response_unit_ids": [],
                        "score_point_ids": [],
                        "score_condition_ids": [],
                        "requirement_ids": [],
                    },
                )
                blueprint = ChapterBlueprint.model_validate(payload)
                parent_ids = {"test-parent"}
            parent_id = sorted(parent_ids)[0]
            with (
                mock.patch(
                    "document_pipeline.writer_bundle.load_promoted_chapter_blueprint",
                    return_value=blueprint,
                ),
                self.assertRaises(ControlPlaneError) as raised,
            ):
                WriterInputBundleAssembler(context, deterministic_test=True).assemble(
                    "parent-unit", [parent_id]
                )
            self.assertEqual(raised.exception.code, "CHAPTER_NOT_WRITABLE")


if __name__ == "__main__":
    unittest.main()
