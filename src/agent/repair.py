from __future__ import annotations



"""Minimal repair plans for quality Issues (plan G2)."""





from pathlib import Path

from typing import Any



from agent.issues import load_open_issues, mark_issue_status, record_issue_metric

from agent.root_cause import ROOT_CAUSE_TABLE

from utils import project_root, stringify





def _issue_by_id(root: Path, issue_id: str) -> dict[str, Any] | None:

    for item in load_open_issues(root):

        if str(item.get("id")) == issue_id:

            return item

    return None





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



    # For score points, leave chapter_ids empty -> fix_coverage will resolve

    steps: list[dict[str, Any]] = []

    for action in issue.get("suggested_actions") or table.get("actions") or []:

        if not isinstance(action, dict):

            continue

        a = dict(action)

        params = dict(a.get("params") or {})

        atype = str(a.get("type") or "")

        if atype == "rewrite_chapters" and chapter_ids:

            params["chapter_ids"] = chapter_ids

        if atype == "open_detail":

            params.setdefault("command", issue.get("command") or "")

        a["params"] = params

        steps.append(a)



    revalidate = list(table.get("revalidate") or [])

    if not revalidate and issue.get("command"):

        revalidate = [str(issue.get("command"))]



    # Always end with revalidate of discovering gate if not already

    discover = str(issue.get("command") or "")

    if discover and discover not in revalidate:

        revalidate.append(discover)



    return {

        "ok": True,

        "issue_id": issue_id,

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





def execute_repair_plan(

    root: Path | None,

    issue_id: str,

    *,

    confirm: bool = False,

    dry_run: bool = False,

) -> dict[str, Any]:

    """Execute minimal repair for an issue (requires confirm=True)."""

    root = root or project_root()

    plan = build_repair_plan(root, issue_id)

    if not plan.get("ok"):

        return plan

    if dry_run or not confirm:

        return {

            **plan,

            "executed": False,

            "message": "预览模式，未执行。传入 confirm=true 开始修复。",

        }



    from agent.tool_runtime import invoke



    mark_issue_status(root, issue_id, "in_progress")

    results: list[dict[str, Any]] = []

    ok = True



    for step in plan.get("steps") or []:

        atype = str(step.get("type") or "")

        params = dict(step.get("params") or {})

        if atype in {"upload_evidence", "open_detail", "accept_risk"}:

            results.append({"step": step, "ok": True, "skipped": True, "message": "需人工处理，已跳过自动执行"})

            continue

        if atype == "rewrite_chapters":

            tool = "rewrite_chapters"

            args = {

                "chapter_ids": params.get("chapter_ids") or plan.get("chapter_ids") or [],

                "workers": int(params.get("workers") or 2),

            }

            if not args["chapter_ids"]:

                # no chapters -> try fix_coverage instead

                tool = "fix_coverage"

                args = {"max_chapters": 5, "confirm_execute": True, "rebuild_matrix": True, "max_rounds": 1}

        elif atype == "fix_coverage":

            tool = "fix_coverage"

            args = {

                "max_chapters": int(params.get("max_chapters") or 5),

                "confirm_execute": True,

                "rebuild_matrix": bool(params.get("rebuild_matrix", True)),

                "max_rounds": int(params.get("max_rounds") or 1),

            }

        elif atype == "fix_compliance":

            tool = "fix_compliance"

            args = {

                "confirm_execute": True,

                "sync": True,

                "max_chapters": int(params.get("max_chapters") or 8),

                "rerun_check": False,

            }

        elif atype == "rerun_stage":

            tool = "run_stage"

            args = {"command": params.get("command") or "", "force": True}

            if not args["command"]:

                results.append({"step": step, "ok": False, "message": "缺少 command"})

                ok = False

                continue

        elif atype == "revalidate_gate":

            # handled in revalidate loop

            results.append({"step": step, "ok": True, "skipped": True, "message": "将在重验阶段执行"})

            continue

        else:

            results.append({"step": step, "ok": False, "message": f"未知动作: {atype}"})

            ok = False

            continue



        result = invoke(tool, args, root=root, actor="repair")

        results.append(

            {

                "step": step,

                "tool": tool,

                "args": args,

                "ok": result.ok,

                "summary": result.summary_for_llm,

                "error": result.error.to_dict() if result.error else None,

            }

        )

        if not result.ok:

            ok = False

            break



    revalidate_results: list[dict[str, Any]] = []

    if ok:

        for command in plan.get("revalidate") or []:

            if not command:

                continue

            # force re-run gate stage

            result = invoke("run_stage", {"command": command, "force": True}, root=root, actor="repair")

            revalidate_results.append(

                {

                    "command": command,

                    "ok": result.ok,

                    "summary": result.summary_for_llm,

                    "error": result.error.to_dict() if result.error else None,

                }

            )

            if not result.ok:

                ok = False

                break



    # After revalidate, re-sync issues from reports and mark fixed if this issue code gone

    try:

        from agent.root_cause import sync_issues_from_compliance, sync_issues_from_global_review



        sync_issues_from_global_review(root)

        sync_issues_from_compliance(root)

    except Exception:

        pass



    still = _issue_by_id(root, issue_id)

    # if issue still open with same code after sync, replace_stage may have recreated with new id

    # Mark original fixed only if no open block with same code+stage remains

    remaining_same = [

        i

        for i in load_open_issues(root)

        if str(i.get("status")) in {"open", "in_progress"}

        and str(i.get("code")) == str(plan.get("issue", {}).get("code"))

        and str(i.get("stage_id")) == str(plan.get("issue", {}).get("stage_id"))

    ]

    if ok and not remaining_same:

        mark_issue_status(root, issue_id, "fixed")

        final_status = "fixed"

    else:

        mark_issue_status(root, issue_id, "open" if not ok else "open")

        final_status = "open"



    return {

        "ok": ok,

        "executed": True,

        "issue_id": issue_id,

        "final_status": final_status,

        "plan": plan,

        "step_results": results,

        "revalidate_results": revalidate_results,

        "message": (

            "修复完成，问题已关闭" if final_status == "fixed" else (

                "修复已执行，但问题仍存在或重验未通过，请查看详情" if ok else "修复执行失败"

            )

        ),

    }





def revalidate_gate(root: Path | None, command: str) -> dict[str, Any]:

    root = root or project_root()

    from agent.tool_runtime import invoke



    result = invoke("run_stage", {"command": command, "force": True}, root=root, actor="repair")

    try:

        from agent.root_cause import sync_issues_from_compliance, sync_issues_from_global_review



        if command == "global-review":

            sync_issues_from_global_review(root)

        if command == "compliance-check":

            sync_issues_from_compliance(root)

        if command not in {"global-review", "compliance-check"}:

            sync_issues_from_global_review(root)

            sync_issues_from_compliance(root)

    except Exception:

        pass

    return {

        "ok": result.ok,

        "command": command,

        "summary": result.summary_for_llm,

        "error": result.error.to_dict() if result.error else None,

    }


def execute_repair_batch(
    root: Path | None,
    issue_ids: list[str],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    max_issues: int | None = None,
) -> dict[str, Any]:
    """Execute repairs for multiple issues sequentially."""
    import os

    root = root or project_root()
    ids = [str(x).strip() for x in (issue_ids or []) if str(x).strip()]
    if not ids:
        return {"ok": False, "message": "issue_ids 为空", "results": []}
    limit = max_issues
    if limit is None:
        try:
            limit = max(1, int(os.environ.get("REPAIR_MAX_ISSUES", "5")))
        except ValueError:
            limit = 5
    ids = ids[:limit]

    if dry_run or not confirm:
        plans = [build_repair_plan(root, i) for i in ids]
        return {
            "ok": True,
            "executed": False,
            "issue_ids": ids,
            "plans": plans,
            "message": f"批量预览 {len(ids)} 条问题（confirm=true 后执行）",
        }

    results = []
    ok_count = 0
    for iid in ids:
        one = execute_repair_plan(root, iid, confirm=True, dry_run=False)
        results.append(one)
        if one.get("ok") and one.get("final_status") == "fixed":
            ok_count += 1
        elif one.get("ok") and one.get("executed"):
            ok_count += 1  # executed even if still open after revalidate
    return {
        "ok": ok_count == len(ids),
        "executed": True,
        "issue_ids": ids,
        "results": results,
        "success_count": ok_count,
        "total": len(ids),
        "message": f"批量修复完成：成功 {ok_count}/{len(ids)}",
    }
