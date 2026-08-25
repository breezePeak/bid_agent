"""Controlled LLM inference surfaces for the V3 planning pipeline.

The providers in this module only produce strictly validated candidates.  They
do not publish artifacts, allocate canonical IDs, execute tools, or write to
the workspace.  Callers remain responsible for domain validation, inference
receipts, proposal promotion, and human approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Generic, Literal, Mapping, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .canonicalization import canonical_hash
from .scoring_outline_policy import (
    is_sectionable_quality_condition,
    outline_structure_key,
)

PROJECT_PROMPT_FILE = "v3_planning_agent_project.md"
TOPIC_PROMPT_FILE = "v3_planning_agent_topics.md"
OUTLINE_PROMPT_FILE = "v3_planning_agent_blueprint.md"

PROJECT_PROMPT_VERSION = "v3_planning_project_understanding_v2.0"
TOPIC_PROMPT_VERSION = "v3_planning_topic_duty_v1.1"
OUTLINE_PROMPT_VERSION = "v3_planning_chapter_outline_split_v4.0"

PROJECT_CAPABILITY_VERSION = "1.9.0"
TOPIC_CAPABILITY_VERSION = "1.1.0"
OUTLINE_CAPABILITY_VERSION = "4.0.0"

PROJECT_SCHEMA_VERSION = "v3.project_understanding_candidate.v6"
TOPIC_SCHEMA_VERSION = "v3.topic_duty_candidate.v2"
OUTLINE_SCHEMA_VERSION = "v3.chapter_outline_candidate.v2"

OUTLINE_SKILL_ID = "planning.chapter_outline_split"
DEFAULT_TEMPERATURE = 0.1
MAX_REPAIR_ATTEMPTS = 1
OUTLINE_BATCH_MAX_ITEMS = 8
OUTLINE_BATCH_MAX_INPUT_CHARS = 12_000
PROJECT_INPUT_TARGET_CHARS = max(
    1,
    int(os.getenv("PROJECT_UNDERSTANDING_TARGET_CHARS", "16000")),
)
PROJECT_INPUT_MAX_CHARS = max(
    PROJECT_INPUT_TARGET_CHARS,
    int(os.getenv("PROJECT_UNDERSTANDING_MAX_CHARS", "32000")),
)
PROJECT_INPUT_PROJECTION_VERSION = "v3.project_input.v4"

_PROJECT_CITED_LIST_FIELDS = (
    "background",
    "goals",
    "scope",
    "boundaries",
    "work_packages",
    "dependencies",
    "inputs",
    "processing",
    "outputs",
    "deliverables",
    "acceptance_conditions",
    "milestones",
    "roles",
    "risks",
    "constraints",
)

# A score batch describes bid-response implications, not the underlying
# project's identity or implementation scope.  Keeping these projections out
# of score batches prevents "similar performance scoring" and "qualification
# scoring" from being compiled into ProjectModel.scope as confirmed facts.
_SCORE_BATCH_PROJECT_CORE_FIELDS = (
    "project_name",
    "identity",
    "background",
    "scope",
    "boundaries",
    "milestones",
)


class PlanningInferenceError(RuntimeError):
    """Base error for fail-closed controlled planning inference."""


class PlanningInferenceCallError(PlanningInferenceError):
    """The configured model provider could not complete an invocation."""


class PlanningInferenceValidationError(PlanningInferenceError):
    """The model output remained invalid after the single controlled repair."""


class PlanningInferenceOutputTruncatedError(ValueError):
    """The provider explicitly stopped because its output budget was exhausted."""


class StrictPlanningModel(BaseModel):
    """Strict JSON boundary shared by all inference inputs and candidates."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )


class ProjectUnderstandingInput(StrictPlanningModel):
    """Frozen promoted artifacts and selected source context for understanding."""

    requirement_ledger: dict[str, Any]
    source_context: list[dict[str, Any]] = Field(default_factory=list)
    scanned_source_block_count: int = Field(default=0, ge=0)
    review_feedback: str = ""
    batch_id: str = "project-single"
    batch_index: int = Field(default=1, ge=1)
    batch_count: int = Field(default=1, ge=1)


class TopicDutyPlanningInput(StrictPlanningModel):
    """Promoted understanding plus the exact upstream planning dependencies."""

    project_model: dict[str, Any]
    requirement_ledger: dict[str, Any]
    score_model: dict[str, Any]
    source_context: list[dict[str, Any]] = Field(default_factory=list)


class OutlineDecompositionInput(StrictPlanningModel):
    """Exact inputs consumed by the internal chapter-decomposition skill."""

    requirement_ledger: dict[str, Any]
    score_model: dict[str, Any]
    template_structure: dict[str, Any] | None = None
    document_mode: Literal["auto_outline", "template_strict"] = "auto_outline"
    review_feedback: str = ""


class CitedStatementCandidate(StrictPlanningModel):
    """A semantic statement tied to one or more frozen upstream references."""

    text: str = Field(min_length=1)
    upstream_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class IdentityFieldCandidate(StrictPlanningModel):
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    upstream_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TerminologyCandidate(StrictPlanningModel):
    term: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    upstream_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ProjectFactCandidate(StrictPlanningModel):
    local_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    classification: Literal["confirmed", "inference", "conflict"]
    upstream_refs: list[str] = Field(min_length=1)
    requirement_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ProjectEvidenceNeedCandidate(StrictPlanningModel):
    """A source-grounded evidence gap discovered during project understanding."""

    local_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    priority: Literal["blocking", "high", "normal", "low"] = "normal"
    blocking_scope: Literal[
        "workspace",
        "contract",
        "content_unit",
        "none",
    ] = "none"
    deadline_stage: str = Field(min_length=1)
    query_budget: int = Field(default=0, ge=0, le=20)
    upstream_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal[
        "confirmed",
        "needs_review",
        "blocked",
    ] = "confirmed"


class ProjectUnderstandingCandidate(StrictPlanningModel):
    """Model-authored, source-grounded semantic understanding of the project."""

    project_name: CitedStatementCandidate | None = None
    identity: list[IdentityFieldCandidate] = Field(default_factory=list)
    background: list[CitedStatementCandidate] = Field(default_factory=list)
    goals: list[CitedStatementCandidate] = Field(default_factory=list)
    scope: list[CitedStatementCandidate] = Field(default_factory=list)
    boundaries: list[CitedStatementCandidate] = Field(default_factory=list)
    work_packages: list[CitedStatementCandidate] = Field(default_factory=list)
    dependencies: list[CitedStatementCandidate] = Field(default_factory=list)
    inputs: list[CitedStatementCandidate] = Field(default_factory=list)
    processing: list[CitedStatementCandidate] = Field(default_factory=list)
    outputs: list[CitedStatementCandidate] = Field(default_factory=list)
    deliverables: list[CitedStatementCandidate] = Field(default_factory=list)
    acceptance_conditions: list[CitedStatementCandidate] = Field(default_factory=list)
    milestones: list[CitedStatementCandidate] = Field(default_factory=list)
    roles: list[CitedStatementCandidate] = Field(default_factory=list)
    risks: list[CitedStatementCandidate] = Field(default_factory=list)
    constraints: list[CitedStatementCandidate] = Field(default_factory=list)
    terminology: list[TerminologyCandidate] = Field(default_factory=list)
    facts: list[ProjectFactCandidate] = Field(default_factory=list)
    evidence_needs: list[ProjectEvidenceNeedCandidate] = Field(
        default_factory=list
    )
    unknowns: list[str] = Field(default_factory=list)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def local_fact_ids_are_unique(self) -> "ProjectUnderstandingCandidate":
        fact_ids = [fact.local_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("ProjectUnderstandingCandidate 不允许重复 local fact ID")
        need_ids = [need.local_id for need in self.evidence_needs]
        if len(need_ids) != len(set(need_ids)):
            raise ValueError(
                "ProjectUnderstandingCandidate 不允许重复 local evidence need ID"
            )
        return self


TopicTypeCandidate = Literal[
    "business_domain",
    "business_flow",
    "business_capability",
    "function",
    "architecture",
    "data",
    "integration",
    "security",
    "non_functional",
    "implementation",
    "project_management",
    "service_operation",
    "training",
    "deliverable",
    "acceptance",
    "qualification",
    "commercial",
    "compliance",
]
DutyTypeCandidate = Literal[
    "summarize",
    "explain",
    "design",
    "implement",
    "operate",
    "verify",
    "accept",
    "commit",
    "cross_reference",
]
TopicRelationCandidate = Literal[
    "parent_of",
    "depends_on",
    "realizes",
    "constrained_by",
    "step_of",
    "produces",
    "consumes",
    "interfaces_with",
    "verified_by",
    "supports_score",
]


class ResponseTopicCandidate(StrictPlanningModel):
    local_id: str = Field(min_length=1)
    parent_local_id: str | None = None
    topic_type: TopicTypeCandidate
    canonical_name: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    upstream_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"


class ResponseDutyCandidate(StrictPlanningModel):
    local_id: str = Field(min_length=1)
    topic_local_id: str = Field(min_length=1)
    duty_type: DutyTypeCandidate
    requirement_ids: list[str] = Field(default_factory=list)
    score_point_ids: list[str] = Field(default_factory=list)
    score_response_unit_ids: list[str] = Field(default_factory=list)
    response_expectations: list[str] = Field(default_factory=list)
    evidence_need_ids: list[str] = Field(default_factory=list)
    priority: Literal["blocking", "high", "normal", "low"] = "normal"
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"


class TopicEdgeCandidate(StrictPlanningModel):
    local_id: str = Field(min_length=1)
    source_topic_local_id: str = Field(min_length=1)
    target_topic_local_id: str = Field(min_length=1)
    relation: TopicRelationCandidate
    order: int = Field(ge=0)
    requirement_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TopicDutyPlanningCandidate(StrictPlanningModel):
    """Semantic topic aggregation and response-duty planning candidate."""

    root_topic_local_ids: list[str] = Field(min_length=1)
    topics: list[ResponseTopicCandidate] = Field(min_length=1)
    duties: list[ResponseDutyCandidate] = Field(min_length=1)
    edges: list[TopicEdgeCandidate] = Field(default_factory=list)
    review_status: Literal["confirmed", "needs_review", "blocked"] = "confirmed"

    @model_validator(mode="after")
    def references_are_local_and_unique(self) -> "TopicDutyPlanningCandidate":
        topic_ids = [topic.local_id for topic in self.topics]
        duty_ids = [duty.local_id for duty in self.duties]
        edge_ids = [edge.local_id for edge in self.edges]
        for label, values in (
            ("Topic", topic_ids),
            ("Duty", duty_ids),
            ("Edge", edge_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"TopicDutyPlanningCandidate 不允许重复 {label} local ID")
        known_topics = set(topic_ids)
        if unknown := set(self.root_topic_local_ids) - known_topics:
            raise ValueError(f"根 Topic 引用未知 local ID: {sorted(unknown)}")
        if unknown := {
            topic.parent_local_id for topic in self.topics if topic.parent_local_id
        } - known_topics:
            raise ValueError(f"Topic 引用未知父级 local ID: {sorted(unknown)}")
        if unknown := {duty.topic_local_id for duty in self.duties} - known_topics:
            raise ValueError(f"Duty 引用未知 Topic local ID: {sorted(unknown)}")
        edge_topics = {
            ref
            for edge in self.edges
            for ref in (edge.source_topic_local_id, edge.target_topic_local_id)
        }
        if unknown := edge_topics - known_topics:
            raise ValueError(f"Edge 引用未知 Topic local ID: {sorted(unknown)}")
        return self


class ChapterOutlineNodeCandidate(StrictPlanningModel):
    """One model-authored outline node before canonical ID compilation."""

    local_id: str = Field(min_length=1)
    parent_local_id: str | None = None
    order: int = Field(
        ge=0,
        description=(
            "全目录唯一顺序号；父章节必须排在其子章节之前，"
            "严格模板模式下保持模板原顺序号"
        ),
    )
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    primary_response_unit_ids: list[str] = Field(default_factory=list)
    supporting_response_unit_ids: list[str] = Field(default_factory=list)
    score_condition_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    planned_tables: list[str] = Field(default_factory=list)
    planned_figures: list[str] = Field(default_factory=list)
    target_size: int = Field(default=800, ge=1)
    template_slot_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False

    @model_validator(mode="after")
    def direct_bindings_are_unique(self) -> "ChapterOutlineNodeCandidate":
        for field_name in (
            "primary_response_unit_ids",
            "supporting_response_unit_ids",
            "score_condition_ids",
            "requirement_ids",
            "template_slot_ids",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(
                    f"ChapterOutlineNodeCandidate.{field_name} 不允许重复 ID"
                )
        overlap = set(self.primary_response_unit_ids) & set(
            self.supporting_response_unit_ids
        )
        if overlap:
            raise ValueError(
                "同一章节不能同时 primary/supporting 绑定同一 "
                f"ScoreResponseUnit: {sorted(overlap)}"
            )
        return self


class ChapterOutlineCandidate(StrictPlanningModel):
    """Complete candidate emitted by ``planning.chapter_outline_split``."""

    nodes: list[ChapterOutlineNodeCandidate] = Field(min_length=1)
    document_quality_response_unit_ids: list[str] = Field(default_factory=list)
    review_status: Literal["draft", "needs_review", "blocked"] = "draft"

    @model_validator(mode="after")
    def tree_and_primary_bindings_are_valid(self) -> "ChapterOutlineCandidate":
        node_ids = [node.local_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ChapterOutlineCandidate 不允许重复 local node ID")
        known_nodes = set(node_ids)
        if unknown := {
            node.parent_local_id
            for node in self.nodes
            if node.parent_local_id is not None
        } - known_nodes:
            raise ValueError(f"章节引用未知父节点 local ID: {sorted(unknown)}")

        parent_by_id = {node.local_id: node.parent_local_id for node in self.nodes}
        for node_id in node_ids:
            seen: set[str] = set()
            cursor: str | None = node_id
            while cursor is not None:
                if cursor in seen:
                    raise ValueError("ChapterOutlineCandidate 章节父子关系存在环")
                seen.add(cursor)
                cursor = parent_by_id[cursor]

        orders = [node.order for node in self.nodes]
        if len(orders) != len(set(orders)):
            raise ValueError("ChapterOutlineCandidate 的 order 必须全局唯一")
        order_by_id = {node.local_id: node.order for node in self.nodes}
        for node in self.nodes:
            if (
                node.parent_local_id is not None
                and order_by_id[node.parent_local_id] >= node.order
            ):
                raise ValueError(
                    f"章节 {node.local_id} 必须排在其父章节之后"
                )

        primary_units = [
            unit_id
            for node in self.nodes
            for unit_id in node.primary_response_unit_ids
        ]
        if len(primary_units) != len(set(primary_units)):
            raise ValueError(
                "每个 ScoreResponseUnit 只能绑定一个 primary 章节候选"
            )
        quality_units = self.document_quality_response_unit_ids
        if len(quality_units) != len(set(quality_units)):
            raise ValueError(
                "document_quality_response_unit_ids 不允许重复 ID"
            )
        return self


CandidateT = TypeVar("CandidateT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredInferenceResult(Generic[CandidateT]):
    """Candidate plus immutable material needed to create an inference receipt."""

    candidate: CandidateT
    raw_output: str
    normalized_output: str
    reasoning: str
    input_snapshot: str
    attempt_count: int
    capability_id: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float
    normalized_reference_count: int = 0
    validation_errors: tuple[str, ...] = ()


class ProjectUnderstandingProvider(Protocol):
    capability_version: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float

    def understand(
        self,
        request: ProjectUnderstandingInput,
    ) -> StructuredInferenceResult[ProjectUnderstandingCandidate]: ...


class TopicDutyPlanningProvider(Protocol):
    capability_version: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float

    def plan(
        self,
        request: TopicDutyPlanningInput,
    ) -> StructuredInferenceResult[TopicDutyPlanningCandidate]: ...


class OutlineDecompositionProvider(Protocol):
    skill_id: str
    capability_version: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float

    def split(
        self,
        request: OutlineDecompositionInput,
    ) -> StructuredInferenceResult[ChapterOutlineCandidate]: ...


ChatResponse = Mapping[str, Any] | str
ChatCallable = Callable[..., ChatResponse]


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _count_normalized_references(before: Any, after: Any) -> int:
    if isinstance(before, dict) and isinstance(after, dict):
        count = 0
        for key, value in before.items():
            if key not in after:
                continue
            if key == "upstream_refs" and isinstance(value, list):
                normalized = after[key]
                if isinstance(normalized, list):
                    count += sum(
                        left != right
                        for left, right in zip(value, normalized, strict=False)
                    )
            else:
                count += _count_normalized_references(value, after[key])
        return count
    if isinstance(before, list) and isinstance(after, list):
        return abs(len(before) - len(after)) + sum(
            _count_normalized_references(left, right)
            for left, right in zip(before, after, strict=False)
        )
    return 0


def _project_input_evidence_error(request: ProjectUnderstandingInput) -> str | None:
    has_source_text = any(
        str(item.get("content") or "").strip()
        for item in request.source_context
        if isinstance(item, dict)
    )
    has_requirement_text = any(
        str(
            item.get("normalized_requirement")
            or item.get("original_text")
            or ""
        ).strip()
        for item in request.requirement_ledger.get("requirements", [])
        if isinstance(item, dict)
    )
    if not has_source_text and not has_requirement_text:
        return "ProjectUnderstandingInput 缺少可用于项目理解的招标正文或项目要求"
    return None


def _prompt_path(filename: str) -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / filename


def load_planning_prompt(filename: str) -> str:
    path = _prompt_path(filename)
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PlanningInferenceError(f"无法读取受控规划提示词: {path}") from exc
    if not prompt:
        raise PlanningInferenceError(f"受控规划提示词为空: {path}")
    return prompt


def planning_prompt_hash(filename: str) -> str:
    return hashlib.sha256(load_planning_prompt(filename).encode("utf-8")).hexdigest()


def _default_chat(messages: list[dict[str, str]], *, temperature: float) -> ChatResponse:
    from llm_client import chat_with_meta

    return chat_with_meta(messages, temperature=temperature)


def _configured_model_fingerprint() -> str:
    try:
        from config import get_settings
        from utils import project_root

        settings = get_settings(project_root())
        return f"{settings.provider}:{settings.model}"
    except Exception as exc:
        raise PlanningInferenceError(
            "无法解析当前活动模型配置，不能创建可审计的规划 Provider"
        ) from exc


def _configured_provider_fingerprint() -> str:
    try:
        from config import get_settings
        from utils import project_root

        settings = get_settings(project_root())
        return canonical_hash(
            {
                "adapter": "llm_client.chat_with_meta",
                "provider": settings.provider,
                "base_url": settings.base_url,
            }
        )
    except Exception as exc:
        raise PlanningInferenceError(
            "无法解析当前 Provider 配置，不能创建可审计的规划 Provider"
        ) from exc


def _callable_fingerprint(chat_callable: ChatCallable) -> str:
    module = getattr(chat_callable, "__module__", chat_callable.__class__.__module__)
    qualname = getattr(
        chat_callable,
        "__qualname__",
        chat_callable.__class__.__qualname__,
    )
    return f"injected:{module}.{qualname}"


class _StructuredLLMProvider(Generic[CandidateT]):
    """Shared strict-JSON invocation with a capability-bounded repair loop."""

    capability_id: str
    prompt_file: str
    prompt_version: str
    schema_version: str
    candidate_model: type[CandidateT]
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS

    def __init__(
        self,
        *,
        chat_callable: ChatCallable | None = None,
        model_fingerprint: str | None = None,
        provider_fingerprint: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        if not 0 <= temperature <= 1:
            raise ValueError("planning inference temperature 必须在 0 到 1 之间")
        self._chat = chat_callable or _default_chat
        self.temperature = float(temperature)
        if model_fingerprint:
            self.model_fingerprint = model_fingerprint.strip()
        elif chat_callable is None:
            self.model_fingerprint = _configured_model_fingerprint()
        else:
            self.model_fingerprint = _callable_fingerprint(chat_callable)
        if not self.model_fingerprint:
            raise ValueError("model_fingerprint 不能为空")
        if provider_fingerprint and provider_fingerprint.strip():
            self.provider_fingerprint = provider_fingerprint.strip()
        elif chat_callable is None:
            self.provider_fingerprint = _configured_provider_fingerprint()
        else:
            self.provider_fingerprint = canonical_hash(
                {
                    "adapter": _callable_fingerprint(chat_callable),
                }
            )
        self.prompt_hash = planning_prompt_hash(self.prompt_file)

    @staticmethod
    def _is_truncated_json_error(error: BaseException) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, PlanningInferenceOutputTruncatedError):
                return True
            current = current.__cause__
        return False

    def _invoke(
        self,
        request: BaseModel,
        *,
        logical_batch_id: str = "",
        repair_attempts: int | None = None,
    ) -> StructuredInferenceResult[CandidateT]:
        input_snapshot = _canonical_json(request)
        if (
            isinstance(request, ProjectUnderstandingInput)
            and len(input_snapshot) > PROJECT_INPUT_MAX_CHARS
        ):
            raise PlanningInferenceError(
                "ProjectUnderstandingInput exceeds the configured single-batch "
                f"budget ({PROJECT_INPUT_MAX_CHARS} characters)"
            )
        if isinstance(request, ProjectUnderstandingInput):
            if evidence_error := _project_input_evidence_error(request):
                failure = PlanningInferenceError(evidence_error)
                failure.code = "PROJECT_INPUT_MISSING_TENDER_EVIDENCE"
                failure.retryable = True
                failure.details = {
                    "input_chars": len(input_snapshot),
                    "source_block_count": len(request.source_context),
                    "scanned_source_block_count": (
                        request.scanned_source_block_count
                    ),
                    "normalized_reference_count": 0,
                    "missing": "tender_project_evidence",
                }
                raise failure
        schema_json = json.dumps(
            self.candidate_model.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
        )
        system_prompt = (
            f"{load_planning_prompt(self.prompt_file)}\n\n"
            f"输出 Schema 版本：{self.schema_version}\n"
            "必须只输出一个完整 JSON 对象，并严格满足以下 JSON Schema：\n"
            f"{schema_json}"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下是内容寻址后冻结的精确输入快照。"
                    "只能引用其中已有的 ID 和事实：\n"
                    f"{input_snapshot}"
                ),
            },
        ]
        last_error: Exception | None = None
        last_raw = ""
        last_reasoning = ""
        first_candidate: CandidateT | None = None
        last_candidate: CandidateT | None = None
        attempt_count = 0
        validation_errors: list[str] = []
        normalized_reference_count = 0

        allowed_repair_attempts = (
            self.max_repair_attempts
            if repair_attempts is None
            else max(0, int(repair_attempts))
        )
        for attempt_index in range(allowed_repair_attempts + 1):
            attempt_count = attempt_index + 1
            candidate_for_feedback: BaseModel | None = None
            try:
                from .llm_telemetry import llm_request_metadata

                with llm_request_metadata(
                    logical_batch_id=(
                        logical_batch_id or self.capability_id
                    ),
                    attempt_kind=(
                        "initial" if attempt_index == 0 else "controlled_repair"
                    ),
                    candidate_attempt=attempt_index + 1,
                    repair_of_attempt=(1 if attempt_index else None),
                ):
                    response = self._chat(
                        messages,
                        temperature=self.temperature,
                    )
            except Exception as exc:
                raise PlanningInferenceCallError(
                    f"{self.capability_id} 大模型调用失败；未生成可晋级候选：{exc}"
                ) from exc

            if isinstance(response, str):
                raw_output = response
                reasoning = ""
                finish_reason = ""
            elif isinstance(response, Mapping):
                raw_output = str(response.get("content") or "")
                reasoning = str(response.get("reasoning") or "")
                finish_reason = str(response.get("finish_reason") or "").lower()
            else:
                raise PlanningInferenceCallError(
                    f"{self.capability_id} Provider 返回了不支持的响应类型: "
                    f"{type(response).__name__}"
                )

            last_raw = raw_output
            last_reasoning = reasoning
            try:
                if finish_reason in {
                    "length",
                    "max_tokens",
                    "max_output_tokens",
                }:
                    raise PlanningInferenceOutputTruncatedError(
                        f"模型输出因长度限制被截断: finish_reason={finish_reason}"
                    )
                candidate_payload = json.loads(raw_output)
                original_payload = candidate_payload
                candidate_payload = self._prepare_candidate_payload(
                    candidate_payload,
                    request,
                )
                normalized_reference_count += _count_normalized_references(
                    original_payload,
                    candidate_payload,
                )
                candidate = self.candidate_model.model_validate(
                    candidate_payload,
                    strict=True,
                )
                candidate = self._prepare_candidate(candidate, request)
                candidate_for_feedback = candidate
                if first_candidate is None:
                    first_candidate = candidate.model_copy(deep=True)
                last_candidate = candidate
                self._validate_candidate(candidate, request)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                validation_errors.append(str(exc))
                if (
                    attempt_index >= allowed_repair_attempts
                    or self._is_truncated_json_error(exc)
                ):
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": raw_output},
                        {
                            "role": "user",
                            "content": (
                                "上一个输出未通过严格 JSON/Pydantic 校验。"
                                "这是受控修复；不得改变输入事实或虚构 ID。"
                                "请只返回修复后的完整 JSON 对象。\n"
                                + f"\n当前为第 {attempt_index + 1}/{allowed_repair_attempts} 次受控修复。\n"
                                + self._repair_feedback(
                                    exc,
                                    candidate_for_feedback,
                                    request,
                                )
                            ),
                        },
                    ]
                )
                continue

            return StructuredInferenceResult(
                candidate=candidate,
                raw_output=raw_output,
                normalized_output=_canonical_json(candidate),
                reasoning=reasoning,
                input_snapshot=input_snapshot,
                attempt_count=attempt_index + 1,
                capability_id=self.capability_id,
                prompt_version=self.prompt_version,
                prompt_hash=self.prompt_hash,
                schema_version=self.schema_version,
                provider_fingerprint=self.provider_fingerprint,
                model_fingerprint=self.model_fingerprint,
                temperature=self.temperature,
                normalized_reference_count=normalized_reference_count,
                validation_errors=tuple(validation_errors),
            )

        recovered_candidate = self._recover_candidate(
            first_candidate=first_candidate,
            last_candidate=last_candidate,
            request=request,
            error=last_error,
        )
        if recovered_candidate is not None:
            try:
                self._validate_candidate(recovered_candidate, request)
            except (ValidationError, ValueError) as recovery_error:
                last_error = recovery_error
            else:
                return StructuredInferenceResult(
                    candidate=recovered_candidate,
                    raw_output=last_raw,
                    normalized_output=_canonical_json(recovered_candidate),
                    reasoning=last_reasoning,
                    input_snapshot=input_snapshot,
                    attempt_count=attempt_count,
                    capability_id=self.capability_id,
                    prompt_version=self.prompt_version,
                    prompt_hash=self.prompt_hash,
                    schema_version=self.schema_version,
                    provider_fingerprint=self.provider_fingerprint,
                    model_fingerprint=self.model_fingerprint,
                    temperature=self.temperature,
                    normalized_reference_count=normalized_reference_count,
                    validation_errors=tuple(validation_errors),
                )

        last_error_summary = str(last_error or "未知校验错误")
        batch_summary = (
            f"逻辑批次={logical_batch_id}；" if logical_batch_id else ""
        )
        repair_summary = (
            "未触发全对象修复"
            if allowed_repair_attempts == 0
            else (
                "在一次受控修复后"
                if allowed_repair_attempts == 1
                else f"在 {allowed_repair_attempts} 次受控修复后"
            )
        )
        failure = PlanningInferenceValidationError(
            f"{self.capability_id} 输出{repair_summary}仍未通过 "
            f"{self.schema_version} 严格校验；未生成可晋级候选。"
            f"{batch_summary}"
            f"最后输出长度={len(last_raw)}，reasoning长度={len(last_reasoning)}；"
            f"最后校验错误：{last_error_summary}"
        )
        failure.attempts = attempt_count
        failure.errors = tuple(validation_errors)
        failure.details = {
            "input_chars": len(input_snapshot),
            "source_block_count": len(
                getattr(request, "source_context", []) or []
            ),
            "scanned_source_block_count": int(
                getattr(request, "scanned_source_block_count", 0) or 0
            ),
            "normalized_reference_count": normalized_reference_count,
            "attempts": attempt_count,
        }
        if isinstance(request, ProjectUnderstandingInput):
            failure.code = "PROJECT_UNDERSTANDING_ACTION_REQUIRED"
            failure.retryable = True
        raise failure from last_error

    def _prepare_candidate(
        self,
        candidate: CandidateT,
        request: BaseModel,
    ) -> CandidateT:
        """Apply deterministic domain projection before semantic validation."""

        del request
        return candidate

    def _prepare_candidate_payload(
        self,
        payload: Any,
        request: BaseModel,
    ) -> Any:
        """Normalize a decoded JSON payload before strict model construction."""

        del request
        return payload

    def _recover_candidate(
        self,
        *,
        first_candidate: CandidateT | None,
        last_candidate: CandidateT | None,
        request: BaseModel,
        error: Exception | None,
    ) -> CandidateT | None:
        """Optionally salvage only verified content after controlled repair."""

        del first_candidate, last_candidate, request, error
        return None

    def _repair_feedback(
        self,
        error: Exception,
        candidate: BaseModel | None,
        request: BaseModel,
    ) -> str:
        del candidate, request
        return f"校验错误：{error}"

    def _validate_candidate(
        self,
        candidate: CandidateT,
        request: BaseModel,
    ) -> None:
        """Validate semantic references while still inside the one-repair loop."""


def _active_requirement_ids(ledger: dict[str, Any]) -> set[str]:
    return {
        str(item.get("requirement_id"))
        for item in ledger.get("requirements", [])
        if item.get("requirement_id")
        and item.get("status") not in {"blocked", "waived"}
    }


def _score_ids(score_model: dict[str, Any]) -> set[str]:
    return {
        str(item.get("score_point_id"))
        for item in score_model.get("points", [])
        if item.get("score_point_id")
    }


def _planning_reference_ids(
    *,
    ledger: dict[str, Any],
    score_model: dict[str, Any],
    source_context: list[dict[str, Any]],
    project_model: dict[str, Any] | None = None,
) -> set[str]:
    refs = {
        f"RequirementLedger:{item['requirement_id']}"
        for item in ledger.get("requirements", [])
        if item.get("requirement_id")
    }
    for group in score_model.get("groups", []):
        if group.get("group_id"):
            refs.add(f"ScoreModel:{group['group_id']}")
    for point in score_model.get("points", []):
        if point.get("score_point_id"):
            refs.add(f"ScoreModel:{point['score_point_id']}")
        for condition in point.get("score_conditions", []):
            if condition.get("condition_id"):
                refs.add(f"ScoreModel:{condition['condition_id']}")
        for unit in point.get("response_units", []):
            if unit.get("unit_id"):
                refs.add(f"ScoreModel:{unit['unit_id']}")
    for block in source_context:
        if block.get("block_id"):
            refs.add(f"SourceIndex:{block['block_id']}")
        anchor = block.get("source_anchor") or {}
        if block.get("input_id") and anchor.get("chunk_id"):
            refs.add(
                f"SourceIndex:{block['input_id']}:{anchor['chunk_id']}"
            )
    if project_model is not None:
        if project_model.get("project_id"):
            refs.add(f"ProjectModel:{project_model['project_id']}")
        for field in ("confirmed_facts", "inferences", "conflicts"):
            for fact in project_model.get(field, []):
                if fact.get("fact_id"):
                    refs.add(f"ProjectModel:{fact['fact_id']}")
    return refs


def _normalize_project_source_refs(
    payload: Any,
    source_context: list[dict[str, Any]],
) -> Any:
    """Canonicalize the one harmless shorthand accepted at the JSON boundary.

    Models occasionally return a SourceIndex block ID (``B-17``) instead of
    the namespaced reference required by the compiler.  Resolve only exact,
    unique block/chunk matches from this request's frozen source context.  No
    fuzzy matching is allowed: unknown or ambiguous values remain unchanged
    and are rejected by the ordinary reference validator.
    """

    if not isinstance(payload, (dict, list)):
        return payload
    block_matches: dict[str, list[str]] = defaultdict(list)
    chunk_matches: dict[str, list[str]] = defaultdict(list)
    for block in source_context:
        block_id = str(block.get("block_id") or "").strip()
        if block_id:
            block_matches[block_id].append(f"SourceIndex:{block_id}")
        anchor = block.get("source_anchor") or {}
        input_id = str(block.get("input_id") or anchor.get("source_input_id") or "").strip()
        chunk_id = str(anchor.get("chunk_id") or "").strip()
        if input_id and chunk_id:
            key = f"{input_id}:{chunk_id}"
            chunk_matches[key].append(f"SourceIndex:{key}")

    def normalize_ref(value: Any) -> Any:
        if not isinstance(value, str) or value.startswith("SourceIndex:"):
            return value
        block_values = block_matches.get(value, [])
        if len(block_values) == 1:
            return block_values[0]
        chunk_values = chunk_matches.get(value, [])
        if len(chunk_values) == 1:
            return chunk_values[0]
        return value

    def walk(value: Any, *, in_upstream_refs: bool = False) -> Any:
        if isinstance(value, list):
            return [walk(item, in_upstream_refs=in_upstream_refs) for item in value]
        if isinstance(value, dict):
            return {
                key: (
                    [normalize_ref(item) for item in raw]
                    if key == "upstream_refs" and isinstance(raw, list)
                    else walk(raw, in_upstream_refs=(key == "upstream_refs"))
                )
                for key, raw in value.items()
            }
        return normalize_ref(value) if in_upstream_refs else value

    return walk(payload)


def _sanitize_project_candidate_payload(
    payload: Any,
    source_context: list[dict[str, Any]],
) -> Any:
    """Keep only facts grounded in raw blocks from this exact invocation."""

    if not isinstance(payload, dict):
        return payload
    known_refs = {
        ref
        for block in source_context
        for ref in (
            (
                f"SourceIndex:{block.get('block_id')}"
                if block.get("block_id")
                else ""
            ),
            (
                "SourceIndex:"
                f"{block.get('input_id')}:"
                f"{(block.get('source_anchor') or {}).get('chunk_id')}"
                if block.get("input_id")
                and (block.get("source_anchor") or {}).get("chunk_id")
                else ""
            ),
        )
        if ref
    }

    def clean_item(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        refs = [
            str(ref)
            for ref in item.get("upstream_refs", [])
            if str(ref) in known_refs
        ]
        if not refs:
            return None
        value = {**item, "upstream_refs": list(dict.fromkeys(refs))}
        if "requirement_ids" in value:
            value["requirement_ids"] = []
        return value

    value = dict(payload)
    if value.get("project_name") is not None:
        value["project_name"] = clean_item(value.get("project_name"))
    for field_name in (
        "identity",
        *_PROJECT_CITED_LIST_FIELDS,
        "terminology",
        "facts",
        "evidence_needs",
    ):
        items = value.get(field_name)
        if isinstance(items, list):
            value[field_name] = [
                cleaned
                for item in items
                if (cleaned := clean_item(item)) is not None
            ]
    return value


def _project_candidate_refs(
    candidate: ProjectUnderstandingCandidate,
) -> list[str]:
    refs: list[str] = []
    if candidate.project_name is not None:
        refs.extend(candidate.project_name.upstream_refs)
    refs.extend(
        ref
        for item in candidate.identity
        for ref in item.upstream_refs
    )
    for field in _PROJECT_CITED_LIST_FIELDS:
        refs.extend(
            ref
            for item in getattr(candidate, field)
            for ref in item.upstream_refs
        )
    refs.extend(
        ref
        for item in candidate.terminology
        for ref in item.upstream_refs
    )
    refs.extend(
        ref
        for item in candidate.facts
        for ref in item.upstream_refs
    )
    refs.extend(
        ref
        for item in candidate.evidence_needs
        for ref in item.upstream_refs
    )
    return refs


def _project_reference_texts(
    request: ProjectUnderstandingInput,
) -> dict[str, list[str]]:
    """Build the exact text catalog used to ground confirmed project prose."""

    catalog: dict[str, list[str]] = {}

    def add(ref: str, *values: Any) -> None:
        texts = [
            str(value)
            for value in values
            if value is not None and str(value).strip()
        ]
        if texts:
            catalog.setdefault(ref, []).extend(texts)

    for item in request.requirement_ledger.get("requirements", []):
        requirement_id = item.get("requirement_id")
        if requirement_id:
            add(
                f"RequirementLedger:{requirement_id}",
                item.get("original_text"),
                item.get("normalized_requirement"),
            )
    for block in request.source_context:
        content = block.get("content")
        if block.get("block_id"):
            add(f"SourceIndex:{block['block_id']}", content)
        anchor = block.get("source_anchor") or {}
        if block.get("input_id") and anchor.get("chunk_id"):
            add(
                f"SourceIndex:{block['input_id']}:{anchor['chunk_id']}",
                content,
            )
    return catalog


def _confirmed_project_candidate_items(
    candidate: ProjectUnderstandingCandidate,
) -> list[tuple[str, list[str], str]]:
    items: list[tuple[str, list[str], str]] = []
    if candidate.project_name is not None:
        items.append(
            (
                "project_name",
                candidate.project_name.upstream_refs,
                candidate.project_name.text,
            )
        )
    items.extend(
        (
            f"identity.{item.field}",
            item.upstream_refs,
            item.value,
        )
        for item in candidate.identity
    )
    for field in (
        "background",
        "goals",
        "scope",
        "boundaries",
        "work_packages",
        "dependencies",
        "inputs",
        "processing",
        "outputs",
        "deliverables",
        "acceptance_conditions",
        "milestones",
        "roles",
        "risks",
        "constraints",
    ):
        items.extend(
            (
                f"{field}.{index}",
                item.upstream_refs,
                item.text,
            )
            for index, item in enumerate(
                getattr(candidate, field),
                start=1,
            )
        )
    items.extend(
        (
            f"terminology.{item.term}",
            item.upstream_refs,
            f"{item.term}：{item.definition}",
        )
        for item in candidate.terminology
    )
    items.extend(
        (
            f"fact.{item.local_id}",
            item.upstream_refs,
            item.statement,
        )
        for item in candidate.facts
        if item.classification == "confirmed"
    )
    return items


def _unsupported_project_candidate_items(
    candidate: ProjectUnderstandingCandidate,
    request: ProjectUnderstandingInput,
) -> list[dict[str, Any]]:
    """Return complete, evidence-bearing diagnostics for confirmed prose."""

    from .project_model import _is_text_supported_by_groups

    reference_texts = _project_reference_texts(request)
    unsupported: list[dict[str, Any]] = []
    for owner, refs, statement in _confirmed_project_candidate_items(
        candidate
    ):
        evidence_groups = [reference_texts.get(ref, []) for ref in refs]
        if _is_text_supported_by_groups(statement, evidence_groups):
            continue
        unsupported.append(
            {
                "owner": owner,
                "statement": statement,
                "cited_evidence": [
                    {
                        "ref": ref,
                        "excerpts": [
                            text[:600]
                            for text in reference_texts.get(ref, [])[:2]
                        ],
                    }
                    for ref in refs
                ],
            }
        )
    return unsupported


def _supported_project_candidate_projection(
    candidate: ProjectUnderstandingCandidate,
    request: ProjectUnderstandingInput,
) -> tuple[ProjectUnderstandingCandidate, set[str]]:
    """Remove only confirmed prose that the frozen references cannot verify."""

    unsupported = {
        item["owner"]
        for item in _unsupported_project_candidate_items(candidate, request)
    }
    if not unsupported:
        return candidate.model_copy(deep=True), set()

    value = candidate.model_dump(mode="json")
    if "project_name" in unsupported:
        value["project_name"] = None
    value["identity"] = [
        item
        for item in value.get("identity", [])
        if f"identity.{item.get('field')}" not in unsupported
    ]
    for field in _PROJECT_CITED_LIST_FIELDS:
        value[field] = [
            item
            for index, item in enumerate(value.get(field, []), start=1)
            if f"{field}.{index}" not in unsupported
        ]
    value["terminology"] = [
        item
        for item in value.get("terminology", [])
        if f"terminology.{item.get('term')}" not in unsupported
    ]
    value["facts"] = [
        item
        for item in value.get("facts", [])
        if (
            item.get("classification") != "confirmed"
            or f"fact.{item.get('local_id')}" not in unsupported
        )
    ]
    value["review_status"] = "needs_review"
    return ProjectUnderstandingCandidate.model_validate(value), unsupported


def _merge_verified_project_candidates(
    first: ProjectUnderstandingCandidate | None,
    last: ProjectUnderstandingCandidate,
    request: ProjectUnderstandingInput,
) -> ProjectUnderstandingCandidate:
    """Reconcile a full-output repair without allowing unrelated regressions.

    The model currently returns a complete object for its one repair attempt.
    Only individually supported prose from that object is accepted, while
    supported items from the first attempt are frozen and carried forward.
    Unsupported prose is never promoted by this recovery path.
    """

    verified_last, removed_last = _supported_project_candidate_projection(
        last,
        request,
    )
    if first is None:
        return verified_last
    verified_first, removed_first = _supported_project_candidate_projection(
        first,
        request,
    )

    value = verified_last.model_dump(mode="json")
    if verified_first.project_name is not None:
        value["project_name"] = verified_first.project_name.model_dump(
            mode="json"
        )

    def merge_by_key(
        first_items: list[BaseModel],
        last_items: list[BaseModel],
        *,
        key: Callable[[BaseModel], str],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in (*first_items, *last_items):
            item_key = key(item)
            if item_key in seen:
                continue
            seen.add(item_key)
            merged.append(item.model_dump(mode="json"))
        return merged

    value["identity"] = merge_by_key(
        verified_first.identity,
        verified_last.identity,
        key=lambda item: str(getattr(item, "field", "")).casefold(),
    )
    for field in _PROJECT_CITED_LIST_FIELDS:
        value[field] = merge_by_key(
            list(getattr(verified_first, field)),
            list(getattr(verified_last, field)),
            key=lambda item: "".join(
                str(getattr(item, "text", "")).split()
            ).casefold(),
        )
    value["terminology"] = merge_by_key(
        verified_first.terminology,
        verified_last.terminology,
        key=lambda item: str(getattr(item, "term", "")).casefold(),
    )
    value["facts"] = merge_by_key(
        verified_first.facts,
        verified_last.facts,
        key=lambda item: str(getattr(item, "local_id", "")).casefold(),
    )

    # EvidenceNeed is the one area where repair-time changes should win (for
    # example blocked -> needs_review), so merge the final attempt first.
    value["evidence_needs"] = merge_by_key(
        verified_last.evidence_needs,
        verified_first.evidence_needs,
        key=lambda item: str(getattr(item, "local_id", "")).casefold(),
    )
    value["unknowns"] = list(
        dict.fromkeys(
            [
                *verified_first.unknowns,
                *verified_last.unknowns,
            ]
        )
    )
    if removed_first or removed_last:
        value["review_status"] = "needs_review"
    return ProjectUnderstandingCandidate.model_validate(value)


def merge_project_understanding_candidates(
    candidates: list[ProjectUnderstandingCandidate],
) -> ProjectUnderstandingCandidate:
    """Deterministically merge independently validated project batches."""

    if not candidates:
        raise ValueError("项目理解分批候选不能为空")
    value = candidates[0].model_dump(mode="json")
    needs_review = any(item.review_status != "confirmed" for item in candidates)
    conflict_notes: list[str] = []

    def norm(text: Any) -> str:
        return "".join(str(text or "").split()).casefold()

    first_name = candidates[0].project_name
    for candidate in candidates[1:]:
        if candidate.project_name is None:
            continue
        if first_name is None:
            first_name = candidate.project_name
            value["project_name"] = first_name.model_dump(mode="json")
        elif norm(first_name.text) != norm(candidate.project_name.text):
            needs_review = True
            conflict_notes.append(
                f"项目名称候选冲突：{first_name.text} / {candidate.project_name.text}"
            )

    def merge_items(field: str, *, text_field: str) -> None:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for candidate in candidates:
            for item in getattr(candidate, field):
                key = (
                    norm(getattr(item, text_field)),
                    tuple(sorted(item.upstream_refs)),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item.model_dump(mode="json"))
        value[field] = merged

    identities: list[dict[str, Any]] = []
    identity_by_field: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for item in candidate.identity:
            key = item.field.casefold()
            prior = identity_by_field.get(key)
            if prior is None:
                dumped = item.model_dump(mode="json")
                identity_by_field[key] = dumped
                identities.append(dumped)
            elif norm(prior.get("value")) != norm(item.value):
                needs_review = True
                conflict_notes.append(
                    f"项目身份字段 {item.field} 候选冲突："
                    f"{prior.get('value')} / {item.value}"
                )
    value["identity"] = identities

    for field in _PROJECT_CITED_LIST_FIELDS:
        merge_items(field, text_field="text")
    merge_items("terminology", text_field="definition")

    facts: list[dict[str, Any]] = []
    seen_facts: set[tuple[str, tuple[str, ...]]] = set()
    for batch_index, candidate in enumerate(candidates, start=1):
        for item_index, item in enumerate(candidate.facts, start=1):
            key = (norm(item.statement), tuple(sorted(item.upstream_refs)))
            if key in seen_facts:
                continue
            seen_facts.add(key)
            dumped = item.model_dump(mode="json")
            dumped["local_id"] = f"PF-B{batch_index:02d}-{item_index:03d}"
            facts.append(dumped)
    value["facts"] = facts

    needs: list[dict[str, Any]] = []
    seen_needs: set[tuple[str, str, tuple[str, ...]]] = set()
    for batch_index, candidate in enumerate(candidates, start=1):
        for item_index, item in enumerate(candidate.evidence_needs, start=1):
            key = (
                norm(item.question),
                item.topic_id.casefold(),
                tuple(sorted(item.upstream_refs)),
            )
            if key in seen_needs:
                continue
            seen_needs.add(key)
            dumped = item.model_dump(mode="json")
            dumped["local_id"] = f"PEN-B{batch_index:02d}-{item_index:03d}"
            needs.append(dumped)
    value["evidence_needs"] = needs
    value["unknowns"] = list(
        dict.fromkeys(
            [
                *(text for item in candidates for text in item.unknowns),
                *conflict_notes,
            ]
        )
    )
    value["review_status"] = "needs_review" if needs_review else "confirmed"
    return ProjectUnderstandingCandidate.model_validate(value)


class LLMProjectUnderstandingProvider(
    _StructuredLLMProvider[ProjectUnderstandingCandidate]
):
    capability_id = "planning.project_understanding"
    capability_version = PROJECT_CAPABILITY_VERSION
    prompt_file = PROJECT_PROMPT_FILE
    prompt_version = PROJECT_PROMPT_VERSION
    schema_version = PROJECT_SCHEMA_VERSION
    candidate_model = ProjectUnderstandingCandidate
    # One raw-source call normally suffices.  Only malformed JSON or an empty
    # core understanding receives one controlled repair.
    max_repair_attempts = 1

    @staticmethod
    def _raw_source_mode(request: BaseModel) -> bool:
        return bool(
            isinstance(request, ProjectUnderstandingInput)
            and request.requirement_ledger.get("projection_version")
            == PROJECT_INPUT_PROJECTION_VERSION
        )

    def _prepare_candidate_payload(
        self,
        payload: Any,
        request: BaseModel,
    ) -> Any:
        if not isinstance(request, ProjectUnderstandingInput):
            return payload
        normalized = _normalize_project_source_refs(
            payload,
            request.source_context,
        )
        if not self._raw_source_mode(request):
            return normalized
        return _sanitize_project_candidate_payload(
            normalized,
            request.source_context,
        )

    def _prepare_candidate(
        self,
        candidate: ProjectUnderstandingCandidate,
        request: BaseModel,
    ) -> ProjectUnderstandingCandidate:
        if not isinstance(request, ProjectUnderstandingInput):
            return candidate
        return candidate

    def _recover_candidate(
        self,
        *,
        first_candidate: ProjectUnderstandingCandidate | None,
        last_candidate: ProjectUnderstandingCandidate | None,
        request: BaseModel,
        error: Exception | None,
    ) -> ProjectUnderstandingCandidate | None:
        if (
            last_candidate is None
            or not isinstance(request, ProjectUnderstandingInput)
            or error is None
            or "与其引用来源缺少可核验文本关联" not in str(error)
        ):
            return None
        recovered = _merge_verified_project_candidates(
            first_candidate,
            last_candidate,
            request,
        )
        # Item-level quarantine must never turn a failed project candidate into
        # an empty shell.  Keep the original fail-closed behavior when no
        # project goal, scope, work package, explicit gap, or unknown survives.
        if not any(
            (
                recovered.goals,
                recovered.scope,
                recovered.work_packages,
                recovered.evidence_needs,
                recovered.unknowns,
            )
        ):
            return None
        return recovered

    def understand(
        self,
        request: ProjectUnderstandingInput,
    ) -> StructuredInferenceResult[ProjectUnderstandingCandidate]:
        return self._invoke(
            request,
            logical_batch_id=request.batch_id,
            repair_attempts=(1 if self._raw_source_mode(request) else 2),
        )

    def _repair_feedback(
        self,
        error: Exception,
        candidate: BaseModel | None,
        request: BaseModel,
    ) -> str:
        if not isinstance(request, ProjectUnderstandingInput):
            return super()._repair_feedback(error, candidate, request)

        active_requirement_ids = sorted(
            _active_requirement_ids(request.requirement_ledger)
        )
        candidate_diagnostics: list[str] = []
        if isinstance(candidate, ProjectUnderstandingCandidate):
            raw_source_mode = self._raw_source_mode(request)
            known_refs = _planning_reference_ids(
                ledger=(
                    {}
                    if raw_source_mode
                    else request.requirement_ledger
                ),
                score_model={},
                source_context=request.source_context,
            )
            candidate_refs = set(_project_candidate_refs(candidate))
            if unknown_refs := sorted(candidate_refs - known_refs):
                allowed_source_refs = sorted(
                    ref
                    for ref in known_refs
                    if ref.startswith("SourceIndex:")
                )
                candidate_diagnostics.extend(
                    [
                        "当前候选含以下未知 upstream_refs，必须删除或替换为输入中"
                        "真实存在的正式引用：",
                        json.dumps(unknown_refs, ensure_ascii=False),
                        "本批允许使用的 SourceIndex 引用如下；不得保留或新造"
                        "清单外的 SourceIndex ID：",
                        json.dumps(allowed_source_refs, ensure_ascii=False),
                    ]
                )

            known_requirement_ids = {
                str(item.get("requirement_id"))
                for item in request.requirement_ledger.get(
                    "requirements",
                    [],
                )
                if item.get("requirement_id")
            }
            fact_requirement_ids = {
                requirement_id
                for fact in candidate.facts
                for requirement_id in fact.requirement_ids
            }
            if unknown_fact_ids := sorted(
                fact_requirement_ids - known_requirement_ids
            ):
                candidate_diagnostics.extend(
                    [
                        "当前候选 facts.requirement_ids 含以下未知 ID，必须删除：",
                        json.dumps(unknown_fact_ids, ensure_ascii=False),
                    ]
                )

            unsupported_items = (
                []
                if raw_source_mode
                else _unsupported_project_candidate_items(
                    candidate,
                    request,
                )
            )
            if unsupported_items:
                candidate_diagnostics.extend(
                    [
                        "以下 confirmed 语义项与引用文本缺少可核验关联。"
                        "每项已列出当前陈述、引用和对应来源原文；必须逐项忠实"
                        "改写、拆分复合陈述、删除无依据分句，或改用真正支持"
                        "该文本的输入引用。若不同分句确由不同来源分别支撑，"
                        "应保留全部必要引用：",
                        json.dumps(unsupported_items, ensure_ascii=False),
                    ]
                )

            blocked_needs = sorted(
                need.local_id
                for need in candidate.evidence_needs
                if not raw_source_mode and need.review_status == "blocked"
            )
            if blocked_needs:
                candidate_diagnostics.extend(
                    [
                        "以下 EvidenceNeed 不得标记 blocked，必须改为 needs_review "
                        "或 confirmed：",
                        json.dumps(blocked_needs, ensure_ascii=False),
                    ]
                )

        details = [
            f"校验错误：{error}",
            "必须在受控修复中完成以下修复；不得改变输入事实或虚构 ID：",
            *candidate_diagnostics,
            "1. 删除所有不在输入快照中的 upstream_refs 和 requirement_ids；"
            "SourceIndex 引用必须使用 SourceIndex:<block_id> 或 "
            "SourceIndex:<input_id>:<chunk_id> 格式。",
            "2. 保留已有的项目名称、目标、范围、工作包、交付物、验收、"
            "风险和证据缺口等有效语义内容，只修复覆盖数组、引用和明确的校验问题。",
        ]
        return "\n".join(details)

    def _validate_candidate(
        self,
        candidate: ProjectUnderstandingCandidate,
        request: BaseModel,
    ) -> None:
        if not isinstance(request, ProjectUnderstandingInput):
            raise ValueError("ProjectUnderstandingProvider 输入类型错误")
        raw_source_mode = self._raw_source_mode(request)
        known_refs = _planning_reference_ids(
            ledger=(
                {}
                if raw_source_mode
                else request.requirement_ledger
            ),
            score_model={},
            source_context=request.source_context,
        )
        candidate_refs = set(_project_candidate_refs(candidate))
        unknown_refs = candidate_refs - known_refs
        if unknown_refs:
            raise ValueError(
                f"项目理解引用未知上游 ID: {sorted(unknown_refs)}"
            )
        if not any(
            (
                candidate.project_name is not None,
                candidate.goals,
                candidate.scope,
                candidate.work_packages,
                candidate.evidence_needs if not raw_source_mode else [],
                candidate.unknowns if not raw_source_mode else [],
            )
        ):
            raise ValueError(
                "项目理解未形成目标、范围、工作包，也未明确 unknown/evidence_need"
            )
        unsupported_items = (
            []
            if raw_source_mode
            else _unsupported_project_candidate_items(candidate, request)
        )
        if unsupported_items:
            owners = [item["owner"] for item in unsupported_items]
            raise ValueError(
                f"项目理解 {owners[0]} 与其引用来源缺少可核验文本关联；"
                f"全部未通过项={owners}"
            )
        fact_requirement_ids = {
            requirement_id
            for fact in candidate.facts
            for requirement_id in fact.requirement_ids
        }
        known_requirement_ids = {
            str(item.get("requirement_id"))
            for item in request.requirement_ledger.get(
                "requirements",
                [],
            )
            if item.get("requirement_id")
        }
        if unknown := fact_requirement_ids - known_requirement_ids:
            raise ValueError(
                f"项目事实引用未知 Requirement: {sorted(unknown)}"
            )
        if not raw_source_mode and any(
            need.review_status == "blocked"
            for need in candidate.evidence_needs
        ):
            raise ValueError("项目理解包含 blocked EvidenceNeed 候选")
        if not raw_source_mode and candidate.review_status == "blocked":
            raise ValueError("项目理解候选标记为 blocked")
        semantic_lists = {
            name: [
                "".join(item.text.split()).casefold()
                for item in getattr(candidate, name)
            ]
            for name in ("goals", "scope", "work_packages")
        }
        for left, right in (
            ("goals", "scope"),
            ("goals", "work_packages"),
            ("scope", "work_packages"),
        ):
            left_values = semantic_lists[left]
            right_values = semantic_lists[right]
            if not raw_source_mode and (
                left_values
                and right_values
                and (
                    left_values == right_values
                    or set(left_values) == set(right_values)
                )
            ):
                raise ValueError(
                    f"项目理解候选机械复制 {left}/{right}，必须按语义重新归纳"
                )


class LLMTopicDutyPlanningProvider(
    _StructuredLLMProvider[TopicDutyPlanningCandidate]
):
    capability_id = "planning.topic_duty_plan"
    capability_version = TOPIC_CAPABILITY_VERSION
    prompt_file = TOPIC_PROMPT_FILE
    prompt_version = TOPIC_PROMPT_VERSION
    schema_version = TOPIC_SCHEMA_VERSION
    candidate_model = TopicDutyPlanningCandidate

    def plan(
        self,
        request: TopicDutyPlanningInput,
    ) -> StructuredInferenceResult[TopicDutyPlanningCandidate]:
        return self._invoke(request)

    def _validate_candidate(
        self,
        candidate: TopicDutyPlanningCandidate,
        request: BaseModel,
    ) -> None:
        if not isinstance(request, TopicDutyPlanningInput):
            raise ValueError("TopicDutyPlanningProvider 输入类型错误")
        active_requirements = _active_requirement_ids(
            request.requirement_ledger
        )
        score_ids = _score_ids(request.score_model)
        score_unit_owner = {
            str(unit["unit_id"]): str(point["score_point_id"])
            for point in request.score_model.get("points", [])
            if point.get("score_point_id")
            for unit in point.get("response_units", [])
            if unit.get("unit_id")
        }
        known_requirement_ids = {
            str(item.get("requirement_id"))
            for item in request.requirement_ledger.get(
                "requirements",
                [],
            )
            if item.get("requirement_id")
        }
        evidence_ids = {
            str(item.get("need_id"))
            for item in request.project_model.get("evidence_needs", [])
            if item.get("need_id")
        }
        known_refs = _planning_reference_ids(
            ledger=request.requirement_ledger,
            score_model=request.score_model,
            source_context=request.source_context,
            project_model=request.project_model,
        )
        unknown_refs = {
            ref
            for topic in candidate.topics
            for ref in topic.upstream_refs
            if ref not in known_refs
        }
        if unknown_refs:
            raise ValueError(
                f"Topic 候选引用未知上游 ID: {sorted(unknown_refs)}"
            )
        parent_by_topic = {
            topic.local_id: topic.parent_local_id
            for topic in candidate.topics
        }
        for topic_id in parent_by_topic:
            seen: set[str] = set()
            cursor: str | None = topic_id
            while cursor is not None:
                if cursor in seen:
                    raise ValueError("Topic 父子关系存在环")
                seen.add(cursor)
                cursor = parent_by_topic[cursor]
        covered_requirements: set[str] = set()
        covered_scores: set[str] = set()
        covered_score_units: list[str] = []
        topic_by_id = {topic.local_id: topic for topic in candidate.topics}
        for topic in candidate.topics:
            if unknown := set(topic.requirement_ids) - known_requirement_ids:
                raise ValueError(
                    f"Topic {topic.local_id} 引用未知 Requirement: {sorted(unknown)}"
                )
            if unknown := set(topic.score_point_ids) - score_ids:
                raise ValueError(
                    f"Topic {topic.local_id} 引用未知 ScorePoint: {sorted(unknown)}"
                )
            if len(topic.score_point_ids) > 1:
                raise ValueError(
                    f"Topic {topic.local_id} 同时绑定多个 ScorePoint；"
                    "应使用不直接绑定评分点的语义父 Topic 聚合"
                )
            for requirement_id in topic.requirement_ids:
                if f"RequirementLedger:{requirement_id}" not in topic.upstream_refs:
                    raise ValueError(
                        f"Topic {topic.local_id} 缺少 Requirement 来源引用"
                    )
            for score_point_id in topic.score_point_ids:
                if f"ScoreModel:{score_point_id}" not in topic.upstream_refs:
                    raise ValueError(
                        f"Topic {topic.local_id} 缺少 ScorePoint 来源引用"
                    )
        for duty in candidate.duties:
            topic = topic_by_id[duty.topic_local_id]
            if not set(duty.requirement_ids) <= set(topic.requirement_ids):
                raise ValueError(
                    f"Duty {duty.local_id} 的 Requirement 未在所属 Topic 声明"
                )
            if not set(duty.score_point_ids) <= set(topic.score_point_ids):
                raise ValueError(
                    f"Duty {duty.local_id} 的 ScorePoint 未在所属 Topic 声明"
                )
            if len(duty.score_response_unit_ids) != len(
                set(duty.score_response_unit_ids)
            ):
                raise ValueError(
                    f"Duty {duty.local_id} 重复绑定 ScoreResponseUnit"
                )
            if len(duty.score_response_unit_ids) > 1:
                raise ValueError(
                    f"Duty {duty.local_id} 压缩多个独立 ScoreResponseUnit"
                )
            if unknown := set(duty.score_response_unit_ids) - set(
                score_unit_owner
            ):
                raise ValueError(
                    f"Duty {duty.local_id} 引用未知 ScoreResponseUnit: "
                    f"{sorted(unknown)}"
                )
            for unit_id in duty.score_response_unit_ids:
                owner_score_id = score_unit_owner[unit_id]
                if owner_score_id not in duty.score_point_ids:
                    raise ValueError(
                        f"Duty {duty.local_id} 的 ScoreResponseUnit {unit_id} "
                        f"不属于其绑定 ScorePoint"
                    )
            if unknown := set(duty.evidence_need_ids) - evidence_ids:
                raise ValueError(
                    f"Duty {duty.local_id} 引用未知 EvidenceNeed: {sorted(unknown)}"
                )
            covered_requirements.update(duty.requirement_ids)
            covered_scores.update(duty.score_point_ids)
            covered_score_units.extend(duty.score_response_unit_ids)
        for edge in candidate.edges:
            if unknown := set(edge.requirement_ids) - known_requirement_ids:
                raise ValueError(
                    f"Edge {edge.local_id} 引用未知 Requirement: {sorted(unknown)}"
                )
        depends_on: dict[str, set[str]] = {
            topic.local_id: set() for topic in candidate.topics
        }
        for edge in candidate.edges:
            if edge.relation == "depends_on":
                depends_on[edge.source_topic_local_id].add(
                    edge.target_topic_local_id
                )
        for topic_id in depends_on:
            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(current: str) -> None:
                if current in visiting:
                    raise ValueError("Topic depends_on 关系存在环")
                if current in visited:
                    return
                visiting.add(current)
                for dependency in depends_on[current]:
                    visit(dependency)
                visiting.remove(current)
                visited.add(current)

            visit(topic_id)
        if covered_requirements != active_requirements:
            raise ValueError(
                "Topic/Duty 未精确覆盖有效 Requirement；"
                f"missing={sorted(active_requirements - covered_requirements)}, "
                f"extra={sorted(covered_requirements - active_requirements)}"
            )
        if covered_scores != score_ids:
            raise ValueError(
                "Topic/Duty 未精确覆盖 ScorePoint；"
                f"missing={sorted(score_ids - covered_scores)}, "
                f"extra={sorted(covered_scores - score_ids)}"
            )
        if (
            set(covered_score_units) != set(score_unit_owner)
            or len(covered_score_units) != len(set(covered_score_units))
        ):
            raise ValueError(
                "Topic/Duty 未将每个 ScoreResponseUnit 精确绑定一次"
            )
        if candidate.review_status == "blocked":
            raise ValueError("Topic/Duty 候选标记为 blocked")


@dataclass(frozen=True, slots=True)
class OutlineBatchSpec:
    """A content-addressed, independently retryable outline request."""

    batch_id: str
    group_id: str
    point_ids: tuple[str, ...]
    sort_key: tuple[int, ...]
    request: OutlineDecompositionInput


class FileOutlineFragmentCache:
    """Strict cache for validated outline fragments.

    A fragment is reusable only when its exact projected input and every
    inference-control fingerprint match.  Corrupt or legacy entries are misses.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def load(self, cache_key: str) -> dict[str, Any] | None:
        path = self.root / cache_key[:2] / f"{cache_key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(value, dict)
            or value.get("cache_key") != cache_key
            or (
                not isinstance(value.get("candidate"), dict)
                and value.get("disposition") != "split"
            )
        ):
            return None
        return value

    def store(self, cache_key: str, value: Mapping[str, Any]) -> None:
        path = self.root / cache_key[:2] / f"{cache_key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cache_key": cache_key, **dict(value)}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)


class LLMOutlineDecompositionProvider(
    _StructuredLLMProvider[ChapterOutlineCandidate]
):
    skill_id = OUTLINE_SKILL_ID
    capability_id = OUTLINE_SKILL_ID
    capability_version = OUTLINE_CAPABILITY_VERSION
    prompt_file = OUTLINE_PROMPT_FILE
    prompt_version = OUTLINE_PROMPT_VERSION
    schema_version = OUTLINE_SCHEMA_VERSION
    candidate_model = ChapterOutlineCandidate
    # All code paths inside split() pass repair_attempts=1 explicitly.
    # The class-level value aligns with MAX_REPAIR_ATTEMPTS so that any
    # future code path that forgets to pass the argument also gets one
    # controlled-repair attempt instead of silently skipping it.
    max_repair_attempts = MAX_REPAIR_ATTEMPTS

    def __init__(
        self,
        *,
        chat_callable: ChatCallable | None = None,
        model_fingerprint: str | None = None,
        provider_fingerprint: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        batch_cache: FileOutlineFragmentCache | None = None,
    ) -> None:
        super().__init__(
            chat_callable=chat_callable,
            model_fingerprint=model_fingerprint,
            provider_fingerprint=provider_fingerprint,
            temperature=temperature,
        )
        self.batch_cache = batch_cache
        self.last_batch_summary: dict[str, int] = {}

    def _repair_feedback(
        self,
        error: Exception,
        candidate: BaseModel | None,
        request: BaseModel,
    ) -> str:
        base = super()._repair_feedback(error, candidate, request)
        error_text = str(error)
        hints: list[str] = []
        if "章节标题包含评分式评价语" in error_text or "章节标题缺少业务对象" in error_text:
            hints.append(
                "标题修复：章节标题只写业务对象、任务、方法、过程或成果；"
                "将“科学、合理、细致、条理清楚、重点突出、可操作性强”等"
                "评分评价语移入 writing_objectives。相关 condition_id 可以绑定同一"
                "业务章节，不必为每个条件复制一个评分句式标题。"
            )
        if "未进入其 ScoreResponseUnit" in error_text or "未进入其主责章节子树" in error_text:
            hints.append(
                "Requirement 绑定修复：ScoreResponseUnit 关联的所有 linked_requirement_ids"
                "必须出现在该 Unit 唯一 primary 章节的子树内（该节点或其任意子节点的"
                "requirement_ids 字段中），不得挂到无关章节。"
            )
        if "未保留 outline_path" in error_text:
            hints.append(
                "outline_path 修复：当 ScoreResponseUnit 携带 outline_path 时，必须按照"
                "原路径依次建立或复用同标题的祖先节点，不得在路径末级与条件节点之间插入"
                "改写标题或省略路径节点。"
            )
        if hints:
            return base + "\n\n" + "\n".join(hints)
        return base

    def split(
        self,
        request: OutlineDecompositionInput,
    ) -> StructuredInferenceResult[ChapterOutlineCandidate]:
        if request.document_mode == "template_strict":
            # Template structure is already a fixed skeleton. Keep the existing
            # strict full-object protocol until template binding is projected
            # wholly by the compiler; automatic outlines use the incremental
            # protocol below.
            result = self._invoke(request, repair_attempts=1)
            self.last_batch_summary = {
                "outline_batch_count": 1,
                "outline_batch_generated_count": 1,
                "outline_batch_reused_count": 0,
            }
            return result

        controlled_request = request
        specs = self._build_batch_specs(request)
        if len(specs) <= 1:
            result = self._invoke(
                request,
                logical_batch_id=(
                    specs[0].batch_id if specs else self.capability_id
                ),
                # 与 template_strict 和单点批次保持一致：给予一次受控修复机会
                repair_attempts=1,
            )
            self.last_batch_summary = {
                "outline_batch_count": 1,
                "outline_batch_generated_count": 1,
                "outline_batch_reused_count": 0,
            }
            # Receipts and durable checkpoints remain bound to the exact
            # caller-controlled ScoreModel snapshot used for topology.
            return replace(
                result,
                input_snapshot=_canonical_json(controlled_request),
            )

        pending = list(specs)
        completed: list[
            tuple[OutlineBatchSpec, StructuredInferenceResult[ChapterOutlineCandidate]]
        ] = []
        generated_count = 0
        reused_count = 0
        while pending:
            spec = pending.pop(0)
            if self._cached_split(spec):
                left, right = self._split_spec(spec)
                pending[0:0] = [left, right]
                continue
            cached = self._load_cached_fragment(spec)
            if cached is not None:
                completed.append((spec, cached))
                reused_count += 1
                continue
            try:
                result = self._invoke(
                    spec.request,
                    logical_batch_id=spec.batch_id,
                    repair_attempts=1,
                )
            except PlanningInferenceValidationError as exc:
                if self._is_truncated_json_error(exc) and len(spec.point_ids) > 1:
                    left, right = self._split_spec(spec)
                    self._store_split(spec)
                    pending[0:0] = [left, right]
                    continue
                self.last_batch_summary = {
                    "outline_batch_count": len(pending) + len(completed) + 1,
                    "outline_batch_generated_count": generated_count,
                    "outline_batch_reused_count": reused_count,
                    "outline_batch_failed_count": 1,
                }
                raise
            self._store_cached_fragment(spec, result)
            completed.append((spec, result))
            generated_count += 1

        completed.sort(key=lambda item: item[0].sort_key)
        merged = self._merge_fragments(request, completed)
        self._validate_candidate(merged, request)
        attempts = sum(result.attempt_count for _, result in completed)
        self.last_batch_summary = {
            "outline_batch_count": len(completed),
            "outline_batch_generated_count": generated_count,
            "outline_batch_reused_count": reused_count,
            "outline_batch_failed_count": 0,
        }
        return StructuredInferenceResult(
            candidate=merged,
            raw_output=_canonical_json(
                {
                    "mode": "batched_outline",
                    "batches": [
                        {
                            "batch_id": spec.batch_id,
                            "point_ids": list(spec.point_ids),
                            "raw_output": result.raw_output,
                        }
                        for spec, result in completed
                    ],
                }
            ),
            normalized_output=_canonical_json(merged),
            reasoning="\n".join(
                result.reasoning
                for _, result in completed
                if result.reasoning.strip()
            ),
            input_snapshot=_canonical_json(controlled_request),
            attempt_count=attempts,
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
            temperature=self.temperature,
        )

    def _cache_key(self, spec: OutlineBatchSpec) -> str:
        return canonical_hash(
            {
                "protocol": "outline_fragment_cache.v1",
                "batch_id": spec.batch_id,
                "request": spec.request.model_dump(mode="json"),
                "capability_version": self.capability_version,
                "prompt_version": self.prompt_version,
                "prompt_hash": self.prompt_hash,
                "schema_version": self.schema_version,
                "provider_fingerprint": self.provider_fingerprint,
                "model_fingerprint": self.model_fingerprint,
                "temperature": self.temperature,
            }
        )

    def _load_cached_fragment(
        self,
        spec: OutlineBatchSpec,
    ) -> StructuredInferenceResult[ChapterOutlineCandidate] | None:
        if self.batch_cache is None:
            return None
        value = self.batch_cache.load(self._cache_key(spec))
        if value is None:
            return None
        try:
            candidate = ChapterOutlineCandidate.model_validate(
                value["candidate"],
                strict=True,
            )
            self._validate_candidate(candidate, spec.request)
        except (ValidationError, ValueError):
            return None
        return StructuredInferenceResult(
            candidate=candidate,
            raw_output=str(value.get("raw_output") or ""),
            normalized_output=_canonical_json(candidate),
            reasoning=str(value.get("reasoning") or ""),
            input_snapshot=_canonical_json(spec.request),
            attempt_count=0,
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
            temperature=self.temperature,
        )

    def _cached_split(self, spec: OutlineBatchSpec) -> bool:
        if self.batch_cache is None:
            return False
        value = self.batch_cache.load(self._cache_key(spec))
        return bool(value and value.get("disposition") == "split")

    def _store_split(self, spec: OutlineBatchSpec) -> None:
        if self.batch_cache is None:
            return
        self.batch_cache.store(
            self._cache_key(spec),
            {
                "batch_id": spec.batch_id,
                "point_ids": list(spec.point_ids),
                "disposition": "split",
            },
        )

    def _store_cached_fragment(
        self,
        spec: OutlineBatchSpec,
        result: StructuredInferenceResult[ChapterOutlineCandidate],
    ) -> None:
        if self.batch_cache is None:
            return
        self.batch_cache.store(
            self._cache_key(spec),
            {
                "batch_id": spec.batch_id,
                "point_ids": list(spec.point_ids),
                "candidate": result.candidate.model_dump(mode="json"),
                "raw_output": result.raw_output,
                "reasoning": result.reasoning,
            },
        )

    @staticmethod
    def _is_truncated_json_error(error: BaseException) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, json.JSONDecodeError):
                # After the one allowed repair, any invalid JSON from a
                # multi-score batch is recoverable by splitting the request.
                # This also covers a missing ':' delimiter in an otherwise
                # complete large object.
                return True
            if isinstance(current, PlanningInferenceOutputTruncatedError):
                return True
            current = current.__cause__
        return False

    def _build_batch_specs(
        self,
        request: OutlineDecompositionInput,
    ) -> list[OutlineBatchSpec]:
        score_model = request.score_model
        points = [
            point
            for point in score_model.get("points", [])
            if any(
                unit.get("response_scope") == "section"
                for unit in point.get("response_units", [])
            )
        ]
        points_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for point in points:
            points_by_group[str(point.get("group_id") or "")].append(point)

        specs: list[OutlineBatchSpec] = []
        for group_index, group in enumerate(score_model.get("groups", [])):
            group_id = str(group.get("group_id") or "")
            group_points = points_by_group.get(group_id, [])
            current: list[dict[str, Any]] = []
            chunk_index = 0
            for point in group_points:
                proposed = [*current, point]
                scoped = self._scoped_request(request, group, proposed)
                if current and (
                    len(proposed) > OUTLINE_BATCH_MAX_ITEMS
                    or len(_canonical_json(scoped)) > OUTLINE_BATCH_MAX_INPUT_CHARS
                ):
                    specs.append(
                        self._make_spec(
                            request,
                            group,
                            current,
                            (group_index, chunk_index),
                        )
                    )
                    chunk_index += 1
                    current = [point]
                else:
                    current = proposed
            if current:
                specs.append(
                    self._make_spec(
                        request,
                        group,
                        current,
                        (group_index, chunk_index),
                    )
                )
        return specs

    def _make_spec(
        self,
        request: OutlineDecompositionInput,
        group: Mapping[str, Any],
        points: list[dict[str, Any]],
        sort_key: tuple[int, ...],
    ) -> OutlineBatchSpec:
        point_ids = tuple(str(point.get("score_point_id") or "") for point in points)
        group_id = str(group.get("group_id") or "")
        batch_id = "outline-" + canonical_hash(
            {"group_id": group_id, "point_ids": point_ids}
        )[:16]
        return OutlineBatchSpec(
            batch_id=batch_id,
            group_id=group_id,
            point_ids=point_ids,
            sort_key=sort_key,
            request=self._scoped_request(request, group, points),
        )

    def _split_spec(
        self,
        spec: OutlineBatchSpec,
    ) -> tuple[OutlineBatchSpec, OutlineBatchSpec]:
        points_by_id = {
            str(point.get("score_point_id") or ""): point
            for point in spec.request.score_model.get("points", [])
        }
        midpoint = max(1, len(spec.point_ids) // 2)
        left_ids = spec.point_ids[:midpoint]
        right_ids = spec.point_ids[midpoint:]
        group = spec.request.score_model.get("groups", [])[0]
        full_request = spec.request
        left = self._make_spec(
            full_request,
            group,
            [points_by_id[point_id] for point_id in left_ids],
            (*spec.sort_key, 0),
        )
        right = self._make_spec(
            full_request,
            group,
            [points_by_id[point_id] for point_id in right_ids],
            (*spec.sort_key, 1),
        )
        return left, right

    @staticmethod
    def _scoped_request(
        request: OutlineDecompositionInput,
        group: Mapping[str, Any],
        points: list[dict[str, Any]],
    ) -> OutlineDecompositionInput:
        requirement_ids: set[str] = set()
        for point in points:
            requirement_ids.update(
                str(item)
                for item in point.get("linked_requirement_ids", [])
                if item
            )
            for unit in point.get("response_units", []):
                requirement_ids.update(
                    str(item)
                    for item in unit.get("linked_requirement_ids", [])
                    if item
                )
        ledger = dict(request.requirement_ledger)
        ledger["requirements"] = [
            item
            for item in request.requirement_ledger.get("requirements", [])
            if str(item.get("requirement_id") or "") in requirement_ids
        ]
        scoped_group = dict(group)
        if isinstance(scoped_group.get("score_point_ids"), list):
            scoped_group["score_point_ids"] = [
                str(point.get("score_point_id") or "") for point in points
            ]
        score_model = dict(request.score_model)
        score_model["groups"] = [scoped_group]
        score_model["points"] = [dict(point) for point in points]
        score_model["total_points"] = sum(
            float(point.get("max_points") or 0.0) for point in points
        )
        return OutlineDecompositionInput(
            requirement_ledger=ledger,
            score_model=score_model,
            template_structure=None,
            document_mode="auto_outline",
        )

    def _merge_fragments(
        self,
        request: OutlineDecompositionInput,
        completed: list[
            tuple[OutlineBatchSpec, StructuredInferenceResult[ChapterOutlineCandidate]]
        ],
    ) -> ChapterOutlineCandidate:
        nodes_by_group: dict[str, list[ChapterOutlineNodeCandidate]] = defaultdict(list)
        root_id_by_group: dict[str, str] = {}
        shared_node_id_by_group_path: dict[
            str, dict[tuple[str, str], str]
        ] = defaultdict(dict)
        outline_titles_by_group: dict[str, set[str]] = defaultdict(set)
        for point in request.score_model.get("points", []):
            group_id = str(point.get("group_id") or "")
            outline_paths = [
                point.get("outline_path") or [],
                *(
                    unit.get("outline_path") or []
                    for unit in point.get("response_units", [])
                ),
            ]
            for outline_path in outline_paths:
                outline_titles_by_group[group_id].update(
                    outline_structure_key(str(title))
                    for title in outline_path
                    if str(title).strip()
                )
        statuses: list[str] = []

        def merge_node_bindings(
            existing: ChapterOutlineNodeCandidate,
            incoming: ChapterOutlineNodeCandidate,
        ) -> ChapterOutlineNodeCandidate:
            list_updates = {
                field_name: list(
                    dict.fromkeys(
                        [
                            *getattr(existing, field_name),
                            *getattr(incoming, field_name),
                        ]
                    )
                )
                for field_name in (
                    "writing_objectives",
                    "primary_response_unit_ids",
                    "supporting_response_unit_ids",
                    "score_condition_ids",
                    "requirement_ids",
                    "required_mentions",
                    "planned_tables",
                    "planned_figures",
                    "template_slot_ids",
                )
            }
            primary_ids = set(list_updates["primary_response_unit_ids"])
            list_updates["supporting_response_unit_ids"] = [
                unit_id
                for unit_id in list_updates["supporting_response_unit_ids"]
                if unit_id not in primary_ids
            ]
            return existing.model_copy(
                update={
                    **list_updates,
                    "target_size": max(existing.target_size, incoming.target_size),
                    "confidence": min(existing.confidence, incoming.confidence),
                    "needs_human": existing.needs_human or incoming.needs_human,
                }
            )

        for spec, result in completed:
            statuses.append(result.candidate.review_status)
            ordered = sorted(result.candidate.nodes, key=lambda node: node.order)
            roots = [node for node in ordered if node.parent_local_id is None]
            if len(roots) != 1:
                raise ValueError(
                    f"目录批次 {spec.batch_id} 必须且只能生成一个评分组根节点"
                )
            source_root = roots[0]
            canonical_root = root_id_by_group.get(spec.group_id)
            if canonical_root is None:
                canonical_root = f"group-{canonical_hash(spec.group_id)[:16]}"
                root_id_by_group[spec.group_id] = canonical_root
                nodes_by_group[spec.group_id].append(
                    source_root.model_copy(
                        update={
                            "local_id": canonical_root,
                            "parent_local_id": None,
                        }
                    )
                )
            else:
                existing_root = nodes_by_group[spec.group_id][0]
                nodes_by_group[spec.group_id][0] = merge_node_bindings(
                    existing_root,
                    source_root,
                )
            remap = {
                node.local_id: (
                    canonical_root
                    if node.local_id == source_root.local_id
                    else f"{spec.batch_id}:{node.local_id}"
                )
                for node in ordered
            }
            for node in ordered:
                if node.local_id == source_root.local_id:
                    continue
                parent_id = (
                    remap.get(node.parent_local_id)
                    if node.parent_local_id is not None
                    else canonical_root
                )
                normalized_title = re.sub(r"\s+", " ", node.title).strip()
                normalized_title_key = outline_structure_key(normalized_title)
                shared_key = (str(parent_id or ""), normalized_title_key)
                existing_id = (
                    shared_node_id_by_group_path[spec.group_id].get(shared_key)
                    if normalized_title_key in outline_titles_by_group[spec.group_id]
                    else None
                )
                if existing_id is not None:
                    existing_index = next(
                        index
                        for index, existing in enumerate(nodes_by_group[spec.group_id])
                        if existing.local_id == existing_id
                    )
                    nodes_by_group[spec.group_id][existing_index] = merge_node_bindings(
                        nodes_by_group[spec.group_id][existing_index],
                        node,
                    )
                    remap[node.local_id] = existing_id
                    continue

                merged_node = node.model_copy(
                    update={
                        "local_id": remap[node.local_id],
                        "parent_local_id": parent_id,
                    }
                )
                nodes_by_group[spec.group_id].append(merged_node)
                if normalized_title_key in outline_titles_by_group[spec.group_id]:
                    shared_node_id_by_group_path[spec.group_id][shared_key] = (
                        merged_node.local_id
                    )

        merged_nodes: list[ChapterOutlineNodeCandidate] = []
        for group in request.score_model.get("groups", []):
            merged_nodes.extend(
                nodes_by_group.get(str(group.get("group_id") or ""), [])
            )
        merged_nodes = [
            node.model_copy(update={"order": index})
            for index, node in enumerate(merged_nodes)
        ]
        catalog = self._direct_catalog(request)
        status = (
            "blocked"
            if "blocked" in statuses
            else ("needs_review" if "needs_review" in statuses else "draft")
        )
        return ChapterOutlineCandidate(
            nodes=merged_nodes,
            document_quality_response_unit_ids=sorted(
                catalog["document_unit_ids"]
            ),
            review_status=status,
        )

    def _prepare_candidate_payload(
        self,
        payload: Any,
        request: BaseModel,
    ) -> Any:
        if (
            isinstance(request, OutlineDecompositionInput)
            and request.document_mode == "template_strict"
        ):
            # Template order is authoritative (and may contain intentional
            # gaps). Strict validation below must reject any model deviation.
            return payload
        if not isinstance(payload, dict):
            return payload
        nodes = payload.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return payload
        if not all(isinstance(node, dict) for node in nodes):
            return payload

        node_ids = [node.get("local_id") for node in nodes]
        if (
            not all(isinstance(node_id, str) and node_id for node_id in node_ids)
            or len(node_ids) != len(set(node_ids))
        ):
            return payload
        known_node_ids = set(node_ids)
        if any(
            node.get("parent_local_id") is not None
            and node.get("parent_local_id") not in known_node_ids
            for node in nodes
        ):
            return payload
        if any(
            type(node.get("order")) is not int or node["order"] < 0
            for node in nodes
        ):
            return payload

        order_by_id = {
            str(node["local_id"]): int(node["order"]) for node in nodes
        }
        parent_by_id = {
            str(node["local_id"]): node.get("parent_local_id") for node in nodes
        }
        orders_are_valid = len(set(order_by_id.values())) == len(nodes) and all(
            parent_id is None
            or order_by_id[str(parent_id)] < order_by_id[node_id]
            for node_id, parent_id in parent_by_id.items()
        )
        if orders_are_valid:
            return payload

        input_index = {
            str(node_id): index for index, node_id in enumerate(node_ids)
        }
        children_by_parent: dict[str | None, list[str]] = defaultdict(list)
        for node_id, parent_id in parent_by_id.items():
            children_by_parent[
                str(parent_id) if parent_id is not None else None
            ].append(node_id)
        for children in children_by_parent.values():
            children.sort(
                key=lambda node_id: (
                    order_by_id[node_id],
                    input_index[node_id],
                )
            )

        ordered_node_ids: list[str] = []
        visited: set[str] = set()
        stack = list(reversed(children_by_parent.get(None, [])))
        while stack:
            node_id = stack.pop()
            if node_id in visited:
                return payload
            visited.add(node_id)
            ordered_node_ids.append(node_id)
            stack.extend(reversed(children_by_parent.get(node_id, [])))
        if len(ordered_node_ids) != len(nodes):
            # A valid tree must expose every node from a root. Leave cycles to
            # the strict candidate validator instead of repairing hierarchy.
            return payload

        normalized_order = {
            node_id: order for order, node_id in enumerate(ordered_node_ids)
        }
        normalized_payload = dict(payload)
        normalized_payload["nodes"] = [
            {
                **node,
                "order": normalized_order[str(node["local_id"])],
            }
            for node in nodes
        ]
        return normalized_payload

    @staticmethod
    def _direct_catalog(
        request: OutlineDecompositionInput,
    ) -> dict[str, Any]:
        points: dict[str, dict[str, Any]] = {}
        units: dict[str, dict[str, Any]] = {}
        unit_owner: dict[str, str] = {}
        unit_order: list[str] = []
        conditions: dict[str, dict[str, Any]] = {}
        condition_owner_point: dict[str, str] = {}
        condition_owner_unit: dict[str, str] = {}
        condition_unit_counts: dict[str, int] = defaultdict(int)
        duplicate_point_ids: set[str] = set()
        duplicate_unit_ids: set[str] = set()
        duplicate_condition_ids: set[str] = set()

        for point in request.score_model.get("points", []):
            point_id = str(point.get("score_point_id") or "")
            if not point_id:
                continue
            if point_id in points:
                duplicate_point_ids.add(point_id)
            points[point_id] = point
            for condition in point.get("score_conditions", []):
                condition_id = str(condition.get("condition_id") or "")
                if not condition_id:
                    continue
                if condition_id in conditions:
                    duplicate_condition_ids.add(condition_id)
                conditions[condition_id] = condition
                condition_owner_point[condition_id] = point_id
            for unit in point.get("response_units", []):
                unit_id = str(unit.get("unit_id") or "")
                if not unit_id:
                    continue
                if unit_id in units:
                    duplicate_unit_ids.add(unit_id)
                else:
                    unit_order.append(unit_id)
                units[unit_id] = unit
                unit_owner[unit_id] = point_id
                for condition_id_value in unit.get("condition_ids", []):
                    condition_id = str(condition_id_value)
                    condition_unit_counts[condition_id] += 1
                    condition_owner_unit[condition_id] = unit_id

        active_point_ids = {
            point_id
            for point_id, point in points.items()
            if point.get("review_status") != "blocked"
        }
        active_unit_ids = {
            unit_id
            for unit_id, point_id in unit_owner.items()
            if point_id in active_point_ids
            and units[unit_id].get("review_status") != "blocked"
        }
        section_unit_ids = {
            unit_id
            for unit_id in active_unit_ids
            if units[unit_id].get("response_scope", "section") == "section"
        }
        document_unit_ids = {
            unit_id
            for unit_id in active_unit_ids
            if units[unit_id].get("response_scope", "section") == "document"
        }
        active_condition_ids = {
            condition_id
            for condition_id, condition in conditions.items()
            if condition.get("review_status") != "blocked"
            and condition_owner_point.get(condition_id) in active_point_ids
        }
        visible_condition_ids = {
            condition_id
            for condition_id in active_condition_ids
            if condition_owner_unit.get(condition_id) in section_unit_ids
            and (
                conditions[condition_id].get("condition_role") or "content"
            )
            != "document"
        }
        document_condition_ids = active_condition_ids - visible_condition_ids

        requirements = {
            str(item.get("requirement_id")): item
            for item in request.requirement_ledger.get("requirements", [])
            if item.get("requirement_id")
        }
        active_requirement_ids = {
            requirement_id
            for requirement_id, item in requirements.items()
            if item.get("status") not in {"blocked", "waived"}
        }
        linked_requirement_ids: set[str] = set()
        linked_requirements_by_unit: dict[str, set[str]] = {}
        for unit_id in unit_order:
            if unit_id not in active_unit_ids:
                continue
            unit = units[unit_id]
            linked = {
                str(requirement_id)
                for requirement_id in unit.get(
                    "linked_requirement_ids",
                    [],
                )
                if requirement_id
            }
            linked_requirements_by_unit[unit_id] = linked
            linked_requirement_ids.update(linked)
        required_requirement_ids = {
            requirement_id
            for unit_id in section_unit_ids
            for requirement_id in linked_requirements_by_unit.get(
                unit_id,
                set(),
            )
            if requirement_id in active_requirement_ids
        }
        return {
            "points": points,
            "units": units,
            "unit_owner": unit_owner,
            "unit_order": unit_order,
            "conditions": conditions,
            "condition_owner_point": condition_owner_point,
            "condition_owner_unit": condition_owner_unit,
            "condition_unit_counts": condition_unit_counts,
            "duplicate_point_ids": duplicate_point_ids,
            "duplicate_unit_ids": duplicate_unit_ids,
            "duplicate_condition_ids": duplicate_condition_ids,
            "active_point_ids": active_point_ids,
            "active_unit_ids": active_unit_ids,
            "section_unit_ids": section_unit_ids,
            "document_unit_ids": document_unit_ids,
            "active_condition_ids": active_condition_ids,
            "visible_condition_ids": visible_condition_ids,
            "document_condition_ids": document_condition_ids,
            "requirements": requirements,
            "active_requirement_ids": active_requirement_ids,
            "linked_requirement_ids": linked_requirement_ids,
            "linked_requirements_by_unit": linked_requirements_by_unit,
            "required_requirement_ids": required_requirement_ids,
        }

    @staticmethod
    def _next_local_id(
        prefix: str,
        source_id: str,
        known_ids: set[str],
    ) -> str:
        base = (
            f"{prefix}-"
            f"{hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:12]}"
        )
        candidate = base
        suffix = 2
        while candidate in known_ids:
            candidate = f"{base}-{suffix}"
            suffix += 1
        known_ids.add(candidate)
        return candidate

    @staticmethod
    def _template_target_local_id(
        nodes: list[dict[str, Any]],
        template: dict[str, Any] | None,
        requirement_ids: set[str],
    ) -> str:
        ordered_nodes = sorted(
            nodes,
            key=lambda item: (int(item.get("order", 0)), str(item["local_id"])),
        )
        if not ordered_nodes:
            raise ValueError("章节候选缺少可绑定节点")
        if template is not None:
            template_nodes = sorted(
                template.get("nodes", []),
                key=lambda item: int(item.get("order", 0)),
            )
            if len(template_nodes) == len(ordered_nodes):
                for candidate_node, template_node in zip(
                    ordered_nodes,
                    template_nodes,
                    strict=True,
                ):
                    if requirement_ids & {
                        str(requirement_id)
                        for requirement_id in template_node.get(
                            "requirement_ids",
                            [],
                        )
                    }:
                        return str(candidate_node["local_id"])
        roots = [
            node
            for node in ordered_nodes
            if node.get("parent_local_id") is None
        ]
        return str((roots or ordered_nodes)[0]["local_id"])

    def _prepare_candidate(
        self,
        candidate: ChapterOutlineCandidate,
        request: BaseModel,
    ) -> ChapterOutlineCandidate:
        """Project model annotations onto the deterministic scoring-table tree."""

        if (
            not isinstance(request, OutlineDecompositionInput)
            or request.document_mode != "auto_outline"
        ):
            return candidate

        catalog = self._direct_catalog(request)
        section_unit_ids = set(catalog["section_unit_ids"])
        active_condition_ids = set(catalog["visible_condition_ids"])

        annotations_by_unit: dict[
            str, list[ChapterOutlineNodeCandidate]
        ] = defaultdict(list)
        annotations_by_title: dict[
            str, list[ChapterOutlineNodeCandidate]
        ] = defaultdict(list)
        for node in candidate.nodes:
            annotations_by_title[outline_structure_key(node.title)].append(node)
            for unit_id in (
                *node.primary_response_unit_ids,
                *node.supporting_response_unit_ids,
            ):
                if unit_id in section_unit_ids:
                    annotations_by_unit[unit_id].append(node)

        def annotations(
            title: str,
            unit_ids: list[str],
        ) -> list[ChapterOutlineNodeCandidate]:
            selected: list[ChapterOutlineNodeCandidate] = []
            seen: set[str] = set()
            for unit_id in unit_ids:
                for node in annotations_by_unit.get(unit_id, []):
                    if node.local_id not in seen:
                        seen.add(node.local_id)
                        selected.append(node)
            for node in annotations_by_title.get(
                outline_structure_key(title),
                [],
            ):
                if node.local_id not in seen:
                    seen.add(node.local_id)
                    selected.append(node)
            return selected

        def merged_strings(
            selected: list[ChapterOutlineNodeCandidate],
            field_name: str,
        ) -> list[str]:
            return list(
                dict.fromkeys(
                    value
                    for node in selected
                    for value in getattr(node, field_name)
                    if str(value).strip()
                )
            )

        groups = [
            group
            for group in request.score_model.get("groups", [])
            if any(
                catalog["points"][catalog["unit_owner"][unit_id]].get(
                    "group_id"
                )
                == group.get("group_id")
                for unit_id in section_unit_ids
            )
        ]
        points = request.score_model.get("points", [])
        points_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for point in points:
            points_by_group[str(point.get("group_id") or "")].append(point)

        nodes: list[ChapterOutlineNodeCandidate] = []
        node_index_by_path: dict[tuple[str, tuple[str, ...]], int] = {}

        for group in groups:
            group_id = str(group.get("group_id") or "")
            group_title = str(group.get("title") or "").strip()
            root_id = f"group-{canonical_hash(group_id)[:16]}"
            root_annotations = annotations(group_title, [])
            nodes.append(
                ChapterOutlineNodeCandidate(
                    local_id=root_id,
                    parent_local_id=None,
                    order=len(nodes),
                    title=group_title,
                    purpose=(
                        next(
                            (
                                node.purpose
                                for node in root_annotations
                                if node.purpose.strip()
                            ),
                            f"完整响应{group_title}评分要求",
                        )
                    ),
                    writing_objectives=merged_strings(
                        root_annotations,
                        "writing_objectives",
                    ),
                    required_mentions=merged_strings(
                        root_annotations,
                        "required_mentions",
                    ),
                    planned_tables=merged_strings(
                        root_annotations,
                        "planned_tables",
                    ),
                    planned_figures=merged_strings(
                        root_annotations,
                        "planned_figures",
                    ),
                    target_size=max(
                        [800, *(node.target_size for node in root_annotations)]
                    ),
                    confidence=min(
                        [1.0, *(node.confidence for node in root_annotations)]
                    ),
                    needs_human=any(
                        node.needs_human for node in root_annotations
                    ),
                )
            )
            node_index_by_path[(group_id, ())] = len(nodes) - 1

            for point in points_by_group.get(group_id, []):
                point_id = str(point.get("score_point_id") or "")
                point_units = [
                    unit
                    for unit in point.get("response_units", [])
                    if str(unit.get("unit_id") or "") in section_unit_ids
                ]
                if not point_units:
                    continue
                unit_ids = [str(unit["unit_id"]) for unit in point_units]
                raw_path = [
                    str(value).strip()
                    for value in point.get("outline_path", [])
                    if str(value).strip()
                ]
                if (
                    raw_path
                    and outline_structure_key(raw_path[0])
                    == outline_structure_key(group_title)
                ):
                    raw_path.pop(0)
                if not raw_path:
                    fallback_title = str(point.get("title") or "").strip()
                    if not fallback_title and point_units:
                        fallback_title = str(
                            point_units[0].get("title") or ""
                        ).strip()
                    if fallback_title:
                        raw_path.append(fallback_title)

                parent_id = root_id
                path_keys: list[str] = []
                for title in raw_path:
                    path_keys.append(outline_structure_key(title))
                    path_key = (group_id, tuple(path_keys))
                    existing_index = node_index_by_path.get(path_key)
                    if existing_index is not None:
                        parent_id = nodes[existing_index].local_id
                        continue
                    selected = annotations(title, unit_ids)
                    local_id = "factor-" + canonical_hash(
                        {"group_id": group_id, "path": path_keys}
                    )[:16]
                    nodes.append(
                        ChapterOutlineNodeCandidate(
                            local_id=local_id,
                            parent_local_id=parent_id,
                            order=len(nodes),
                            title=title,
                            purpose=(
                                next(
                                    (
                                        node.purpose
                                        for node in selected
                                        if node.purpose.strip()
                                    ),
                                    f"完整响应评分因素“{title}”",
                                )
                            ),
                            writing_objectives=merged_strings(
                                selected,
                                "writing_objectives",
                            ),
                            required_mentions=merged_strings(
                                selected,
                                "required_mentions",
                            ),
                            planned_tables=merged_strings(
                                selected,
                                "planned_tables",
                            ),
                            planned_figures=merged_strings(
                                selected,
                                "planned_figures",
                            ),
                            target_size=max(
                                [800, *(node.target_size for node in selected)]
                            ),
                            confidence=min(
                                [1.0, *(node.confidence for node in selected)]
                            ),
                            needs_human=any(node.needs_human for node in selected),
                        )
                    )
                    node_index_by_path[path_key] = len(nodes) - 1
                    parent_id = local_id

                leaf_index = node_index_by_path[(group_id, tuple(path_keys))]
                leaf = nodes[leaf_index]
                selected = annotations(leaf.title, unit_ids)
                condition_ids = list(
                    dict.fromkeys(
                        str(condition_id)
                        for unit in point_units
                        for condition_id in unit.get("condition_ids", [])
                        if str(condition_id) in active_condition_ids
                    )
                )
                requirement_ids = list(
                    dict.fromkeys(
                        str(requirement_id)
                        for unit in point_units
                        for requirement_id in unit.get(
                            "linked_requirement_ids",
                            [],
                        )
                        if str(requirement_id)
                        in catalog["active_requirement_ids"]
                    )
                )
                objectives = list(leaf.writing_objectives)
                for condition_id in condition_ids:
                    condition = catalog["conditions"][condition_id]
                    objective = str(
                        condition.get("response_intent")
                        or condition.get("normalized_condition")
                        or condition.get("text")
                        or ""
                    ).strip()
                    if objective and objective not in objectives:
                        objectives.append(objective)
                for unit in point_units:
                    expectation = str(
                        unit.get("response_expectation") or ""
                    ).strip()
                    if expectation and expectation not in objectives:
                        objectives.append(expectation)
                nodes[leaf_index] = leaf.model_copy(
                    update={
                        "writing_objectives": objectives,
                        "primary_response_unit_ids": list(
                            dict.fromkeys(
                                [*leaf.primary_response_unit_ids, *unit_ids]
                            )
                        ),
                        "score_condition_ids": list(
                            dict.fromkeys(
                                [*leaf.score_condition_ids, *condition_ids]
                            )
                        ),
                        "requirement_ids": list(
                            dict.fromkeys(
                                [*leaf.requirement_ids, *requirement_ids]
                            )
                        ),
                        "required_mentions": list(
                            dict.fromkeys(
                                [
                                    *leaf.required_mentions,
                                    *merged_strings(selected, "required_mentions"),
                                    point_id,
                                    *requirement_ids,
                                ]
                            )
                        ),
                        "planned_tables": list(
                            dict.fromkeys(
                                [
                                    *leaf.planned_tables,
                                    *merged_strings(selected, "planned_tables"),
                                ]
                            )
                        ),
                        "planned_figures": list(
                            dict.fromkeys(
                                [
                                    *leaf.planned_figures,
                                    *merged_strings(selected, "planned_figures"),
                                ]
                            )
                        ),
                        "target_size": max(
                            [leaf.target_size, *(node.target_size for node in selected)]
                        ),
                        "confidence": min(
                            [leaf.confidence, *(node.confidence for node in selected)]
                        ),
                        "needs_human": (
                            leaf.needs_human
                            or any(node.needs_human for node in selected)
                        ),
                    }
                )

        return ChapterOutlineCandidate(
            nodes=nodes,
            document_quality_response_unit_ids=sorted(
                catalog["document_unit_ids"]
            ),
            review_status=candidate.review_status,
        )

    def _validate_candidate(
        self,
        candidate: ChapterOutlineCandidate,
        request: BaseModel,
    ) -> None:
        if not isinstance(request, OutlineDecompositionInput):
            raise ValueError("OutlineDecompositionProvider 输入类型错误")
        catalog = self._direct_catalog(request)
        for label, duplicates in (
            ("ScorePoint", catalog["duplicate_point_ids"]),
            ("ScoreResponseUnit", catalog["duplicate_unit_ids"]),
            ("ScoreCondition", catalog["duplicate_condition_ids"]),
        ):
            if duplicates:
                raise ValueError(
                    f"{label} ID 非全局唯一: {sorted(duplicates)}"
                )
        if not catalog["active_unit_ids"] and catalog["active_point_ids"]:
            raise ValueError(
                "活动 ScorePoint 缺少 ScoreResponseUnit，不能建立直接目录主责"
            )
        units_by_point: dict[str, set[str]] = {}
        for unit_id in catalog["active_unit_ids"]:
            units_by_point.setdefault(
                catalog["unit_owner"][unit_id],
                set(),
            ).add(unit_id)
            unit = catalog["units"][unit_id]
            unknown_conditions = {
                str(condition_id)
                for condition_id in unit.get("condition_ids", [])
            } - set(catalog["conditions"])
            if unknown_conditions:
                raise ValueError(
                    f"ScoreResponseUnit {unit_id} 引用未知 condition_id: "
                    f"{sorted(unknown_conditions)}"
                )
        if missing_units := (
            set(catalog["active_point_ids"]) - set(units_by_point)
        ):
            raise ValueError(
                "活动 ScorePoint 缺少活动 ScoreResponseUnit: "
                f"{sorted(missing_units)}"
            )
        invalid_condition_cardinality = {
            condition_id: catalog["condition_unit_counts"].get(
                condition_id,
                0,
            )
            for condition_id in catalog["active_condition_ids"]
            if catalog["condition_unit_counts"].get(condition_id, 0) != 1
        }
        if invalid_condition_cardinality:
            raise ValueError(
                "每个活动 ScoreCondition 必须由且仅由一个 "
                "ScoreResponseUnit 绑定: "
                f"{invalid_condition_cardinality}"
            )
        for condition_id in catalog["active_condition_ids"]:
            condition = catalog["conditions"][condition_id]
            owner_unit_id = catalog["condition_owner_unit"][condition_id]
            if (
                catalog["unit_owner"].get(owner_unit_id)
                != catalog["condition_owner_point"].get(condition_id)
            ):
                raise ValueError(
                    f"ScoreCondition {condition_id} 与其 "
                    f"ScoreResponseUnit {owner_unit_id} 不属于同一 ScorePoint"
                )
            role = condition.get("condition_role") or "content"
            if role not in {
                "content",
                "evidence",
                "constraint",
                "quality",
                "document",
            }:
                raise ValueError(
                    f"ScoreCondition {condition_id} condition_role 非法: {role}"
                )
            if (
                role == "document"
                and owner_unit_id not in catalog["document_unit_ids"]
            ):
                raise ValueError(
                    f"document condition {condition_id} 必须属于 document "
                    "ScoreResponseUnit"
                )
        if unknown_links := (
            set(catalog["linked_requirement_ids"])
            - set(catalog["requirements"])
        ):
            raise ValueError(
                "ScoreModel 引用未知 Requirement: "
                f"{sorted(unknown_links)}"
            )
        if (
            set(candidate.document_quality_response_unit_ids)
            != set(catalog["document_unit_ids"])
        ):
            raise ValueError(
                "全文质量 ScoreResponseUnit 识别不一致；"
                f"expected={sorted(catalog['document_unit_ids'])}, "
                "actual="
                f"{sorted(candidate.document_quality_response_unit_ids)}"
            )

        primary_by_unit: dict[str, str] = {}
        visible_conditions: list[str] = []
        covered_requirement_ids: set[str] = set()
        node_by_id = {node.local_id: node for node in candidate.nodes}
        visible_unit_ids: set[str] = set()
        for node in candidate.nodes:
            referenced_units = (
                *node.primary_response_unit_ids,
                *node.supporting_response_unit_ids,
            )
            if unknown := set(referenced_units) - set(catalog["units"]):
                raise ValueError(
                    f"章节 {node.local_id} 引用未知 ScoreResponseUnit: "
                    f"{sorted(unknown)}"
                )
            if document_visible := (
                set(referenced_units) & set(catalog["document_unit_ids"])
            ):
                raise ValueError(
                    "全文质量 ScoreResponseUnit 不得绑定可见章节: "
                    f"{sorted(document_visible)}"
                )
            if unknown := set(node.score_condition_ids) - set(
                catalog["conditions"]
            ):
                raise ValueError(
                    f"章节 {node.local_id} 引用未知 condition_id: "
                    f"{sorted(unknown)}"
                )
            if unknown := set(node.requirement_ids) - set(
                catalog["active_requirement_ids"]
            ):
                raise ValueError(
                    f"章节 {node.local_id} 引用未知或非活动 Requirement: "
                    f"{sorted(unknown)}"
                )
            visible_conditions.extend(node.score_condition_ids)
            covered_requirement_ids.update(node.requirement_ids)
            visible_unit_ids.update(referenced_units)
            for unit_id in node.primary_response_unit_ids:
                if unit_id in primary_by_unit:
                    raise ValueError(
                        f"ScoreResponseUnit {unit_id} 出现多个 primary 章节"
                    )
                primary_by_unit[unit_id] = node.local_id
        if set(primary_by_unit) != set(catalog["section_unit_ids"]):
            raise ValueError(
                "目录 primary ScoreResponseUnit 覆盖不完整；"
                "missing="
                f"{sorted(set(catalog['section_unit_ids']) - set(primary_by_unit))}, "
                "extra="
                f"{sorted(set(primary_by_unit) - set(catalog['section_unit_ids']))}"
            )
        if len(visible_conditions) != len(set(visible_conditions)):
            raise ValueError(
                "同一 condition_id 不得由多个可见章节重复声明"
            )
        if set(visible_conditions) != set(catalog["visible_condition_ids"]):
            raise ValueError(
                "目录未精确覆盖可见评分条件；"
                "missing="
                f"{sorted(set(catalog['visible_condition_ids']) - set(visible_conditions))}, "
                "extra="
                f"{sorted(set(visible_conditions) - set(catalog['visible_condition_ids']))}"
            )
        if missing_requirements := (
            set(catalog["required_requirement_ids"])
            - covered_requirement_ids
        ):
            raise ValueError(
                "目录遗漏评分关联或 blocking Requirement: "
                f"{sorted(missing_requirements)}"
            )

        children: dict[str, list[str]] = {}
        for node in candidate.nodes:
            if node.parent_local_id is not None:
                children.setdefault(node.parent_local_id, []).append(
                    node.local_id
                )

        if request.document_mode == "auto_outline":
            def root_for(local_id: str) -> str | None:
                cursor = local_id
                seen: set[str] = set()
                while cursor in node_by_id and node_by_id[cursor].parent_local_id is not None:
                    if cursor in seen:
                        return None
                    seen.add(cursor)
                    cursor = str(node_by_id[cursor].parent_local_id)
                return cursor if cursor in node_by_id else None

            def group_subject(title: str) -> str:
                label = str(title or "")
                label = re.sub(r"[（(][^）)]*(?:分|明标|暗标)[^）)]*[）)]", "", label)
                return re.sub(r"[\s：:；;、，,。.\-—]|明标|暗标", "", label)

            groups = [
                group
                for group in request.score_model.get("groups", [])
                if any(
                    catalog["points"][catalog["unit_owner"][unit_id]].get("group_id")
                    == group.get("group_id")
                    for unit_id in catalog["section_unit_ids"]
                )
            ]
            roots = sorted(
                (
                    node
                    for node in candidate.nodes
                    if node.parent_local_id is None
                ),
                key=lambda node: node.order,
            )
            if len(roots) != len(groups) or [node.title for node in roots] != [
                str(group.get("title") or "") for group in groups
            ]:
                raise ValueError(
                    "自动目录的评分组根章节必须与 ScoreModel.groups 的可见组一一对应且顺序一致"
                )
            for group in groups:
                group_id = str(group.get("group_id") or "")
                group_units = {
                    unit_id
                    for unit_id in catalog["section_unit_ids"]
                    if catalog["points"][catalog["unit_owner"][unit_id]].get("group_id")
                    == group_id
                }
                group_roots = {
                    root_for(primary_by_unit[unit_id])
                    for unit_id in group_units
                    if unit_id in primary_by_unit
                }
                if len(group_roots) != 1 or None in group_roots:
                    raise ValueError(
                        f"评分组 {group.get('title')} 的目录根缺失或混入其他评分组"
                    )
                for unit_id in group_units:
                    primary_id = primary_by_unit.get(unit_id)
                    if primary_id is None:
                        continue
                    point = catalog["points"][catalog["unit_owner"][unit_id]]
                    expected_path = [
                        str(title).strip()
                        for title in (point.get("outline_path") or [])
                        if str(title).strip()
                    ]
                    if (
                        expected_path
                        and outline_structure_key(expected_path[0])
                        == outline_structure_key(str(group.get("title") or ""))
                    ):
                        expected_path.pop(0)
                    compact_path: list[str] = []
                    for label in expected_path:
                        text = str(label).strip()
                        if text and (
                            not compact_path
                            or outline_structure_key(compact_path[-1])
                            != outline_structure_key(text)
                        ):
                            compact_path.append(text)
                    if not compact_path:
                        continue
                    chain: list[str] = []
                    cursor = primary_id
                    while cursor in node_by_id:
                        chain.append(node_by_id[cursor].title)
                        parent_id = node_by_id[cursor].parent_local_id
                        if parent_id is None:
                            break
                        cursor = parent_id
                    actual_path = list(reversed(chain))[1:]
                    if [outline_structure_key(title) for title in actual_path] != [
                        outline_structure_key(title) for title in compact_path
                    ]:
                        raise ValueError(
                            f"ScoreResponseUnit {unit_id} 未保留 outline_path: {compact_path}"
                        )

        def subtree(root_id: str) -> set[str]:
            found: set[str] = set()
            pending = [root_id]
            while pending:
                current = pending.pop()
                if current in found:
                    continue
                found.add(current)
                pending.extend(children.get(current, []))
            return found

        for unit_id in catalog["section_unit_ids"]:
            primary_node = primary_by_unit.get(unit_id)
            if primary_node is None:
                continue
            required_for_unit = (
                set(
                    catalog["linked_requirements_by_unit"].get(
                        unit_id,
                        set(),
                    )
                )
                & set(catalog["active_requirement_ids"])
            )
            covered_in_subtree = {
                requirement_id
                for node_id in subtree(primary_node)
                for requirement_id in node_by_id[
                    node_id
                ].requirement_ids
            }
            if missing := required_for_unit - covered_in_subtree:
                raise ValueError(
                    f"ScoreResponseUnit {unit_id} 的关联 Requirement "
                    "未进入其主责章节子树: "
                    f"{sorted(missing)}"
                )

        for condition_id in catalog["visible_condition_ids"]:
            unit_id = catalog["condition_owner_unit"].get(condition_id)
            primary_node = primary_by_unit.get(unit_id or "")
            if unit_id is None or primary_node is None:
                raise ValueError(
                    f"condition_id {condition_id} 缺少唯一 "
                    "ScoreResponseUnit/primary 链路"
                )
            subtree_node_ids = subtree(primary_node)
            covered_nodes = {
                node_id
                for node_id in subtree_node_ids
                if condition_id
                in node_by_id[node_id].score_condition_ids
            }
            if not covered_nodes:
                raise ValueError(
                    f"condition_id {condition_id} 未进入其 "
                    f"ScoreResponseUnit {unit_id} "
                    "主责章节子树"
                )
            role = (
                catalog["conditions"][condition_id].get(
                    "condition_role"
                )
                or "content"
            )
            sectionable_quality = is_sectionable_quality_condition(
                catalog["conditions"][condition_id]
            )
            if (
                role == "quality"
                and not sectionable_quality
                and covered_nodes != {primary_node}
            ):
                raise ValueError(
                    f"quality condition {condition_id} 必须绑定 Unit "
                    f"{unit_id} 的 primary 章节并转为写作要求，"
                    "不得单独生成空洞质量章节"
                )
        template = request.template_structure
        if request.document_mode == "template_strict" and template is None:
            raise ValueError(
                "template_strict 模式必须提供 TemplateStructureContract"
            )
        if request.document_mode == "auto_outline" and template is not None:
            raise ValueError(
                "auto_outline 模式不得提供 TemplateStructureContract"
            )
        if template is None:
            if any(node.template_slot_ids for node in candidate.nodes):
                raise ValueError("auto_outline 模式不得声明模板 Slot")
        else:
            expected_nodes = sorted(
                template.get("nodes", []),
                key=lambda item: int(item.get("order", 0)),
            )
            actual_nodes = sorted(candidate.nodes, key=lambda item: item.order)
            if len(expected_nodes) != len(actual_nodes):
                raise ValueError("严格模板模式的章节节点数量发生变化")
            local_to_template = {
                node.local_id: expected
                for node, expected in zip(
                    actual_nodes,
                    expected_nodes,
                    strict=True,
                )
            }
            slots_by_node: dict[str, set[str]] = {}
            for slot in template.get("slots", []):
                slots_by_node.setdefault(
                    str(slot.get("node_id")),
                    set(),
                ).add(str(slot.get("slot_id")))
            for actual, expected in zip(
                actual_nodes,
                expected_nodes,
                strict=True,
            ):
                if (
                    actual.order != int(expected.get("order", 0))
                    or actual.title != str(expected.get("title") or "")
                ):
                    raise ValueError("严格模板标题或顺序发生变化")
                expected_parent = (
                    local_to_template[
                        actual.parent_local_id
                    ].get("node_id")
                    if actual.parent_local_id
                    else None
                )
                if expected_parent != expected.get("parent_node_id"):
                    raise ValueError("严格模板父子层级发生变化")
                expected_slots = slots_by_node.get(
                    str(expected.get("node_id")),
                    set(),
                )
                if set(actual.template_slot_ids) != expected_slots:
                    raise ValueError("严格模板 Slot 发生变化")
        if candidate.review_status == "blocked":
            raise ValueError("章节候选标记为 blocked")
