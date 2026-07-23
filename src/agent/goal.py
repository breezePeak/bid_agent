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


def _goal_control_store(root: Path):
    from control_plane import ControlStore, WorkspaceContext

    return ControlStore(WorkspaceContext.resolve(root.parent, root.name))


def load_goal(root: Path | None = None) -> dict[str, Any] | None:
    root = (root or project_root()).resolve()
    store = _goal_control_store(root)
    return store.goal_state()


def save_goal(root: Path | None, goal: dict[str, Any]) -> Path:
    root = (root or project_root()).resolve()
    goal = dict(goal)
    goal["updated_at"] = _now()
    _goal_control_store(root).upsert_goal_state(goal)
    return goal_path(root)




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


_RETRYABLE_ERROR_CODES = frozenset(
    {
        "timeout",
        "rate_limit",
        "temporary_network",
        "runner_failed",
    }
)
_NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "invalid_args",
        "unknown_tool",
        "blocked_policy",
        "missing_required_artifact",
        "missing_requires",
        "human_confirmation_required",
        "gate_blocked",
    }
)


def is_retryable_error(*, error_code: str = "", retryable: bool | None = None) -> bool:
    code = str(error_code or "").strip()
    if code in _NON_RETRYABLE_ERROR_CODES:
        return False
    if retryable is False:
        return False
    if retryable is True:
        return True
    return code in _RETRYABLE_ERROR_CODES


def evaluate_criteria(root: Path, criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in criteria:
        check = str(item.get("check") or "")
        # PR-8: alias export_preflight_ok → export_preflight
        if check == "export_preflight_ok":
            check = "export_preflight"
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
        elif check in {"export_preflight", "export_preflight_ok"}:
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


def handle_plan_step_result(
    root: Path | None,
    goal: dict[str, Any] | None,
    step_id: str,
    *,
    ok: bool,
    error: str = "",
    error_code: str = "",
    retryable: bool | None = None,
    outcome: str = "",
) -> dict[str, Any]:
    """Record plan step outcome with retry policy (PR-4).

    Layer-2: Tool outcome drives step status. Tool success ≠ Goal success.
    - completed / partial_completed → step done (goal still needs reevaluate)
    - blocked / waiting_human → step blocked (goal may become blocked_human)
    - failed → retry or step failed
    """
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    failed_hard = False
    step_blocked = False
    # Normalize outcome from ok when not provided
    oc = str(outcome or "").strip()
    if oc not in {"completed", "partial_completed", "blocked", "failed", "waiting_human"}:
        oc = "completed" if ok else "failed"
    for step in plan:
        if not isinstance(step, dict):
            continue
        if str(step.get("step_id")) != str(step_id):
            continue
        attempts = int(step.get("attempts") or 0)
        # running already incremented attempts; if still pending, count this try
        if str(step.get("status") or "") != "running":
            attempts += 1
            step["attempts"] = attempts
        max_attempts = max(1, int(step.get("max_attempts") or 2))
        step["last_outcome"] = oc
        if oc in {"completed", "partial_completed"}:
            step["status"] = "done"
            step["completed_at"] = _now()
            step["last_error"] = ""
        elif oc in {"blocked", "waiting_human"}:
            step["status"] = "blocked"
            step["last_error"] = (error or error_code or oc)[:500]
            step["last_failed_at"] = _now()
            step["last_error_code"] = str(error_code or oc)
            step_blocked = True
        else:
            # failed
            step["last_error"] = (error or error_code or "failed")[:500]
            step["last_failed_at"] = _now()
            step["last_error_code"] = str(error_code or "")
            can_retry = attempts < max_attempts and is_retryable_error(
                error_code=error_code, retryable=retryable
            )
            if can_retry:
                step["status"] = "pending"
            else:
                step["status"] = "failed"
                failed_hard = True
        break
    goal["plan"] = plan
    if failed_hard:
        old_status = str(goal.get("status") or "pending")
        if validate_goal_transition(old_status, "failed", context={"reason": "plan_step_failed"}):
            goal["status"] = "failed"
            goal["blocked_reason"] = f"计划步骤失败: {step_id}"
            goal["failed_step_id"] = str(step_id)
            goal["recommended_actions"] = [
                "查看失败详情",
                "人工重试该步骤",
                "修改配置后恢复",
            ]
    elif step_blocked:
        old_status = str(goal.get("status") or "pending")
        if old_status not in {"cancelled", "failed", "budget_exceeded", "blocked_policy"}:
            # Do not mark goal succeeded; leave for reevaluate / material path
            if validate_goal_transition(
                old_status, "blocked_human", context={"reason": error or "step_blocked"}
            ):
                goal["status"] = "blocked_human"
                goal["blocked_reason"] = (error or f"计划步骤阻断: {step_id}")[:500]
    goal = refresh_plan_statuses(root, goal)
    save_goal(root, goal)
    return goal


def goal_succeeded(goal: dict[str, Any] | None) -> bool:
    if not goal:
        return False
    return str(goal.get("status")) == "succeeded" or bool(goal.get("all_criteria_ok"))


def plan_has_open_steps(goal: dict[str, Any] | None) -> bool:
    if not goal or not isinstance(goal.get("plan"), list):
        return False
    for step in goal.get("plan") or []:
        if not isinstance(step, dict):
            continue
        st = str(step.get("status") or "pending")
        if st in {"pending", "running", "blocked"}:
            return True
    return False


def runtime_blocks_success(root: Path | None, goal: dict[str, Any] | None = None) -> str:
    """Extra gates so criteria-ok alone cannot declare success while work is live.

    Plan open steps are NOT a hard block: criteria are the success definition;
    plan is only an execution guide (artifacts may already satisfy criteria).
    """
    root = root or project_root()
    # open quality blockers — never declare success while pipeline is gate-blocked
    try:
        from agent.issues import open_block_issues

        blocks = open_block_issues(root) or []
        if blocks:
            return f"仍有 {len(blocks)} 个开放阻断问题"
    except Exception as exc:
        return f"质量 Issue 状态读取失败，禁止宣告 Goal 成功: {exc}"
    # active chapter workers
    try:
        from agent.activity import has_active_workers

        if has_active_workers(root):
            return "章节工位仍有在岗/排队任务"
    except Exception as exc:
        return f"AgentActivity 状态读取失败，禁止宣告 Goal 成功: {exc}"
    # active repair job
    try:
        from agent.repair_jobs import ACTIVE_REPAIR_STATUSES, load_repair_job

        job = load_repair_job(root)
        if str(job.get("status") or "") in ACTIVE_REPAIR_STATUSES:
            return f"最小修复任务进行中（{job.get('status')}）"
    except Exception as exc:
        return f"RepairJob 状态读取失败，禁止宣告 Goal 成功: {exc}"
    # hard materials for actionable goals
    if goal:
        objectives = [
            str(o.get("type") or "")
            for o in (goal.get("normalized_objectives") or [])
            if isinstance(o, dict)
        ]
        actionable = any(
            t in objectives
            for t in ("fix_coverage", "fix_compliance", "export", "full_generate", "fix_chapter")
        )
        constraints = goal.get("constraints") if isinstance(goal.get("constraints"), dict) else {}
        if actionable and constraints.get("block_on_missing_materials", True):
            reason = detect_human_block(root, goal)
            if reason:
                return reason
    return ""


def validate_goal_transition(
    old_status: str,
    new_status: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Guard illegal Goal transitions. Tool success alone cannot force succeeded.

    Layer-3 protection:
    - in_progress → succeeded only when goal_success_evaluation is True
    - blocked_human → succeeded only when materials/issues revalidated
    """
    old = str(old_status or "pending")
    new = str(new_status or "")
    ctx = context if isinstance(context, dict) else {}
    if old == new:
        return True
    if new not in (
        GOAL_TERMINAL
        | GOAL_ACTIVE
        | frozenset({"pending", "in_progress", "awaiting_confirmation"})
    ):
        return False
    # Always allow demotion / non-success terminals from active states
    if new in {"failed", "cancelled", "budget_exceeded", "blocked_policy", "blocked_human"}:
        return True
    if new in GOAL_ACTIVE or new == "pending":
        return True
    # Succeeded requires explicit evaluation
    if new == "succeeded":
        if old in {"cancelled", "failed", "budget_exceeded"}:
            return False
        if not bool(ctx.get("goal_success_evaluation")):
            return False
        if old == "blocked_human":
            # Must re-validate materials/issues
            if ctx.get("materials_revalidated") is False:
                return False
            if ctx.get("issues_revalidated") is False:
                return False
        return True
    return True


def evaluate_goal_success(
    root: Path | None,
    goal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strict Layer-3 success evaluation. Never trusts Tool.ok alone.

    Succeeds only when:
    - all success_criteria pass (for criteria mode), OR plan-only modes meet plan+gates
    - no open block issues
    - no mandatory material missing (actionable goals)
    - required plan steps are not failed (blocked steps prevent success)
    """
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        return {
            "ok": False,
            "reason": "no_goal",
            "all_criteria_ok": False,
            "runtime_block": "no_goal",
            "plan_all_done": False,
        }
    criteria = goal.get("success_criteria") if isinstance(goal.get("success_criteria"), list) else []
    results = evaluate_criteria(root, criteria)
    all_ok = all(bool(r.get("ok")) for r in results) if results else False
    completion_mode = str(goal.get("completion_mode") or "criteria")
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    plan_steps = [s for s in plan if isinstance(s, dict)]
    # Non-empty plan: all steps done/skipped. Empty plan is NOT auto-done
    # (tool_once empty plan succeeds only after a tool executed — see below).
    plan_all_done = bool(plan_steps) and all(
        str(s.get("status") or "pending") in {"done", "skipped"} for s in plan_steps
    )
    plan_has_failed = any(str(s.get("status") or "") == "failed" for s in plan_steps)
    plan_has_blocked = any(str(s.get("status") or "") == "blocked" for s in plan_steps)
    runtime_block = runtime_blocks_success(root, goal)

    objectives = [
        str(o.get("type") or "")
        for o in (goal.get("normalized_objectives") or [])
        if isinstance(o, dict)
    ]
    types = set(objectives)
    # status/diagnose/chat goals report issues; open blocks must not block their completion
    diagnostic_only = (not types) or types.issubset({"status", "diagnose", "chat"})
    actionable = any(
        t in types
        for t in ("fix_coverage", "fix_compliance", "export", "full_generate", "fix_chapter")
    )

    open_blocks_reason = ""
    blocks: list[Any] = []
    try:
        from agent.issues import open_block_issues

        blocks = open_block_issues(root) or []
        if blocks and not diagnostic_only:
            open_blocks_reason = f"仍有 {len(blocks)} 个开放阻断问题"
    except Exception:
        blocks = []

    material_reason = ""
    constraints = goal.get("constraints") if isinstance(goal.get("constraints"), dict) else {}
    if actionable and constraints.get("block_on_missing_materials", True):
        material_reason = detect_human_block(root, goal) or ""

    # runtime_blocks_success already covers open blocks / workers / materials
    # For diagnostic-only goals, ignore open-block portion of runtime_block
    effective_runtime_block = runtime_block
    if diagnostic_only and runtime_block and "开放阻断" in runtime_block:
        effective_runtime_block = ""

    tool_once_executed = bool(goal.get("tool_once_executed")) or int(
        (goal.get("progress") or {}).get("tools_executed") or 0
    ) > 0

    ok = False
    reason = ""
    if plan_has_failed and not diagnostic_only:
        ok = False
        reason = "plan_step_failed"
    elif plan_has_blocked and not diagnostic_only:
        ok = False
        reason = "plan_step_blocked"
    elif open_blocks_reason:
        ok = False
        reason = open_blocks_reason
    elif material_reason:
        ok = False
        reason = material_reason
    elif effective_runtime_block and not diagnostic_only:
        ok = False
        reason = effective_runtime_block
    elif completion_mode == "criteria":
        # Empty criteria: never auto-succeed on tool alone
        if not criteria:
            ok = False
            reason = "no_success_criteria"
        elif all_ok and not open_blocks_reason and not material_reason and not effective_runtime_block:
            ok = True
            reason = "criteria_met"
        else:
            ok = False
            reason = (
                open_blocks_reason
                or material_reason
                or effective_runtime_block
                or "criteria_not_met"
            )
    elif completion_mode in {"plan_completed", "tool_once"}:
        if plan_steps:
            steps_ok = plan_all_done
        else:
            # Empty plan: only after a real tool ran (status/diagnose one-shot).
            # Prevents create_goal → immediate succeeded before any tool.
            steps_ok = completion_mode == "tool_once" and tool_once_executed
        if steps_ok and not open_blocks_reason and not material_reason:
            if diagnostic_only or not effective_runtime_block:
                ok = True
                reason = "plan_completed"
            else:
                ok = False
                reason = effective_runtime_block
        else:
            ok = False
            reason = "plan_not_complete" if not steps_ok else (open_blocks_reason or material_reason)
    else:
        ok = False
        reason = "unknown_completion_mode"

    return {
        "ok": bool(ok),
        "reason": reason,
        "all_criteria_ok": bool(all_ok),
        "criteria_results": results,
        "runtime_block": runtime_block or open_blocks_reason or material_reason,
        "plan_all_done": plan_all_done,
        "plan_has_failed": plan_has_failed,
        "plan_has_blocked": plan_has_blocked,
        "open_block_count": len(blocks) if isinstance(blocks, list) else 0,
        "material_block": material_reason,
        "completion_mode": completion_mode,
        "goal_success_evaluation": bool(ok),
        "materials_revalidated": True,
        "issues_revalidated": True,
    }


def set_goal_status(
    root: Path | None,
    status: str,
    *,
    blocked_reason: str = "",
    goal: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    old_status = str(goal.get("status") or "pending")
    new_status = str(status or "")
    ctx = dict(context or {})
    # Succeeded must go through evaluate_goal_success unless caller already proved it
    if new_status == "succeeded" and not ctx.get("goal_success_evaluation"):
        evaluation = evaluate_goal_success(root, goal)
        ctx.update(evaluation)
        if not evaluation.get("ok"):
            # Refuse illegal promotion; keep / set non-success status
            if evaluation.get("material_block") or (
                "材料" in str(evaluation.get("reason") or "")
                or "缺少" in str(evaluation.get("reason") or "")
            ):
                new_status = "blocked_human"
                blocked_reason = blocked_reason or str(evaluation.get("reason") or "")
            elif evaluation.get("open_block_count"):
                new_status = "in_progress"
                blocked_reason = blocked_reason or str(evaluation.get("reason") or "")
            elif evaluation.get("plan_has_failed"):
                new_status = "failed"
                blocked_reason = blocked_reason or str(evaluation.get("reason") or "")
            else:
                new_status = "in_progress" if old_status != "blocked_human" else old_status
                blocked_reason = blocked_reason or str(evaluation.get("reason") or "")
            goal["status"] = new_status
            if blocked_reason:
                goal["blocked_reason"] = blocked_reason
            goal["last_success_evaluation"] = evaluation
            save_goal(root, goal)
            return goal
    if not validate_goal_transition(old_status, new_status, context=ctx):
        # Illegal transition: do not apply
        return goal
    goal["status"] = new_status
    if blocked_reason:
        goal["blocked_reason"] = blocked_reason
    elif new_status not in {"blocked_human", "blocked_policy", "budget_exceeded", "failed"}:
        goal["blocked_reason"] = ""
    save_goal(root, goal)
    return goal


def grant_confirmation(
    root: Path | None,
    tools: list[str] | str | None = None,
    *,
    all_mutations: bool = False,
) -> dict[str, Any]:
    """Record user confirmation. Prefer tool_scope; all_mutations only when explicitly granted (PR-1)."""
    root = root or project_root()
    goal = load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    scope = dict(goal.get("confirmation_scope") or {})
    allowed = set(scope.get("tools") or [])
    if tools:
        if isinstance(tools, str):
            tools = [tools]
        allowed.update(str(t) for t in tools if str(t).strip())
        scope["mode"] = "tool_scope"
        # explicit tool list never implies all_mutations
        if not all_mutations:
            scope["all_mutations"] = False
    if all_mutations and not tools:
        scope["all_mutations"] = True
        scope["mode"] = "all_mutations"
    elif all_mutations and tools:
        # both provided: still prefer scoped tools; do not expand to all
        scope["all_mutations"] = False
        scope["mode"] = "tool_scope"
    scope["tools"] = sorted(allowed)
    scope["confirmed_at"] = _now()
    goal["confirmation_scope"] = scope
    if str(goal.get("status")) == "awaiting_confirmation":
        goal["status"] = "in_progress"
    save_goal(root, goal)
    return goal


def confirmation_allows(
    goal: dict[str, Any] | None,
    tool: str,
    *,
    user_confirmed: bool = False,
    confirmed_tools: list[str] | None = None,
) -> bool:
    """Check whether tool is allowed under confirmation scope.

    user_confirmed alone does NOT grant all tools (PR-1). Only:
    - tool in confirmed_tools this turn
    - tool in goal.confirmation_scope.tools
    - scope.all_mutations is True
    """
    tool_name = str(tool or "").strip()
    if not tool_name:
        return False
    if confirmed_tools:
        if tool_name in {str(t) for t in confirmed_tools}:
            return True
    if not goal:
        # legacy broad confirm only when explicitly all-mutations path (no tools list)
        return bool(user_confirmed and not confirmed_tools)
    scope = goal.get("confirmation_scope") if isinstance(goal.get("confirmation_scope"), dict) else {}
    if scope.get("all_mutations"):
        return True
    tools = scope.get("tools") if isinstance(scope.get("tools"), list) else []
    if tool_name in {str(t) for t in tools}:
        return True
    # user_confirmed without tool scope: only allow when all_mutations already set
    return False


def detect_human_block(root: Path, goal: dict[str, Any] | None = None) -> str:
    """Detect materials / fatal issues that require human intervention."""
    try:
        from agent.snapshot import build_snapshot, human_blocking_reason

        snap = build_snapshot(root, goal=goal, for_llm=False)
        return human_blocking_reason(snap, goal)
    except Exception as exc:
        return f"人工阻断状态读取失败，禁止继续: {exc}"


def resume_goal_after_materials(
    root: Path | None,
    *,
    note: str = "",
    item_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Clear blocked_human and re-enter in_progress after materials upload (PR-7).

    Uses one-shot resume_context instead of permanently disabling material blocks.
    """
    root = root or project_root()
    goal = load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    if str(goal.get("status")) == "blocked_human":
        prev_reason = str(goal.get("blocked_reason") or "")
        goal["status"] = "in_progress"
        goal["blocked_reason"] = ""
        goal["resume_note"] = note or "materials_updated"
        goal["resumed_at"] = _now()
        goal["resume_context"] = {
            "reason": "material_verified",
            "item_ids": list(item_ids or []),
            "skip_same_snapshot_once": True,
            "prev_blocked_reason": prev_reason[:500],
            "created_at": _now(),
        }
        # keep block_on_missing_materials True so other missing materials re-block
        constraints = dict(goal.get("constraints") or {})
        constraints["block_on_missing_materials"] = True
        goal["constraints"] = constraints
    goal = refresh_plan_statuses(root, goal)
    save_goal(root, goal)
    return reevaluate_goal(root, goal)


def archive_goal(root: Path | None, goal: dict[str, Any] | None = None) -> Path | None:
    """Archive current goal to workspace/agent/goals/<goal_id>.json (PR-6)."""
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        return None
    gid = str(goal.get("goal_id") or new_goal_id())
    archive_dir = root / "workspace" / "agent" / "goals"
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{gid}.json"
    payload = dict(goal)
    payload["archived_at"] = _now()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def explicit_resume_intent(message: str) -> bool:
    """True only when user explicitly asks to resume the previous goal (PR-6)."""
    text = (message or "").strip()
    if not text:
        return False
    keywords = (
        "继续上一个任务",
        "恢复刚才的任务",
        "继续执行剩余计划",
        "材料已补，继续",
        "材料已补继续",
        "材料已上传",
        "补料完成",
        "确认执行",
        "继续上一个",
        "恢复任务",
        "继续剩余",
        "材料齐备",
        "继续整个流程",
        "继续整个",
        "继续流程",
        "继续进行",
        "继续跑",
        "接着跑",
        "一键跑完",
        "跑完剩余",
    )
    if any(k in text for k in keywords):
        return True
    # short resume cues when not clearly a new intent
    if text in {"继续", "恢复", "接着做", "接着跑", "继续执行", "继续啊", "继续吧", "继续进行啊"}:
        return True
    # "继续…" / "继续xxx" short free-form (avoid matching unrelated long questions)
    if len(text) <= 16 and text.startswith("继续"):
        return True
    return False


def completion_mode_for_objectives(objectives: list[dict[str, Any]] | None) -> str:
    types = {
        str(o.get("type") or "")
        for o in (objectives or [])
        if isinstance(o, dict)
    }
    if "status" in types or "diagnose" in types:
        return "plan_completed"
    if types == {"chat"} or not types:
        return "tool_once"
    return "criteria"


def reevaluate_goal(root: Path | None, goal: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    criteria = goal.get("success_criteria") if isinstance(goal.get("success_criteria"), list) else []
    # normalize legacy check names on save path
    normalized_criteria: list[dict[str, Any]] = []
    for item in criteria:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if str(row.get("check") or "") == "export_preflight_ok":
            row["check"] = "export_preflight"
        normalized_criteria.append(row)
    if normalized_criteria != criteria:
        goal["success_criteria"] = normalized_criteria
        criteria = normalized_criteria

    # refresh plan progress first
    if isinstance(goal.get("plan"), list) and goal.get("plan"):
        goal = refresh_plan_statuses(root, goal)

    results = evaluate_criteria(root, criteria)
    all_ok = all(bool(r.get("ok")) for r in results) if results else False
    old_status = str(goal.get("status") or "pending")
    status = old_status
    completion_mode = str(goal.get("completion_mode") or "criteria")

    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    plan_all_done = bool(plan) and all(
        str(s.get("status") or "pending") in {"done", "skipped"}
        for s in plan
        if isinstance(s, dict)
    )
    plan_has_failed = any(
        isinstance(s, dict) and str(s.get("status") or "") == "failed" for s in plan
    )
    plan_has_blocked = any(
        isinstance(s, dict) and str(s.get("status") or "") == "blocked" for s in plan
    )

    runtime_block = runtime_blocks_success(root, goal)
    success_eval = evaluate_goal_success(root, goal)

    # PR-7: one-shot resume_context — re-evaluate materials and clear after one pass
    resume_ctx = goal.get("resume_context") if isinstance(goal.get("resume_context"), dict) else None
    skip_same_once = bool(resume_ctx and resume_ctx.get("skip_same_snapshot_once"))
    prev_block_reason = str((resume_ctx or {}).get("prev_blocked_reason") or "")

    if status in {"cancelled", "failed", "budget_exceeded", "blocked_policy"}:
        pass
    elif plan_has_failed and completion_mode in {"plan_completed", "criteria", "tool_once"}:
        status = "failed"
        failed_steps = [
            str(s.get("step_id") or "")
            for s in plan
            if isinstance(s, dict) and str(s.get("status") or "") == "failed"
        ]
        goal["blocked_reason"] = f"计划步骤失败: {','.join(failed_steps[:5])}"
        if failed_steps:
            goal["failed_step_id"] = failed_steps[0]
    elif plan_has_blocked:
        # Material / quality block on a step — not success
        block_reason = (
            str(success_eval.get("material_block") or "")
            or str(success_eval.get("runtime_block") or "")
            or "计划步骤被阻断"
        )
        status = "blocked_human"
        goal["blocked_reason"] = block_reason
    elif success_eval.get("ok") and validate_goal_transition(
        old_status,
        "succeeded",
        context=success_eval,
    ):
        # Only path to succeeded: strict evaluate_goal_success
        status = "succeeded"
        goal["blocked_reason"] = ""
        all_ok = True
    elif completion_mode == "criteria" and all_ok and runtime_block:
        if "材料" in runtime_block or "缺少" in runtime_block:
            status = "blocked_human"
            goal["blocked_reason"] = runtime_block
        else:
            status = "in_progress"
            goal["blocked_reason"] = runtime_block
    elif status == "awaiting_confirmation":
        pass
    elif status == "blocked_human":
        mat = detect_human_block(root, goal)
        if not mat and not success_eval.get("open_block_count"):
            # May leave blocked_human only after materials cleared; success still needs eval
            if success_eval.get("ok") and validate_goal_transition(
                "blocked_human", "succeeded", context=success_eval
            ):
                status = "succeeded"
                goal["blocked_reason"] = ""
            else:
                status = "in_progress"
                goal["blocked_reason"] = runtime_block if runtime_block else ""
        else:
            goal["blocked_reason"] = mat or str(success_eval.get("reason") or goal.get("blocked_reason") or "")
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
        if reason and actionable and (completion_mode != "plan_completed"):
            constraints = goal.get("constraints") if isinstance(goal.get("constraints"), dict) else {}
            if constraints.get("block_on_missing_materials", True):
                if skip_same_once and reason == prev_block_reason:
                    # skip only the exact previous snapshot once
                    status = "in_progress"
                    goal["blocked_reason"] = ""
                else:
                    status = "blocked_human"
                    goal["blocked_reason"] = reason
            else:
                status = "in_progress"
        else:
            status = "in_progress"
            if runtime_block:
                goal["blocked_reason"] = runtime_block
            elif success_eval.get("open_block_count"):
                goal["blocked_reason"] = str(success_eval.get("reason") or "")

    # clear one-shot resume context after reevaluation
    if resume_ctx is not None:
        goal.pop("resume_context", None)

    # Final guard: never keep succeeded if evaluation fails
    if status == "succeeded" and not success_eval.get("ok"):
        if success_eval.get("material_block") or "材料" in str(success_eval.get("reason") or ""):
            status = "blocked_human"
        elif success_eval.get("plan_has_failed"):
            status = "failed"
        else:
            status = "in_progress"
        goal["blocked_reason"] = str(success_eval.get("reason") or goal.get("blocked_reason") or "")

    if completion_mode in {"plan_completed", "tool_once"} and status == "succeeded":
        all_ok = True

    goal["status"] = status
    goal["criteria_results"] = results
    goal["last_success_evaluation"] = {
        "ok": bool(success_eval.get("ok")),
        "reason": success_eval.get("reason"),
        "open_block_count": success_eval.get("open_block_count"),
    }
    goal["all_criteria_ok"] = bool(
        (all_ok and not runtime_block and not success_eval.get("open_block_count"))
        if completion_mode == "criteria"
        else (status == "succeeded")
    )
    goal["criteria_ok_raw"] = all_ok if completion_mode == "criteria" else (status == "succeeded")
    if (runtime_block or success_eval.get("runtime_block")) and status != "succeeded":
        progress = dict(goal.get("progress") or {})
        progress["runtime_block"] = runtime_block or success_eval.get("runtime_block")
        goal["progress"] = progress
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
            "run_pipeline_remaining",
            {"resume": True},
            depends_on=["query_status"],
            label="按流水线推进剩余阶段",
            max_attempts=2,
        )
        add(
            "export_preflight",
            "export_preflight",
            {},
            depends_on=["run_remaining"],
            label="出稿前检查",
        )
        add(
            "export",
            "build_export",
            {"targets": ["md", "docx", "format"]},
            depends_on=["export_preflight"],
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
    completion_mode: str | None = None,
    archive_previous: bool = True,
) -> dict[str, Any]:
    root = root or project_root()
    if archive_previous:
        try:
            prev = load_goal(root)
            if prev and str(prev.get("goal_id") or ""):
                archive_goal(root, prev)
        except Exception:
            pass
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
    # normalize criteria check names
    criteria = []
    for item in success_criteria or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if str(row.get("check") or "") == "export_preflight_ok":
            row["check"] = "export_preflight"
        criteria.append(row)
    mode = completion_mode or completion_mode_for_objectives(objectives)
    goal = {
        "goal_id": new_goal_id(),
        "raw_user_goal": raw_user_goal,
        "normalized_objectives": objectives,
        "constraints": constraints,
        "success_criteria": criteria,
        "completion_mode": mode,
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
    wants_rewrite = any(k in text for k in ("改第", "重写", "改稿", "只修", "定向", "重新写", "再写一遍", "重新生成"))
    # Retry failed chapter writing — must win over bare "失败" status/diagnose heuristics
    wants_retry_failed_write = any(
        k in text
        for k in (
            "写作失败",
            "写失败",
            "章节失败",
            "失败的重新写",
            "失败章节",
            "重新写失败",
            "重写失败",
            "失败的重新",
            "将写作失败",
            "写作失败的",
            "重试写作",
            "补写失败",
        )
    )
    wants_full = any(k in text for k in ("一键生成", "全部跑完", "全量生成", "从头生成"))
    # Do NOT treat bare "失败" as status — it appears in "写作失败" repair requests
    wants_status = any(k in text for k in ("状态", "进度", "诊断", "当前进度", "怎么样了")) and not wants_retry_failed_write
    wants_diagnose = any(k in text for k in ("诊断", "为啥挂", "怎么挂的", "失败原因", "错误原因")) and not wants_retry_failed_write
    wants_coverage = any(k in text for k in ("覆盖率", "评分点", "未覆盖", "补齐评分", "覆盖缺口", "补齐所有可自动", "补齐评分点"))
    wants_compliance = any(k in text for k in ("合规", "废标", "blocking"))
    no_price = any(
        k in text
        for k in ("不要改报价", "不要修改报价", "禁止修改报价", "不改价格", "不修改报价", "跳过报价")
    )
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
    elif wants_retry_failed_write or (wants_rewrite and (chapter_ids or "失败" in text or "全部" in text)):
        # Resolve failed chapter ids from open block issues / activity when user did not list them
        resolved_ids = list(chapter_ids)
        if not resolved_ids or wants_retry_failed_write:
            try:
                from agent.issues import open_block_issues
                from utils import project_root as _pr

                for iss in open_block_issues(_pr()) or []:
                    if not isinstance(iss, dict):
                        continue
                    code = str(iss.get("code") or "")
                    if code not in {
                        "WRITE_CHAPTER_FAILED",
                        "CHAPTER_REVIEW_BLOCKER",
                        "MISSING_CHAPTER",
                        "CHAPTER_CONFLICT",
                    } and "写作失败" not in str(iss.get("title") or ""):
                        # still accept write/review related blocks with chapter targets
                        if str(iss.get("stage_id") or "") not in {
                            "write_chapters",
                            "review_fix_chapters",
                        }:
                            continue
                    for tid in iss.get("target_ids") or []:
                        cid = str(tid or "").strip()
                        if cid and cid not in resolved_ids:
                            resolved_ids.append(cid)
                    title = str(iss.get("title") or "")
                    for m in re.findall(r"章节\s*([0-9]+(?:\.[0-9]+)*)", title):
                        if m and m not in resolved_ids:
                            resolved_ids.append(m)
            except Exception:
                pass
            try:
                from agent.activity import failed_chapter_ids
                from utils import project_root as _pr2

                for cid in failed_chapter_ids(_pr2()) or []:
                    c = str(cid or "").strip()
                    if c and c not in resolved_ids:
                        resolved_ids.append(c)
            except Exception:
                pass
        if not resolved_ids:
            # Fall back to re-running write-all stage via full-ish plan
            objectives.append({"type": "full_generate"})
            criteria.extend(
                [
                    {"check": "no_open_blocks"},
                    {"check": "artifact_exists", "path": "workspace/chapters"},
                ]
            )
            plan = [
                {
                    "step_id": "write_chapters",
                    "tool": "write_chapters",
                    "args": {},
                    "depends_on": [],
                    "run_if": {},
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 2,
                    "label": "重试章节写作",
                }
            ]
            return {
                "objectives": objectives,
                "success_criteria": criteria,
                "constraints": constraints,
                "chapter_ids": [],
                "plan": plan,
                "completion_mode": "criteria",
            }
        objectives.append({"type": "fix_chapter", "chapter_ids": resolved_ids})
        constraints["chapter_ids"] = resolved_ids
        criteria.append({"check": "chapters_written", "chapter_ids": resolved_ids})
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
            "completion_mode": "criteria",
        }
    elif wants_diagnose:
        objectives.append({"type": "diagnose"})
        criteria = []
    elif wants_status:
        objectives.append({"type": "status"})
        criteria = []
    else:
        objectives.append({"type": "chat"})
        criteria = []

    plan = build_plan_for_objectives(objectives, constraints=constraints, chapter_ids=chapter_ids)
    mode = completion_mode_for_objectives(objectives)
    return {
        "objectives": objectives,
        "success_criteria": criteria,
        "constraints": constraints,
        "chapter_ids": chapter_ids,
        "plan": plan,
        "completion_mode": mode,
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
