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
    PROJECT_INPUT_MAX_CHARS,
    PROJECT_INPUT_PROJECTION_VERSION,
    PROJECT_INPUT_TARGET_CHARS,
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

PROJECT_SOURCE_BLOCK_LIMIT = 18
PROJECT_SOURCE_TEXT_LIMIT = 600

_PROJECT_PRIORITY_TOKENS = (
    ("blocking", 100),
    ("阻断", 100),
    ("deliverable", 90),
    ("交付", 90),
    ("acceptance", 85),
    ("验收", 85),
    ("contract", 80),
    ("合同", 80),
    ("project_name", 75),
    ("项目名称", 75),
    ("project", 70),
    ("项目", 70),
    ("goal", 65),
    ("目标", 65),
    ("scope", 60),
    ("范围", 60),
    ("schedule", 55),
    ("工期", 55),
    ("role", 50),
    ("角色", 50),
    ("constraint", 45),
    ("约束", 45),
)

_PROJECT_EXCLUDED_SECTION_TOKENS = (
    "评标办法",
    "评标方法",
    "评分办法",
    "评分标准",
    "评审标准",
    "评分标准",
    "投标文件格式",
    "响应文件格式",
    "报价文件格式",
    "开标一览表",
    "附件格式",
    "商品包装",
    "快递包装",
)

_PROJECT_FACT_CATEGORY_TOKENS = (
    ("name", ("项目名称", "采购项目名称")),
    ("background", ("项目背景", "工作背景", "建设背景", "项目概况")),
    ("goal", ("项目目标", "工作目标", "建设目标", "总体目标")),
    (
        "scope",
        (
            "项目范围",
            "采购范围",
            "服务范围",
            "工作范围",
            "工作内容",
            "主要任务",
            "主要工作",
            "建设内容",
        ),
    ),
    ("deliverable", ("交付成果", "成果清单", "成果要求", "提交成果")),
    ("acceptance", ("验收标准", "验收要求", "成果验收")),
    ("schedule", ("服务期限", "项目周期", "工作期限", "工期要求")),
    ("role", ("人员要求", "人员配置", "项目团队", "组织分工")),
    ("constraint", ("质量要求", "保密要求", "安全要求", "技术要求")),
)


def _project_block_rank(
    block: SourceBlock,
    *,
    cited_chunks: set[str],
) -> int:
    heading_text = " ".join(block.heading_path)
    text = f"{heading_text} {block.content}"
    score = 30 if block.source_anchor.chunk_id in cited_chunks else 0
    score += _project_priority(
        text,
        kind=block.block_kind,
        severity=block.input_role.value,
    )
    if "采购需求" in heading_text:
        score += 100
    if "合同条款" in heading_text:
        score -= 35
    if any(token in text for token in _PROJECT_EXCLUDED_SECTION_TOKENS):
        score -= 300
    score += 12 * sum(
        1
        for _, tokens in _PROJECT_FACT_CATEGORY_TOKENS
        if any(token in text for token in tokens)
    )
    return score


def _project_priority(text: str, *, kind: str = "", severity: str = "") -> int:
    haystack = f"{kind} {severity} {text}".casefold()
    # The weights encode an ordered bucket, rather than an additive score:
    # several low-priority hints must not outrank one blocking/acceptance hint.
    return max(
        (weight for token, weight in _PROJECT_PRIORITY_TOKENS if token.casefold() in haystack),
        default=0,
    )


def select_planning_source_context(
    source_blocks: list[SourceBlock],
    *,
    requirement_chunk_ids: set[str],
    score_chunk_ids: set[str],
    compact: bool = False,
    project_input: bool = False,
) -> list[dict[str, Any]]:
    """Select the exact bounded source context supplied to planning Providers."""

    cited_chunks = (
        requirement_chunk_ids
        if project_input
        else requirement_chunk_ids | score_chunk_ids
    )
    selected = [
        block
        for block in source_blocks
        if (
            not project_input
            or block.input_role
            in {InputRole.TENDER, InputRole.AMENDMENT, InputRole.COMPANY}
        )
        and (
            block.source_anchor.chunk_id in cited_chunks
            or (
                not project_input
                and block.input_role == InputRole.COMPANY
            )
            or (
                block.input_role
                in (
                    {InputRole.TENDER, InputRole.AMENDMENT}
                    if project_input
                    else {InputRole.TENDER, InputRole.AMENDMENT, InputRole.SCORE}
                )
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
        )
    ]
    if not compact:
        return [block.model_dump(mode="json") for block in selected]

    # SourceBlock contains parser/layout metadata which is useful for the
    # frozen SourceIndex, but not for semantic planning.  Sending all of it
    # made a large tender produce >1 MB requests and the local gateway returned
    # HTTP 400 before the model was invoked.  Keep every selected block and its
    # stable anchors, while omitting only non-semantic layout/hash fields.
    if project_input:
        # Project facts must not depend on a brittle keyword hit.  Real tender
        # documents often describe the work directly without headings such as
        # “项目范围”.  Rank every tender/amendment block, while still placing
        # cited and project-like blocks first.
        project_blocks = [
            block
            for block in source_blocks
            if block.input_role
            in {InputRole.TENDER, InputRole.AMENDMENT, InputRole.COMPANY}
        ]
        ranked = sorted(
            enumerate(project_blocks),
            key=lambda pair: (
                -_project_block_rank(pair[1], cited_chunks=cited_chunks),
                pair[0],
            ),
        )
        pinned: list[SourceBlock] = []
        for _, tokens in _PROJECT_FACT_CATEGORY_TOKENS:
            match = next(
                (
                    block
                    for _, block in ranked
                    if any(
                        token in " ".join((*block.heading_path, block.content))
                        for token in tokens
                    )
                    and not any(
                        excluded
                        in " ".join((*block.heading_path, block.content[:120]))
                        for excluded in _PROJECT_EXCLUDED_SECTION_TOKENS
                    )
                ),
                None,
            )
            if match is not None and match not in pinned:
                pinned.append(match)
        selected = []
        seen_block_ids: set[str] = set()
        for block in [*pinned, *(item for _, item in ranked)]:
            if block.block_id in seen_block_ids:
                continue
            seen_block_ids.add(block.block_id)
            selected.append(block)
            if len(selected) >= PROJECT_SOURCE_BLOCK_LIMIT:
                break
    return [
        {
            "block_id": block.block_id,
            "input_id": block.input_id,
            "input_role": block.input_role.value,
            "block_kind": block.block_kind,
            "ordinal": block.ordinal,
            "content": (
                block.content
            ),
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


def _project_requirement_snapshot(
    ledger: RequirementLedger,
    *,
    source_chunk_ids: set[str],
    requirement_limit: int | None = None,
    text_limit: int | None = None,
) -> dict[str, Any]:
    """Keep the bounded requirement contract consumed by project providers.

    Raw SourceIndex blocks remain the primary prose input.  Requirement IDs and
    their normalized statements are retained because the provider and compiler
    must agree on exact coverage and provenance.
    """

    kind_priority = {
        "mandatory": 5,
        "deliverable": 4,
        "acceptance": 3,
        "contract": 2,
        "qualification": 1,
    }
    ranked = sorted(
        enumerate(ledger.requirements),
        key=lambda pair: (
            0 if pair[1].status not in {"blocked", "waived"} else 1,
            0 if pair[1].severity == "blocking" else 1,
            0 if pair[1].source_anchor.chunk_id in source_chunk_ids else 1,
            -kind_priority.get(pair[1].kind.value, 0),
            pair[0],
        ),
    )
    selected = [item for _, item in ranked]
    if requirement_limit is not None:
        selected = selected[:requirement_limit]
    requirements = []
    for item in selected:
        normalized = item.normalized_requirement
        if text_limit is not None:
            normalized = normalized[:text_limit]
        requirements.append(
            {
                "requirement_id": item.requirement_id,
                "kind": item.kind.value,
                "normalized_requirement": normalized,
                "status": item.status,
                "severity": item.severity,
            }
        )
    return {
        "projection_version": PROJECT_INPUT_PROJECTION_VERSION,
        "revision": ledger.revision,
        "total_requirement_count": len(ledger.requirements),
        "selected_requirement_count": len(requirements),
        "omitted_requirement_count": len(ledger.requirements) - len(requirements),
        "requirements": requirements,
    }


def _bounded_project_source_context(
    source_context: list[dict[str, Any]],
    ledger: RequirementLedger,
    scanned_source_block_count: int,
    review_feedback: str = "",
    *,
    max_chars: int = PROJECT_INPUT_TARGET_CHARS,
    batch_id: str = "project-single",
    batch_index: int = 1,
    batch_count: int = 1,
) -> ProjectUnderstandingInput:
    """Build a non-empty, raw-source-first request within the input budget."""

    def make(
        blocks: list[dict[str, Any]],
        ledger_snapshot: dict[str, Any],
    ) -> ProjectUnderstandingInput:
        return ProjectUnderstandingInput(
            requirement_ledger=ledger_snapshot,
            source_context=blocks,
            scanned_source_block_count=scanned_source_block_count,
            review_feedback=str(review_feedback or "").strip()[:2000],
            batch_id=batch_id,
            batch_index=batch_index,
            batch_count=batch_count,
        )

    chunk_ids = {
        str((block.get("source_anchor") or {}).get("chunk_id") or "")
        for block in source_context
        if isinstance(block, dict)
    }
    limits = [
        (None, None),
        (80, 320),
        (40, 240),
        (24, 160),
        (12, 120),
        (6, 80),
        (0, 0),
    ]
    for requirement_limit, text_limit in limits:
        snapshot = _project_requirement_snapshot(
            ledger,
            source_chunk_ids=chunk_ids,
            requirement_limit=requirement_limit,
            text_limit=text_limit,
        )
        candidate = make(source_context, snapshot)
        if len(canonical_json(candidate.model_dump(mode="json"))) <= max_chars:
            return candidate
    block_id = str((source_context[0] if source_context else {}).get("block_id") or "")
    actual_chars = len(canonical_json(candidate.model_dump(mode="json")))
    error = ValueError(
        "单个项目理解原文块连同最小协议开销仍超过配置上限: "
        f"block_id={block_id}, chars={actual_chars}, max_chars={max_chars}; "
        "请在源文件预处理阶段按完整段落或表格边界拆分该块，或提高配置上限。"
    )
    error.code = "PROJECT_INPUT_BLOCK_EXCEEDS_MAX"
    error.retryable = False
    raise error


def build_project_understanding_input_batches(
    ledger: RequirementLedger,
    source_index: SourceIndex,
    *,
    review_feedback: str = "",
) -> list[ProjectUnderstandingInput]:
    source_context = select_planning_source_context(
        list(source_index.blocks),
        requirement_chunk_ids={
            item.source_anchor.chunk_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        },
        score_chunk_ids=set(),
        compact=True,
        project_input=True,
    )
    if not source_context:
        return [
            _bounded_project_source_context(
                source_context,
                ledger,
                len(source_index.blocks),
                review_feedback,
                max_chars=PROJECT_INPUT_MAX_CHARS,
            )
        ]

    grouped: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for block in source_context:
        proposed = [*current, block]
        try:
            _bounded_project_source_context(
                proposed,
                ledger,
                len(source_index.blocks),
                review_feedback,
                max_chars=PROJECT_INPUT_TARGET_CHARS,
            )
        except ValueError:
            if current:
                grouped.append(current)
                current = []
            _bounded_project_source_context(
                [block],
                ledger,
                len(source_index.blocks),
                review_feedback,
                max_chars=PROJECT_INPUT_MAX_CHARS,
            )
            current = [block]
        else:
            current = proposed
    if current:
        grouped.append(current)

    batch_count = len(grouped)
    batches: list[ProjectUnderstandingInput] = []
    for index, blocks in enumerate(grouped, start=1):
        digest = canonical_hash(
            {
                "revision": ledger.revision,
                "block_ids": [block.get("block_id") for block in blocks],
                "review_feedback": review_feedback,
            }
        )
        batches.append(
            _bounded_project_source_context(
                blocks,
                ledger,
                len(source_index.blocks),
                review_feedback,
                max_chars=PROJECT_INPUT_MAX_CHARS,
                batch_id=f"project-{digest[:16]}",
                batch_index=index,
                batch_count=batch_count,
            )
        )
    return batches


def build_project_understanding_input(
    ledger: RequirementLedger,
    source_index: SourceIndex,
    *,
    review_feedback: str = "",
) -> ProjectUnderstandingInput:
    candidate = build_project_understanding_input_batches(
        ledger,
        source_index,
        review_feedback=review_feedback,
    )[0]
    has_project_text = any(
        str(block.get("content") or "").strip()
        for block in candidate.source_context
    ) or any(
        str(item.get("normalized_requirement") or "").strip()
        for item in candidate.requirement_ledger.get("requirements", [])
    )
    if not has_project_text:
        error = ValueError(
            "ProjectUnderstandingInput 缺少可用于项目理解的招标正文或项目要求"
        )
        error.code = "PROJECT_INPUT_MISSING_TENDER_EVIDENCE"
        error.retryable = True
        error.details = {
            "input_chars": len(canonical_json(candidate.model_dump(mode="json"))),
            "source_block_count": len(candidate.source_context),
            "scanned_source_block_count": len(source_index.blocks),
            "missing": "tender_project_evidence",
        }
        raise error
    return candidate


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
    elif artifact_kind == "ProjectModel":
        source_index = SourceIndex.model_validate(
            dependency_payloads["SourceIndex"]
        )
        request = build_project_understanding_input(
            ledger,
            source_index,
        )
    else:
        scores = ScoreModel.model_validate(dependency_payloads["ScoreModel"])
        if artifact_kind == "ResponseTopicGraph":
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
            if proposal_payload.get("planning_model") == "rewrite_merge":
                from .chapter_outline_skill import build_chapter_outline
                from .contracts import LegacyBidIndex
                from .rewrite_outline_merge_skill import build_rewrite_outline_merge_input

                initial = build_chapter_outline(
                    ledger,
                    scores,
                    template,
                    annotations=None,
                )
                request = build_rewrite_outline_merge_input(
                    initial,
                    ledger,
                    scores,
                    ProjectModel.model_validate(dependency_payloads["ProjectModel"]),
                    LegacyBidIndex.model_validate(dependency_payloads["LegacyBidIndex"]),
                    template,
                )
            else:
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
