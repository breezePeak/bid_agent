from __future__ import annotations

"""Structured Goal Compiler (PR-A4).

LLM draft → JSON Schema-ish validation → deterministic plan compile.
Falls back to keyword infer_goal_from_message on failure.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.goal import build_plan_for_objectives, infer_goal_from_message
from agent.tool_registry import get_tool
from utils import project_root

_VALID_OBJECTIVE_TYPES = frozenset(
    {
        "status",
        "diagnose",
        "chat",
        "full_generate",
        "fix_coverage",
        "fix_compliance",
        "fix_chapter",
        "export",
    }
)

_VALID_CRITERION_CHECKS = frozenset(
    {
        "artifact_exists",
        "stage_ready",
        "no_stale",
        "score_coverage_min",
        "no_open_blocks",
        "chapters_written",
        "export_preflight",
        "export_preflight_ok",  # legacy alias → normalized to export_preflight
    }
)
_CRITERION_ALIASES = {
    "export_preflight_ok": "export_preflight",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compile_audit_path(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "workspace" / "agent" / "goal_compile_audit.jsonl"


def append_compile_audit(root: Path | None, record: dict[str, Any]) -> None:
    path = compile_audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**record, "at": _now()}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


_COMPILER_SYSTEM = """你是标书 Goal 编译器。把用户目标编译为结构化 JSON（不要 Markdown）。
字段：
{
  "objectives": [{"type": "fix_coverage|fix_compliance|fix_chapter|export|full_generate|status|diagnose|chat", "chapter_ids": []}],
  "scope": {"chapter_ids": [], "include_sections": [], "exclude_sections": []},
  "constraints": {
    "forbid_price_changes": true/false,
    "allow_placeholders_for_missing_materials": true/false,
    "require_compliance_pass_before_export": true/false,
    "block_on_missing_materials": true/false
  },
  "success_criteria": [{"check": "score_coverage_min|artifact_exists|no_open_blocks|no_stale|chapters_written|export_preflight", "path": "", "ratio": 0.95, "chapter_ids": []}],
  "human_confirmation": {"required_for": ["export", "mutations"]}
}
只使用上述 objective type 与 criterion check。不要发明 tool 名或代码。
"""


def validate_goal_draft(draft: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Deterministic validation + normalization of a goal draft."""
    if not isinstance(draft, dict):
        return False, "draft_not_object", {}

    objectives_in = draft.get("objectives") or []
    if not isinstance(objectives_in, list):
        return False, "objectives_not_list", {}

    objectives: list[dict[str, Any]] = []
    for item in objectives_in:
        if not isinstance(item, dict):
            continue
        otype = str(item.get("type") or "").strip()
        if otype not in _VALID_OBJECTIVE_TYPES:
            continue
        row: dict[str, Any] = {"type": otype}
        cids = item.get("chapter_ids") or []
        if isinstance(cids, list) and cids:
            row["chapter_ids"] = [str(x) for x in cids if str(x).strip()][:20]
        if otype == "export":
            row["targets"] = list(item.get("targets") or ["md", "docx"])
        objectives.append(row)

    if not objectives:
        return False, "no_valid_objectives", {}

    scope = draft.get("scope") if isinstance(draft.get("scope"), dict) else {}
    chapter_ids = [str(x) for x in (scope.get("chapter_ids") or []) if str(x).strip()][:20]
    for obj in objectives:
        if obj.get("type") == "fix_chapter" and obj.get("chapter_ids"):
            chapter_ids = list(dict.fromkeys(chapter_ids + list(obj["chapter_ids"])))

    exclude_sections = [str(x) for x in (scope.get("exclude_sections") or []) if str(x).strip()][:50]
    include_sections = [str(x) for x in (scope.get("include_sections") or []) if str(x).strip()][:50]

    raw_c = draft.get("constraints") if isinstance(draft.get("constraints"), dict) else {}
    constraints: dict[str, Any] = {
        "allow_skip_compliance": False,
        "require_human_on_critical": True,
        "require_compliance_before_export": bool(
            raw_c.get(
                "require_compliance_pass_before_export",
                raw_c.get("require_compliance_before_export", True),
            )
        ),
        "block_on_missing_materials": bool(raw_c.get("block_on_missing_materials", True)),
        "material_placeholder_on_missing": bool(
            raw_c.get(
                "allow_placeholders_for_missing_materials",
                raw_c.get("material_placeholder_on_missing", True),
            )
        ),
    }
    if raw_c.get("forbid_price_changes") or raw_c.get("forbid_price_chapters"):
        constraints["forbid_price_chapters"] = True
        constraints["forbid_price_changes"] = True
    if chapter_ids:
        constraints["chapter_ids"] = chapter_ids
    if exclude_sections:
        constraints["exclude_sections"] = exclude_sections
    if include_sections:
        constraints["include_sections"] = include_sections
    # propagate price forbid into exclude hints
    if constraints.get("forbid_price_chapters"):
        for token in ("报价", "价格", "price", "商务报价"):
            if token not in exclude_sections:
                exclude_sections.append(token)
        constraints["exclude_sections"] = exclude_sections

    criteria_in = draft.get("success_criteria") or []
    criteria: list[dict[str, Any]] = []
    if isinstance(criteria_in, list):
        for item in criteria_in:
            if not isinstance(item, dict):
                continue
            check = str(item.get("check") or "").strip()
            check = _CRITERION_ALIASES.get(check, check)
            if check not in _VALID_CRITERION_CHECKS and check not in _CRITERION_ALIASES.values():
                continue
            row = {"check": check}
            if item.get("path"):
                row["path"] = str(item["path"])[:200]
            if item.get("ratio") is not None:
                try:
                    row["ratio"] = float(item["ratio"])
                except (TypeError, ValueError):
                    pass
            if item.get("chapter_ids"):
                row["chapter_ids"] = [str(x) for x in item["chapter_ids"] if str(x).strip()][:20]
            if item.get("paths"):
                row["paths"] = [str(x) for x in item["paths"] if str(x).strip()][:20]
            criteria.append(row)

    # default criteria if empty
    if not criteria:
        types = {o["type"] for o in objectives}
        if "fix_coverage" in types:
            criteria.append({"check": "score_coverage_min", "ratio": 0.95})
        if "fix_compliance" in types:
            criteria.append({"check": "no_open_blocks"})
        if "export" in types or "full_generate" in types:
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                ]
            )
        if "fix_chapter" in types and chapter_ids:
            criteria.append({"check": "chapters_written", "chapter_ids": chapter_ids})

    plan = build_plan_for_objectives(objectives, constraints=constraints, chapter_ids=chapter_ids)
    # strip unregistered tools
    safe_plan: list[dict[str, Any]] = []
    for step in plan:
        tool = str(step.get("tool") or "")
        if tool and get_tool(tool) is None and tool not in {
            "run_stage",
            "run_pipeline_remaining",
            "query_status",
            "diagnose_failure",
            "analyze_coverage",
            "fix_coverage",
            "analyze_compliance",
            "fix_compliance",
            "rewrite_chapters",
            "review_chapters",
            "export_preflight",
            "build_export",
            "write_chapters",
        }:
            continue
        # inject exclude constraints into mutation args
        args = dict(step.get("args") or {})
        if constraints.get("forbid_price_chapters") and tool in {
            "rewrite_chapters",
            "fix_coverage",
            "fix_compliance",
            "write_chapters",
        }:
            args["forbid_price_chapters"] = True
            args["exclude_sections"] = list(constraints.get("exclude_sections") or [])
        step = {**step, "args": args}
        safe_plan.append(step)

    human = draft.get("human_confirmation") if isinstance(draft.get("human_confirmation"), dict) else {}
    required_for = [str(x) for x in (human.get("required_for") or ["export", "mutations"]) if str(x).strip()]

    normalized = {
        "objectives": objectives,
        "success_criteria": criteria,
        "constraints": constraints,
        "chapter_ids": chapter_ids,
        "plan": safe_plan,
        "scope": {
            "chapter_ids": chapter_ids,
            "include_sections": include_sections,
            "exclude_sections": exclude_sections,
        },
        "human_confirmation": {"required_for": required_for},
    }
    return True, "ok", normalized


def compile_goal_from_message(
    message: str,
    *,
    root: Path | None = None,
    llm_chat: Callable[..., str] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Compile user message into normalized goal template + plan.

    Returns same shape as infer_goal_from_message, plus compiler metadata.
    """
    root = root or project_root()
    fallback = infer_goal_from_message(message)
    source = "rules"
    draft: dict[str, Any] | None = None
    error = ""

    if use_llm:
        try:
            if llm_chat is None:
                from llm_client import chat

                raw = chat(
                    [
                        {"role": "system", "content": _COMPILER_SYSTEM},
                        {"role": "user", "content": f"用户目标：\n{message}"},
                    ],
                    temperature=0.1,
                )
            else:
                raw = llm_chat(
                    [
                        {"role": "system", "content": _COMPILER_SYSTEM},
                        {"role": "user", "content": f"用户目标：\n{message}"},
                    ],
                    temperature=0.1,
                )
                if isinstance(raw, dict):
                    raw = raw.get("content") or ""
            draft = _extract_json(str(raw or ""))
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:300]
            draft = None

    if draft:
        ok, reason, normalized = validate_goal_draft(draft)
        if ok:
            source = "llm"
            result = {
                "objectives": normalized["objectives"],
                "success_criteria": normalized["success_criteria"],
                "constraints": normalized["constraints"],
                "chapter_ids": normalized["chapter_ids"],
                "plan": normalized["plan"],
                "scope": normalized.get("scope") or {},
                "human_confirmation": normalized.get("human_confirmation") or {},
                "compiler": {"source": source, "ok": True, "reason": reason},
            }
            append_compile_audit(
                root,
                {
                    "message": (message or "")[:500],
                    "source": source,
                    "ok": True,
                    "objectives": result["objectives"],
                    "constraints": result["constraints"],
                },
            )
            return result
        error = reason or "validate_failed"

    # merge rule fallback with extra compound keyword boosts
    result = {
        **fallback,
        "compiler": {"source": "rules", "ok": True, "fallback_from": error or "llm_skipped"},
    }
    text = message or ""
    types = {str(o.get("type")) for o in (result.get("objectives") or []) if isinstance(o, dict)}
    plan_dirty = False
    if any(k in text for k in ("评分点", "覆盖")) and "fix_coverage" not in types:
        objs = list(result.get("objectives") or [])
        objs.insert(0, {"type": "fix_coverage"})
        result["objectives"] = objs
        crit = list(result.get("success_criteria") or [])
        if not any(c.get("check") == "score_coverage_min" for c in crit if isinstance(c, dict)):
            crit.append({"check": "score_coverage_min", "ratio": 0.95})
        result["success_criteria"] = crit
        plan_dirty = True
    if any(k in text for k in ("合规", "废标")) and "fix_compliance" not in types:
        objs = list(result.get("objectives") or [])
        objs.insert(0, {"type": "fix_compliance"})
        result["objectives"] = objs
        crit = list(result.get("success_criteria") or [])
        if not any(c.get("check") == "no_open_blocks" for c in crit if isinstance(c, dict)):
            crit.append({"check": "no_open_blocks"})
        result["success_criteria"] = crit
        plan_dirty = True
    if any(k in text for k in ("不要改报价", "不要修改报价", "禁止修改报价", "不改价格", "不修改报价")):
        constraints = dict(result.get("constraints") or {})
        constraints["forbid_price_chapters"] = True
        constraints["forbid_price_changes"] = True
        constraints.setdefault("exclude_sections", [])
        for token in ("报价", "价格", "price"):
            if token not in constraints["exclude_sections"]:
                constraints["exclude_sections"].append(token)
        result["constraints"] = constraints
        plan_dirty = True
    # Preserve specialized plans from infer_goal_from_message unless objectives changed
    if plan_dirty or not result.get("plan"):
        chapter_ids = list(
            result.get("chapter_ids") or (result.get("constraints") or {}).get("chapter_ids") or []
        )
        result["plan"] = build_plan_for_objectives(
            result.get("objectives") or [],
            constraints=result.get("constraints") or {},
            chapter_ids=chapter_ids,
        )
    append_compile_audit(
        root,
        {
            "message": (message or "")[:500],
            "source": "rules",
            "ok": True,
            "error": error,
            "objectives": result.get("objectives"),
            "constraints": result.get("constraints"),
        },
    )
    return result
