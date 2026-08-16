from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext
from document_pipeline.contracts import (
    InputItem,
    InputManifest,
    InputRole,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    ScoreCondition,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    ScoreResponseUnit,
    ScoringLevel,
    SourceAnchor,
    SourceBlock,
)
from document_pipeline.input_manifest import InputManifestService, MANIFEST_PATH, V3_ROOT
from document_pipeline.score_agent import ScoreAgent
from document_pipeline.score_model import (
    audit_score_model,
    partition_score_model_audit,
)
from document_pipeline.scoring_outline_policy import (
    full_score_condition_heading,
    highest_score_conditions,
    is_contextless_heading,
    is_evaluative_sentence_heading,
)
from document_pipeline.source_normalizer import SOURCE_INDEX_PATH
from document_pipeline.stage_runner import V3StageRunner
from utils import write_json


class TestV3ScoreAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.context = WorkspaceContext(root=self.root, workspace_id="ws_score")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _anchor(chunk_id: str) -> SourceAnchor:
        return SourceAnchor(source_input_id="in-score", chunk_id=chunk_id, location="table:1:row:1:cell:1")

    def test_extracts_groups_points_links_and_evidence_candidates(self) -> None:
        anchor = self._anchor("score-1")
        ledger = RequirementLedger(
            source_hashes={"in-score": "scorehash"},
            requirements=[
                RequirementItem(
                    requirement_id="R-score-1",
                    kind=RequirementKind.SCORE,
                    source_anchor=anchor,
                    original_text="技术方案完整性，满分10分，提供实施方案和业绩证明。",
                    normalized_requirement="技术方案完整性，满分10分，提供实施方案和业绩证明。",
                    response_type="score_response",
                    evidence_policy="tender_traceable",
                )
            ],
        )
        blocks = [
            SourceBlock(
                block_id="heading-1",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="heading",
                ordinal=0,
                content="技术评分（10分）",
                source_anchor=self._anchor("heading-1"),
                content_hash="h1",
            ),
            SourceBlock(
                block_id="score-1",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="table_cell",
                ordinal=1,
                content="技术方案完整性，满分10分，提供实施方案和业绩证明。",
                source_anchor=anchor,
                content_hash="h2",
            ),
        ]

        model = ScoreAgent(self.context).build_score_model(blocks, ledger, revision=1, source_hashes=ledger.source_hashes)

        self.assertEqual(model.total_points, 10)
        self.assertEqual(len(model.groups), 1)
        self.assertEqual(len(model.points), 1)
        point = model.points[0]
        self.assertEqual(point.max_points, 10)
        self.assertEqual(point.linked_requirement_ids, ["R-score-1"])
        self.assertEqual(point.source_anchors, [anchor])
        self.assertEqual(point.response_depth, "detailed")
        self.assertEqual(point.required_evidence_types, ["project_reference", "supporting_document"])
        self.assertEqual(model.evidence_need_candidates[0].score_point_id, point.score_point_id)
        self.assertTrue(audit_score_model(model, ledger, blocks)["passed"])

    def test_score_model_rejects_unreconciled_group_and_total(self) -> None:
        anchor = self._anchor("score-1")
        point = ScorePoint(
            score_point_id="SP-1",
            group_id="SG-1",
            title="技术方案",
            criterion="技术方案满分10分",
            max_points=10,
            response_expectation="完整响应",
            source_anchors=[anchor],
            confidence=1,
        )
        with self.assertRaisesRegex(ValueError, "小计"):
            ScoreModel(
                model_id="SM-1",
                source_input_ids=["in-score"],
                total_points=10,
                groups=[ScoreGroup(group_id="SG-1", title="技术", declared_points=8)],
                points=[point],
            )
        with self.assertRaisesRegex(ValueError, "total_points"):
            ScoreModel(
                model_id="SM-1",
                source_input_ids=["in-score"],
                total_points=8,
                groups=[ScoreGroup(group_id="SG-1", title="技术", declared_points=10)],
                points=[point],
            )

    def test_point_value_only_cells_do_not_become_score_points(self) -> None:
        self.assertEqual(ScoreAgent._atomic_criteria("（50分）"), [])
        self.assertEqual(ScoreAgent._atomic_criteria("10分"), [])

    def test_repeated_scoring_factors_get_short_semantic_leaf_titles(self) -> None:
        points = [
            ScorePoint(
                score_point_id="SP-preparation",
                group_id="SG-tech",
                title="核查准备工作",
                criterion=(
                    "1.核查准备工作全面细致，能够满足后续核查工作的需要；"
                    "数据接收内容全面、具体，检查方法科学，得2分。"
                ),
                max_points=2,
                response_expectation="完整响应",
                source_anchors=[self._anchor("score-preparation")],
                confidence=1,
            ),
            ScorePoint(
                score_point_id="SP-change-check",
                group_id="SG-tech",
                title="核查准备工作",
                criterion="2.变更图斑正确性检查分析条理清楚、逻辑清晰，得4分。",
                max_points=4,
                response_expectation="完整响应",
                source_anchors=[self._anchor("score-change-check")],
                confidence=1,
            ),
        ]

        titled = ScoreAgent._disambiguate_titles(points)

        self.assertEqual(
            [point.title for point in titled],
            [
                "核查准备工作—数据接收内容",
                "核查准备工作—变更图斑正确性检查分析",
            ],
        )
        self.assertTrue(all(len(point.title) < 40 for point in titled))

    def test_highest_band_is_split_into_atomic_outline_conditions(self) -> None:
        criterion = (
            "项目任务背景描述清楚，工作必要性和可行性理由充分、逻辑清晰；"
            "工作目标明确、可行，工作内容具体、翔实，得4分；"
            "项目任务背景描述较清楚，工作必要性和可行性理由较充分，得2分。"
        )
        levels = ScoreAgent._scoring_levels(criterion)

        self.assertEqual(
            highest_score_conditions(criterion, levels, 4),
            [
                "项目任务背景描述清楚",
                "工作必要性和可行性理由充分、逻辑清晰",
                "工作目标明确、可行",
                "工作内容具体、翔实",
            ],
        )

    def test_scoring_table_factor_keeps_title_and_expands_highest_band_requirements(self) -> None:
        criterion = (
            "项目任务背景描述清楚，工作必要性和可行性理由充分、逻辑清晰；"
            "工作目标明确、可行，工作内容具体、翔实，得4分；"
            "项目任务背景描述较清楚，工作必要性和可行性理由较充分，得2分；"
            "项目任务背景描述不清楚，工作目标不明确，得0分。"
        )
        criterion_anchor = SourceAnchor(
            source_input_id="in-score",
            chunk_id="target-criterion",
            location="table:1:row:1:cell:1",
        )
        blocks = [
            SourceBlock(
                block_id="technical-heading",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="heading",
                ordinal=0,
                content="技术部分（暗标，4分）",
                source_anchor=self._anchor("technical-heading"),
                content_hash="heading",
            ),
            *[
                SourceBlock(
                    block_id=f"header-{column}",
                    input_id="in-score",
                    input_role=InputRole.SCORE,
                    block_kind="table_cell",
                    ordinal=column + 1,
                    content=content,
                    table_index=1,
                    row_index=0,
                    column_index=column,
                    source_anchor=SourceAnchor(
                        source_input_id="in-score",
                        chunk_id=f"header-{column}",
                        location=f"table:1:row:0:cell:{column}",
                    ),
                    content_hash=f"header-{column}",
                )
                for column, content in enumerate(("评分因素", "评分标准"))
            ],
            SourceBlock(
                block_id="target-factor",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="table_cell",
                ordinal=3,
                content="目标任务（4分）",
                table_index=1,
                row_index=1,
                column_index=0,
                source_anchor=SourceAnchor(
                    source_input_id="in-score",
                    chunk_id="target-factor",
                    location="table:1:row:1:cell:0",
                ),
                content_hash="factor",
            ),
            SourceBlock(
                block_id="target-criterion",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="table_cell",
                ordinal=4,
                content=criterion,
                table_index=1,
                row_index=1,
                column_index=1,
                source_anchor=criterion_anchor,
                content_hash="criterion",
            ),
        ]
        ledger = RequirementLedger(
            source_hashes={"in-score": "scorehash"},
            requirements=[
                RequirementItem(
                    requirement_id="R-target",
                    kind=RequirementKind.SCORE,
                    source_anchor=criterion_anchor,
                    original_text=criterion,
                    normalized_requirement=criterion,
                    response_type="score_response",
                    evidence_policy="tender_traceable",
                )
            ],
        )

        model = ScoreAgent(self.context).build_score_model(
            blocks,
            ledger,
            revision=1,
            source_hashes=ledger.source_hashes,
        )

        self.assertEqual(len(model.points), 1)
        point = model.points[0]
        self.assertEqual(point.title, "目标任务")
        self.assertEqual(point.outline_path, ["目标任务（4分）"])
        self.assertEqual(
            point.full_score_conditions,
            [
                "项目任务背景描述清楚",
                "工作必要性和可行性理由充分、逻辑清晰",
                "工作目标明确、可行",
                "工作内容具体、翔实",
            ],
        )

    def test_compound_full_score_band_splits_all_independent_response_subjects(self) -> None:
        criterion = (
            "变更图斑正确性检查分析条理清楚、逻辑清晰、重点突出；"
            "核查样本影像分类方法合理，使用说明细致，"
            "对容易混淆的类型有具体实例、可操作性强，得4分。"
        )
        levels = ScoreAgent._scoring_levels(criterion)
        conditions = highest_score_conditions(criterion, levels, 4)

        self.assertEqual(
            conditions,
            [
                "变更图斑正确性检查分析条理清楚、逻辑清晰、重点突出",
                "核查样本影像分类方法合理",
                "使用说明细致",
                "对容易混淆的类型有具体实例、可操作性强",
            ],
        )
        self.assertEqual(
            [
                full_score_condition_heading(condition, index)
                for index, condition in enumerate(conditions, start=1)
            ],
            [
                "变更图斑正确性检查分析",
                "核查样本影像分类方法",
                "使用说明",
                "易混淆类型判别实例与操作指引",
            ],
        )

    def test_score_style_headings_are_rejected_without_damaging_topic_nouns(self) -> None:
        self.assertTrue(is_evaluative_sentence_heading("检查方法科学、重点突出、方法可行"))
        self.assertTrue(is_evaluative_sentence_heading("核查样本影像分类方法合理"))
        self.assertTrue(is_evaluative_sentence_heading("对容易混淆的类型有具体实例、可操作性强"))
        self.assertTrue(is_contextless_heading("使用说明"))
        self.assertFalse(is_evaluative_sentence_heading("工作必要性和可行性"))
        self.assertFalse(is_evaluative_sentence_heading("方案可行性分析"))

    def test_score_model_is_promoted_and_idempotent(self) -> None:
        from document_pipeline.contracts import SourceAnchor, SourceBlock, SourceIndex
        from document_pipeline.source_artifacts import promote_source_artifact

        (self.root / V3_ROOT).mkdir(parents=True, exist_ok=True)
        manifest = InputManifest(
            inputs=[
                InputItem(
                    input_id="in-tender",
                    role=InputRole.TENDER,
                    filename="tender.md",
                    mime_type="text/markdown",
                    sha256="tenderhash",
                    version=1,
                ),
                InputItem(
                    input_id="in-score",
                    role=InputRole.SCORE,
                    filename="score.md",
                    mime_type="text/markdown",
                    sha256="scorehash",
                    version=1,
                ),
            ]
        )
        promote_source_artifact(
            self.context,
            artifact_kind="InputManifest",
            payload=manifest.model_dump(mode="json"),
            operation_id="fixture-manifest-score",
            gate_id="G0_INPUT_MANIFEST_INTEGRITY",
        )
        tender_anchor = SourceAnchor(source_input_id="in-tender", chunk_id="tender-1", location="paragraph:1")
        score_anchor = SourceAnchor(source_input_id="in-score", chunk_id="score-1", location="paragraph:1")
        source_index = SourceIndex(
            revision=1,
            source_hashes={"in-tender": "tenderhash", "in-score": "scorehash"},
            input_manifest_revision=1,
            blocks=[
                SourceBlock(block_id="tender-1", input_id="in-tender", input_role=InputRole.TENDER, block_kind="paragraph", ordinal=0, content="投标人须提供技术方案。", source_anchor=tender_anchor, content_hash="t1"),
                SourceBlock(block_id="score-heading", input_id="in-score", input_role=InputRole.SCORE, block_kind="heading", ordinal=0, content="技术评分（10分）", source_anchor=SourceAnchor(source_input_id="in-score", chunk_id="score-heading", location="paragraph:1"), content_hash="s1"),
                SourceBlock(block_id="score-1", input_id="in-score", input_role=InputRole.SCORE, block_kind="paragraph", ordinal=1, content="技术方案完整性，满分10分，提供业绩证明。", source_anchor=score_anchor, content_hash="s2"),
            ],
        )
        promote_source_artifact(
            self.context,
            artifact_kind="SourceIndex",
            payload=source_index.model_dump(mode="json"),
            operation_id="fixture-source-score",
            gate_id="G0_SOURCE_STRUCTURE",
            cited_source_ids=["in-tender", "in-score"],
        )
        runner = V3StageRunner.for_deterministic_tests(self.context)
        runner.run("build_requirement_ledger")
        model = runner.run("analyze_scores")

        self.assertEqual(model.revision, 1)
        self.assertEqual(model.total_points, 10)
        self.assertTrue(all(point.source_anchors and point.linked_requirement_ids for point in model.points))
        active = ControlStore(self.context).v3_active_artifact("ScoreModel")
        self.assertIsNotNone(active)
        self.assertEqual(active["revision"], 1)
        self.assertEqual(ControlStore(self.context).v3_proposal(active["proposal_id"])["producer_role"], "score_agent")
        self.assertFalse((self.root / V3_ROOT / "score_model.json").exists())
        self.assertEqual(runner.run("analyze_scores").revision, 1)
        self.assertEqual(ControlStore(self.context).v3_active_artifact("ScoreModel")["revision"], 1)

    def test_parses_level_ranges_without_splitting_into_extra_points(self) -> None:
        anchor = self._anchor("score-2")
        blocks = [
            SourceBlock(
                block_id="heading-1",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="heading",
                ordinal=0,
                content="技术方案（45分）",
                source_anchor=self._anchor("heading-1"),
                content_hash="h1",
            ),
            SourceBlock(
                block_id="score-2",
                input_id="in-score",
                input_role=InputRole.SCORE,
                block_kind="paragraph",
                ordinal=1,
                content="方案完整性与针对性，满分45分。优秀40-45分；良好32-39分；一般20-31分。",
                source_anchor=anchor,
                content_hash="h2",
            ),
        ]
        level_texts = [
            "方案完整性与针对性，满分45分",
            "优秀40-45分",
            "良好32-39分",
            "一般20-31分",
        ]
        ledger = RequirementLedger(
            source_hashes={"in-score": "scorehash"},
            requirements=[
                RequirementItem(
                    requirement_id=f"R-level-{index}",
                    kind=RequirementKind.SCORE,
                    source_anchor=anchor,
                    original_text=text,
                    normalized_requirement=text,
                    response_type="score_response",
                    evidence_policy="tender_traceable",
                )
                for index, text in enumerate(level_texts, start=1)
            ],
        )
        model = ScoreAgent(self.context).build_score_model(blocks, ledger, revision=1, source_hashes=ledger.source_hashes)
        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.points[0].max_points, 45)
        self.assertGreaterEqual(len(model.points[0].scoring_levels), 2)
        self.assertEqual(model.points[0].linked_requirement_ids, ["R-level-1"])
        self.assertTrue(audit_score_model(model, ledger, blocks)["passed"])

    def test_tender_embedded_scoring_section_is_a_legal_score_source(self) -> None:
        anchor = SourceAnchor(
            source_input_id="in-tender",
            chunk_id="embedded-score",
            location="paragraph:3",
        )
        ledger = RequirementLedger(
            source_hashes={"in-tender": "tenderhash"},
            requirements=[
                RequirementItem(
                    requirement_id="R-embedded-score",
                    kind=RequirementKind.SCORE,
                    source_anchor=anchor,
                    original_text="项目实施方案完整性，满分10分。",
                    normalized_requirement="项目实施方案完整性，满分10分。",
                    response_type="score_response",
                    evidence_policy="tender_traceable",
                )
            ],
        )
        blocks = [
            SourceBlock(
                block_id="score-heading",
                input_id="in-tender",
                input_role=InputRole.TENDER,
                block_kind="heading",
                ordinal=1,
                content="技术评分",
                heading_path=["评标办法", "技术评分"],
                source_anchor=SourceAnchor(
                    source_input_id="in-tender",
                    chunk_id="score-heading",
                    location="paragraph:2",
                ),
                content_hash="heading",
            ),
            SourceBlock(
                block_id="embedded-score",
                input_id="in-tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=2,
                content="项目实施方案完整性，满分10分。",
                heading_path=["评标办法", "技术评分"],
                source_anchor=anchor,
                content_hash="criterion",
            ),
        ]

        model = ScoreAgent(self.context).build_score_model(
            blocks,
            ledger,
            revision=1,
            source_hashes=ledger.source_hashes,
        )

        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.points[0].linked_requirement_ids, ["R-embedded-score"])
        self.assertTrue(audit_score_model(model, ledger, blocks)["passed"])

    def test_scoring_tables_create_one_point_per_data_row_and_preserve_declared_groups(self) -> None:
        blocks: list[SourceBlock] = []
        requirements: list[RequirementItem] = []
        ordinal = 0
        group_specs = [
            ("价格部分（10分）", 5, [10]),
            ("商务部分（明标，25分）", 6, [18, 4, 3]),
            (
                "技术部分（暗标，65分）",
                7,
                [4, 6, 2, 4, 4, 8, 8, 6, 5, 2, 4, 2, 2, 4, 2, 2],
            ),
        ]
        for group_title, table_index, row_points in group_specs:
            group_id = f"group-{table_index}"
            blocks.append(
                SourceBlock(
                    block_id=group_id,
                    input_id="in-tender",
                    input_role=InputRole.TENDER,
                    block_kind="paragraph",
                    ordinal=ordinal,
                    content=group_title,
                    heading_path=["第三章 评标方法和标准"],
                    source_anchor=SourceAnchor(
                        source_input_id="in-tender",
                        chunk_id=group_id,
                        location=f"paragraph:{ordinal}",
                    ),
                    content_hash=f"hash-{group_id}",
                )
            )
            ordinal += 1
            for column, content in enumerate(("评分因素", "评分标准")):
                block_id = f"table-{table_index}-header-{column}"
                blocks.append(
                    SourceBlock(
                        block_id=block_id,
                        input_id="in-tender",
                        input_role=InputRole.TENDER,
                        block_kind="table_cell",
                        ordinal=ordinal,
                        content=content,
                        heading_path=["第三章 评标方法和标准"],
                        table_index=table_index,
                        row_index=0,
                        column_index=column,
                        source_anchor=SourceAnchor(
                            source_input_id="in-tender",
                            chunk_id=block_id,
                            location=f"table:{table_index}:row:0:cell:{column}",
                        ),
                        content_hash=f"hash-{block_id}",
                    )
                )
                ordinal += 1
            for row_index, max_points in enumerate(row_points, 1):
                factor_id = f"table-{table_index}-row-{row_index}-factor"
                criterion_id = f"table-{table_index}-row-{row_index}-criterion"
                criterion = f"评分项{table_index}-{row_index}完整且可行，得{max_points}分；未响应得0分。"
                factor = f"评分项{table_index}-{row_index}（{max_points}分）"
                for column, block_id, content in (
                    (0, factor_id, factor),
                    (1, criterion_id, criterion),
                    # Simulate a DOCX gridSpan duplicate: it must not create another point.
                    (2, f"{criterion_id}-gridspan", criterion),
                ):
                    anchor = SourceAnchor(
                        source_input_id="in-tender",
                        chunk_id=block_id,
                        location=f"table:{table_index}:row:{row_index}:cell:{column}",
                    )
                    blocks.append(
                        SourceBlock(
                            block_id=block_id,
                            input_id="in-tender",
                            input_role=InputRole.TENDER,
                            block_kind="table_cell",
                            ordinal=ordinal,
                            content=content,
                            heading_path=["第三章 评标方法和标准"],
                            table_index=table_index,
                            row_index=row_index,
                            column_index=column,
                            source_anchor=anchor,
                            content_hash=f"hash-{block_id}",
                        )
                    )
                    ordinal += 1
                    if column == 1:
                        requirements.append(
                            RequirementItem(
                                requirement_id=f"R-{criterion_id}",
                                kind=RequirementKind.SCORE,
                                source_anchor=anchor,
                                original_text=criterion,
                                normalized_requirement=criterion,
                                response_type="score_response",
                                evidence_policy="tender_traceable",
                            )
                        )

        ledger = RequirementLedger(
            source_hashes={"in-tender": "tenderhash"},
            requirements=requirements,
        )
        model = ScoreAgent(self.context).build_score_model(
            blocks,
            ledger,
            revision=1,
            source_hashes=ledger.source_hashes,
        )

        self.assertEqual(len(model.points), 20)
        self.assertEqual(model.total_points, 100)
        self.assertEqual(
            [(group.declared_points, sum(point.max_points or 0 for point in model.points if point.group_id == group.group_id)) for group in model.groups],
            [(10, 10), (25, 25), (65, 65)],
        )
        self.assertTrue(all(len(point.linked_requirement_ids) == 1 for point in model.points))
        self.assertTrue(all(len(point.scoring_levels) == 2 for point in model.points))
        self.assertTrue(audit_score_model(model, ledger, blocks)["passed"])

    def test_score_file_golden_runs_through_promotion_without_bulk_binding(self) -> None:
        source_dir = (
            ROOT
            / "tests"
            / "fixtures"
            / "v3_golden"
            / "samples"
            / "G-A-SCORE-FILE-001"
            / "source"
        )
        inputs = InputManifestService(self.context)
        inputs.register_local_file(source_dir / "tender.md", InputRole.TENDER)
        inputs.register_local_file(source_dir / "score.md", InputRole.SCORE)
        runner = V3StageRunner.for_deterministic_tests(self.context)
        runner.run("normalize_sources")
        runner.run("build_requirement_ledger")

        model = runner.run("analyze_scores")

        self.assertEqual(len(model.points), 3)
        self.assertTrue(all(len(point.linked_requirement_ids) == 1 for point in model.points))
        active = ControlStore(self.context).v3_active_artifact("ScoreModel")
        self.assertIsNotNone(active)

    def test_embedded_score_section_in_tender_role_extracts_score_points(self) -> None:
        embedded_scoring_section = (
            ROOT
            / "tests"
            / "fixtures"
            / "v3_golden"
            / "samples"
            / "G-A-COMPLEX-SCORE-001"
            / "source"
            / "score.md"
        )
        InputManifestService(self.context).register_local_file(
            embedded_scoring_section,
            InputRole.TENDER,
        )
        runner = V3StageRunner.for_deterministic_tests(self.context)
        runner.run("normalize_sources")
        runner.run("build_requirement_ledger")

        model = runner.run("analyze_scores")

        self.assertEqual(model.total_points, 100)
        self.assertEqual(
            [point.title for point in model.points],
            ["平台方案", "接入能力", "数据安全", "质保服务"],
        )
        self.assertTrue(all(len(point.linked_requirement_ids) == 1 for point in model.points))

    def test_score_audit_blocks_missing_requirement_and_bulk_binding(self) -> None:
        anchor = self._anchor("score-1")
        ledger = RequirementLedger(
            requirements=[
                RequirementItem(requirement_id="R-1", kind=RequirementKind.SCORE, source_anchor=anchor, original_text="评分项一", normalized_requirement="评分项一", response_type="score_response", evidence_policy="tender_traceable"),
                RequirementItem(requirement_id="R-2", kind=RequirementKind.SCORE, source_anchor=anchor, original_text="评分项二", normalized_requirement="评分项二", response_type="score_response", evidence_policy="tender_traceable"),
            ]
        )
        point = ScorePoint(score_point_id="SP-1", group_id="SG-1", title="评分项", criterion="评分项", response_expectation="响应", linked_requirement_ids=["R-1", "R-2"], source_anchors=[anchor], confidence=1)
        model = ScoreModel(model_id="SM-1", source_input_ids=["in-score"], total_points=0, groups=[ScoreGroup(group_id="SG-1", title="评分")], points=[point])
        block = SourceBlock(block_id="score-1", input_id="in-score", input_role=InputRole.SCORE, block_kind="paragraph", ordinal=0, content="评分项", source_anchor=anchor, content_hash="h")
        audit = audit_score_model(model, ledger, [block])
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["bulk_linked_score_point_ids"], ["SP-1"])

    def test_g1_blocks_lossy_highest_band_condition_decomposition(self) -> None:
        anchor = self._anchor("score-lossy")
        source_text = (
            "背景描述清楚，工作目标明确，工作内容具体，得4分；"
            "背景描述一般，工作目标较明确，得2分。"
        )
        ledger = RequirementLedger(
            requirements=[
                RequirementItem(
                    requirement_id="R-lossy",
                    kind=RequirementKind.SCORE,
                    source_anchor=anchor,
                    original_text=source_text,
                    normalized_requirement=source_text,
                    response_type="score_response",
                    evidence_policy="tender_traceable",
                )
            ]
        )
        background_excerpt = "背景描述清楚"
        target_excerpt = "工作目标明确"
        background_start = source_text.index(background_excerpt)
        target_start = source_text.index(target_excerpt)
        conditions = [
            ScoreCondition(
                condition_id="SP-lossy-C-aaaaaaaaaaaa",
                text=background_excerpt,
                source_excerpt=background_excerpt,
                source_level_id="SP-lossy-L01",
                subject="项目背景",
                response_intent="清楚描述项目背景",
                source_anchor=anchor,
                source_span_start=background_start,
                source_span_end=background_start + len(background_excerpt),
            ),
            ScoreCondition(
                condition_id="SP-lossy-C-bbbbbbbbbbbb",
                text=target_excerpt,
                source_excerpt=target_excerpt,
                source_level_id="SP-lossy-L01",
                subject="工作目标",
                response_intent="明确说明工作目标",
                source_anchor=anchor,
                source_span_start=target_start,
                source_span_end=target_start + len(target_excerpt),
            ),
        ]
        point = ScorePoint(
            score_point_id="SP-lossy",
            group_id="SG-1",
            title="目标任务",
            criterion=source_text,
            max_points=4,
            scoring_levels=[
                ScoringLevel(
                    label="4分档",
                    points=4,
                    criterion="背景描述清楚，工作目标明确，工作内容具体，得4分",
                ),
                ScoringLevel(
                    label="2分档",
                    points=2,
                    criterion="背景描述一般，工作目标较明确，得2分",
                ),
            ],
            score_conditions=conditions,
            response_units=[
                ScoreResponseUnit(
                    unit_id="SP-lossy-U01",
                    title="目标任务",
                    source_level_ids=["SP-lossy-L01", "SP-lossy-L02"],
                    condition_ids=[
                        condition.condition_id for condition in conditions
                    ],
                    response_expectation="完整响应目标任务",
                )
            ],
            response_expectation="完整响应目标任务",
            linked_requirement_ids=["R-lossy"],
            source_anchors=[anchor],
            confidence=1,
        )
        model = ScoreModel(
            model_id="SM-lossy",
            source_input_ids=["in-score"],
            total_points=4,
            groups=[ScoreGroup(group_id="SG-1", title="技术评分")],
            points=[point],
        )
        block = SourceBlock(
            block_id="score-lossy",
            input_id="in-score",
            input_role=InputRole.SCORE,
            block_kind="paragraph",
            ordinal=0,
            content=source_text,
            source_anchor=anchor,
            content_hash="lossy-hash",
        )

        audit = audit_score_model(
            model,
            ledger,
            [block],
            require_semantic=True,
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(
            audit["incomplete_condition_coverage_score_point_ids"],
            ["SP-lossy"],
        )
        self.assertEqual(
            audit[
                "incomplete_response_unit_requirement_score_point_ids"
            ],
            [],
        )
        self.assertEqual(audit["invalid_condition_source_ids"], [])
        blocking, review_only = partition_score_model_audit(audit)
        self.assertEqual(blocking, {})
        self.assertEqual(
            review_only["incomplete_condition_coverage_score_point_ids"],
            ["SP-lossy"],
        )
        procurement_ledger = ledger.model_copy(
            update={
                "requirements": [
                    ledger.requirements[0].model_copy(
                        update={"kind": RequirementKind.MANDATORY}
                    )
                ]
            }
        )
        procurement_audit = audit_score_model(
            model,
            procurement_ledger,
            [block],
            require_semantic=True,
        )
        self.assertEqual(
            procurement_audit[
                "incomplete_response_unit_requirement_score_point_ids"
            ],
            ["SP-lossy"],
        )

        changed_source = block.model_copy(
            update={
                "content": source_text.replace(
                    background_excerpt,
                    "背景说明清楚",
                    1,
                )
            }
        )
        changed_source_audit = audit_score_model(
            model,
            ledger,
            [changed_source],
            require_semantic=True,
        )
        self.assertIn(
            "SP-lossy-C-aaaaaaaaaaaa",
            changed_source_audit["invalid_condition_source_ids"],
        )
        blocking, _ = partition_score_model_audit(changed_source_audit)
        self.assertIn("invalid_condition_source_ids", blocking)

    def test_personnel_awards_are_partitioned_identically_across_layouts(self) -> None:
        criterion = (
            "主要人员满足要求，得4分。\n"
            "技术负责人（1分）\n"
            "负责人工作3年以上且有3个项目经验，得1分；\n"
            "负责人工作1年以上且有1个项目经验，得0.5分；\n"
            "不满足，得0分。\n"
            "2. 驻场人员（3分）\n"
            "（1）包5-包6：\n"
            "高级人员每有一人满足要求得0.6分；本项最高3分。\n"
            "中级人员每有一人满足要求得每人0.3分；本项最高2分。\n"
            "其他人员每有一人满足要求得0.1分；本项最高1分。\n"
            "累计计分，本项最高3分。\n"
            "（2）包7-包9：\n"
            "高级人员每有一人满足要求得0.75分；本项最高3分。\n"
            "中级人员每有一人满足要求得0.3分；本项最高2分。\n"
            "其他人员每有一人满足要求得0.1分；本项最高1分。\n"
            "累计计分，本项最高3分。\n"
            "说明：\n"
            "1.须提供项目组组成表。\n"
            "2.须提供社保证明或劳动合同。"
        )
        expected_points = [
            4.0,
            1.0,
            0.5,
            0.0,
            0.6,
            0.3,
            0.1,
            0.75,
            0.3,
            0.1,
        ]

        for layout in (criterion, criterion.replace("\n", "；")):
            with self.subTest(layout="newline" if "\n" in layout else "inline"):
                levels = ScoreAgent._scoring_levels(layout)
                self.assertEqual(
                    [level.points for level in levels],
                    expected_points,
                )
                self.assertTrue(
                    all(
                        len(ScoreAgent._score_award_events(level.criterion))
                        == 1
                        for level in levels
                    )
                )
                self.assertIn("包5-包6", levels[4].criterion)
                self.assertIn("包7-包9", levels[7].criterion)
                self.assertNotIn("累计计分", levels[7].criterion)

    def test_primary_award_tokenizer_supports_per_item_word_orders(self) -> None:
        for wording in (
            "满足要求得0.3分",
            "满足要求得每人0.3分",
            "满足要求每人得0.3分",
            "每份计0.3分",
        ):
            with self.subTest(wording=wording):
                events = ScoreAgent._score_award_events(wording)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][1], 0.3)
        self.assertEqual(
            ScoreAgent._score_award_events("本项最高得3分"),
            [],
        )

    def test_common_requirements_keep_substance_and_drop_only_score_mechanics(self) -> None:
        cases = {
            "类似业绩": (
                "每个项目得0.1分；本项最高18分；关于合格业绩的规定："
                "项目时间须在2023年至2026年；须同时提供①合同或任务书+②验收证明。",
                "项目时间须在2023年至2026年；须同时提供①合同或任务书+②验收证明",
            ),
            "组织成员": (
                "每人得0.1分；本项最高1分；累计计分，本项最高3分；"
                "说明：1.须提供组成表；2.须提供社保证明或劳动合同。",
                "1.须提供组成表；2.须提供社保证明或劳动合同",
            ),
            "测绘资质": (
                "甲级得3分；乙级得1分；没有不得分，"
                "本项总得分不超过3分；注：提供有效期内证书复印件加盖公章。",
                "提供有效期内证书复印件加盖公章",
            ),
        }

        for title, (criterion, expected) in cases.items():
            with self.subTest(title=title):
                self.assertEqual(
                    ScoreAgent._common_score_requirements(criterion),
                    expected,
                )
        qualification_levels = ScoreAgent._scoring_levels(
            cases["测绘资质"][0]
        )
        self.assertEqual(
            [level.points for level in qualification_levels],
            [3.0, 1.0, 0.0],
        )

    def test_scored_price_with_veto_note_is_not_globally_disqualifying(self) -> None:
        price = (
            "有效最低投标报价为满分，其他报价按公式计算。"
            "注：不能证明报价合理性的，作为无效投标被拒绝。"
        )
        self.assertFalse(
            ScoreAgent._is_wholly_disqualifying_criterion(
                price,
                max_points=10.0,
            )
        )
        self.assertEqual(
            ScoreAgent._common_score_requirements(price),
            "不能证明报价合理性的，作为无效投标被拒绝",
        )
        self.assertTrue(
            ScoreAgent._is_wholly_disqualifying_criterion(
                "资格性审查不合格的投标将被否决",
                max_points=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
