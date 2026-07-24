from __future__ import annotations

"""Unified workspace snapshot for Supervisor decisions (PR-9)."""

import json
from pathlib import Path
from typing import Any

from agent.budgets import observation_max_chars, snapshot_max_chars
from agent.invalidation import load_stale
from utils import project_root


def _trim(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 3] + "..."
    if isinstance(value, list):
        out = []
        size = 2
        for item in value:
            chunk = _trim(item, max(80, limit // 4))
            piece = json.dumps(chunk, ensure_ascii=False, default=str)
            if size + len(piece) > limit:
                break
            out.append(chunk)
            size += len(piece) + 1
        return out
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        size = 2
        for key, item in value.items():
            chunk = _trim(item, max(80, limit // 4))
            piece = json.dumps({key: chunk}, ensure_ascii=False, default=str)
            if size + len(piece) > limit:
                break
            out[str(key)] = chunk
            size += len(piece) + 1
        return out
    return value


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _artifact_status(root: Path, rel: str) -> str:
    path = root / rel
    if not path.exists():
        return "missing"
    try:
        from agent.invalidation import is_stale

        if is_stale(root, rel):
            return "stale"
    except Exception:
        pass
    if path.is_file() and path.stat().st_size <= 0:
        return "empty"
    return "ready"


def _collect_artifacts(root: Path) -> dict[str, list[str]]:
    candidates = [
        "outputs/final.md",
        "outputs/final.docx",
        "outputs/draft.docx",
        "outputs/risk_register.md",
        "workspace/global_review.json",
        "workspace/compliance_report.json",
        "workspace/score_coverage_matrix.json",
        "workspace/format_check_report.json",
        "workspace/outline.json",
    ]
    ready: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    for rel in candidates:
        status = _artifact_status(root, rel)
        if status == "ready":
            ready.append(rel)
        elif status == "stale":
            stale.append(rel)
        else:
            missing.append(rel)
    return {"ready": ready, "missing": missing, "stale": stale}


def _issues_view(root: Path) -> dict[str, Any]:
    try:
        from agent.issues import load_open_issues, open_block_issues

        blocks = open_block_issues(root)
        all_open = load_open_issues(root)
        warnings = [
            i
            for i in all_open
            if str(i.get("status") or "open") == "open" and str(i.get("severity")) == "warn"
        ]
        accepted = [i for i in all_open if str(i.get("status")) == "accepted"]
        return {
            "open_blocks": [
                {
                    "id": b.get("id"),
                    "code": b.get("code"),
                    "title": str(b.get("title") or "")[:120],
                    "stage_id": b.get("stage_id"),
                    "risk_class": b.get("risk_class") or b.get("severity"),
                }
                for b in blocks[:20]
            ],
            "open_warnings": [
                {"id": w.get("id"), "code": w.get("code"), "title": str(w.get("title") or "")[:80]}
                for w in warnings[:15]
            ],
            "accepted": [
                {
                    "id": a.get("id"),
                    "code": a.get("code"),
                    "title": str(a.get("title") or "")[:80],
                    "accept_reason": str(a.get("accept_reason") or "")[:120],
                }
                for a in accepted[:15]
            ],
            "open_block_count": len(blocks),
            "open_warning_count": len(warnings),
            "accepted_count": len(accepted),
        }
    except Exception:
        return {
            "open_blocks": [],
            "open_warnings": [],
            "accepted": [],
            "open_block_count": 0,
            "open_warning_count": 0,
            "accepted_count": 0,
        }


def _materials_view(root: Path) -> dict[str, Any]:
    try:
        from control_plane import ControlStore, WorkspaceContext

        items = ControlStore(WorkspaceContext(root.name, root)).material_states()
        missing = []
        uploaded = []
        for item in items:
            if not isinstance(item, dict):
                continue
            lifecycle = str(item.get("lifecycle_status") or item.get("response_status") or "")
            evidence = str(item.get("evidence_status") or "")
            compact = {
                "item_id": item.get("item_id"),
                "category": item.get("category"),
                "requirement": str(item.get("requirement") or "")[:100],
                "severity": item.get("severity"),
                "lifecycle_status": lifecycle,
                "evidence_status": evidence,
                "target_chapter_hints": (item.get("target_chapter_hints") or [])[:5],
                "suggested_attachment": str(item.get("suggested_attachment") or "")[:80],
            }
            if lifecycle in {"missing", "requested", "deferred"} or evidence in {"missing", "weak"}:
                if str(item.get("response_status") or "") != "ready":
                    missing.append(compact)
            if lifecycle in {"uploaded", "verified", "injected", "resolved"} or evidence == "satisfied":
                uploaded.append(compact)
        return {
            "missing": missing[:30],
            "uploaded": uploaded[:30],
            "summary": {
                "total": len(items),
                "ready": sum(1 for item in items if str(item.get("response_status") or "") == "ready"),
                "deferred": sum(1 for item in items if str(item.get("response_status") or "deferred") == "deferred"),
                "waived": sum(1 for item in items if str(item.get("response_status") or "") == "waived"),
            },
        }
    except Exception:
        return {"missing": [], "uploaded": [], "summary": {}}


def _pipeline_view(root: Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    if status:
        try:
            from session_orchestrator import _compact_status_snapshot

            compact = _compact_status_snapshot(status)
            return {
                "status": str((status.get("run_state") or {}).get("status") or compact.get("run_status") or "idle"),
                "current_stage": compact.get("current_stage") or "",
                "next_step": compact.get("next_step") or status.get("next_step") or {},
                "progress": compact.get("progress") or {},
            }
        except Exception:
            pass
    # lightweight file-based fallback
    run_state = _safe_json(root / "workspace" / "run_state.json") or {}
    next_step = {}
    try:
        qs = None
        from agent.tool_runtime import invoke

        qs = invoke("query_status", {"view": "summary"}, root=root, actor="snapshot")
        metrics = qs.metrics if qs and qs.ok else {}
        return {
            "status": str(run_state.get("status") or metrics.get("run_status") or "idle"),
            "current_stage": metrics.get("current_stage") or run_state.get("current_stage") or "",
            "next_step": metrics.get("next_step") or {},
            "progress": metrics.get("progress") or {},
        }
    except Exception:
        return {
            "status": str(run_state.get("status") or "idle"),
            "current_stage": str(run_state.get("current_stage") or ""),
            "next_step": next_step,
            "progress": {},
        }


def _repair_job_view(root: Path) -> dict[str, Any]:
    try:
        from agent.repair_jobs import load_repair_job

        job = load_repair_job(root)
        if not job:
            return {}
        return {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "progress": job.get("progress") or {},
            "resume_command": job.get("resume_command") or "",
        }
    except Exception:
        path = root / "workspace" / "repair_job.json"
        data = _safe_json(path)
        return data if isinstance(data, dict) else {}


def _manual_review_view(root: Path) -> dict[str, Any]:
    path = root / "workspace" / "manual_review" / "summary.json"
    data = _safe_json(path)
    if isinstance(data, dict):
        return {
            "pending": data.get("pending") or data.get("open") or 0,
            "accepted": data.get("accepted") or 0,
            "summary": _trim(data, 800),
        }
    return {}


def _goal_view(root: Path, goal: dict[str, Any] | None = None) -> dict[str, Any]:
    if goal is None:
        try:
            from agent.goal import load_goal

            goal = load_goal(root)
        except Exception:
            goal = None
    if not goal:
        return {}
    plan = goal.get("plan") if isinstance(goal.get("plan"), list) else []
    return {
        "goal_id": goal.get("goal_id"),
        "raw_user_goal": str(goal.get("raw_user_goal") or "")[:300],
        "status": goal.get("status"),
        "blocked_reason": str(goal.get("blocked_reason") or "")[:300],
        "all_criteria_ok": goal.get("all_criteria_ok"),
        "current_plan_index": goal.get("current_plan_index", 0),
        "plan_len": len(plan),
        "plan_preview": [
            {
                "step_id": s.get("step_id"),
                "tool": s.get("tool"),
                "status": s.get("status"),
                "attempts": s.get("attempts", 0),
            }
            for s in plan[:12]
            if isinstance(s, dict)
        ],
        "criteria_results": _trim(goal.get("criteria_results") or [], 2000),
        "confirmation_scope": goal.get("confirmation_scope") or {},
        "progress": goal.get("progress") or {},
        "normalized_objectives": _trim(goal.get("normalized_objectives") or [], 800),
        "constraints": goal.get("constraints") or {},
    }


def human_blocking_reason(snapshot: dict[str, Any], goal: dict[str, Any] | None = None) -> str:
    """Return non-empty reason if supervisor must pause for human."""
    if goal and str(goal.get("status")) == "blocked_human":
        return str(goal.get("blocked_reason") or "目标已标记为需要人工处理")
    materials = snapshot.get("materials") if isinstance(snapshot.get("materials"), dict) else {}
    missing = materials.get("missing") if isinstance(materials.get("missing"), list) else []
    fatal_missing = [
        m
        for m in missing
        if isinstance(m, dict)
        and str(m.get("severity") or "").lower() in {"block", "fatal", "critical", "blocker"}
        and str(m.get("lifecycle_status") or m.get("evidence_status") or "")
        in {"missing", "requested", "deferred", "weak"}
    ]
    if fatal_missing:
        titles = [str(m.get("requirement") or m.get("item_id") or "材料")[:40] for m in fatal_missing[:5]]
        return "缺少不可自动补齐的材料: " + "；".join(titles)

    issues = snapshot.get("issues") if isinstance(snapshot.get("issues"), dict) else {}
    blocks = issues.get("open_blocks") if isinstance(issues.get("open_blocks"), list) else []
    fatal_blocks = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        risk = str(b.get("risk_class") or "").lower()
        code = str(b.get("code") or "").upper()
        if risk in {"fatal", "disqualify", "废标", "qualification_missing"} or code in {
            "FATAL",
            "DISQUALIFY",
            "QUALIFICATION_MISSING",
            "MISSING_CERTIFICATE",
        }:
            fatal_blocks.append(b)
    if fatal_blocks:
        return "存在不可自动处理的阻断问题，需要人工补料或决策"
    return ""


def build_snapshot(
    root: Path | None = None,
    *,
    status: dict[str, Any] | None = None,
    goal: dict[str, Any] | None = None,
    last_tool_result: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    for_llm: bool = True,
) -> dict[str, Any]:
    root = root or project_root()
    snapshot = {
        "pipeline": _pipeline_view(root, status),
        "goal": _goal_view(root, goal),
        "artifacts": _collect_artifacts(root),
        "issues": _issues_view(root),
        "materials": _materials_view(root),
        "repair_job": _repair_job_view(root),
        "manual_review": _manual_review_view(root),
        "last_tool_result": _trim(last_tool_result or {}, observation_max_chars()),
        "budget": budget or {},
        "stale": {
            "count": len((load_stale(root).get("items") or {})),
            "paths": list((load_stale(root).get("items") or {}).keys())[:20],
        },
    }
    if for_llm:
        limit = snapshot_max_chars()
        raw = json.dumps(snapshot, ensure_ascii=False, default=str)
        if len(raw) > limit:
            snapshot = _trim(snapshot, limit)
            snapshot["_truncated"] = True
            snapshot["_max_chars"] = limit
    return snapshot


def snapshot_for_llm(snapshot: dict[str, Any]) -> dict[str, Any]:
    return _trim(snapshot, snapshot_max_chars())
