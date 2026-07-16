from __future__ import annotations

"""Unified quality Issues for gate stop + minimal repair (plan G0+)."""


import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils import project_root, stringify

_lock = threading.RLock()

Severity = str  # block | warn | info
IssueStatus = str  # open | in_progress | fixed | accepted | wontfix


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def issues_dir(root: Path | None = None) -> Path:
    root = root or project_root()
    path = root / "workspace" / "issues"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_issues_path(root: Path | None = None) -> Path:
    return issues_dir(root) / "open.json"


def issues_log_path(root: Path | None = None) -> Path:
    return issues_dir(root) / "issues.jsonl"


def new_issue_id() -> str:
    return "iss_" + uuid4().hex[:10]


def quality_gate_mode() -> str:
    """strict = block stops pipeline; soft = record only."""
    mode = str(os.environ.get("QUALITY_GATE_MODE", "strict")).strip().lower()
    return mode if mode in {"strict", "soft"} else "strict"


def make_issue(
    *,
    stage_id: str,
    command: str,
    severity: str,
    code: str,
    title: str,
    detail: str = "",
    target_type: str = "global",
    target_ids: list[str] | None = None,
    likely_cause_stage: str = "",
    suggested_actions: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "gate",
    status: str = "open",
) -> dict[str, Any]:
    sev = severity if severity in {"block", "warn", "info"} else "warn"
    now = _now()
    return {
        "id": new_issue_id(),
        "stage_id": stage_id,
        "command": command,
        "severity": sev,
        "code": code,
        "title": title,
        "detail": detail or title,
        "evidence": evidence or {},
        "target": {
            "type": target_type,
            "ids": [str(x) for x in (target_ids or []) if str(x).strip()],
        },
        "likely_cause_stage": likely_cause_stage or stage_id,
        "suggested_actions": suggested_actions or [],
        "status": status if status in {"open", "in_progress", "fixed", "accepted", "wontfix"} else "open",
        "source": source,
        "created_at": now,
        "updated_at": now,
    }


def load_open_issues(root: Path | None = None) -> list[dict[str, Any]]:
    path = open_issues_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return [i for i in data["issues"] if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def save_open_issues(root: Path | None, issues: list[dict[str, Any]]) -> Path:
    root = root or project_root()
    path = open_issues_path(root)
    payload = {
        "updated_at": _now(),
        "count": len(issues),
        "block_count": sum(1 for i in issues if i.get("severity") == "block" and i.get("status") == "open"),
        "issues": issues,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def append_issue_log(root: Path | None, issue: dict[str, Any]) -> None:
    path = issues_log_path(root)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(issue, ensure_ascii=False) + "\n")


def upsert_issues(
    root: Path | None,
    new_issues: list[dict[str, Any]],
    *,
    replace_stage_id: str | None = None,
) -> list[dict[str, Any]]:
    """Merge issues into open snapshot. Optionally drop previous open issues from same stage."""
    root = root or project_root()
    with _lock:
        current = load_open_issues(root)
        if replace_stage_id:
            current = [
                i
                for i in current
                if not (
                    str(i.get("stage_id")) == replace_stage_id
                    and str(i.get("status")) in {"open", "in_progress"}
                )
            ]
        # de-dupe by stage+code+target ids
        index = {
            (
                str(i.get("stage_id")),
                str(i.get("code")),
                tuple((i.get("target") or {}).get("ids") or []),
            ): i
            for i in current
        }
        for issue in new_issues:
            if not isinstance(issue, dict):
                continue
            key = (
                str(issue.get("stage_id")),
                str(issue.get("code")),
                tuple((issue.get("target") or {}).get("ids") or []),
            )
            if key in index and str(index[key].get("status")) in {"open", "in_progress"}:
                # refresh detail, keep id
                old = index[key]
                issue = {**issue, "id": old.get("id"), "created_at": old.get("created_at"), "updated_at": _now()}
                index[key] = issue
            else:
                index[key] = issue
            append_issue_log(root, issue)
        merged = list(index.values())
        save_open_issues(root, merged)
        return merged


def open_block_issues(root: Path | None = None) -> list[dict[str, Any]]:
    return [
        i
        for i in load_open_issues(root)
        if str(i.get("severity")) == "block" and str(i.get("status")) in {"open", "in_progress"}
    ]


def can_proceed(root: Path | None = None, *, next_command: str = "") -> dict[str, Any]:
    """Return whether pipeline may proceed to next_command."""
    mode = quality_gate_mode()
    blocks = open_block_issues(root)
    if mode == "soft":
        return {
            "ok": True,
            "can_proceed": True,
            "mode": mode,
            "block_count": len(blocks),
            "blocks": blocks,
            "message": "soft 模式：仅记录不阻断",
        }
    if not blocks:
        return {
            "ok": True,
            "can_proceed": True,
            "mode": mode,
            "block_count": 0,
            "blocks": [],
            "message": "无 open block 问题",
            "next_command": next_command,
        }
    titles = [str(b.get("title") or b.get("code") or b.get("id")) for b in blocks[:5]]
    return {
        "ok": True,
        "can_proceed": False,
        "mode": mode,
        "block_count": len(blocks),
        "blocks": blocks,
        "next_command": next_command,
        "message": (
            f"存在 {len(blocks)} 个阻断问题，禁止进入下一步"
            + (f" `{next_command}`" if next_command else "")
            + "。请先处理："
            + "；".join(titles)
            + ("…" if len(blocks) > 5 else "")
        ),
    }


def assert_can_proceed(root: Path | None = None, *, next_command: str = "") -> None:
    result = can_proceed(root, next_command=next_command)
    if not result.get("can_proceed"):
        raise RuntimeError(str(result.get("message") or "质量门禁阻断，禁止进入下一步"))


def mark_issue_status(root: Path | None, issue_id: str, status: str) -> dict[str, Any] | None:
    root = root or project_root()
    with _lock:
        issues = load_open_issues(root)
        found = None
        for i in issues:
            if str(i.get("id")) == issue_id:
                i["status"] = status
                i["updated_at"] = _now()
                found = i
                append_issue_log(root, i)
                break
        if found is None:
            return None
        # keep fixed issues out of open snapshot optional: keep but filtered by open_block
        save_open_issues(root, issues)
        return found


def issues_summary(root: Path | None = None) -> dict[str, Any]:
    issues = load_open_issues(root)
    open_issues = [i for i in issues if str(i.get("status")) in {"open", "in_progress"}]
    blocks = [i for i in open_issues if i.get("severity") == "block"]
    warns = [i for i in open_issues if i.get("severity") == "warn"]
    return {
        "open_count": len(open_issues),
        "block_count": len(blocks),
        "warn_count": len(warns),
        "can_proceed": quality_gate_mode() == "soft" or len(blocks) == 0,
        "mode": quality_gate_mode(),
        "top_blocks": [
            {"id": b.get("id"), "code": b.get("code"), "title": b.get("title"), "stage_id": b.get("stage_id")}
            for b in blocks[:8]
        ],
    }
