"""Deterministic, score-direct outline construction for recoverable LLM defects.

The builder intentionally owns only auto-outline layout.  Strict templates keep
their original topology and are represented verbatim, so deterministic recovery
never inserts score-group or condition headings into a template contract.
"""

from __future__ import annotations

import re
from hashlib import sha256
from collections import defaultdict

from .contracts import RequirementLedger, ScoreModel, TemplateStructureContract
from .planning_inference import ChapterOutlineCandidate, ChapterOutlineNodeCandidate
from .scoring_outline_policy import (
    full_score_condition_heading,
    is_hollow_quality_heading,
    is_sectionable_quality_condition,
    outline_subject,
)


def _local_id(prefix: str, value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    suffix = sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{normalized or 'node'}-{suffix}"


def _group_subject(title: str) -> str:
    value = re.sub(r"[（(][^）)]*(?:分|明标|暗标)[^）)]*[）)]", "", title)
    value = re.sub(r"(明标|暗标)", "", value)
    return outline_subject(value)


def _path_for_unit(unit: object, point: object, group_title: str) -> list[str]:
    raw = list(getattr(unit, "outline_path", []) or getattr(point, "outline_path", []))
    path = [str(item).strip() for item in raw if str(item).strip()]
    if path and _group_subject(path[0]) == _group_subject(group_title):
        path.pop(0)
    compact: list[str] = []
    for item in path:
        if not compact or outline_subject(compact[-1]) != outline_subject(item):
            compact.append(item)
    return compact


def _condition_title(condition: object, index: int) -> str:
    subject = str(getattr(condition, "subject", "") or "").strip()
    if subject and not is_hollow_quality_heading(subject):
        return subject[:56]
    text = str(
        getattr(condition, "normalized_condition", "")
        or getattr(condition, "text", "")
    )
    return full_score_condition_heading(text, index)


def build_deterministic_outline_candidate(
    ledger: RequirementLedger,
    scores: ScoreModel,
    template_structure: TemplateStructureContract | None,
) -> ChapterOutlineCandidate:
    """Build a complete, stable outline from ScoreModel semantics.

    It is used after a recoverable outline-provider defect (for example omitted
    condition IDs).  All orders are assigned while nodes are appended in
    depth-first order, making the final sequence global and deterministic.
    """

    visible_units_by_group: dict[str, list[tuple[object, object]]] = defaultdict(list)
    visible_unit_ids: list[str] = []
    quality_unit_ids: list[str] = []
    conditions_by_id = {
        condition.condition_id: condition
        for point in scores.points
        for condition in point.score_conditions
    }
    for point in scores.points:
        if point.review_status == "blocked":
            continue
        for unit in point.response_units:
            if unit.review_status == "blocked":
                continue
            if unit.response_scope == "document":
                quality_unit_ids.append(unit.unit_id)
            else:
                visible_unit_ids.append(unit.unit_id)
                visible_units_by_group[point.group_id].append((point, unit))

    active_requirements = {
        requirement.requirement_id
        for requirement in ledger.requirements
        if requirement.status not in {"blocked", "waived"}
    }
    visible_condition_ids = {
        condition_id
        for units in visible_units_by_group.values()
        for _point, unit in units
        for condition_id in unit.condition_ids
        if condition_id in conditions_by_id
    }

    if template_structure is not None:
        slots_by_node: dict[str, list[str]] = defaultdict(list)
        for slot in template_structure.slots:
            slots_by_node[slot.node_id].append(slot.slot_id)
        template_nodes = sorted(template_structure.nodes, key=lambda item: item.order)
        first_node_id = template_nodes[0].node_id
        linked_requirement_ids = [
            requirement.requirement_id
            for requirement in ledger.requirements
            if requirement.requirement_id in active_requirements
            and any(
                requirement.requirement_id in unit.linked_requirement_ids
                for units in visible_units_by_group.values()
                for _point, unit in units
            )
        ]
        return ChapterOutlineCandidate(
            nodes=[
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
                        visible_unit_ids if node.node_id == first_node_id else []
                    ),
                    score_condition_ids=(
                        sorted(visible_condition_ids)
                        if node.node_id == first_node_id
                        else []
                    ),
                    requirement_ids=(
                        linked_requirement_ids if node.node_id == first_node_id else []
                    ),
                    template_slot_ids=slots_by_node.get(node.node_id, []),
                    target_size=800,
                    confidence=1.0,
                )
                for node in template_nodes
            ],
            document_quality_response_unit_ids=quality_unit_ids,
            review_status="draft",
        )

    nodes: list[ChapterOutlineNodeCandidate] = []

    def append(**kwargs: object) -> str:
        local_id = str(kwargs["local_id"])
        kwargs.setdefault("confidence", 1.0)
        nodes.append(
            ChapterOutlineNodeCandidate(order=len(nodes), **kwargs)
        )
        return local_id

    for group in scores.groups:
        units = visible_units_by_group.get(group.group_id, [])
        if not units:
            continue
        group_id = append(
            local_id=_local_id("outline-group", group.group_id),
            parent_local_id=None,
            title=group.title,
            purpose="组织该评分组的独立得分响应任务",
            writing_objectives=[],
            primary_response_unit_ids=[],
            score_condition_ids=[],
            requirement_ids=[],
            target_size=200,
        )
        path_nodes: dict[tuple[str, ...], str] = {}
        primary_owner: dict[str, str] = {}
        for point, unit in units:
            path = _path_for_unit(unit, point, group.title)
            parent_id = group_id
            key_parts: list[str] = []
            for path_index, title in enumerate(path):
                key_parts.append(outline_subject(title) or title)
                key = tuple(key_parts)
                local_id = path_nodes.get(key)
                if local_id is None:
                    local_id = append(
                        local_id=_local_id(
                            f"outline-path-{group.group_id}-{path_index}",
                            "-".join(key_parts),
                        ),
                        parent_local_id=parent_id,
                        title=title,
                        purpose="承接评分来源中的目录语义层级",
                        writing_objectives=[],
                        primary_response_unit_ids=[],
                        score_condition_ids=[],
                        requirement_ids=[],
                        target_size=500,
                    )
                    path_nodes[key] = local_id
                parent_id = local_id

            primary_title = path[-1] if path else unit.title
            primary_id = parent_id
            # A shared path leaf cannot carry two independent primary units.
            if primary_id == group_id or primary_id in primary_owner:
                primary_id = append(
                    local_id=_local_id("outline-unit", unit.unit_id),
                    parent_local_id=parent_id,
                    title=unit.title,
                    purpose=unit.response_expectation,
                    writing_objectives=[],
                    primary_response_unit_ids=[],
                    score_condition_ids=[],
                    requirement_ids=[],
                    target_size=900,
                )
            elif not path:
                primary_id = append(
                    local_id=_local_id("outline-unit", unit.unit_id),
                    parent_local_id=group_id,
                    title=primary_title,
                    purpose=unit.response_expectation,
                    writing_objectives=[],
                    primary_response_unit_ids=[],
                    score_condition_ids=[],
                    requirement_ids=[],
                    target_size=900,
                )
            primary_owner[primary_id] = unit.unit_id
            primary_index = next(
                index for index, node in enumerate(nodes) if node.local_id == primary_id
            )

            unit_condition_ids = [
                condition_id
                for condition_id in unit.condition_ids
                if condition_id in visible_condition_ids
            ]
            sectionable_ids = [
                condition_id
                for condition_id in unit_condition_ids
                if conditions_by_id[condition_id].condition_role in {"content", "evidence"}
                or is_sectionable_quality_condition(conditions_by_id[condition_id])
            ]
            primary_condition_ids = [
                condition_id
                for condition_id in unit_condition_ids
                if condition_id not in sectionable_ids
            ]
            objectives = [unit.response_expectation]
            for condition_id in primary_condition_ids:
                condition = conditions_by_id[condition_id]
                if condition.condition_role in {"quality", "constraint"}:
                    objectives.append(condition.response_intent)
            nodes[primary_index] = nodes[primary_index].model_copy(
                update={
                    "purpose": unit.response_expectation,
                    "writing_objectives": list(dict.fromkeys(filter(None, objectives))),
                    "primary_response_unit_ids": [unit.unit_id],
                    "score_condition_ids": primary_condition_ids,
                    "requirement_ids": [
                        requirement_id
                        for requirement_id in unit.linked_requirement_ids
                        if requirement_id in active_requirements
                    ],
                    "target_size": 1000,
                    "confidence": unit.confidence,
                }
            )
            primary_subject = outline_subject(nodes[primary_index].title)
            for index, condition_id in enumerate(sectionable_ids, start=1):
                condition = conditions_by_id[condition_id]
                title = _condition_title(condition, index)
                # Do not manufacture a duplicate child whose only topic is the
                # already existing primary title.
                if outline_subject(title) == primary_subject:
                    nodes[primary_index] = nodes[primary_index].model_copy(
                        update={
                            "score_condition_ids": [
                                *nodes[primary_index].score_condition_ids,
                                condition_id,
                            ]
                        }
                    )
                    continue
                append(
                    local_id=_local_id("outline-condition", condition_id),
                    parent_local_id=primary_id,
                    title=title,
                    purpose=condition.response_intent,
                    writing_objectives=[condition.response_intent],
                    # Keep the unit as supporting only: primary ownership stays
                    # on the unit chapter; child condition slices still need the
                    # unit frozen for evidence-need resolution.
                    primary_response_unit_ids=[],
                    supporting_response_unit_ids=[unit.unit_id],
                    score_condition_ids=[condition_id],
                    requirement_ids=[],
                    planned_tables=(
                        ["证明材料清单"]
                        if condition.condition_role == "evidence"
                        else []
                    ),
                    target_size=600,
                    confidence=condition.confidence,
                )

    if not nodes:
        append(
            local_id=_local_id("outline-requirements", "fallback"),
            parent_local_id=None,
            title="招标需求响应",
            purpose="在评分模型未形成可用章节时，完整承接招标需求并保留待复核缺口",
            writing_objectives=["逐项响应招标需求并明确交付、实施和验收安排"],
            primary_response_unit_ids=[],
            score_condition_ids=[],
            requirement_ids=sorted(active_requirements),
            target_size=1200,
        )

    return ChapterOutlineCandidate(
        nodes=nodes,
        document_quality_response_unit_ids=quality_unit_ids,
        review_status="draft",
    )
