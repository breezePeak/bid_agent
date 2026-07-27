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
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    SourceAnchor,
    SourceBlock,
)
from document_pipeline.input_manifest import MANIFEST_PATH, V3_ROOT
from document_pipeline.score_agent import ScoreAgent
from document_pipeline.score_model import audit_score_model
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
        runner = V3StageRunner(self.context)
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


if __name__ == "__main__":
    unittest.main()
