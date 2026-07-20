from __future__ import annotations

"""Live sub-agent activity ledger for UI (not log scraping)."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import project_root

_lock = threading.RLock()

ROLE_META = {
    "coordinator": {"label": "主 Agent", "emoji": "🧭", "color": "indigo"},
    "chapter_writer": {"label": "写作 Agent", "emoji": "✍️", "color": "blue"},
    "chapter_reviewer": {"label": "审核 Agent", "emoji": "🔍", "color": "purple"},
    "chapter_rewriter": {"label": "改稿 Agent", "emoji": "📝", "color": "orange"},
    "global_reviewer": {"label": "全文审核 Agent", "emoji": "📋", "color": "teal"},
    "pipeline": {"label": "流水线", "emoji": "⚙️", "color": "slate"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def activity_path(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "workspace" / "agent" / "activity.json"


def _empty() -> dict[str, Any]:
    return {
        "updated_at": _now(),
        "phase": "",
        "phase_label": "",
        "status": "idle",
        "agents": [],
        "summary": {"total": 0, "running": 0, "done": 0, "failed": 0, "queued": 0},
    }


def load_activity(root: Path | None = None) -> dict[str, Any]:
    path = activity_path(root)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("agents", [])
    data.setdefault("summary", _empty()["summary"])
    return data


def _save(root: Path, data: dict[str, Any]) -> None:
    path = activity_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    agents = data.get("agents") if isinstance(data.get("agents"), list) else []
    summary = {"total": len(agents), "running": 0, "done": 0, "failed": 0, "queued": 0}
    for a in agents:
        if not isinstance(a, dict):
            continue
        st = str(a.get("status") or "")
        if st in summary:
            summary[st] = int(summary[st]) + 1
        elif st == "running":
            summary["running"] += 1
    data["summary"] = summary
    data["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def begin_phase(
    root: Path | None,
    *,
    phase: str,
    phase_label: str,
    role: str,
    chapter_ids: list[str],
) -> dict[str, Any]:
    root = root or project_root()
    meta = ROLE_META.get(role, {"label": role, "emoji": "🤖", "color": "slate"})
    with _lock:
        agents = []
        for cid in chapter_ids:
            agents.append(
                {
                    "id": f"{role}:{cid}",
                    "role": role,
                    "label": meta["label"],
                    "emoji": meta["emoji"],
                    "color": meta["color"],
                    "chapter_id": str(cid),
                    "status": "queued",
                    "attempt": 0,
                    "message": "排队中",
                    "started_at": "",
                    "ended_at": "",
                }
            )
        data = {
            "updated_at": _now(),
            "phase": phase,
            "phase_label": phase_label,
            "status": "running",
            "agents": agents,
            "summary": {},
        }
        _save(root, data)
        return data


def mark_agent(
    root: Path | None,
    *,
    role: str,
    chapter_id: str,
    status: str,
    message: str = "",
    attempt: int | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    agent_id = f"{role}:{chapter_id}"
    with _lock:
        data = load_activity(root)
        agents = data.get("agents") if isinstance(data.get("agents"), list) else []
        found = None
        for a in agents:
            if isinstance(a, dict) and str(a.get("id")) == agent_id:
                found = a
                break
        meta = ROLE_META.get(role, {"label": role, "emoji": "🤖", "color": "slate"})
        if found is None:
            found = {
                "id": agent_id,
                "role": role,
                "label": meta["label"],
                "emoji": meta["emoji"],
                "color": meta["color"],
                "chapter_id": str(chapter_id),
            }
            agents.append(found)
            data["agents"] = agents
        found["status"] = status
        if message:
            found["message"] = message
        if attempt is not None:
            found["attempt"] = attempt
        if status == "running" and not found.get("started_at"):
            found["started_at"] = _now()
        if status in {"done", "failed"}:
            found["ended_at"] = _now()
        # phase status
        if any(str(a.get("status")) == "running" for a in agents if isinstance(a, dict)):
            data["status"] = "running"
        elif any(str(a.get("status")) == "failed" for a in agents if isinstance(a, dict)):
            data["status"] = "partial_failed"
        elif agents and all(str(a.get("status")) in {"done", "failed"} for a in agents if isinstance(a, dict)):
            data["status"] = "done"
        _save(root, data)
        return data


def end_phase(root: Path | None, *, status: str = "done", message: str = "") -> dict[str, Any]:
    root = root or project_root()
    with _lock:
        data = load_activity(root)
        data["status"] = status
        if message:
            data["message"] = message
        # mark remaining queued as skipped
        for a in data.get("agents") or []:
            if isinstance(a, dict) and a.get("status") == "queued":
                a["status"] = "skipped"
                a["message"] = a.get("message") or "未执行"
                a["ended_at"] = _now()
        _save(root, data)
        return data


def failed_chapter_ids(root: Path | None = None, *, role: str = "chapter_writer") -> list[str]:
    """Chapter ids currently marked failed for a role (for fire-desk retry)."""
    data = load_activity(root)
    out: list[str] = []
    for a in data.get("agents") or []:
        if not isinstance(a, dict):
            continue
        if a.get("is_coordinator") or str(a.get("role")) == "coordinator":
            continue
        if role and str(a.get("role") or "") != role:
            continue
        if str(a.get("status") or "") != "failed":
            continue
        cid = str(a.get("chapter_id") or "").strip()
        if cid and cid not in out:
            out.append(cid)
    return out


def has_active_workers(root: Path | None = None) -> bool:
    """True when chapter workers are mid-phase (excludes coordinator)."""
    data = load_activity(root)
    if str(data.get("status") or "") == "running":
        agents = data.get("agents") if isinstance(data.get("agents"), list) else []
        for a in agents:
            if not isinstance(a, dict):
                continue
            if a.get("is_coordinator") or str(a.get("role")) == "coordinator":
                continue
            if str(a.get("status") or "") in {"running", "queued"}:
                return True
        # phase marked running but no workers → treat as inactive
    agents = data.get("agents") if isinstance(data.get("agents"), list) else []
    for a in agents:
        if not isinstance(a, dict):
            continue
        if a.get("is_coordinator") or str(a.get("role")) == "coordinator":
            continue
        if str(a.get("status") or "") in {"running", "queued"}:
            return True
    return False


def reconcile_interrupted_activity(root: Path | None = None) -> dict[str, Any]:
    """Close ghost running/queued seats after process restart (mirror repair reconcile)."""
    root = root or project_root()
    with _lock:
        data = load_activity(root)
        agents = data.get("agents") if isinstance(data.get("agents"), list) else []
        changed = False
        for a in agents:
            if not isinstance(a, dict):
                continue
            if a.get("is_coordinator") or str(a.get("role")) == "coordinator":
                continue
            st = str(a.get("status") or "")
            if st == "running":
                a["status"] = "failed"
                a["message"] = "服务重启中断，章节任务未完成"
                a["ended_at"] = _now()
                a["interrupted_by_restart"] = True
                changed = True
            elif st == "queued":
                a["status"] = "skipped"
                a["message"] = "服务重启后未继续领取"
                a["ended_at"] = _now()
                a["interrupted_by_restart"] = True
                changed = True
        if changed or str(data.get("status") or "") == "running":
            data["status"] = "interrupted"
            data["message"] = "服务重启已清理在岗/排队工位；重新执行阶段后恢复"
            data["phase"] = str(data.get("phase") or "")
            # clear phase_label so coordinator does not look mid-write
            if str(data.get("phase_label") or ""):
                data["phase_label"] = ""
            _save(root, data)
        return data


def _materials_deferred_count(root: Path) -> int:
    try:
        from materials_checklist import load_materials_checklist

        checklist = load_materials_checklist(root)
        summary = checklist.get("summary") if isinstance(checklist.get("summary"), dict) else {}
        return max(0, int(summary.get("deferred") or 0))
    except Exception:
        return 0


def _ensure_coordinator(root: Path, agents: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Always surface the main coordinator agent in the office UI."""
    meta = ROLE_META["coordinator"]
    existing = next((a for a in agents if isinstance(a, dict) and str(a.get("role")) == "coordinator"), None)
    phase_running = str(data.get("status") or "") == "running" or any(
        str(a.get("status")) == "running" for a in agents if isinstance(a, dict) and str(a.get("role")) != "coordinator"
    )
    deferred = _materials_deferred_count(root)
    if phase_running:
        status = "running"
        message = str(data.get("phase_label") or data.get("phase") or "统筹调度中")
    else:
        status = "running"  # 主 Agent 常驻值班
        message = "值班统筹 · 等待用户指令"
    if deferred > 0:
        message = f"{message} · 待补材料 {deferred} 条"

    if existing is None:
        agents.insert(
            0,
            {
                "id": "coordinator:main",
                "role": "coordinator",
                "label": meta["label"],
                "emoji": meta["emoji"],
                "color": meta["color"],
                "chapter_id": "主控",
                "status": status,
                "attempt": 0,
                "message": message,
                "started_at": "",
                "ended_at": "",
                "is_coordinator": True,
            },
        )
    else:
        existing["label"] = meta["label"]
        existing["emoji"] = meta["emoji"]
        existing["color"] = meta["color"]
        existing["chapter_id"] = existing.get("chapter_id") or "主控"
        existing["status"] = status
        existing["message"] = message
        existing["is_coordinator"] = True
        # keep coordinator first
        agents = [existing] + [a for a in agents if a is not existing]
    return agents


def activity_for_api(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    data = load_activity(root)
    # sort: running first, then queued, then done/failed
    order = {"running": 0, "queued": 1, "failed": 2, "done": 3, "skipped": 4}
    agents = [a for a in (data.get("agents") or []) if isinstance(a, dict)]
    agents = _ensure_coordinator(root, agents, data)
    agents.sort(
        key=lambda a: (
            0 if a.get("is_coordinator") or str(a.get("role")) == "coordinator" else 1,
            order.get(str(a.get("status")), 9),
            str(a.get("chapter_id") or ""),
        )
    )
    data["agents"] = agents
    data["coordinator"] = next(
        (a for a in agents if str(a.get("role")) == "coordinator"),
        None,
    )
    data["materials_deferred"] = _materials_deferred_count(root)
    return data
