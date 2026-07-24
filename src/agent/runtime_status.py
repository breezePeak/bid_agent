from __future__ import annotations

"""Unified runtime status view + consistency checks.

Product UI previously read 4+ independent stores with different lifecycles.
V2 owns Goal, Materials and Issues/Policy in control.db.

This module is the single aggregator for "what is the system doing now".
It does not replace domain stores; it composes them and surfaces conflicts.
"""

import json
from pathlib import Path
from typing import Any

from utils import project_root

# Canonical high-level product modes (UI should prefer these over raw store status)
PRODUCT_MODES = frozenset(
    {
        "idle",
        "agent_running",
        "pipeline_running",
        "repair_running",
        "awaiting_confirmation",
        "blocked_human",
        "blocked_policy",
        "budget_exceeded",
        "succeeded",
        "failed",
        "inconsistent",
    }
)


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _goal_slice(root: Path) -> dict[str, Any]:
    try:
        from agent.goal import load_goal

        goal = load_goal(root)
    except Exception:
        goal = None
    if not goal:
        return {"exists": False, "status": "", "goal_id": "", "all_criteria_ok": False}
    return {
        "exists": True,
        "goal_id": str(goal.get("goal_id") or ""),
        "status": str(goal.get("status") or ""),
        "all_criteria_ok": bool(goal.get("all_criteria_ok")),
        "blocked_reason": str(goal.get("blocked_reason") or "")[:300],
        "raw_user_goal": str(goal.get("raw_user_goal") or "")[:200],
        "runtime_block": str((goal.get("progress") or {}).get("runtime_block") or "")[:200],
        "plan_open": _plan_open_count(goal),
    }


def _plan_open_count(goal: dict[str, Any]) -> int:
    n = 0
    for step in goal.get("plan") or []:
        if isinstance(step, dict) and str(step.get("status") or "pending") in {
            "pending",
            "running",
            "blocked",
        }:
            n += 1
    return n


def _activity_slice(root: Path) -> dict[str, Any]:
    try:
        from agent.activity import activity_for_api, has_active_workers

        data = activity_for_api(root)
        workers_active = has_active_workers(root)
    except Exception:
        data = {"status": "idle", "agents": [], "summary": {}}
        workers_active = False
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return {
        "status": str(data.get("status") or "idle"),
        "phase": str(data.get("phase") or ""),
        "phase_label": str(data.get("phase_label") or ""),
        "workers_active": workers_active,
        "running": int(summary.get("running") or 0),
        "queued": int(summary.get("queued") or 0),
        "done": int(summary.get("done") or 0),
        "failed": int(summary.get("failed") or 0),
        "materials_deferred": int(data.get("materials_deferred") or 0),
        "coordinator_message": str((data.get("coordinator") or {}).get("message") or "")[:200],
    }


def _repair_slice(root: Path) -> dict[str, Any]:
    try:
        from agent.repair_jobs import ACTIVE_REPAIR_STATUSES, load_repair_job

        job = load_repair_job(root) or {}
        status = str(job.get("status") or "")
        return {
            "exists": bool(job.get("job_id")),
            "job_id": str(job.get("job_id") or ""),
            "status": status,
            "phase": str(job.get("phase") or ""),
            "active": status in ACTIVE_REPAIR_STATUSES,
            "message": str(job.get("message") or "")[:300],
            "resume_command": str(job.get("resume_command") or ""),
            "interrupted": str(job.get("phase") or "") == "interrupted"
            or "服务重启中断" in str(job.get("message") or ""),
        }
    except Exception:
        return {"exists": False, "status": "", "active": False}


def _pipeline_slice(root: Path) -> dict[str, Any]:
    run_state = _safe_json(root / "workspace" / "run_state.json")
    recovery = _safe_json(root / "workspace" / "recovery_state.json")
    control: dict[str, Any] = {}
    try:
        # pipeline_supervisor may not always be importable in unit tests
        from pipeline_supervisor import PipelineSupervisor

        # best-effort: many codepaths use a singleton; fall back to file
        control_path = root / "workspace" / "pipeline_control.json"
        control = _safe_json(control_path)
    except Exception:
        control = _safe_json(root / "workspace" / "pipeline_control.json")

    status = str(run_state.get("status") or control.get("status") or "idle")
    stage = str(run_state.get("stage") or control.get("current_stage") or "")
    busy = status in {
        "running",
        "recovering",
        "retrying",
        "pausing",
        "progress",
    } or str(control.get("status") or "") in {"running", "recovering", "retrying", "pausing"}
    return {
        "run_state_status": str(run_state.get("status") or ""),
        "run_state_stage": stage,
        "run_state_message": str(run_state.get("message") or "")[:300],
        "control_status": str(control.get("status") or ""),
        "busy": busy,
        "recovery_status": str(recovery.get("status") or recovery.get("phase") or ""),
    }


def _materials_slice(root: Path) -> dict[str, Any]:
    try:
        from control_plane import ControlStore, WorkspaceContext

        items = ControlStore(WorkspaceContext(root.name, root)).material_states()
        return {
            "exists": bool(items),
            "total": len(items),
            "deferred": sum(1 for item in items if str(item.get("response_status") or "deferred") == "deferred"),
            "ready": sum(1 for item in items if str(item.get("response_status") or "") == "ready"),
            "missing": sum(1 for item in items if str(item.get("evidence_status") or "missing") == "missing"),
        }
    except Exception:
        return {"exists": False, "total": 0, "deferred": 0, "ready": 0, "missing": 0}


def _issues_slice(root: Path) -> dict[str, Any]:
    try:
        from agent.issues import issues_summary, open_block_issues

        summary = issues_summary(root)
        blocks = open_block_issues(root)
        return {
            "open_blocks": len(blocks) if isinstance(blocks, list) else int(summary.get("open_blocks") or 0),
            "summary": summary if isinstance(summary, dict) else {},
        }
    except Exception:
        return {"open_blocks": 0, "summary": {}}


def detect_inconsistencies(slices: dict[str, Any]) -> list[dict[str, str]]:
    """Return list of {code, severity, message} for UI/API warnings."""
    warnings: list[dict[str, str]] = []
    goal = slices.get("goal") or {}
    activity = slices.get("activity") or {}
    repair = slices.get("repair") or {}
    pipeline = slices.get("pipeline") or {}
    materials = slices.get("materials") or {}
    issues = slices.get("issues") or {}

    g_status = str(goal.get("status") or "")
    workers = bool(activity.get("workers_active"))
    repair_active = bool(repair.get("active"))
    pipe_busy = bool(pipeline.get("busy"))
    deferred = int(materials.get("deferred") or 0)
    open_blocks = int(issues.get("open_blocks") or 0)

    if g_status == "succeeded" and workers:
        warnings.append(
            {
                "code": "goal_succeeded_workers_active",
                "severity": "error",
                "message": "Goal 已 succeeded，但章节工位仍有在岗/排队任务",
            }
        )
    if g_status == "succeeded" and repair_active:
        warnings.append(
            {
                "code": "goal_succeeded_repair_active",
                "severity": "error",
                "message": "Goal 已 succeeded，但最小修复任务仍在进行",
            }
        )
    if g_status == "succeeded" and pipe_busy:
        warnings.append(
            {
                "code": "goal_succeeded_pipeline_busy",
                "severity": "error",
                "message": f"Goal 已 succeeded，但流水线状态为 {pipeline.get('run_state_status') or pipeline.get('control_status')}",
            }
        )
    if g_status == "succeeded" and deferred > 0:
        warnings.append(
            {
                "code": "goal_succeeded_materials_deferred",
                "severity": "warn",
                "message": f"Goal 已 succeeded，但仍有 {deferred} 条待补材料",
            }
        )
    if g_status == "succeeded" and open_blocks > 0:
        warnings.append(
            {
                "code": "goal_succeeded_open_blocks",
                "severity": "error",
                "message": f"Goal 已 succeeded，但仍有 {open_blocks} 个开放阻断问题",
            }
        )
    if repair.get("interrupted") and workers:
        warnings.append(
            {
                "code": "repair_interrupted_workers_active",
                "severity": "error",
                "message": "最小修复已因重启中断，但工位仍显示在岗（可能是未清理的残留）",
            }
        )
    if repair.get("interrupted") and g_status == "succeeded":
        warnings.append(
            {
                "code": "repair_interrupted_goal_succeeded",
                "severity": "warn",
                "message": "最小修复中断，但 Goal 显示 succeeded——聊天历史可能仍显示旧的「目标已完成」",
            }
        )
    if g_status == "blocked_human" and not deferred and open_blocks == 0 and not goal.get("blocked_reason"):
        warnings.append(
            {
                "code": "blocked_human_without_reason",
                "severity": "warn",
                "message": "Goal 为 blocked_human，但未找到材料缺口或开放阻断",
            }
        )
    if workers and not pipe_busy and str(activity.get("status") or "") == "running":
        # possible ghost phase if pipeline idle
        if not repair_active:
            warnings.append(
                {
                    "code": "workers_without_pipeline",
                    "severity": "warn",
                    "message": "工位显示执行中，但流水线未标记 running（可能为残留 activity）",
                }
            )
    if pipe_busy and g_status in {"", "succeeded", "cancelled"} and not goal.get("exists"):
        warnings.append(
            {
                "code": "pipeline_without_goal",
                "severity": "info",
                "message": "流水线在运行，但无活动 Goal（可能是确定性 pipeline 路径）",
            }
        )
    return warnings


def derive_product_mode(slices: dict[str, Any], warnings: list[dict[str, str]]) -> str:
    """Single product-facing mode for badges and orchestration."""
    hard = [w for w in warnings if w.get("severity") == "error"]
    if hard:
        return "inconsistent"

    goal = slices.get("goal") or {}
    activity = slices.get("activity") or {}
    repair = slices.get("repair") or {}
    pipeline = slices.get("pipeline") or {}
    g_status = str(goal.get("status") or "")

    if repair.get("active"):
        return "repair_running"
    if str(repair.get("status") or "") == "awaiting_confirmation":
        return "awaiting_confirmation"
    if g_status == "awaiting_confirmation":
        return "awaiting_confirmation"
    if g_status == "blocked_human":
        return "blocked_human"
    if g_status == "blocked_policy":
        return "blocked_policy"
    if g_status == "budget_exceeded":
        return "budget_exceeded"
    if activity.get("workers_active") or pipeline.get("busy"):
        if g_status in {"pending", "in_progress"} or not goal.get("exists"):
            return "pipeline_running" if pipeline.get("busy") and not activity.get("workers_active") else "agent_running"
        return "agent_running"
    if g_status == "succeeded":
        return "succeeded"
    if g_status == "failed":
        return "failed"
    if repair.get("interrupted") or str(repair.get("status") or "") == "failed":
        return "failed"
    if g_status in {"pending", "in_progress"}:
        return "idle"  # waiting for user / next step
    return "idle"


def build_runtime_status(root: Path | None = None, *, reevaluate_goal: bool = False) -> dict[str, Any]:
    """Aggregate all runtime stores into one payload."""
    root = root or project_root()
    if reevaluate_goal:
        try:
            from agent.goal import load_goal, reevaluate_goal as _reeval

            g = load_goal(root)
            if g:
                _reeval(root, g)
        except Exception:
            pass

    slices = {
        "goal": _goal_slice(root),
        "activity": _activity_slice(root),
        "repair": _repair_slice(root),
        "pipeline": _pipeline_slice(root),
        "materials": _materials_slice(root),
        "issues": _issues_slice(root),
    }
    warnings = detect_inconsistencies(slices)
    mode = derive_product_mode(slices, warnings)
    return {
        "ok": True,
        "product_mode": mode,
        "product_mode_label": _mode_label(mode),
        "consistent": not any(w.get("severity") == "error" for w in warnings),
        "warnings": warnings,
        "stores": slices,
        "truth": {
            "note": (
                "权威源：Goal/Materials/Issues/Policy/RepairJob/AgentActivity=control.db；"
                "流水线控制=control.db（V1 投影为 run_state/pipeline_control）；"
                "聊天消息仅为历史快照，不参与 live 状态。"
            ),
            "live_sources": [
                "workspace/control.db",
            ],
            "compatibility_projections": [
                "workspace/agent/goal_state.json",
                "workspace/agent/activity.json",
                "workspace/materials_checklist.json",
                "workspace/issues/open.json",
                "workspace/repair_job.json",
                "workspace/run_state.json",
                "workspace/pipeline_control.json",
            ],
            "historical_only": [
                "chat messages (SQLite)",
                "decision_trace / last_plan snapshots",
            ],
        },
    }


def _mode_label(mode: str) -> str:
    return {
        "idle": "空闲",
        "agent_running": "Agent 执行中",
        "pipeline_running": "流水线执行中",
        "repair_running": "最小修复中",
        "awaiting_confirmation": "等待确认",
        "blocked_human": "需人工/补料",
        "blocked_policy": "策略阻断",
        "budget_exceeded": "预算熔断",
        "succeeded": "目标已完成",
        "failed": "失败/中断",
        "inconsistent": "状态不一致",
    }.get(mode, mode)


def soft_heal_inconsistencies(root: Path | None = None) -> dict[str, Any]:
    """Apply safe auto-heals for known false-success states.

    Does NOT auto-kill activity mid-run (pipeline may lag control file).
    Ghost workers are handled by reconcile_interrupted_activity on process startup only.
    """
    root = root or project_root()
    actions: list[str] = []
    runtime = build_runtime_status(root, reevaluate_goal=False)
    stores = runtime.get("stores") or {}
    goal = stores.get("goal") or {}
    activity = stores.get("activity") or {}
    repair = stores.get("repair") or {}
    pipeline = stores.get("pipeline") or {}

    # False goal success while workers / repair / pipeline still active OR open blockers remain
    open_blocks = int((stores.get("issues") or {}).get("open_blocks") or 0)
    if goal.get("status") == "succeeded" and (
        activity.get("workers_active")
        or repair.get("active")
        or pipeline.get("busy")
        or open_blocks > 0
    ):
        try:
            from agent.goal import load_goal, set_goal_status

            g = load_goal(root)
            if g:
                if open_blocks > 0:
                    reason = f"运行时状态不一致：仍有 {open_blocks} 个开放阻断问题，目标不应为已完成"
                else:
                    reason = "运行时状态不一致：工位/修复/流水线仍活跃"
                set_goal_status(root, "in_progress", blocked_reason=reason, goal=g)
                actions.append("demote_goal_succeeded_to_in_progress")
        except Exception:
            pass

    # Re-evaluate after heals (applies runtime_blocks_success gates)
    try:
        from agent.goal import load_goal, reevaluate_goal

        g = load_goal(root)
        if g:
            reevaluate_goal(root, g)
            actions.append("reevaluate_goal")
    except Exception:
        pass

    final = build_runtime_status(root, reevaluate_goal=False)
    final["heal_actions"] = actions
    return final
