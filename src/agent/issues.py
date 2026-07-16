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
    # Allow re-running the gate stage that produced the blocks (so repair can revalidate)
    if next_command:
        block_commands = {str(b.get("command") or "") for b in blocks}
        if next_command in block_commands:
            return {
                "ok": True,
                "can_proceed": True,
                "mode": mode,
                "block_count": len(blocks),
                "blocks": blocks,
                "next_command": next_command,
                "message": f"允许重验门禁阶段 `{next_command}`（当前仍有 {len(blocks)} 条 block）",
                "revalidate_allowed": True,
            }
    titles = [str(b.get("title") or b.get("code") or b.get("id")) for b in blocks[:5]]
    try:
        record_issue_metric(root, "gate_block", next_command=next_command, block_count=len(blocks))
    except Exception:
        pass
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


def metrics_path(root: Path | None = None) -> Path:
    return issues_dir(root) / "metrics.jsonl"


def record_issue_metric(root: Path | None, event: str, **fields: Any) -> None:
    root = root or project_root()
    payload = {"ts": _now(), "event": event, **fields}
    path = metrics_path(root)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_issue_metrics(root: Path | None = None, *, tail: int = 200) -> dict[str, Any]:
    path = metrics_path(root)
    if not path.exists():
        return {"events": [], "block_events": 0, "repair_success": 0, "repair_fail": 0}
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, tail):]
    events = []
    block_events = repair_success = repair_fail = 0
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        events.append(item)
        ev = str(item.get("event") or "")
        if ev == "gate_block":
            block_events += 1
        elif ev == "repair_success":
            repair_success += 1
        elif ev == "repair_fail":
            repair_fail += 1
    return {
        "events": events,
        "block_events": block_events,
        "repair_success": repair_success,
        "repair_fail": repair_fail,
        "repair_success_rate": (
            round(repair_success / max(1, repair_success + repair_fail), 3)
            if (repair_success + repair_fail)
            else None
        ),
    }


def accept_risk_enabled() -> bool:
    flag = str(os.environ.get("ISSUE_ACCEPT_RISK_ENABLED", "0")).strip().lower()
    return flag not in {"0", "false", "no", "off", ""}


def accept_issue_risk(
    root: Path | None,
    issue_id: str,
    *,
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Mark a block issue as accepted risk (does not delete evidence)."""
    if not accept_risk_enabled():
        return {
            "ok": False,
            "message": "未开启接受风险功能。请设置 ISSUE_ACCEPT_RISK_ENABLED=1（管理员）。",
        }
    root = root or project_root()
    reason = str(reason or "").strip()
    if len(reason) < 4:
        return {"ok": False, "message": "接受风险必须填写原因（至少 4 个字）。"}

    with _lock:
        issues = load_open_issues(root)
        found = None
        for item in issues:
            if str(item.get("id")) == issue_id:
                found = item
                break
        if found is None:
            return {"ok": False, "message": f"未找到问题: {issue_id}"}
        if str(found.get("severity")) != "block":
            return {"ok": False, "message": "仅阻断级（block）问题支持接受风险。"}
        found["status"] = "accepted"
        found["updated_at"] = _now()
        found["accepted_at"] = _now()
        found["accepted_by"] = actor
        found["accept_reason"] = reason[:500]
        append_issue_log(root, found)
        save_open_issues(root, issues)
        try:
            record_issue_metric(
                root,
                "accept_risk",
                issue_id=issue_id,
                code=str(found.get("code") or ""),
                actor=actor,
            )
        except Exception:
            pass
        return {
            "ok": True,
            "issue": found,
            "message": "已接受风险，该问题不再阻断流水线（仍保留记录）。",
            "can_proceed": can_proceed(root).get("can_proceed"),
        }


def batch_issue_ids_open_blocks(root: Path | None = None) -> list[str]:
    return [str(i.get("id")) for i in open_block_issues(root) if i.get("id")]


def export_preflight(root: Path | None = None) -> dict[str, Any]:
    """Pre-export checklist: all open blocks must be zero; surface key report flags."""
    root = root or project_root()
    # refresh from latest reports
    try:
        from agent.root_cause import sync_issues_from_compliance, sync_issues_from_global_review

        sync_issues_from_global_review(root)
        sync_issues_from_compliance(root)
    except Exception:
        pass

    summary = issues_summary(root)
    blocks = open_block_issues(root)
    checks: list[dict[str, Any]] = []

    gr_path = root / "workspace" / "global_review.json"
    if gr_path.exists():
        try:
            gr = json.loads(gr_path.read_text(encoding="utf-8"))
        except Exception:
            gr = {}
        gr_block = bool(isinstance(gr, dict) and (gr.get("blocking") or open_block_issues(root)))
        # more precise: global stage blocks
        gr_blocks = [b for b in blocks if str(b.get("stage_id")) == "global_review"]
        checks.append(
            {
                "id": "global_review",
                "label": "全文审核门禁",
                "ok": len(gr_blocks) == 0 and not (isinstance(gr, dict) and gr.get("blocking")),
                "detail": "通过" if not gr_blocks and not (isinstance(gr, dict) and gr.get("blocking")) else f"阻断 {len(gr_blocks)} 项",
            }
        )
    else:
        checks.append({"id": "global_review", "label": "全文审核门禁", "ok": False, "detail": "缺少 global_review.json"})

    cr_path = root / "workspace" / "compliance_report.json"
    if cr_path.exists():
        try:
            cr = json.loads(cr_path.read_text(encoding="utf-8"))
        except Exception:
            cr = {}
        summary_cr = cr.get("summary") if isinstance(cr, dict) and isinstance(cr.get("summary"), dict) else {}
        blocking = bool(isinstance(cr, dict) and (cr.get("blocking") or summary_cr.get("blocking")))
        cr_blocks = [b for b in blocks if str(b.get("stage_id")) == "compliance_check"]
        checks.append(
            {
                "id": "compliance_check",
                "label": "专项合规门禁",
                "ok": (not blocking) and len(cr_blocks) == 0,
                "detail": "通过" if not blocking and not cr_blocks else f"阻断 blocking={blocking}, issues={len(cr_blocks)}",
            }
        )
    else:
        checks.append({"id": "compliance_check", "label": "专项合规门禁", "ok": False, "detail": "缺少 compliance_report.json"})

    open_blocks_ok = len(blocks) == 0
    checks.append(
        {
            "id": "open_blocks",
            "label": "无 open block 问题单",
            "ok": open_blocks_ok,
            "detail": "通过" if open_blocks_ok else f"仍有 {len(blocks)} 条 block",
        }
    )

    md = root / "outputs" / "final.md"
    checks.append(
        {
            "id": "final_md",
            "label": "存在 final.md",
            "ok": md.exists() and md.stat().st_size > 0,
            "detail": str(md) if md.exists() else "缺失",
        }
    )

    can_export = all(bool(c.get("ok")) for c in checks)
    return {
        "ok": True,
        "can_export": can_export,
        "checks": checks,
        "issues_summary": summary,
        "block_issues": [
            {"id": b.get("id"), "code": b.get("code"), "title": b.get("title"), "stage_id": b.get("stage_id")}
            for b in blocks[:20]
        ],
        "message": "可以出正式稿" if can_export else "出稿前检查未通过，请先处理阻断项",
    }
