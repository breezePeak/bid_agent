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


def activity_for_api(root: Path | None = None) -> dict[str, Any]:
    data = load_activity(root)
    # sort: running first, then queued, then done/failed
    order = {"running": 0, "queued": 1, "failed": 2, "done": 3, "skipped": 4}
    agents = [a for a in (data.get("agents") or []) if isinstance(a, dict)]
    agents.sort(key=lambda a: (order.get(str(a.get("status")), 9), str(a.get("chapter_id") or "")))
    data["agents"] = agents
    return data
