"""Compile the internal ChapterWritingPlan used by the one writing chain.

This is a Service projection of promoted Blueprint + ScoreModel +
RequirementLedger. It is not a canonical Artifact and does not add a
human Gate. The writer must expand these blocks; it must not invent a
second chapter structure.
"""

from __future__ import annotations

import re
from typing import Any

MAX_BLOCKS = 8
MAX_TEXT = 160

_ROLE_KIND = {
    "content": "response",
    "evidence": "evidence",
    "constraint": "constraint",
    "quality": "quality",
}

_WRITE_AS = {
    "response": "只围绕 Blueprint 原始章节目的和本块 must_answer 回答；按内容本身需要组织表达，不自动补写步骤、分工、输入输出、交付物或验收内容。",
    "evidence": "只写需要出示的证明类型、证明对象和放置位置；没有企业材料就写待补，禁止编造业绩、人员、证书。",
    "constraint": "把约束写进方案如何遵守，并给出可检查口径，不要单独喊口号。",
    "quality": "写成可检查的质控点：查什么、何时查、不合格怎么办；只有招标要求明确验收时才写验收点。",
}

_DELIVERABLE_CUE = re.compile(r"交付|交(\s*)?成果|成果(文件|资料|清单)|提交|移交")
_ACCEPTANCE_CUE = re.compile(r"验收|终验|初验|竣工验收")
_WORK_CONTENT_CUE = re.compile(r"工作内容|具体任务|实施内容|任务说明")

_RUBRIC_LEAK = re.compile(
    r"满分条件|得分任务|得分点|评分要求|评分标准|本节用于|"
    r"按已确认的章节边界|展开具体响应内容"
)

def _clean(value: Any, limit: int = MAX_TEXT) -> str:
    text = _RUBRIC_LEAK.sub("", re.sub(r"\s+", " ", str(value or "")).strip())
    text = text.strip(" ：:；;，,。")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _heading(condition: dict[str, Any], fallback: str, index: int) -> str:
    for key in ("subject", "response_intent", "normalized_condition", "text"):
        text = _clean(condition.get(key), 28)
        if text:
            return text
    return f"{fallback}{index}"


def _outcome_kind(*values: Any) -> str:
    """Return the contractual outcome explicitly required by this writing block."""
    text = " ".join(_clean(value, 200) for value in values if value)
    if _ACCEPTANCE_CUE.search(text):
        return "acceptance"
    if _DELIVERABLE_CUE.search(text):
        return "deliverable"
    return ""


def _write_as(kind: str, outcome_kind: str) -> str:
    if outcome_kind == "deliverable":
        return "在本章相关做法后说明招标文件明确要求的交付成果及其形成方式；不得泛化为“本章交付物”。"
    if outcome_kind == "acceptance":
        return "在本章相关做法后说明招标文件明确要求的验收口径、检查方式或验收依据；不得泛化为“本章交付物”。"
    return _WRITE_AS.get(kind, _WRITE_AS["response"])


def _explicit_purpose_objectives(purpose: str) -> list[str]:
    """Split only an explicit “分别 A 和 B” purpose into its stated objectives."""
    text = _clean(purpose)
    match = re.fullmatch(r"分别(.+?)(?:和|与|及)(.+)", text)
    if not match:
        return []
    left = match.group(1).strip(" ，、；")
    right = match.group(2).strip(" ，、；")
    if not left or not right:
        return []
    for verb in ("论证", "说明", "分析", "阐明", "明确"):
        if left.startswith(verb) and not right.startswith(verb):
            right = f"{verb}{right}"
            break
    return [left, right]


def compile_chapter_writing_plan(
    chapter: dict[str, Any],
    *,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
    writing_orientation: dict[str, Any] | None = None,
    chapter_context_items: list[dict[str, Any]] | None = None,
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ordered writing blocks the chapter body must cover."""
    node = chapter.get("blueprint_node") if isinstance(chapter.get("blueprint_node"), dict) else {}
    title = str(chapter.get("title") or node.get("title") or "当前章节")
    purpose = str(node.get("purpose") or "").strip()
    objectives = [
        str(item).strip()
        for item in node.get("writing_objectives") or []
        if str(item or "").strip()
    ][:6]
    if not objectives:
        objectives = _explicit_purpose_objectives(purpose)
    primary_unit_ids = {
        str(item) for item in (node.get("primary_response_unit_ids") or []) if item
    }
    supporting_unit_ids = {
        str(item) for item in (node.get("supporting_response_unit_ids") or []) if item
    }
    chapter_condition_ids = {
        str(item) for item in (node.get("score_condition_ids") or []) if item
    }

    req_by_id = {
        str(item.get("requirement_id") or ""): _clean(item.get("text") or "", 90)
        for item in (tender_requirements or [])
        if isinstance(item, dict) and item.get("requirement_id")
    }

    blocks: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def add_block(
        *,
        kind: str,
        heading: str,
        must_answer: str,
        score_point_id: str = "",
        condition_id: str = "",
        requirement_ids: list[str] | None = None,
        ownership: str = "primary",
        outcome_kind: str = "",
        project_fact_refs: list[str] | None = None,
    ) -> None:
        if len(blocks) >= MAX_BLOCKS:
            return
        answer = _clean(must_answer, 140)
        head = _clean(heading, 28) or f"响应要点{len(blocks) + 1}"
        key = f"{kind}:{condition_id or head}:{answer}"
        if not answer or key in seen_keys:
            return
        seen_keys.add(key)
        blocks.append(
            {
                "block_id": f"WO-{len(blocks) + 1}",
                "kind": kind,
                "heading": head,
                "must_answer": answer,
                "write_as": _write_as(kind, outcome_kind),
                "outcome_kind": outcome_kind,
                "score_point_id": score_point_id,
                "condition_id": condition_id,
                "requirement_ids": [
                    item for item in (requirement_ids or []) if item
                ][:4],
                "ownership": ownership,
                "project_fact_refs": list(project_fact_refs or [])[:4],
            }
        )

    for point in scoring_requirements or []:
        if not isinstance(point, dict):
            continue
        score_id = str(point.get("score_point_id") or "")
        conditions = list(point.get("conditions") or point.get("score_conditions") or [])
        units = list(point.get("response_units") or [])
        unit_by_condition: dict[str, dict[str, Any]] = {}
        for unit in units:
            if not isinstance(unit, dict):
                continue
            for condition_id in unit.get("condition_ids") or []:
                unit_by_condition[str(condition_id)] = unit
        if not conditions and (point.get("response_expectation") or point.get("title")):
            add_block(
                kind="response",
                heading=str(point.get("title") or "评分响应"),
                must_answer=str(
                    point.get("response_expectation") or point.get("title") or ""
                ),
                score_point_id=score_id,
                ownership="primary" if primary_unit_ids else "supporting",
                outcome_kind=_outcome_kind(
                    point.get("title"), point.get("response_expectation")
                ),
            )
            continue
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            condition_id = str(condition.get("condition_id") or "")
            if chapter_condition_ids and condition_id not in chapter_condition_ids:
                continue
            role = str(condition.get("condition_role") or "content")
            if role == "document":
                continue
            kind = _ROLE_KIND.get(role, "response")
            unit = unit_by_condition.get(condition_id, {})
            unit_id = str(unit.get("unit_id") or "")
            if unit_id and unit_id in supporting_unit_ids and unit_id not in primary_unit_ids:
                ownership = "supporting"
            else:
                ownership = "primary"
            linked = [
                str(item)
                for item in (unit.get("linked_requirement_ids") or [])
                if str(item) in req_by_id
            ]
            add_block(
                kind=kind,
                heading=_heading(condition, "响应要点", len(blocks) + 1),
                must_answer=(
                    condition.get("response_intent")
                    or condition.get("normalized_condition")
                    or condition.get("text")
                    or ""
                ),
                score_point_id=score_id,
                condition_id=condition_id,
                requirement_ids=linked,
                ownership=ownership,
                outcome_kind=_outcome_kind(
                    _heading(condition, "", len(blocks) + 1),
                    condition.get("response_intent"),
                    condition.get("normalized_condition"),
                    condition.get("text"),
                    *(req_by_id.get(requirement_id, "") for requirement_id in linked),
                ),
            )

    project = project_context if isinstance(project_context, dict) else {}
    work_packages = list(project.get("work_packages") or [])
    work_focus = " ".join([purpose, *objectives, *[
        str(item.get("must_answer") or "") for item in blocks if isinstance(item, dict)
    ]])
    if work_packages and _WORK_CONTENT_CUE.search(work_focus):
        inherited = blocks[0] if len(blocks) == 1 and blocks[0].get("kind") == "response" else {}
        blocks = []
        seen_keys.clear()
        for index, item in enumerate(work_packages[:MAX_BLOCKS]):
            if isinstance(item, dict):
                fact = str(
                    item.get("statement")
                    or item.get("text")
                    or item.get("description")
                    or item.get("title")
                    or ""
                ).strip()
            else:
                fact = str(item or "").strip()
            if not fact:
                continue
            heading = re.split(r"[：:；;。]", fact, maxsplit=1)[0].strip() or f"任务 {index + 1}"
            add_block(
                kind="response",
                heading=heading,
                must_answer=fact,
                score_point_id=str(inherited.get("score_point_id") or ""),
                condition_id=str(inherited.get("condition_id") or ""),
                requirement_ids=list(inherited.get("requirement_ids") or []),
                ownership=str(inherited.get("ownership") or "primary"),
                outcome_kind=str(inherited.get("outcome_kind") or ""),
                project_fact_refs=[f"work_packages[{index}]"],
            )

    if not blocks:
        for objective in objectives:
            add_block(kind="response", heading=objective, must_answer=objective)
        if purpose and not blocks:
            add_block(kind="response", heading=title, must_answer=purpose)
        if not blocks:
            add_block(
                kind="response",
                heading=title,
                must_answer=f"围绕「{title}」写清本章做法和可检查口径",
            )

    local_facts = [
        _clean(f"{item.get('title') or ''}:{item.get('body') or ''}", 80)
        for item in (chapter_context_items or [])
        if isinstance(item, dict) and item.get("kind") in {"KEY_FACT", "GOAL"}
    ][:4]
    usable_project_facts = [
        _clean(
            item.get("statement") or item.get("text") or item.get("description") or item.get("title") or "",
            120,
        )
        if isinstance(item, dict)
        else _clean(item, 120)
        for item in work_packages[:MAX_BLOCKS]
    ]
    usable_project_facts = [item for item in usable_project_facts if item]

    return {
        "schema_version": "v3.chapter-writing-plan.v1",
        "chapter_id": str(chapter.get("chapter_id") or node.get("chapter_id") or ""),
        "chapter_title": title,
        "purpose": purpose,
        "writing_objectives": objectives,
        "block_count": len(blocks),
        "blocks": blocks,
        "usable_local_facts": local_facts,
        "usable_project_facts": usable_project_facts,
        "writing_rule": (
            "Blueprint 的 purpose 与 writing_objectives 是唯一章节目标，必须按原文执行，不得分类、改写或扩展。"
            "按 blocks 顺序回答评分条件明确要求的 must_answer；write_as 只是中性表达规则，不能另造章节写法。"
            "输入材料只是证据池，不得因为材料中存在某项事实就把它写进正文。"
            "只有 outcome_kind=deliverable/acceptance 的块，才能写对应的交付成果或验收内容；"
            "其他块不得机械补写“本章交付物”。不要输出提纲标题本身，不要出现评分术语。"
        ),
    }


# Historical import alias. This is the same internal plan compiler, not an
# approval artifact or an alternate writing route.
compile_chapter_writing_outline = compile_chapter_writing_plan
