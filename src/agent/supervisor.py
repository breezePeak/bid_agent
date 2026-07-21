from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from agent.budgets import (
    AgentBudget,
    criteria_fingerprint,
    issues_fingerprint,
    max_steps_budget,
    observation_max_chars,
)
from agent.flags import agent_supervisor_enabled
from agent.goal import (
    confirmation_allows,
    create_goal,
    explicit_resume_intent,
    grant_confirmation,
    handle_plan_step_result,
    infer_goal_from_message,
    load_goal,
    mark_plan_step,
    next_plan_step,
    plan_has_open_steps,
    reevaluate_goal,
    resume_goal_after_materials,
    set_goal_status,
)
from agent.policy import evaluate_tool_call, is_readonly_tool
from agent.snapshot import build_snapshot, human_blocking_reason
from agent.tool_registry import tool_manifest
from agent.tool_runtime import invoke
from agent.trace import append_decision, max_steps_default, new_trace_id, save_last_plan
from utils import project_root


_SUPERVISOR_SYSTEM = """你是标书系统的 Supervisor Agent（多步闭环）。
你只能通过选择已注册 tool 推进目标，不能发明新阶段。

## 可用 tools（JSON）
{tools}

## 输出
只输出一个 JSON 对象，不要 Markdown 代码块，字段：
- thought_summary: 一句话说明为什么选这个 tool（给用户看）
- tool: tool 名（必须来自可用列表；若无需 tool 填 ""）
- args: 对象（无参数用 {{}}）
- reply: 给用户的中文回复
- done: 布尔（仅建议；系统会按 GoalState/预算覆盖）
- need_confirm: 布尔，是否需要用户确认后再执行变更

## 规则
1. 优先推进 plan 中的下一步；已完成且未失效的步骤不要重复。
2. 用户问状态/进度/为什么失败 → 优先 query_status 或 diagnose_failure。
3. 禁止编造产物状态；只根据 snapshot 判断。
4. 每轮只选 0 或 1 个 tool；系统会在 Goal 未完成时自动继续。
5. 高风险导出（build_export / build-docx）默认 need_confirm=true。
6. 若 Goal 已 succeeded，tool="" 且 done=true。
7. 若缺少人工材料，tool=""，说明缺什么。
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _rule_based_decision(
    message: str,
    snapshot: dict[str, Any],
    *,
    root: Path | None = None,
    goal: dict[str, Any] | None = None,
    prefer_plan: bool = True,
) -> dict[str, Any]:
    plan_step = None
    # PR-3: only next_plan_step() may select plan steps (enforces depends_on)
    if prefer_plan and goal and isinstance(goal.get("plan"), list) and goal.get("plan"):
        try:
            from utils import project_root as _pr

            plan_root = root or _pr()
            plan_step = next_plan_step(plan_root, goal)
        except Exception:
            plan_step = None

    if plan_step and str(plan_step.get("tool") or "").strip():
        tool = str(plan_step.get("tool") or "")
        args = dict(plan_step.get("args") or {})
        need_confirm = not is_readonly_tool(tool)
        return {
            "thought_summary": f"按计划执行：{plan_step.get('label') or plan_step.get('step_id') or tool}",
            "tool": tool,
            "args": args,
            "reply": f"按目标计划执行 {plan_step.get('label') or tool}。",
            "done": False,
            "need_confirm": need_confirm,
            "plan_step_id": plan_step.get("step_id"),
        }

    # diagnose
    if any(k in message for k in ("诊断", "失败", "错误", "为啥挂", "怎么修")):
        return {
            "thought_summary": "用户在排查失败，先做只读诊断",
            "tool": "diagnose_failure",
            "args": {},
            "reply": "我先汇总当前失败信息。",
            "done": False,
            "need_confirm": False,
        }
    if any(k in message for k in ("合规", "废标", "blocking", "专项合规", "合规检查失败")):
        if any(k in message for k in ("改", "修", "补", "处理", "修复", "改稿")):
            return {
                "thought_summary": "用户要求按合规失败项改稿，给出 fix_compliance 计划",
                "tool": "fix_compliance",
                "args": {"confirm_execute": False, "sync": True},
                "reply": "我将根据合规报告生成定向改稿计划（确认后执行）。",
                "done": False,
                "need_confirm": True,
            }
        return {
            "thought_summary": "用户询问合规问题，执行只读分析",
            "tool": "analyze_compliance",
            "args": {"sync": True},
            "reply": "我先分析当前合规报告与可改写项。",
            "done": False,
            "need_confirm": False,
        }

    if any(k in message for k in ("覆盖率", "评分点未覆盖", "未覆盖评分", "补齐评分", "覆盖缺口", "评分覆盖", "补齐所有可自动")):
        if any(k in message for k in ("改", "修", "补", "处理", "修复", "补齐")):
            return {
                "thought_summary": "用户要求按覆盖缺口改稿，先分析再修复",
                "tool": "analyze_coverage",
                "args": {"rebuild": True, "max_chapters": 5},
                "reply": "我先分析评分覆盖缺口，再规划定向改稿。",
                "done": False,
                "need_confirm": False,
            }
        return {
            "thought_summary": "用户询问评分覆盖，执行只读分析",
            "tool": "analyze_coverage",
            "args": {"rebuild": True, "max_chapters": 5},
            "reply": "我先分析当前评分点覆盖情况。",
            "done": False,
            "need_confirm": False,
        }

    if any(k in message for k in ("状态", "进度", "到哪了", "现在怎样", "当前")):
        return {
            "thought_summary": "用户询问状态，查询进度快照",
            "tool": "query_status",
            "args": {"view": "summary"},
            "reply": "我先查看当前流水线进度。",
            "done": False,
            "need_confirm": False,
        }
    # targeted rewrite / write
    if any(k in message for k in ("改第", "重写第", "只写第", "定向改", "改稿")):
        import re as _re

        ids = _re.findall(r"\d+(?:\.\d+)*", message)
        chapter_ids = [i for i in ids if i][:10]
        tool = "rewrite_chapters" if any(k in message for k in ("改", "重写", "改稿")) else "write_chapters"
        if "只写" in message or "生成第" in message:
            tool = "write_chapters"
        args = {"chapter_ids": chapter_ids} if chapter_ids else {}
        return {
            "thought_summary": f"用户要求定向章节操作，建议 {tool}",
            "tool": tool,
            "args": args,
            "reply": f"将对章节 {chapter_ids or '（需你指定 id）'} 执行 {tool}，确认后执行。",
            "done": False,
            "need_confirm": True,
        }

    if any(k in message for k in ("出 Word", "生成 Word", "导出", "出稿", "build_export", "final.docx")):
        return {
            "thought_summary": "用户要求导出终稿，使用 build_export（含 stale 重建）",
            "tool": "build_export",
            "args": {"targets": ["md", "docx", "format"]},
            "reply": "将导出 Markdown/Word（若终稿已失效会先强制重建）。请确认后执行。",
            "done": False,
            "need_confirm": True,
        }

    # explicit run next
    if any(k in message for k in ("继续", "下一步", "执行下一步", "开始跑", "重试", "确认执行", "确认")):
        next_step = snapshot.get("pipeline", {}).get("next_step") if isinstance(snapshot.get("pipeline"), dict) else None
        if not next_step:
            next_step = snapshot.get("next_step") if isinstance(snapshot.get("next_step"), dict) else None
        command = str((next_step or {}).get("command") or "")
        if command:
            return {
                "thought_summary": f"用户要求继续，建议执行 {command}",
                "tool": "run_stage",
                "args": {"command": command},
                "reply": f"下一步是 `{command}`。确认后我可以执行。",
                "done": False,
                "need_confirm": True,
            }
    return {
        "thought_summary": "无明确 tool 意图，仅对话回复",
        "tool": "",
        "args": {},
        "reply": "我可以帮你查看状态、诊断失败，或在你确认后执行修复/导出计划。",
        "done": True,
        "need_confirm": False,
    }


def _llm_decision(
    message: str,
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    llm_chat: Callable[..., str] | None,
    *,
    budget: AgentBudget | None = None,
) -> dict[str, Any]:
    tools = tool_manifest()
    compact_tools = []
    for item in tools:
        if item["name"] in {
            "run_stage",
            "run_pipeline_remaining",
            "query_status",
            "query_artifacts",
            "diagnose_failure",
            "analyze_coverage",
            "analyze_compliance",
            "fix_coverage",
            "fix_compliance",
            "build_export",
            "export_preflight",
            "list_issues",
        } or item.get("kind") == "meta":
            compact_tools.append(
                {
                    "name": item["name"],
                    "label": item["label"],
                    "description": item["description"],
                    "params_schema": item.get("params_schema") or {},
                }
            )
        else:
            compact_tools.append({"name": item["name"], "label": item["label"], "command": item.get("command")})

    system = _SUPERVISOR_SYSTEM.replace("{tools}", json.dumps(compact_tools, ensure_ascii=False)[:12000])
    hist = []
    for item in (history or [])[-6:]:
        if not isinstance(item, dict):
            continue
        hist.append({"role": item.get("role"), "content": str(item.get("content") or "")[:500]})
    user = json.dumps(
        {
            "message": message,
            "snapshot": snapshot,
            "history": hist,
        },
        ensure_ascii=False,
    )
    reasoning = ""
    if budget is not None:
        budget.record_llm_call()
    if llm_chat is None:
        from llm_client import chat_with_meta

        meta = chat_with_meta(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
        )
        raw = meta.get("content") or ""
        reasoning = str(meta.get("reasoning") or "").strip()
    else:
        raw = llm_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
        )
        if isinstance(raw, dict):
            reasoning = str(raw.get("reasoning") or "").strip()
            raw = raw.get("content") or ""
    data = _extract_json(raw)
    if not data:
        raise ValueError("supervisor LLM 未返回合法 JSON")
    if reasoning:
        data["_reasoning"] = reasoning
    return data


def _normalize_decision(data: dict[str, Any]) -> dict[str, Any]:
    tool = str(data.get("tool") or "").strip()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    return {
        "thought_summary": str(data.get("thought_summary") or "").strip() or "（无摘要）",
        "tool": tool,
        "args": args,
        "reply": str(data.get("reply") or "").strip(),
        "done": bool(data.get("done", False)),
        "need_confirm": bool(data.get("need_confirm", False)),
        "plan_step_id": str(data.get("plan_step_id") or "").strip(),
    }


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Public wrapper for adapters (PR-A2)."""
    return _normalize_decision(raw)


def decide_next_step(
    message: str,
    snapshot: dict[str, Any],
    *,
    root: Path | None = None,
    goal: dict[str, Any] | None = None,
    prefer_plan: bool = True,
    use_llm: bool = False,
    history: list[dict[str, Any]] | None = None,
    llm_chat: Callable[..., str] | None = None,
    budget: AgentBudget | None = None,
) -> dict[str, Any]:
    """Public single-step decision for adapters/tests. Prefer run_supervisor_turn for full loops."""
    if use_llm:
        try:
            raw = _llm_decision(message, snapshot, history or [], llm_chat, budget=budget)
        except Exception:
            raw = _rule_based_decision(
                message, snapshot, root=root, goal=goal, prefer_plan=prefer_plan
            )
    else:
        raw = _rule_based_decision(
            message, snapshot, root=root, goal=goal, prefer_plan=prefer_plan
        )
    return _normalize_decision(raw)


def _terminal_payload(
    *,
    terminal_status: str,
    reply: str,
    actions: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    goal: dict[str, Any] | None,
    goal_id: str,
    budget: AgentBudget,
    reasoning_parts: list[str],
    recommended_actions: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "reply": reply,
        "actions": actions,
        "steps": steps,
        "goal_id": goal_id,
        "goal": {
            "status": (goal or {}).get("status") or terminal_status,
            "all_criteria_ok": (goal or {}).get("all_criteria_ok"),
            "criteria_results": (goal or {}).get("criteria_results") or [],
            "blocked_reason": (goal or {}).get("blocked_reason") or "",
            "plan": (goal or {}).get("plan") or [],
            "current_plan_index": (goal or {}).get("current_plan_index", 0),
            "progress": (goal or {}).get("progress") or {},
            "raw_user_goal": (goal or {}).get("raw_user_goal") or "",
            "confirmation_scope": (goal or {}).get("confirmation_scope") or {},
        },
        "terminal_status": terminal_status,
        "budget": budget.to_dict(),
        "recommended_actions": recommended_actions or [],
        "supervisor": True,
        "action": "chat",
        "auto_execute": False,
        "intent": "supervisor_turn",
    }
    thinking = "\n\n".join(part for part in reasoning_parts if part).strip()
    if thinking:
        payload["thinking"] = thinking
    return payload


def run_supervisor_turn(
    message: str,
    *,
    root: Path | None = None,
    status: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    llm_chat: Callable[..., str] | None = None,
    use_llm: bool = True,
    auto_execute_readonly: bool = True,
    user_confirmed: bool = False,
    max_steps: int | None = None,
    confirmed_tools: list[str] | None = None,
) -> dict[str, Any]:
    """Multi-step observe → decide → policy → invoke → reevaluate loop (PR-9)."""
    root = root or project_root()
    history = history or []
    status = status or {}

    budget = AgentBudget(max_steps=max_steps if max_steps is not None else max_steps_budget())
    # keep soft upper bound from legacy callers
    if max_steps is None:
        try:
            budget.max_steps = max(budget.max_steps, min(max_steps_default(), budget.max_steps))
        except Exception:
            pass

    try:
        from agent.goal_compiler import compile_goal_from_message

        inferred = compile_goal_from_message(
            message,
            root=root,
            llm_chat=llm_chat,
            use_llm=use_llm,
        )
    except Exception:
        inferred = infer_goal_from_message(message)
    goal = load_goal(root)
    resume_requested = explicit_resume_intent(message)
    material_resume = any(
        k in (message or "")
        for k in ("补料完成", "材料已上传", "材料已补", "材料齐备", "继续上一个任务", "恢复刚才的任务")
    )
    confirm_all = bool(
        (
            user_confirmed
            and not confirmed_tools
            and any(k in (message or "") for k in ("确认执行全部", "确认全部剩余", "确认执行所有", "全部确认"))
        )
        or any(
            k in (message or "")
            for k in ("继续整个流程", "一键跑完", "跑完剩余", "继续全部", "确认执行全部")
        )
    )
    # Chat phrases like「继续」「继续进行」while waiting for confirm → grant next plan step tool
    confirmed_tools = list(confirmed_tools or [])
    if goal and resume_requested and not confirmed_tools:
        goal_status_pre = str((goal or {}).get("status") or "")
        if goal_status_pre in {"awaiting_confirmation", "in_progress", "pending"}:
            try:
                nxt = next_plan_step(root, goal)
                nxt_tool = str((nxt or {}).get("tool") or "").strip()
                if nxt_tool and not is_readonly_tool(nxt_tool):
                    confirmed_tools.append(nxt_tool)
                    user_confirmed = True
            except Exception:
                pass
        if confirm_all:
            user_confirmed = True

    # PR-1: tool_scope vs all_mutations — never expand single-tool confirm to all_mutations
    if goal and (confirmed_tools or user_confirmed):
        try:
            if confirmed_tools:
                goal = grant_confirmation(root, tools=confirmed_tools, all_mutations=False)
            elif confirm_all:
                goal = grant_confirmation(root, all_mutations=True)
            # bare user_confirmed without tools: do not grant all_mutations
        except Exception:
            pass

    # PR-6: terminal goals do not pollute new requests unless explicit resume
    goal_status = str((goal or {}).get("status") or "")
    inferred_types = {
        str(o.get("type") or "")
        for o in (inferred.get("objectives") or [])
        if isinstance(o, dict)
    }
    is_readonly_intent = bool(inferred_types & {"status", "diagnose", "chat"}) and not (
        inferred_types
        & {"full_generate", "fix_coverage", "fix_compliance", "fix_chapter", "export"}
    )

    should_create_new = False
    if not goal:
        should_create_new = True
    elif resume_requested or confirmed_tools or (
        user_confirmed and goal_status == "awaiting_confirmation"
    ):
        should_create_new = False
        if material_resume and goal_status == "blocked_human":
            try:
                goal = resume_goal_after_materials(root, note="user_requested_resume")
            except Exception:
                pass
    elif goal_status in {"succeeded", "failed", "cancelled", "budget_exceeded", "blocked_policy"}:
        should_create_new = True
    elif goal_status == "blocked_human":
        if material_resume:
            try:
                goal = resume_goal_after_materials(root, note="user_requested_resume")
            except Exception:
                pass
            should_create_new = False
        else:
            # new question / status while blocked → new goal (business goal archived)
            should_create_new = True
    else:
        # active goal: keep unless user starts a clearly different mutation objective
        existing_types = {
            str(o.get("type") or "")
            for o in ((goal or {}).get("normalized_objectives") or [])
            if isinstance(o, dict)
        }
        mutation_new = inferred_types & {
            "full_generate",
            "fix_coverage",
            "fix_compliance",
            "fix_chapter",
            "export",
        }
        mutation_old = existing_types & {
            "full_generate",
            "fix_coverage",
            "fix_compliance",
            "fix_chapter",
            "export",
        }
        if mutation_new and mutation_old and not mutation_new.intersection(mutation_old):
            should_create_new = True
        else:
            should_create_new = False

    if should_create_new:
        goal = create_goal(
            root,
            raw_user_goal=message,
            objectives=inferred.get("objectives") or [],
            success_criteria=inferred.get("success_criteria") or [],
            constraints=inferred.get("constraints") or {},
            plan=inferred.get("plan"),
            completion_mode=inferred.get("completion_mode"),
        )
        if confirmed_tools:
            try:
                goal = grant_confirmation(root, tools=confirmed_tools, all_mutations=False)
            except Exception:
                pass
    else:
        try:
            if goal is not None:
                goal = reevaluate_goal(root, goal)
        except Exception:
            pass
    if goal is None:
        goal = create_goal(
            root,
            raw_user_goal=message,
            objectives=inferred.get("objectives") or [],
            success_criteria=inferred.get("success_criteria") or [],
            constraints=inferred.get("constraints") or {},
            plan=inferred.get("plan"),
            completion_mode=inferred.get("completion_mode"),
        )

    goal_id = str(goal.get("goal_id") or new_trace_id())
    steps: list[dict[str, Any]] = []
    final_reply_parts: list[str] = []
    actions: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    terminal_status = "in_progress"
    last_tool_result_dict: dict[str, Any] | None = None
    obs_limit = observation_max_chars()

    # seed budget fingerprints
    try:
        budget.last_criteria_fp = criteria_fingerprint(goal.get("criteria_results") or [])
    except Exception:
        pass

    while budget.allow_next_step():
        snapshot = build_snapshot(
            root,
            status=status,
            goal=goal,
            last_tool_result=last_tool_result_dict,
            budget=budget.to_dict(),
            for_llm=True,
        )
        try:
            goal = reevaluate_goal(root, goal)
        except Exception:
            pass

        if str(goal.get("status")) == "succeeded":
            terminal_status = "succeeded"
            final_reply_parts.append("目标已完成。")
            break
        if str(goal.get("status")) == "failed":
            terminal_status = "failed"
            final_reply_parts.append(
                f"目标失败：{(goal.get('blocked_reason') or goal.get('failed_step_id') or '计划步骤失败')}"
            )
            break

        block_reason = human_blocking_reason(snapshot, goal)
        if block_reason and not confirmed_tools and not (
            user_confirmed and str(goal.get("status")) != "blocked_human"
        ):
            objectives = [
                str(o.get("type") or "")
                for o in (goal.get("normalized_objectives") or [])
                if isinstance(o, dict)
            ]
            if any(
                t in objectives
                for t in ("fix_coverage", "fix_compliance", "export", "full_generate", "fix_chapter")
            ):
                terminal_status = "blocked_human"
                set_goal_status(root, "blocked_human", blocked_reason=block_reason, goal=goal)
                goal = load_goal(root) or goal
                final_reply_parts.append(f"需要人工处理：{block_reason}")
                actions.append(
                    {
                        "type": "show_step",
                        "command": "build-materials-checklist",
                        "label": "打开材料清单",
                    }
                )
                actions.append({"type": "upload_materials", "label": "上传缺失材料"})
                break

        plan_driven = bool(goal.get("plan"))
        decision_raw: dict[str, Any]
        error_note = ""
        try:
            if use_llm and not plan_driven:
                decision_raw = _llm_decision(message, snapshot, history, llm_chat, budget=budget)
            elif use_llm and plan_driven and budget.llm_calls_used < budget.max_llm_calls:
                decision_raw = _rule_based_decision(
                    message, snapshot, root=root, goal=goal, prefer_plan=True
                )
                if not decision_raw.get("tool"):
                    decision_raw = _llm_decision(message, snapshot, history, llm_chat, budget=budget)
            else:
                decision_raw = _rule_based_decision(
                    message, snapshot, root=root, goal=goal, prefer_plan=True
                )
        except Exception as exc:  # noqa: BLE001
            error_note = str(exc)
            decision_raw = _rule_based_decision(
                message, snapshot, root=root, goal=goal, prefer_plan=True
            )
            decision_raw["reply"] = (decision_raw.get("reply") or "") + f"（规则兜底：{error_note[:120]}）"

        step_reasoning = str(decision_raw.pop("_reasoning", "") or "").strip()
        if step_reasoning:
            reasoning_parts.append(step_reasoning)
        decision = _normalize_decision(decision_raw)
        tool = decision["tool"]
        args = dict(decision["args"])
        plan_step_id = decision.get("plan_step_id") or ""
        observation = ""
        policy_reason = ""
        executed = False
        tool_result = None
        policy_ask_human = False

        # Resolve next plan step id if missing
        if not plan_step_id and plan_driven:
            nxt = next_plan_step(root, goal)
            if nxt and str(nxt.get("tool") or "") == tool:
                plan_step_id = str(nxt.get("step_id") or "")
        confirmed_for_tool = confirmation_allows(
            goal,
            tool,
            user_confirmed=user_confirmed,
            confirmed_tools=confirmed_tools,
        )
        if tool:
            policy = evaluate_tool_call(
                tool,
                args,
                auto_execute=is_readonly_tool(tool) and auto_execute_readonly,
                user_confirmed=confirmed_for_tool,
            )
            policy_reason = policy.reason
            policy_ask_human = bool(policy.ask_human)

            can_run = policy.allow and (is_readonly_tool(tool) or confirmed_for_tool)
            if can_run:
                if plan_step_id:
                    try:
                        goal = mark_plan_step(root, plan_step_id, status="running", goal=goal)
                    except Exception:
                        pass
                tool_result = invoke(tool, args, root=root, actor="supervisor")
                executed = True
                observation = (tool_result.summary_for_llm or "")[:obs_limit]
                err_code = tool_result.error.code if tool_result.error else ""
                err_retryable = (
                    bool(tool_result.error.retryable) if tool_result.error is not None else None
                )
                tool_outcome = str(getattr(tool_result, "outcome", "") or "")
                if tool_outcome not in {
                    "completed",
                    "partial_completed",
                    "blocked",
                    "failed",
                    "waiting_human",
                }:
                    tool_outcome = "completed" if tool_result.ok else "failed"
                last_tool_result_dict = {
                    "tool": tool,
                    "ok": tool_result.ok,
                    "summary": observation[:500],
                    "error": err_code,
                    "outcome": tool_outcome,
                }
                # tool_once empty-plan goals: mark that a tool actually ran
                if str(goal.get("completion_mode") or "") == "tool_once":
                    try:
                        goal["tool_once_executed"] = True
                        progress = dict(goal.get("progress") or {})
                        progress["tools_executed"] = int(progress.get("tools_executed") or 0) + 1
                        goal["progress"] = progress
                        from agent.goal import save_goal

                        save_goal(root, goal)
                    except Exception:
                        pass
                if plan_step_id:
                    try:
                        # Layer-2: outcome drives step; never promote Goal from tool.ok alone
                        step_ok = tool_outcome in {"completed", "partial_completed"}
                        goal = handle_plan_step_result(
                            root,
                            goal,
                            plan_step_id,
                            ok=step_ok,
                            error=(
                                ""
                                if step_ok
                                else (observation or err_code or tool_outcome)
                            ),
                            error_code=err_code or (
                                "blocked" if tool_outcome in {"blocked", "waiting_human"} else ""
                            ),
                            retryable=err_retryable,
                            outcome=tool_outcome,
                        )
                    except Exception:
                        pass
            elif policy.allow and not is_readonly_tool(tool):
                observation = "待确认后执行"
                decision["need_confirm"] = True
                actions.append(
                    {
                        "type": "confirm_tool",
                        "command": str(args.get("command") or tool),
                        "label": f"确认执行 {args.get('command') or tool}",
                        "tool": tool,
                        "args": args,
                        "user_confirmed": True,
                    }
                )
            else:
                observation = f"策略拒绝: {policy.reason}"
                if policy.ask_human:
                    decision["need_confirm"] = True
                    actions.append(
                        {
                            "type": "confirm_tool",
                            "command": str(args.get("command") or tool),
                            "label": f"确认执行 {args.get('command') or tool}",
                            "tool": tool,
                            "args": args,
                            "user_confirmed": True,
                        }
                    )
                else:
                    terminal_status = "blocked_policy"
        else:
            policy_reason = "无 tool"

        # Reevaluate after tool
        try:
            goal = reevaluate_goal(root, goal)
        except Exception:
            pass

        # PR-9: progress fingerprints from post-tool snapshot
        try:
            post_snapshot = build_snapshot(
                root,
                status=status,
                goal=goal,
                last_tool_result=last_tool_result_dict,
                budget=budget.to_dict(),
                for_llm=True,
            )
        except Exception:
            post_snapshot = snapshot
        crit_fp = criteria_fingerprint(goal.get("criteria_results") or [])
        iss_fp = issues_fingerprint(
            (post_snapshot.get("issues") or {}).get("open_blocks") or [],
            (post_snapshot.get("issues") or {}).get("open_warnings") or [],
        )
        # plan progress also counts
        plan_progress = str((goal.get("progress") or {}).get("plan_done") or "")
        plan_status_blob = "|".join(
            f"{s.get('step_id')}:{s.get('status')}"
            for s in (goal.get("plan") or [])
            if isinstance(s, dict)
        )
        if plan_status_blob:
            import hashlib as _hl

            crit_fp = _hl.sha1(f"{crit_fp}|{plan_status_blob}|{plan_progress}".encode()).hexdigest()[:16]
        budget.record_step(
            tool=tool,
            args=args,
            observation=observation,
            criteria_fp=crit_fp,
            issues_fp=iss_fp,
            executed=executed,
            ok=bool(tool_result.ok) if tool_result is not None else True,
        )

        record = append_decision(
            root,
            {
                "goal_id": goal_id,
                "step_index": budget.steps_used,
                "thought_summary": decision["thought_summary"],
                "selected_tool": tool,
                "tool_args": args,
                "policy_reason": policy_reason,
                "observation_summary": (observation or "")[: min(1000, obs_limit)],
                "executed": executed,
                "ok": bool(tool_result.ok) if tool_result is not None else True,
                "message": message[:500],
                "plan_step_id": plan_step_id,
                "goal_status": goal.get("status"),
                "terminal_hint": terminal_status,
                "user_summary": decision["thought_summary"],
            },
        )
        step_view = {
            "step": budget.steps_used,
            "thought_summary": decision["thought_summary"],
            "tool": tool,
            "args": args,
            "executed": executed,
            "observation": (observation or "")[:500],
            "trace_id": record.get("trace_id"),
            "plan_step_id": plan_step_id,
            "ok": bool(tool_result.ok) if tool_result is not None else True,
        }
        steps.append(step_view)

        reply = decision["reply"]
        if executed and tool_result is not None and tool_result.summary_for_llm:
            if reply:
                reply = f"{reply}\n\n{tool_result.summary_for_llm}"
            else:
                reply = tool_result.summary_for_llm
        if reply:
            final_reply_parts.append(reply)

        # Stop conditions (system-driven, not free model done)
        if str(goal.get("status")) == "succeeded":
            terminal_status = "succeeded"
            break
        if str(goal.get("status")) == "failed":
            terminal_status = "failed"
            break

        if decision["need_confirm"] or policy_ask_human:
            terminal_status = (
                "blocked_human" if "材料" in (observation or "") else "awaiting_confirmation"
            )
            if terminal_status == "awaiting_confirmation":
                set_goal_status(root, "awaiting_confirmation", goal=goal)
                goal = load_goal(root) or goal
            break

        if terminal_status == "blocked_policy":
            set_goal_status(root, "blocked_policy", blocked_reason=policy_reason, goal=goal)
            goal = load_goal(root) or goal
            break

        if not tool and decision.get("done"):
            if str(goal.get("status")) == "succeeded" or goal.get("all_criteria_ok"):
                terminal_status = "succeeded"
            else:
                terminal_status = str(goal.get("status") or "in_progress")
            break

        if not executed and not is_readonly_tool(tool or "x"):
            break

        # plan_completed / tool_once: stop after plan exhausted — only if evaluation says so
        if str(goal.get("completion_mode") or "") in {"plan_completed", "tool_once"}:
            nxt_after = next_plan_step(root, goal)
            if nxt_after is None and str(goal.get("status")) != "failed":
                try:
                    goal = reevaluate_goal(root, goal)
                except Exception:
                    pass
                # Never: plan empty ⇒ succeeded. Only reevaluate / set_goal_status (guarded).
                if str(goal.get("status")) == "succeeded":
                    terminal_status = "succeeded"
                    break
                if str(goal.get("status")) == "blocked_human":
                    terminal_status = "blocked_human"
                    break
                if not plan_has_open_steps(goal):
                    # Attempt guarded promotion; set_goal_status refuses without evaluation
                    set_goal_status(root, "succeeded", goal=goal)
                    goal = load_goal(root) or goal
                    if str(goal.get("status")) == "succeeded":
                        terminal_status = "succeeded"
                        break
                    terminal_status = str(goal.get("status") or "in_progress")
                    if terminal_status in {"blocked_human", "failed"}:
                        break

        if not budget.allow_next_step():
            terminal_status = "budget_exceeded"
            set_goal_status(
                root,
                "budget_exceeded",
                blocked_reason=budget.stop_reason or "预算或无进展熔断",
                goal=goal,
            )
            goal = load_goal(root) or goal
            break

        if executed and tool_result is not None and not tool_result.ok:
            if str(goal.get("status")) == "failed":
                terminal_status = "failed"
                break
            if budget.no_progress_steps >= budget.max_no_progress_steps:
                terminal_status = "budget_exceeded"
                break
            continue

    if budget.stop_reason == "budget_exceeded" and terminal_status == "in_progress":
        terminal_status = "budget_exceeded"
        try:
            set_goal_status(
                root,
                "budget_exceeded",
                blocked_reason="达到步数/模型调用/无进展上限",
                goal=goal,
            )
            goal = load_goal(root) or goal
        except Exception:
            pass

    try:
        goal = reevaluate_goal(root, goal)
        if str(goal.get("status")) == "succeeded":
            terminal_status = "succeeded"
        elif str(goal.get("status")) == "failed" and terminal_status == "in_progress":
            terminal_status = "failed"
        elif str(goal.get("status")) == "blocked_human" and terminal_status == "in_progress":
            terminal_status = "blocked_human"
        # plan_completed safety net — still requires evaluate_goal_success via set_goal_status
        if (
            terminal_status == "in_progress"
            and str(goal.get("completion_mode") or "") in {"plan_completed", "tool_once"}
            and not plan_has_open_steps(goal)
        ):
            set_goal_status(root, "succeeded", goal=goal)
            goal = load_goal(root) or goal
            terminal_status = str(goal.get("status") or terminal_status)
            if terminal_status not in {
                "succeeded",
                "blocked_human",
                "failed",
                "budget_exceeded",
                "blocked_policy",
                "awaiting_confirmation",
            }:
                terminal_status = "in_progress"
        goal_id = str(goal.get("goal_id") or goal_id)
    except Exception:
        goal = load_goal(root)

    reply = "\n".join(part for part in final_reply_parts if part).strip()
    if not reply:
        if terminal_status == "succeeded":
            reply = "目标已完成。"
        elif terminal_status == "budget_exceeded":
            reply = "已达预算或无进展上限，已停止自动循环。请查看已完成步骤与建议操作。"
        elif terminal_status == "blocked_human":
            reply = f"需要人工处理：{(goal or {}).get('blocked_reason') or '请补充材料或确认风险'}"
        elif terminal_status == "failed":
            reply = f"目标失败：{(goal or {}).get('blocked_reason') or '请查看失败步骤'}"
        else:
            reply = "已处理完本轮。"

    if terminal_status == "budget_exceeded":
        reply += (
            f"\n\n已完成步骤 {budget.steps_used}/{budget.max_steps}；"
            f"未完成目标请查看 Goal 条件；"
            f"可恢复入口：继续对话或确认执行剩余计划。"
        )

    if not actions:
        next_step = None
        if isinstance((goal or {}).get("plan"), list):
            nxt = next_plan_step(root, goal)
            if nxt:
                need_c = not is_readonly_tool(str(nxt.get("tool") or ""))
                actions.append(
                    {
                        "type": "confirm_tool" if need_c else "run_command",
                        "command": str((nxt.get("args") or {}).get("command") or nxt.get("tool") or ""),
                        "label": f"继续：{nxt.get('label') or nxt.get('tool')}",
                        "tool": nxt.get("tool"),
                        "args": nxt.get("args") or {},
                        "user_confirmed": True if need_c else False,
                    }
                )
        pipe = build_snapshot(root, status=status, goal=goal, for_llm=False).get("pipeline") or {}
        next_step = pipe.get("next_step") if isinstance(pipe, dict) else None
        if next_step and next_step.get("command"):
            actions.append(
                {
                    "type": "run_command",
                    "command": str(next_step.get("command")),
                    "label": f"执行 {next_step.get('label') or next_step.get('command')}",
                }
            )
        actions.append({"type": "auto_run", "label": "一键跑完剩余"})

    recommended = list((goal or {}).get("recommended_actions") or [])
    if not recommended:
        if terminal_status == "blocked_human":
            recommended = ["上传缺失材料", "打开材料清单", "材料齐备后发送“继续”"]
        elif terminal_status == "awaiting_confirmation":
            recommended = ["确认执行变更类操作"]
        elif terminal_status == "budget_exceeded":
            recommended = ["查看决策轨迹", "手动指定下一步", "调整预算后重试"]
        elif terminal_status == "failed":
            recommended = ["查看失败详情", "人工重试该步骤", "修改配置后恢复"]
        elif terminal_status == "succeeded":
            recommended = ["下载 final.docx", "查看风险登记"]

    payload = _terminal_payload(
        terminal_status=terminal_status,
        reply=reply,
        actions=actions,
        steps=steps,
        goal=goal,
        goal_id=goal_id,
        budget=budget,
        reasoning_parts=reasoning_parts,
        recommended_actions=recommended,
    )
    save_last_plan(root, payload)
    return payload


def plan_with_supervisor(
    message: str,
    history: list[dict[str, Any]],
    status: dict[str, Any],
    llm_chat=None,
    review_context: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
    user_confirmed: bool = False,
    confirmed_tools: list[str] | None = None,
) -> dict[str, Any] | None:
    """If supervisor flag enabled, return a plan-like dict for session_orchestrator; else None."""
    if not agent_supervisor_enabled():
        return None
    root = root or project_root()
    use_llm = True
    try:
        result = run_supervisor_turn(
            message,
            root=root,
            status=status,
            history=history,
            llm_chat=llm_chat,
            use_llm=use_llm,
            auto_execute_readonly=True,
            user_confirmed=bool(user_confirmed),
            max_steps=None,
            confirmed_tools=confirmed_tools,
        )
    except Exception:
        return None

    plan = {
        "intent": result.get("intent") or "supervisor",
        "action": "chat",
        "query_type": "status",
        "command": "",
        "auto_execute": False,
        "reply": result.get("reply") or "",
        "actions": result.get("actions") or [],
        "supervisor_steps": result.get("steps") or [],
        "goal_id": result.get("goal_id"),
        "goal": result.get("goal") or {},
        "terminal_status": result.get("terminal_status"),
        "budget": result.get("budget") or {},
        "recommended_actions": result.get("recommended_actions") or [],
        "supervisor": True,
    }
    if result.get("thinking"):
        plan["thinking"] = result.get("thinking")
    return plan
