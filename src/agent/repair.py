"""Repair planning and grouped execution for quality issues."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from agent.issues import load_open_issues, mark_issue_status, record_issue_metric
from agent.root_cause import ROOT_CAUSE_TABLE
from utils import project_root, stringify


_OPEN_STATUSES = {"open", "in_progress"}
_MANUAL_ACTIONS = {"upload_evidence", "open_detail", "accept_risk"}
ProgressCallback = Callable[[str, dict[str, Any]], None]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_issue_ids(issue_ids: list[str] | None) -> list[str]:
    return _ordered_unique([str(item or "").strip() for item in (issue_ids or [])])


def issue_fingerprint(issue: dict[str, Any] | None) -> str:
    """Return a stable identity based on stage, code, and canonical target.

    Issue ids can be recreated by a gate revalidation.  The fingerprint is
    deliberately independent of those ids and of the order of target ids.
    """

    issue = issue if isinstance(issue, dict) else {}
    target = issue.get("target") if isinstance(issue.get("target"), dict) else {}
    target_ids = sorted(
        {
            stringify(item).strip()
            for item in (target.get("ids") or [])
            if stringify(item).strip()
        }
    )
    identity = [
        str(issue.get("stage_id") or "").strip(),
        str(issue.get("code") or "").strip(),
        {
            "type": str(target.get("type") or "global").strip(),
            "ids": target_ids,
        },
    ]
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _issue_by_id(root: Path, issue_id: str) -> dict[str, Any] | None:
    for item in load_open_issues(root):
        if str(item.get("id")) == issue_id:
            return item
    return None


def _open_issue_map(root: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for issue in load_open_issues(root):
        if str(issue.get("status") or "open") not in _OPEN_STATUSES:
            continue
        result.setdefault(issue_fingerprint(issue), []).append(issue)
    return result


def build_repair_plan(root: Path | None, issue_id: str) -> dict[str, Any]:
    """Preview a minimal repair plan for one issue (no side effects)."""

    root = root or project_root()
    issue = _issue_by_id(root, issue_id)
    if not issue:
        return {"ok": False, "message": f"未找到问题: {issue_id}"}

    code = str(issue.get("code") or "")
    table = ROOT_CAUSE_TABLE.get(code) or {}
    target = issue.get("target") if isinstance(issue.get("target"), dict) else {}
    target_ids = [stringify(x) for x in (target.get("ids") or []) if stringify(x)]
    chapter_ids = target_ids if str(target.get("type")) == "chapter" else []

    # suggested_actions intentionally take precedence over the root-cause defaults.
    steps: list[dict[str, Any]] = []
    for action in issue.get("suggested_actions") or table.get("actions") or []:
        if not isinstance(action, dict):
            continue
        normalized = dict(action)
        params = dict(normalized.get("params") or {})
        action_type = str(normalized.get("type") or "")
        if action_type == "rewrite_chapters" and chapter_ids:
            params["chapter_ids"] = chapter_ids
        if action_type == "open_detail":
            params.setdefault("command", issue.get("command") or "")
        normalized["params"] = params
        steps.append(normalized)

    revalidate = [str(item) for item in (table.get("revalidate") or []) if str(item)]
    if not revalidate and issue.get("command"):
        revalidate = [str(issue.get("command"))]

    # A suggested revalidation action may be more specific than the default table.
    for step in steps:
        if str(step.get("type") or "") != "revalidate_gate":
            continue
        command = str((step.get("params") or {}).get("command") or "").strip()
        if command:
            revalidate.append(command)

    discover = str(issue.get("command") or "")
    if discover:
        revalidate.append(discover)
    revalidate = _ordered_unique(revalidate)

    fingerprint = issue_fingerprint(issue)
    return {
        "ok": True,
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "issue": {
            "id": issue.get("id"),
            "code": issue.get("code"),
            "title": issue.get("title"),
            "severity": issue.get("severity"),
            "stage_id": issue.get("stage_id"),
            "command": issue.get("command"),
            "likely_cause_stage": issue.get("likely_cause_stage"),
            "target": target,
        },
        "steps": steps,
        "revalidate": revalidate,
        "chapter_ids": chapter_ids,
        "summary": (
            f"修复「{issue.get('title')}」：建议动作 {len(steps)} 步，"
            f"重验 {', '.join(revalidate) or '无'}。"
        ),
    }


def _merge_params(current: dict[str, Any], incoming: dict[str, Any]) -> None:
    maximum_keys = {"max_chapters", "max_rounds", "workers", "max_retries"}
    boolean_keys = {"rebuild_matrix", "sync", "rerun_check", "confirm_execute"}
    for key, value in incoming.items():
        if key == "chapter_ids":
            current[key] = _ordered_unique(
                [str(x) for x in (current.get(key) or [])]
                + [str(x) for x in (value or [])]
            )
        elif key in maximum_keys:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            try:
                previous = int(current.get(key))
            except (TypeError, ValueError):
                previous = 0
            current[key] = max(previous, number)
        elif key in boolean_keys:
            if key not in current:
                current[key] = bool(value)
            else:
                current[key] = bool(current[key]) or bool(value)
        elif key not in current:
            current[key] = value


def build_repair_batch_plan(
    root: Path | None,
    issue_ids: list[str],
    *,
    max_issues: int | None = None,
) -> dict[str, Any]:
    """Build one grouped, de-duplicated plan for the selected issues."""

    root = root or project_root()
    ids = _normalize_issue_ids(issue_ids)
    if max_issues is not None:
        ids = ids[: max(1, int(max_issues))]
    if not ids:
        return {
            "ok": False,
            "message": "issue_ids 为空",
            "issue_ids": [],
            "plans": [],
            "groups": [],
            "actions": [],
            "revalidate": [],
            "errors": [],
        }

    snapshot = {str(item.get("id")): item for item in load_open_issues(root)}
    records: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for issue_id in ids:
        issue = snapshot.get(issue_id)
        if issue is None:
            error = {"ok": False, "issue_id": issue_id, "message": f"未找到问题: {issue_id}"}
            plans.append(error)
            errors.append(error)
            continue
        plan = build_repair_plan(root, issue_id)
        plans.append(plan)
        if not plan.get("ok"):
            errors.append(plan)
            continue
        code = str(issue.get("code") or "")
        cause = str(
            issue.get("likely_cause_stage")
            or (ROOT_CAUSE_TABLE.get(code) or {}).get("likely_cause_stage")
            or issue.get("stage_id")
            or "unknown"
        )
        records.append(
            {
                "issue_id": issue_id,
                "issue": issue,
                "fingerprint": str(plan.get("fingerprint") or issue_fingerprint(issue)),
                "root_cause_stage": cause,
                "plan": plan,
            }
        )

    # Dict insertion order makes group and action ordering deterministic.
    grouped_records: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped_records.setdefault(record["root_cause_stage"], []).append(record)

    actions: list[dict[str, Any]] = []
    action_index: dict[tuple[str, ...], dict[str, Any]] = {}
    group_rows: list[dict[str, Any]] = []
    all_revalidate: list[str] = []

    for cause, cause_records in grouped_records.items():
        group_action_ids: list[str] = []
        group_revalidate: list[str] = []
        for record in cause_records:
            plan = record["plan"]
            issue_id = record["issue_id"]
            fingerprint = record["fingerprint"]
            plan_steps = plan.get("steps") or []
            has_group_root_fix = any(
                str(item.get("type") or "") in {"fix_coverage", "fix_compliance"}
                for item in plan_steps
                if isinstance(item, dict)
            )
            for command in plan.get("revalidate") or []:
                command = str(command or "").strip()
                if command:
                    group_revalidate.append(command)
                    all_revalidate.append(command)

            for step in plan_steps:
                action_type = str(step.get("type") or "").strip()
                params = dict(step.get("params") or {})
                if action_type == "revalidate_gate":
                    continue

                target_ids: list[str] = []
                fallback_from = ""
                if action_type == "rewrite_chapters":
                    target_ids = _ordered_unique(
                        [str(x) for x in (params.get("chapter_ids") or plan.get("chapter_ids") or [])]
                    )
                    if target_ids:
                        key = ("rewrite_chapters",)
                        params["chapter_ids"] = target_ids
                    elif has_group_root_fix:
                        # Coverage/compliance helpers select concrete chapters;
                        # do not add a second unscoped rewrite action.
                        continue
                    else:
                        # An unscoped rewrite is not safe to guess. Keep the issue
                        # visible as manual instead of converting it to another fix.
                        fallback_from = "rewrite_chapters"
                        action_type = "open_detail"
                        params.pop("chapter_ids", None)
                        params.setdefault("reason", "missing_explicit_chapter_target")
                        key = ("manual", fingerprint, action_type)
                elif action_type in {"fix_coverage", "fix_compliance"}:
                    key = (action_type,)
                elif action_type == "rerun_stage":
                    key = (action_type, str(params.get("command") or "").strip())
                elif action_type in _MANUAL_ACTIONS:
                    key = ("manual", fingerprint, action_type)
                else:
                    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
                    key = (action_type or "unknown", encoded)

                action = action_index.get(key)
                if action is None:
                    action = {
                        "action_id": f"action_{len(actions) + 1}",
                        "type": action_type,
                        "params": {},
                        "target_ids": [],
                        "issue_ids": [],
                        "fingerprints": [],
                        "root_cause_stages": [],
                        "source_types": [],
                        "source_steps": [],
                        "manual": action_type in _MANUAL_ACTIONS,
                    }
                    action_index[key] = action
                    actions.append(action)
                _merge_params(action["params"], params)
                action["target_ids"] = _ordered_unique(action["target_ids"] + target_ids)
                action["issue_ids"] = _ordered_unique(action["issue_ids"] + [issue_id])
                action["fingerprints"] = _ordered_unique(action["fingerprints"] + [fingerprint])
                action["root_cause_stages"] = _ordered_unique(
                    action["root_cause_stages"] + [cause]
                )
                source_type = fallback_from or str(step.get("type") or "")
                action["source_types"] = _ordered_unique(action["source_types"] + [source_type])
                action["source_steps"].append(step)
                group_action_ids.append(action["action_id"])

        group_rows.append(
            {
                "root_cause_stage": cause,
                "issue_ids": [record["issue_id"] for record in cause_records],
                "fingerprints": [record["fingerprint"] for record in cause_records],
                "action_ids": _ordered_unique(group_action_ids),
                "revalidate": _ordered_unique(group_revalidate),
            }
        )

    # Ensure the merged chapter list is the only rewrite target list used.
    for action in actions:
        if action["type"] == "rewrite_chapters":
            action["params"]["chapter_ids"] = list(action["target_ids"])

    return {
        "ok": not errors,
        "issue_ids": ids,
        "plans": plans,
        "records": records,
        "groups": group_rows,
        "actions": actions,
        "revalidate": _ordered_unique(all_revalidate),
        "errors": errors,
        "message": (
            f"已生成 {len(records)} 条问题的分组修复计划："
            f"{len(group_rows)} 个根因组，{len(actions)} 个去重动作。"
        ),
    }


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(phase, payload)
    except Exception:
        # Progress persistence must never turn a repair into a failed repair.
        pass


def _invoke_result_row(result: Any) -> dict[str, Any]:
    error = getattr(result, "error", None)
    if error is not None and hasattr(error, "to_dict"):
        error = error.to_dict()
    return {
        "ok": bool(getattr(result, "ok", False)),
        "summary": str(getattr(result, "summary_for_llm", "") or ""),
        "error": error,
    }


def _execute_action(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    from agent.tool_runtime import invoke

    action_type = str(action.get("type") or "")
    params = dict(action.get("params") or {})
    base = {
        "action_id": action.get("action_id"),
        "type": action_type,
        "issue_ids": list(action.get("issue_ids") or []),
        "fingerprints": list(action.get("fingerprints") or []),
        "root_cause_stages": list(action.get("root_cause_stages") or []),
        "source_types": list(action.get("source_types") or []),
    }

    if action_type in _MANUAL_ACTIONS:
        return {
            **base,
            "ok": True,
            "skipped": True,
            "manual": True,
            "message": "需人工处理，未自动执行",
        }

    if action_type == "rewrite_chapters":
        tool = "rewrite_chapters"
        chapter_ids = _ordered_unique(
            [str(x) for x in (action.get("target_ids") or params.get("chapter_ids") or [])]
        )
        if not chapter_ids:
            return {**base, "ok": False, "message": "缺少 chapter_ids"}
        args = {
            "chapter_ids": chapter_ids,
            "workers": _positive_int(params.get("workers"), 2),
        }
    elif action_type == "fix_coverage":
        tool = "fix_coverage"
        args = {
            "max_chapters": _positive_int(params.get("max_chapters"), 10_000),
            "confirm_execute": True,
            "rebuild_matrix": bool(params.get("rebuild_matrix", True)),
            "max_rounds": _positive_int(params.get("max_rounds"), 3),
        }
        if params.get("workers") is not None:
            args["workers"] = _positive_int(params.get("workers"), 2)
    elif action_type == "fix_compliance":
        tool = "fix_compliance"
        args = {
            "confirm_execute": True,
            "sync": bool(params.get("sync", True)),
            "max_chapters": _positive_int(params.get("max_chapters"), 10_000),
            # Revalidation is globally de-duplicated after every edit.
            "rerun_check": False,
        }
        if params.get("workers") is not None:
            args["workers"] = _positive_int(params.get("workers"), 2)
    elif action_type == "rerun_stage":
        tool = "run_stage"
        command = str(params.get("command") or "").strip()
        if not command:
            return {**base, "ok": False, "message": "缺少 command"}
        args = {"command": command, "force": True}
    else:
        return {**base, "ok": False, "message": f"未知动作: {action_type}"}

    try:
        result = invoke(tool, args, root=root, actor="repair")
        return {**base, "tool": tool, "args": args, **_invoke_result_row(result)}
    except Exception as exc:  # noqa: BLE001 - turn tool crashes into a per-issue failure
        return {
            **base,
            "tool": tool,
            "args": args,
            "ok": False,
            "summary": "",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def _run_revalidations(
    root: Path,
    commands: list[str],
    *,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    from agent.tool_runtime import invoke

    results: list[dict[str, Any]] = []
    unique_commands = _ordered_unique(commands)
    _emit_progress(
        progress_callback,
        "revalidate",
        {"completed": 0, "total": len(unique_commands), "commands": unique_commands},
    )
    for index, command in enumerate(unique_commands, start=1):
        try:
            result = invoke(
                "run_stage",
                {"command": command, "force": True},
                root=root,
                actor="repair",
            )
            row = {"command": command, **_invoke_result_row(result)}
            results.append(row)
        except Exception as exc:  # noqa: BLE001
            row = {
                "command": command,
                "ok": False,
                "summary": "",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
            results.append(row)
        _emit_progress(
            progress_callback,
            "revalidate",
            {
                "completed": index,
                "total": len(unique_commands),
                "command": command,
                "ok": bool(row.get("ok")),
                "commands": unique_commands,
            },
        )
    return results


def _sync_gate_issues(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        from agent.root_cause import sync_issues_from_compliance, sync_issues_from_global_review

        try:
            sync_issues_from_global_review(root)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"global-review: {type(exc).__name__}: {exc}")
        try:
            sync_issues_from_compliance(root)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"compliance-check: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sync import: {type(exc).__name__}: {exc}")
    return errors


def _classification_message(classification: str) -> str:
    return {
        "resolved": "修复完成，重验后问题已关闭",
        "still_open": "修复已执行，但重验后问题仍存在",
        "manual": "重验后问题仍存在，且包含需人工处理的动作",
        "failed": "修复动作或重验执行失败，问题仍未关闭",
    }.get(classification, classification)


def execute_repair_plan(
    root: Path | None,
    issue_id: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute one issue through the same grouped engine used for batches."""

    root = root or project_root()
    if dry_run or not confirm:
        plan = build_repair_plan(root, issue_id)
        if not plan.get("ok"):
            return plan
        return {
            **plan,
            "executed": False,
            "message": "预览模式，未执行。传入 confirm=true 开始修复。",
        }

    batch = execute_repair_batch(
        root,
        [issue_id],
        confirm=True,
        dry_run=False,
        max_issues=1,
        progress_callback=progress_callback,
    )
    results = batch.get("results") or []
    if not results:
        return {
            "ok": False,
            "executed": True,
            "issue_id": issue_id,
            "final_status": "open",
            "classification": "failed",
            "message": str(batch.get("message") or f"未找到问题: {issue_id}"),
            "batch": batch,
        }
    return {**results[0], "batch": batch}


def revalidate_gate(root: Path | None, command: str) -> dict[str, Any]:
    root = root or project_root()
    rows = _run_revalidations(root, [command])
    sync_errors = _sync_gate_issues(root)
    row = rows[0] if rows else {"ok": False, "command": command, "summary": "", "error": None}
    if sync_errors:
        row = {**row, "sync_errors": sync_errors}
    return row


def _preview_issue_limit(max_issues: int | None) -> int | None:
    if max_issues is not None:
        return max(1, int(max_issues))
    try:
        return max(1, int(os.environ.get("REPAIR_MAX_ISSUES", "5")))
    except ValueError:
        return 5


def execute_repair_batch(
    root: Path | None,
    issue_ids: list[str],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    max_issues: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute a grouped repair batch, then revalidate and classify once.

    Confirmed batches have no implicit five-issue cap.  ``max_issues`` remains
    available as an explicit caller guard; unconfirmed previews retain the
    configurable preview cap for compatibility.
    """

    root = root or project_root()
    requested_ids = _normalize_issue_ids(issue_ids)
    if not requested_ids:
        return {"ok": False, "message": "issue_ids 为空", "results": []}

    requested_count = len(requested_ids)
    preview = dry_run or not confirm
    limit = _preview_issue_limit(max_issues) if preview else (
        max(1, int(max_issues)) if max_issues is not None else None
    )
    selected_ids = requested_ids[:limit] if limit is not None else requested_ids
    truncated = len(selected_ids) < requested_count
    batch_plan = build_repair_batch_plan(root, selected_ids)
    _emit_progress(
        progress_callback,
        "analysis",
        {
            "completed": len(batch_plan.get("records") or []),
            "total": len(selected_ids),
            "group_count": len(batch_plan.get("groups") or []),
            "action_count": len(batch_plan.get("actions") or []),
            "revalidate_count": len(batch_plan.get("revalidate") or []),
        },
    )

    if preview:
        return {
            "ok": bool(batch_plan.get("ok")),
            "executed": False,
            "issue_ids": selected_ids,
            "requested_count": requested_count,
            "limit": limit,
            "truncated": truncated,
            "plans": batch_plan.get("plans") or [],
            "groups": batch_plan.get("groups") or [],
            "actions": batch_plan.get("actions") or [],
            "revalidate": batch_plan.get("revalidate") or [],
            "errors": batch_plan.get("errors") or [],
            "message": (
                f"已预览 {len(selected_ids)}/{requested_count} 条问题（confirm=true 后执行）"
                + (f"；预览上限为 {limit} 条。" if truncated else "")
            ),
        }

    records = list(batch_plan.get("records") or [])
    for record in records:
        mark_issue_status(root, record["issue_id"], "in_progress")

    # Actions are already ordered by root-cause group and de-duplicated globally.
    planned_actions = list(batch_plan.get("actions") or [])
    action_results: list[dict[str, Any]] = []
    _emit_progress(
        progress_callback,
        "edit",
        {"completed": 0, "total": len(planned_actions)},
    )
    for index, action in enumerate(planned_actions, start=1):
        row = _execute_action(root, action)
        action_results.append(row)
        _emit_progress(
            progress_callback,
            "edit",
            {
                "completed": index,
                "total": len(planned_actions),
                "action_id": action.get("action_id"),
                "action_type": action.get("type"),
                "ok": bool(row.get("ok")),
                "manual": bool(row.get("manual")),
            },
        )

    # Every discovering gate is forced exactly once, after all edit attempts.
    revalidate_results = _run_revalidations(
        root,
        list(batch_plan.get("revalidate") or []),
        progress_callback=progress_callback,
    )
    sync_errors = _sync_gate_issues(root)
    post_by_fingerprint = _open_issue_map(root)

    failed_action_fingerprints: set[str] = set()
    manual_fingerprints: set[str] = set()
    for row in action_results:
        fingerprints = {str(item) for item in (row.get("fingerprints") or [])}
        if row.get("manual"):
            manual_fingerprints.update(fingerprints)
        elif not row.get("ok"):
            failed_action_fingerprints.update(fingerprints)

    failed_commands = {
        str(row.get("command"))
        for row in revalidate_results
        if not row.get("ok") and row.get("command")
    }

    results: list[dict[str, Any]] = []
    classification_ids: dict[str, list[str]] = {
        "resolved": [],
        "still_open": [],
        "manual": [],
        "failed": [],
    }
    record_by_id = {record["issue_id"]: record for record in records}
    plan_by_id = {
        str(plan.get("issue_id")): plan
        for plan in (batch_plan.get("plans") or [])
        if isinstance(plan, dict) and plan.get("issue_id")
    }

    for issue_id in selected_ids:
        record = record_by_id.get(issue_id)
        if record is None:
            classification = "failed"
            fingerprint = ""
            plan = plan_by_id.get(issue_id) or {
                "ok": False,
                "issue_id": issue_id,
                "message": f"未找到问题: {issue_id}",
            }
            issue_action_results: list[dict[str, Any]] = []
            issue_revalidate_results: list[dict[str, Any]] = []
        else:
            fingerprint = record["fingerprint"]
            plan = record["plan"]
            issue_action_results = [
                row for row in action_results if issue_id in (row.get("issue_ids") or [])
            ]
            plan_commands = set(plan.get("revalidate") or [])
            issue_revalidate_results = [
                row for row in revalidate_results if row.get("command") in plan_commands
            ]

            # Post-revalidation state is authoritative. A failed helper does not
            # make a genuinely closed fingerprint fail, while an open fingerprint
            # can never be reported as a success merely because tools ran.
            if fingerprint not in post_by_fingerprint:
                classification = "resolved"
            # An evidence-upload/open-detail action is deliberately not run by
            # the automated repairer.  A later gate can fail because that
            # evidence is still absent, but that must remain an actionable
            # manual item instead of being reported as a repair execution
            # failure for every issue sharing the gate.
            elif fingerprint in manual_fingerprints:
                classification = "manual"
            elif fingerprint in failed_action_fingerprints or (plan_commands & failed_commands):
                classification = "failed"
            else:
                classification = "still_open"

        classification_ids[classification].append(issue_id)
        final_status = "fixed" if classification == "resolved" else "open"
        ok = classification == "resolved"
        result = {
            "ok": ok,
            "executed": True,
            "issue_id": issue_id,
            "fingerprint": fingerprint,
            "classification": classification,
            "final_status": final_status,
            "plan": plan,
            "step_results": issue_action_results,
            "revalidate_results": issue_revalidate_results,
            "message": _classification_message(classification),
        }
        results.append(result)

        # Restore still-open issues from in_progress and keep recreated ids open.
        if classification == "resolved":
            mark_issue_status(root, issue_id, "fixed")
        else:
            mark_issue_status(root, issue_id, "open")
            for post_issue in post_by_fingerprint.get(fingerprint, []):
                post_id = str(post_issue.get("id") or "")
                if post_id:
                    mark_issue_status(root, post_id, "open")

        try:
            record_issue_metric(
                root,
                "repair_success" if ok else "repair_fail",
                issue_id=issue_id,
                fingerprint=fingerprint,
                classification=classification,
            )
        except Exception:
            pass

    resolved_count = len(classification_ids["resolved"])
    total = len(selected_ids)
    attempted_automatic = any(
        not row.get("manual") and not row.get("skipped") for row in action_results
    )
    valid_fingerprints = [record["fingerprint"] for record in records]
    no_progress = bool(
        attempted_automatic
        and valid_fingerprints
        and resolved_count == 0
        and all(fingerprint in post_by_fingerprint for fingerprint in valid_fingerprints)
    )
    all_resolved = total > 0 and resolved_count == total

    response = {
        "ok": all_resolved,
        "executed": True,
        "issue_ids": selected_ids,
        "requested_count": requested_count,
        "limit": limit,
        "truncated": truncated,
        "groups": batch_plan.get("groups") or [],
        "actions": batch_plan.get("actions") or [],
        "step_results": action_results,
        "revalidate_results": revalidate_results,
        "revalidated_commands": [
            str(row.get("command")) for row in revalidate_results if row.get("command")
        ],
        "sync_errors": sync_errors,
        "results": results,
        "success_count": resolved_count,
        "resolved_count": resolved_count,
        "still_open_count": len(classification_ids["still_open"]),
        "manual_count": len(classification_ids["manual"]),
        "failed_count": len(classification_ids["failed"]),
        "total": total,
        "resolved": classification_ids["resolved"],
        "still_open": classification_ids["still_open"],
        "manual": classification_ids["manual"],
        "failed": classification_ids["failed"],
        "classifications": classification_ids,
        "no_progress": no_progress,
        "message": (
            f"批量修复完成：已解决 {resolved_count}/{total}，"
            f"仍未解决 {len(classification_ids['still_open'])}，"
            f"需人工 {len(classification_ids['manual'])}，"
            f"失败 {len(classification_ids['failed'])}"
            + ("；检测到无进展，已停止继续尝试" if no_progress else "")
        ),
    }
    _emit_progress(
        progress_callback,
        "complete",
        {
            "completed": total,
            "total": total,
            "resolved_count": response["resolved_count"],
            "still_open_count": response["still_open_count"],
            "manual_count": response["manual_count"],
            "failed_count": response["failed_count"],
            "revalidated_commands": response["revalidated_commands"],
            "no_progress": response["no_progress"],
        },
    )
    return response
