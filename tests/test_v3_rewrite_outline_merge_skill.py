from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.planning_inference import (  # noqa: E402
    ChapterOutlineCandidate,
    ChapterOutlineNodeCandidate,
)
from document_pipeline.rewrite_outline_merge_skill import (  # noqa: E402
    ConditionText,
    InitialOutlineCard,
    LegacyBlockCard,
    LegacySectionCard,
    RewriteLegacySource,
    RewriteOutlineAlignment,
    RewriteOutlineMergeCandidate,
    RewriteOutlineMergeInput,
    apply_rewrite_outline_merge,
    validate_rewrite_outline_merge,
)


class RewriteOutlineMergeSkillTests(unittest.TestCase):
    @staticmethod
    def request() -> RewriteOutlineMergeInput:
        return RewriteOutlineMergeInput(
            requirement_ledger={},
            score_model={},
            project_model={},
            review_feedback="不要采用旧标书中的培训章节",
            initial_outline=[
                InitialOutlineCard(
                    node_id="root",
                    path=["项目实施方案"],
                    depth=1,
                    title="项目实施方案",
                    purpose="实施",
                    subtree_response_unit_ids=["U1", "U2"],
                    subtree_condition_ids=["C1", "C2"],
                ),
                InitialOutlineCard(
                    node_id="migration",
                    parent_node_id="root",
                    path=["项目实施方案", "数据迁移"],
                    depth=2,
                    title="数据迁移",
                    purpose="迁移",
                    direct_response_unit_ids=["U1"],
                    subtree_response_unit_ids=["U1"],
                    subtree_condition_ids=["C1"],
                    score_conditions=[ConditionText(condition_id="C1", text="完成数据迁移")],
                ),
                InitialOutlineCard(
                    node_id="service",
                    parent_node_id="root",
                    path=["项目实施方案", "售后服务"],
                    depth=2,
                    title="售后服务",
                    purpose="服务",
                    direct_response_unit_ids=["U2"],
                    subtree_response_unit_ids=["U2"],
                    subtree_condition_ids=["C2"],
                ),
            ],
            legacy_sections=[
                LegacySectionCard(
                    section_id="old-migration",
                    path=["数据迁移"],
                    depth=1,
                    order=0,
                    title="数据迁移",
                    direct_content="迁移方案",
                    blocks=[LegacyBlockCard(block_id="B1", content_hash="H1", content="迁移方案")],
                    candidate_target_ids=["migration", "root"],
                ),
                LegacySectionCard(
                    section_id="inventory",
                    parent_section_id="old-migration",
                    path=["数据迁移", "1. 数据盘点"],
                    depth=2,
                    order=1,
                    title="1. 数据盘点",
                    direct_content="盘点正文",
                    blocks=[LegacyBlockCard(block_id="B2", content_hash="H2", content="盘点正文")],
                    candidate_target_ids=["migration", "service"],
                ),
            ],
        )

    @staticmethod
    def candidate() -> RewriteOutlineMergeCandidate:
        return RewriteOutlineMergeCandidate(
            alignments=[
                RewriteOutlineAlignment(
                    legacy_section_id="old-migration",
                    target_node_id="migration",
                    placement="same_scope",
                    matched_response_unit_ids=["U1"],
                    rewrite_mode="light_edit",
                    legacy_sources=[RewriteLegacySource(section_id="old-migration", block_id="B1", content_hash="H1")],
                    reason="职责一致",
                    required_changes=["替换项目字段"],
                    confidence=0.95,
                ),
                RewriteOutlineAlignment(
                    legacy_section_id="inventory",
                    target_node_id="migration",
                    placement="child_detail",
                    matched_condition_ids=["C1"],
                    purpose="完成迁移前的数据盘点",
                    writing_objectives=["形成盘点清单"],
                    rewrite_mode="copy",
                    legacy_sources=[RewriteLegacySource(section_id="inventory", block_id="B2", content_hash="H2")],
                    reason="独立细分任务",
                    confidence=0.9,
                ),
            ]
        )

    def test_same_scope_and_child_detail_produce_one_stable_leaf_strategy(self) -> None:
        request = self.request()
        candidate = self.candidate()
        validate_rewrite_outline_merge(request, candidate)
        initial = ChapterOutlineCandidate(nodes=[
            ChapterOutlineNodeCandidate(
                local_id="root", order=0, title="项目实施方案", purpose="实施",
                primary_response_unit_ids=["U2"], confidence=1.0,
            ),
            ChapterOutlineNodeCandidate(
                local_id="migration", parent_local_id="root", order=1,
                title="数据迁移", purpose="迁移", primary_response_unit_ids=["U1"], confidence=1.0,
            ),
            ChapterOutlineNodeCandidate(
                local_id="service", parent_local_id="root", order=2,
                title="售后服务", purpose="服务", confidence=1.0,
            ),
        ])
        legacy = SimpleNamespace(sections=[
            SimpleNamespace(section_id="old-migration", parent_section_id=None, order=0, title="数据迁移"),
            SimpleNamespace(section_id="inventory", parent_section_id="old-migration", order=1, title="1. 数据盘点"),
        ])
        first = apply_rewrite_outline_merge(initial, candidate, legacy)
        second = apply_rewrite_outline_merge(initial, candidate, legacy)
        self.assertEqual(first.model_dump(), second.model_dump())
        migration = next(item for item in first.nodes if item.local_id == "migration")
        inventory = next(item for item in first.nodes if item.structure_origin == "legacy_enriched")
        service = next(item for item in first.nodes if item.local_id == "service")
        self.assertIsNone(migration.rewrite_mode)
        self.assertEqual(inventory.parent_local_id, "migration")
        self.assertEqual(inventory.title, "数据盘点")
        self.assertEqual(inventory.rewrite_mode, "copy")
        self.assertEqual(service.rewrite_mode, "new_write")

    def test_candidate_target_and_old_parent_branch_are_enforced(self) -> None:
        candidate = self.candidate()
        broken = candidate.model_copy(deep=True)
        broken.alignments[1].target_node_id = "service"
        broken.alignments[1].matched_condition_ids = ["C2"]
        with self.assertRaisesRegex(ValueError, "旧父章节目标之外"):
            validate_rewrite_outline_merge(self.request(), broken)

    def test_cross_branch_responsibility_id_is_rejected(self) -> None:
        candidate = self.candidate()
        broken = candidate.model_copy(deep=True)
        broken.alignments[1].matched_condition_ids = ["C2"]
        with self.assertRaisesRegex(ValueError, "目标分支外"):
            validate_rewrite_outline_merge(self.request(), broken)

    def test_multiple_same_scope_alignments_for_one_target_are_rejected(self) -> None:
        candidate = self.candidate()
        broken = candidate.model_copy(deep=True)
        broken.alignments[1].placement = "same_scope"
        with self.assertRaisesRegex(ValueError, "最多只能有一个 same_scope"):
            validate_rewrite_outline_merge(self.request(), broken)

    def test_same_scope_with_child_detail_is_valid(self) -> None:
        validate_rewrite_outline_merge(self.request(), self.candidate())

    def test_input_has_feedback_without_full_legacy_index(self) -> None:
        payload = self.request().model_dump(mode="json")
        self.assertEqual(
            payload["review_feedback"],
            "不要采用旧标书中的培训章节",
        )
        self.assertNotIn("legacy_bid_index", payload)


if __name__ == "__main__":
    unittest.main()
