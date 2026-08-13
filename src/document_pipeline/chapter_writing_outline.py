"""Compile a chapter writing outline from score conditions.

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
    "response": "写可执行做法：步骤、分工、输入输出、本章交付物。不要复述评分原文。",
    "evidence": "只写需要出示的证明类型、证明对象和放置位置；没有企业材料就写待补，禁止编造业绩、人员、证书。",
    "constraint": "把约束写进方案如何遵守，并给出可检查口径，不要单独喊口号。",
    "quality": "写成可检查的质控/验收点：查什么、何时查、不合格怎么办。",
}

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


def compile_chapter_writing_outline(
    chapter: dict[str, Any],
    *,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
    writing_orientation: dict[str, Any] | None = None,
    chapter_context_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build ordered writing blocks the chapter body must cover."""
    node = chapter.get("blueprint_node") if isinstance(chapter.get("blueprint_node"), dict) else {}
    title = str(chapter.get("title") or node.get("title") or "当前章节")
    purpose = _clean(
        (writing_orientation or {}).get("writing_purpose", {}).get("purpose")
        if isinstance((writing_orientation or {}).get("writing_purpose"), dict)
        else node.get("purpose")
        or "",
        180,
    )
    objectives = [
        _clean(item, 80)
        for item in (
            ((writing_orientation or {}).get("writing_purpose") or {}).get("writing_objectives")
            if isinstance((writing_orientation or {}).get("writing_purpose"), dict)
            else None
        )
        or node.get("writing_objectives")
        or []
        if str(item or "").strip()
    ][:6]
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
                "write_as": _WRITE_AS.get(kind, _WRITE_AS["response"]),
                "score_point_id": score_point_id,
                "condition_id": condition_id,
                "requirement_ids": [
                    item for item in (requirement_ids or []) if item
                ][:4],
                "ownership": ownership,
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
                must_answer=f"围绕「{title}」写清本章做法、交付物和可检查口径",
            )

    local_facts = [
        _clean(f"{item.get('title') or ''}:{item.get('body') or ''}", 80)
        for item in (chapter_context_items or [])
        if isinstance(item, dict) and item.get("kind") in {"KEY_FACT", "GOAL"}
    ][:4]

    return {
        "schema_version": "v3.chapter-writing-outline.v1",
        "chapter_id": str(chapter.get("chapter_id") or node.get("chapter_id") or ""),
        "chapter_title": title,
        "purpose": purpose,
        "block_count": len(blocks),
        "blocks": blocks,
        "usable_local_facts": local_facts,
        "writing_rule": (
            "按 blocks 顺序写正文，每块至少一段；每段写清做法或检查口径，"
            "并给出本章交付物或验收点。不要输出提纲标题本身，不要出现评分术语。"
        ),
    }
