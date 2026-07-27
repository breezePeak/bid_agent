"""Planning Agent: controlled projections from promoted requirements and scoring logic."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from control_plane import WorkspaceContext

from .contracts import (
    EvidenceNeed, InputRole, ProjectFact, ProjectModel, RequirementKind, RequirementLedger,
    ResponseDuty, ResponseTopic, ResponseTopicGraph, ScoreModel, SourceBlock, TopicEdge,
)
from .proposals import ProposalEnvelope, dependency_fingerprint


class PlanningAgent:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def project_model(
        self, ledger: RequirementLedger, scores: ScoreModel, source_blocks: list[SourceBlock], *, revision: int,
    ) -> ProjectModel:
        mandatory = [item for item in ledger.requirements if item.kind is RequirementKind.MANDATORY]
        qualifications = [item for item in ledger.requirements if item.kind is RequirementKind.QUALIFICATION]
        company_blocks = [block for block in source_blocks if block.input_role is InputRole.COMPANY]
        tender_blocks = [block for block in source_blocks if block.input_role is InputRole.TENDER]
        evidence_needs: list[EvidenceNeed] = []
        unknowns: list[str] = []
        if qualifications and not company_blocks:
            unknowns.append("尚未提供可核验的企业资质、人员或业绩材料")
            evidence_needs.append(EvidenceNeed(need_id="EN-company-qualification", question="请补充与资格要求对应的企业资质、人员和业绩材料。", topic_id="company_qualification", priority="blocking", blocking_scope="content_unit", deadline_stage="write_content", query_budget=0))
        for candidate in scores.evidence_need_candidates:
            evidence_needs.append(EvidenceNeed(need_id=candidate.need_id, question=candidate.question, topic_id=f"score:{candidate.score_point_id}", priority=candidate.priority, blocking_scope="content_unit" if candidate.priority == "blocking" else "none", deadline_stage="resolve_evidence", query_budget=0))
        deliverables = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.DELIVERABLE]
        acceptance = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.ACCEPTANCE]
        if not deliverables:
            unknowns.append("招标来源未识别出明确交付物")
        if not acceptance:
            unknowns.append("招标来源未识别出明确验收条件")
        source_hashes = {**ledger.source_hashes, **scores.source_hashes}
        return ProjectModel(
            revision=revision, source_hashes=source_hashes,
            project_id=f"project-{hashlib.sha256('|'.join(item.requirement_id for item in ledger.requirements).encode()).hexdigest()[:12]}",
            identity={"project_name": tender_blocks[0].content[:80] if tender_blocks else "未命名项目"},
            background=[tender_blocks[0].content] if tender_blocks else [],
            goals=[item.normalized_requirement for item in mandatory[:3]],
            scope=[item.normalized_requirement for item in mandatory], work_packages=[item.normalized_requirement for item in mandatory],
            deliverables=deliverables, acceptance_conditions=acceptance,
            milestones=[item.normalized_requirement for item in ledger.requirements if any(token in item.normalized_requirement for token in ("工期", "期限", "工作日", "个月", "年度"))],
            roles=[item.normalized_requirement for item in qualifications],
            constraints=[item.normalized_requirement for item in ledger.requirements if item.kind in {RequirementKind.CONTRACT, RequirementKind.QUALIFICATION}],
            confirmed_facts=[ProjectFact(fact_id=f"F-{hashlib.sha256(block.block_id.encode()).hexdigest()[:12]}", statement=block.content, source_anchor=block.source_anchor) for block in company_blocks],
            unknowns=unknowns, requirement_ids=[item.requirement_id for item in ledger.requirements],
            score_point_ids=[point.score_point_id for point in scores.points], evidence_needs=evidence_needs,
        )

    def topic_graph(self, ledger: RequirementLedger, scores: ScoreModel, project: ProjectModel, *, revision: int) -> ResponseTopicGraph:
        topics: list[ResponseTopic] = []
        duties: list[ResponseDuty] = []
        edges: list[TopicEdge] = []
        need_ids_by_score = defaultdict(list)
        for need in project.evidence_needs:
            if need.topic_id.startswith("score:"):
                need_ids_by_score[need.topic_id.removeprefix("score:")].append(need.need_id)
        for index, requirement in enumerate(ledger.requirements):
            topic_id = f"T-R-{requirement.requirement_id.removeprefix('R-')}"
            topic_type, duty_type = self._requirement_topic_type(requirement.kind, requirement.normalized_requirement)
            topics.append(ResponseTopic(topic_id=topic_id, topic_type=topic_type, canonical_name=requirement.normalized_requirement[:80], intent="响应采购义务", summary=requirement.normalized_requirement, attributes={"upstream_refs": [f"RequirementLedger:{requirement.requirement_id}"]}, source_anchors=[requirement.source_anchor], confidence=1.0, review_status="confirmed"))
            duties.append(ResponseDuty(duty_id=f"D-R-{requirement.requirement_id.removeprefix('R-')}", topic_id=topic_id, duty_type=duty_type, requirement_ids=[requirement.requirement_id], response_expectations=[requirement.response_type], priority="blocking" if requirement.severity == "blocking" else "normal", confidence=1.0, review_status="confirmed"))
        for index, point in enumerate(scores.points):
            topic_id = f"T-S-{point.score_point_id.removeprefix('SP-')}"
            topics.append(ResponseTopic(topic_id=topic_id, topic_type=self._score_topic_type(point.title), canonical_name=point.title, intent="响应评分逻辑", summary=point.criterion, attributes={"upstream_refs": [f"ScoreModel:{point.score_point_id}"]}, source_anchors=point.source_anchors, confidence=point.confidence, review_status=point.review_status))
            duties.append(ResponseDuty(duty_id=f"D-S-{point.score_point_id.removeprefix('SP-')}", topic_id=topic_id, duty_type="verify" if point.disqualifying else "explain", requirement_ids=point.linked_requirement_ids, score_point_ids=[point.score_point_id], response_expectations=[point.response_expectation], evidence_need_ids=need_ids_by_score[point.score_point_id], priority="blocking" if point.disqualifying else ("high" if point.max_points and point.max_points >= 10 else "normal"), confidence=point.confidence, review_status=point.review_status))
            for requirement_id in point.linked_requirement_ids:
                requirement_topic = f"T-R-{requirement_id.removeprefix('R-')}"
                edges.append(TopicEdge(edge_id=f"E-{point.score_point_id}-{requirement_id}", source_topic_id=topic_id, target_topic_id=requirement_topic, relation="supports_score", order=index, requirement_ids=[requirement_id], rationale="评分点通过 Requirement ID 引用采购义务", confidence=1.0))
        return ResponseTopicGraph(revision=revision, source_hashes={**ledger.source_hashes, **scores.source_hashes}, graph_id=f"TG-{hashlib.sha256((project.project_id + str(revision)).encode()).hexdigest()[:12]}", requirement_ledger_revision=ledger.revision, score_model_revision=scores.revision, project_model_revision=project.revision, root_topic_ids=[topic.topic_id for topic in topics], topics=topics, duties=duties, edges=edges)

    def proposal(self, artifact_kind: str, payload: ProjectModel | ResponseTopicGraph, *, base_revision: int, operation_id: str, upstream_revisions: tuple[int, ...]) -> ProposalEnvelope:
        source_ids = sorted(payload.source_hashes)
        return ProposalEnvelope(artifact_kind=artifact_kind, producer_role="planning_agent", operation_id=operation_id, base_revision=base_revision, dependency_fingerprint=dependency_fingerprint(payload.source_hashes, upstream_revisions, artifact_kind, "v3_planning_agent_v1.0"), payload=payload.model_dump(mode="json"), cited_source_ids=source_ids, prompt_version="v3_planning_agent_v1.0", model_fingerprint="deterministic_v3_agent")

    @staticmethod
    def _requirement_topic_type(kind: RequirementKind, text: str) -> tuple[str, str]:
        if kind is RequirementKind.QUALIFICATION: return "qualification", "verify"
        if kind is RequirementKind.DELIVERABLE: return "deliverable", "commit"
        if kind is RequirementKind.ACCEPTANCE: return "acceptance", "accept"
        if kind is RequirementKind.CONTRACT: return "commercial", "commit"
        if any(token in text for token in ("安全", "保密", "权限")): return "security", "design"
        if any(token in text for token in ("数据", "库", "接口")): return "data", "design"
        if any(token in text for token in ("实施", "部署", "培训")): return "implementation", "implement"
        if any(token in text for token in ("运维", "服务", "响应")): return "service_operation", "operate"
        if any(token in text for token in ("架构", "平台", "系统")): return "architecture", "design"
        return "function", "explain"

    @staticmethod
    def _score_topic_type(title: str) -> str:
        return PlanningAgent._requirement_topic_type(RequirementKind.MANDATORY, title)[0]
