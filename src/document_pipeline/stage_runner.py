from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel

from control_plane import ControlStore, WorkspaceContext

from .artifact_promotion import (
    AgentProposalSandbox,
    ArtifactPromotionService,
    GateService,
    build_declared_dependency_fingerprint,
    validate_and_record,
)
from .artifact_registry import ARTIFACT_REGISTRY
from .canonicalization import canonical_hash, canonical_json, canonical_payload_hash
from .chapter_blueprint import (
    audit_chapter_blueprint,
    load_promoted_chapter_blueprint,
    partition_chapter_blueprint_audit,
)
from .content_scheduler import ContentUnitScheduler
from .content_writer import ContentWriter
from .contracts import (
    ChapterBlueprint,
    InputRole,
    ProjectModel,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreCondition,
    ScoreModel,
    ScorePoint,
    ScoreResponseUnit,
    SourceAnchor,
    SourceBlock,
    TemplateStructureContract,
)
from .document_contract import DocumentContractCompiler
from .document_planner import DocumentPlanner
from .deterministic_outline import build_deterministic_outline_candidate
from .inference_receipts import InferenceReceiptService
from .inference_inputs import (
    build_outline_decomposition_input,
    build_project_understanding_input,
    select_planning_source_context,
)
from .inference_runtime import (
    INFERENCE_RUNTIME_REGISTRY,
    InferenceRuntimeMetadata,
)
from .integrator import DocumentIntegrator
from .input_manifest import InputManifestService
from .material_sync import MaterialRequirementsSynchronizer
from .planning_agent import PlanningAgent
from .planning_inference import (
    OUTLINE_CAPABILITY_VERSION,
    OUTLINE_PROMPT_VERSION,
    OUTLINE_SCHEMA_VERSION,
    PROJECT_CAPABILITY_VERSION,
    PROJECT_SCHEMA_VERSION,
    TOPIC_CAPABILITY_VERSION,
    TOPIC_SCHEMA_VERSION,
    ChapterOutlineCandidate,
    ChapterOutlineNodeCandidate,
    FileOutlineFragmentCache,
    LLMOutlineDecompositionProvider,
    LLMProjectUnderstandingProvider,
    LLMTopicDutyPlanningProvider,
    OUTLINE_SKILL_ID,
    OutlineDecompositionInput,
    OutlineDecompositionProvider,
    PlanningInferenceValidationError,
    ProjectFactCandidate,
    ProjectUnderstandingInput,
    ProjectUnderstandingCandidate,
    ProjectUnderstandingProvider,
    ResponseDutyCandidate,
    ResponseTopicCandidate,
    StructuredInferenceResult,
    TopicDutyPlanningCandidate,
    TopicDutyPlanningInput,
    TopicDutyPlanningProvider,
)
from .planning_skill_registry import get_planning_skill
from .pipeline_policy import (
    configured_validation_failure_blocks,
    validation_policy_scope,
)
from .proposals import DependencyRef, InferenceReceiptRef, ProposalEnvelope
from .project_model import load_promoted_project_model
from .quality import CONTENT_QUALITY_PATH, QualityGate
from .renderers.render_verifier import DeliveryVerifier
from .renderers.standard_renderer import StandardRenderer
from .renderers.template_renderer import StrictTemplateRenderer
from .requirement_agent import RequirementAgent
from .requirement_ledger import audit_reverse_coverage, load_promoted_requirement_ledger
from .score_agent import ScoreAgent
from .score_semantic import (
    FileScoreSemanticBatchCache,
    LLMScoreSemanticProvider,
    SCORE_SEMANTIC_CAPABILITY_ID,
    SCORE_SEMANTIC_CAPABILITY_VERSION,
    SCORE_SEMANTIC_SCHEMA_VERSION,
    ScoreSemanticInferenceError,
    ScoreSemanticInferenceResult,
    ScoreSemanticInput,
    ScoreSemanticProvider,
    semantic_coverage_text,
)
from .score_model import (
    audit_score_model,
    load_promoted_score_model,
    partition_score_model_audit,
)
from .topic_graph import load_promoted_topic_graph
from .source_artifacts import require_promoted_source_index
from .source_normalizer import SourceNormalizer
from .template_contract import TemplateContractCompiler
from utils import read_json


def _active_payload_matches(active_artifact: dict, proposal_payload: dict) -> bool:
    """Compare deterministic content while normalising the next revision field."""

    candidate = dict(proposal_payload)
    candidate["revision"] = int(active_artifact["revision"])
    return str(active_artifact["artifact_hash"]) == canonical_payload_hash(candidate)


_INFERENCE_MODE_LLM = "llm"
_INFERENCE_MODE_DETERMINISTIC_TEST = "deterministic_test"
_DETERMINISTIC_TEST_AUTHORITY = object()
_PROJECT_CAPABILITY_ID = "planning.project_understanding"
_TOPIC_CAPABILITY_ID = "planning.topic_duty_plan"
_INFERENCE_CAPABILITY_BY_ARTIFACT = {
    "ScoreModel": SCORE_SEMANTIC_CAPABILITY_ID,
    "ProjectModel": _PROJECT_CAPABILITY_ID,
    "ResponseTopicGraph": _TOPIC_CAPABILITY_ID,
    "ChapterBlueprint": OUTLINE_SKILL_ID,
}
_NONBLOCKING_OUTLINE_VALIDATION_MARKERS = (
    "全文质量 ScoreResponseUnit 识别不一致",
    "标题仅包含空洞质量形容词",
    "目录 primary ScoreResponseUnit 覆盖不完整",
    "出现多个 primary 章节",
    "同一 condition_id 不得由多个可见章节重复声明",
    "目录未精确覆盖可见评分条件",
    "目录遗漏评分关联或 blocking Requirement",
    "的关联 Requirement 未进入其主责章节子树",
    "缺少唯一 ScoreResponseUnit/primary 链路",
    "未进入其 ScoreResponseUnit",
    "quality condition",
    "content/evidence 满分条件必须各自形成可检查章节节点",
    "可成文满分条件必须各自形成可检查章节节点",
    "评分组根章节",
    "目录根缺失或混入其他评分组",
    "outline_path",
)
@dataclass(frozen=True, slots=True)
class _DeterministicInferenceResult:
    candidate: Any
    raw_output: str
    normalized_output: str
    input_snapshot: str
    attempt_count: int
    capability_id: str
    capability_version: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float


class V3StageRunner:
    """The single V3 content execution kernel; unknown stages are errors."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        score_semantic_provider: ScoreSemanticProvider | None = None,
        project_understanding_provider: ProjectUnderstandingProvider | None = None,
        topic_duty_planning_provider: TopicDutyPlanningProvider | None = None,
        outline_decomposition_provider: OutlineDecompositionProvider | None = None,
        _deterministic_test_authority: object | None = None,
    ) -> None:
        self.context = context
        requested_mode = os.environ.get(
            "BID_AGENT_INFERENCE_MODE",
            _INFERENCE_MODE_LLM,
        ).strip().lower()
        if _deterministic_test_authority is _DETERMINISTIC_TEST_AUTHORITY:
            mode = _INFERENCE_MODE_DETERMINISTIC_TEST
        elif requested_mode == _INFERENCE_MODE_DETERMINISTIC_TEST:
            raise ValueError(
                "BID_AGENT_INFERENCE_MODE 不能启用 deterministic_test；"
                "规则候选仅能由显式测试构造器注入，生产链路禁止规则回退"
            )
        elif requested_mode == _INFERENCE_MODE_LLM:
            mode = requested_mode
        else:
            raise ValueError(
                "BID_AGENT_INFERENCE_MODE 只允许 llm；"
                "测试规则链路必须使用 V3StageRunner.for_deterministic_tests"
            )
        self.inference_mode = mode
        self._score_semantic_provider = score_semantic_provider
        self._project_understanding_provider = project_understanding_provider
        self._topic_duty_planning_provider = topic_duty_planning_provider
        self._outline_decomposition_provider = outline_decomposition_provider
        self.validation_failure_blocks_pipeline = (
            configured_validation_failure_blocks()
        )
        self._stage_warnings: dict[str, list[dict[str, Any]]] = {}
        self._generation_chapter_ids: list[str] = []

    def set_generation_scope(self, chapter_ids: list[str] | None) -> None:
        self._generation_chapter_ids = [
            str(item).strip()
            for item in (chapter_ids or [])
            if str(item).strip()
        ]

    def validation_policy_scope(self):
        return validation_policy_scope(
            self.validation_failure_blocks_pipeline
        )

    def _add_stage_warning(
        self,
        stage: str,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._stage_warnings.setdefault(stage, []).append(
            {
                "code": code,
                "message": message,
                "details": dict(details or {}),
                "policy_override": "continue_with_warnings",
            }
        )

    def consume_stage_warnings(self, stage: str) -> list[dict[str, Any]]:
        return self._stage_warnings.pop(stage, [])

    @classmethod
    def for_deterministic_tests(
        cls,
        context: WorkspaceContext,
        *,
        score_semantic_provider: ScoreSemanticProvider | None = None,
        project_understanding_provider: ProjectUnderstandingProvider | None = None,
        topic_duty_planning_provider: TopicDutyPlanningProvider | None = None,
        outline_decomposition_provider: OutlineDecompositionProvider | None = None,
    ) -> Self:
        """Construct the non-production deterministic harness explicitly."""

        return cls(
            context,
            score_semantic_provider=score_semantic_provider,
            project_understanding_provider=project_understanding_provider,
            topic_duty_planning_provider=topic_duty_planning_provider,
            outline_decomposition_provider=outline_decomposition_provider,
            _deterministic_test_authority=_DETERMINISTIC_TEST_AUTHORITY,
        )

    @property
    def _uses_deterministic_score(self) -> bool:
        return (
            self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
            and self._score_semantic_provider is None
        )

    @property
    def _uses_deterministic_project(self) -> bool:
        return (
            self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
            and self._project_understanding_provider is None
        )

    @property
    def _uses_deterministic_topic(self) -> bool:
        return (
            self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
            and self._topic_duty_planning_provider is None
        )

    @property
    def _uses_deterministic_outline(self) -> bool:
        return (
            self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
            and self._outline_decomposition_provider is None
        )

    def _score_provider(self) -> ScoreSemanticProvider:
        if self._score_semantic_provider is None:
            self._score_semantic_provider = LLMScoreSemanticProvider(
                batch_cache=FileScoreSemanticBatchCache(
                    self.context.root
                    / "workspace"
                    / "v3"
                    / "cache"
                    / "score_semantic"
                )
            )
        return self._score_semantic_provider

    def _project_provider(self) -> ProjectUnderstandingProvider:
        if self._project_understanding_provider is None:
            self._project_understanding_provider = LLMProjectUnderstandingProvider()
        return self._project_understanding_provider

    def _topic_provider(self) -> TopicDutyPlanningProvider:
        if self._topic_duty_planning_provider is None:
            self._topic_duty_planning_provider = LLMTopicDutyPlanningProvider()
        return self._topic_duty_planning_provider

    def _outline_provider(self) -> OutlineDecompositionProvider:
        if self._outline_decomposition_provider is None:
            self._outline_decomposition_provider = LLMOutlineDecompositionProvider(
                batch_cache=FileOutlineFragmentCache(
                    self.context.root
                    / "workspace"
                    / "v3"
                    / "cache"
                    / "chapter_outline"
                )
            )
        return self._outline_decomposition_provider

    def _record_stage_output(
        self,
        operation_id: str | None,
        stage: str,
        *,
        phase: str,
        products: list[dict[str, Any]],
    ) -> None:
        """Publish non-authoritative progress products for the active command."""

        if not operation_id:
            return
        from control_plane import ControlStore

        store = ControlStore(self.context)
        latest = store.latest_stage_run(operation_id, stage)
        if latest is None or str(latest.get("status") or "") not in {
            "queued",
            "running",
        }:
            return
        store.record_stage_run(
            operation_id,
            stage,
            "running",
            disposition=f"v3_outline_phase:{phase}",
            output={
                "phase": phase,
                "products": products,
            },
        )

    def _active_artifact_dependencies_are_current(
        self,
        artifact_kind: str,
    ) -> bool:
        store = ControlStore(self.context)
        active = store.v3_active_artifact(artifact_kind)
        if active is None:
            return False
        proposal = store.v3_proposal(str(active.get("proposal_id") or ""))
        if proposal is None:
            return False
        for dependency in proposal.get("declared_dependencies") or []:
            if not isinstance(dependency, dict):
                return False
            kind = str(dependency.get("artifact_kind") or "")
            current = store.v3_active_artifact(kind)
            if (
                current is None
                or int(current.get("revision") or 0)
                != int(dependency.get("expected_revision") or 0)
                or str(current.get("artifact_hash") or "")
                != str(dependency.get("expected_hash") or "")
            ):
                return False
        return (
            str(active.get("artifact_hash") or "")
            == canonical_payload_hash(active.get("payload") or {})
        )

    def can_reuse_stage(self, stage: str) -> bool:
        """Return whether an outline stage can be skipped before execution."""

        if stage == "build_requirement_ledger":
            return self._active_artifact_dependencies_are_current(
                "RequirementLedger"
            )
        if stage == "analyze_scores":
            if not self._active_artifact_dependencies_are_current("ScoreModel"):
                return False
            active = ControlStore(self.context).v3_active_artifact("ScoreModel")
            if active is None:
                return False
            points = (active.get("payload") or {}).get("points") or []
            if not points:
                prompt_version = (
                    f"{SCORE_SEMANTIC_CAPABILITY_ID}.empty_input.v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "deterministic_test", "version": prompt_version}
                )
                schema_version = "v3.score_semantic.empty.v1"
                provider_fingerprint = self._deterministic_provider_fingerprint(
                    SCORE_SEMANTIC_CAPABILITY_ID
                )
                model_fingerprint = (
                    f"deterministic_structure:"
                    f"{SCORE_SEMANTIC_CAPABILITY_ID}:empty_input:v1"
                )
                temperature = 0.0
            elif self._uses_deterministic_score:
                prompt_version = (
                    f"{SCORE_SEMANTIC_CAPABILITY_ID}.deterministic_test.v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "deterministic_test", "version": prompt_version}
                )
                schema_version = SCORE_SEMANTIC_SCHEMA_VERSION
                provider_fingerprint = self._deterministic_provider_fingerprint(
                    SCORE_SEMANTIC_CAPABILITY_ID
                )
                model_fingerprint = (
                    f"deterministic_test:{SCORE_SEMANTIC_CAPABILITY_ID}:v1"
                )
                temperature = 0.0
            else:
                provider = self._score_provider()
                prompt_version = provider.prompt_version
                prompt_hash = provider.prompt_hash
                schema_version = provider.schema_version
                provider_fingerprint = provider.provider_fingerprint
                model_fingerprint = provider.model_fingerprint
                temperature = provider.temperature
            return self._active_inference_artifact_is_current(
                "ScoreModel",
                capability_version=SCORE_SEMANTIC_CAPABILITY_VERSION,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                output_schema_version=schema_version,
                provider_fingerprint=provider_fingerprint,
                model_fingerprint=model_fingerprint,
                temperature=temperature,
            )
        if stage == "compile_chapter_blueprint":
            if not self._active_artifact_dependencies_are_current(
                "ChapterBlueprint"
            ):
                return False
            template_structure = self._template_structure()
            optional_dependencies = (
                ("TemplateStructureContract",)
                if template_structure is not None
                else ()
            )
            registration = get_planning_skill(
                OUTLINE_SKILL_ID,
                caller_role="planning_agent",
            )
            if self._uses_deterministic_outline:
                prompt_version = (
                    f"{OUTLINE_SKILL_ID}.deterministic_test.v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "deterministic_test", "version": prompt_version}
                )
                provider_fingerprint = self._deterministic_provider_fingerprint(
                    OUTLINE_SKILL_ID
                )
                model_fingerprint = (
                    f"deterministic_test:{OUTLINE_SKILL_ID}:v1"
                )
                temperature = 0.0
            elif template_structure is not None:
                prompt_version = (
                    f"{OUTLINE_SKILL_ID}.template_projection.v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "template_projection", "version": prompt_version}
                )
                provider_fingerprint = self._deterministic_provider_fingerprint(
                    f"{OUTLINE_SKILL_ID}.template_projection"
                )
                model_fingerprint = (
                    f"program_template_projection:{OUTLINE_SKILL_ID}:v1"
                )
                temperature = 0.0
            else:
                provider = self._outline_provider()
                prompt_version = provider.prompt_version
                prompt_hash = provider.prompt_hash
                provider_fingerprint = provider.provider_fingerprint
                model_fingerprint = provider.model_fingerprint
                temperature = provider.temperature
            current = self._active_inference_artifact_is_current(
                "ChapterBlueprint",
                capability_version=registration.version,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                output_schema_version=registration.schema_version,
                provider_fingerprint=provider_fingerprint,
                model_fingerprint=model_fingerprint,
                temperature=temperature,
                optional_kinds=optional_dependencies,
            )
            if current or self._uses_deterministic_outline or template_structure is not None:
                return current
            (
                fallback_prompt_version,
                fallback_prompt_hash,
                fallback_provider_fingerprint,
                fallback_model_fingerprint,
            ) = self._outline_fallback_runtime_metadata()
            return self._active_inference_artifact_is_current(
                "ChapterBlueprint",
                capability_version=registration.version,
                prompt_version=fallback_prompt_version,
                prompt_hash=fallback_prompt_hash,
                output_schema_version=registration.schema_version,
                provider_fingerprint=fallback_provider_fingerprint,
                model_fingerprint=fallback_model_fingerprint,
                temperature=0.0,
                optional_kinds=optional_dependencies,
            )
        return False

    @staticmethod
    def _score_structure_product(score_model: ScoreModel) -> dict[str, Any]:
        return {
            "kind": "ScoreStructureDraft",
            "label": "评分结构解析结果",
            "status": "ready",
            "summary": {
                "group_count": len(score_model.groups),
                "score_rule_count": len(score_model.points),
                "total_points": score_model.total_points,
            },
            "items": [
                {
                    "id": point.score_point_id,
                    "title": point.title,
                    "max_points": point.max_points,
                    "level_count": len(point.scoring_levels),
                }
                for point in score_model.points[:100]
            ],
        }

    @staticmethod
    def _score_semantic_product(
        score_model: ScoreModel,
        *,
        warnings: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        audit_warnings = list(
            dict.fromkeys(
                str(item).strip()
                for item in warnings
                if str(item).strip()
            )
        )
        return {
            "kind": "ScoreSemanticResult",
            "label": "评分语义理解结果",
            "status": "warning" if audit_warnings else "ready",
            "warnings": audit_warnings,
            "summary": {
                "score_point_count": len(score_model.points),
                "response_unit_count": sum(
                    len(point.response_units) for point in score_model.points
                ),
                "condition_count": sum(
                    len(point.score_conditions) for point in score_model.points
                ),
                "warning_count": len(audit_warnings),
            },
            "items": [
                {
                    "id": point.score_point_id,
                    "title": point.title,
                    "response_units": [
                        unit.title for unit in point.response_units
                    ],
                    "full_score_conditions": list(
                        point.full_score_conditions
                    ),
                    "review_status": point.review_status,
                }
                for point in score_model.points[:100]
            ],
        }

    @staticmethod
    def _mark_score_model_needs_review(score_model: ScoreModel) -> ScoreModel:
        return score_model.model_copy(
            update={
                "points": [
                    point.model_copy(
                        update={
                            "review_status": (
                                point.review_status
                                if point.review_status == "blocked"
                                else "needs_review"
                            ),
                            "response_units": [
                                unit.model_copy(
                                    update={
                                        "review_status": (
                                            unit.review_status
                                            if unit.review_status == "blocked"
                                            else "needs_review"
                                        )
                                    }
                                )
                                for unit in point.response_units
                            ],
                            "score_conditions": [
                                condition.model_copy(
                                    update={
                                        "review_status": (
                                            condition.review_status
                                            if condition.review_status == "blocked"
                                            else "needs_review"
                                        )
                                    }
                                )
                                for condition in point.score_conditions
                            ],
                        }
                    )
                    for point in score_model.points
                ]
            }
        )

    @staticmethod
    def _project_product(project: ProjectModel) -> dict[str, Any]:
        return {
            "kind": "ProjectModel",
            "label": "项目整体理解",
            "status": "ready",
            "summary": {
                "goal_count": len(project.goals),
                "scope_count": len(project.scope),
                "work_package_count": len(project.work_packages),
                "fact_count": len(project.confirmed_facts),
            },
            "items": {
                "identity": dict(project.identity),
                "goals": list(project.goals[:20]),
                "scope": list(project.scope[:20]),
                "work_packages": list(project.work_packages[:30]),
            },
        }

    @staticmethod
    def _topic_graph_product(graph: ResponseTopicGraph) -> dict[str, Any]:
        duty_count_by_topic: dict[str, int] = {}
        for duty in graph.duties:
            duty_count_by_topic[duty.topic_id] = (
                duty_count_by_topic.get(duty.topic_id, 0) + 1
            )
        return {
            "kind": "ResponseTopicGraph",
            "label": "Topic/Duty 规划结果",
            "status": "ready",
            "summary": {
                "topic_count": len(graph.topics),
                "duty_count": len(graph.duties),
                "edge_count": len(graph.edges),
            },
            "items": [
                {
                    "id": topic.topic_id,
                    "title": topic.canonical_name,
                    "intent": topic.intent,
                    "duty_count": duty_count_by_topic.get(
                        topic.topic_id,
                        0,
                    ),
                }
                for topic in graph.topics[:100]
            ],
        }

    @staticmethod
    def _blueprint_product(
        blueprint: ChapterBlueprint,
    ) -> dict[str, Any]:
        warnings = [
            str(item)
            for item in blueprint.coverage_summary.get(
                "program_audit_warnings",
                [],
            )
            if str(item).strip()
        ]
        return {
            "kind": "ChapterBlueprint",
            "label": "章节拆分 Skill 产物",
            "status": "warning" if warnings else "ready",
            "summary": {
                "chapter_count": len(blueprint.nodes),
                "primary_response_unit_count": len(
                    {
                        unit_id
                        for node in blueprint.nodes
                        for unit_id in node.primary_response_unit_ids
                    }
                ),
                "supporting_response_unit_count": len(
                    {
                        unit_id
                        for node in blueprint.nodes
                        for unit_id in node.supporting_response_unit_ids
                    }
                ),
                "score_condition_count": len(
                    {
                        condition_id
                        for node in blueprint.nodes
                        for condition_id in node.score_condition_ids
                    }
                ),
                "requirement_count": len(
                    {
                        requirement_id
                        for node in blueprint.nodes
                        for requirement_id in node.requirement_ids
                    }
                ),
                "quality_gate_count": len(
                    blueprint.document_quality_gates
                ),
                "warning_count": len(warnings),
                "outline_batch_count": int(
                    blueprint.coverage_summary.get("outline_batch_count", 0)
                ),
                "outline_batch_generated_count": int(
                    blueprint.coverage_summary.get(
                        "outline_batch_generated_count",
                        0,
                    )
                ),
                "outline_batch_reused_count": int(
                    blueprint.coverage_summary.get(
                        "outline_batch_reused_count",
                        0,
                    )
                ),
                "outline_batch_failed_count": int(
                    blueprint.coverage_summary.get(
                        "outline_batch_failed_count",
                        0,
                    )
                ),
            },
            "warnings": warnings,
            "items": [
                {
                    "id": node.chapter_id,
                    "title": node.title,
                    "template_level": node.template_level,
                    "parent_id": node.parent_chapter_id,
                    "score_condition_count": len(
                        node.score_condition_ids
                    ),
                    "primary_response_unit_count": len(
                        node.primary_response_unit_ids
                    ),
                    "supporting_response_unit_count": len(
                        node.supporting_response_unit_ids
                    ),
                    "requirement_count": len(node.requirement_ids),
                }
                for node in blueprint.nodes[:200]
            ],
        }

    @staticmethod
    def _deterministic_provider_fingerprint(capability_id: str) -> str:
        return canonical_hash(
            {
                "adapter": "bid_agent.internal_deterministic_test",
                "capability_id": capability_id,
                "version": "v1",
            }
        )

    @staticmethod
    def _deterministic_result(
        *,
        capability_id: str,
        schema_version: str,
        candidate: Any,
        input_value: Any,
        capability_version: str = "1.0.0",
        prompt_version: str | None = None,
        model_fingerprint: str | None = None,
        provider_fingerprint: str | None = None,
        execution_mode: str = "deterministic_test",
    ) -> _DeterministicInferenceResult:
        version = prompt_version or f"{capability_id}.deterministic_test.v1"
        candidate_value = (
            candidate.model_dump(mode="json")
            if isinstance(candidate, BaseModel)
            else candidate
        )
        input_snapshot = canonical_json(
            input_value.model_dump(mode="json")
            if isinstance(input_value, BaseModel)
            else input_value
        )
        normalized = canonical_json(candidate_value)
        return _DeterministicInferenceResult(
            candidate=candidate,
            raw_output=normalized,
            normalized_output=normalized,
            input_snapshot=input_snapshot,
            attempt_count=1,
            capability_id=capability_id,
            capability_version=capability_version,
            prompt_version=version,
            prompt_hash=canonical_hash(
                {"mode": execution_mode, "version": version}
            ),
            schema_version=schema_version,
            provider_fingerprint=(
                provider_fingerprint
                or V3StageRunner._deterministic_provider_fingerprint(
                    capability_id
                )
            ),
            model_fingerprint=(
                model_fingerprint or f"deterministic_test:{capability_id}:v1"
            ),
            temperature=0.0,
        )

    def _active_dependency_snapshot(
        self,
        artifact_kind: str,
        *,
        optional_kinds: tuple[str, ...] = (),
    ) -> tuple[dict[str, dict[str, Any]], list[DependencyRef]]:
        store = ControlStore(self.context)
        registration = ARTIFACT_REGISTRY.get(artifact_kind)
        snapshot: dict[str, dict[str, Any]] = {}
        declared: list[DependencyRef] = []
        for kind in (*registration.dependency_kinds, *optional_kinds):
            if kind in snapshot:
                continue
            active = store.v3_active_artifact(kind)
            if active is None:
                if kind in registration.dependency_kinds:
                    raise ValueError(f"{artifact_kind} 缺少已晋级依赖 {kind}")
                continue
            entry = {
                "artifact_kind": kind,
                "artifact_id": str(active["artifact_id"]),
                "revision": int(active["revision"]),
                "artifact_hash": str(active["artifact_hash"]),
            }
            snapshot[kind] = entry
            declared.append(
                DependencyRef(
                    artifact_kind=kind,
                    expected_revision=entry["revision"],
                    expected_hash=entry["artifact_hash"],
                )
            )
        return snapshot, declared

    def _active_inference_artifact_is_current(
        self,
        artifact_kind: str,
        *,
        capability_version: str,
        prompt_version: str,
        prompt_hash: str,
        output_schema_version: str,
        provider_fingerprint: str,
        model_fingerprint: str,
        temperature: float,
        optional_kinds: tuple[str, ...] = (),
    ) -> bool:
        INFERENCE_RUNTIME_REGISTRY.publish(
            self.context,
            artifact_kind,
            InferenceRuntimeMetadata(
                runtime_mode=self.inference_mode,
                capability_id=_INFERENCE_CAPABILITY_BY_ARTIFACT[artifact_kind],
                capability_version=capability_version,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                provider_fingerprint=provider_fingerprint,
                model_fingerprint=model_fingerprint,
                output_schema_version=output_schema_version,
                temperature=temperature,
            ),
        )
        store = ControlStore(self.context)
        active = store.v3_active_artifact(artifact_kind)
        if active is None:
            return False
        snapshot, _ = self._active_dependency_snapshot(
            artifact_kind,
            optional_kinds=optional_kinds,
        )
        expected = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=snapshot,
            artifact_kind=artifact_kind,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )
        if str(active.get("dependency_fingerprint") or "") != expected:
            return False
        proposal = store.v3_proposal(str(active.get("proposal_id") or ""))
        refs = (proposal or {}).get("inference_receipt_refs") or []
        if len(refs) != 1:
            return False
        ref = refs[0]
        receipt = store.v3_inference_receipt(str(ref.get("receipt_id") or ""))
        return bool(
            receipt is not None
            and str(receipt.get("receipt_hash") or "")
            == str(ref.get("receipt_hash") or "")
            and str(receipt.get("capability_id") or "")
            == _INFERENCE_CAPABILITY_BY_ARTIFACT[artifact_kind]
            and str(receipt.get("capability_version") or "")
            == capability_version
            and str(receipt.get("prompt_version") or "") == prompt_version
            and str(receipt.get("prompt_hash") or "") == prompt_hash
            and str(receipt.get("output_schema_version") or "")
            == output_schema_version
            and str(receipt.get("provider_fingerprint") or "")
            == provider_fingerprint
            and str(receipt.get("model_fingerprint") or "") == model_fingerprint
            and float(receipt.get("temperature") or 0.0) == float(temperature)
            and str(receipt.get("compiled_payload_hash") or "")
            == canonical_payload_hash(active["payload"])
        )

    def _require_active_inference_artifact(self, artifact_kind: str) -> dict:
        from control_plane import ControlPlaneError

        store = ControlStore(self.context)
        active = store.v3_active_artifact(artifact_kind)
        if active is None:
            raise ControlPlaneError(
                "V3_INFERENCE_DEPENDENCY_MISSING",
                f"缺少已晋级推理 Artifact: {artifact_kind}",
                status_code=409,
            )
        proposal = store.v3_proposal(str(active.get("proposal_id") or ""))
        refs = (proposal or {}).get("inference_receipt_refs") or []
        if len(refs) != 1:
            raise ControlPlaneError(
                "V3_INFERENCE_RECEIPT_REQUIRED",
                f"{artifact_kind} 缺少唯一、真实的 InferenceReceipt",
                status_code=409,
            )
        ref = refs[0]
        receipt = store.v3_inference_receipt(str(ref.get("receipt_id") or ""))
        expected_capability = _INFERENCE_CAPABILITY_BY_ARTIFACT[artifact_kind]
        if (
            receipt is None
            or str(receipt.get("receipt_hash") or "")
            != str(ref.get("receipt_hash") or "")
            or str(receipt.get("capability_id") or "") != expected_capability
            or str(receipt.get("compiled_payload_hash") or "")
            != canonical_payload_hash(active["payload"])
        ):
            raise ControlPlaneError(
                "V3_INFERENCE_RECEIPT_INVALID",
                f"{artifact_kind} 的 InferenceReceipt 不存在、失配或未绑定当前 payload",
                status_code=409,
            )
        return active

    def _proposal_from_inference(
        self,
        *,
        artifact_kind: str,
        producer_role: str,
        payload: ScoreModel | ProjectModel | ResponseTopicGraph | ChapterBlueprint,
        base_revision: int,
        operation_id: str,
        result: ScoreSemanticInferenceResult
        | StructuredInferenceResult[Any]
        | _DeterministicInferenceResult,
        input_snapshot: Any,
        optional_dependency_kinds: tuple[str, ...] = (),
        capability_version: str,
    ) -> ProposalEnvelope:
        snapshot, declared = self._active_dependency_snapshot(
            artifact_kind,
            optional_kinds=optional_dependency_kinds,
        )
        payload_value = payload.model_dump(mode="json")
        expected_input_snapshot = canonical_json(
            input_snapshot.model_dump(mode="json")
            if isinstance(input_snapshot, BaseModel)
            else input_snapshot
        )
        if result.input_snapshot != expected_input_snapshot:
            from control_plane import ControlPlaneError

            raise ControlPlaneError(
                "V3_INFERENCE_INPUT_SNAPSHOT_MISMATCH",
                f"{artifact_kind} Provider 返回的 input_snapshot 与实际受控输入不一致",
                status_code=409,
            )
        INFERENCE_RUNTIME_REGISTRY.publish(
            self.context,
            artifact_kind,
            InferenceRuntimeMetadata(
                runtime_mode=self.inference_mode,
                capability_id=result.capability_id,
                capability_version=capability_version,
                prompt_version=result.prompt_version,
                prompt_hash=result.prompt_hash,
                provider_fingerprint=result.provider_fingerprint,
                model_fingerprint=result.model_fingerprint,
                output_schema_version=result.schema_version,
                temperature=result.temperature,
            ),
        )
        receipt_ref = InferenceReceiptService(self.context).record(
            invocation_id=f"{operation_id}:{result.capability_id}",
            capability_id=result.capability_id,
            capability_version=capability_version,
            prompt_version=result.prompt_version,
            prompt_hash=result.prompt_hash,
            provider_fingerprint=result.provider_fingerprint,
            model_fingerprint=result.model_fingerprint,
            temperature=result.temperature,
            output_schema_version=result.schema_version,
            input_artifact_refs=snapshot,
            input_snapshot=expected_input_snapshot,
            raw_output=result.raw_output,
            normalized_candidate=(
                result.candidate.model_dump(mode="json")
                if isinstance(result.candidate, BaseModel)
                else result.candidate
            ),
            compiled_payload=payload_value,
        )
        dependency_fingerprint = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=snapshot,
            artifact_kind=artifact_kind,
            prompt_version=result.prompt_version,
            model_fingerprint=result.model_fingerprint,
        )
        return ProposalEnvelope(
            workspace_id=self.context.workspace_id,
            artifact_kind=artifact_kind,
            producer_role=producer_role,
            operation_id=operation_id,
            base_revision=base_revision,
            declared_dependencies=declared,
            dependency_fingerprint=dependency_fingerprint,
            payload=payload_value,
            cited_source_ids=sorted(payload.source_hashes),
            prompt_version=result.prompt_version,
            model_fingerprint=result.model_fingerprint,
            inference_receipt_refs=[receipt_ref],
        )

    def _validate_gate_promote(
        self,
        proposal: ProposalEnvelope,
        *,
        producer_role: str,
        gate_id: str,
    ) -> None:
        from control_plane import ControlPlaneError

        stored = AgentProposalSandbox(self.context, role=producer_role).submit(proposal)
        proposal_id = str(stored["proposal_id"])
        report = validate_and_record(self.context, proposal_id)
        if not report.passed:
            raise ControlPlaneError(
                "V3_PROPOSAL_INVALID",
                f"{proposal.artifact_kind} Proposal 验证未通过: {report.findings}",
            )
        gate_receipt = GateService(self.context).evaluate(proposal_id, gate_id=gate_id)
        if gate_receipt.verdict != "pass":
            raise ControlPlaneError(
                "V3_GATE_BLOCKED",
                f"{proposal.artifact_kind} 门禁阻断: {gate_receipt.findings}",
            )
        ArtifactPromotionService(self.context).promote(
            proposal_id,
            gate_receipt_ids=[gate_receipt.receipt_id],
        )

    @staticmethod
    def _planning_source_context(
        source_blocks: list[SourceBlock],
        *,
        requirement_chunk_ids: set[str],
        score_chunk_ids: set[str],
    ) -> list[dict[str, Any]]:
        return select_planning_source_context(
            source_blocks,
            requirement_chunk_ids=requirement_chunk_ids,
            score_chunk_ids=score_chunk_ids,
        )

    def _template_structure(self) -> TemplateStructureContract | None:
        active = ControlStore(self.context).v3_active_artifact("TemplateStructureContract")
        if active is None:
            return None
        return TemplateStructureContract.model_validate(active["payload"])

    @staticmethod
    def _deterministic_score_projection(
        structural_model: ScoreModel,
        source_blocks: list[SourceBlock],
        semantic_input: ScoreSemanticInput,
    ) -> ScoreModel:
        """Build a conservative, source-grounded model when semantic inference fails.

        Exact source excerpts are retained whenever they can be located.  A rule
        whose semantic wording cannot be projected exactly still receives a
        response unit, but no invented condition; the semantic audit then exposes
        that omission as a non-blocking review warning.
        """

        source_blocks_by_anchor = {
            (
                block.source_anchor.source_input_id,
                block.source_anchor.chunk_id,
            ): block
            for block in source_blocks
        }
        requirement_ids_by_point = {
            rule.rule_id: list(
                dict.fromkeys(
                    (
                        *rule.linked_requirement_ids,
                        *rule.context_requirement_ids,
                    )
                )
            )
            for rule in semantic_input.rules
        }
        context_requirement_ids_by_point = {
            rule.rule_id: list(rule.context_requirement_ids)
            for rule in semantic_input.rules
        }
        points = []
        for source_point in structural_model.points:
            point = source_point.model_copy(
                update={
                    "context_requirement_ids": (
                        context_requirement_ids_by_point.get(
                            source_point.score_point_id,
                            source_point.context_requirement_ids,
                        )
                    )
                }
            )
            source_conditions = [
                text
                for text in (
                    point.full_score_conditions or [point.criterion]
                )
                if semantic_coverage_text(text)
            ]
            level_ids = [
                f"{point.score_point_id}-L{index:02d}"
                for index, _ in enumerate(point.scoring_levels, start=1)
            ]
            scored_levels = [
                (level_id, level.points)
                for level_id, level in zip(
                    level_ids,
                    point.scoring_levels,
                    strict=True,
                )
                if level.points is not None
            ]
            full_level_id = (
                max(scored_levels, key=lambda item: float(item[1]))[0]
                if scored_levels
                else (level_ids[0] if level_ids else None)
            )
            full_level = (
                point.scoring_levels[level_ids.index(full_level_id)]
                if full_level_id is not None
                else None
            )
            full_level_has_semantic_text = bool(
                full_level is not None
                and semantic_coverage_text(full_level.criterion)
            )
            conditions: list[ScoreCondition] = []
            for text in source_conditions:
                condition_level_id = (
                    full_level_id
                    if full_level_has_semantic_text
                    and full_level is not None
                    and "".join(text.split())
                    in "".join(full_level.criterion.split())
                    else None
                )
                try:
                    (
                        source_anchor,
                        source_excerpt,
                        source_span_start,
                        source_span_end,
                    ) = V3StageRunner._locate_deterministic_condition_source(
                        point,
                        text,
                        source_blocks_by_anchor,
                    )
                except ValueError:
                    # Never manufacture a source span.  The rule-level response
                    # unit remains usable and the missing semantic condition is
                    # surfaced as a review-only audit warning.
                    continue
                token = canonical_hash(
                    {
                        "score_point_id": point.score_point_id,
                        "source_level_id": condition_level_id,
                        "source_anchor": source_anchor.model_dump(
                            mode="json"
                        ),
                        "source_span_start": source_span_start,
                        "source_span_end": source_span_end,
                        "source_excerpt": "".join(
                            source_excerpt.split()
                        ),
                    }
                )[:12]
                conditions.append(
                    ScoreCondition(
                        condition_id=f"{point.score_point_id}-C-{token}",
                        text=text,
                        source_excerpt=source_excerpt,
                        source_level_id=condition_level_id,
                        subject=point.title,
                        response_intent="完整响应该满分条件",
                        source_anchor=source_anchor,
                        source_span_start=source_span_start,
                        source_span_end=source_span_end,
                        confidence=point.confidence,
                        review_status=point.review_status,
                    )
                )
            unit = ScoreResponseUnit(
                unit_id=f"{point.score_point_id}-U01",
                title=point.title,
                outline_path=point.outline_path,
                source_level_ids=level_ids,
                condition_ids=[
                    condition.condition_id for condition in conditions
                ],
                linked_requirement_ids=requirement_ids_by_point.get(
                    point.score_point_id,
                    [],
                ),
                response_scope=point.response_scope,
                response_expectation=point.response_expectation,
                required_evidence_types=point.required_evidence_types,
                confidence=point.confidence,
                review_status=point.review_status,
            )
            points.append(
                point.model_copy(
                    update={
                        "full_score_conditions": [
                            condition.text for condition in conditions
                        ],
                        "score_conditions": conditions,
                        "response_units": [unit],
                    }
                )
            )
        return ScoreModel.model_validate(
            structural_model.model_copy(update={"points": points}).model_dump(
                mode="json"
            )
        )

    @staticmethod
    def _locate_deterministic_condition_source(
        point: ScorePoint,
        text: str,
        source_blocks_by_anchor: dict[tuple[str, str], SourceBlock],
    ) -> tuple[SourceAnchor, str, int, int]:
        normalized_text = "".join(text.split())
        for anchor in point.source_anchors:
            block = source_blocks_by_anchor.get(
                (anchor.source_input_id, anchor.chunk_id)
            )
            if block is None:
                continue
            exact_start = block.content.find(text)
            if exact_start >= 0:
                exact_end = exact_start + len(text)
                return anchor, text, exact_start, exact_end
            source_characters: list[str] = []
            source_indexes: list[int] = []
            for index, character in enumerate(block.content):
                if character.isspace():
                    continue
                source_characters.append(character)
                source_indexes.append(index)
            normalized_source = "".join(source_characters)
            normalized_start = normalized_source.find(normalized_text)
            if normalized_start >= 0 and normalized_text:
                source_start = source_indexes[normalized_start]
                source_end = source_indexes[
                    normalized_start + len(normalized_text) - 1
                ] + 1
                return (
                    anchor,
                    block.content[source_start:source_end],
                    source_start,
                    source_end,
                )
        raise ValueError(
            "无法将评分条件逐字定位到 SourceBlock: "
            f"{point.score_point_id}/{text}"
        )

    @staticmethod
    def _deterministic_project_candidate(
        ledger: RequirementLedger,
        scores: ScoreModel,
    ) -> ProjectUnderstandingCandidate:
        active_requirement_ids = [
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        ]
        score_point_ids = [
            point.score_point_id for point in scores.points
        ]
        return ProjectUnderstandingCandidate(
            facts=[
                ProjectFactCandidate(
                    local_id="deterministic-test-semantic-coverage",
                    statement=(
                        "deterministic_test 仅以显式推断占位验证受控规划链路。"
                    ),
                    classification="inference",
                    upstream_refs=[
                        *(
                            f"RequirementLedger:{requirement_id}"
                            for requirement_id in active_requirement_ids
                        ),
                        *(
                            f"ScoreModel:{score_point_id}"
                            for score_point_id in score_point_ids
                        ),
                    ],
                    requirement_ids=active_requirement_ids,
                    confidence=1.0,
                )
            ]
            if active_requirement_ids or score_point_ids
            else [],
            unknowns=[
                "deterministic_test 模式仅验证受控规划链路，不替代生产大模型项目理解"
            ],
            covered_requirement_ids=active_requirement_ids,
            covered_score_point_ids=score_point_ids,
            review_status="confirmed",
        )

    @staticmethod
    def _deterministic_topic_candidate(
        ledger: RequirementLedger,
        scores: ScoreModel,
        project: ProjectModel,
    ) -> TopicDutyPlanningCandidate:
        root_local_id = "test-project-response"
        topics = [
            ResponseTopicCandidate(
                local_id=root_local_id,
                topic_type="business_domain",
                canonical_name="项目整体响应",
                intent="聚合项目需求、评分责任与响应任务",
                summary="项目整体响应主题",
                upstream_refs=[f"ProjectModel:{project.project_id}"],
                confidence=1.0,
                review_status="confirmed",
            )
        ]
        duties: list[ResponseDutyCandidate] = []
        for index, requirement in enumerate(ledger.requirements, start=1):
            if requirement.status in {"blocked", "waived"}:
                continue
            local_id = f"test-requirement-{index}"
            topics.append(
                ResponseTopicCandidate(
                    local_id=local_id,
                    parent_local_id=root_local_id,
                    topic_type="compliance",
                    canonical_name=requirement.normalized_requirement[:80],
                    intent="响应采购义务",
                    summary=requirement.normalized_requirement,
                    requirement_ids=[requirement.requirement_id],
                    upstream_refs=[
                        f"RequirementLedger:{requirement.requirement_id}"
                    ],
                    confidence=1.0,
                    review_status="confirmed",
                )
            )
            duties.append(
                ResponseDutyCandidate(
                    local_id=f"test-duty-requirement-{index}",
                    topic_local_id=local_id,
                    duty_type="explain",
                    requirement_ids=[requirement.requirement_id],
                    response_expectations=[requirement.response_type],
                    priority=(
                        "blocking"
                        if requirement.severity == "blocking"
                        else "normal"
                    ),
                    confidence=1.0,
                    review_status="confirmed",
                )
            )
        needs_by_score: dict[str, list[str]] = {}
        for need in project.evidence_needs:
            if need.topic_id.startswith("score:"):
                needs_by_score.setdefault(
                    need.topic_id.removeprefix("score:"),
                    [],
                ).append(need.need_id)
        for index, point in enumerate(scores.points, start=1):
            local_id = f"test-score-{index}"
            topics.append(
                ResponseTopicCandidate(
                    local_id=local_id,
                    parent_local_id=root_local_id,
                    topic_type="implementation",
                    canonical_name=point.title,
                    intent="响应评分逻辑",
                    summary=point.criterion,
                    score_point_ids=[point.score_point_id],
                    upstream_refs=[f"ScoreModel:{point.score_point_id}"],
                    confidence=point.confidence,
                    review_status=point.review_status,
                )
            )
            response_units = list(point.response_units) or [None]
            for unit_index, response_unit in enumerate(
                response_units,
                start=1,
            ):
                duties.append(
                    ResponseDutyCandidate(
                        local_id=(
                            f"test-duty-score-{index}-unit-{unit_index}"
                        ),
                        topic_local_id=local_id,
                        duty_type=(
                            "verify" if point.disqualifying else "explain"
                        ),
                        score_point_ids=[point.score_point_id],
                        score_response_unit_ids=(
                            [response_unit.unit_id]
                            if response_unit is not None
                            else []
                        ),
                        response_expectations=[
                            response_unit.response_expectation
                            if response_unit is not None
                            else point.response_expectation
                        ],
                        evidence_need_ids=needs_by_score.get(
                            point.score_point_id,
                            [],
                        ),
                        priority=(
                            "blocking"
                            if point.disqualifying
                            else (
                                "high"
                                if point.max_points
                                and point.max_points >= 10
                                else "normal"
                            )
                        ),
                        confidence=(
                            response_unit.confidence
                            if response_unit is not None
                            else point.confidence
                        ),
                        review_status=(
                            response_unit.review_status
                            if response_unit is not None
                            else point.review_status
                        ),
                    )
                )
        if not duties:
            duties.append(
                ResponseDutyCandidate(
                    local_id="test-duty-project-summary",
                    topic_local_id=root_local_id,
                    duty_type="summarize",
                    response_expectations=["形成项目整体响应说明"],
                    priority="normal",
                    confidence=1.0,
                    review_status="confirmed",
                )
            )
        return TopicDutyPlanningCandidate(
            root_topic_local_ids=[root_local_id],
            topics=topics,
            duties=duties,
            edges=[],
            review_status="confirmed",
        )

    @staticmethod
    def _deterministic_outline_candidate(
        ledger: RequirementLedger,
        scores: ScoreModel,
        template_structure: TemplateStructureContract | None,
    ) -> ChapterOutlineCandidate:
        return build_deterministic_outline_candidate(
            ledger,
            scores,
            template_structure,
        )

        # Kept below temporarily as historical context for older persisted test
        # fixtures.  The shared builder above is the only reachable path.
        visible_response_unit_ids = [
            unit.unit_id
            for point in scores.points
            if point.review_status != "blocked"
            for unit in point.response_units
            if unit.review_status != "blocked"
            and unit.response_scope == "section"
        ]
        quality_response_unit_ids = [
            unit.unit_id
            for point in scores.points
            if point.review_status != "blocked"
            for unit in point.response_units
            if unit.review_status != "blocked"
            and unit.response_scope == "document"
        ]
        visible_condition_ids = [
            condition_id
            for point in scores.points
            for unit in point.response_units
            if unit.unit_id in visible_response_unit_ids
            for condition_id in unit.condition_ids
        ]
        linked_requirement_ids = {
            requirement_id
            for point in scores.points
            for unit in point.response_units
            if unit.unit_id in visible_response_unit_ids
            for requirement_id in unit.linked_requirement_ids
        }
        requirement_ids = [
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
            and item.requirement_id in linked_requirement_ids
        ]

        if template_structure is None:
            nodes = [
                ChapterOutlineNodeCandidate(
                    local_id="test-outline-root",
                    order=0,
                    title="投标响应方案",
                    purpose="按独立得分任务组织响应目录",
                    writing_objectives=["形成统一的项目响应结构"],
                    target_size=200,
                    confidence=1.0,
                )
            ]
            conditions_by_id = {
                condition.condition_id: condition
                for point in scores.points
                for condition in point.score_conditions
            }
            units_in_order = [
                unit
                for point in scores.points
                if point.review_status != "blocked"
                for unit in point.response_units
                if unit.unit_id in visible_response_unit_ids
            ]
            next_order = 1
            for unit in units_in_order:
                unit_condition_ids = [
                    condition_id
                    for condition_id in unit.condition_ids
                    if condition_id in visible_condition_ids
                ]
                substantive_ids = [
                    condition_id
                    for condition_id in unit_condition_ids
                    if conditions_by_id[
                        condition_id
                    ].condition_role
                    in {"content", "evidence"}
                ]
                primary_condition_ids = [
                    condition_id
                    for condition_id in unit_condition_ids
                    if conditions_by_id[
                        condition_id
                    ].condition_role
                    in {"constraint", "quality"}
                ]
                if len(substantive_ids) == 1:
                    primary_condition_ids.extend(substantive_ids)
                quality_objectives = [
                    conditions_by_id[
                        condition_id
                    ].response_intent
                    for condition_id in unit_condition_ids
                    if conditions_by_id[
                        condition_id
                    ].condition_role
                    == "quality"
                ]
                primary_local_id = (
                    "test-outline-unit-"
                    + unit.unit_id.replace("_", "-")
                )
                unit_requirement_ids = [
                    requirement_id
                    for requirement_id in unit.linked_requirement_ids
                    if requirement_id in requirement_ids
                ]
                nodes.append(
                    ChapterOutlineNodeCandidate(
                        local_id=primary_local_id,
                        parent_local_id="test-outline-root",
                        order=next_order,
                        title=unit.title,
                        purpose=unit.response_expectation,
                        writing_objectives=list(
                            dict.fromkeys(
                                [
                                    unit.response_expectation,
                                    *quality_objectives,
                                ]
                            )
                        ),
                        primary_response_unit_ids=[unit.unit_id],
                        score_condition_ids=primary_condition_ids,
                        requirement_ids=unit_requirement_ids,
                        target_size=1000,
                        confidence=unit.confidence,
                    )
                )
                next_order += 1
                if len(substantive_ids) <= 1:
                    continue
                for condition_id in substantive_ids:
                    condition = conditions_by_id[condition_id]
                    nodes.append(
                        ChapterOutlineNodeCandidate(
                            local_id=(
                                "test-outline-condition-"
                                + condition_id.replace("_", "-")
                            ),
                            parent_local_id=primary_local_id,
                            order=next_order,
                            title=condition.normalized_condition,
                            purpose=condition.response_intent,
                            writing_objectives=[
                                condition.response_intent
                            ],
                            # Primary stays on the unit chapter; condition child
                            # keeps the unit as supporting for evidence mapping.
                            supporting_response_unit_ids=[unit.unit_id],
                            score_condition_ids=[condition_id],
                            planned_tables=(
                                ["证明材料清单"]
                                if condition.condition_role == "evidence"
                                else []
                            ),
                            target_size=600,
                            confidence=condition.confidence,
                        )
                    )
                    next_order += 1
        else:
            slots_by_node: dict[str, list[str]] = {}
            for slot in template_structure.slots:
                slots_by_node.setdefault(slot.node_id, []).append(slot.slot_id)
            ordered_template_nodes = sorted(
                template_structure.nodes,
                key=lambda item: item.order,
            )
            first_node_id = ordered_template_nodes[0].node_id
            nodes = [
                ChapterOutlineNodeCandidate(
                    local_id=node.node_id,
                    parent_local_id=node.parent_node_id,
                    order=node.order,
                    title=node.title,
                    purpose=(
                        "承载全部非全文质量响应责任"
                        if node.node_id == first_node_id
                        else "保持严格模板既有章节结构"
                    ),
                    writing_objectives=(
                        ["完整覆盖需求、评分点及响应义务"]
                        if node.node_id == first_node_id
                        else []
                    ),
                    primary_response_unit_ids=(
                        visible_response_unit_ids
                        if node.node_id == first_node_id
                        else []
                    ),
                    score_condition_ids=(
                        visible_condition_ids
                        if node.node_id == first_node_id
                        else []
                    ),
                    requirement_ids=(
                        requirement_ids
                        if node.node_id == first_node_id
                        else []
                    ),
                    template_slot_ids=slots_by_node.get(node.node_id, []),
                    target_size=800,
                    confidence=1.0,
                )
                for node in ordered_template_nodes
            ]
        return ChapterOutlineCandidate(
            nodes=nodes,
            document_quality_response_unit_ids=quality_response_unit_ids,
            review_status="draft",
        )

    @staticmethod
    def _outline_validation_can_fallback(
        error: PlanningInferenceValidationError,
    ) -> bool:
        """Only downgrade known directory-semantic defects.

        JSON/schema failures, unknown or dangling source IDs, upstream catalog
        defects, dependency mismatches, and strict-template conflicts remain
        fail-closed because none of their error messages are in this whitelist.
        """

        messages: list[str] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            messages.append(str(current))
            current = current.__cause__
        combined = "\n".join(messages)
        return any(
            marker in combined
            for marker in _NONBLOCKING_OUTLINE_VALIDATION_MARKERS
        )

    @staticmethod
    def _blueprint_audit_can_fallback(
        audit: dict[str, object],
    ) -> bool:
        blocking, review_only = partition_chapter_blueprint_audit(audit)
        return not blocking and bool(review_only)

    @staticmethod
    def _outline_warning_detail(value: object, *, limit: int = 1200) -> str:
        detail = " ".join(str(value or "").split())
        if len(detail) <= limit:
            return detail
        return f"{detail[:limit]}…"

    def _outline_fallback_runtime_metadata(
        self,
    ) -> tuple[str, str, str, str]:
        registration = get_planning_skill(
            OUTLINE_SKILL_ID,
            caller_role="planning_agent",
        )
        prompt_version = (
            f"{OUTLINE_SKILL_ID}.program_audit_fallback.v3."
            f"{OUTLINE_PROMPT_VERSION}"
        )
        prompt_hash = canonical_hash(
            {
                "mode": "program_audit_fallback",
                "version": prompt_version,
            }
        )
        provider_fingerprint = canonical_hash(
            {
                "adapter": "bid_agent.internal_outline_audit_fallback",
                "capability_id": OUTLINE_SKILL_ID,
                "version": "v3",
                "source_prompt_hash": registration.prompt_hash,
            }
        )
        model_fingerprint = (
            f"deterministic_fallback:{OUTLINE_SKILL_ID}:v3"
        )
        return (
            prompt_version,
            prompt_hash,
            provider_fingerprint,
            model_fingerprint,
        )

    def _compile_outline_audit_fallback(
        self,
        *,
        planning_agent: PlanningAgent,
        ledger: RequirementLedger,
        scores: ScoreModel,
        template_structure: TemplateStructureContract | None,
        outline_request: OutlineDecompositionInput,
        base_revision: int,
        capability_version: str,
        schema_version: str,
        warning: str,
        audit_findings: list[dict[str, object]] | None = None,
    ) -> tuple[ChapterBlueprint, _DeterministicInferenceResult]:
        candidate = self._deterministic_outline_candidate(
            ledger,
            scores,
            template_structure,
        )
        blueprint = planning_agent.compile_outline_candidate(
            candidate,
            ledger,
            scores,
            revision=base_revision + 1,
            template_structure=template_structure,
        )
        fallback_audit = audit_chapter_blueprint(
            blueprint,
            ledger,
            score_model=scores,
            template_structure=template_structure,
        )
        fallback_passed = bool(fallback_audit.get("passed"))
        if (
            not fallback_passed
            and not self._blueprint_audit_can_fallback(fallback_audit)
        ):
            from control_plane import ControlPlaneError

            findings = self._outline_warning_detail(
                fallback_audit.get("findings")
            )
            raise ControlPlaneError(
                "V3_BLUEPRINT_FALLBACK_INVALID",
                "保守目录仍未通过结构、来源、依赖或模板校验，不能晋级："
                f"{findings}",
                status_code=409,
            )
        recorded_findings = [
            dict(finding)
            for finding in (audit_findings or [])
            if isinstance(finding, dict)
        ]
        fallback_findings = [
            dict(finding)
            for finding in (fallback_audit.get("findings") or [])
            if isinstance(finding, dict)
        ]
        recorded_findings.extend(fallback_findings)
        warning_texts: list[str] = []
        if not fallback_passed:
            warning_texts.append(self._outline_warning_detail(warning))
            warning_texts.append(
                "保守目录的最终程序语义审核仍有提示，流程已继续，"
                "请人工复核："
                + self._outline_warning_detail(fallback_findings)
            )
        audit_codes = sorted(
            {
                str(finding.get("code") or "")
                for finding in recorded_findings
                if str(finding.get("code") or "").strip()
            }
        )
        recovery_summary: dict[str, object] = {
            **blueprint.coverage_summary,
            "recovery_mode": "deterministic_outline",
            "recovered_issue_codes": audit_codes,
        }
        if not fallback_passed:
            recovery_summary.update(
                {
                    "program_audit_warning": True,
                    "program_audit_warnings": warning_texts,
                    "program_audit_codes": audit_codes,
                    "program_audit_findings": recorded_findings,
                    "review_status": "needs_review",
                    "needs_human": True,
                }
            )
        blueprint = blueprint.model_copy(
            update={"coverage_summary": recovery_summary}
        )
        (
            fallback_prompt_version,
            _fallback_prompt_hash,
            fallback_provider_fingerprint,
            fallback_model_fingerprint,
        ) = self._outline_fallback_runtime_metadata()
        del _fallback_prompt_hash
        result = self._deterministic_result(
            capability_id=OUTLINE_SKILL_ID,
            capability_version=capability_version,
            schema_version=schema_version,
            candidate=candidate,
            input_value=outline_request,
            prompt_version=fallback_prompt_version,
            model_fingerprint=fallback_model_fingerprint,
            provider_fingerprint=fallback_provider_fingerprint,
            execution_mode="program_audit_fallback",
        )
        return blueprint, result

    @staticmethod
    def _content_unit_write_priority(title: str) -> tuple[int, str]:
        normalized = str(title or "")
        deferred = any(token in normalized for token in ("商务", "报价", "价格", "财务"))
        return (1 if deferred else 0, normalized)

    def run(self, stage: str, *, operation_id: str | None = None):
        if stage == "ingest_inputs":
            manifest = InputManifestService(self.context).load()
            if not any(item.active and item.role.value == "tender" for item in manifest.inputs):
                raise ValueError("INGEST_BLOCKED: 至少需要一份活动招标文件")
            return manifest
        if stage == "normalize_sources":
            return SourceNormalizer(self.context).normalize_active_inputs()
        if stage == "compile_template_structure":
            manifest = InputManifestService(self.context).load()
            template = next((item for item in manifest.inputs if item.active and item.role.value == "template"), None)
            return TemplateContractCompiler(self.context).compile_structure(template) if template else None
        if stage in ("build_requirement_ledger", "analyze_requirements"):
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record
            from .contracts import RequirementLedger

            manifest = InputManifestService(self.context).load()
            source_index = require_promoted_source_index(self.context)
            idx = source_index.model_dump(mode="json")
            source_blocks = list(source_index.blocks)

            agent = RequirementAgent(self.context)
            items = agent.extract_requirements(source_blocks, manifest)

            store = ControlStore(self.context)
            active_art = store.v3_active_artifact("RequirementLedger")
            base_rev = int(active_art["revision"]) if active_art is not None else 0
            source_hashes = dict(source_index.source_hashes)
            draft_ledger = RequirementLedger(revision=base_rev + 1, source_hashes=source_hashes, requirements=items)
            coverage_audit = audit_reverse_coverage(draft_ledger, idx)
            if not coverage_audit["passed"]:
                missing_chunks = coverage_audit["missing_chunk_ids"]
                message = (
                    "RequirementLedger 需求覆盖校验失败："
                    f"{len(missing_chunks)} 个来源片段未覆盖。"
                )
                if self.validation_failure_blocks_pipeline:
                    raise ControlPlaneError(
                        "V3_REQUIREMENT_COVERAGE_BLOCKED",
                        message,
                        status_code=409,
                        details={"coverage_audit": coverage_audit},
                    )
                self._add_stage_warning(
                    "build_requirement_ledger",
                    code="V3_REQUIREMENT_COVERAGE_WARNING",
                    message=message,
                    details={"coverage_audit": coverage_audit},
                )
            op_id = operation_id or f"requirement:{manifest.revision}"
            proposal = agent.create_extraction_proposal(
                items,
                base_revision=base_rev,
                operation_id=op_id,
                source_hashes=source_hashes,
                coverage_audit=coverage_audit,
            )
            if (
                active_art is not None
                and active_art["dependency_fingerprint"] == proposal.dependency_fingerprint
                and _active_payload_matches(active_art, proposal.payload)
            ):
                return load_promoted_requirement_ledger(self.context)

            sandbox = AgentProposalSandbox(self.context, role="requirement_agent")
            stored_proposal = sandbox.submit(proposal)
            proposal_id = str(stored_proposal["proposal_id"])

            report = validate_and_record(self.context, proposal_id)
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"RequirementLedger Proposal 验证未通过: {report.findings}")

            gate_service = GateService(self.context)
            receipt = gate_service.evaluate(proposal_id, gate_id="G1_REQUIREMENT_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"RequirementLedger 门禁阻断: {receipt.findings}")

            promotion_service = ArtifactPromotionService(self.context)
            promotion_service.promote(proposal_id, gate_receipt_ids=[receipt.receipt_id])

            return load_promoted_requirement_ledger(self.context)

        if stage == "analyze_scores":
            from control_plane import ControlPlaneError, ControlStore

            requirement_ledger = load_promoted_requirement_ledger(self.context)
            source_index = require_promoted_source_index(self.context)
            source_blocks = list(source_index.blocks)
            source_hashes = dict(source_index.source_hashes)
            idx = source_index.model_dump(mode="json")

            store = ControlStore(self.context)
            active_art = store.v3_active_artifact("ScoreModel")
            base_rev = int(active_art["revision"]) if active_art is not None else 0
            agent = ScoreAgent(self.context)
            structural_model = agent.build_score_model(
                source_blocks,
                requirement_ledger,
                revision=base_rev + 1,
                source_hashes=source_hashes,
            )
            score_products = [
                self._score_structure_product(structural_model)
            ]
            score_warnings: list[str] = []
            self._record_stage_output(
                operation_id,
                "analyze_scores",
                phase="score_semantic_inference",
                products=score_products,
            )

            score_capability_version = SCORE_SEMANTIC_CAPABILITY_VERSION
            if not structural_model.points:
                prompt_version = f"{SCORE_SEMANTIC_CAPABILITY_ID}.empty_input.v1"
                model_fingerprint = (
                    f"deterministic_structure:{SCORE_SEMANTIC_CAPABILITY_ID}:"
                    "empty_input:v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "deterministic_test", "version": prompt_version}
                )
                score_schema_version = "v3.score_semantic.empty.v1"
                score_provider_fingerprint = (
                    self._deterministic_provider_fingerprint(
                        SCORE_SEMANTIC_CAPABILITY_ID
                    )
                )
                score_temperature = 0.0
            elif self._uses_deterministic_score:
                prompt_version = f"{SCORE_SEMANTIC_CAPABILITY_ID}.deterministic_test.v1"
                model_fingerprint = (
                    f"deterministic_test:{SCORE_SEMANTIC_CAPABILITY_ID}:v1"
                )
                prompt_hash = canonical_hash(
                    {"mode": "deterministic_test", "version": prompt_version}
                )
                score_schema_version = SCORE_SEMANTIC_SCHEMA_VERSION
                score_provider_fingerprint = (
                    self._deterministic_provider_fingerprint(
                        SCORE_SEMANTIC_CAPABILITY_ID
                    )
                )
                score_temperature = 0.0
            else:
                score_provider = self._score_provider()
                if (
                    score_provider.capability_version
                    != SCORE_SEMANTIC_CAPABILITY_VERSION
                ):
                    raise ControlPlaneError(
                        "V3_INFERENCE_CAPABILITY_VERSION_MISMATCH",
                        "ScoreSemanticProvider capability version 不受支持",
                        status_code=409,
                    )
                prompt_version = score_provider.prompt_version
                prompt_hash = score_provider.prompt_hash
                score_schema_version = score_provider.schema_version
                score_provider_fingerprint = (
                    score_provider.provider_fingerprint
                )
                model_fingerprint = score_provider.model_fingerprint
                score_temperature = score_provider.temperature
            if self._active_inference_artifact_is_current(
                "ScoreModel",
                capability_version=score_capability_version,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                output_schema_version=score_schema_version,
                provider_fingerprint=score_provider_fingerprint,
                model_fingerprint=model_fingerprint,
                temperature=score_temperature,
            ):
                promoted_score_model = load_promoted_score_model(
                    self.context
                )
                cached_audit = audit_score_model(
                    promoted_score_model,
                    requirement_ledger,
                    source_blocks,
                    require_semantic=True,
                )
                _, cached_review_findings = partition_score_model_audit(
                    cached_audit
                )
                cached_warnings: list[str] = []
                if cached_review_findings or any(
                    point.review_status == "needs_review"
                    for point in promoted_score_model.points
                ):
                    cached_warnings.append(
                        "评分理解结果包含需复核项；已复用现有结果并继续生成目录，"
                        "不阻塞后续流程。"
                    )
                self._record_stage_output(
                    operation_id,
                    "analyze_scores",
                    phase="completed",
                    products=[
                        *score_products,
                        self._score_semantic_product(
                            promoted_score_model,
                            warnings=cached_warnings,
                        ),
                    ],
                )
                return promoted_score_model

            if not structural_model.points:
                score_model = structural_model
                inference_result = self._deterministic_result(
                    capability_id=SCORE_SEMANTIC_CAPABILITY_ID,
                    capability_version=score_capability_version,
                    schema_version=score_schema_version,
                    candidate={
                        "mode": "empty_score_structure",
                        "structural_score_model_hash": canonical_hash(
                            structural_model.model_dump(mode="json")
                        ),
                    },
                    input_value={
                        "source_snapshot_hash": canonical_hash(source_hashes),
                        "total_points": structural_model.total_points,
                        "groups": [],
                        "rules": [],
                    },
                    prompt_version=prompt_version,
                    model_fingerprint=model_fingerprint,
                    provider_fingerprint=score_provider_fingerprint,
                )
            elif self._uses_deterministic_score:
                semantic_input = agent.semantic_input(
                    structural_model,
                    source_blocks,
                    requirement_ledger,
                )
                context_ids_by_point = {
                    rule.rule_id: list(rule.context_requirement_ids)
                    for rule in semantic_input.rules
                }
                structural_model = structural_model.model_copy(
                    update={
                        "points": [
                            point.model_copy(
                                update={
                                    "context_requirement_ids": (
                                        context_ids_by_point.get(
                                            point.score_point_id,
                                            [],
                                        )
                                    )
                                }
                            )
                            for point in structural_model.points
                        ]
                    }
                )
                score_model = self._deterministic_score_projection(
                    structural_model,
                    source_blocks,
                    semantic_input,
                )
                inference_result = self._deterministic_result(
                    capability_id=SCORE_SEMANTIC_CAPABILITY_ID,
                    capability_version=score_capability_version,
                    schema_version=SCORE_SEMANTIC_SCHEMA_VERSION,
                    candidate={
                        "mode": "deterministic_test",
                        "structural_score_model_hash": canonical_hash(
                            structural_model.model_dump(mode="json")
                        ),
                    },
                    input_value=semantic_input,
                )
            else:
                semantic_input = agent.semantic_input(
                    structural_model,
                    source_blocks,
                    requirement_ledger,
                )
                score_provider = self._score_provider()
                from .llm_telemetry import llm_stage_context

                try:
                    with llm_stage_context(
                        self.context,
                        operation_id,
                        "score_semantic",
                        capability_id=SCORE_SEMANTIC_CAPABILITY_ID,
                        prompt_version=score_provider.prompt_version,
                        schema_version=score_provider.schema_version,
                        model=score_provider.model_fingerprint,
                        temperature=score_provider.temperature,
                    ):
                        score_inference = score_provider.interpret(
                            semantic_input
                        )
                except ScoreSemanticInferenceError as exc:
                    score_model = self._deterministic_score_projection(
                        structural_model,
                        source_blocks,
                        semantic_input,
                    )
                    score_warnings.append(
                        "大模型评分理解未形成可用候选"
                        f"（{exc.code}），已按评分原文生成保守响应任务，"
                        "标记为需复核并继续生成目录。"
                    )
                    fallback_prompt_version = (
                        f"{SCORE_SEMANTIC_CAPABILITY_ID}."
                        "program_audit_fallback.v1"
                    )
                    fallback_provider_fingerprint = canonical_hash(
                        {
                            "adapter": (
                                "bid_agent.internal_program_audit_fallback"
                            ),
                            "capability_id": (
                                SCORE_SEMANTIC_CAPABILITY_ID
                            ),
                            "version": "v1",
                        }
                    )
                    inference_result = self._deterministic_result(
                        capability_id=SCORE_SEMANTIC_CAPABILITY_ID,
                        capability_version=score_capability_version,
                        schema_version=SCORE_SEMANTIC_SCHEMA_VERSION,
                        candidate={
                            "mode": "program_audit_fallback",
                            "trigger_code": exc.code,
                            "attempt_count": exc.attempts,
                            "diagnostic_hash": canonical_hash(
                                list(exc.errors)
                            ),
                            "structural_score_model_hash": canonical_hash(
                                structural_model.model_dump(mode="json")
                            ),
                        },
                        input_value=semantic_input,
                        prompt_version=fallback_prompt_version,
                        model_fingerprint=(
                            "deterministic_fallback:"
                            f"{SCORE_SEMANTIC_CAPABILITY_ID}:v1"
                        ),
                        provider_fingerprint=(
                            fallback_provider_fingerprint
                        ),
                        execution_mode="program_audit_fallback",
                    )
                else:
                    score_warnings.extend(score_inference.warnings)
                    if (
                        score_inference.capability_id
                        != SCORE_SEMANTIC_CAPABILITY_ID
                        or score_inference.prompt_version
                        != score_provider.prompt_version
                        or score_inference.prompt_hash
                        != score_provider.prompt_hash
                        or score_inference.schema_version
                        != score_provider.schema_version
                        or score_inference.provider_fingerprint
                        != score_provider.provider_fingerprint
                        or score_inference.model_fingerprint
                        != score_provider.model_fingerprint
                    ):
                        raise ControlPlaneError(
                            "V3_INFERENCE_CAPABILITY_MISMATCH",
                            "ScoreSemanticProvider 返回结果的受控元数据不一致",
                            status_code=409,
                        )
                    score_model = agent.apply_semantic_candidate(
                        structural_model,
                        score_inference.candidate,
                    )
                    inference_result = score_inference

            score_audit = audit_score_model(
                score_model,
                requirement_ledger,
                source_blocks,
                require_semantic=True,
            )
            blocking_findings, review_findings = partition_score_model_audit(
                score_audit
            )
            if blocking_findings:
                issue_count = sum(
                    len(value) if isinstance(value, list) else 1
                    for value in blocking_findings.values()
                )
                message = (
                    "ScoreModel 结构或来源完整性审计失败："
                    f"发现 {issue_count} 个问题。"
                )
                if self.validation_failure_blocks_pipeline:
                    raise ControlPlaneError(
                        "V3_SCORE_INTEGRITY_BLOCKED",
                        message,
                        status_code=409,
                        details={
                            "score_audit": score_audit,
                            "blocking_findings": blocking_findings,
                        },
                    )
                self._add_stage_warning(
                    "analyze_scores",
                    code="V3_SCORE_INTEGRITY_WARNING",
                    message=message,
                    details={
                        "score_audit": score_audit,
                        "blocking_findings": blocking_findings,
                    },
                )
            if review_findings:
                review_issue_count = sum(
                    len(value) if isinstance(value, list) else 1
                    for value in review_findings.values()
                )
                score_warnings.append(
                    "评分理解程序审核发现 "
                    f"{review_issue_count} 项语义完整性提示，"
                    "已标记为需复核并继续生成目录。"
                )
            if score_warnings:
                score_model = self._mark_score_model_needs_review(score_model)
            op_id = operation_id or f"score:{requirement_ledger.revision}:{idx.get('revision', 0)}"
            proposal = self._proposal_from_inference(
                artifact_kind="ScoreModel",
                producer_role="score_agent",
                payload=score_model,
                base_revision=base_rev,
                operation_id=op_id,
                result=inference_result,
                input_snapshot=(
                    semantic_input
                    if structural_model.points
                    else {
                        "source_snapshot_hash": canonical_hash(source_hashes),
                        "total_points": structural_model.total_points,
                        "groups": [],
                        "rules": [],
                    }
                ),
                capability_version=score_capability_version,
            )
            self._validate_gate_promote(
                proposal,
                producer_role="score_agent",
                gate_id="G1_SCORE_INTEGRITY",
            )
            promoted_score_model = load_promoted_score_model(self.context)
            self._record_stage_output(
                operation_id,
                "analyze_scores",
                phase="completed",
                products=[
                    *score_products,
                    self._score_semantic_product(
                        promoted_score_model,
                        warnings=score_warnings,
                    ),
                ],
            )
            return promoted_score_model

        if stage in ("plan_response", "build_project_model"):
            from control_plane import ControlPlaneError, ControlStore

            ledger = load_promoted_requirement_ledger(self.context)
            scores = load_promoted_score_model(self.context)
            self._require_active_inference_artifact("ScoreModel")
            source_index = require_promoted_source_index(self.context)
            source_blocks = list(source_index.blocks)
            store = ControlStore(self.context)
            agent = PlanningAgent(self.context)
            source_context = self._planning_source_context(
                source_blocks,
                requirement_chunk_ids={
                    item.source_anchor.chunk_id for item in ledger.requirements
                },
                score_chunk_ids={
                    anchor.chunk_id
                    for point in scores.points
                    for anchor in point.source_anchors
                },
            )

            active_project = store.v3_active_artifact("ProjectModel")
            project_base = int(active_project["revision"]) if active_project is not None else 0
            project_request = build_project_understanding_input(
                ledger,
                scores,
                source_index,
            )
            project_capability_version = PROJECT_CAPABILITY_VERSION
            if self._uses_deterministic_project:
                project_prompt_version = (
                    f"{_PROJECT_CAPABILITY_ID}.deterministic_test.v1"
                )
                project_prompt_hash = canonical_hash(
                    {
                        "mode": "deterministic_test",
                        "version": project_prompt_version,
                    }
                )
                project_schema_version = PROJECT_SCHEMA_VERSION
                project_provider_fingerprint = (
                    self._deterministic_provider_fingerprint(
                        _PROJECT_CAPABILITY_ID
                    )
                )
                project_model_fingerprint = (
                    f"deterministic_test:{_PROJECT_CAPABILITY_ID}:v1"
                )
                project_temperature = 0.0
            else:
                project_provider = self._project_provider()
                if (
                    project_provider.capability_version
                    != project_capability_version
                ):
                    raise ControlPlaneError(
                        "V3_INFERENCE_CAPABILITY_VERSION_MISMATCH",
                        "ProjectUnderstandingProvider capability version 不受支持",
                        status_code=409,
                    )
                project_prompt_version = project_provider.prompt_version
                project_prompt_hash = project_provider.prompt_hash
                project_schema_version = project_provider.schema_version
                project_provider_fingerprint = (
                    project_provider.provider_fingerprint
                )
                project_model_fingerprint = project_provider.model_fingerprint
                project_temperature = project_provider.temperature
            if not self._active_inference_artifact_is_current(
                "ProjectModel",
                capability_version=project_capability_version,
                prompt_version=project_prompt_version,
                prompt_hash=project_prompt_hash,
                output_schema_version=project_schema_version,
                provider_fingerprint=project_provider_fingerprint,
                model_fingerprint=project_model_fingerprint,
                temperature=project_temperature,
            ):
                if self._uses_deterministic_project:
                    project_candidate = self._deterministic_project_candidate(
                        ledger,
                        scores,
                    )
                    project = agent.compile_project_candidate(
                        project_candidate,
                        ledger,
                        scores,
                        source_blocks,
                        revision=project_base + 1,
                    )
                    project_result = self._deterministic_result(
                        capability_id=_PROJECT_CAPABILITY_ID,
                        capability_version=project_capability_version,
                        schema_version=project_schema_version,
                        candidate=project_candidate,
                        input_value=project_request,
                        prompt_version=project_prompt_version,
                        model_fingerprint=project_model_fingerprint,
                        provider_fingerprint=project_provider_fingerprint,
                    )
                else:
                    from .llm_telemetry import llm_stage_context

                    with llm_stage_context(
                        self.context,
                        operation_id,
                        "project_understanding",
                        capability_id=_PROJECT_CAPABILITY_ID,
                        prompt_version=project_provider.prompt_version,
                        schema_version=project_provider.schema_version,
                        model=project_provider.model_fingerprint,
                        temperature=project_provider.temperature,
                    ):
                        project_result = project_provider.understand(project_request)
                    if (
                        project_result.capability_id != _PROJECT_CAPABILITY_ID
                        or project_result.prompt_version
                        != project_provider.prompt_version
                        or project_result.prompt_hash
                        != project_provider.prompt_hash
                        or project_result.schema_version
                        != project_provider.schema_version
                        or project_result.provider_fingerprint
                        != project_provider.provider_fingerprint
                        or project_result.model_fingerprint
                        != project_provider.model_fingerprint
                    ):
                        raise ControlPlaneError(
                            "V3_INFERENCE_CAPABILITY_MISMATCH",
                            "ProjectUnderstandingProvider 返回结果的受控元数据不一致",
                            status_code=409,
                        )
                    project = agent.compile_project_candidate(
                        project_result.candidate,
                        ledger,
                        scores,
                        source_blocks,
                        revision=project_base + 1,
                    )
                project_op_id = operation_id or (
                    f"planning-project:{ledger.revision}:{scores.revision}"
                )
                project_proposal = self._proposal_from_inference(
                    artifact_kind="ProjectModel",
                    producer_role="planning_agent",
                    payload=project,
                    base_revision=project_base,
                    operation_id=project_op_id,
                    result=project_result,
                    input_snapshot=project_request,
                    capability_version=project_capability_version,
                )
                self._validate_gate_promote(
                    project_proposal,
                    producer_role="planning_agent",
                    gate_id="G1_PROJECT_MODEL_INTEGRITY",
                )
            project = load_promoted_project_model(self.context)
            self._require_active_inference_artifact("ProjectModel")
            planning_products = [self._project_product(project)]
            self._record_stage_output(
                operation_id,
                "plan_response",
                phase="topic_duty_planning",
                products=planning_products,
            )

            active_graph = store.v3_active_artifact("ResponseTopicGraph")
            graph_base = int(active_graph["revision"]) if active_graph is not None else 0
            topic_request = TopicDutyPlanningInput(
                project_model=project.model_dump(mode="json"),
                requirement_ledger=ledger.model_dump(mode="json"),
                score_model=scores.model_dump(mode="json"),
                source_context=source_context,
            )
            topic_capability_version = TOPIC_CAPABILITY_VERSION
            if self._uses_deterministic_topic:
                topic_prompt_version = f"{_TOPIC_CAPABILITY_ID}.deterministic_test.v1"
                topic_prompt_hash = canonical_hash(
                    {
                        "mode": "deterministic_test",
                        "version": topic_prompt_version,
                    }
                )
                topic_schema_version = TOPIC_SCHEMA_VERSION
                topic_provider_fingerprint = (
                    self._deterministic_provider_fingerprint(
                        _TOPIC_CAPABILITY_ID
                    )
                )
                topic_model_fingerprint = (
                    f"deterministic_test:{_TOPIC_CAPABILITY_ID}:v1"
                )
                topic_temperature = 0.0
            else:
                topic_provider = self._topic_provider()
                if topic_provider.capability_version != topic_capability_version:
                    raise ControlPlaneError(
                        "V3_INFERENCE_CAPABILITY_VERSION_MISMATCH",
                        "TopicDutyPlanningProvider capability version 不受支持",
                        status_code=409,
                    )
                topic_prompt_version = topic_provider.prompt_version
                topic_prompt_hash = topic_provider.prompt_hash
                topic_schema_version = topic_provider.schema_version
                topic_provider_fingerprint = (
                    topic_provider.provider_fingerprint
                )
                topic_model_fingerprint = topic_provider.model_fingerprint
                topic_temperature = topic_provider.temperature
            if not self._active_inference_artifact_is_current(
                "ResponseTopicGraph",
                capability_version=topic_capability_version,
                prompt_version=topic_prompt_version,
                prompt_hash=topic_prompt_hash,
                output_schema_version=topic_schema_version,
                provider_fingerprint=topic_provider_fingerprint,
                model_fingerprint=topic_model_fingerprint,
                temperature=topic_temperature,
            ):
                if self._uses_deterministic_topic:
                    topic_candidate = self._deterministic_topic_candidate(
                        ledger,
                        scores,
                        project,
                    )
                    graph = agent.compile_topic_candidate(
                        topic_candidate,
                        ledger,
                        scores,
                        project,
                        source_blocks,
                        revision=graph_base + 1,
                    )
                    topic_result = self._deterministic_result(
                        capability_id=_TOPIC_CAPABILITY_ID,
                        capability_version=topic_capability_version,
                        schema_version=topic_schema_version,
                        candidate=topic_candidate,
                        input_value=topic_request,
                        prompt_version=topic_prompt_version,
                        model_fingerprint=topic_model_fingerprint,
                        provider_fingerprint=topic_provider_fingerprint,
                    )
                else:
                    from .llm_telemetry import llm_stage_context

                    with llm_stage_context(
                        self.context,
                        operation_id,
                        "topic_duty_planning",
                        capability_id=_TOPIC_CAPABILITY_ID,
                        prompt_version=topic_provider.prompt_version,
                        schema_version=topic_provider.schema_version,
                        model=topic_provider.model_fingerprint,
                        temperature=topic_provider.temperature,
                    ):
                        topic_result = topic_provider.plan(topic_request)
                    if (
                        topic_result.capability_id != _TOPIC_CAPABILITY_ID
                        or topic_result.prompt_version
                        != topic_provider.prompt_version
                        or topic_result.prompt_hash
                        != topic_provider.prompt_hash
                        or topic_result.schema_version
                        != topic_provider.schema_version
                        or topic_result.provider_fingerprint
                        != topic_provider.provider_fingerprint
                        or topic_result.model_fingerprint
                        != topic_provider.model_fingerprint
                    ):
                        raise ControlPlaneError(
                            "V3_INFERENCE_CAPABILITY_MISMATCH",
                            "TopicDutyPlanningProvider 返回结果的受控元数据不一致",
                            status_code=409,
                        )
                    graph = agent.compile_topic_candidate(
                        topic_result.candidate,
                        ledger,
                        scores,
                        project,
                        source_blocks,
                        revision=graph_base + 1,
                    )
                graph_op_id = operation_id or (
                    f"planning-graph:{ledger.revision}:{scores.revision}:"
                    f"{project.revision}"
                )
                graph_proposal = self._proposal_from_inference(
                    artifact_kind="ResponseTopicGraph",
                    producer_role="planning_agent",
                    payload=graph,
                    base_revision=graph_base,
                    operation_id=graph_op_id,
                    result=topic_result,
                    input_snapshot=topic_request,
                    capability_version=topic_capability_version,
                )
                self._validate_gate_promote(
                    graph_proposal,
                    producer_role="planning_agent",
                    gate_id="G1_TOPIC_GRAPH_INTEGRITY",
                )
            graph = load_promoted_topic_graph(self.context)
            self._record_stage_output(
                operation_id,
                "plan_response",
                phase="completed",
                products=[
                    *planning_products,
                    self._topic_graph_product(graph),
                ],
            )
            return project

        if stage == "compile_chapter_blueprint":
            from control_plane import ControlPlaneError, ControlStore

            ledger = load_promoted_requirement_ledger(self.context)
            scores = load_promoted_score_model(self.context)
            self._require_active_inference_artifact("ScoreModel")
            template_structure = self._template_structure()
            store = ControlStore(self.context)
            active = store.v3_active_artifact("ChapterBlueprint")
            base_revision = int(active["revision"]) if active is not None else 0

            outline_request = build_outline_decomposition_input(
                ledger,
                scores,
                template_structure,
            )
            uses_program_outline = (
                self._uses_deterministic_outline
                or outline_request.document_mode == "template_strict"
            )
            optional_dependencies = (
                ("TemplateStructureContract",)
                if template_structure is not None
                else ()
            )
            skill_registration = get_planning_skill(
                OUTLINE_SKILL_ID,
                caller_role="planning_agent",
            )
            if uses_program_outline:
                outline_prompt_version = (
                    f"{OUTLINE_SKILL_ID}.deterministic_test.v1"
                    if self._uses_deterministic_outline
                    else f"{OUTLINE_SKILL_ID}.template_projection.v1"
                )
                outline_prompt_hash = canonical_hash(
                    {
                        "mode": (
                            "deterministic_test"
                            if self._uses_deterministic_outline
                            else "template_projection"
                        ),
                        "version": outline_prompt_version,
                    }
                )
                outline_schema_version = skill_registration.schema_version
                outline_provider_fingerprint = self._deterministic_provider_fingerprint(
                    (
                        OUTLINE_SKILL_ID
                        if self._uses_deterministic_outline
                        else f"{OUTLINE_SKILL_ID}.template_projection"
                    )
                )
                outline_model_fingerprint = (
                    f"deterministic_test:{OUTLINE_SKILL_ID}:v1"
                    if self._uses_deterministic_outline
                    else f"program_template_projection:{OUTLINE_SKILL_ID}:v1"
                )
                outline_temperature = 0.0
            else:
                outline_provider = self._outline_provider()
                if (
                    outline_provider.skill_id != skill_registration.skill_id
                    or outline_provider.capability_version
                    != skill_registration.version
                    or outline_provider.capability_version
                    != OUTLINE_CAPABILITY_VERSION
                    or outline_provider.prompt_version != OUTLINE_PROMPT_VERSION
                    or outline_provider.prompt_hash
                    != skill_registration.prompt_hash
                    or outline_provider.schema_version
                    != skill_registration.schema_version
                    or outline_provider.schema_version != OUTLINE_SCHEMA_VERSION
                ):
                    raise ControlPlaneError(
                        "V3_INTERNAL_SKILL_MISMATCH",
                        "OutlineDecompositionProvider 未绑定固定版本、Prompt 和 Schema 的内部章节拆分 Skill",
                        status_code=409,
                    )
                outline_prompt_version = outline_provider.prompt_version
                outline_prompt_hash = outline_provider.prompt_hash
                outline_schema_version = outline_provider.schema_version
                outline_provider_fingerprint = (
                    outline_provider.provider_fingerprint
                )
                outline_model_fingerprint = outline_provider.model_fingerprint
                outline_temperature = outline_provider.temperature
            capability_version = skill_registration.version
            outline_is_current = self._active_inference_artifact_is_current(
                "ChapterBlueprint",
                capability_version=capability_version,
                prompt_version=outline_prompt_version,
                prompt_hash=outline_prompt_hash,
                output_schema_version=outline_schema_version,
                provider_fingerprint=outline_provider_fingerprint,
                model_fingerprint=outline_model_fingerprint,
                temperature=outline_temperature,
                optional_kinds=optional_dependencies,
            )
            if not outline_is_current and not uses_program_outline:
                (
                    fallback_prompt_version,
                    fallback_prompt_hash,
                    fallback_provider_fingerprint,
                    fallback_model_fingerprint,
                ) = self._outline_fallback_runtime_metadata()
                outline_is_current = self._active_inference_artifact_is_current(
                    "ChapterBlueprint",
                    capability_version=capability_version,
                    prompt_version=fallback_prompt_version,
                    prompt_hash=fallback_prompt_hash,
                    output_schema_version=outline_schema_version,
                    provider_fingerprint=fallback_provider_fingerprint,
                    model_fingerprint=fallback_model_fingerprint,
                    temperature=0.0,
                    optional_kinds=optional_dependencies,
                )
            if outline_is_current:
                return load_promoted_chapter_blueprint(self.context)

            planning_agent = PlanningAgent(self.context)
            used_outline_fallback = False
            if uses_program_outline:
                outline_candidate = self._deterministic_outline_candidate(
                    ledger,
                    scores,
                    template_structure,
                )
                blueprint = planning_agent.compile_outline_candidate(
                    outline_candidate,
                    ledger,
                    scores,
                    revision=base_revision + 1,
                    template_structure=template_structure,
                )
                outline_result = self._deterministic_result(
                    capability_id=OUTLINE_SKILL_ID,
                    capability_version=capability_version,
                    schema_version=outline_schema_version,
                    candidate=outline_candidate,
                    input_value=outline_request,
                    prompt_version=outline_prompt_version,
                    model_fingerprint=outline_model_fingerprint,
                    provider_fingerprint=outline_provider_fingerprint,
                )
            else:
                from .llm_telemetry import llm_stage_context

                try:
                    with llm_stage_context(
                        self.context,
                        operation_id,
                        "compile_chapter_blueprint",
                        capability_id=OUTLINE_SKILL_ID,
                        prompt_version=outline_provider.prompt_version,
                        schema_version=outline_provider.schema_version,
                        model=outline_provider.model_fingerprint,
                        temperature=outline_provider.temperature,
                    ):
                        outline_result = outline_provider.split(
                            outline_request
                        )
                except PlanningInferenceValidationError as exc:
                    if not self._outline_validation_can_fallback(exc):
                        raise
                    root_error = exc.__cause__ or exc
                    warning = (
                        "章节目录大模型结果未通过最终程序语义审核；"
                        "已使用保守确定性目录继续，需人工复核。原因："
                        f"{self._outline_warning_detail(root_error)}"
                    )
                    blueprint, outline_result = (
                        self._compile_outline_audit_fallback(
                            planning_agent=planning_agent,
                            ledger=ledger,
                            scores=scores,
                            template_structure=template_structure,
                            outline_request=outline_request,
                            base_revision=base_revision,
                            capability_version=capability_version,
                            schema_version=outline_schema_version,
                            warning=warning,
                            audit_findings=[
                                {
                                    "code": (
                                        "OUTLINE_INFERENCE_SEMANTIC_INVALID"
                                    ),
                                    "message": self._outline_warning_detail(
                                        root_error
                                    ),
                                }
                            ],
                        )
                    )
                    used_outline_fallback = True
                else:
                    if (
                        outline_result.capability_id
                        != skill_registration.skill_id
                        or outline_result.prompt_version
                        != OUTLINE_PROMPT_VERSION
                        or outline_result.prompt_hash
                        != skill_registration.prompt_hash
                        or outline_result.schema_version
                        != skill_registration.schema_version
                        or outline_result.provider_fingerprint
                        != outline_provider.provider_fingerprint
                        or outline_result.model_fingerprint
                        != outline_provider.model_fingerprint
                    ):
                        raise ControlPlaneError(
                            "V3_INTERNAL_SKILL_VERSION_MISMATCH",
                            "章节拆分推理结果与固定 Skill 注册版本不一致",
                            status_code=409,
                        )
                    blueprint = planning_agent.compile_outline_candidate(
                        outline_result.candidate,
                        ledger,
                        scores,
                        revision=base_revision + 1,
                        template_structure=template_structure,
                    )
                    batch_summary = getattr(
                        outline_provider,
                        "last_batch_summary",
                        {},
                    )
                    if isinstance(batch_summary, dict) and batch_summary:
                        blueprint = blueprint.model_copy(
                            update={
                                "coverage_summary": {
                                    **blueprint.coverage_summary,
                                    **{
                                        str(key): int(value)
                                        for key, value in batch_summary.items()
                                    },
                                }
                            }
                        )
            if not uses_program_outline and not used_outline_fallback:
                blueprint_audit = audit_chapter_blueprint(
                    blueprint,
                    ledger,
                    score_model=scores,
                    template_structure=template_structure,
                )
                if (
                    not bool(blueprint_audit.get("passed"))
                    and self._blueprint_audit_can_fallback(blueprint_audit)
                ):
                    warning = (
                        "章节目录最终程序语义审核未全部通过；"
                        "已使用保守确定性目录继续，需人工复核。审核项："
                        f"{self._outline_warning_detail(blueprint_audit.get('findings'))}"
                    )
                    blueprint, outline_result = (
                        self._compile_outline_audit_fallback(
                            planning_agent=planning_agent,
                            ledger=ledger,
                            scores=scores,
                            template_structure=template_structure,
                            outline_request=outline_request,
                            base_revision=base_revision,
                            capability_version=capability_version,
                            schema_version=outline_schema_version,
                            warning=warning,
                            audit_findings=[
                                dict(finding)
                                for finding in (
                                    blueprint_audit.get("findings") or []
                                )
                                if isinstance(finding, dict)
                            ],
                        )
                    )
                elif not bool(blueprint_audit.get("passed")):
                    message = (
                        "章节目录覆盖校验未全部通过："
                        f"{self._outline_warning_detail(blueprint_audit.get('findings'))}"
                    )
                    if self.validation_failure_blocks_pipeline:
                        from control_plane import ControlPlaneError

                        raise ControlPlaneError(
                            "V3_BLUEPRINT_COVERAGE_BLOCKED",
                            message,
                            status_code=409,
                            details={"blueprint_audit": blueprint_audit},
                        )
                    self._add_stage_warning(
                        "compile_chapter_blueprint",
                        code="V3_BLUEPRINT_COVERAGE_WARNING",
                        message=message,
                        details={"blueprint_audit": blueprint_audit},
                    )
            blueprint_op_id = operation_id or (
                f"blueprint:{ledger.revision}:{scores.revision}"
            )
            proposal = self._proposal_from_inference(
                artifact_kind="ChapterBlueprint",
                producer_role="planning_agent",
                payload=blueprint,
                base_revision=base_revision,
                operation_id=blueprint_op_id,
                result=outline_result,
                input_snapshot=outline_request,
                optional_dependency_kinds=optional_dependencies,
                capability_version=capability_version,
            )
            self._validate_gate_promote(
                proposal,
                producer_role="planning_agent",
                gate_id="G2_BLUEPRINT_INTEGRITY",
            )
            promoted_blueprint = load_promoted_chapter_blueprint(
                self.context
            )
            self._record_stage_output(
                operation_id,
                "compile_chapter_blueprint",
                phase="completed",
                products=[
                    self._blueprint_product(promoted_blueprint)
                ],
            )
            return promoted_blueprint

        if stage == "confirm_planning":
            from .artifact_promotion import HumanGateService

            # H1 is a deliberate pause.  StageRunner must never manufacture a user identity.
            service = HumanGateService(self.context)
            try:
                receipt = service.require_current_confirmation()
                return {"verdict": "pass", "planning_receipt_id": receipt.receipt_id}
            except Exception:
                return {"verdict": "needs_human", "planning_snapshot": service.planning_snapshot()}
        if stage == "sync_material_requirements":
            return MaterialRequirementsSynchronizer(self.context).sync()
        if stage == "compile_document_contract":
            return DocumentContractCompiler(self.context).compile()
        if stage == "plan_document":
            return DocumentPlanner(self.context).build()
        if stage == "execute_content_plan":
            from .artifact_promotion import HumanGateService
            from .writer_bundle import WriterInputBundleAssembler
            HumanGateService(self.context).require_current_confirmation()
            deterministic_writer = (
                self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
            )
            scheduler = ContentUnitScheduler(
                self.context,
                deterministic_test=deterministic_writer,
            )
            units = scheduler.initialize()
            blueprint = load_promoted_chapter_blueprint(self.context)
            requested_chapter_ids = list(
                dict.fromkeys(
                    getattr(self, "_generation_chapter_ids", []) or []
                )
            )
            if requested_chapter_ids:
                blueprint_by_id = {
                    node.chapter_id: node for node in blueprint.nodes
                }
                unknown = sorted(
                    set(requested_chapter_ids) - set(blueprint_by_id)
                )
                if unknown:
                    raise ValueError(
                        "DOCUMENT_WRITE_SCOPE_INVALID: 已确认目录中不存在章节 "
                        f"{unknown}"
                    )
                selected_ids: set[str] = set()
                for requested_id in requested_chapter_ids:
                    selected_ids.add(requested_id)
                    changed = True
                    while changed:
                        before = len(selected_ids)
                        selected_ids.update(
                            node.chapter_id
                            for node in blueprint.nodes
                            if node.parent_chapter_id in selected_ids
                        )
                        changed = len(selected_ids) != before
                scoped_units = []
                for unit in units:
                    node_ids = [
                        node_id for node_id in unit.node_ids
                        if node_id in selected_ids
                    ]
                    if not node_ids:
                        continue
                    scoped_units.append(
                        unit.model_copy(
                            update={
                                "unit_id": "unit-scope-" + node_ids[0],
                                "node_ids": node_ids,
                            }
                        )
                    )
                units = scoped_units
            title_by_chapter = {
                node.chapter_id: node.title
                for node in blueprint.nodes
            }
            deferred_tokens = ("商务", "报价", "价格", "财务", "业绩", "资质", "人员", "资格")
            technical_units = [
                unit for unit in units
                if all(
                    not any(
                        token in title_by_chapter.get(node_id, "")
                        for token in deferred_tokens
                    )
                    for node_id in unit.node_ids
                )
            ]
            deferred_units = [
                unit for unit in units
                if unit not in technical_units
            ]
            writer = (
                ContentWriter.for_deterministic_tests(self.context)
                if deterministic_writer
                else ContentWriter(self.context)
            )
            assembler = WriterInputBundleAssembler(
                self.context,
                deterministic_test=deterministic_writer,
            )
            from .writer_research import writer_research_enabled

            # Public policy/method research is allowed when a research provider
            # is configured. Enterprise facts still require operator materials;
            # WriterResearchCoordinator hard-blocks those scopes.
            allow_writer_research = (
                not deterministic_writer and writer_research_enabled()
            )
            results = []
            for unit in technical_units:
                current = scheduler.store.content_unit_state(unit.unit_id) or {}
                if str(current.get("state") or "") == "completed":
                    continue
                scheduler.mark_running(unit)
                try:
                    bundle = assembler.assemble(unit.unit_id, unit.node_ids)
                    results.append(
                        writer.write_bundle(
                            bundle,
                            operation_id=operation_id or "",
                            enable_writer_research=allow_writer_research,
                        )
                    )
                except Exception as exc:
                    if getattr(exc, "code", "") in {
                        "WRITER_RESEARCH_ACTION_REQUIRED",
                        "WRITER_MODEL_ACTION_REQUIRED",
                    }:
                        scheduler.mark_blocked(unit, exc)
                    else:
                        scheduler.mark_failed(unit, exc)
                    raise
            if deferred_units:
                raise ControlPlaneError(
                    "TECHNICAL_DRAFT_READY",
                    "技术章节已写完；商务部分和价格部分按当前要求暂不写入。",
                    details={"deferred_unit_ids": [unit.unit_id for unit in deferred_units]},
                )
            return results
        if stage == "integrate_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .document_planner import DOCUMENT_PLAN_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, DocumentPlan
            contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root / DOCUMENT_CONTRACT_PATH))
            plan = DocumentPlan.model_validate(read_json(self.context.root / DOCUMENT_PLAN_PATH))
            return DocumentIntegrator(
                self.context,
                deterministic_test=(
                    self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
                ),
            ).integrate(
                contract_revision=contract.revision,
                plan_revision=plan.revision,
            )
        if stage == "verify_document":
            from .writer_policy import require_all_content_units_fresh

            require_all_content_units_fresh(
                self.context,
                deterministic_test=(
                    self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
                ),
                code="QUALITY_GATE_STALE_CONTENT",
            )
            report = QualityGate(self.context).verify()
            if report.verdict != "pass":
                message = (
                    f"终稿质量校验返回 {report.verdict}，"
                    f"发现 {len(report.findings)} 个问题。"
                )
                if self.validation_failure_blocks_pipeline:
                    raise ValueError(f"QUALITY_GATE_BLOCKED: {message}")
                self._add_stage_warning(
                    "verify_document",
                    code="V3_DOCUMENT_QUALITY_WARNING",
                    message=message,
                    details={"findings": report.findings},
                )
            return report
        if stage == "render_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, TemplateContract
            from .writer_policy import require_all_content_units_fresh

            require_all_content_units_fresh(
                self.context,
                deterministic_test=(
                    self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
                ),
                code="RENDER_BLOCKED_STALE_CONTENT",
            )
            quality_path = self.context.root / CONTENT_QUALITY_PATH

            if not quality_path.is_file():
                raise ValueError("RENDER_BLOCKED: 尚未执行内容质量门禁")
            quality = read_json(quality_path)
            if (
                quality.get("verdict") != "pass"
                and self.validation_failure_blocks_pipeline
            ):
                raise ValueError("RENDER_BLOCKED: 内容质量门禁未通过")
            contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root / DOCUMENT_CONTRACT_PATH))
            if isinstance(contract, TemplateContract):
                output = StrictTemplateRenderer(self.context).render()
                return (
                    output,
                    self.context.root
                    / "outputs"
                    / "v3"
                    / "final.md",
                )
            return StandardRenderer(self.context).render()
        if stage == "verify_delivery":
            from .writer_policy import require_all_content_units_fresh

            require_all_content_units_fresh(
                self.context,
                deterministic_test=(
                    self.inference_mode == _INFERENCE_MODE_DETERMINISTIC_TEST
                ),
                code="DELIVERY_BLOCKED_STALE_CONTENT",
            )
            return DeliveryVerifier(
                self.context,
                allow_quality_warnings=(
                    not self.validation_failure_blocks_pipeline
                ),
                operation_id=operation_id,
            ).verify()
        raise ValueError(f"V3_UNKNOWN_STAGE: {stage}")
