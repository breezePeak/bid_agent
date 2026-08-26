from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import LegacyBidIndex, RequirementLedger, ScoreModel
from .legacy_bid_semantic import LegacyBidSemanticReranker
from .planning_inference import (
    ChapterOutlineCandidate,
    ChapterOutlineNodeCandidate,
    DEFAULT_TEMPERATURE,
    MAX_REPAIR_ATTEMPTS,
    REWRITE_OUTLINE_CAPABILITY_VERSION,
    REWRITE_OUTLINE_PROMPT_FILE,
    REWRITE_OUTLINE_PROMPT_VERSION,
    REWRITE_OUTLINE_SCHEMA_VERSION,
    REWRITE_OUTLINE_SKILL_ID,
    REWRITE_OUTLINE_STRUCTURE_PROMPT_FILE,
    REWRITE_CHAPTER_CONTENT_PROMPT_FILE,
    StrictPlanningModel,
    StructuredInferenceResult,
    PlanningInferenceError,
    _StructuredLLMProvider,
    _canonical_json,
    rewrite_outline_prompt_hash,
)


RewriteMode = Literal["copy", "light_edit", "restructure", "new_write"]

REWRITE_STRUCTURE_MAX_INPUT_CHARS = max(
    12_000, int(os.getenv("REWRITE_STRUCTURE_MAX_INPUT_CHARS", "60000"))
)
REWRITE_CONTENT_MAX_BLOCKS = max(
    1, int(os.getenv("REWRITE_CONTENT_MAX_BLOCKS", "8"))
)
REWRITE_CONTENT_MAX_INPUT_CHARS = max(
    8_000, int(os.getenv("REWRITE_CONTENT_MAX_INPUT_CHARS", "12000"))
)
REWRITE_BLOCK_MAX_CHARS = max(
    500, int(os.getenv("REWRITE_BLOCK_MAX_CHARS", "2000"))
)


class ResponsibilityText(StrictPlanningModel):
    requirement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ConditionText(StrictPlanningModel):
    condition_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class InitialOutlineCard(StrictPlanningModel):
    node_id: str = Field(min_length=1)
    parent_node_id: str | None = None
    path: list[str] = Field(min_length=1)
    depth: int = Field(ge=1)
    order: int = Field(default=0, ge=0)
    title: str = Field(min_length=1)
    child_titles: list[str] = Field(default_factory=list)
    is_leaf: bool = False
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    direct_response_unit_ids: list[str] = Field(default_factory=list)
    direct_condition_ids: list[str] = Field(default_factory=list)
    direct_requirement_ids: list[str] = Field(default_factory=list)
    subtree_response_unit_ids: list[str] = Field(default_factory=list)
    subtree_condition_ids: list[str] = Field(default_factory=list)
    subtree_requirement_ids: list[str] = Field(default_factory=list)
    requirements: list[ResponsibilityText] = Field(default_factory=list)
    score_conditions: list[ConditionText] = Field(default_factory=list)


class LegacyBlockCard(StrictPlanningModel):
    block_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    content: str = Field(min_length=1)


class LegacySectionCard(StrictPlanningModel):
    section_id: str = Field(min_length=1)
    parent_section_id: str | None = None
    path: list[str] = Field(min_length=1)
    depth: int = Field(ge=1)
    order: int = Field(ge=0)
    title: str = Field(min_length=1)
    child_titles: list[str] = Field(default_factory=list)
    direct_content: str = ""
    blocks: list[LegacyBlockCard] = Field(default_factory=list)
    candidate_target_ids: list[str] = Field(default_factory=list)


class RewriteOutlineMergeInput(StrictPlanningModel):
    requirement_ledger: dict[str, Any]
    score_model: dict[str, Any]
    project_model: dict[str, Any]
    template_structure: dict[str, Any] | None = None
    document_mode: Literal["auto_outline", "template_strict"] = "auto_outline"
    initial_outline: list[InitialOutlineCard] = Field(min_length=1)
    legacy_sections: list[LegacySectionCard] = Field(min_length=1)
    review_feedback: str = ""


class LegacyOutlineCard(StrictPlanningModel):
    section_id: str = Field(min_length=1)
    parent_section_id: str | None = None
    path: list[str] = Field(min_length=1)
    depth: int = Field(ge=1)
    order: int = Field(ge=0)
    title: str = Field(min_length=1)
    child_titles: list[str] = Field(default_factory=list)


class RewriteOutlineStructureMatchInput(StrictPlanningModel):
    document_mode: Literal["auto_outline", "template_strict"] = "auto_outline"
    initial_outline: list[InitialOutlineCard] = Field(min_length=1)
    legacy_outline: list[LegacyOutlineCard] = Field(min_length=1)
    review_feedback: str = ""


class RewriteOutlineStructureAlignment(StrictPlanningModel):
    legacy_section_id: str = Field(min_length=1)
    target_node_id: str | None = None
    placement: Literal["same_scope", "child_detail", "ignore"]
    matched_response_unit_ids: list[str] = Field(default_factory=list)
    matched_condition_ids: list[str] = Field(default_factory=list)
    matched_requirement_ids: list[str] = Field(default_factory=list)
    purpose: str = ""
    writing_objectives: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False

    @model_validator(mode="after")
    def relation_is_consistent(self) -> "RewriteOutlineStructureAlignment":
        if self.placement == "ignore":
            if self.target_node_id is not None:
                raise ValueError("ignore 不得声明 target_node_id")
            return self
        if not self.target_node_id:
            raise ValueError("非 ignore 目录匹配必须声明 target_node_id")
        if not (
            self.matched_response_unit_ids
            or self.matched_condition_ids
            or self.matched_requirement_ids
        ):
            raise ValueError("非 ignore 目录匹配必须引用至少一个真实责任 ID")
        return self


class RewriteSupplementalNode(StrictPlanningModel):
    target_node_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    matched_response_unit_ids: list[str] = Field(default_factory=list)
    matched_condition_ids: list[str] = Field(default_factory=list)
    matched_requirement_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False

    @model_validator(mode="after")
    def has_real_responsibility(self) -> "RewriteSupplementalNode":
        if not (
            self.matched_response_unit_ids
            or self.matched_condition_ids
            or self.matched_requirement_ids
        ):
            raise ValueError("补充目录必须承接至少一个真实责任 ID")
        return self


class RewriteOutlineStructureMatchCandidate(StrictPlanningModel):
    alignments: list[RewriteOutlineStructureAlignment] = Field(min_length=1)
    supplemental_nodes: list[RewriteSupplementalNode] = Field(default_factory=list)
    review_status: Literal["draft", "needs_review", "blocked"] = "draft"

    @model_validator(mode="after")
    def section_ids_are_unique(self) -> "RewriteOutlineStructureMatchCandidate":
        ids = [item.legacy_section_id for item in self.alignments]
        if len(ids) != len(set(ids)):
            raise ValueError("结构匹配中每个旧章节只能出现一次")
        return self


class RewriteContentBlock(StrictPlanningModel):
    section_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    content: str = Field(min_length=1)
    excerpt_index: int = Field(default=0, ge=0)


class RewriteChapterContentAssessmentInput(StrictPlanningModel):
    target_node_id: str = Field(min_length=1)
    path: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    writing_objectives: list[str] = Field(default_factory=list)
    response_unit_ids: list[str] = Field(default_factory=list)
    condition_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    requirements: list[ResponsibilityText] = Field(default_factory=list)
    score_conditions: list[ConditionText] = Field(default_factory=list)
    allowed_legacy_section_ids: list[str] = Field(min_length=1)
    blocks: list[RewriteContentBlock] = Field(min_length=1)
    review_feedback: str = ""


class RewriteChapterContentAssessmentCandidate(StrictPlanningModel):
    target_node_id: str = Field(min_length=1)
    rewrite_mode: RewriteMode
    legacy_sources: list["RewriteLegacySource"] = Field(default_factory=list)
    covered_requirement_ids: list[str] = Field(default_factory=list)
    covered_condition_ids: list[str] = Field(default_factory=list)
    missing_requirement_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False

    @model_validator(mode="after")
    def strategy_is_consistent(self) -> "RewriteChapterContentAssessmentCandidate":
        if self.rewrite_mode == "copy":
            if not self.legacy_sources or self.required_changes or self.missing_requirement_ids or self.conflicts:
                raise ValueError("copy 必须有来源且不得存在修改项、缺失要求或冲突")
        elif self.rewrite_mode in {"light_edit", "restructure"}:
            if not self.legacy_sources or not self.required_changes:
                raise ValueError(f"{self.rewrite_mode} 必须有来源和 required_changes")
        elif self.legacy_sources:
            raise ValueError("new_write 不得引用旧正文来源")
        return self


class RewriteLegacySource(StrictPlanningModel):
    section_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class RewriteOutlineAlignment(StrictPlanningModel):
    legacy_section_id: str = Field(min_length=1)
    target_node_id: str | None = None
    placement: Literal["same_scope", "child_detail", "ignore"]
    matched_response_unit_ids: list[str] = Field(default_factory=list)
    matched_condition_ids: list[str] = Field(default_factory=list)
    matched_requirement_ids: list[str] = Field(default_factory=list)
    purpose: str = ""
    writing_objectives: list[str] = Field(default_factory=list)
    rewrite_mode: RewriteMode | None = None
    legacy_sources: list[RewriteLegacySource] = Field(default_factory=list)
    reason: str = ""
    required_changes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False

    @model_validator(mode="after")
    def strategy_is_self_consistent(self) -> "RewriteOutlineAlignment":
        if self.placement == "ignore":
            if self.target_node_id is not None or self.rewrite_mode is not None or self.legacy_sources:
                raise ValueError("ignore 不得声明目标、rewrite_mode 或旧正文来源")
            return self
        if not self.target_node_id or self.rewrite_mode is None:
            raise ValueError("非 ignore 对齐必须声明目标和 rewrite_mode")
        if not (
            self.matched_response_unit_ids
            or self.matched_condition_ids
            or self.matched_requirement_ids
        ):
            raise ValueError("非 ignore 对齐必须引用至少一个真实责任 ID")
        if self.rewrite_mode == "copy":
            if not self.legacy_sources or self.required_changes:
                raise ValueError("copy 必须有旧正文来源且 required_changes 为空")
        elif self.rewrite_mode in {"light_edit", "restructure"}:
            if not self.legacy_sources or not self.required_changes:
                raise ValueError(f"{self.rewrite_mode} 必须有旧正文来源和 required_changes")
        elif self.legacy_sources:
            raise ValueError("new_write 不得引用旧正文来源")
        return self


class RewriteOutlineMergeCandidate(StrictPlanningModel):
    alignments: list[RewriteOutlineAlignment] = Field(min_length=1)
    supplemental_nodes: list[RewriteSupplementalNode] = Field(default_factory=list)
    review_status: Literal["draft", "needs_review", "blocked"] = "draft"

    @model_validator(mode="after")
    def section_ids_are_unique(self) -> "RewriteOutlineMergeCandidate":
        ids = [item.legacy_section_id for item in self.alignments]
        if len(ids) != len(set(ids)):
            raise ValueError("每个旧章节只能出现一次")
        return self


def _paths(items: list[Any], id_attr: str, parent_attr: str, title_attr: str) -> dict[str, list[str]]:
    by_id = {str(getattr(item, id_attr)): item for item in items}
    result: dict[str, list[str]] = {}
    for item_id, item in by_id.items():
        chain: list[str] = []
        cursor = item
        seen: set[str] = set()
        while cursor is not None:
            cursor_id = str(getattr(cursor, id_attr))
            if cursor_id in seen:
                raise ValueError("目录父子关系存在循环")
            seen.add(cursor_id)
            chain.append(str(getattr(cursor, title_attr)))
            parent_id = getattr(cursor, parent_attr)
            cursor = by_id.get(str(parent_id)) if parent_id else None
        result[item_id] = list(reversed(chain))
    return result


def _initial_cards(
    outline: ChapterOutlineCandidate,
    ledger: RequirementLedger,
    scores: ScoreModel,
) -> list[InitialOutlineCard]:
    nodes = list(outline.nodes)
    by_id = {node.local_id: node for node in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_local_id:
            children[node.parent_local_id].append(node.local_id)
    paths = _paths(nodes, "local_id", "parent_local_id", "title")
    requirements = {item.requirement_id: item for item in ledger.requirements}
    conditions = {
        condition.condition_id: condition
        for point in scores.points
        for condition in point.score_conditions
    }

    def subtree(node_id: str) -> list[ChapterOutlineNodeCandidate]:
        found: list[ChapterOutlineNodeCandidate] = []
        pending = [node_id]
        while pending:
            current = pending.pop(0)
            found.append(by_id[current])
            pending[0:0] = children.get(current, [])
        return found

    cards: list[InitialOutlineCard] = []
    for node in sorted(nodes, key=lambda item: item.order):
        branch = subtree(node.local_id)
        response_ids = list(dict.fromkeys(
            value for item in branch
            for value in (*item.primary_response_unit_ids, *item.supporting_response_unit_ids)
        ))
        condition_ids = list(dict.fromkeys(
            value for item in branch for value in item.score_condition_ids
        ))
        requirement_ids = list(dict.fromkeys(
            value for item in branch for value in item.requirement_ids
        ))
        cards.append(InitialOutlineCard(
            node_id=node.local_id,
            parent_node_id=node.parent_local_id,
            path=paths[node.local_id],
            depth=len(paths[node.local_id]),
            order=node.order,
            title=node.title,
            child_titles=[by_id[value].title for value in children.get(node.local_id, [])],
            is_leaf=not bool(children.get(node.local_id)),
            purpose=node.purpose,
            writing_objectives=node.writing_objectives,
            direct_response_unit_ids=list(dict.fromkeys([
                *node.primary_response_unit_ids, *node.supporting_response_unit_ids
            ])),
            direct_condition_ids=node.score_condition_ids,
            direct_requirement_ids=node.requirement_ids,
            subtree_response_unit_ids=response_ids,
            subtree_condition_ids=condition_ids,
            subtree_requirement_ids=requirement_ids,
            requirements=[
                ResponsibilityText(
                    requirement_id=value,
                    text=requirements[value].normalized_requirement,
                )
                for value in requirement_ids if value in requirements
            ],
            score_conditions=[
                ConditionText(condition_id=value, text=conditions[value].text)
                for value in condition_ids if value in conditions
            ],
        ))
    return cards


def _legacy_cards(index: LegacyBidIndex, targets: list[InitialOutlineCard]) -> list[LegacySectionCard]:
    sections = list(index.sections)
    paths = _paths(sections, "section_id", "parent_section_id", "title")
    blocks = {block.block_id: block for block in index.blocks}
    children: dict[str, list[Any]] = defaultdict(list)
    for section in sections:
        if section.parent_section_id:
            children[section.parent_section_id].append(section)
    leaf_target_ids = [item.node_id for item in targets if item.is_leaf]
    cards: list[LegacySectionCard] = []
    for section in sorted(sections, key=lambda item: item.order):
        direct_blocks = [
            blocks[block_id]
            for block_id in section.content_block_ids
            if block_id in blocks
        ]
        direct_content = "\n".join(block.content for block in direct_blocks)
        cards.append(LegacySectionCard(
            section_id=section.section_id,
            parent_section_id=section.parent_section_id,
            path=paths[section.section_id],
            depth=len(paths[section.section_id]),
            order=section.order,
            title=section.title,
            child_titles=[item.title for item in sorted(
                children.get(section.section_id, []), key=lambda item: item.order
            )],
            direct_content=direct_content,
            blocks=[LegacyBlockCard(
                block_id=block.block_id,
                content_hash=block.content_hash,
                content=block.content,
            ) for block in direct_blocks],
            # Compatibility-only field for injected deterministic providers.
            # The real structure LLM receives LegacyOutlineCard and never sees it.
            candidate_target_ids=leaf_target_ids,
        ))
    return cards


def build_rewrite_outline_merge_input(
    initial_outline: ChapterOutlineCandidate,
    ledger: RequirementLedger,
    scores: ScoreModel,
    project_model: Any,
    legacy_index: LegacyBidIndex,
    template_structure: Any | None = None,
    review_feedback: str = "",
) -> RewriteOutlineMergeInput:
    targets = _initial_cards(initial_outline, ledger, scores)
    return RewriteOutlineMergeInput(
        requirement_ledger=ledger.model_dump(mode="json"),
        score_model=scores.model_dump(mode="json"),
        project_model=project_model.model_dump(mode="json"),
        template_structure=(template_structure.model_dump(mode="json") if template_structure else None),
        document_mode="template_strict" if template_structure else "auto_outline",
        initial_outline=targets,
        legacy_sections=_legacy_cards(legacy_index, targets),
        review_feedback=str(review_feedback or "").strip(),
    )


def _build_structure_match_input(
    request: RewriteOutlineMergeInput,
) -> RewriteOutlineStructureMatchInput:
    return RewriteOutlineStructureMatchInput(
        document_mode=request.document_mode,
        initial_outline=request.initial_outline,
        legacy_outline=[
            LegacyOutlineCard(
                section_id=item.section_id,
                parent_section_id=item.parent_section_id,
                path=item.path,
                depth=item.depth,
                order=item.order,
                title=item.title,
                child_titles=item.child_titles,
            )
            for item in request.legacy_sections
        ],
        review_feedback=request.review_feedback,
    )


def validate_rewrite_outline_structure_match(
    request: RewriteOutlineStructureMatchInput,
    candidate: RewriteOutlineStructureMatchCandidate,
) -> None:
    expected = {item.section_id for item in request.legacy_outline}
    actual = {item.legacy_section_id for item in candidate.alignments}
    if actual != expected or len(candidate.alignments) != len(expected):
        raise ValueError(
            f"旧章节必须且只能出现一次；missing={sorted(expected-actual)}, "
            f"extra={sorted(actual-expected)}"
        )
    targets = {item.node_id: item for item in request.initial_outline}
    original_leaf_ids = set(targets) - {
        str(item.parent_node_id)
        for item in request.initial_outline
        if item.parent_node_id
    }
    sections = {item.section_id: item for item in request.legacy_outline}
    by_section = {item.legacy_section_id: item for item in candidate.alignments}
    same_scope_targets: set[str] = set()
    for alignment in candidate.alignments:
        if alignment.placement == "ignore":
            continue
        target = targets.get(str(alignment.target_node_id))
        if target is None:
            raise ValueError(f"{alignment.legacy_section_id} 引用了未知新目录节点")
        if target.node_id not in original_leaf_ids:
            raise ValueError("旧目录只能匹配或扩展新目录原始叶子节点")
        checks = (
            (alignment.matched_response_unit_ids, target.subtree_response_unit_ids, "response unit"),
            (alignment.matched_condition_ids, target.subtree_condition_ids, "condition"),
            (alignment.matched_requirement_ids, target.subtree_requirement_ids, "requirement"),
        )
        for values, allowed, label in checks:
            if unknown := set(values) - set(allowed):
                raise ValueError(
                    f"{alignment.legacy_section_id} 引用了目标职责外的 {label}: {sorted(unknown)}"
                )
        if alignment.placement == "same_scope":
            target_id = str(alignment.target_node_id)
            if target_id in same_scope_targets:
                raise ValueError("同一个新叶子最多只能有一个 same_scope 锚点")
            same_scope_targets.add(target_id)
            continue
        if request.document_mode == "template_strict":
            raise ValueError("template_strict 不允许迁入旧子目录")
        parent_id = sections[alignment.legacy_section_id].parent_section_id
        parent = by_section.get(str(parent_id or ""))
        if (
            parent is None
            or parent.placement == "ignore"
            or parent.target_node_id != alignment.target_node_id
        ):
            raise ValueError("child_detail 必须位于同一新叶子锚点的已匹配旧父章节下")

    covered_by_target: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"response": set(), "condition": set(), "requirement": set()}
    )
    expanded_targets: set[str] = set()
    for alignment in candidate.alignments:
        if alignment.placement != "child_detail" or not alignment.target_node_id:
            continue
        target_id = str(alignment.target_node_id)
        expanded_targets.add(target_id)
        covered_by_target[target_id]["response"].update(alignment.matched_response_unit_ids)
        covered_by_target[target_id]["condition"].update(alignment.matched_condition_ids)
        covered_by_target[target_id]["requirement"].update(alignment.matched_requirement_ids)
    supplemental_titles: set[tuple[str, str]] = set()
    for item in candidate.supplemental_nodes:
        target = targets.get(item.target_node_id)
        if target is None or target.node_id not in original_leaf_ids:
            raise ValueError("补充目录只能挂到新目录原始叶子节点")
        if request.document_mode == "template_strict":
            raise ValueError("template_strict 不允许新增补充目录")
        if item.target_node_id not in same_scope_targets:
            raise ValueError("补充目录必须挂到已有 same_scope 锚点下")
        title_key = (item.target_node_id, _NUMBERING_RE.sub("", item.title).strip().lower())
        if title_key in supplemental_titles:
            raise ValueError("同一新叶子下不允许生成同名补充目录")
        supplemental_titles.add(title_key)
        checks = (
            (item.matched_response_unit_ids, target.subtree_response_unit_ids, "response unit"),
            (item.matched_condition_ids, target.subtree_condition_ids, "condition"),
            (item.matched_requirement_ids, target.subtree_requirement_ids, "requirement"),
        )
        for values, allowed, label in checks:
            if unknown := set(values) - set(allowed):
                raise ValueError(f"补充目录引用了目标职责外的 {label}: {sorted(unknown)}")
        coverage = covered_by_target[item.target_node_id]
        new_ids = (
            set(item.matched_response_unit_ids) - coverage["response"]
            or set(item.matched_condition_ids) - coverage["condition"]
            or set(item.matched_requirement_ids) - coverage["requirement"]
        )
        if not new_ids:
            raise ValueError("补充目录必须承接尚未由迁入子目录覆盖的责任")
        coverage["response"].update(item.matched_response_unit_ids)
        coverage["condition"].update(item.matched_condition_ids)
        coverage["requirement"].update(item.matched_requirement_ids)
        expanded_targets.add(item.target_node_id)
    for target_id in sorted(expanded_targets):
        target = targets[target_id]
        coverage = covered_by_target[target_id]
        missing = {
            "response_unit_ids": set(target.subtree_response_unit_ids) - coverage["response"],
            "condition_ids": set(target.subtree_condition_ids) - coverage["condition"],
            "requirement_ids": set(target.subtree_requirement_ids) - coverage["requirement"],
        }
        missing = {key: sorted(value) for key, value in missing.items() if value}
        if missing:
            raise ValueError(f"扩展后的新叶子仍有未承接责任: {target_id}: {missing}")
    if candidate.review_status == "blocked":
        raise ValueError("structure match candidate 标记为 blocked")


def validate_rewrite_outline_merge(
    request: RewriteOutlineMergeInput,
    candidate: RewriteOutlineMergeCandidate,
) -> None:
    expected = {item.section_id for item in request.legacy_sections}
    actual = {item.legacy_section_id for item in candidate.alignments}
    if actual != expected or len(candidate.alignments) != len(expected):
        raise ValueError(f"旧章节必须且只能出现一次；missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    targets = {item.node_id: item for item in request.initial_outline}
    original_leaf_ids = set(targets) - {
        str(item.parent_node_id)
        for item in request.initial_outline
        if item.parent_node_id
    }
    sections = {item.section_id: item for item in request.legacy_sections}
    alignment_by_section = {item.legacy_section_id: item for item in candidate.alignments}
    same_scope_targets = [
        str(item.target_node_id)
        for item in candidate.alignments
        if item.placement == "same_scope"
    ]
    if len(same_scope_targets) != len(set(same_scope_targets)):
        raise ValueError("同一个 target_node_id 最多只能有一个 same_scope 对齐")

    for alignment in candidate.alignments:
        section = sections[alignment.legacy_section_id]
        if alignment.placement == "ignore":
            continue
        if request.document_mode == "template_strict" and alignment.placement == "child_detail":
            raise ValueError("template_strict 不允许 child_detail")
        target = targets.get(str(alignment.target_node_id))
        if target is None or target.node_id not in original_leaf_ids:
            raise ValueError(f"{section.section_id} 只能映射到新目录原始叶子节点")
        checks = (
            (alignment.matched_response_unit_ids, target.subtree_response_unit_ids, "response unit"),
            (alignment.matched_condition_ids, target.subtree_condition_ids, "condition"),
            (alignment.matched_requirement_ids, target.subtree_requirement_ids, "requirement"),
        )
        for values, allowed, label in checks:
            if unknown := set(values) - set(allowed):
                raise ValueError(f"{section.section_id} 引用了目标分支外的 {label}: {sorted(unknown)}")
        allowed_section_ids = {section.section_id}
        cursor = section
        while cursor.parent_section_id:
            parent = sections.get(cursor.parent_section_id)
            if parent is None:
                break
            parent_alignment = alignment_by_section.get(parent.section_id)
            if (
                parent_alignment is None
                or parent_alignment.placement == "ignore"
                or parent_alignment.target_node_id != alignment.target_node_id
            ):
                break
            allowed_section_ids.add(parent.section_id)
            cursor = parent
        allowed_sources = {
            (owner.section_id, block.block_id): block.content_hash
            for owner in request.legacy_sections
            if owner.section_id in allowed_section_ids
            for block in owner.blocks
        }
        source_keys = [
            (source.section_id, source.block_id, source.content_hash)
            for source in alignment.legacy_sources
        ]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError(f"{section.section_id} 不允许重复 legacy_sources")
        for source in alignment.legacy_sources:
            if allowed_sources.get((source.section_id, source.block_id)) != source.content_hash:
                raise ValueError(f"{section.section_id} 引用了未知或过期的旧正文来源")
        if section.parent_section_id:
            parent = alignment_by_section.get(section.parent_section_id)
            if parent and parent.placement != "ignore" and alignment.target_node_id:
                if alignment.target_node_id != parent.target_node_id:
                    raise ValueError(f"旧章节 {section.section_id} 映射到旧父章节目标之外的分支")
    structure_candidate = RewriteOutlineStructureMatchCandidate(
        alignments=[RewriteOutlineStructureAlignment(
            legacy_section_id=item.legacy_section_id,
            target_node_id=item.target_node_id,
            placement=item.placement,
            matched_response_unit_ids=item.matched_response_unit_ids,
            matched_condition_ids=item.matched_condition_ids,
            matched_requirement_ids=item.matched_requirement_ids,
            purpose=item.purpose,
            writing_objectives=item.writing_objectives,
            reason=item.reason,
            confidence=item.confidence,
            needs_human=item.needs_human,
        ) for item in candidate.alignments],
        supplemental_nodes=candidate.supplemental_nodes,
        review_status=candidate.review_status,
    )
    validate_rewrite_outline_structure_match(
        _build_structure_match_input(request), structure_candidate
    )
    if candidate.review_status == "blocked":
        raise ValueError("merge candidate 标记为 blocked")


_NUMBERING_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百零〇0-9]+[章节篇部]|[0-9一二三四五六七八九十]+(?:[.、．]|\s+))+\s*")


def apply_rewrite_outline_merge(
    initial_outline: ChapterOutlineCandidate,
    merge_candidate: RewriteOutlineMergeCandidate,
    legacy_index: LegacyBidIndex,
) -> ChapterOutlineCandidate:
    nodes = [node.model_copy(deep=True) for node in sorted(initial_outline.nodes, key=lambda item: item.order)]
    by_id = {node.local_id: node for node in nodes}
    sections = {item.section_id: item for item in legacy_index.sections}
    created_by_section: dict[str, str] = {}
    alignments = sorted(
        merge_candidate.alignments,
        key=lambda item: sections[item.legacy_section_id].order,
    )

    def merge_metadata(node: ChapterOutlineNodeCandidate, alignment: RewriteOutlineAlignment) -> ChapterOutlineNodeCandidate:
        sources = [item.model_dump(mode="json") for item in alignment.legacy_sources]
        return node.model_copy(update={
            "rewrite_mode": alignment.rewrite_mode,
            "legacy_section_ids": [alignment.legacy_section_id],
            "legacy_sources": sources,
            "rewrite_basis": {
                "response_unit_ids": alignment.matched_response_unit_ids,
                "condition_ids": alignment.matched_condition_ids,
                "requirement_ids": alignment.matched_requirement_ids,
            },
            "rewrite_reason": alignment.reason,
            "required_changes": alignment.required_changes,
        })

    for alignment in alignments:
        if alignment.placement == "ignore":
            continue
        if alignment.placement == "same_scope":
            target_id = str(alignment.target_node_id)
            replacement = merge_metadata(by_id[target_id], alignment)
            nodes[nodes.index(by_id[target_id])] = replacement
            by_id[target_id] = replacement
            created_by_section[alignment.legacy_section_id] = target_id
            continue
        section = sections[alignment.legacy_section_id]
        parent_id = created_by_section.get(str(section.parent_section_id)) or str(alignment.target_node_id)
        local_id = "legacy-" + hashlib.sha256(
            f"{alignment.target_node_id}|{alignment.legacy_section_id}".encode("utf-8")
        ).hexdigest()[:20]
        title = _NUMBERING_RE.sub("", section.title).strip() or section.title
        node = ChapterOutlineNodeCandidate(
            local_id=local_id,
            parent_local_id=parent_id,
            order=len(nodes),
            title=title,
            purpose=alignment.purpose or f"细化{title}",
            writing_objectives=alignment.writing_objectives,
            supporting_response_unit_ids=alignment.matched_response_unit_ids,
            requirement_ids=alignment.matched_requirement_ids,
            confidence=alignment.confidence,
            needs_human=alignment.needs_human,
            structure_origin="legacy_enriched",
            rewrite_mode=alignment.rewrite_mode,
            legacy_section_ids=[alignment.legacy_section_id],
            legacy_sources=[item.model_dump(mode="json") for item in alignment.legacy_sources],
            rewrite_basis={
                "response_unit_ids": alignment.matched_response_unit_ids,
                "condition_ids": alignment.matched_condition_ids,
                "requirement_ids": alignment.matched_requirement_ids,
            },
            rewrite_reason=alignment.reason,
            required_changes=alignment.required_changes,
        )
        nodes.append(node)
        by_id[local_id] = node
        created_by_section[alignment.legacy_section_id] = local_id

    for supplemental in merge_candidate.supplemental_nodes:
        local_id = "supplement-" + hashlib.sha256(
            _canonical_json({
                "target_node_id": supplemental.target_node_id,
                "title": supplemental.title,
                "response_unit_ids": supplemental.matched_response_unit_ids,
                "condition_ids": supplemental.matched_condition_ids,
                "requirement_ids": supplemental.matched_requirement_ids,
            }).encode("utf-8")
        ).hexdigest()[:20]
        if local_id in by_id:
            continue
        node = ChapterOutlineNodeCandidate(
            local_id=local_id,
            parent_local_id=supplemental.target_node_id,
            order=len(nodes),
            title=supplemental.title,
            purpose=supplemental.purpose,
            writing_objectives=supplemental.writing_objectives,
            supporting_response_unit_ids=supplemental.matched_response_unit_ids,
            score_condition_ids=supplemental.matched_condition_ids,
            requirement_ids=supplemental.matched_requirement_ids,
            confidence=supplemental.confidence,
            needs_human=supplemental.needs_human,
            structure_origin="tender_supplement",
            rewrite_mode="new_write",
            rewrite_basis={
                "response_unit_ids": supplemental.matched_response_unit_ids,
                "condition_ids": supplemental.matched_condition_ids,
                "requirement_ids": supplemental.matched_requirement_ids,
            },
            rewrite_reason=supplemental.reason,
        )
        nodes.append(node)
        by_id[local_id] = node

    children_by_parent: dict[str, list[ChapterOutlineNodeCandidate]] = defaultdict(list)
    roots: list[ChapterOutlineNodeCandidate] = []
    for node in nodes:
        if node.parent_local_id:
            children_by_parent[node.parent_local_id].append(node)
        else:
            roots.append(node)
    ordered_nodes: list[ChapterOutlineNodeCandidate] = []

    def append_branch(node: ChapterOutlineNodeCandidate) -> None:
        ordered_nodes.append(node)
        for child in sorted(
            children_by_parent.get(node.local_id, []), key=lambda item: item.order
        ):
            append_branch(child)

    for root in sorted(roots, key=lambda item: item.order):
        append_branch(root)
    if len(ordered_nodes) != len(nodes):
        raise ValueError("融合目录存在孤立节点或父子循环")

    parents = {node.parent_local_id for node in ordered_nodes if node.parent_local_id}
    finalized = []
    for node in ordered_nodes:
        finalized.append(node.model_copy(update={
            "order": len(finalized),
            "rewrite_mode": None if node.local_id in parents else (node.rewrite_mode or "new_write"),
        }))
    return ChapterOutlineCandidate(
        nodes=finalized,
        document_quality_response_unit_ids=initial_outline.document_quality_response_unit_ids,
        review_status=merge_candidate.review_status,
    )


class _LLMRewriteOutlineStructureProvider(
    _StructuredLLMProvider[RewriteOutlineStructureMatchCandidate]
):
    capability_id = f"{REWRITE_OUTLINE_SKILL_ID}.structure_match"
    prompt_file = REWRITE_OUTLINE_STRUCTURE_PROMPT_FILE
    prompt_version = "v3_rewrite_outline_structure_match_v1"
    schema_version = "v3.rewrite_outline_structure_match.v1"
    candidate_model = RewriteOutlineStructureMatchCandidate

    def _validate_candidate(self, candidate: RewriteOutlineStructureMatchCandidate, request: Any) -> None:
        if not isinstance(request, RewriteOutlineStructureMatchInput):
            raise ValueError("目录匹配输入类型错误")
        validate_rewrite_outline_structure_match(request, candidate)


class _LLMRewriteChapterContentProvider(
    _StructuredLLMProvider[RewriteChapterContentAssessmentCandidate]
):
    capability_id = f"{REWRITE_OUTLINE_SKILL_ID}.content_assessment"
    prompt_file = REWRITE_CHAPTER_CONTENT_PROMPT_FILE
    prompt_version = "v3_rewrite_chapter_content_assessment_v1"
    schema_version = "v3.rewrite_chapter_content_assessment.v1"
    candidate_model = RewriteChapterContentAssessmentCandidate

    def _validate_candidate(self, candidate: RewriteChapterContentAssessmentCandidate, request: Any) -> None:
        if not isinstance(request, RewriteChapterContentAssessmentInput):
            raise ValueError("正文评估输入类型错误")
        if candidate.target_node_id != request.target_node_id:
            raise ValueError("正文评估返回了其他目标章节")
        allowed_sections = set(request.allowed_legacy_section_ids)
        allowed_blocks = {
            (item.section_id, item.block_id): item.content_hash for item in request.blocks
        }
        for source in candidate.legacy_sources:
            if source.section_id not in allowed_sections:
                raise ValueError("正文评估引用了结构匹配范围外的旧章节")
            if allowed_blocks.get((source.section_id, source.block_id)) != source.content_hash:
                raise ValueError("正文评估引用了未知或过期的正文块")
        if unknown := set(candidate.covered_requirement_ids) - set(request.requirement_ids):
            raise ValueError(f"正文评估引用未知 requirement: {sorted(unknown)}")
        if unknown := set(candidate.missing_requirement_ids) - set(request.requirement_ids):
            raise ValueError(f"正文评估引用未知 missing requirement: {sorted(unknown)}")
        if unknown := set(candidate.covered_condition_ids) - set(request.condition_ids):
            raise ValueError(f"正文评估引用未知 condition: {sorted(unknown)}")


class LLMRewriteOutlineMergeProvider(
    _StructuredLLMProvider[RewriteOutlineMergeCandidate]
):
    skill_id = REWRITE_OUTLINE_SKILL_ID
    capability_id = REWRITE_OUTLINE_SKILL_ID
    capability_version = REWRITE_OUTLINE_CAPABILITY_VERSION
    prompt_file = REWRITE_OUTLINE_PROMPT_FILE
    prompt_version = REWRITE_OUTLINE_PROMPT_VERSION
    schema_version = REWRITE_OUTLINE_SCHEMA_VERSION
    candidate_model = RewriteOutlineMergeCandidate
    max_repair_attempts = MAX_REPAIR_ATTEMPTS

    def __init__(
        self,
        *,
        chat_callable: Any | None = None,
        model_fingerprint: str | None = None,
        provider_fingerprint: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        super().__init__(
            chat_callable=chat_callable,
            model_fingerprint=model_fingerprint,
            provider_fingerprint=provider_fingerprint,
            temperature=temperature,
        )
        self.prompt_hash = rewrite_outline_prompt_hash()
        shared = {
            "chat_callable": self._chat,
            "model_fingerprint": self.model_fingerprint,
            "provider_fingerprint": self.provider_fingerprint,
            "temperature": self.temperature,
        }
        self._structure_provider = _LLMRewriteOutlineStructureProvider(**shared)
        self._content_provider = _LLMRewriteChapterContentProvider(**shared)
        self._internal_batch_cache: dict[str, StructuredInferenceResult[Any]] = {}
        self._internal_cache_hits = 0

    def _invoke_internal(
        self,
        provider: _StructuredLLMProvider[Any],
        request: StrictPlanningModel,
        *,
        logical_batch_id: str,
    ) -> StructuredInferenceResult[Any]:
        cache_key = hashlib.sha256(
            _canonical_json({
                "capability_version": self.capability_version,
                "prompt_hash": provider.prompt_hash,
                "schema_version": provider.schema_version,
                "logical_batch_id": logical_batch_id,
                "request": request.model_dump(mode="json"),
            }).encode("utf-8")
        ).hexdigest()
        cached = self._internal_batch_cache.get(cache_key)
        if cached is not None:
            self._internal_cache_hits += 1
            return cached
        result = provider._invoke(
            request,
            logical_batch_id=logical_batch_id,
            repair_attempts=1,
        )
        self._internal_batch_cache[cache_key] = result
        return result

    def merge(
        self,
        request: RewriteOutlineMergeInput,
    ) -> StructuredInferenceResult[RewriteOutlineMergeCandidate]:
        structure_request = _build_structure_match_input(request)
        structure_chars = len(_canonical_json(structure_request))
        if structure_chars > REWRITE_STRUCTURE_MAX_INPUT_CHARS:
            raise PlanningInferenceError(
                "完整新旧目录结构超过单次匹配预算；请精简目录摘要后重试，"
                "不会回退为携带正文的分批匹配。"
            )
        structure_result = self._invoke_internal(
            self._structure_provider,
            structure_request,
            logical_batch_id="rewrite-outline-structure-match",
        )
        content_results: list[StructuredInferenceResult[RewriteChapterContentAssessmentCandidate]] = []
        final_alignments: list[RewriteOutlineAlignment] = []
        structure_by_id = {
            item.legacy_section_id: item
            for item in structure_result.candidate.alignments
        }
        children: dict[str, list[str]] = defaultdict(list)
        for section in request.legacy_sections:
            if section.parent_section_id:
                children[section.parent_section_id].append(section.section_id)

        for section in request.legacy_sections:
            structural = structure_by_id[section.section_id]
            if structural.placement == "ignore":
                final_alignments.append(RewriteOutlineAlignment(
                    legacy_section_id=section.section_id,
                    placement="ignore",
                    reason=structural.reason,
                    confidence=structural.confidence,
                    needs_human=structural.needs_human,
                ))
                continue
            has_migrated_child = any(
                structure_by_id[child_id].placement != "ignore"
                for child_id in children.get(section.section_id, [])
            )
            assessments: list[RewriteChapterContentAssessmentCandidate] = []
            if not has_migrated_child:
                for batch_index, content_request in enumerate(
                    self._content_requests(request, structural, structure_by_id), start=1
                ):
                    result = self._invoke_internal(
                        self._content_provider,
                        content_request,
                        logical_batch_id=(
                            f"rewrite-content-{section.order:04d}-{batch_index:03d}"
                        ),
                    )
                    content_results.append(result)
                    assessments.append(result.candidate)
            assessment = self._merge_content_assessments(
                structural, assessments
            )
            final_alignments.append(RewriteOutlineAlignment(
                legacy_section_id=section.section_id,
                target_node_id=structural.target_node_id,
                placement=structural.placement,
                matched_response_unit_ids=structural.matched_response_unit_ids,
                matched_condition_ids=structural.matched_condition_ids,
                matched_requirement_ids=structural.matched_requirement_ids,
                purpose=structural.purpose,
                writing_objectives=structural.writing_objectives,
                rewrite_mode=assessment.rewrite_mode,
                legacy_sources=assessment.legacy_sources,
                reason=assessment.reason or structural.reason,
                required_changes=assessment.required_changes,
                confidence=min(structural.confidence, assessment.confidence),
                needs_human=structural.needs_human or assessment.needs_human,
            ))
        candidate = RewriteOutlineMergeCandidate(
            alignments=final_alignments,
            supplemental_nodes=structure_result.candidate.supplemental_nodes,
            review_status=(
                "needs_review"
                if structure_result.candidate.review_status == "needs_review"
                or any(item.needs_human for item in final_alignments)
                else "draft"
            ),
        )
        self._validate_candidate(candidate, request)
        self.last_batch_summary = {
            "rewrite_structure_call_count": 1,
            "rewrite_structure_input_chars": structure_chars,
            "rewrite_content_batch_count": len(content_results),
            "rewrite_internal_cache_hits": self._internal_cache_hits,
            "rewrite_merge_section_count": len(request.legacy_sections),
            "rewrite_content_batch_input_chars": [
                len(result.input_snapshot) for result in content_results
            ],
        }
        return StructuredInferenceResult(
            candidate=candidate,
            raw_output=_canonical_json({
                "mode": "structure_then_content",
                "structure": structure_result.raw_output,
                "content_batches": [item.raw_output for item in content_results],
            }),
            normalized_output=_canonical_json(candidate),
            reasoning="\n".join(
                item.reasoning
                for item in [structure_result, *content_results]
                if item.reasoning.strip()
            ),
            input_snapshot=_canonical_json(structure_request),
            attempt_count=structure_result.attempt_count + sum(
                item.attempt_count for item in content_results
            ),
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
            temperature=self.temperature,
            normalized_reference_count=sum(
                item.normalized_reference_count
                for item in [structure_result, *content_results]
            ),
            validation_errors=tuple(
                error
                for item in [structure_result, *content_results]
                for error in item.validation_errors
            ),
        )

    @staticmethod
    def _alignment_node_id(item: RewriteOutlineStructureAlignment) -> str:
        if item.placement == "same_scope":
            return str(item.target_node_id)
        return "legacy-" + hashlib.sha256(
            f"{item.target_node_id}|{item.legacy_section_id}".encode("utf-8")
        ).hexdigest()[:20]

    def _content_requests(
        self,
        request: RewriteOutlineMergeInput,
        alignment: RewriteOutlineStructureAlignment,
        structure_by_id: dict[str, RewriteOutlineStructureAlignment],
    ) -> list[RewriteChapterContentAssessmentInput]:
        targets = {item.node_id: item for item in request.initial_outline}
        sections = {item.section_id: item for item in request.legacy_sections}
        target = targets[str(alignment.target_node_id)]
        allowed_ids: list[str] = []
        cursor = sections[alignment.legacy_section_id]
        while cursor:
            allowed_ids.append(cursor.section_id)
            if not cursor.parent_section_id:
                break
            parent = sections.get(cursor.parent_section_id)
            parent_alignment = structure_by_id.get(cursor.parent_section_id)
            if (
                parent is None
                or parent_alignment is None
                or parent_alignment.placement == "ignore"
                or parent_alignment.target_node_id != alignment.target_node_id
            ):
                break
            cursor = parent
        query = "\n".join([
            *target.path,
            alignment.purpose or target.purpose,
            *(alignment.writing_objectives or target.writing_objectives),
            *(item.text for item in target.requirements),
            *(item.text for item in target.score_conditions),
        ])
        payloads = [
            {
                "block_id": block.block_id,
                "section_id": section.section_id,
                "content_hash": block.content_hash,
                "ordinal": section.order,
                "heading_path": section.path,
                "content": block.content,
            }
            for section in request.legacy_sections
            if section.section_id in allowed_ids
            for block in section.blocks
        ]
        ranked = LegacyBidSemanticReranker().rerank(
            query, payloads, limit=REWRITE_CONTENT_MAX_BLOCKS
        )
        excerpts: list[RewriteContentBlock] = []
        for item in ranked:
            content = str(item.get("content") or "")
            for index, start in enumerate(range(0, len(content), REWRITE_BLOCK_MAX_CHARS)):
                excerpts.append(RewriteContentBlock(
                    section_id=str(item["section_id"]),
                    block_id=str(item["block_id"]),
                    content_hash=str(item["content_hash"]),
                    content=content[start:start + REWRITE_BLOCK_MAX_CHARS],
                    excerpt_index=index,
                ))
        if not excerpts:
            return []
        base = {
            "target_node_id": self._alignment_node_id(alignment),
            "path": [*target.path, *(
                [_NUMBERING_RE.sub("", sections[alignment.legacy_section_id].title).strip()]
                if alignment.placement == "child_detail" else []
            )],
            "title": (
                _NUMBERING_RE.sub("", sections[alignment.legacy_section_id].title).strip()
                if alignment.placement == "child_detail" else target.title
            ),
            "purpose": alignment.purpose or target.purpose,
            "writing_objectives": alignment.writing_objectives or target.writing_objectives,
            "response_unit_ids": alignment.matched_response_unit_ids,
            "condition_ids": alignment.matched_condition_ids,
            "requirement_ids": alignment.matched_requirement_ids,
            "requirements": [
                item for item in target.requirements
                if item.requirement_id in alignment.matched_requirement_ids
            ],
            "score_conditions": [
                item for item in target.score_conditions
                if item.condition_id in alignment.matched_condition_ids
            ],
            "allowed_legacy_section_ids": allowed_ids,
            "review_feedback": request.review_feedback,
        }
        batches: list[RewriteChapterContentAssessmentInput] = []
        current: list[RewriteContentBlock] = []
        for excerpt in excerpts:
            candidate = RewriteChapterContentAssessmentInput(**base, blocks=[*current, excerpt])
            if current and (
                len(current) >= REWRITE_CONTENT_MAX_BLOCKS
                or len(_canonical_json(candidate)) > REWRITE_CONTENT_MAX_INPUT_CHARS
            ):
                batches.append(RewriteChapterContentAssessmentInput(**base, blocks=current))
                current = [excerpt]
            else:
                current.append(excerpt)
        if current:
            final = RewriteChapterContentAssessmentInput(**base, blocks=current)
            if len(_canonical_json(final)) > REWRITE_CONTENT_MAX_INPUT_CHARS:
                raise PlanningInferenceError("单个正文窗口超过评估输入预算")
            batches.append(final)
        return batches

    def _merge_content_assessments(
        self,
        structural: RewriteOutlineStructureAlignment,
        items: list[RewriteChapterContentAssessmentCandidate],
    ) -> RewriteChapterContentAssessmentCandidate:
        target_id = self._alignment_node_id(structural)
        if not items:
            return RewriteChapterContentAssessmentCandidate(
                target_node_id=target_id,
                rewrite_mode="new_write",
                missing_requirement_ids=structural.matched_requirement_ids,
                required_changes=["未召回到可可靠复用的旧正文，按新要求重新编写"],
                reason="结构匹配成功，但没有可用旧正文",
                confidence=structural.confidence,
                needs_human=structural.needs_human,
            )
        covered_requirements = list(dict.fromkeys(
            value for item in items for value in item.covered_requirement_ids
        ))
        covered_conditions = list(dict.fromkeys(
            value for item in items for value in item.covered_condition_ids
        ))
        missing = [
            value for value in structural.matched_requirement_ids
            if value not in covered_requirements
        ]
        conflicts = list(dict.fromkeys(value for item in items for value in item.conflicts))
        changes = list(dict.fromkeys(value for item in items for value in item.required_changes))
        sources_by_key = {
            (source.section_id, source.block_id, source.content_hash): source
            for item in items if item.rewrite_mode != "new_write"
            for source in item.legacy_sources
        }
        sources = list(sources_by_key.values())
        if missing or conflicts or not sources:
            mode: RewriteMode = "new_write"
            sources = []
            if not changes:
                changes = ["旧正文未完整满足当前章节要求，按新要求重新编写"]
        elif all(item.rewrite_mode == "copy" for item in items) and not changes:
            mode = "copy"
        elif any(item.rewrite_mode == "restructure" for item in items):
            mode = "restructure"
        else:
            mode = "light_edit"
        if mode in {"light_edit", "restructure"} and not changes:
            changes = ["按当前招标要求核对并更新旧正文"]
        return RewriteChapterContentAssessmentCandidate(
            target_node_id=target_id,
            rewrite_mode=mode,
            legacy_sources=sources,
            covered_requirement_ids=covered_requirements,
            covered_condition_ids=covered_conditions,
            missing_requirement_ids=missing,
            conflicts=conflicts,
            required_changes=changes,
            reason="；".join(item.reason for item in items if item.reason),
            confidence=min(item.confidence for item in items),
            needs_human=any(item.needs_human for item in items),
        )

    def _validate_candidate(self, candidate: RewriteOutlineMergeCandidate, request: Any) -> None:
        if not isinstance(request, RewriteOutlineMergeInput):
            raise ValueError("RewriteOutlineMergeProvider 输入类型错误")
        validate_rewrite_outline_merge(request, candidate)


__all__ = [
    "RewriteOutlineMergeInput",
    "RewriteOutlineMergeCandidate",
    "RewriteOutlineAlignment",
    "RewriteLegacySource",
    "build_rewrite_outline_merge_input",
    "validate_rewrite_outline_merge",
    "apply_rewrite_outline_merge",
    "LLMRewriteOutlineMergeProvider",
]
