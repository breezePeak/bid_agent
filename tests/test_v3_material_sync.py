from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.material_sync import (  # noqa: E402
    MaterialRequirementsSynchronizer,
)


class MaterialRequirementsSynchronizerTests(unittest.TestCase):
    def test_score_evidence_keeps_condition_and_target_binding(
        self,
    ) -> None:
        condition = SimpleNamespace(
            condition_id="SP-1-C-evidence",
            condition_role="evidence",
            normalized_condition="提供同类项目案例合同",
            text="提供同类项目案例合同",
            review_status="confirmed",
            source_anchor=None,
        )
        response_unit = SimpleNamespace(
            unit_id="RU-1",
            title="类似项目业绩",
            response_expectation="说明业绩并提交证明",
            condition_ids=[condition.condition_id],
            required_evidence_types=["案例合同"],
            review_status="confirmed",
        )
        point = SimpleNamespace(
            score_point_id="SP-1",
            disqualifying=False,
            source_anchors=[],
            review_status="confirmed",
            score_conditions=[condition],
            response_units=[response_unit],
        )
        scores = SimpleNamespace(revision=2, points=[point])
        ledger = SimpleNamespace(revision=1, requirements=[])
        blueprint = SimpleNamespace(
            nodes=[
                SimpleNamespace(
                    chapter_id="chapter-case",
                    template_target="slot-case",
                    score_condition_ids=[condition.condition_id],
                    primary_response_unit_ids=[],
                )
            ]
        )
        manifest = SimpleNamespace(inputs=[])

        with tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        ) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "materials").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "materials")
            with (
                patch(
                    "document_pipeline.material_sync."
                    "load_promoted_requirement_ledger",
                    return_value=ledger,
                ),
                patch(
                    "document_pipeline.material_sync."
                    "load_promoted_score_model",
                    return_value=scores,
                ),
                patch(
                    "document_pipeline.material_sync."
                    "load_promoted_chapter_blueprint",
                    return_value=blueprint,
                ),
                patch(
                    "document_pipeline.material_sync."
                    "InputManifestService.load",
                    return_value=manifest,
                ),
            ):
                report = MaterialRequirementsSynchronizer(
                    context
                ).sync()

        self.assertEqual(report["summary"]["total"], 1)
        item = report["items"][0]
        self.assertEqual(item["item_type"], "score_evidence")
        self.assertEqual(item["score_point_id"], "SP-1")
        self.assertEqual(item["response_unit_id"], "RU-1")
        self.assertEqual(
            item["condition_id"],
            "SP-1-C-evidence",
        )
        self.assertEqual(item["chapter_id"], "chapter-case")
        self.assertEqual(item["target_node_id"], "slot-case")
        self.assertEqual(
            item["binding_status"],
            "condition_chapter",
        )
        self.assertEqual(item["evidence_type"], "案例合同")
        self.assertIn("提供同类项目案例合同", item["requirement"])

    def test_historical_unit_only_evidence_remains_readable(
        self,
    ) -> None:
        unit = SimpleNamespace(
            unit_id="RU-legacy",
            title="历史业绩",
            response_expectation="提交证明",
            condition_ids=[],
            required_evidence_types=["证明文件"],
            review_status="confirmed",
        )
        scores = SimpleNamespace(
            points=[
                SimpleNamespace(
                    score_point_id="SP-legacy",
                    disqualifying=False,
                    source_anchors=[],
                    review_status="confirmed",
                    score_conditions=[],
                    response_units=[unit],
                )
            ]
        )
        items = MaterialRequirementsSynchronizer._score_evidence_items(
            scores=scores,
            blueprint=None,
            company_supplied=False,
        )
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["condition_id"])
        self.assertIsNone(items[0]["chapter_id"])
        self.assertIsNone(items[0]["target_node_id"])
        self.assertEqual(
            items[0]["binding_status"],
            "legacy_unit_only",
        )
        self.assertEqual(items[0]["status"], "missing")

    def test_active_evidence_condition_requires_chapter_binding(
        self,
    ) -> None:
        condition = SimpleNamespace(
            condition_id="SP-1-C-evidence",
            condition_role="evidence",
            normalized_condition="提供案例合同",
            text="提供案例合同",
            review_status="confirmed",
            source_anchor=None,
        )
        unit = SimpleNamespace(
            unit_id="RU-1",
            title="案例",
            response_expectation="提交证明",
            condition_ids=[condition.condition_id],
            required_evidence_types=["案例合同"],
            review_status="confirmed",
        )
        scores = SimpleNamespace(
            points=[
                SimpleNamespace(
                    score_point_id="SP-1",
                    disqualifying=False,
                    source_anchors=[],
                    review_status="confirmed",
                    score_conditions=[condition],
                    response_units=[unit],
                )
            ]
        )
        blueprint = SimpleNamespace(
            nodes=[
                SimpleNamespace(
                    chapter_id="chapter-other",
                    template_target=None,
                    score_condition_ids=[],
                    primary_response_unit_ids=["RU-1"],
                )
            ]
        )
        with self.assertRaisesRegex(
            ValueError,
            "EVIDENCE_TARGET_MISSING",
        ):
            MaterialRequirementsSynchronizer._score_evidence_items(
                scores=scores,
                blueprint=blueprint,
                company_supplied=False,
            )


if __name__ == "__main__":
    unittest.main()
