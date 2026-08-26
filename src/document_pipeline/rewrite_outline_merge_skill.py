from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import LegacyBidIndex, RequirementLedger, ScoreModel
from .legacy_bid_semantic import LegacyBidSemanticReranker, semantic_similarity
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
    StrictPlanningModel,
    StructuredInferenceResult,
    _StructuredLLMProvider,
)


RewriteMode = Literal["copy", "light_edit", "restructure", "new_write"]


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
    title: str = Field(min_length=1)
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
    candidate_target_ids: list[str] = Field(min_length=1, max_length=5)


class RewriteOutlineMergeInput(StrictPlanningModel):
    requirement_ledger: dict[str, Any]
    score_model: dict[str, Any]
    project_model: dict[str, Any]
    legacy_bid_index: dict[str, Any]
    template_structure: dict[str, Any] | None = None
    document_mode: Literal["auto_outline", "template_strict"] = "auto_outline"
    initial_outline: list[InitialOutlineCard] = Field(min_length=1)
    legacy_sections: list[LegacySectionCard] = Field(min_length=1)


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
            title=node.title,
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
    reranker = LegacyBidSemanticReranker()
    target_payloads = []
    target_by_id = {card.node_id: card for card in targets}
    for card in targets:
        responsibility_text = " ".join([
            *card.path,
            card.purpose,
            *card.writing_objectives,
            *(item.text for item in card.requirements),
            *(item.text for item in card.score_conditions),
        ])
        target_payloads.append({
            "block_id": card.node_id,
            "ordinal": card.depth,
            "heading_path": card.path,
            "content": responsibility_text,
        })
    cards: list[LegacySectionCard] = []
    for section in sorted(sections, key=lambda item: item.order):
        direct_blocks = [
            blocks[block_id]
            for block_id in section.content_block_ids
            if block_id in blocks
        ]
        direct_content = "\n".join(block.content for block in direct_blocks)
        query = " ".join([
            *paths[section.section_id],
            *(child.title for child in children.get(section.section_id, [])),
            direct_content,
        ])
        recalled = reranker.rerank(query, target_payloads, limit=5)
        ranked_ids = [str(item["block_id"]) for item in recalled]
        remaining = sorted(
            (card for card in targets if card.node_id not in ranked_ids),
            key=lambda card: (
                -semantic_similarity(query, " ".join([*card.path, card.purpose])),
                -card.depth,
                card.node_id,
            ),
        )
        ranked_ids.extend(card.node_id for card in remaining[: 5 - len(ranked_ids)])
        ranked_ids.sort(
            key=lambda node_id: (
                -semantic_similarity(query, " ".join([
                    *target_by_id[node_id].path,
                    target_by_id[node_id].purpose,
                    *target_by_id[node_id].writing_objectives,
                ])),
                -target_by_id[node_id].depth,
                node_id,
            )
        )
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
            candidate_target_ids=ranked_ids[:5],
        ))
    return cards


def build_rewrite_outline_merge_input(
    initial_outline: ChapterOutlineCandidate,
    ledger: RequirementLedger,
    scores: ScoreModel,
    project_model: Any,
    legacy_index: LegacyBidIndex,
    template_structure: Any | None = None,
) -> RewriteOutlineMergeInput:
    targets = _initial_cards(initial_outline, ledger, scores)
    return RewriteOutlineMergeInput(
        requirement_ledger=ledger.model_dump(mode="json"),
        score_model=scores.model_dump(mode="json"),
        project_model=project_model.model_dump(mode="json"),
        legacy_bid_index=legacy_index.model_dump(mode="json"),
        template_structure=(template_structure.model_dump(mode="json") if template_structure else None),
        document_mode="template_strict" if template_structure else "auto_outline",
        initial_outline=targets,
        legacy_sections=_legacy_cards(legacy_index, targets),
    )


def validate_rewrite_outline_merge(
    request: RewriteOutlineMergeInput,
    candidate: RewriteOutlineMergeCandidate,
) -> None:
    expected = {item.section_id for item in request.legacy_sections}
    actual = {item.legacy_section_id for item in candidate.alignments}
    if actual != expected or len(candidate.alignments) != len(expected):
        raise ValueError(f"旧章节必须且只能出现一次；missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    targets = {item.node_id: item for item in request.initial_outline}
    sections = {item.section_id: item for item in request.legacy_sections}
    parent_by_target = {item.node_id: item.parent_node_id for item in request.initial_outline}
    alignment_by_section = {item.legacy_section_id: item for item in candidate.alignments}

    def target_is_within(node_id: str, ancestor_id: str) -> bool:
        cursor: str | None = node_id
        while cursor:
            if cursor == ancestor_id:
                return True
            cursor = parent_by_target.get(cursor)
        return False

    for alignment in candidate.alignments:
        section = sections[alignment.legacy_section_id]
        if alignment.placement == "ignore":
            continue
        if request.document_mode == "template_strict" and alignment.placement == "child_detail":
            raise ValueError("template_strict 不允许 child_detail")
        if alignment.target_node_id not in section.candidate_target_ids:
            raise ValueError(f"{section.section_id} 只能从 candidate_target_ids 选择目标")
        target = targets[str(alignment.target_node_id)]
        checks = (
            (alignment.matched_response_unit_ids, target.subtree_response_unit_ids, "response unit"),
            (alignment.matched_condition_ids, target.subtree_condition_ids, "condition"),
            (alignment.matched_requirement_ids, target.subtree_requirement_ids, "requirement"),
        )
        for values, allowed, label in checks:
            if unknown := set(values) - set(allowed):
                raise ValueError(f"{section.section_id} 引用了目标分支外的 {label}: {sorted(unknown)}")
        allowed_sources = {item.block_id: item.content_hash for item in section.blocks}
        for source in alignment.legacy_sources:
            if source.section_id != section.section_id or allowed_sources.get(source.block_id) != source.content_hash:
                raise ValueError(f"{section.section_id} 引用了未知或过期的旧正文来源")
        if section.parent_section_id:
            parent = alignment_by_section.get(section.parent_section_id)
            if parent and parent.placement != "ignore" and alignment.target_node_id:
                if not target_is_within(str(alignment.target_node_id), str(parent.target_node_id)):
                    raise ValueError(f"旧章节 {section.section_id} 映射到旧父章节目标之外的分支")
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
        modes = [node.rewrite_mode, alignment.rewrite_mode]
        if sources or node.legacy_sources:
            priority = {"copy": 1, "light_edit": 2, "restructure": 3}
            mode = max((value for value in modes if value in priority), key=lambda value: priority[value], default="copy")
        else:
            mode = "new_write"
        return node.model_copy(update={
            "rewrite_mode": mode,
            "legacy_section_ids": list(dict.fromkeys([*node.legacy_section_ids, alignment.legacy_section_id])),
            "legacy_sources": list({
                (item["section_id"], item["block_id"], item["content_hash"]): item
                for item in [*node.legacy_sources, *sources]
            }.values()),
            "rewrite_basis": {
                "response_unit_ids": list(dict.fromkeys([*(node.rewrite_basis.get("response_unit_ids", [])), *alignment.matched_response_unit_ids])),
                "condition_ids": list(dict.fromkeys([*(node.rewrite_basis.get("condition_ids", [])), *alignment.matched_condition_ids])),
                "requirement_ids": list(dict.fromkeys([*(node.rewrite_basis.get("requirement_ids", [])), *alignment.matched_requirement_ids])),
            },
            "rewrite_reason": "；".join(filter(None, [node.rewrite_reason, alignment.reason])),
            "required_changes": list(dict.fromkeys([*node.required_changes, *alignment.required_changes])),
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

    parents = {node.parent_local_id for node in nodes if node.parent_local_id}
    finalized = []
    for node in nodes:
        finalized.append(node.model_copy(update={
            "order": len(finalized),
            "rewrite_mode": None if node.local_id in parents else (node.rewrite_mode or "new_write"),
        }))
    return ChapterOutlineCandidate(
        nodes=finalized,
        document_quality_response_unit_ids=initial_outline.document_quality_response_unit_ids,
        review_status=merge_candidate.review_status,
    )


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

    def merge(
        self,
        request: RewriteOutlineMergeInput,
    ) -> StructuredInferenceResult[RewriteOutlineMergeCandidate]:
        return self._invoke(request, repair_attempts=1)

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
