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


def _issue_control_store(root: Path):
    from control_plane import ControlStore, WorkspaceContext

    return ControlStore(WorkspaceContext.resolve(root.parent, root.name))


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
    root = (root or project_root()).resolve()
    path = open_issues_path(root)
    imported: list[dict[str, Any]] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict) and isinstance(data.get("issues"), list):
            imported = [i for i in data["issues"] if isinstance(i, dict)]
        elif isinstance(data, list):
            imported = [i for i in data if isinstance(i, dict)]
    store = _issue_control_store(root)
    store.ensure_issue_states(imported)
    return store.issue_states()


def save_open_issues(root: Path | None, issues: list[dict[str, Any]]) -> Path:
    root = (root or project_root()).resolve()
    normalized = [dict(item) for item in issues if isinstance(item, dict) and str(item.get("id") or "").strip()]
    _issue_control_store(root).replace_issue_states(normalized)
    path = open_issues_path(root)
    payload = {
        "updated_at": _now(),
        "count": len(normalized),
        "block_count": sum(1 for i in normalized if i.get("severity") == "block" and i.get("status") == "open"),
        "issues": normalized,
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
    # PR-12: default OFF
    flag = str(os.environ.get("ISSUE_ACCEPT_RISK_ENABLED", "0")).strip().lower()
    return flag not in {"0", "false", "no", "off", ""}


_FATAL_CODES = {
    "FATAL",
    "DISQUALIFY",
    "废标",
    "BID_REJECTION",
}

_QUALIFICATION_CODES = {
    "QUALIFICATION_MISSING",
    "MISSING_CERTIFICATE",
    "MANDATORY_DOC_MISSING",
    "MATERIAL_GAP",
    "NEED_EVIDENCE",
}


def classify_issue_risk(issue: dict[str, Any]) -> str:
    """Return risk class: fatal | qualification | critical | major | minor."""
    code = str(issue.get("code") or "").upper()
    risk = str(issue.get("risk_class") or "").lower()
    sev = str(issue.get("severity") or "").lower()
    title = str(issue.get("title") or "") + str(issue.get("detail") or "")
    if risk in {"fatal", "qualification", "critical", "major", "minor"}:
        return risk
    if code in _FATAL_CODES or "废标" in title or "fatal" in title.lower():
        return "fatal"
    if code in _QUALIFICATION_CODES or "资格" in title or "证书" in title:
        return "qualification"
    if sev == "block" and (code.startswith("CRITICAL") or "critical" in title.lower()):
        return "critical"
    if sev == "block":
        return "major"
    if sev == "warn":
        return "major"
    return "minor"


def _effective_reason_chars(reason: str) -> str:
    # strip whitespace for min length
    return "".join(ch for ch in (reason or "") if not ch.isspace())


def accept_issue_risk(
    root: Path | None,
    issue_id: str,
    *,
    reason: str = "",
    actor: str = "user",
    is_admin: bool = False,
    confirm_critical: bool = False,
) -> dict[str, Any]:
    """Mark a block issue as accepted risk (does not delete evidence). PR-12 tightened."""
    if not accept_risk_enabled():
        return {
            "ok": False,
            "message": "未开启接受风险功能。请设置 ISSUE_ACCEPT_RISK_ENABLED=1（管理员）。",
        }
    root = root or project_root()
    reason = str(reason or "").strip()
    if len(_effective_reason_chars(reason)) < 8:
        return {
            "ok": False,
            "message": "接受风险必须填写原因（至少 8 个有效字符）。",
            "code": "reason_too_short",
        }

    with _lock:
        issues = load_open_issues(root)
        found = None
        for item in issues:
            if str(item.get("id")) == issue_id:
                found = item
                break
        if found is None:
            return {"ok": False, "message": f"未找到问题: {issue_id}"}

        risk_class = classify_issue_risk(found)
        found["risk_class"] = risk_class

        if risk_class == "fatal":
            return {
                "ok": False,
                "message": "fatal 废标项禁止通过接受风险关闭。",
                "code": "fatal_forbidden",
                "risk_class": risk_class,
            }
        if risk_class == "qualification":
            return {
                "ok": False,
                "message": "资格材料缺失不可直接接受风险，请补料或标记 deferred。",
                "code": "qualification_deferred_only",
                "risk_class": risk_class,
            }
        if risk_class == "critical":
            if not is_admin:
                return {
                    "ok": False,
                    "message": "critical 合规冲突仅管理员可接受风险。",
                    "code": "admin_required",
                    "risk_class": risk_class,
                }
            if not confirm_critical:
                return {
                    "ok": False,
                    "message": "critical 问题需要二次确认（confirm_critical=true）。",
                    "code": "confirm_critical_required",
                    "risk_class": risk_class,
                }

        if str(found.get("severity")) not in {"block", "warn"}:
            return {"ok": False, "message": "仅 block/warn 问题支持接受风险。"}

        # Preserve original evidence; never delete
        evidence = dict(found.get("evidence") or {})
        evidence.setdefault("pre_accept_snapshot", {
            "status": found.get("status"),
            "severity": found.get("severity"),
            "detail": str(found.get("detail") or "")[:500],
        })

        found["status"] = "accepted"
        found["updated_at"] = _now()
        found["accepted_at"] = _now()
        found["accepted_by"] = actor
        found["accept_reason"] = reason[:500]
        found["evidence"] = evidence
        found["risk_class"] = risk_class
        append_issue_log(root, found)
        save_open_issues(root, issues)
        _issue_control_store(root).record_policy_decision(
            issue_id=issue_id,
            decision_type="accept_risk",
            decision={
                "risk_class": risk_class,
                "reason": reason[:500],
                "accepted_at": found.get("accepted_at"),
                "evidence": evidence,
            },
            actor={"type": "authenticated_user", "id": actor},
        )
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
        try:
            write_risk_register(root)
        except Exception:
            pass
        return {
            "ok": True,
            "issue": found,
            "message": "已接受风险，该问题不再阻断流水线（仍保留记录与证据）。",
            "can_proceed": can_proceed(root).get("can_proceed"),
            "risk_class": risk_class,
            "all_passed": False,  # accepted risk means never "全部通过"
        }


def list_accepted_risks(root: Path | None = None) -> list[dict[str, Any]]:
    return [i for i in load_open_issues(root) if str(i.get("status")) == "accepted"]


def write_risk_register(root: Path | None = None) -> Path | None:
    """Optional outputs/risk_register.md for audit."""
    root = root or project_root()
    accepted = list_accepted_risks(root)
    out = root / "outputs" / "risk_register.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 风险接受登记册",
        "",
        f"生成时间: {_now()}",
        "",
        f"已接受风险数: {len(accepted)}",
        "",
    ]
    if not accepted:
        lines.append("（当前无已接受风险）")
    else:
        lines.append("| ID | 代码 | 风险类 | 标题 | 原因 | 操作人 | 时间 |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in accepted:
            lines.append(
                "| {id} | {code} | {rc} | {title} | {reason} | {actor} | {at} |".format(
                    id=str(item.get("id") or ""),
                    code=str(item.get("code") or ""),
                    rc=str(item.get("risk_class") or classify_issue_risk(item)),
                    title=str(item.get("title") or "").replace("|", "/")[:60],
                    reason=str(item.get("accept_reason") or "").replace("|", "/")[:80],
                    actor=str(item.get("accepted_by") or ""),
                    at=str(item.get("accepted_at") or "")[:19],
                )
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def batch_issue_ids_open_blocks(root: Path | None = None) -> list[str]:
    return [str(i.get("id")) for i in open_block_issues(root) if i.get("id")]


def export_preflight(root: Path | None = None) -> dict[str, Any]:
    """Pre-export checklist: open blocks zero; always surface accepted risks (PR-12)."""
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
    accepted = list_accepted_risks(root)
    checks: list[dict[str, Any]] = []

    gr_path = root / "workspace" / "global_review.json"
    if gr_path.exists():
        try:
            gr = json.loads(gr_path.read_text(encoding="utf-8"))
        except Exception:
            gr = {}
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

    # Accepted risks: never block export by themselves, but never claim "全部通过"
    checks.append(
        {
            "id": "accepted_risks",
            "label": "已接受风险披露",
            "ok": True,
            "detail": "无" if not accepted else f"存在 {len(accepted)} 条已接受风险（终稿不得显示全部通过）",
            "count": len(accepted),
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

    gate_ok = all(bool(c.get("ok")) for c in checks if c.get("id") != "accepted_risks")
    can_export = gate_ok  # accepted risks do not block formal export after gates pass
    all_passed = can_export and len(accepted) == 0
    try:
        write_risk_register(root)
    except Exception:
        pass

    if can_export and accepted:
        message = f"可以出正式稿，但存在 {len(accepted)} 条已接受风险，不得标注“全部通过”"
    elif can_export:
        message = "可以出正式稿"
    else:
        message = "出稿前检查未通过，请先处理阻断项"

    return {
        "ok": True,
        "can_export": can_export,
        "all_passed": all_passed,
        "has_accepted_risks": len(accepted) > 0,
        "accepted_risks": [
            {
                "id": a.get("id"),
                "code": a.get("code"),
                "title": a.get("title"),
                "risk_class": a.get("risk_class") or classify_issue_risk(a),
                "accept_reason": a.get("accept_reason"),
                "accepted_by": a.get("accepted_by"),
                "accepted_at": a.get("accepted_at"),
            }
            for a in accepted[:50]
        ],
        "checks": checks,
        "issues_summary": summary,
        "block_issues": [
            {
                "id": b.get("id"),
                "code": b.get("code"),
                "title": b.get("title"),
                "stage_id": b.get("stage_id"),
                "risk_class": b.get("risk_class") or classify_issue_risk(b),
            }
            for b in blocks[:20]
        ],
        "draft_allowed": True,  # draft.docx may carry unresolved risks
        "final_requires_gates": True,
        "message": message,
    }
