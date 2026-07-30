"""Deterministic construction and verification of controlled inference inputs."""

from __future__ import annotations

from typing import Any

from control_plane import WorkspaceContext

from .canonicalization import canonical_hash, canonical_json
from .contracts import (
    InputRole,
    ProjectModel,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreModel,
    SourceBlock,
    SourceIndex,
    TemplateStructureContract,
)
from .planning_inference import (
    OutlineDecompositionInput,
    ProjectUnderstandingInput,
    TopicDutyPlanningInput,
)
from .score_agent import ScoreAgent
from .score_semantic import (
    SCORE_SEMANTIC_DEFAULT_BATCH_CHARS,
    ScoreSemanticBatch,
    ScoreSemanticInput,
    build_score_semantic_batches,
)


PROJECT_CONTEXT_TOKENS = (
    "项目概况",
    "项目背景",
    "建设背景",
    "采购背景",
    "项目目标",
    "建设目标",
    "服务目标",
    "工作目标",
    "采购范围",
    "服务范围",
    "工作范围",
    "项目范围",
    "采购内容",
    "工作内容",
    "主要任务",
    "总体要求",
    "项目需求",
)


def select_planning_source_context(
    source_blocks: list[SourceBlock],
    *,
    requirement_chunk_ids: set[str],
    score_chunk_ids: set[str],
    compact: bool = False,
) -> list[dict[str, Any]]:
    """Select the exact bounded source context supplied to planning Providers."""

    cited_chunks = requirement_chunk_ids | score_chunk_ids
    selected = [
        block
        for block in source_blocks
        if block.source_anchor.chunk_id in cited_chunks
        or block.input_role == InputRole.COMPANY
        or (
            block.input_role
            in {
                InputRole.TENDER,
                InputRole.AMENDMENT,
                InputRole.SCORE,
            }
            and (
                block.block_kind == "heading"
                or any(
                    token
                    in " ".join(
                        (
                            *block.heading_path,
                            block.content[:240],
                        )
                    )
                    for token in PROJECT_CONTEXT_TOKENS
                )
            )
        )
    ]
    if not compact:
        return [block.model_dump(mode="json") for block in selected]

    # SourceBlock contains parser/layout metadata which is useful for the
    # frozen SourceIndex, but not for semantic planning.  Sending all of it
    # made a large tender produce >1 MB requests and the local gateway returned
    # HTTP 400 before the model was invoked.  Keep every selected block and its
    # stable anchors, while omitting only non-semantic layout/hash fields.
    return [
        {
            "block_id": block.block_id,
            "input_id": block.input_id,
            "input_role": block.input_role.value,
            "block_kind": block.block_kind,
            "ordinal": block.ordinal,
            "content": block.content,
            "heading_path": list(block.heading_path),
            "source_anchor": {
                "source_input_id": block.source_anchor.source_input_id,
                "chunk_id": block.source_anchor.chunk_id,
                "page": block.source_anchor.page,
                "location": block.source_anchor.location,
            },
        }
        for block in selected
    ]


def _project_requirement_snapshot(ledger: RequirementLedger) -> dict[str, Any]:
    """Return the semantic subset of the ledger needed by project planning.

    ``coverage_audit`` is a promotion/audit record and can contain the full
    batch classification transcript.  It is not an input fact for project
    understanding, so including it needlessly inflated the prompt by roughly
    150k characters in the failing workspace.
    """

    requirements: list[dict[str, Any]] = []
    for item in ledger.requirements:
        value = item.model_dump(mode="json")
        snapshot = {
            key: value[key]
            for key in (
                "requirement_id",
                "kind",
                "normalized_requirement",
                "status",
                "severity",
            )
            if key in value
        }
        # Preserve the source wording only when normalization changed it.  In
        # the common case the two fields are identical and duplicating them
        # contributes substantial prompt noise without new evidence.
        if value.get("original_text") != value.get("normalized_requirement"):
            snapshot["original_text"] = value.get("original_text")
        requirements.append(snapshot)

    return {"requirements": requirements}


def build_project_understanding_input(
    ledger: RequirementLedger,
    scores: ScoreModel,
    source_index: SourceIndex,
) -> ProjectUnderstandingInput:
    source_context = select_planning_source_context(
        list(source_index.blocks),
        requirement_chunk_ids={
            item.source_anchor.chunk_id for item in ledger.requirements
        },
        score_chunk_ids={
            anchor.chunk_id
            for point in scores.points
            for anchor in point.source_anchors
        },
        compact=True,
    )
    return ProjectUnderstandingInput(
        requirement_ledger=_project_requirement_snapshot(ledger),
        score_model=scores.model_dump(mode="json"),
        source_context=source_context,
    )


def build_score_semantic_input(
    scores: ScoreModel,
    source_index: SourceIndex,
    ledger: RequirementLedger | None = None,
) -> ScoreSemanticInput:
    """Build the frozen score semantic package from canonical dependencies."""

    return ScoreAgent.semantic_input(
        scores,
        list(source_index.blocks),
        ledger,
    )


def build_score_semantic_input_batches(
    scores: ScoreModel,
    source_index: SourceIndex,
    ledger: RequirementLedger | None = None,
    *,
    max_input_chars: int = SCORE_SEMANTIC_DEFAULT_BATCH_CHARS,
) -> list[ScoreSemanticBatch]:
    """Expose independently fingerprinted score-group batches for orchestration."""

    return build_score_semantic_batches(
        build_score_semantic_input(scores, source_index, ledger),
        max_input_chars=max_input_chars,
    )


def build_topic_duty_planning_input(
    project: ProjectModel,
    ledger: RequirementLedger,
    scores: ScoreModel,
    source_index: SourceIndex,
) -> TopicDutyPlanningInput:
    source_context = select_planning_source_context(
        list(source_index.blocks),
        requirement_chunk_ids={
            item.source_anchor.chunk_id for item in ledger.requirements
        },
        score_chunk_ids={
            anchor.chunk_id
            for point in scores.points
            for anchor in point.source_anchors
        },
    )
    return TopicDutyPlanningInput(
        project_model=project.model_dump(mode="json"),
        requirement_ledger=ledger.model_dump(mode="json"),
        score_model=scores.model_dump(mode="json"),
        source_context=source_context,
    )


def build_outline_decomposition_input(
    ledger: RequirementLedger,
    scores: ScoreModel,
    template: TemplateStructureContract | None,
) -> OutlineDecompositionInput:
    """Build the final outline request from score semantics, not full contracts.

    The outline model needs every response unit and full-score condition, plus
    only their associated procurement requirements.  It does not need ledger
    coverage metadata, scoring-band duplicates, source hash maps, or unrelated
    mandatory clauses.
    """

    candidate_requirement_ids = {
        requirement_id
        for point in scores.points
        if point.review_status != "blocked"
        for unit in point.response_units
        if unit.review_status != "blocked"
        for requirement_id in unit.linked_requirement_ids
    }
    requirements = [
        item
        for item in ledger.requirements
        if item.status not in {"blocked", "waived"}
        and item.kind.value != "score"
        and item.requirement_id in candidate_requirement_ids
    ]
    linked_requirement_ids = {
        item.requirement_id for item in requirements
    }
    requirement_projection = {
        "revision": ledger.revision,
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "kind": item.kind.value,
                "normalized_requirement": item.normalized_requirement,
                "original_text": item.original_text,
                "severity": item.severity,
                "response_type": item.response_type,
                "evidence_policy": item.evidence_policy,
                "status": item.status,
                "clause_id": item.clause_id,
                "parent_clause_id": item.parent_clause_id,
                "subject": item.subject,
                "source_anchor": item.source_anchor.model_dump(mode="json"),
            }
            for item in requirements
        ],
    }
    score_projection = {
        "revision": scores.revision,
        "model_id": scores.model_id,
        "total_points": scores.total_points,
        "groups": [
            group.model_dump(mode="json")
            for group in scores.groups
        ],
        "points": [
            {
                "score_point_id": point.score_point_id,
                "group_id": point.group_id,
                "title": point.title,
                "max_points": point.max_points,
                "disqualifying": point.disqualifying,
                "response_expectation": point.response_expectation,
                "linked_requirement_ids": list(
                    requirement_id
                    for requirement_id in point.linked_requirement_ids
                    if requirement_id in linked_requirement_ids
                ),
                "context_requirement_ids": list(
                    requirement_id
                    for requirement_id in point.context_requirement_ids
                    if requirement_id in linked_requirement_ids
                ),
                "score_conditions": [
                    {
                        "condition_id": condition.condition_id,
                        "text": condition.text,
                        "normalized_condition": (
                            condition.normalized_condition
                        ),
                        "condition_role": condition.condition_role,
                        "source_excerpt": condition.source_excerpt,
                        "source_level_id": condition.source_level_id,
                        "subject": condition.subject,
                        "response_intent": condition.response_intent,
                        "source_anchor": (
                            condition.source_anchor.model_dump(mode="json")
                            if condition.source_anchor is not None
                            else None
                        ),
                        "source_span_start": condition.source_span_start,
                        "source_span_end": condition.source_span_end,
                        "review_status": condition.review_status,
                    }
                    for condition in point.score_conditions
                ],
                "response_units": [
                    {
                        **unit.model_dump(mode="json"),
                        "linked_requirement_ids": [
                            requirement_id
                            for requirement_id in unit.linked_requirement_ids
                            if requirement_id in linked_requirement_ids
                        ],
                    }
                    for unit in point.response_units
                ],
                "review_status": point.review_status,
            }
            for point in scores.points
        ],
    }
    return OutlineDecompositionInput(
        requirement_ledger=requirement_projection,
        score_model=score_projection,
        template_structure=(
            template.model_dump(mode="json")
            if template is not None
            else None
        ),
        document_mode=(
            "template_strict" if template is not None else "auto_outline"
        ),
    )


def reconstruct_inference_input_snapshot(
    context: WorkspaceContext,
    *,
    artifact_kind: str,
    proposal_payload: dict[str, Any],
    dependency_payloads: dict[str, dict[str, Any]],
) -> str:
    """Rebuild exact Provider input solely from frozen promoted dependencies."""

    ledger = RequirementLedger.model_validate(
        dependency_payloads["RequirementLedger"]
    )
    scores: ScoreModel
    source_index: SourceIndex
    if artifact_kind == "ScoreModel":
        source_index = SourceIndex.model_validate(
            dependency_payloads["SourceIndex"]
        )
        structural = ScoreAgent(context).build_score_model(
            list(source_index.blocks),
            ledger,
            revision=int(proposal_payload.get("revision") or 1),
            source_hashes=dict(source_index.source_hashes),
        )
        if not structural.points:
            request: Any = {
                "source_snapshot_hash": canonical_hash(
                    source_index.source_hashes
                ),
                "total_points": structural.total_points,
                "groups": [],
                "rules": [],
            }
        else:
            request = build_score_semantic_input(
                structural,
                source_index,
                ledger,
            )
    else:
        scores = ScoreModel.model_validate(dependency_payloads["ScoreModel"])
        if artifact_kind == "ProjectModel":
            source_index = SourceIndex.model_validate(
                dependency_payloads["SourceIndex"]
            )
            request = build_project_understanding_input(
                ledger,
                scores,
                source_index,
            )
        elif artifact_kind == "ResponseTopicGraph":
            source_index = SourceIndex.model_validate(
                dependency_payloads["SourceIndex"]
            )
            project = ProjectModel.model_validate(
                dependency_payloads["ProjectModel"]
            )
            request = build_topic_duty_planning_input(
                project,
                ledger,
                scores,
                source_index,
            )
        elif artifact_kind == "ChapterBlueprint":
            template_payload = dependency_payloads.get(
                "TemplateStructureContract"
            )
            template = (
                TemplateStructureContract.model_validate(template_payload)
                if template_payload is not None
                else None
            )
            request = build_outline_decomposition_input(
                ledger,
                scores,
                template,
            )
        else:
            raise ValueError(
                f"{artifact_kind} 不属于受控推理 Artifact"
            )
    return canonical_json(
        request.model_dump(mode="json")
        if hasattr(request, "model_dump")
        else request
    )
