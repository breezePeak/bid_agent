from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext
from document_pipeline.contracts import (
    ProjectModel,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    ResponseDuty,
    ResponseTopic,
    ResponseTopicGraph,
    ScoreCondition,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    ScoreResponseUnit,
    SourceAnchor,
)
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.stage_runner import V3StageRunner
from document_pipeline.contracts import InputRole
from document_pipeline.source_normalizer import SourceNormalizer
from document_pipeline.topic_graph import load_promoted_topic_graph
from document_pipeline.planning_agent import PlanningAgent
from document_pipeline.chapter_blueprint import audit_chapter_blueprint


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
            runner = V3StageRunner.for_deterministic_tests(context)
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
            self.assertEqual(blueprint.planning_model, "score_direct")
            self.assertEqual(blueprint.assignments, [])
            expected_section_units = {
                unit.unit_id
                for point in scores.points
                for unit in point.response_units
                if unit.response_scope == "section"
                and unit.review_status != "rejected"
            }
            actual_primary_units = {
                unit_id
                for node in blueprint.nodes
                for unit_id in node.primary_response_unit_ids
            }
            self.assertEqual(actual_primary_units, expected_section_units)
            self.assertTrue(any(item.parent_chapter_id is None for item in blueprint.nodes))
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

    def test_uses_controlled_response_sections_instead_of_tender_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            graph = ResponseTopicGraph(
                graph_id="TG-section-outline",
                requirement_ledger_revision=1,
                score_model_revision=1,
                project_model_revision=1,
                root_topic_ids=["T-1", "T-2", "T-3"],
                topics=[
                    ResponseTopic(topic_id="T-1", topic_type="function", canonical_name="要求一", intent="响应", summary="要求一", attributes={"source_section": "第五章 采购需求", "upstream_refs": ["RequirementLedger:R-1"]}, confidence=1),
                    ResponseTopic(topic_id="T-2", topic_type="function", canonical_name="要求二", intent="响应", summary="要求二", attributes={"source_section": "第五章 采购需求", "upstream_refs": ["RequirementLedger:R-2"]}, confidence=1),
                    ResponseTopic(topic_id="T-3", topic_type="qualification", canonical_name="资格", intent="响应", summary="资格", attributes={"source_section": "第六章 投标文件格式", "upstream_refs": ["RequirementLedger:R-3"]}, confidence=1),
                ],
                duties=[
                    ResponseDuty(duty_id="D-1", topic_id="T-1", duty_type="explain", requirement_ids=["R-1"], response_expectations=["mandatory_response"], confidence=1),
                    ResponseDuty(duty_id="D-2", topic_id="T-2", duty_type="explain", requirement_ids=["R-2"], response_expectations=["mandatory_response"], confidence=1),
                    ResponseDuty(duty_id="D-3", topic_id="T-3", duty_type="verify", requirement_ids=["R-3"], response_expectations=["qualification_response"], confidence=1),
                ],
            )

            blueprint = PlanningAgent(context).chapter_blueprint(graph, revision=1)

            self.assertEqual([node.title for node in blueprint.nodes], ["资格与合规响应", "技术响应"])
            self.assertEqual(len(blueprint.assignments), 3)
            assignment_by_duty = {item.duty_id: item.chapter_id for item in blueprint.assignments}
            self.assertEqual(assignment_by_duty["D-1"], assignment_by_duty["D-2"])
            self.assertNotEqual(assignment_by_duty["D-2"], assignment_by_duty["D-3"])
            self.assertFalse(
                {"第五章 采购需求", "第六章 投标文件格式"}
                & {node.title for node in blueprint.nodes}
            )

    def test_builds_score_driven_outline_for_realistic_100_point_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            topics: list[ResponseTopic] = []
            duties: list[ResponseDuty] = []

            administrative_sections = [
                ("R-1", "投标邀请", "function", "项目采购需求"),
                ("R-2", "投标人须知", "commercial", "合同与付款要求"),
                ("R-3", "评标方法", "qualification", "资格证明要求"),
                ("R-4", "合同条款", "acceptance", "验收与交付要求"),
            ]
            for requirement_id, source_section, topic_type, name in administrative_sections:
                suffix = requirement_id.removeprefix("R-")
                topics.append(
                    ResponseTopic(
                        topic_id=f"T-R-{suffix}",
                        topic_type=topic_type,
                        canonical_name=name,
                        intent="响应采购义务",
                        summary=name,
                        attributes={
                            "source_section": source_section,
                            "upstream_refs": [f"RequirementLedger:{requirement_id}"],
                        },
                        confidence=1,
                    )
                )
                duties.append(
                    ResponseDuty(
                        duty_id=f"D-R-{suffix}",
                        topic_id=f"T-R-{suffix}",
                        duty_type="verify" if topic_type == "qualification" else "explain",
                        requirement_ids=[requirement_id],
                        response_expectations=["完整响应"],
                        confidence=1,
                    )
                )

            group_specs = [
                ("G-price", "价格部分（10分）", 10, 1),
                ("G-business", "商务部分（明标，25分）", 25, 3),
                ("G-technical", "技术部分（暗标，65分）", 65, 16),
            ]
            score_index = 0
            for group_order, (group_id, group_title, declared_points, point_count) in enumerate(group_specs):
                for point_in_group in range(1, point_count + 1):
                    score_index += 1
                    point_id = f"SP-{score_index:02d}"
                    topic_id = f"T-S-{score_index:02d}"
                    title = (
                        "投标报价"
                        if group_id == "G-price"
                        else (
                            "投标文件整体评价"
                            if group_id == "G-technical" and point_in_group == point_count
                            else f"{'商务' if group_id == 'G-business' else '技术'}评分项{point_in_group}"
                        )
                    )
                    topics.append(
                        ResponseTopic(
                            topic_id=topic_id,
                            topic_type="commercial" if group_id != "G-technical" else "function",
                            canonical_name=title,
                            intent="响应评分逻辑",
                            summary=f"{title}评分标准",
                            attributes={
                                "upstream_refs": [f"ScoreModel:{point_id}"],
                                "score_group_id": group_id,
                                "score_group_title": group_title,
                                "score_group_order": group_order,
                            "score_group_declared_points": declared_points,
                            "score_point_order": score_index - 1,
                            "planning_role": (
                                "document_quality_gate"
                                if title == "投标文件整体评价"
                                else "content_section"
                            ),
                            "response_shape": "form/table" if group_id == "G-price" else "narrative",
                        },
                            confidence=1,
                        )
                    )
                    duties.append(
                        ResponseDuty(
                            duty_id=f"D-S-{score_index:02d}",
                            topic_id=topic_id,
                            duty_type="explain",
                            requirement_ids=["R-1"] if score_index == 1 else [],
                            score_point_ids=[point_id],
                            response_expectations=["逐项证明并争取得分"],
                            confidence=1,
                        )
                    )

            graph = ResponseTopicGraph(
                graph_id="TG-realistic-score-outline",
                requirement_ledger_revision=1,
                score_model_revision=1,
                project_model_revision=1,
                root_topic_ids=[topic.topic_id for topic in topics],
                topics=topics,
                duties=duties,
            )

            blueprint = PlanningAgent(context).chapter_blueprint(graph, revision=1)

            root_titles = [node.title for node in blueprint.nodes if node.parent_chapter_id is None]
            self.assertEqual(root_titles[:2], ["报价响应（10分）", "商务评分响应（明标，25分）"])
            self.assertNotIn("技术方案（暗标，65分）", root_titles)
            self.assertIn("技术评分项1", root_titles)
            self.assertIn("资格与合规响应", root_titles)
            self.assertFalse(
                {"投标邀请", "投标人须知", "评标方法", "合同条款", "2026年6月", "第二章...\t6"}
                & {node.title for node in blueprint.nodes}
            )
            score_duty_ids = {f"D-S-{index:02d}" for index in range(1, 21)}
            score_assignments = [
                item
                for item in blueprint.assignments
                if item.role == "primary" and item.duty_id in score_duty_ids
            ]
            self.assertEqual({item.duty_id for item in score_assignments}, score_duty_ids)
            self.assertEqual(len({item.chapter_id for item in score_assignments}), 19)
            quality_assignment = next(
                item for item in score_assignments if item.duty_id == "D-S-20"
            )
            quality_node = next(
                node for node in blueprint.nodes if node.chapter_id == quality_assignment.chapter_id
            )
            self.assertIsNone(quality_node.parent_chapter_id)
            self.assertEqual(
                {item.duty_id for item in blueprint.assignments if item.role == "primary"},
                {item.duty_id for item in graph.duties},
            )
            self.assertEqual(blueprint.coverage_summary["score_point_count"], 20)
            self.assertEqual(blueprint.coverage_summary["score_primary_chapter_count"], 19)
            assignment_by_duty = {item.duty_id: item.chapter_id for item in blueprint.assignments}
            self.assertEqual(assignment_by_duty["D-R-1"], assignment_by_duty["D-S-01"])
            price_node = next(node for node in blueprint.nodes if node.chapter_id == assignment_by_duty["D-S-01"])
            self.assertIn("form/table", price_node.required_mentions)
            self.assertEqual(price_node.planned_tables, ["报价一览表"])
            self.assertIn("document_quality_gate", quality_node.required_mentions)
            self.assertFalse(any("整体评价" in node.title for node in blueprint.nodes))
            self.assertEqual(
                [gate.duty_id for gate in blueprint.document_quality_gates],
                ["D-S-20"],
            )

    def test_full_score_conditions_become_subheadings_under_scoring_factor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            conditions = [
                "项目任务背景描述清楚",
                "工作必要性和可行性理由充分、逻辑清晰",
                "工作目标明确、可行",
                "工作内容具体、翔实",
            ]
            topic = ResponseTopic(
                topic_id="T-S-target",
                topic_type="function",
                canonical_name="目标任务",
                intent="响应评分逻辑",
                summary="目标任务满分档要求",
                attributes={
                    "upstream_refs": ["ScoreModel:SP-target"],
                    "score_group_id": "G-technical",
                    "score_group_title": "技术部分（暗标，65分）",
                    "score_group_declared_points": 65,
                    "score_group_order": 0,
                    "score_point_order": 0,
                    "score_outline_path": ["目标任务"],
                    "full_score_conditions": conditions,
                    "planning_role": "content_section",
                    "response_shape": "narrative",
                },
                confidence=1,
            )
            duty = ResponseDuty(
                duty_id="D-S-target",
                topic_id=topic.topic_id,
                duty_type="explain",
                score_point_ids=["SP-target"],
                score_response_unit_ids=["SP-target-U01"],
                response_expectations=["完整覆盖目标任务满分档"],
                confidence=1,
            )
            graph = ResponseTopicGraph(
                revision=1,
                source_hashes={"score": "hash-score"},
                graph_id="TG-target",
                requirement_ledger_revision=1,
                score_model_revision=1,
                project_model_revision=1,
                root_topic_ids=[topic.topic_id],
                topics=[topic],
                duties=[duty],
            )

            blueprint = PlanningAgent(context).chapter_blueprint(graph, revision=1)

            target_node = next(node for node in blueprint.nodes if node.title == "目标任务")
            child_titles = [
                node.title
                for node in blueprint.nodes
                if node.parent_chapter_id == target_node.chapter_id
            ]
            self.assertEqual(
                child_titles,
                ["项目任务背景", "工作必要性和可行性", "工作目标", "工作内容"],
            )
            self.assertEqual(
                {
                    assignment.role
                    for assignment in blueprint.assignments
                    if assignment.chapter_id in {
                        node.chapter_id
                        for node in blueprint.nodes
                        if node.parent_chapter_id == target_node.chapter_id
                    }
                },
                {"supporting"},
            )
            condition_ids = [
                f"SP-target-C{index:02d}"
                for index in range(1, len(conditions) + 1)
            ]
            condition_nodes = [
                node
                for node in blueprint.nodes
                if node.parent_chapter_id == target_node.chapter_id
            ]
            blueprint = blueprint.model_copy(
                update={
                    "nodes": [
                        node.model_copy(
                            update={
                                "score_condition_ids": [
                                    condition_ids[
                                        condition_nodes.index(node)
                                    ]
                                ]
                            }
                        )
                        if node in condition_nodes
                        else node
                        for node in blueprint.nodes
                    ]
                }
            )
            anchor = SourceAnchor(
                source_input_id="score",
                chunk_id="score-target",
                location="table:1:row:1:cell:1",
            )
            score_model = ScoreModel(
                revision=1,
                source_hashes={"score": "hash-score"},
                model_id="SM-target",
                source_input_ids=["score"],
                total_points=4,
                groups=[
                    ScoreGroup(
                        group_id="G-technical",
                        title="技术部分（暗标，65分）",
                    )
                ],
                points=[
                    ScorePoint(
                        score_point_id="SP-target",
                        group_id="G-technical",
                        title="目标任务",
                        criterion="；".join(conditions),
                        max_points=4,
                        full_score_conditions=conditions,
                        score_conditions=[
                            ScoreCondition(
                                condition_id=condition_id,
                                text=text,
                                source_excerpt=text,
                                subject=text,
                                response_intent="完整响应",
                                source_anchor=anchor,
                            )
                            for condition_id, text in zip(
                                condition_ids,
                                conditions,
                                strict=True,
                            )
                        ],
                        response_units=[
                            ScoreResponseUnit(
                                unit_id="SP-target-U01",
                                title="目标任务",
                                condition_ids=condition_ids,
                                response_expectation="完整响应",
                            )
                        ],
                        response_expectation="完整响应",
                        source_anchors=[anchor],
                        confidence=1,
                    )
                ],
            )
            self.assertTrue(
                audit_chapter_blueprint(
                    blueprint,
                    graph,
                    score_model,
                )["passed"]
            )
            omitted = next(node for node in blueprint.nodes if node.title == "工作内容")
            broken = blueprint.model_copy(
                update={
                    "nodes": [
                        node for node in blueprint.nodes if node.chapter_id != omitted.chapter_id
                    ],
                    "assignments": [
                        assignment
                        for assignment in blueprint.assignments
                        if assignment.chapter_id != omitted.chapter_id
                    ],
                }
            )
            broken_audit = audit_chapter_blueprint(
                broken,
                graph,
                score_model,
            )
            self.assertFalse(broken_audit["passed"])
            self.assertIn(
                "SCORE_CONDITION_COVERAGE_MISSING",
                {item["code"] for item in broken_audit["findings"]},
            )

    def test_nested_scoring_factors_and_atomic_conditions_create_four_heading_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            topic = ResponseTopic(
                topic_id="T-S-preparation",
                topic_type="function",
                canonical_name="核查准备工作—数据接收内容与检查方法",
                intent="响应评分逻辑",
                summary="核查准备工作及数据接收满分档要求",
                attributes={
                    "upstream_refs": ["ScoreModel:SP-preparation"],
                    "score_group_id": "G-technical",
                    "score_group_title": "技术部分（暗标，65分）",
                    "score_group_declared_points": 65,
                    "score_group_order": 0,
                    "score_point_order": 0,
                    "score_outline_path": ["技术方法（43分）", "核查准备工作（6分）"],
                    "full_score_conditions": [
                        "1.核查准备工作全面细致，能够满足后续核查工作的需要",
                        "数据接收内容全面、具体",
                        "检查方法科学、重点突出、方法可行",
                    ],
                    "planning_role": "content_section",
                    "response_shape": "narrative",
                },
                confidence=1,
            )
            duty = ResponseDuty(
                duty_id="D-S-preparation",
                topic_id=topic.topic_id,
                duty_type="explain",
                score_point_ids=["SP-preparation"],
                response_expectations=["完整覆盖核查准备满分档"],
                confidence=1,
            )
            graph = ResponseTopicGraph(
                revision=1,
                source_hashes={"score": "hash-score"},
                graph_id="TG-preparation",
                requirement_ledger_revision=1,
                score_model_revision=1,
                project_model_revision=1,
                root_topic_ids=[topic.topic_id],
                topics=[topic],
                duties=[duty],
            )

            blueprint = PlanningAgent(context).chapter_blueprint(graph, revision=1)
            by_title = {node.title: node for node in blueprint.nodes}

            self.assertNotIn("技术方案（暗标，65分）", by_title)
            self.assertIsNone(by_title["技术方法（43分）"].parent_chapter_id)
            self.assertEqual(
                by_title["核查准备工作（6分）"].parent_chapter_id,
                by_title["技术方法（43分）"].chapter_id,
            )
            self.assertEqual(
                by_title["数据接收内容与检查方法"].parent_chapter_id,
                by_title["核查准备工作（6分）"].chapter_id,
            )
            self.assertEqual(
                by_title["检查方法"].parent_chapter_id,
                by_title["数据接收内容与检查方法"].chapter_id,
            )
            self.assertTrue(audit_chapter_blueprint(blueprint, graph)["passed"])

    def test_topic_graph_skips_blocked_requirements_and_marks_special_score_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            anchor_open = SourceAnchor(
                source_input_id="tender-1",
                chunk_id="r-open",
                location="paragraph:1",
            )
            anchor_blocked = SourceAnchor(
                source_input_id="tender-1",
                chunk_id="r-blocked",
                location="paragraph:2",
            )
            anchor_waived = SourceAnchor(
                source_input_id="tender-1",
                chunk_id="r-waived",
                location="paragraph:3",
            )
            ledger = RequirementLedger(
                revision=1,
                source_hashes={"tender-1": "hash-tender"},
                requirements=[
                    RequirementItem(
                        requirement_id="R-open",
                        kind=RequirementKind.MANDATORY,
                        source_anchor=anchor_open,
                        original_text="须提交报价表",
                        normalized_requirement="提交报价表",
                        response_type="form",
                        evidence_policy="tender",
                        status="confirmed",
                    ),
                    RequirementItem(
                        requirement_id="R-blocked",
                        kind=RequirementKind.MANDATORY,
                        source_anchor=anchor_blocked,
                        original_text="冲突条款",
                        normalized_requirement="冲突条款",
                        response_type="narrative",
                        evidence_policy="tender",
                        status="blocked",
                    ),
                    RequirementItem(
                        requirement_id="R-waived",
                        kind=RequirementKind.CONTRACT,
                        source_anchor=anchor_waived,
                        original_text="已豁免条款",
                        normalized_requirement="已豁免条款",
                        response_type="narrative",
                        evidence_policy="tender",
                        status="waived",
                    ),
                ],
            )
            score_anchor_1 = SourceAnchor(
                source_input_id="score-1",
                chunk_id="s-price",
                location="table:1:row:1",
            )
            score_anchor_2 = SourceAnchor(
                source_input_id="score-1",
                chunk_id="s-quality",
                location="table:1:row:2",
            )
            scores = ScoreModel(
                revision=1,
                source_hashes={"score-1": "hash-score"},
                model_id="SM-test",
                source_input_ids=["score-1"],
                total_points=15,
                groups=[
                    ScoreGroup(group_id="G-price", title="价格部分（10分）", declared_points=10),
                    ScoreGroup(group_id="G-tech", title="技术部分（5分）", declared_points=5),
                ],
                points=[
                    ScorePoint(
                        score_point_id="SP-price",
                        group_id="G-price",
                        title="投标报价",
                        criterion="报价表计算得分",
                        max_points=10,
                        response_expectation="填写报价表",
                        linked_requirement_ids=["R-open"],
                        source_anchors=[score_anchor_1],
                        confidence=1,
                    ),
                    ScorePoint(
                        score_point_id="SP-quality",
                        group_id="G-tech",
                        title="投标文件整体评价",
                        criterion="对投标文件整体质量进行评价",
                        max_points=5,
                        response_expectation="全文保持一致完整",
                        linked_requirement_ids=["R-blocked"],
                        source_anchors=[score_anchor_2],
                        confidence=1,
                    ),
                ],
            )
            project = ProjectModel(
                revision=1,
                source_hashes={"tender-1": "hash-tender", "score-1": "hash-score"},
                project_id="P-test",
                requirement_ids=["R-open", "R-blocked", "R-waived"],
                score_point_ids=["SP-price", "SP-quality"],
            )

            graph = PlanningAgent(context).topic_graph(ledger, scores, project, revision=1)

            self.assertNotIn("T-R-blocked", {topic.topic_id for topic in graph.topics})
            self.assertNotIn("T-R-waived", {topic.topic_id for topic in graph.topics})
            self.assertNotIn("D-R-blocked", {duty.duty_id for duty in graph.duties})
            quality_topic = next(topic for topic in graph.topics if topic.topic_id == "T-S-quality")
            price_topic = next(topic for topic in graph.topics if topic.topic_id == "T-S-price")
            self.assertEqual(quality_topic.attributes["planning_role"], "document_quality_gate")
            self.assertEqual(price_topic.attributes["response_shape"], "form/table")
            quality_duty = next(duty for duty in graph.duties if duty.duty_id == "D-S-quality")
            self.assertEqual(quality_duty.requirement_ids, [])


if __name__ == "__main__":
    unittest.main()
