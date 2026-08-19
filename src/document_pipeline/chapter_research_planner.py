"""Plan public research for a chapter draft by model decision.

Flow:
1. Deterministically compile writing orientation (purpose, document position,
   relations) plus a short materials inventory.
2. The chapter agent first confirms that orientation, then decides whether
   existing materials are enough, and only then whether to web_search.
3. Never paste the full tender or a raw project_context JSON dump into search.

Search need is model-owned after orientation is confirmed unless the caller
explicitly marks a user search request with ``force_research=True``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .sibling_chapter_context import _chapter_role
from .writing_orientation import compact_orientation_for_prompt

MAX_BRIEF_CHARS = 700
MAX_QUERY_CHARS = 420
MAX_TASK_SNIPPETS = 4
MAX_SIBLING_SNIPPETS = 3


def _clean(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _identity(project_context: dict[str, Any]) -> dict[str, str]:
    identity = project_context.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    return {str(k): str(v).strip() for k, v in identity.items() if str(v).strip()}


def _pick_identity(identity: dict[str, str], *keys: str) -> str:
    lowered = {k.casefold(): v for k, v in identity.items()}
    for key in keys:
        if key in identity and identity[key]:
            return identity[key]
        value = lowered.get(key.casefold(), "")
        if value:
            return value
    return ""


def _list_snip(values: Any, *, limit: int = 3, each: int = 48) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for item in values:
        text = _clean(item, each)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def distill_chapter_research_brief(
    chapter: dict[str, Any],
    *,
    project_context: dict[str, Any] | None = None,
    sibling_context: dict[str, Any] | None = None,
    writing_orientation: dict[str, Any] | None = None,
    inspected_chapters: list[dict[str, Any]] | None = None,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    """Build a compact, chapter-relevant brief for the model to decide on research."""
    orientation = compact_orientation_for_prompt(writing_orientation)
    purpose_block = orientation.get("writing_purpose") or {}
    position_block = orientation.get("document_position") or {}
    relations_block = orientation.get("chapter_relations") or {}
    materials_block = orientation.get("existing_materials") or {}
    node = chapter.get("blueprint_node") if isinstance(chapter.get("blueprint_node"), dict) else {}
    title = _clean(
        purpose_block.get("title") or chapter.get("title") or node.get("title") or "当前章节",
        80,
    )
    purpose = _clean(purpose_block.get("purpose") or node.get("purpose") or "", 160)
    objectives = [
        _clean(item, 80)
        for item in (
            purpose_block.get("writing_objectives")
            or node.get("writing_objectives")
            or []
        )
        if str(item or "").strip()
    ][:4]
    chapter_role = str(
        purpose_block.get("role")
        or (sibling_context or {}).get("chapter_role")
        or _chapter_role(title, purpose)
    )
    ctx = project_context if isinstance(project_context, dict) else {}
    identity = _identity(ctx)
    project_name = _pick_identity(identity, "project_name", "项目名称", "project", "项目")
    purchaser = _pick_identity(identity, "purchaser", "buyer", "采购人", "招标人")

    task_snippets = [
        *_list_snip(ctx.get("scope"), limit=2),
        *_list_snip(ctx.get("work_packages"), limit=2),
        *_list_snip(ctx.get("processing"), limit=1),
    ][:MAX_TASK_SNIPPETS]

    chapter_ctx = chapter.get("context") if isinstance(chapter.get("context"), dict) else {}
    local_focus = [
        _clean(f"{item.get('title') or ''}:{item.get('body') or ''}", 90)
        for item in (chapter_ctx.get("items") or [])
        if isinstance(item, dict) and (item.get("title") or item.get("body"))
    ][:3]

    req_focus = [
        _clean(item.get("text") or "", 90)
        for item in (tender_requirements or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ][:2]
    score_focus = [
        _clean(
            f"{item.get('title') or ''}:{item.get('response_expectation') or ''}",
            90,
        )
        for item in (scoring_requirements or [])
        if isinstance(item, dict)
    ][:2]

    sibling = sibling_context if isinstance(sibling_context, dict) else {}
    sibling_ready: list[str] = []
    for item in inspected_chapters or []:
        if not isinstance(item, dict):
            continue
        snippet = _clean(
            f"{item.get('title') or item.get('chapter_id')}: "
            f"{item.get('summary') or item.get('purpose') or ''}",
            140,
        )
        if snippet:
            sibling_ready.append(snippet)
        if len(sibling_ready) >= MAX_SIBLING_SNIPPETS:
            break
    if not sibling_ready:
        for item in sibling.get("siblings") or []:
            if not isinstance(item, dict) or not item.get("has_content"):
                continue
            sibling_ready.append(
                _clean(
                    f"{item.get('title') or item.get('chapter_id')}: "
                    f"{item.get('purpose') or ''}",
                    140,
                )
            )
            if len(sibling_ready) >= MAX_SIBLING_SNIPPETS:
                break
    missing_upstream = [
        _clean(item.get("title") or item.get("chapter_id") or "", 40)
        for item in (
            relations_block.get("missing_upstream")
            or sibling.get("missing_upstream")
            or []
        )
        if isinstance(item, dict)
    ][:4]
    relation_lines = []
    for item in relations_block.get("items") or []:
        if not isinstance(item, dict):
            continue
        relation_lines.append(
            _clean(
                f"{item.get('relation_label') or item.get('relation')}:"
                f"{item.get('title') or item.get('chapter_id')}",
                60,
            )
        )
        if len(relation_lines) >= 6:
            break
    materials_notes = [
        _clean(item, 80)
        for item in (materials_block.get("notes") or [])
        if str(item or "").strip()
    ][:6]

    focus_text = " ".join(
        [
            title,
            purpose,
            " ".join(objectives),
            instruction,
            " ".join(local_focus),
            " ".join(req_focus),
            " ".join(score_focus),
        ]
    )

    brief = {
        "project_name": project_name,
        "purchaser": purchaser,
        "related_tasks": task_snippets,
        "chapter_id": str(chapter.get("chapter_id") or ""),
        "chapter_title": title,
        "chapter_role": chapter_role,
        "chapter_purpose": purpose,
        "writing_objectives": objectives,
        "document_path": _clean(position_block.get("path_label") or title, 120),
        "chapter_relations": relation_lines,
        "existing_materials": materials_notes,
        "orientation_summary": _clean(orientation.get("summary_text") or "", 400),
        "chapter_focus_notes": local_focus,
        "requirement_focus": req_focus,
        "scoring_focus": score_focus,
        "sibling_ready_summaries": sibling_ready,
        "missing_upstream_siblings": missing_upstream,
        "user_instruction": _clean(instruction, 120),
        "focus_keywords": _extract_keywords(focus_text),
    }
    brief["brief_text"] = _render_brief_text(brief)
    return brief


def _extract_keywords(text: str, *, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9_\-]{2,20}", text or "")
    stop = {
        "本章", "章节", "内容", "说明", "进行", "以及", "相关", "工作", "项目",
        "技术", "要求", "完成", "提供", "实现", "包括", "通过", "需要", "应当",
    }
    out: list[str] = []
    for token in tokens:
        if token in stop or token in out:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _render_brief_text(brief: dict[str, Any]) -> str:
    lines = [
        f"项目：{brief.get('project_name') or '（未命名）'}",
    ]
    if brief.get("purchaser"):
        lines.append(f"采购人：{brief['purchaser']}")
    if brief.get("related_tasks"):
        lines.append("相关任务：" + "；".join(brief["related_tasks"]))
    lines.append(
        f"本章：《{brief.get('chapter_title') or ''}》"
        f"（角色提示={brief.get('chapter_role') or 'general'}，仅供参考）"
    )
    if brief.get("orientation_summary"):
        lines.append(f"写作处境：{brief['orientation_summary']}")
    if brief.get("chapter_purpose"):
        lines.append(f"写作目的：{brief['chapter_purpose']}")
    if brief.get("document_path"):
        lines.append(f"全书位置：{brief['document_path']}")
    if brief.get("chapter_relations"):
        lines.append("章节关系：" + "；".join(brief["chapter_relations"]))
    if brief.get("existing_materials"):
        lines.append("已有资料：" + "；".join(brief["existing_materials"]))
    if brief.get("writing_objectives"):
        lines.append("写作目标：" + "；".join(brief["writing_objectives"]))
    if brief.get("requirement_focus"):
        lines.append("本章招标要点：" + "；".join(brief["requirement_focus"]))
    if brief.get("scoring_focus"):
        lines.append("本章评分要点：" + "；".join(brief["scoring_focus"]))
    if brief.get("sibling_ready_summaries"):
        lines.append("已有兄弟章摘要：" + " | ".join(brief["sibling_ready_summaries"]))
    if brief.get("missing_upstream_siblings"):
        lines.append("待补兄弟章：" + "、".join(brief["missing_upstream_siblings"]))
    if brief.get("focus_keywords"):
        lines.append("候选关键词：" + "、".join(brief["focus_keywords"]))
    if brief.get("user_instruction"):
        lines.append(f"用户补充：{brief['user_instruction']}")
    text = "\n".join(lines)
    return text if len(text) <= MAX_BRIEF_CHARS else text[: MAX_BRIEF_CHARS - 1] + "…"


def _sanitize_search_query(query: str, brief: dict[str, Any]) -> str:
    """Keep model query compact; rebuild from brief if it looks like a dump."""
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    if not text:
        return ""
    if (
        len(text) > MAX_QUERY_CHARS
        or text.count("{") > 3
        or '"confirmed_facts"' in text
        or '"identity"' in text
        or "整份招标" in text
    ):
        # Formatting repair only — does not decide need_research.
        parts = [
            "根据整理要点检索可核验公开资料，不要复述整份招标文件。",
            f"项目：{brief.get('project_name') or '（未知）'}",
        ]
        if brief.get("related_tasks"):
            parts.append("任务：" + "；".join(brief["related_tasks"][:3]))
        parts.append(f"章节：{brief.get('chapter_title') or ''}")
        if brief.get("chapter_purpose"):
            parts.append(f"目的：{brief['chapter_purpose']}")
        if brief.get("focus_keywords"):
            parts.append("关键词：" + "、".join(brief["focus_keywords"][:6]))
        parts.append("目标：与本章直接相关的公开标准、同类方法或专业规范。")
        text = " ".join(parts)
    if len(text) > MAX_QUERY_CHARS:
        text = text[: MAX_QUERY_CHARS - 1] + "…"
    return text


def plan_chapter_research(
    chapter: dict[str, Any],
    *,
    project_context: dict[str, Any] | None = None,
    sibling_context: dict[str, Any] | None = None,
    writing_orientation: dict[str, Any] | None = None,
    inspected_chapters: list[dict[str, Any]] | None = None,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
    instruction: str = "",
    force_research: bool = False,
    decision_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Confirm orientation, then decide search from existing materials.

    Returns:
        need_research, reason, search_query, brief, decision_source,
        orientation_confirmed, orientation_summary
    """
    brief = distill_chapter_research_brief(
        chapter,
        project_context=project_context,
        sibling_context=sibling_context,
        writing_orientation=writing_orientation,
        inspected_chapters=inspected_chapters,
        tender_requirements=tender_requirements,
        scoring_requirements=scoring_requirements,
        instruction=instruction,
    )
    decision = (
        decision_provider(brief)
        if decision_provider is not None
        else _model_decide(brief)
    )
    if decision is None:
        if force_research:
            query = _fallback_search_query(brief)
            return {
                "need_research": bool(query),
                "reason": "用户明确要求联网搜索，已按章节上下文执行。",
                "search_query": query,
                "brief": brief,
                "decision_source": "explicit_request_fallback",
                "orientation_confirmed": True,
                "orientation_summary": str(brief.get("orientation_summary") or ""),
                "existing_materials_sufficient": False,
            }
        return {
            "need_research": False,
            "reason": "章节 Agent 未能完成检索决策，已跳过公开检索，直接基于已有要点写作。",
            "search_query": "",
            "brief": brief,
            "decision_source": "agent_unavailable",
            "orientation_confirmed": False,
            "orientation_summary": str(brief.get("orientation_summary") or ""),
            "existing_materials_sufficient": True,
        }

    confirmed = bool(decision.get("orientation_confirmed", True))
    materials_enough = bool(decision.get("existing_materials_sufficient"))
    need = bool(decision.get("need_research"))
    # An explicit user request has priority over the model's sufficiency judgment.
    if not force_research and (not confirmed or materials_enough):
        need = False
    reason = str(decision.get("reason") or "").strip()
    query = _sanitize_search_query(str(decision.get("search_query") or ""), brief)
    if (need or force_research) and not query:
        # Model asked for research but forgot query — ask not invented need.
        query = _sanitize_search_query(
            " ".join(
                [
                    str(brief.get("project_name") or ""),
                    str(brief.get("chapter_title") or ""),
                    " ".join(brief.get("related_tasks") or []),
                    " ".join(brief.get("focus_keywords") or []),
                    "公开标准 或 同类方法",
                ]
            ),
            brief,
        )
    if not need and not force_research:
        query = ""

    need = bool(need or force_research)

    return {
        "need_research": bool(need and query),
        "reason": reason
        or (
            "已有资料足够，无需公开检索"
            if materials_enough or not need
            else "需要公开资料补充"
        ),
        "search_query": query if need else "",
        "brief": brief,
        "decision_source": "chapter_agent",
        "orientation_confirmed": confirmed,
        "orientation_summary": str(
            decision.get("orientation_summary") or brief.get("orientation_summary") or ""
        ).strip(),
        "existing_materials_sufficient": materials_enough,
    }


def _fallback_search_query(brief: dict[str, Any]) -> str:
    """Build a conservative chapter-scoped query without inventing project facts."""
    return _sanitize_search_query(
        " ".join(
            [
                str(brief.get("project_name") or ""),
                str(brief.get("chapter_title") or ""),
                " ".join(brief.get("related_tasks") or []),
                " ".join(brief.get("focus_keywords") or []),
                "政策 标准 同类方法",
            ]
        ),
        brief,
    )


def _model_decide(brief: dict[str, Any]) -> dict[str, Any] | None:
    """Confirm writing orientation, then decide research from existing materials."""
    system = (
        "你是标书章节写作 Agent 的检索规划器。"
        "必须先确认写作处境，再根据已有资料决定是否联网检索。"
        "唯一输入是「已整理的本章相关要点」，不是整份招标文件。"
        "\n决策顺序："
        "1) 先确认写作目的、本章在整份标书中的位置、与其他章节的关系；"
        "orientation_summary 用一两句中文复述这三点。"
        "2) 再盘点已有资料（物化上下文、招标/评分要点、已读他章）是否足够支撑本章目的。"
        "3) 只有已有资料仍缺公开政策、标准或通用方法时，才 need_research=true。"
        "4) 需要检索时，search_query 必须短、具体，只含项目名、相关任务要点、本章焦点；"
        "禁止粘贴长文、JSON、整标原文。"
        "5) 公开检索只能补政策/标准/同类专业方法等可核验资料，"
        "不能证明本企业资质、业绩、人员、报价或承诺。"
        "6) 图示/路线图类章节若兄弟章已给出阶段骨架，优先直接成图。"
        "7) 不要为了“显得全面”而每次都检索。"
        "\n只输出 JSON 对象，不要 Markdown："
        '{"orientation_confirmed":true,'
        '"orientation_summary":"本章写什么、在全书何处、与何章何关系",'
        '"existing_materials_sufficient":true/false,'
        '"need_research":true/false,'
        '"reason":"简短中文理由",'
        '"search_query":"需要时填写，否则空字符串"}'
    )
    user = (
        "已整理要点（唯一决策输入）：\n"
        f"{brief.get('brief_text') or ''}\n\n"
        "请先确认写作处境，再判断已有资料是否足够，最后给出是否检索的 JSON。"
    )
    try:
        from llm_client import chat

        try:
            raw = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
            )
        except TypeError:
            raw = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        raw = str(raw or "").strip()
    except Exception:
        return None

    payload = _parse_json_object(raw)
    if not isinstance(payload, dict):
        return None
    if "need_research" not in payload:
        return None
    return {
        "orientation_confirmed": bool(payload.get("orientation_confirmed", True)),
        "orientation_summary": _clean(payload.get("orientation_summary") or "", 240),
        "existing_materials_sufficient": bool(
            payload.get("existing_materials_sufficient")
        ),
        "need_research": bool(payload.get("need_research")),
        "reason": _clean(payload.get("reason") or "", 200),
        "search_query": str(payload.get("search_query") or "").strip(),
    }


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
