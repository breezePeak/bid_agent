from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)


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
    # Reserved for the isolated bid-rewrite parser.  The generic uploads API
    # rejects this role so legacy content can never enter InputManifest.
    LEGACY_BID = "legacy_bid"


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


SOURCE_PARSER_VERSION = "v3-source-parser-2"

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


class LegacyBidSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    legacy_bid_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    version: int = Field(ge=1)
    active: bool = True
    stored_path: str = Field(pattern=r"^workspace/v3/legacy_bid_sources/")


class LegacyBidSourceManifest(ContractModel):
    sources: list[LegacyBidSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_sources(self) -> "LegacyBidSourceManifest":
        ids = [item.legacy_bid_id for item in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("LegacyBidSourceManifest 不允许重复 legacy_bid_id")
        return self


class LegacyBidSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(min_length=1)
    parent_section_id: str | None = None
    level: int = Field(ge=1, le=9)
    order: int = Field(ge=0)
    title: str = Field(min_length=1)
    heading_block_id: str = Field(min_length=1)
    content_block_ids: list[str] = Field(default_factory=list)
    start_ordinal: int = Field(ge=0)
    end_ordinal: int = Field(ge=0)
    needs_review: bool = False


class LegacyBidIndex(ContractModel):
    legacy_bid_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    file_hash: str = Field(min_length=1)
    parser_version: str = Field(default=SOURCE_PARSER_VERSION, min_length=1)
    source_manifest_revision: int = Field(ge=1)
    source_manifest_artifact_hash: str = Field(min_length=1)
    sections: list[LegacyBidSection] = Field(default_factory=list)
    blocks: list[SourceBlock] = Field(default_factory=list)
    structure_gaps: list[SourceNormalizationCoverageItem] = Field(default_factory=list)
    needs_review: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identities_are_unique(self) -> "LegacyBidIndex":
        block_ids = [item.block_id for item in self.blocks]
        section_ids = [item.section_id for item in self.sections]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("LegacyBidIndex 不允许重复 block_id")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("LegacyBidIndex 不允许重复 section_id")
        known_blocks = set(block_ids)
        for section in self.sections:
            if section.heading_block_id not in known_blocks:
                raise ValueError("LegacyBidSection heading_block_id 不存在")
            if set(section.content_block_ids) - known_blocks:
                raise ValueError("LegacyBidSection 引用了未知 block_id")
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


class ScoreCondition(BaseModel):
    """One source-bound semantic condition required by the highest scoring band."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    condition_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    normalized_condition: str = Field(min_length=1)
    condition_role: Literal[
        "content",
        "evidence",
        "constraint",
        "quality",
        "document",
    ] = "content"
    source_excerpt: str = Field(min_length=1)
    source_level_id: str | None = None
    subject: str = Field(min_length=1)
    response_intent: str = Field(min_length=1)
    source_anchor: SourceAnchor | None = None
    source_span_start: int | None = Field(default=None, ge=0)
    source_span_end: int | None = Field(default=None, gt=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="before")
    @classmethod
    def legacy_condition_defaults(cls, value: Any) -> Any:
        """Keep historical ScoreModel artifacts readable after semantic enrichment."""

        if not isinstance(value, dict):
            return value
        hydrated = dict(value)
        if not str(hydrated.get("normalized_condition") or "").strip():
            hydrated["normalized_condition"] = hydrated.get("text")
        hydrated.setdefault("condition_role", "content")
        return hydrated

    @model_validator(mode="after")
    def source_span_is_complete(self) -> "ScoreCondition":
        if (self.source_span_start is None) != (self.source_span_end is None):
            raise ValueError("ScoreCondition source span 必须同时提供起止位置")
        if (
            self.source_span_start is not None
            and self.source_span_end is not None
            and self.source_span_end <= self.source_span_start
        ):
            raise ValueError("ScoreCondition source_span_end 必须大于 source_span_start")
        return self


class ScoreResponseUnit(BaseModel):
    """One semantically independent response task inside a physical scoring rule."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unit_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outline_path: list[str] = Field(default_factory=list)
    source_level_ids: list[str] = Field(default_factory=list)
    condition_ids: list[str] = Field(default_factory=list)
    condition_join: Literal[
        "all",
        "any",
        "ordered",
        "threshold",
        "mixed",
    ] = "all"
    linked_requirement_ids: list[str] = Field(default_factory=list)
    response_scope: Literal["section", "document"] = "section"
    response_expectation: str = Field(min_length=1)
    required_evidence_types: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def references_are_unique(self) -> "ScoreResponseUnit":
        if len(self.source_level_ids) != len(set(self.source_level_ids)):
            raise ValueError("ScoreResponseUnit 不允许重复 source_level_ids")
        if len(self.condition_ids) != len(set(self.condition_ids)):
            raise ValueError("ScoreResponseUnit 不允许重复 condition_ids")
        if len(self.linked_requirement_ids) != len(set(self.linked_requirement_ids)):
            raise ValueError("ScoreResponseUnit 不允许重复 linked_requirement_ids")
        return self


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
    response_scope: Literal["section", "document"] = "section"
    outline_path: list[str] = Field(default_factory=list)
    full_score_conditions: list[str] = Field(default_factory=list)
    score_conditions: list[ScoreCondition] = Field(default_factory=list)
    response_units: list[ScoreResponseUnit] = Field(default_factory=list)
    response_expectation: str = Field(min_length=1)
    response_depth: Literal["basic", "substantive", "detailed"] = "basic"
    required_evidence_types: list[str] = Field(default_factory=list)
    linked_requirement_ids: list[str] = Field(default_factory=list)
    context_requirement_ids: list[str] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def score_point_references_are_unique(self) -> "ScorePoint":
        if len(self.linked_requirement_ids) != len(set(self.linked_requirement_ids)):
            raise ValueError("ScorePoint 不允许重复 linked_requirement_ids")
        if len(self.context_requirement_ids) != len(
            set(self.context_requirement_ids)
        ):
            raise ValueError("ScorePoint 不允许重复 context_requirement_ids")
        anchor_ids = [(anchor.source_input_id, anchor.chunk_id) for anchor in self.source_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("ScorePoint 不允许重复 source_anchors")
        condition_ids = [condition.condition_id for condition in self.score_conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("ScorePoint 不允许重复 condition_id")
        if self.score_conditions:
            condition_texts = [condition.text for condition in self.score_conditions]
            if self.full_score_conditions and self.full_score_conditions != condition_texts:
                raise ValueError("full_score_conditions 必须是 score_conditions.text 的兼容投影")
            self.full_score_conditions = condition_texts
        elif self.full_score_conditions:
            anchor = self.source_anchors[0] if self.source_anchors else None
            self.score_conditions = [
                ScoreCondition(
                    condition_id=f"{self.score_point_id}-C{index:02d}",
                    text=text,
                    source_excerpt=text,
                    subject=text,
                    response_intent="完整响应该满分条件",
                    source_anchor=anchor,
                )
                for index, text in enumerate(self.full_score_conditions, start=1)
            ]
        known_condition_ids = {condition.condition_id for condition in self.score_conditions}
        known_level_ids = {
            f"{self.score_point_id}-L{index:02d}"
            for index, _ in enumerate(self.scoring_levels, start=1)
        }
        conditions_by_id = {
            condition.condition_id: condition for condition in self.score_conditions
        }
        unit_ids = [unit.unit_id for unit in self.response_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("ScorePoint 不允许重复 response unit ID")
        assigned_level_ids: list[str] = []
        for unit in self.response_units:
            unknown = set(unit.condition_ids) - known_condition_ids
            if unknown:
                raise ValueError(
                    f"ScoreResponseUnit {unit.unit_id} 引用未知 condition_id: {sorted(unknown)}"
                )
            unknown_levels = set(unit.source_level_ids) - known_level_ids
            if unknown_levels:
                raise ValueError(
                    f"ScoreResponseUnit {unit.unit_id} 引用未知 source_level_id: "
                    f"{sorted(unknown_levels)}"
                )
            unknown_requirements = (
                set(unit.linked_requirement_ids)
                - {
                    *self.linked_requirement_ids,
                    *self.context_requirement_ids,
                }
            )
            if unknown_requirements:
                raise ValueError(
                    f"ScoreResponseUnit {unit.unit_id} 引用评分点未关联的 requirement_id: "
                    f"{sorted(unknown_requirements)}"
                )
            assigned_level_ids.extend(unit.source_level_ids)
            for condition_id in unit.condition_ids:
                condition_level_id = conditions_by_id[condition_id].source_level_id
                if (
                    condition_level_id is not None
                    and condition_level_id not in unit.source_level_ids
                ):
                    raise ValueError(
                        f"ScoreResponseUnit {unit.unit_id} 未绑定条件 "
                        f"{condition_id} 的 source_level_id"
                    )
        if len(assigned_level_ids) != len(set(assigned_level_ids)):
            raise ValueError("ScorePoint 的评分档次不能被多个 ScoreResponseUnit 重复绑定")
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
    upstream_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_unique(self) -> "ProjectFact":
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("ProjectFact 不允许重复 requirement_ids")
        if len(self.upstream_refs) != len(set(self.upstream_refs)):
            raise ValueError("ProjectFact 不允许重复 upstream_refs")
        return self

    @model_serializer(mode="wrap")
    def _serialize_model(self, handler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("upstream_refs"):
            data.pop("upstream_refs", None)
        return data


class EvidenceRelevanceTier(str, Enum):
    """How a public source may be used for the current procurement."""

    PROJECT_DIRECT = "project_direct"
    SIMILAR_PROJECT = "similar_project"
    INDUSTRY_STANDARD = "industry_standard"
    GENERAL_REFERENCE = "general_reference"


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
    project_anchors: list[str] = Field(default_factory=list)
    task_anchors: list[str] = Field(default_factory=list)
    # Immutable chapter-scoped context for semantic review of public sources.
    # Kept optional so evidence batches created before this policy remain readable.
    relevance_context: dict[str, Any] = Field(default_factory=dict)
    allowed_relevance_tiers: list[EvidenceRelevanceTier] = Field(
        default_factory=lambda: [
            EvidenceRelevanceTier.PROJECT_DIRECT,
            EvidenceRelevanceTier.SIMILAR_PROJECT,
            EvidenceRelevanceTier.INDUSTRY_STANDARD,
        ]
    )
    max_adopted_items: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def research_anchors_are_unique(self) -> "EvidenceNeed":
        if len(self.project_anchors) != len(set(self.project_anchors)):
            raise ValueError("EvidenceNeed 不允许重复 project_anchors")
        if len(self.task_anchors) != len(set(self.task_anchors)):
            raise ValueError("EvidenceNeed 不允许重复 task_anchors")
        if len(self.allowed_relevance_tiers) != len(
            set(self.allowed_relevance_tiers)
        ):
            raise ValueError("EvidenceNeed 不允许重复 allowed_relevance_tiers")
        return self


class ResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    target_node_ids: list[str] = Field(min_length=1)
    applicability: str = Field(min_length=1)
    status: Literal[
        "planned",
        "researching",
        "published",
        "blocked_human",
        "skipped",
    ] = "planned"
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    batch_id: str = ""
    evidence_count: int = Field(default=0, ge=0)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""

    @model_validator(mode="after")
    def target_ids_are_unique(self) -> "ResearchQuery":
        if len(self.target_node_ids) != len(set(self.target_node_ids)):
            raise ValueError("ResearchQuery 不允许重复 target_node_ids")
        return self


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    applicable_chapter_ids: list[str] = Field(default_factory=list)
    applicable_chapter_titles: list[str] = Field(default_factory=list)
    needs_research: bool
    # True only when the planner has explicitly established that the public
    # search is optional and the already-authorized project material can still
    # satisfy every WritingPlan block.  Presence of unrelated project facts is
    # never enough to infer this after a failed search.
    fallback_to_existing_materials: bool = False
    reason: str = Field(min_length=1)
    queries: list[ResearchQuery] = Field(default_factory=list, max_length=3)
    prohibited_research_scopes: list[str] = Field(default_factory=list)
    decision_status: Literal[
        "planned",
        "skipped",
        "researching",
        "published",
        "blocked_human",
    ]
    runtime: dict[str, Any] = Field(default_factory=dict)
    used_evidence_by_chapter: dict[str, list[str]] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def decision_is_consistent(self) -> "ResearchDecision":
        if self.needs_research and not self.queries:
            raise ValueError("需要检索的 ResearchDecision 至少包含一个查询")
        if not self.needs_research and self.queries:
            raise ValueError("不需要检索的 ResearchDecision 不应包含查询")
        if len(self.applicable_chapter_ids) != len(
            set(self.applicable_chapter_ids)
        ):
            raise ValueError("ResearchDecision 不允许重复适用章节")
        return self


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
    relevance_tier: EvidenceRelevanceTier = (
        EvidenceRelevanceTier.GENERAL_REFERENCE
    )
    matched_project_anchors: list[str] = Field(default_factory=list)
    matched_task_anchors: list[str] = Field(default_factory=list)
    supporting_excerpt: str = ""
    usage_constraints: list[str] = Field(default_factory=list)
    # Semantic-review output. `content` remains the source record; the writer
    # consumes the following focused fields instead of the complete web page.
    extracted_points: list[str] = Field(default_factory=list)
    relevance_reason: str = ""
    relevance_confidence: float = Field(default=0.0, ge=0, le=1)
    usage_category: str = ""

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
    # Non-authoritative summary of the append-only Deep Research audit run.
    # Optional so batches created before the policy remain readable.
    research_run: dict[str, Any] = Field(default_factory=dict)


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
    semantic_upstream_refs: list[str] = Field(default_factory=list)
    evidence_needs: list[EvidenceNeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_project_references(self) -> "ProjectModel":
        fact_ids = [
            fact.fact_id
            for group in (self.confirmed_facts, self.inferences, self.conflicts)
            for fact in group
        ]
        need_ids = [need.need_id for need in self.evidence_needs]
        if len(fact_ids) != len(set(fact_ids)) or len(need_ids) != len(set(need_ids)):
            raise ValueError("ProjectModel 不允许重复事实或 EvidenceNeed ID")
        if len(self.semantic_upstream_refs) != len(set(self.semantic_upstream_refs)):
            raise ValueError("ProjectModel 不允许重复 semantic_upstream_refs")
        return self


class GlobalProjectContext(BaseModel):
    """One immutable project fact view inherited by every chapter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    global_context_id: str = Field(min_length=1)
    global_context_revision: int = Field(ge=1)
    global_context_hash: str = Field(min_length=1)
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
    unknowns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_shared_project_identity(self) -> "GlobalProjectContext":
        identity = {str(key).casefold(): str(value) for key, value in self.identity.items()}
        project_name = next(
            (
                identity[key]
                for key in ("project_name", "项目名称", "project", "项目")
                if identity.get(key)
            ),
            "",
        )
        if not project_name:
            raise ValueError("全局项目上下文必须包含项目名称")
        fact_ids = [fact.fact_id for fact in self.confirmed_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("全局项目上下文不允许重复 fact_id")
        return self


class ChapterGroundingContext(BaseModel):
    """Chapter-local additions referencing, never copying, global facts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_id: str = Field(min_length=1)
    global_context_id: str = Field(min_length=1)
    global_context_revision: int = Field(ge=1)
    global_context_hash: str = Field(min_length=1)
    chapter_context_id: str = Field(min_length=1)
    chapter_context_revision: int = Field(default=0, ge=0)
    chapter_context_hash: str = Field(min_length=1)
    requirement_excerpts: list[dict[str, Any]] = Field(default_factory=list)
    score_obligations: list[dict[str, Any]] = Field(default_factory=list)
    chapter_context_items: list[dict[str, Any]] = Field(default_factory=list)
    highlighted_fact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def highlighted_facts_are_unique(self) -> "ChapterGroundingContext":
        if len(self.highlighted_fact_ids) != len(set(self.highlighted_fact_ids)):
            raise ValueError("章节上下文不允许重复 highlighted_fact_ids")
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
    score_response_unit_ids: list[str] = Field(default_factory=list)
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


class BlueprintLegacySource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    section_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class BlueprintRewriteBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    response_unit_ids: list[str] = Field(default_factory=list)
    condition_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class BlueprintNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_id: str = Field(min_length=1)
    parent_chapter_id: str | None = None
    order: int = Field(ge=0)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    primary_response_unit_ids: list[str] = Field(default_factory=list)
    supporting_response_unit_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    score_condition_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    forbidden_topic_ids: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    cross_references: list[str] = Field(default_factory=list)
    planned_tables: list[str] = Field(default_factory=list)
    planned_figures: list[str] = Field(default_factory=list)
    target_size: int = Field(default=800, ge=1)
    section_domain: Literal["technical", "price", "commercial"] = "technical"
    content_policy: Literal["full", "structural_only", "deferred_title_only"] = "full"
    deferred_reason: str | None = None
    template_node_id: str | None = None
    template_level: int | None = Field(default=None, ge=1, le=9)
    template_numbering: str | None = None
    template_slot_ids: list[str] = Field(default_factory=list)
    template_target: str | None = None
    structure_origin: Literal["tender_initial", "legacy_enriched"] = "tender_initial"
    rewrite_mode: Literal["copy", "light_edit", "restructure", "new_write"] | None = None
    legacy_section_ids: list[str] = Field(default_factory=list)
    legacy_sources: list[BlueprintLegacySource] = Field(default_factory=list)
    rewrite_basis: BlueprintRewriteBasis = Field(default_factory=BlueprintRewriteBasis)
    rewrite_reason: str = ""
    required_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def direct_bindings_are_unique(self) -> "BlueprintNode":
        binding_groups = {
            "primary_response_unit_ids": self.primary_response_unit_ids,
            "supporting_response_unit_ids": self.supporting_response_unit_ids,
            "score_point_ids": self.score_point_ids,
            "score_condition_ids": self.score_condition_ids,
            "requirement_ids": self.requirement_ids,
        }
        for field_name, values in binding_groups.items():
            if len(values) != len(set(values)):
                raise ValueError(f"BlueprintNode 不允许重复 {field_name}")
        overlap = set(self.primary_response_unit_ids) & set(
            self.supporting_response_unit_ids
        )
        if overlap:
            raise ValueError(
                "同一章节的 response unit 不能同时是 primary 与 supporting: "
                f"{sorted(overlap)}"
            )
        return self


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


class DocumentQualityGate(BaseModel):
    """A scored whole-document criterion that must not become a visible chapter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gate_id: str = Field(min_length=1)
    duty_id: str | None = Field(default=None, min_length=1)
    response_unit_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(min_length=1)
    score_condition_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(min_length=1)
    check_items: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def quality_bindings_are_unique(self) -> "DocumentQualityGate":
        if not self.duty_id and not self.response_unit_ids:
            raise ValueError(
                "DocumentQualityGate 必须绑定 legacy duty_id 或 response_unit_ids"
            )
        if len(self.response_unit_ids) != len(set(self.response_unit_ids)):
            raise ValueError(
                "DocumentQualityGate 不允许重复 response_unit_id"
            )
        if len(self.score_point_ids) != len(set(self.score_point_ids)):
            raise ValueError(
                "DocumentQualityGate 不允许重复 score_point_id"
            )
        if len(self.score_condition_ids) != len(
            set(self.score_condition_ids)
        ):
            raise ValueError(
                "DocumentQualityGate 不允许重复 score_condition_id"
            )
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError(
                "DocumentQualityGate 不允许重复 requirement_id"
            )
        return self


class ChapterBlueprint(ContractModel):
    blueprint_id: str = Field(min_length=1)
    mode: DocumentMode
    planning_model: Literal["topic_graph", "score_direct", "rewrite_merge"] = "topic_graph"
    requirement_ledger_revision: int | None = Field(default=None, ge=1)
    score_model_revision: int | None = Field(default=None, ge=1)
    topic_graph_revision: int | None = Field(default=None, ge=1)
    template_structure_revision: int | None = Field(default=None, ge=1)
    nodes: list[BlueprintNode] = Field(min_length=1)
    assignments: list[TopicChapterAssignment] = Field(default_factory=list)
    document_quality_gates: list[DocumentQualityGate] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    review_status: Literal["draft", "confirmed", "blocked"] = "draft"

    @model_validator(mode="after")
    def primary_duties_are_complete(self) -> "ChapterBlueprint":
        if self.planning_model == "topic_graph" and self.topic_graph_revision is None:
            raise ValueError(
                "topic_graph 规划模型必须声明 topic_graph_revision"
            )
        if self.planning_model in {"score_direct", "rewrite_merge"} and (
            self.requirement_ledger_revision is None
            or self.score_model_revision is None
        ):
            raise ValueError(
                "score_direct 规划模型必须声明 requirement_ledger_revision "
                "与 score_model_revision"
            )
        if (self.requirement_ledger_revision is None) != (
            self.score_model_revision is None
        ):
            raise ValueError(
                "requirement_ledger_revision 与 score_model_revision 必须同时声明"
            )
        node_ids = [node.chapter_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ChapterBlueprint 不允许重复 chapter_id")
        if unknown := {assignment.chapter_id for assignment in self.assignments} - set(node_ids):
            raise ValueError(f"TopicChapterAssignment 指向未知章节: {sorted(unknown)}")
        primaries = [assignment.duty_id for assignment in self.assignments if assignment.role == "primary"]
        if len(primaries) != len(set(primaries)):
            raise ValueError("每个 Duty 只能有一个 primary chapter")
        gate_ids = [gate.gate_id for gate in self.document_quality_gates]
        gate_duty_ids = [
            gate.duty_id
            for gate in self.document_quality_gates
            if gate.duty_id is not None
        ]
        if len(gate_ids) != len(set(gate_ids)) or len(gate_duty_ids) != len(
            set(gate_duty_ids)
        ):
            raise ValueError("ChapterBlueprint 不允许重复全文质量门或重复质量门 Duty")
        primary_response_unit_ids = [
            response_unit_id
            for node in self.nodes
            for response_unit_id in node.primary_response_unit_ids
        ]
        if len(primary_response_unit_ids) != len(set(primary_response_unit_ids)):
            raise ValueError("每个 response unit 只能有一个 primary chapter")
        gate_response_unit_ids = [
            response_unit_id
            for gate in self.document_quality_gates
            for response_unit_id in gate.response_unit_ids
        ]
        if len(gate_response_unit_ids) != len(set(gate_response_unit_ids)):
            raise ValueError("每个全文级 response unit 只能绑定一个质量门")
        overlap = set(primary_response_unit_ids) & set(gate_response_unit_ids)
        if overlap:
            raise ValueError(
                "response unit 不能同时绑定章节与全文质量门: "
                f"{sorted(overlap)}"
            )
        if self.planning_model == "rewrite_merge":
            parent_ids = {
                node.parent_chapter_id
                for node in self.nodes
                if node.parent_chapter_id is not None
            }
            for node in self.nodes:
                is_leaf = node.chapter_id not in parent_ids
                if is_leaf != (node.rewrite_mode is not None):
                    raise ValueError("rewrite_merge 中父章节 rewrite_mode 必须为空，叶子章节必须声明唯一 rewrite_mode")
                sources = node.legacy_sources
                if node.rewrite_mode in {"copy", "light_edit", "restructure"} and not sources:
                    raise ValueError(f"{node.rewrite_mode} 叶子章节必须声明 legacy_sources")
                if node.rewrite_mode == "copy" and node.required_changes:
                    raise ValueError("copy 叶子章节 required_changes 必须为空")
                if node.rewrite_mode in {"light_edit", "restructure"} and not node.required_changes:
                    raise ValueError(f"{node.rewrite_mode} 叶子章节必须声明 required_changes")
                if node.rewrite_mode == "new_write" and sources:
                    raise ValueError("new_write 叶子章节不得声明 legacy_sources")
        return self


class ChapterWorkspaceRecord(BaseModel):
    """Logical chapter workspace inside a bid project Workspace.

    Phase 1 control-plane aggregate. Not a promoted canonical Artifact and not a
    separate project/control.db. Bound to one ChapterBlueprint ``chapter_id``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_id: str = Field(min_length=1)
    blueprint_revision: int = Field(ge=1)
    blueprint_hash: str = Field(min_length=1)
    title: str = Field(min_length=1)
    parent_chapter_id: str | None = None
    order: int = Field(ge=0)
    status: Literal["active", "archived"] = "active"
    approval_status: Literal[
        "not_started",
        "draft",
        "pending_approval",
        "approved",
    ] = "not_started"
    # Independent CAS counter for chapter-scoped mutations.
    chapter_revision: int = Field(default=0, ge=0)
    head_content_revision: int = Field(default=0, ge=0)
    formal_content_revision: int = Field(default=0, ge=0)
    head_context_revision: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    state_hash: str = Field(default="")
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def chapter_id_is_safe(self) -> "ChapterWorkspaceRecord":
        value = self.chapter_id
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or value != value.strip()
        ):
            raise ValueError("chapter_id 非法")
        return self


ChapterContextItemKind = Literal[
    "GOAL",
    "SCORING_REQUIREMENT",
    "TECHNICAL_CONSTRAINT",
    "KEY_FACT",
]


class ChapterContextItem(BaseModel):
    """Stable chapter-local context item (Phase 2).

    Overlay on shared global artifacts (RequirementLedger / ScoreModel / …).
    Does not replace or delete upstream facts.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_id: str = Field(min_length=1)
    kind: ChapterContextItemKind
    title: str = Field(min_length=1)
    body: str = Field(default="")
    order: int = Field(ge=0)
    source: Literal["BLUEPRINT_SEED", "USER"] = "USER"
    origin_ref: str | None = None

    @model_validator(mode="after")
    def item_id_is_safe(self) -> "ChapterContextItem":
        value = self.item_id
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or value != value.strip()
        ):
            raise ValueError("item_id 非法")
        return self


class ChapterContextRevisionRecord(BaseModel):
    """Append-only chapter context revision metadata + ordered items."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_id: str = Field(min_length=1)
    context_revision: int = Field(ge=1)
    parent_context_revision: int | None = Field(default=None, ge=1)
    items: list[ChapterContextItem] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)
    seeded_from_blueprint: bool = False
    actor: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def items_are_consistent(self) -> "ChapterContextRevisionRecord":
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("ChapterContextRevision 不允许重复 item_id")
        orders = [item.order for item in self.items]
        if len(orders) != len(set(orders)):
            raise ValueError("ChapterContextRevision 不允许重复 order")
        if (
            self.parent_context_revision is not None
            and self.parent_context_revision >= self.context_revision
        ):
            raise ValueError("parent_context_revision 必须小于 context_revision")
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
    section_domain: Literal["technical", "price", "commercial"] = "technical"
    content_policy: Literal["full", "structural_only", "deferred_title_only"] = "full"
    deferred_reason: str | None = None


class TemplateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slot_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    kind: Literal["text_slot", "cell_slot", "flow_slot", "repeat_slot"]
    anchor: str = Field(min_length=1)


class _DocumentContractBase(ContractModel):
    # PR-23: contracts are read-only derivatives of one confirmed Blueprint.
    source_blueprint_artifact_id: str | None = None
    source_blueprint_revision: int | None = Field(default=None, ge=1)
    source_blueprint_hash: str | None = None
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
    section_domain: Literal["technical", "price", "commercial"] = "technical"
    content_policy: Literal["full", "structural_only", "deferred_title_only"] = "full"
    deferred_reason: str | None = None


class DocumentPlan(ContractModel):
    contract_revision: int = Field(ge=1)
    source_blueprint_artifact_id: str | None = None
    source_blueprint_revision: int | None = Field(default=None, ge=1)
    source_blueprint_hash: str | None = None
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


class WriterInputBundle(ContractModel):
    """Frozen, least-privilege input for exactly one semantic content unit."""

    bundle_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    source_blueprint_artifact_id: str = Field(min_length=1)
    source_blueprint_revision: int = Field(ge=1)
    source_blueprint_hash: str = Field(min_length=1)
    h1_receipt_id: str = Field(min_length=1)
    dependency_refs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    blueprint_slice: list[dict[str, Any]] = Field(min_length=1)
    topic_and_duty_slice: list[dict[str, Any]] = Field(default_factory=list)
    requirement_excerpts: list[dict[str, Any]] = Field(default_factory=list)
    score_obligations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    research_decisions: list[dict[str, Any]] = Field(default_factory=list)
    project_context: dict[str, Any] = Field(default_factory=dict)
    global_project_context: dict[str, Any] = Field(default_factory=dict)
    chapter_grounding_context: dict[str, Any] = Field(default_factory=dict)
    chapter_grounding_contexts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    project_constraints: list[str] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    document_target_constraints: list[dict[str, Any]] = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_config_hash: str = Field(min_length=1)
    bundle_hash: str = Field(min_length=1)
    # Phase 7: chapter workspace overlays (optional; empty when not materialised).
    chapter_id: str | None = None
    chapter_context_revision: int = Field(default=0, ge=0)
    chapter_context_items: list[dict[str, Any]] = Field(default_factory=list)
    head_content_revision: int = Field(default=0, ge=0)
    locked_blocks: list[dict[str, Any]] = Field(default_factory=list)
    content_history_summary: list[dict[str, Any]] = Field(default_factory=list)
    # Frozen request intent.  These values are supplied by the writing
    # service; the model kernel must not recover them from transport/chat.
    operation: Literal["create", "rewrite", "repair"] = "create"
    user_instruction: str = ""
    existing_content: str = ""
    # Chapter Agent memory carried into its internal writing tool.  Assistant
    # turns remain non-authoritative in the writing kernel; user turns preserve
    # the chapter-local feedback that led to this write/rewrite action.
    chapter_dialogue: list[dict[str, Any]] = Field(default_factory=list)
    chapter_writing_plan: dict[str, Any] = Field(default_factory=dict)
    overwrite_locked: bool = False


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    type: Literal["paragraph", "list", "table", "figure_ref", "cross_reference"]
    content: str = Field(min_length=1)
    topic_ids: list[str] = Field(default_factory=list)
    duty_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    human_locked: bool = False
    critical_claims: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_bundle_hash: str | None = None
    # Phase 3+ chapter editor fields. Missing legacy fields map to AI_GENERATED.
    source: Literal["AI_GENERATED", "USER_CREATED", "USER_EDITED"] = "AI_GENERATED"
    created_by: str = ""
    updated_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    order: int = Field(default=0, ge=0)
    lock_state: Literal["UNLOCKED", "USER_LOCKED"] = "UNLOCKED"

    @model_validator(mode="after")
    def critical_claims_need_sources(self) -> "ContentBlock":
        if self.critical_claims and not (self.evidence_ids or self.fact_ids):
            raise ValueError("关键 Claim 必须关联 evidence_ids 或 fact_ids")
        # Keep human_locked and lock_state aligned for integrators/renderers.
        updates: dict[str, Any] = {}
        if self.lock_state == "USER_LOCKED" and not self.human_locked:
            updates["human_locked"] = True
        elif self.human_locked and self.lock_state == "UNLOCKED":
            updates["lock_state"] = "USER_LOCKED"
        return self.model_copy(update=updates) if updates else self


class ChapterContentRevisionRecord(BaseModel):
    """Append-only ordered ContentBlock[] revision for one chapter workspace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_id: str = Field(min_length=1)
    content_revision: int = Field(ge=1)
    parent_content_revision: int | None = Field(default=None, ge=1)
    blocks: list[ContentBlock] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)
    source: Literal[
        "user_edit",
        "ai_draft",
        "restore",
        "merge",
        "auto_approve",
    ]
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def blocks_are_consistent(self) -> "ChapterContentRevisionRecord":
        ids = [block.block_id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("ChapterContentRevision 不允许重复 block_id")
        return self


class ChapterApprovalReceiptRecord(BaseModel):
    """H2 chapter body approval (or explicit auto-approval) for one content revision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    content_revision: int = Field(ge=1)
    content_hash: str = Field(min_length=1)
    decision: Literal["approved", "auto_approved"]
    principal_id: str = Field(min_length=1)
    confirmation_required: bool
    receipt_hash: str = Field(min_length=1)
    actor: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)
    gate_id: Literal["H2_CHAPTER_APPROVAL"] = "H2_CHAPTER_APPROVAL"


class ContentProposal(BaseModel):
    """Writer output before G4 admits ContentBlocks into a content-unit artifact."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    bundle_hash: str = Field(min_length=1)
    blocks: list[ContentBlock] = Field(min_length=1)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence_need_proposals: list[dict[str, Any]] = Field(default_factory=list)
    plan_issue_proposals: list[dict[str, Any]] = Field(default_factory=list)


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
