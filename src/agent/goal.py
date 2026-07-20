from __future__ import annotations

"""Goal state machine + plan execution driver (PR-9/10)."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.invalidation import is_stale, load_stale
from pipeline_registry import artifact_exists, stage_outputs_ready, stage_spec_by_id
from utils import project_root

GOAL_TERMINAL = frozenset(
    {"succeeded", "blocked_human", "blocked_policy", "budget_exceeded", "failed", "cancelled"}
)
GOAL_ACTIVE = frozenset({"pending", "in_progress", "awaiting_confirmation"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def goal_path(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "workspace" / "agent" / "goal_state.json"


def new_goal_id() -> str:
    return uuid4().hex[:12]


def load_goal(root: Path | None = None) -> dict[str, Any] | None:
    path = goal_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def save_goal(root: Path | None, goal: dict[str, Any]) -> Path:
    root = root or project_root()
    path = goal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    goal = dict(goal)
    goal["updated_at"] = _now()
    path.write_text(json.dumps(goal, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _criterion_artifact_exists(root: Path, path: str) -> dict[str, Any]:
    from pipeline_registry import RunArtifact

    art = RunArtifact(path=path, kind="file", required_nonempty=True)
    ok = artifact_exists(root, art) and not is_stale(root, path)
    return {
        "check": "artifact_exists",
        "path": path,
        "ok": ok,
        "detail": "missing_or_stale" if not ok else "ready",
    }


def _criterion_stage_ready(root: Path, stage_id: str) -> dict[str, Any]:
    try:
        ok = stage_outputs_ready(root, stage_id)
    except Exception as exc:  # noqa: BLE001
        return {"check": "stage_ready", "stage_id": stage_id, "ok": False, "detail": str(exc)}
    try:
        stage = stage_spec_by_id(stage_id)
        stale_hits = [a.path for a in stage.produces if a.kind != "virtual" and is_stale(root, a.path)]
    except Exception:
        stale_hits = []
    if stale_hits:
        return {
            "check": "stage_ready",
            "stage_id": stage_id,
            "ok": False,
            "detail": f"stale:{','.join(stale_hits[:5])}",
        }
    # diagnose goals should not "succeed" merely because an empty workspace looks ready
    if stage_id == "init_workspace" and not (root / "workspace").exists():
        return {"check": "stage_ready", "stage_id": stage_id, "ok": False, "detail": "workspace_missing"}
    return {"check": "stage_ready", "stage_id": stage_id, "ok": bool(ok), "detail": "ready" if ok else "incomplete"}


def _criterion_no_stale(root: Path, paths: list[str] | None = None) -> dict[str, Any]:
    state = load_stale(root)
    items = state.get("items") or {}
    if paths:
        hits = [p for p in paths if p in items]
    else:
        hits = sorted(items.keys())
    return {
        "check": "no_stale",
        "ok": len(hits) == 0,
        "detail": "clean" if not hits else f"stale:{','.join(hits[:8])}",
        "stale_paths": hits,
    }


def _criterion_score_coverage_min(root: Path, min_ratio: float = 0.95) -> dict[str, Any]:
    path = root / "workspace" / "score_coverage_matrix.json"
    if not path.exists():
        return {
            "check": "score_coverage_min",
            "ok": False,
            "detail": "missing_matrix",
            "ratio": None,
            "min_ratio": min_ratio,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"check": "score_coverage_min", "ok": False, "detail": "invalid_matrix", "min_ratio": min_ratio}
    summary = data.get("summary") if isinstance(data, dict) else {}
    total = int((summary or {}).get("score_point_count") or 0)
    fully = int((summary or {}).get("fully_covered_score_point_count") or 0)
    uncovered = data.get("uncovered_score_points") if isinstance(data, dict) else []
    if total <= 0:
        matrix = data.get("matrix") if isinstance(data, dict) else []
        total = len(matrix) if isinstance(matrix, list) else 0
        fully = total - len(uncovered or [])
    ratio = (fully / total) if total else 0.0
    ok = ratio >= float(min_ratio) and (not uncovered if float(min_ratio) >= 0.999 else True)
    if uncovered and float(min_ratio) < 0.999:
        ok = ratio >= float(min_ratio)
    return {
        "check": "score_coverage_min",
        "ok": bool(ok),
        "detail": f"ratio={ratio:.3f} fully={fully}/{total} uncovered={len(uncovered or [])}",
        "ratio": ratio,
        "min_ratio": min_ratio,
    }


def _criterion_no_open_blocks(root: Path) -> dict[str, Any]:
    try:
        from agent.issues import open_block_issues

        blocks = open_block_issues(root)
    except Exception:
        blocks = []
    return {
        "check": "no_open_blocks",
        "ok": len(blocks) == 0,
        "detail": "clean" if not blocks else f"open_blocks={len(blocks)}",
        "count": len(blocks),
    }


def _criterion_export_preflight(root: Path) -> dict[str, Any]:
    try:
        from agent.issues import export_preflight

        pf = export_preflight(root)
        ok = bool(pf.get("can_export"))
        return {
            "check": "export_preflight",
            "ok": ok,
            "detail": str(pf.get("message") or ("ok" if ok else "blocked")),
            "accepted_risks": len(pf.get("accepted_risks") or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {"check": "export_preflight", "ok": False, "detail": str(exc)}


def evaluate_criteria(root: Path, criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in criteria:
        check = str(item.get("check") or "")
        if check == "artifact_exists":
            results.append(_criterion_artifact_exists(root, str(item.get("path") or "")))
        elif check == "stage_ready":
            results.append(_criterion_stage_ready(root, str(item.get("stage_id") or "")))
        elif check == "no_stale":
            paths = item.get("paths") if isinstance(item.get("paths"), list) else None
            results.append(_criterion_no_stale(root, paths))
        elif check == "score_coverage_min":
            min_ratio = float(item.get("ratio") or item.get("min_ratio") or 0.95)
            results.append(_criterion_score_coverage_min(root, min_ratio=min_ratio))
        elif check == "chapters_written":
            chapter_ids = item.get("chapter_ids") if isinstance(item.get("chapter_ids"), list) else []
            missing = []
            for cid in chapter_ids:
                p = root / "workspace" / "chapters" / f"{cid}.md"
                if not p.exists() or p.stat().st_size == 0:
                    missing.append(str(cid))
            results.append(
                {
                    "check": "chapters_written",
                    "ok": not missing,
                    "detail": "ready" if not missing else f"missing:{','.join(missing)}",
                    "chapter_ids": chapter_ids,
                }
            )
        elif check == "no_open_blocks":
            results.append(_criterion_no_open_blocks(root))
        elif check == "export_preflight":
            results.append(_criterion_export_preflight(root))
        else:
            results.append({"check": check or "unknown", "ok": False, "detail": "unsupported_check"})
    return results


def _open_issue_codes(root: Path) -> set[str]:
    try:
        from agent.issues import load_open_issues

        return {
            str(i.get("code") or "")
            for i in load_open_issues(root)
            if str(i.get("status") or "open") == "open" and i.get("code")
        }
    except Exception:
        return set()


def _plan_step_done(root: Path, step: dict[str, Any]) -> bool:
    status = str(step.get("status") or "pending")
    if status in {"done", "skipped"}:
        return True
    # optional completion probe via criteria-like checks
    done_if = step.get("done_if") if isinstance(step.get("done_if"), dict) else {}
    if done_if.get("check"):
        results = evaluate_criteria(root, [done_if])
        return bool(results and results[0].get("ok"))
    return False


def _run_if_matches(root: Path, step: dict[str, Any]) -> bool:
    run_if = step.get("run_if")
    if not run_if:
        return True
    if not isinstance(run_if, dict):
        return True
    codes = run_if.get("open_issue_codes")
    if isinstance(codes, list) and codes:
        open_codes = _open_issue_codes(root)
        if not any(str(c) in open_codes for c in codes):
            return False
    if "score_coverage_below" in run_if:
        min_ratio = float(run_if.get("score_coverage_below") or 0.95)
        result = _criterion_score_coverage_min(root, min_ratio=min_ratio)
        if result.get("ok"):
            return False
    if run_if.get("always"):
        return True
    return True


def _deps_satisfied(plan: list[dict[str, Any]], step: dict[str, Any]) -> bool:
    depends = step.get("depends_on")
    if not depends:
        return True
    if not isinstance(depends, list):
        depends = [depends]
    status_by_id = {str(s.get("step_id")): str(s.get("status") or "pending") for s in plan if isinstance(s, dict)}
    for dep in depends:
        if status_by_id.get(str(dep)) not in {"done", "skipped"}:
            return False
    return True


def refresh_plan_statuses(root: Path, goal: dict[str, Any]) -> dict[str, Any]:
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    if not plan:
        return goal
    for step in plan:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "pending")
        if status in {"running", "blocked", "failed"}:
            continue
        if status == "done":
            continue
        if not _run_if_matches(root, step):
            step["status"] = "skipped"
            step["skip_reason"] = "run_if_not_matched"
            continue
        if _plan_step_done(root, step):
            step["status"] = "done"
            continue
        if status == "skipped" and _run_if_matches(root, step) and not _plan_step_done(root, step):
            step["status"] = "pending"
            step.pop("skip_reason", None)
    # advance current_plan_index to first incomplete runnable step
    idx = 0
    for i, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        st = str(step.get("status") or "pending")
        if st in {"pending", "running", "blocked", "failed"}:
            idx = i
            break
        idx = i + 1
    goal["plan"] = plan
    goal["current_plan_index"] = idx
    done = sum(1 for s in plan if isinstance(s, dict) and str(s.get("status")) in {"done", "skipped"})
    goal["progress"] = {
        "plan_total": len(plan),
        "plan_done": done,
        "plan_ratio": round(done / len(plan), 3) if plan else 0.0,
        "current_step_id": (plan[idx].get("step_id") if idx < len(plan) and isinstance(plan[idx], dict) else ""),
    }
    return goal


def next_plan_step(root: Path, goal: dict[str, Any] | None = None) -> dict[str, Any] | None:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        return None
    goal = refresh_plan_statuses(root, goal)
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    for step in plan:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "pending")
        if status not in {"pending", "blocked"}:
            continue
        if not _deps_satisfied(plan, step):
            continue
        if not _run_if_matches(root, step):
            step["status"] = "skipped"
            continue
        if _plan_step_done(root, step):
            step["status"] = "done"
            continue
        return step
    return None


def mark_plan_step(
    root: Path | None,
    step_id: str,
    *,
    status: str,
    error: str = "",
    goal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    for step in plan:
        if not isinstance(step, dict):
            continue
        if str(step.get("step_id")) != str(step_id):
            continue
        prev = str(step.get("status") or "pending")
        step["status"] = status
        if status == "running" or (status == "failed" and prev != "running"):
            step["attempts"] = int(step.get("attempts") or 0) + 1
        if error:
            step["last_error"] = error[:500]
        if status == "done":
            step["completed_at"] = _now()
            if prev == "pending":
                step["attempts"] = int(step.get("attempts") or 0) + 1
        break
    goal["plan"] = plan
    goal = refresh_plan_statuses(root, goal)
    save_goal(root, goal)
    return goal


def goal_succeeded(goal: dict[str, Any] | None) -> bool:
    if not goal:
        return False
    return str(goal.get("status")) == "succeeded" or bool(goal.get("all_criteria_ok"))


def set_goal_status(
    root: Path | None,
    status: str,
    *,
    blocked_reason: str = "",
    goal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    goal["status"] = status
    if blocked_reason:
        goal["blocked_reason"] = blocked_reason
    elif status not in {"blocked_human", "blocked_policy", "budget_exceeded", "failed"}:
        goal["blocked_reason"] = ""
    save_goal(root, goal)
    return goal


def grant_confirmation(root: Path | None, tools: list[str] | str | None = None, *, all_mutations: bool = False) -> dict[str, Any]:
    """Record that user confirmed mutation tools for the active goal."""
    root = root or project_root()
    goal = load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    scope = dict(goal.get("confirmation_scope") or {})
    allowed = set(scope.get("tools") or [])
    if all_mutations:
        scope["all_mutations"] = True
    if tools:
        if isinstance(tools, str):
            tools = [tools]
        allowed.update(str(t) for t in tools if str(t).strip())
    scope["tools"] = sorted(allowed)
    scope["confirmed_at"] = _now()
    goal["confirmation_scope"] = scope
    if str(goal.get("status")) == "awaiting_confirmation":
        goal["status"] = "in_progress"
    save_goal(root, goal)
    return goal


def confirmation_allows(goal: dict[str, Any] | None, tool: str, *, user_confirmed: bool = False) -> bool:
    if user_confirmed:
        return True
    if not goal:
        return False
    scope = goal.get("confirmation_scope") if isinstance(goal.get("confirmation_scope"), dict) else {}
    if scope.get("all_mutations"):
        return True
    tools = scope.get("tools") if isinstance(scope.get("tools"), list) else []
    return str(tool) in {str(t) for t in tools}


def detect_human_block(root: Path, goal: dict[str, Any] | None = None) -> str:
    """Detect materials / fatal issues that require human intervention."""
    try:
        from agent.snapshot import build_snapshot, human_blocking_reason

        snap = build_snapshot(root, goal=goal, for_llm=False)
        return human_blocking_reason(snap, goal)
    except Exception:
        pass
    # fallback materials
    try:
        from materials_checklist import load_materials_checklist

        data = load_materials_checklist(root)
        items = data.get("items") if isinstance(data.get("items"), list) else []
        hard = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity") or "").lower()
            resp = str(item.get("response_status") or "")
            evidence = str(item.get("evidence_status") or "")
            if sev in {"block", "fatal", "critical", "blocker"} and resp in {"deferred", "missing", ""} and evidence in {
                "missing",
                "weak",
                "",
            }:
                hard.append(str(item.get("requirement") or item.get("item_id")))
        if hard:
            return "缺少不可自动补齐的材料: " + "；".join(hard[:5])
    except Exception:
        pass
    return ""


def resume_goal_after_materials(root: Path | None, *, note: str = "") -> dict[str, Any]:
    """Clear blocked_human and re-enter in_progress after materials upload."""
    root = root or project_root()
    goal = load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    if str(goal.get("status")) == "blocked_human":
        goal["status"] = "in_progress"
        goal["blocked_reason"] = ""
        goal["resume_note"] = note or "materials_updated"
        goal["resumed_at"] = _now()
        # one-shot: do not immediately re-block on same materials snapshot
        constraints = dict(goal.get("constraints") or {})
        constraints["block_on_missing_materials"] = False
        goal["constraints"] = constraints
    goal = refresh_plan_statuses(root, goal)
    save_goal(root, goal)
    # evaluate criteria without auto re-blocking
    return reevaluate_goal(root, goal)


def reevaluate_goal(root: Path | None, goal: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    criteria = goal.get("success_criteria") if isinstance(goal.get("success_criteria"), list) else []
    results = evaluate_criteria(root, criteria)
    all_ok = all(bool(r.get("ok")) for r in results) if results else False
    status = str(goal.get("status") or "pending")

    # refresh plan progress first
    if isinstance(goal.get("plan"), list) and goal.get("plan"):
        goal = refresh_plan_statuses(root, goal)

    if status in {"cancelled", "failed", "budget_exceeded", "blocked_policy"}:
        pass
    elif all_ok:
        status = "succeeded"
        goal["blocked_reason"] = ""
    elif status == "awaiting_confirmation":
        pass
    elif status == "blocked_human":
        # auto-clear if blocking condition gone
        reason = detect_human_block(root, goal)
        if not reason:
            status = "in_progress"
            goal["blocked_reason"] = ""
        else:
            goal["blocked_reason"] = reason
    else:
        objectives = [
            str(o.get("type") or "")
            for o in (goal.get("normalized_objectives") or [])
            if isinstance(o, dict)
        ]
        actionable = any(
            t in objectives
            for t in ("fix_coverage", "fix_compliance", "export", "full_generate", "fix_chapter")
        )
        reason = detect_human_block(root, goal) if actionable else ""
        if reason and not all_ok and actionable:
            # only block when materials/fatal truly block and criteria not met
            constraints = goal.get("constraints") if isinstance(goal.get("constraints"), dict) else {}
            if constraints.get("block_on_missing_materials", True):
                status = "blocked_human"
                goal["blocked_reason"] = reason
            else:
                status = "in_progress"
        else:
            status = "in_progress"

    goal["status"] = status
    goal["criteria_results"] = results
    goal["all_criteria_ok"] = all_ok
    # progress criteria ratio
    if results:
        ok_n = sum(1 for r in results if r.get("ok"))
        progress = dict(goal.get("progress") or {})
        progress["criteria_total"] = len(results)
        progress["criteria_ok"] = ok_n
        progress["criteria_ratio"] = round(ok_n / len(results), 3)
        goal["progress"] = progress
    save_goal(root, goal)
    return goal


def build_plan_for_objectives(
    objectives: list[dict[str, Any]],
    *,
    constraints: dict[str, Any] | None = None,
    chapter_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Map normalized objectives to a constrained tool plan."""
    constraints = constraints or {}
    chapter_ids = chapter_ids or list(constraints.get("chapter_ids") or [])
    plan: list[dict[str, Any]] = []
    types = [str(o.get("type") or "") for o in objectives if isinstance(o, dict)]

    def add(step_id: str, tool: str, args: dict[str, Any] | None = None, **extra: Any) -> None:
        step = {
            "step_id": step_id,
            "tool": tool,
            "args": args or {},
            "depends_on": extra.get("depends_on") or [],
            "run_if": extra.get("run_if") or {},
            "status": "pending",
            "attempts": 0,
            "max_attempts": int(extra.get("max_attempts") or 2),
            "label": extra.get("label") or step_id,
        }
        if extra.get("done_if"):
            step["done_if"] = extra["done_if"]
        plan.append(step)

    if "status" in types:
        add("query_status", "query_status", {"view": "summary"}, label="查询状态")
        return plan

    if "diagnose" in types:
        add("diagnose", "diagnose_failure", {}, label="诊断失败")
        return plan

    if "chat" in types and len(types) == 1:
        return plan

    if "full_generate" in types:
        add("query_status", "query_status", {"view": "summary"}, label="查询状态")
        add(
            "run_remaining",
            "run_stage",
            {"command": ""},
            depends_on=["query_status"],
            label="按流水线推进",
        )
        add(
            "export",
            "build_export",
            {"targets": ["md", "docx", "format"]},
            depends_on=["run_remaining"],
            label="生成终稿",
            done_if={"check": "artifact_exists", "path": "outputs/final.docx"},
        )
        return plan

    if "fix_compliance" in types:
        add("analyze_compliance", "analyze_compliance", {"sync": True}, label="分析合规问题")
        add(
            "fix_compliance",
            "fix_compliance",
            {"confirm_execute": True, "sync": True},
            depends_on=["analyze_compliance"],
            label="修复合规问题",
        )

    if "fix_coverage" in types:
        add("analyze_coverage", "analyze_coverage", {"rebuild": True, "max_chapters": 5}, label="查询评分覆盖")
        add(
            "fix_coverage",
            "fix_coverage",
            {"max_chapters": 5, "confirm_execute": True, "max_rounds": 2},
            depends_on=["analyze_coverage"],
            run_if={"score_coverage_below": 0.95},
            label="定向补齐评分点",
            max_attempts=2,
        )
        add(
            "recheck_coverage",
            "analyze_coverage",
            {"rebuild": True, "max_chapters": 5},
            depends_on=["fix_coverage"],
            label="重算评分覆盖",
            done_if={"check": "score_coverage_min", "ratio": 0.95},
        )

    if "fix_chapter" in types and chapter_ids:
        add(
            "rewrite_chapters",
            "rewrite_chapters",
            {"chapter_ids": chapter_ids},
            label=f"改写章节 {','.join(chapter_ids[:8])}",
        )
        add(
            "review_chapters",
            "review_chapters",
            {"chapter_ids": chapter_ids},
            depends_on=["rewrite_chapters"],
            label="章节复审",
        )

    if "export" in types or any(t in types for t in ("fix_coverage", "fix_compliance", "fix_chapter", "full_generate")):
        # always end with export when export-like success criteria present
        deps = [s["step_id"] for s in plan[-1:]] if plan else []
        if not any(s.get("tool") == "build_export" for s in plan):
            if constraints.get("require_compliance_before_export", True):
                add(
                    "export_preflight",
                    "export_preflight",
                    {},
                    depends_on=deps,
                    label="出稿前检查",
                )
                deps = ["export_preflight"]
            add(
                "build_export",
                "build_export",
                {"targets": ["md", "docx", "format"]},
                depends_on=deps,
                label="生成 Word",
                done_if={"check": "artifact_exists", "path": "outputs/final.docx"},
            )

    if not plan and objectives:
        add("query_status", "query_status", {"view": "summary"}, label="查询状态")
    return plan


def create_goal(
    root: Path | None,
    *,
    raw_user_goal: str,
    objectives: list[dict[str, Any]] | None = None,
    success_criteria: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
    plan: list[dict[str, Any]] | None = None,
    confirmation_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    objectives = objectives or []
    constraints = constraints or {
        "allow_skip_compliance": False,
        "require_human_on_critical": True,
        "require_compliance_before_export": True,
        "block_on_missing_materials": True,
        "material_placeholder_on_missing": True,
    }
    chapter_ids = list(constraints.get("chapter_ids") or [])
    if plan is None:
        plan = build_plan_for_objectives(objectives, constraints=constraints, chapter_ids=chapter_ids)
    goal = {
        "goal_id": new_goal_id(),
        "raw_user_goal": raw_user_goal,
        "normalized_objectives": objectives,
        "constraints": constraints,
        "success_criteria": success_criteria or [],
        "plan": plan,
        "current_plan_index": 0,
        "status": "pending",
        "blocked_reason": "",
        "confirmation_scope": confirmation_scope or {},
        "progress": {},
        "created_at": _now(),
        "updated_at": _now(),
        "criteria_results": [],
        "all_criteria_ok": False,
    }
    save_goal(root, goal)
    return reevaluate_goal(root, goal)


def infer_goal_from_message(message: str) -> dict[str, Any]:
    """Heuristic goal template from user text (no LLM)."""
    text = message or ""
    chapter_ids = re.findall(r"\d+(?:\.\d+)*", text)
    chapter_ids = [c for c in chapter_ids if len(c) <= 8][:20]

    objectives: list[dict[str, Any]] = []
    criteria: list[dict[str, Any]] = []
    constraints: dict[str, Any] = {
        "allow_skip_compliance": False,
        "require_human_on_critical": True,
        "require_compliance_before_export": True,
        "block_on_missing_materials": True,
        "material_placeholder_on_missing": True,
    }

    wants_export = any(k in text for k in ("出 Word", "生成 Word", "导出", "final.docx", "出稿", "生成docx", "生成 docx"))
    wants_rewrite = any(k in text for k in ("改第", "重写", "改稿", "只修", "定向"))
    wants_full = any(k in text for k in ("一键生成", "全部跑完", "全量生成", "从头生成"))
    wants_status = any(k in text for k in ("状态", "进度", "诊断", "失败"))
    wants_coverage = any(k in text for k in ("覆盖率", "评分点", "未覆盖", "补齐评分", "覆盖缺口", "补齐所有可自动", "补齐评分点"))
    wants_compliance = any(k in text for k in ("合规", "废标", "blocking"))
    no_price = any(k in text for k in ("不要改报价", "禁止修改报价", "不改价格", "跳过报价"))
    tech_only = any(k in text for k in ("只处理技术", "仅技术方案", "只改技术"))

    if no_price:
        constraints["forbid_price_chapters"] = True
    if tech_only:
        constraints["tech_only"] = True
    if any(k in text for k in ("缺材料", "占位", "材料不足")):
        constraints["material_placeholder_on_missing"] = True

    if wants_compliance:
        objectives.append({"type": "fix_compliance"})
        criteria.append({"check": "no_stale", "paths": ["workspace/compliance_report.json"]})
        criteria.append({"check": "no_open_blocks"})
        if wants_export:
            objectives.append({"type": "export", "targets": ["md", "docx"]})
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ]
            )
    elif wants_coverage:
        objectives.append({"type": "fix_coverage"})
        criteria.append({"check": "score_coverage_min", "ratio": 0.95})
        if wants_export or any(k in text for k in ("补齐", "并出", "然后出", "生成最终")):
            objectives.append({"type": "export", "targets": ["md", "docx"]})
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ]
            )
    elif wants_full:
        objectives.append({"type": "full_generate"})
        criteria.extend(
            [
                {"check": "artifact_exists", "path": "outputs/final.md"},
                {"check": "artifact_exists", "path": "outputs/final.docx"},
                {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
            ]
        )
    elif wants_rewrite and chapter_ids:
        objectives.append({"type": "fix_chapter", "chapter_ids": chapter_ids})
        constraints["chapter_ids"] = chapter_ids
        criteria.append({"check": "chapters_written", "chapter_ids": chapter_ids})
        if wants_export:
            objectives.append({"type": "export", "targets": ["md", "docx"]})
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ]
            )
    elif wants_export:
        objectives.append({"type": "export", "targets": ["md", "docx"]})
        criteria.extend(
            [
                {"check": "artifact_exists", "path": "outputs/final.md"},
                {"check": "artifact_exists", "path": "outputs/final.docx"},
                {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
            ]
        )
        # simple export intent: prefer build_export as first actionable step
        plan = [
            {
                "step_id": "build_export",
                "tool": "build_export",
                "args": {"targets": ["md", "docx", "format"]},
                "depends_on": [],
                "run_if": {},
                "status": "pending",
                "attempts": 0,
                "max_attempts": 2,
                "label": "生成 Word",
            }
        ]
        return {
            "objectives": objectives,
            "success_criteria": criteria,
            "constraints": constraints,
            "chapter_ids": chapter_ids,
            "plan": plan,
        }
    elif wants_status:
        if any(k in text for k in ("诊断", "失败", "错误", "为啥挂")):
            objectives.append({"type": "diagnose"})
            # diagnose has no success criteria — running the tool is the goal
            criteria = []
        else:
            objectives.append({"type": "status"})
            criteria = []
    else:
        objectives.append({"type": "chat"})
        criteria = []

    plan = build_plan_for_objectives(objectives, constraints=constraints, chapter_ids=chapter_ids)
    return {
        "objectives": objectives,
        "success_criteria": criteria,
        "constraints": constraints,
        "chapter_ids": chapter_ids,
        "plan": plan,
    }


def goal_summary(goal: dict[str, Any] | None) -> str:
    if not goal:
        return "无活动目标"
    status = goal.get("status")
    ok = goal.get("all_criteria_ok")
    results = goal.get("criteria_results") or []
    failed = [r for r in results if not r.get("ok")]
    progress = goal.get("progress") or {}
    base = f"目标 {goal.get('goal_id')} 状态={status} criteria_ok={ok}"
    if progress:
        if progress.get("plan_total"):
            base += f" 计划={progress.get('plan_done')}/{progress.get('plan_total')}"
        if progress.get("criteria_total"):
            base += f" 条件={progress.get('criteria_ok')}/{progress.get('criteria_total')}"
    if goal.get("blocked_reason"):
        base += f" 阻断={goal.get('blocked_reason')}"
    if failed:
        details = "; ".join(f"{r.get('check')}:{r.get('detail')}" for r in failed[:5])
        base += f" 未达成: {details}"
    return base


def user_facing_decision_summary(decision: dict[str, Any]) -> str:
    """Short human-readable decision line for workbench."""
    thought = str(decision.get("thought_summary") or "").strip()
    tool = str(decision.get("selected_tool") or decision.get("tool") or "").strip()
    if thought and thought != "（无摘要）":
        return thought
    if tool:
        return f"执行 {tool}"
    return "观察当前状态"
