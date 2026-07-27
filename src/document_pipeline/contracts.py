from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


V3_SCHEMA_VERSION = "v3"


class ContractModel(BaseModel):
    """Common immutable-artifact metadata required by every V3 contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["v3"] = V3_SCHEMA_VERSION
    revision: int = Field(default=1, ge=1)
    source_hashes: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_hashes")
    @classmethod
    def source_hashes_must_be_complete(cls, value: dict[str, str]) -> dict[str, str]:
        for key, digest in value.items():
            if not key.strip() or not digest.strip():
                raise ValueError("source_hashes 的键和值不能为空")
        return value


class InputRole(str, Enum):
    TENDER = "tender"
    SCORE = "score"
    TEMPLATE = "template"
    AMENDMENT = "amendment"
    COMPANY = "company"
    REFERENCE = "reference"
    GUIDANCE = "guidance"


class InputItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_id: str = Field(min_length=1)
    role: InputRole
    filename: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    version: int = Field(ge=1)
    active: bool = True
    replaces_input_id: str | None = None
    issued_at: str | None = None
    supersedes_input_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def amendment_requires_issue_date(self) -> "InputItem":
        if self.role is InputRole.AMENDMENT and not self.issued_at:
            raise ValueError("补遗输入必须声明 issued_at")
        if len(self.supersedes_input_ids) != len(set(self.supersedes_input_ids)):
            raise ValueError("supersedes_input_ids 不允许重复")
        return self


class InputManifest(ContractModel):
    inputs: list[InputItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_active_inputs(self) -> "InputManifest":
        ids = [item.input_id for item in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("InputManifest 不允许重复 input_id")
        templates = [item for item in self.inputs if item.role is InputRole.TEMPLATE and item.active]
        if len(templates) > 1:
            raise ValueError("同一工作空间只允许一个活动 template")
        known_ids = set(ids)
        for item in self.inputs:
            if item.replaces_input_id and item.replaces_input_id not in known_ids:
                raise ValueError(f"InputManifest replaces_input_id 不存在: {item.replaces_input_id}")
            if unknown := set(item.supersedes_input_ids) - known_ids:
                raise ValueError(f"InputManifest supersedes_input_ids 不存在: {sorted(unknown)}")
        return self


class SourceAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_input_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    location: str = Field(min_length=1)


class NormalizedChunk(BaseModel):
    """A stable, role-aware source fragment used by every V3 content stage."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chunk_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    role: InputRole
    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1)
    source_anchor: SourceAnchor


SOURCE_PARSER_VERSION = "v3-source-parser-1"

SourceBlockKind = Literal[
    "heading",
    "paragraph",
    "list_item",
    "table",
    "table_cell",
    "image",
    "ocr_gap",
    # Legacy PDF labels retained for deterministic identity continuity.
    "pdf_text",
    "pdf_table_cell",
]


class SourceBlock(BaseModel):
    """Loss-minimising structure recovered from a single frozen input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    input_role: InputRole
    block_kind: SourceBlockKind
    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = Field(default=None, ge=1)
    paragraph_index: int | None = Field(default=None, ge=0)
    table_index: int | None = Field(default=None, ge=0)
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = None
    reading_order: int | None = Field(default=None, ge=0)
    parser_version: str = Field(default=SOURCE_PARSER_VERSION, min_length=1)
    source_anchor: SourceAnchor
    content_hash: str = Field(min_length=1)

    @field_validator("bbox")
    @classmethod
    def bbox_has_four_numbers(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if len(value) != 4:
            raise ValueError("bbox 必须是 [x0, y0, x1, y1]")
        return value


class SourceNormalizationCoverageItem(BaseModel):
    """Per physical element coverage record that promotes with SourceIndex."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    element_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    element_kind: str = Field(min_length=1)
    status: Literal["normalized", "exempt", "structure_gap"]
    locator: str = Field(min_length=1)
    reason: str | None = None
    block_id: str | None = None


class SourceNormalizationCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    items: list[SourceNormalizationCoverageItem] = Field(default_factory=list)

    @property
    def structure_gap_count(self) -> int:
        return sum(1 for item in self.items if item.status == "structure_gap")


class AmendmentRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_id: str = Field(min_length=1)
    issued_at: str = Field(min_length=1)
    supersedes_input_ids: list[str] = Field(default_factory=list)
    replaces_input_id: str | None = None


class SourceInputStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_id: str = Field(min_length=1)
    status: Literal["processed", "blocked", "partial"]
    block_count: int = Field(default=0, ge=0)
    reason: str | None = None


class SourceIndex(ContractModel):
    """Canonical structured recovery of frozen inputs. Disk JSON is only a projection."""

    parser_version: str = Field(default=SOURCE_PARSER_VERSION, min_length=1)
    input_manifest_revision: int = Field(ge=1)
    input_manifest_artifact_hash: str = Field(default="", min_length=0)
    blocks: list[SourceBlock] = Field(default_factory=list)
    coverage: SourceNormalizationCoverage = Field(default_factory=SourceNormalizationCoverage)
    amendments: list[AmendmentRelation] = Field(default_factory=list)
    input_status: list[SourceInputStatus] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocks_have_unique_ids(self) -> "SourceIndex":
        ids = [block.block_id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("SourceIndex 不允许重复 block_id")
        return self


class RequirementKind(str, Enum):
    MANDATORY = "mandatory"
    SCORE = "score"
    QUALIFICATION = "qualification"
    DELIVERABLE = "deliverable"
    ACCEPTANCE = "acceptance"
    CONTRACT = "contract"


class RequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_id: str = Field(min_length=1)
    kind: RequirementKind
    source_anchor: SourceAnchor
    original_text: str = Field(min_length=1)
    normalized_requirement: str = Field(min_length=1)
    severity: Literal["blocking", "major", "normal"] = "normal"
    response_type: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    status: Literal["open", "confirmed", "blocked", "waived"] = "open"
    clause_id: str | None = None
    parent_clause_id: str | None = None
    subject: str | None = None
    action: str | None = None
    target_object: str | None = None
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    quantitative_metrics: dict[str, Any] = Field(default_factory=dict)
    superseded_by_input_id: str | None = None


class RequirementLedger(ContractModel):
    requirements: list[RequirementItem] = Field(default_factory=list)
    coverage_audit: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_requirement_ids(self) -> "RequirementLedger":
        ids = [item.requirement_id for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("RequirementLedger 不允许重复 requirement_id")
        return self


class ScoreGroup(BaseModel):
    """A named scoring section; points remain the source of scoring truth."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    group_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    declared_points: float | None = Field(default=None, ge=0)


class ScoringLevel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1)
    points: float | None = Field(default=None, ge=0)
    criterion: str = Field(min_length=1)


class ScorePoint(BaseModel):
    """One source-traceable scoring rule, linked to rather than copied from requirements."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    score_point_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    max_points: float | None = Field(default=None, ge=0)
    scoring_levels: list[ScoringLevel] = Field(default_factory=list)
    disqualifying: bool = False
    response_expectation: str = Field(min_length=1)
    response_depth: Literal["basic", "substantive", "detailed"] = "basic"
    required_evidence_types: list[str] = Field(default_factory=list)
    linked_requirement_ids: list[str] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def score_point_references_are_unique(self) -> "ScorePoint":
        if len(self.linked_requirement_ids) != len(set(self.linked_requirement_ids)):
            raise ValueError("ScorePoint 不允许重复 linked_requirement_ids")
        anchor_ids = [(anchor.source_input_id, anchor.chunk_id) for anchor in self.source_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("ScorePoint 不允许重复 source_anchors")
        return self


class ScoreEvidenceNeedCandidate(BaseModel):
    """A non-canonical evidence gap proposed by Score Agent for later planning."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    need_id: str = Field(min_length=1)
    score_point_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required_evidence_types: list[str] = Field(default_factory=list)
    priority: Literal["blocking", "high", "normal"] = "normal"


class ScoreModel(ContractModel):
    model_id: str = Field(min_length=1)
    source_input_ids: list[str] = Field(default_factory=list)
    total_points: float = Field(ge=0)
    groups: list[ScoreGroup] = Field(default_factory=list)
    points: list[ScorePoint] = Field(default_factory=list)
    evidence_need_candidates: list[ScoreEvidenceNeedCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_totals_and_references_are_consistent(self) -> "ScoreModel":
        if not self.model_id.strip() or any(not source_id.strip() for source_id in self.source_input_ids):
            raise ValueError("ScoreModel model_id 与 source_input_ids 不能为空")
        if len(self.source_input_ids) != len(set(self.source_input_ids)):
            raise ValueError("ScoreModel 不允许重复 source_input_ids")
        group_ids = [group.group_id for group in self.groups]
        point_ids = [point.score_point_id for point in self.points]
        if len(group_ids) != len(set(group_ids)) or len(point_ids) != len(set(point_ids)):
            raise ValueError("ScoreModel 不允许重复 group_id 或 score_point_id")
        if unknown := {point.group_id for point in self.points} - set(group_ids):
            raise ValueError(f"ScorePoint 指向未知评分组: {sorted(unknown)}")
        if unknown := {
            anchor.source_input_id
            for point in self.points
            for anchor in point.source_anchors
        } - set(self.source_input_ids):
            raise ValueError(f"ScorePoint 指向未知评分输入: {sorted(unknown)}")
        scored_points = [point.max_points for point in self.points if point.max_points is not None]
        if len(scored_points) == len(self.points) and abs(sum(scored_points) - self.total_points) > 1e-6:
            raise ValueError("ScoreModel total_points 与 ScorePoint 分值合计不一致")
        for group in self.groups:
            points = [point.max_points for point in self.points if point.group_id == group.group_id]
            if group.declared_points is not None and points and all(point is not None for point in points):
                if abs(sum(point for point in points if point is not None) - group.declared_points) > 1e-6:
                    raise ValueError(f"ScoreGroup {group.group_id} 小计与 ScorePoint 分值不一致")
        candidate_ids = [candidate.need_id for candidate in self.evidence_need_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("ScoreModel 不允许重复 EvidenceNeed 候选 ID")
        if unknown := {candidate.score_point_id for candidate in self.evidence_need_candidates} - set(point_ids):
            raise ValueError(f"EvidenceNeed 候选指向未知 ScorePoint: {sorted(unknown)}")
        return self


class ProjectFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    source_anchor: SourceAnchor | None = None
    requirement_ids: list[str] = Field(default_factory=list)


class EvidenceNeed(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    need_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    priority: Literal["blocking", "high", "normal", "low"] = "normal"
    blocking_scope: Literal["workspace", "contract", "content_unit", "none"] = "none"
    deadline_stage: str = Field(min_length=1)
    query_budget: int = Field(ge=0, le=20)
    status: Literal["open", "researching", "satisfied", "gap", "cancelled"] = "open"


class EvidenceSourceType(str, Enum):
    TENDER = "tender"
    COMPANY = "company"
    OFFICIAL = "official"
    STANDARD = "standard"
    ACADEMIC = "academic"
    WEB = "web"
    MANUAL = "manual"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    source_type: EvidenceSourceType
    title: str = Field(min_length=1)
    source_url: str | None = None
    publisher: str = Field(min_length=1)
    content: str = Field(min_length=1)
    claim_types: list[Literal["project_context", "standard", "method", "enterprise_capability"]] = Field(default_factory=list)
    retrieved_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def enterprise_claims_require_company_evidence(self) -> "EvidenceItem":
        if "enterprise_capability" in self.claim_types and self.source_type is not EvidenceSourceType.COMPANY:
            raise ValueError("外部资料不能证明企业能力")
        return self


class EvidenceBatch(ContractModel):
    batch_id: str = Field(min_length=1)
    need_id: str = Field(min_length=1)
    query_count: int = Field(ge=0)
    items: list[EvidenceItem] = Field(default_factory=list)
    status: Literal["published", "gap", "failed"]
    error: str | None = None


class ProjectModel(ContractModel):
    project_id: str = Field(min_length=1)
    identity: dict[str, str] = Field(default_factory=dict)
    background: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    work_packages: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    processing: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    acceptance_conditions: list[str] = Field(default_factory=list)
    milestones: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    confirmed_facts: list[ProjectFact] = Field(default_factory=list)
    inferences: list[ProjectFact] = Field(default_factory=list)
    conflicts: list[ProjectFact] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    evidence_needs: list[EvidenceNeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_project_references(self) -> "ProjectModel":
        fact_ids = [fact.fact_id for group in (self.confirmed_facts, self.inferences, self.conflicts) for fact in group]
        need_ids = [need.need_id for need in self.evidence_needs]
        if len(fact_ids) != len(set(fact_ids)) or len(need_ids) != len(set(need_ids)):
            raise ValueError("ProjectModel 不允许重复事实或 EvidenceNeed ID")
        return self


TopicType = Literal[
    "business_domain", "business_flow", "business_capability", "function", "architecture", "data", "integration",
    "security", "non_functional", "implementation", "project_management", "service_operation", "training",
    "deliverable", "acceptance", "qualification", "commercial", "compliance",
]
DutyType = Literal["summarize", "explain", "design", "implement", "operate", "verify", "accept", "commit", "cross_reference"]
TopicRelation = Literal["parent_of", "depends_on", "realizes", "constrained_by", "step_of", "produces", "consumes", "interfaces_with", "verified_by", "supports_score"]


class ResponseTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic_id: str = Field(min_length=1)
    parent_topic_id: str | None = None
    topic_type: TopicType
    canonical_name: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"


class ResponseDuty(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    duty_id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    duty_type: DutyType
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    response_expectations: list[str] = Field(default_factory=list)
    evidence_need_ids: list[str] = Field(default_factory=list)
    priority: Literal["blocking", "high", "normal", "low"] = "normal"
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"


class TopicEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    edge_id: str = Field(min_length=1)
    source_topic_id: str = Field(min_length=1)
    target_topic_id: str = Field(min_length=1)
    relation: TopicRelation
    order: int = Field(ge=0)
    requirement_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ResponseTopicGraph(ContractModel):
    graph_id: str = Field(min_length=1)
    requirement_ledger_revision: int = Field(ge=1)
    score_model_revision: int = Field(ge=1)
    project_model_revision: int = Field(ge=1)
    root_topic_ids: list[str] = Field(default_factory=list)
    topics: list[ResponseTopic] = Field(default_factory=list)
    duties: list[ResponseDuty] = Field(default_factory=list)
    edges: list[TopicEdge] = Field(default_factory=list)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def graph_references_are_complete_and_dependencies_acyclic(self) -> "ResponseTopicGraph":
        topic_ids = [topic.topic_id for topic in self.topics]
        duty_ids = [duty.duty_id for duty in self.duties]
        edge_ids = [edge.edge_id for edge in self.edges]
        if any(len(values) != len(set(values)) for values in (topic_ids, duty_ids, edge_ids)):
            raise ValueError("ResponseTopicGraph 不允许重复 Topic、Duty 或 Edge ID")
        known_topics = set(topic_ids)
        if unknown := set(self.root_topic_ids) - known_topics:
            raise ValueError(f"ResponseTopicGraph 存在悬空根 Topic: {sorted(unknown)}")
        if unknown := {topic.parent_topic_id for topic in self.topics if topic.parent_topic_id} - known_topics:
            raise ValueError(f"ResponseTopicGraph 存在悬空父 Topic: {sorted(unknown)}")
        if unknown := {duty.topic_id for duty in self.duties} - known_topics:
            raise ValueError(f"ResponseDuty 指向未知 Topic: {sorted(unknown)}")
        for topic in self.topics:
            if topic.review_status == "confirmed" and not (topic.source_anchors or topic.attributes.get("upstream_refs")):
                raise ValueError(f"已确认 Topic 缺少来源或上游引用: {topic.topic_id}")
        dependencies: dict[str, set[str]] = {topic_id: set() for topic_id in known_topics}
        for edge in self.edges:
            if edge.source_topic_id not in known_topics or edge.target_topic_id not in known_topics:
                raise ValueError(f"TopicEdge 指向未知 Topic: {edge.edge_id}")
            if edge.relation == "depends_on":
                dependencies[edge.source_topic_id].add(edge.target_topic_id)
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(topic_id: str) -> None:
            if topic_id in visiting:
                raise ValueError("ResponseTopicGraph 执行依赖存在环")
            if topic_id not in visited:
                visiting.add(topic_id)
                for downstream in dependencies[topic_id]:
                    visit(downstream)
                visiting.remove(topic_id)
                visited.add(topic_id)
        for topic_id in known_topics:
            visit(topic_id)
        return self


class BlueprintNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_id: str = Field(min_length=1)
    parent_chapter_id: str | None = None
    order: int = Field(ge=0)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    forbidden_topic_ids: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    cross_references: list[str] = Field(default_factory=list)
    planned_tables: list[str] = Field(default_factory=list)
    planned_figures: list[str] = Field(default_factory=list)
    target_size: int = Field(default=800, ge=1)
    template_target: str | None = None


class TopicChapterAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    assignment_id: str = Field(min_length=1)
    duty_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    role: Literal["primary", "supporting", "mention", "cross_reference"]
    response_scope: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False


class ChapterBlueprint(ContractModel):
    blueprint_id: str = Field(min_length=1)
    mode: DocumentMode
    topic_graph_revision: int = Field(ge=1)
    template_structure_revision: int | None = Field(default=None, ge=1)
    nodes: list[BlueprintNode] = Field(min_length=1)
    assignments: list[TopicChapterAssignment] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    review_status: Literal["draft", "confirmed", "blocked"] = "draft"

    @model_validator(mode="after")
    def primary_duties_are_complete(self) -> "ChapterBlueprint":
        node_ids = [node.chapter_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ChapterBlueprint 不允许重复 chapter_id")
        if unknown := {assignment.chapter_id for assignment in self.assignments} - set(node_ids):
            raise ValueError(f"TopicChapterAssignment 指向未知章节: {sorted(unknown)}")
        primaries = [assignment.duty_id for assignment in self.assignments if assignment.role == "primary"]
        if len(primaries) != len(set(primaries)):
            raise ValueError("每个 Duty 只能有一个 primary chapter")
        return self


class DocumentMode(str, Enum):
    TEMPLATE_STRICT = "template_strict"
    AUTO_OUTLINE = "auto_outline"


class ContractNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(min_length=1)
    parent_node_id: str | None = None
    order: int = Field(ge=0)
    level: int = Field(default=1, ge=1, le=9)
    numbering: str | None = None
    writable_target: str = Field(min_length=1)
    title: str = Field(min_length=1)
    requirement_ids: list[str] = Field(default_factory=list)


class TemplateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slot_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    kind: Literal["text_slot", "cell_slot", "flow_slot", "repeat_slot"]
    anchor: str = Field(min_length=1)


class _DocumentContractBase(ContractModel):
    nodes: list[ContractNode] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_node_tree(self) -> "_DocumentContractBase":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("DocumentContract 不允许重复 node_id")
        known_ids = set(node_ids)
        for node in self.nodes:
            if node.parent_node_id and node.parent_node_id not in known_ids:
                raise ValueError(f"DocumentContract 存在悬空父节点: {node.parent_node_id}")
        return self


class TemplateContract(_DocumentContractBase):
    mode: Literal[DocumentMode.TEMPLATE_STRICT] = DocumentMode.TEMPLATE_STRICT
    template_hash: str = Field(min_length=1)
    structural_fingerprint: str = Field(min_length=1)
    slots: list[TemplateSlot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def slots_must_target_known_nodes(self) -> "TemplateContract":
        node_ids = {node.node_id for node in self.nodes}
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("TemplateContract 不允许重复 slot_id")
        if unknown := {slot.node_id for slot in self.slots} - node_ids:
            raise ValueError(f"TemplateContract slot 指向未知节点: {sorted(unknown)}")
        return self


class TemplateStructureContract(ContractModel):
    """Read-only template topology compiled before semantic planning."""

    template_input_id: str = Field(min_length=1)
    template_hash: str = Field(min_length=1)
    structural_fingerprint: str = Field(min_length=1)
    nodes: list[ContractNode] = Field(min_length=1)
    slots: list[TemplateSlot] = Field(default_factory=list)

    @model_validator(mode="after")
    def structural_slots_target_known_nodes(self) -> "TemplateStructureContract":
        node_ids = {node.node_id for node in self.nodes}
        if unknown := {slot.node_id for slot in self.slots} - node_ids:
            raise ValueError(f"TemplateStructureContract slot 指向未知节点: {sorted(unknown)}")
        return self


class OutlineContract(_DocumentContractBase):
    mode: Literal[DocumentMode.AUTO_OUTLINE] = DocumentMode.AUTO_OUTLINE


DocumentContract = Annotated[TemplateContract | OutlineContract, Field(discriminator="mode")]
DOCUMENT_CONTRACT_ADAPTER = TypeAdapter(DocumentContract)


class DocumentNodePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(min_length=1)
    primary_requirement_ids: list[str] = Field(default_factory=list)
    primary_score_ids: list[str] = Field(default_factory=list)
    owned_topic_ids: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    forbidden_topic_ids: list[str] = Field(default_factory=list)


class DocumentPlan(ContractModel):
    contract_revision: int = Field(ge=1)
    nodes: list[DocumentNodePlan] = Field(min_length=1)

    @model_validator(mode="after")
    def primary_owners_are_unique(self) -> "DocumentPlan":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("DocumentPlan 不允许重复 node_id")
        for label, values in (
            ("requirement", [item for node in self.nodes for item in node.primary_requirement_ids]),
            ("score", [item for node in self.nodes for item in node.primary_score_ids]),
            ("topic", [item for node in self.nodes for item in node.owned_topic_ids]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"DocumentPlan 每个 {label} 只能有一个 primary_owner")
        return self


class ContentUnit(ContractModel):
    unit_id: str = Field(min_length=1)
    contract_revision: int = Field(ge=1)
    node_ids: list[str] = Field(min_length=1)
    upstream_unit_ids: list[str] = Field(default_factory=list)


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    type: Literal["paragraph", "list", "table", "figure_ref", "cross_reference"]
    content: str = Field(min_length=1)
    topic_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    human_locked: bool = False
    critical_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def critical_claims_need_sources(self) -> "ContentBlock":
        if self.critical_claims and not (self.evidence_ids or self.fact_ids):
            raise ValueError("关键 Claim 必须关联 evidence_ids 或 fact_ids")
        return self


class IntegratedDocument(ContractModel):
    contract_revision: int = Field(ge=1)
    plan_revision: int = Field(ge=1)
    blocks: list[ContentBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocks_are_unique(self) -> "IntegratedDocument":
        ids = [block.block_id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("IntegratedDocument 不允许重复 block_id")
        return self


class QualityReport(ContractModel):
    report_id: str = Field(min_length=1)
    verdict: Literal["pass", "warn", "fail"]
    findings: list[dict[str, Any]] = Field(default_factory=list)


class ChangeSet(ContractModel):
    change_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    changed_inputs: list[str] = Field(default_factory=list)
    changed_facts: list[str] = Field(default_factory=list)
    changed_requirements: list[str] = Field(default_factory=list)
    affected_contract_nodes: list[str] = Field(default_factory=list)
    affected_content_units: list[str] = Field(default_factory=list)
    status: Literal["pending", "applied", "cancelled"] = "pending"


class ArtifactManifest(ContractModel):
    artifact_id: str = Field(min_length=1)
    artifact_path: str = Field(pattern=r"^workspace/v3/")
    producer: str = Field(min_length=1)
    dependency_fingerprint: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)


def document_contract_json_schema() -> dict[str, Any]:
    """Return the stable JSON Schema for the discriminated contract union."""
    return DOCUMENT_CONTRACT_ADAPTER.json_schema()
