from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext
from document_pipeline.contracts import ResponseTopic, ResponseTopicGraph
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.stage_runner import V3StageRunner
from document_pipeline.contracts import InputRole
from document_pipeline.source_normalizer import SourceNormalizer
from document_pipeline.topic_graph import load_promoted_topic_graph


class V3PlanningAgentTests(unittest.TestCase):
    def test_promotes_controlled_project_projection_and_topic_duties(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            tender = base / "tender.md"
            score = base / "score.md"
            tender.write_text("系统须满足数据安全要求；供应商须具备相关资质证书；交付实施报告；通过采购人验收。", encoding="utf-8")
            score.write_text("技术评分（10分）\n技术方案完整性，满分10分，提供业绩证明。", encoding="utf-8")
            inputs = InputManifestService(context)
            inputs.register_local_file(tender, InputRole.TENDER)
            inputs.register_local_file(score, InputRole.SCORE)
            SourceNormalizer(context).normalize_active_inputs()
            runner = V3StageRunner(context)
            ledger = runner.run("analyze_requirements")
            scores = runner.run("analyze_scores")
            project = runner.run("plan_response")
            graph = load_promoted_topic_graph(context)
            blueprint = runner.run("compile_chapter_blueprint")

            self.assertEqual(project.requirement_ids, [item.requirement_id for item in ledger.requirements])
            self.assertEqual(project.score_point_ids, [point.score_point_id for point in scores.points])
            self.assertTrue(all(topic.source_anchors or topic.attributes.get("upstream_refs") for topic in graph.topics if topic.review_status == "confirmed"))
            duty_requirements = {item for duty in graph.duties for item in duty.requirement_ids}
            self.assertTrue({item.requirement_id for item in ledger.requirements if item.severity == "blocking"} <= duty_requirements)
            duty_scores = {item for duty in graph.duties for item in duty.score_point_ids}
            self.assertEqual(duty_scores, {item.score_point_id for item in scores.points})
            self.assertEqual(ControlStore(context).v3_active_artifact("ProjectModel")["revision"], 1)
            self.assertEqual(ControlStore(context).v3_active_artifact("ResponseTopicGraph")["revision"], 1)
            self.assertEqual(ControlStore(context).v3_active_artifact("ChapterBlueprint")["revision"], 1)
            self.assertEqual({item.duty_id for item in blueprint.assignments if item.role == "primary"}, {item.duty_id for item in graph.duties})
            self.assertEqual(runner.run("plan_response").revision, 1)
            self.assertEqual(load_promoted_topic_graph(context).revision, 1)

    def test_rejects_cyclic_execution_dependencies(self) -> None:
        topic_a = ResponseTopic(topic_id="T-A", topic_type="function", canonical_name="A", intent="响应", summary="A", attributes={"upstream_refs": ["RequirementLedger:R-A"]}, confidence=1)
        topic_b = ResponseTopic(topic_id="T-B", topic_type="function", canonical_name="B", intent="响应", summary="B", attributes={"upstream_refs": ["RequirementLedger:R-B"]}, confidence=1)
        with self.assertRaisesRegex(ValueError, "依赖存在环"):
            ResponseTopicGraph(
                graph_id="TG-1", requirement_ledger_revision=1, score_model_revision=1, project_model_revision=1,
                root_topic_ids=["T-A", "T-B"], topics=[topic_a, topic_b],
                edges=[
                    {"edge_id": "E-1", "source_topic_id": "T-A", "target_topic_id": "T-B", "relation": "depends_on", "order": 0, "rationale": "A依赖B", "confidence": 1},
                    {"edge_id": "E-2", "source_topic_id": "T-B", "target_topic_id": "T-A", "relation": "depends_on", "order": 1, "rationale": "B依赖A", "confidence": 1},
                ],
            )


if __name__ == "__main__":
    unittest.main()
