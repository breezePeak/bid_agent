"""Single authoritative implementation of ``planning.chapter_outline_split``.

The skill owns every structural decision for a bid-response outline.  Model
output is treated only as optional prose/visual annotation and can never add,
remove, reorder or re-parent nodes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from .contracts import RequirementLedger, ScoreModel, TemplateStructureContract
from .planning_inference import (
    ChapterOutlineAnnotationCandidate,
    ChapterOutlineCandidate,
    ChapterOutlineNodeCandidate,
)
from .scoring_outline_policy import (
    is_applicability_scope_heading,
    is_hollow_quality_heading,
    is_sectionable_quality_condition,
    outline_subject,
    outline_structure_key,
)


def _stable_id(prefix: str, *parts: str) -> str:
    source = "\x1f".join(parts)
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-")[:36]
    return f"{prefix}-{readable or 'node'}-{sha256(source.encode('utf-8')).hexdigest()[:12]}"


def _compact_path(point: object, unit: object, group_title: str) -> list[str]:
    raw = list(getattr(point, "outline_path", []) or [])
    if not raw:
        raw = list(getattr(unit, "outline_path", []) or [])
    path = [str(item).strip() for item in raw if str(item).strip()]
    if path and outline_structure_key(path[0]) == outline_structure_key(group_title):
        path.pop(0)
    if not path:
        fallback = str(
            getattr(point, "title", "") or getattr(unit, "title", "")
        ).strip()
        if fallback:
            path.append(fallback)
    compact: list[str] = []
    for title in path:
        if is_applicability_scope_heading(title):
            continue
        if not compact or outline_structure_key(compact[-1]) != outline_structure_key(title):
            compact.append(title)
    return compact


def _condition_is_leaf(condition: object) -> bool:
    subject = str(getattr(condition, "subject", "") or "").strip()
    normalized_subject = re.sub(r"\s+", "", subject)
    if not normalized_subject or is_hollow_quality_heading(normalized_subject):
        return False
    role = str(getattr(condition, "condition_role", "content") or "content")
    if role in {"content", "evidence", "constraint"}:
        return True
    if role != "quality" or not is_sectionable_quality_condition(condition):
        return False
    return not bool(
        re.fullmatch(
            r"(?:方案|文档|投标文件|响应文件|整体|总体)?(?:质量|品质|效果)",
            normalized_subject,
        )
    )


def _condition_subject(condition: object) -> str:
    """Return the structured business object supplied by ScoreModel."""

    return str(getattr(condition, "subject", "") or "").strip()


def _business_object_key(value: str) -> str:
    """Normalize formatting only; never infer a title from criterion prose."""

    return outline_structure_key(outline_subject(value))


def _condition_objective(condition: object) -> str:
    return str(
        getattr(condition, "response_intent", "")
        or getattr(condition, "normalized_condition", "")
        or getattr(condition, "text", "")
    ).strip()


def _annotation_index(
    candidate: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None,
) -> tuple[
    dict[str, list[object]],
    dict[str, list[object]],
    dict[str, list[object]],
]:
    by_id: dict[str, list[object]] = defaultdict(list)
    by_unit: dict[str, list[object]] = defaultdict(list)
    by_title: dict[str, list[object]] = defaultdict(list)
    if candidate is None:
        return by_id, by_unit, by_title
    items = (
        candidate.annotations
        if isinstance(candidate, ChapterOutlineAnnotationCandidate)
        else candidate.nodes
    )
    for node in items:
        target_node_id = str(
            getattr(node, "target_node_id", "")
            or getattr(node, "local_id", "")
        ).strip()
        if target_node_id:
            by_id[target_node_id].append(node)
        title = str(
            getattr(node, "target_title", "") or getattr(node, "title", "")
        )
        by_title[outline_structure_key(title)].append(node)
        response_unit_ids = list(
            getattr(node, "response_unit_ids", [])
            or [
                *getattr(node, "primary_response_unit_ids", []),
                *getattr(node, "supporting_response_unit_ids", []),
            ]
        )
        for unit_id in response_unit_ids:
            by_unit[unit_id].append(node)
    return by_id, by_unit, by_title


def _annotations_for(
    local_id: str,
    title: str,
    unit_ids: list[str],
    by_id: dict[str, list[object]],
    by_unit: dict[str, list[object]],
    by_title: dict[str, list[object]],
) -> list[object]:
    selected: list[object] = []
    seen: set[str] = set()
    for node in by_id.get(local_id, []):
        identity = str(id(node))
        seen.add(identity)
        selected.append(node)
    for unit_id in unit_ids:
        for node in by_unit.get(unit_id, []):
            identity = str(id(node))
            if identity not in seen:
                seen.add(identity)
                selected.append(node)
    for node in by_title.get(outline_structure_key(title), []):
        identity = str(id(node))
        if identity not in seen:
            seen.add(identity)
            selected.append(node)
    return selected


def _merged_strings(
    selected: list[object], field_name: str
) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for node in selected
            for value in getattr(node, field_name)
            if str(value).strip()
        )
    )


def _annotated_node(
    *,
    local_id: str,
    parent_local_id: str | None,
    order: int,
    title: str,
    purpose: str,
    unit_ids: list[str],
    by_id: dict[str, list[object]],
    by_unit: dict[str, list[object]],
    by_title: dict[str, list[object]],
    **bindings: object,
) -> ChapterOutlineNodeCandidate:
    selected = _annotations_for(
        local_id, title, unit_ids, by_id, by_unit, by_title
    )
    structural_objectives = list(bindings.pop("writing_objectives", []) or [])
    structural_mentions = list(bindings.pop("required_mentions", []) or [])
    structural_tables = list(bindings.pop("planned_tables", []) or [])
    structural_figures = list(bindings.pop("planned_figures", []) or [])
    annotated_purpose = next(
        (node.purpose for node in selected if node.purpose.strip()),
        purpose,
    )
    return ChapterOutlineNodeCandidate(
        local_id=local_id,
        parent_local_id=parent_local_id,
        order=order,
        title=title,
        purpose=annotated_purpose,
        writing_objectives=list(
            dict.fromkeys(
                [*structural_objectives, *_merged_strings(selected, "writing_objectives")]
            )
        ),
        required_mentions=list(
            dict.fromkeys(
                [*structural_mentions, *_merged_strings(selected, "required_mentions")]
            )
        ),
        planned_tables=list(
            dict.fromkeys(
                [*structural_tables, *_merged_strings(selected, "planned_tables")]
            )
        ),
        planned_figures=list(
            dict.fromkeys(
                [*structural_figures, *_merged_strings(selected, "planned_figures")]
            )
        ),
        target_size=max([int(bindings.pop("target_size", 800)), *(node.target_size for node in selected)]),
        confidence=min([1.0, *(node.confidence for node in selected)]),
        needs_human=any(node.needs_human for node in selected),
        **bindings,
    )


class ChapterOutlineSplitSkill:
    """Build the only production outline topology used by BidAgent."""

    skill_id = "planning.chapter_outline_split"

    @classmethod
    def execute(
        cls,
        ledger: RequirementLedger,
        scores: ScoreModel,
        template_structure: TemplateStructureContract | None = None,
        *,
        annotations: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None = None,
    ) -> ChapterOutlineCandidate:
        by_id, by_unit, by_title = _annotation_index(annotations)
        if template_structure is not None:
            return cls._template_outline(
                ledger,
                scores,
                template_structure,
                by_id,
                by_unit,
                by_title,
                annotations,
            )
        return cls._score_outline(
            ledger, scores, by_id, by_unit, by_title, annotations
        )

    @staticmethod
    def _template_outline(
        ledger: RequirementLedger,
        scores: ScoreModel,
        template: TemplateStructureContract,
        by_id: dict[str, list[object]],
        by_unit: dict[str, list[object]],
        by_title: dict[str, list[object]],
        annotations: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None,
    ) -> ChapterOutlineCandidate:
        template_nodes = sorted(template.nodes, key=lambda item: item.order)
        slots_by_node: dict[str, list[str]] = defaultdict(list)
        for slot in template.slots:
            slots_by_node[slot.node_id].append(slot.slot_id)
        section_units = [
            unit
            for point in scores.points
            if getattr(point, "review_status", "confirmed") != "blocked"
            for unit in point.response_units
            if getattr(unit, "review_status", "confirmed") != "blocked"
            and unit.response_scope == "section"
        ]
        quality_units = [
            unit.unit_id
            for point in scores.points
            if getattr(point, "review_status", "confirmed") != "blocked"
            for unit in point.response_units
            if getattr(unit, "review_status", "confirmed") != "blocked"
            and unit.response_scope == "document"
        ]
        active_requirements = {
            item.requirement_id
            for item in ledger.requirements
            if getattr(item, "status", "confirmed") not in {"blocked", "waived"}
        }
        first_id = template_nodes[0].node_id
        condition_ids = [
            condition_id
            for unit in section_units
            for condition_id in unit.condition_ids
        ]
        requirement_ids = list(
            dict.fromkeys(
                requirement_id
                for unit in section_units
                for requirement_id in unit.linked_requirement_ids
                if requirement_id in active_requirements
            )
        )
        nodes = [
            _annotated_node(
                local_id=node.node_id,
                parent_local_id=node.parent_node_id,
                order=node.order,
                title=node.title,
                purpose="保持严格模板既有章节结构",
                unit_ids=[unit.unit_id for unit in section_units],
                by_id=by_id,
                by_unit=by_unit,
                by_title=by_title,
                primary_response_unit_ids=(
                    [unit.unit_id for unit in section_units]
                    if node.node_id == first_id
                    else []
                ),
                score_condition_ids=(condition_ids if node.node_id == first_id else []),
                requirement_ids=(requirement_ids if node.node_id == first_id else []),
                template_slot_ids=slots_by_node.get(node.node_id, []),
                target_size=800,
            )
            for node in template_nodes
        ]
        return ChapterOutlineCandidate(
            nodes=nodes,
            document_quality_response_unit_ids=quality_units,
            review_status=(annotations.review_status if annotations else "draft"),
        )

    @staticmethod
    def _score_outline(
        ledger: RequirementLedger,
        scores: ScoreModel,
        by_id: dict[str, list[object]],
        by_unit: dict[str, list[object]],
        by_title: dict[str, list[object]],
        annotations: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None,
    ) -> ChapterOutlineCandidate:
        active_requirements = {
            item.requirement_id
            for item in ledger.requirements
            if getattr(item, "status", "confirmed") not in {"blocked", "waived"}
        }
        conditions = {
            condition.condition_id: condition
            for point in scores.points
            if getattr(point, "review_status", "confirmed") != "blocked"
            for condition in point.score_conditions
            if getattr(condition, "review_status", "confirmed") != "blocked"
        }
        units_by_group: dict[str, list[tuple[object, object]]] = defaultdict(list)
        quality_units: list[str] = []
        for point in scores.points:
            if getattr(point, "review_status", "confirmed") == "blocked":
                continue
            for unit in point.response_units:
                if getattr(unit, "review_status", "confirmed") == "blocked":
                    continue
                if unit.response_scope == "document":
                    quality_units.append(unit.unit_id)
                else:
                    units_by_group[point.group_id].append((point, unit))

        nodes: list[ChapterOutlineNodeCandidate] = []
        for group in scores.groups:
            group_units = units_by_group.get(group.group_id, [])
            if not group_units:
                continue
            root_id = _stable_id("group", group.group_id)
            nodes.append(
                _annotated_node(
                    local_id=root_id,
                    parent_local_id=None,
                    order=len(nodes),
                    title=group.title,
                    purpose=f"完整响应{group.title}评分要求",
                    unit_ids=[],
                    by_id=by_id,
                    by_unit=by_unit,
                    by_title=by_title,
                    target_size=200,
                )
            )
            path_nodes: dict[tuple[str, ...], str] = {}
            topic_node_indexes: dict[tuple[str, str], int] = {}
            for point, unit in group_units:
                path = _compact_path(point, unit, group.title)
                parent_id = root_id
                keys: list[str] = []
                for path_index, title in enumerate(path):
                    keys.append(outline_structure_key(title) or title)
                    frozen = tuple(keys)
                    local_id = path_nodes.get(frozen)
                    if local_id is None:
                        local_id = _stable_id(
                            "factor", group.group_id, str(path_index), *keys
                        )
                        nodes.append(
                            _annotated_node(
                                local_id=local_id,
                                parent_local_id=parent_id,
                                order=len(nodes),
                                title=title,
                                purpose=f"完整响应评分因素“{title}”",
                                unit_ids=[unit.unit_id],
                                by_id=by_id,
                                by_unit=by_unit,
                                by_title=by_title,
                                target_size=800,
                            )
                        )
                        path_nodes[frozen] = local_id
                    parent_id = local_id

                primary_index = next(
                    index for index, node in enumerate(nodes) if node.local_id == parent_id
                )
                primary = nodes[primary_index]
                unit_condition_ids = [
                    condition_id
                    for condition_id in unit.condition_ids
                    if condition_id in conditions
                ]
                leaf_condition_ids: list[str] = []
                parent_condition_ids: list[str] = []
                parent_subject_key = _business_object_key(primary.title)
                for condition_id in unit_condition_ids:
                    condition = conditions[condition_id]
                    subject_key = _business_object_key(
                        _condition_subject(condition)
                    )
                    if (
                        not subject_key
                        or not _condition_is_leaf(condition)
                        or subject_key == parent_subject_key
                    ):
                        parent_condition_ids.append(condition_id)
                    else:
                        leaf_condition_ids.append(condition_id)

                response_expectation = str(
                    getattr(unit, "response_expectation", "")
                    or getattr(point, "response_expectation", "")
                    or f"完整响应{getattr(unit, 'title', primary.title)}"
                ).strip()
                objectives = [response_expectation]
                objectives.extend(
                    _condition_objective(conditions[condition_id])
                    for condition_id in parent_condition_ids
                )
                nodes[primary_index] = primary.model_copy(
                    update={
                        "purpose": response_expectation or primary.purpose,
                        "writing_objectives": list(
                            dict.fromkeys(
                                [*primary.writing_objectives, *filter(None, objectives)]
                            )
                        ),
                        "primary_response_unit_ids": list(
                            dict.fromkeys([*primary.primary_response_unit_ids, unit.unit_id])
                        ),
                        "score_condition_ids": list(
                            dict.fromkeys([*primary.score_condition_ids, *parent_condition_ids])
                        ),
                        "requirement_ids": list(
                            dict.fromkeys(
                                [
                                    *primary.requirement_ids,
                                    *(
                                        requirement_id
                                        for requirement_id in unit.linked_requirement_ids
                                        if requirement_id in active_requirements
                                    ),
                                ]
                            )
                        ),
                        "target_size": max(primary.target_size, 1000),
                        "confidence": min(
                            primary.confidence,
                            float(getattr(unit, "confidence", 1.0)),
                        ),
                        "needs_human": primary.needs_human or any(
                            not _business_object_key(
                                _condition_subject(conditions[condition_id])
                            )
                            for condition_id in parent_condition_ids
                        ),
                    }
                )

                for condition_id in leaf_condition_ids:
                    condition = conditions[condition_id]
                    title = _condition_subject(condition)
                    subject_key = _business_object_key(title)
                    topic_key = (parent_id, subject_key)
                    objective = _condition_objective(condition)
                    existing_index = topic_node_indexes.get(topic_key)
                    if existing_index is not None:
                        existing = nodes[existing_index]
                        nodes[existing_index] = existing.model_copy(
                            update={
                                "writing_objectives": list(
                                    dict.fromkeys(
                                        [
                                            *existing.writing_objectives,
                                            *([objective] if objective else []),
                                        ]
                                    )
                                ),
                                "supporting_response_unit_ids": list(
                                    dict.fromkeys(
                                        [
                                            *existing.supporting_response_unit_ids,
                                            unit.unit_id,
                                        ]
                                    )
                                ),
                                "score_condition_ids": list(
                                    dict.fromkeys(
                                        [*existing.score_condition_ids, condition_id]
                                    )
                                ),
                                "required_mentions": list(
                                    dict.fromkeys(
                                        [*existing.required_mentions, point.score_point_id]
                                    )
                                ),
                                "confidence": min(
                                    existing.confidence,
                                    float(getattr(unit, "confidence", 1.0)),
                                ),
                            }
                        )
                        continue
                    child = _annotated_node(
                        local_id=_stable_id(
                            "subject",
                            group.group_id,
                            *keys,
                            subject_key,
                        ),
                        parent_local_id=parent_id,
                        order=len(nodes),
                        title=title,
                        purpose="逐项响应最高得分档的原子要求",
                        unit_ids=[unit.unit_id],
                        by_id=by_id,
                        by_unit=by_unit,
                        by_title=by_title,
                        writing_objectives=[objective] if objective else [],
                        supporting_response_unit_ids=[unit.unit_id],
                        score_condition_ids=[condition_id],
                        required_mentions=[point.score_point_id],
                        target_size=600,
                    )
                    # Structural objectives are authoritative; annotations may
                    # enrich but cannot erase the exact score condition.
                    child = child.model_copy(
                        update={
                            "writing_objectives": list(
                                dict.fromkeys(
                                    [objective, *child.writing_objectives]
                                    if objective
                                    else child.writing_objectives
                                )
                            )
                        }
                    )
                    nodes.append(child)
                    topic_node_indexes[topic_key] = len(nodes) - 1

        if not nodes:
            nodes.append(
                ChapterOutlineNodeCandidate(
                    local_id=_stable_id("fallback", "requirements"),
                    parent_local_id=None,
                    order=0,
                    title="招标需求响应",
                    purpose="逐项响应招标需求",
                    writing_objectives=["逐项响应招标需求并明确交付、实施和验收安排"],
                    requirement_ids=sorted(active_requirements),
                    target_size=1200,
                    confidence=1.0,
                )
            )
        return ChapterOutlineCandidate(
            nodes=nodes,
            document_quality_response_unit_ids=quality_units,
            review_status=(
                "needs_review"
                if any(node.needs_human for node in nodes)
                else (annotations.review_status if annotations else "draft")
            ),
        )


def build_chapter_outline(
    ledger: RequirementLedger,
    scores: ScoreModel,
    template_structure: TemplateStructureContract | None = None,
    *,
    annotations: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None = None,
) -> ChapterOutlineCandidate:
    """Public function for every internal caller of the registered Skill."""

    return ChapterOutlineSplitSkill.execute(
        ledger,
        scores,
        template_structure,
        annotations=annotations,
    )


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(
            **{str(key): _namespace(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def build_chapter_outline_from_payload(
    requirement_ledger: dict[str, Any],
    score_model: dict[str, Any],
    template_structure: dict[str, Any] | None = None,
    *,
    annotations: ChapterOutlineAnnotationCandidate | ChapterOutlineCandidate | None = None,
) -> ChapterOutlineCandidate:
    """Execute the same Skill from frozen JSON snapshots before compilation."""

    ledger = _namespace(requirement_ledger)
    scores = _namespace(score_model)
    template = _namespace(template_structure) if template_structure is not None else None
    return ChapterOutlineSplitSkill.execute(
        ledger,
        scores,
        template,
        annotations=annotations,
    )
