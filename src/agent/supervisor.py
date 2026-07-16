from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from agent.flags import agent_supervisor_enabled
from agent.policy import evaluate_tool_call, is_readonly_tool
from agent.tool_registry import tool_manifest
from agent.tool_runtime import invoke
from agent.goal import create_goal, goal_summary, infer_goal_from_message, load_goal, reevaluate_goal
from agent.trace import append_decision, load_decisions, max_steps_default, new_trace_id, save_last_plan
from utils import project_root


_SUPERVISOR_SYSTEM = """你是标书系统的 Supervisor Agent（短循环，PR-3）。
你只能通过选择已注册 tool 推进目标，不能发明新阶段。

## 可用 tools（JSON）
{tools}

## 输出
只输出一个 JSON 对象，不要 Markdown 代码块，字段：
- thought_summary: 一句话说明为什么选这个 tool（给用户看）
- tool: tool 名（必须来自可用列表；若无需 tool 填 ""）
- args: 对象（无参数用 {{}}）
- reply: 给用户的中文回复
- done: 布尔，是否结束本轮循环
- need_confirm: 布尔，是否需要用户确认后再执行变更

## 规则
1. 用户问状态/进度/为什么失败 → 优先 query_status 或 diagnose_failure，done=true。
2. 用户明确要求执行某阶段 → tool=run_stage，args.command=对应 command；若未明确“执行/开始/跑”，need_confirm=true 且不要假设已执行。
3. 禁止编造产物状态；只根据 snapshot 判断。
4. 不要一次规划多步；每轮只选 0 或 1 个 tool。
5. 高风险导出（build-docx）默认 need_confirm=true。
6. 若无法判断，tool=""，done=true，reply 里澄清问题。
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


def _rule_based_decision(message: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    text = (message or "").strip().lower()
    # diagnose
    if any(k in message for k in ("诊断", "失败", "错误", "为啥挂", "怎么修")):
        return {
            "thought_summary": "用户在排查失败，先做只读诊断",
            "tool": "diagnose_failure",
            "args": {},
            "reply": "我先汇总当前失败信息。",
            "done": True,
            "need_confirm": False,
        }
    if any(k in message for k in ("覆盖率", "评分点未覆盖", "未覆盖评分", "补齐评分", "覆盖缺口", "评分覆盖")):
        if any(k in message for k in ("改", "修", "补", "处理", "修复")):
            return {
                "thought_summary": "用户要求按覆盖缺口改稿，先给出 fix_coverage 计划",
                "tool": "fix_coverage",
                "args": {"max_chapters": 5, "confirm_execute": False},
                "reply": "我将分析评分覆盖缺口并给出定向改稿计划（确认后可执行）。",
                "done": True,
                "need_confirm": True,
            }
        return {
            "thought_summary": "用户询问评分覆盖，执行只读分析",
            "tool": "analyze_coverage",
            "args": {"rebuild": True, "max_chapters": 5},
            "reply": "我先分析当前评分点覆盖情况。",
            "done": True,
            "need_confirm": False,
        }

    if any(k in message for k in ("状态", "进度", "到哪了", "现在怎样", "当前")):
        return {
            "thought_summary": "用户询问状态，查询进度快照",
            "tool": "query_status",
            "args": {"view": "summary"},
            "reply": "我先查看当前流水线进度。",
            "done": True,
            "need_confirm": False,
        }
    # targeted rewrite / write
    if any(k in message for k in ("改第", "重写第", "只写第", "定向改", "改稿")):
        # crude chapter id extraction: digits / dotted ids
        import re as _re
        ids = _re.findall(r"\d+(?:\.\d+)*", message)
        # filter likely chapter-like tokens
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
            "done": True,
            "need_confirm": True,
        }

    if any(k in message for k in ("出 Word", "生成 Word", "导出", "出稿", "build_export", "final.docx")):
        return {
            "thought_summary": "用户要求导出终稿，使用 build_export（含 stale 重建）",
            "tool": "build_export",
            "args": {"targets": ["md", "docx", "format"]},
            "reply": "将导出 Markdown/Word（若终稿已失效会先强制重建）。请确认后执行。",
            "done": True,
            "need_confirm": True,
        }

    # explicit run next
    if any(k in message for k in ("继续", "下一步", "执行下一步", "开始跑", "重试")):
        next_step = snapshot.get("next_step") if isinstance(snapshot.get("next_step"), dict) else None
        command = str((next_step or {}).get("command") or "")
        if command:
            return {
                "thought_summary": f"用户要求继续，建议执行 {command}",
                "tool": "run_stage",
                "args": {"command": command},
                "reply": f"下一步是 `{command}`。确认后我可以执行。",
                "done": True,
                "need_confirm": True,
            }
    return {
        "thought_summary": "无明确 tool 意图，仅对话回复",
        "tool": "",
        "args": {},
        "reply": "我可以帮你查看状态、诊断失败，或在你确认后执行某个流水线阶段。",
        "done": True,
        "need_confirm": False,
    }


def _llm_decision(
    message: str,
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    llm_chat: Callable[..., str] | None,
) -> dict[str, Any]:
    tools = tool_manifest()
    # shrink tools for prompt: meta + names only for stages
    compact_tools = []
    for item in tools:
        if item["name"] in {"run_stage", "query_status", "query_artifacts", "diagnose_failure"} or item.get("kind") == "meta":
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
    if llm_chat is None:
        from llm_client import chat as llm_chat  # type: ignore

    raw = llm_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1,
    )
    data = _extract_json(raw)
    if not data:
        raise ValueError("supervisor LLM 未返回合法 JSON")
    return data


def _normalize_decision(data: dict[str, Any]) -> dict[str, Any]:
    tool = str(data.get("tool") or "").strip()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    return {
        "thought_summary": str(data.get("thought_summary") or "").strip() or "（无摘要）",
        "tool": tool,
        "args": args,
        "reply": str(data.get("reply") or "").strip(),
        "done": bool(data.get("done", True)),
        "need_confirm": bool(data.get("need_confirm", False)),
    }


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
) -> dict[str, Any]:
    """Run a short supervisor loop (default 1-5 steps). Readonly tools may auto-run.

    Returns a payload compatible with chat orchestrate enrichment:
    reply, actions, steps, decisions, error?
    """
    root = root or project_root()
    history = history or []
    status = status or {}
    steps_limit = max_steps if max_steps is not None else min(5, max_steps_default())

    # compact snapshot from status if provided; else query_status metrics
    snapshot: dict[str, Any]
    if status:
        from session_orchestrator import _compact_status_snapshot

        snapshot = _compact_status_snapshot(status)
    else:
        qs = invoke("query_status", {"view": "summary"}, root=root, actor="supervisor")
        snapshot = qs.metrics if qs.ok else {}

    inferred = infer_goal_from_message(message)
    goal = load_goal(root)
    if not goal or str(goal.get("status")) in {"succeeded", "failed", "cancelled"}:
        goal = create_goal(
            root,
            raw_user_goal=message,
            objectives=inferred.get("objectives") or [],
            success_criteria=inferred.get("success_criteria") or [],
            constraints=inferred.get("constraints") or {},
        )
    else:
        # refresh evaluation for active goal
        try:
            goal = reevaluate_goal(root, goal)
        except Exception:
            pass
    goal_id = str(goal.get("goal_id") or new_trace_id())
    steps: list[dict[str, Any]] = []
    final_reply_parts: list[str] = []
    actions: list[dict[str, Any]] = []

    for step_index in range(1, steps_limit + 1):
        decision_raw: dict[str, Any]
        error_note = ""
        try:
            if use_llm:
                decision_raw = _llm_decision(message, snapshot, history, llm_chat)
            else:
                decision_raw = _rule_based_decision(message, snapshot)
        except Exception as exc:  # noqa: BLE001
            error_note = str(exc)
            decision_raw = _rule_based_decision(message, snapshot)
            decision_raw["reply"] = (decision_raw.get("reply") or "") + f"（规则兜底：{error_note[:120]}）"

        decision = _normalize_decision(decision_raw)
        tool = decision["tool"]
        args = dict(decision["args"])
        observation = ""
        policy_reason = ""
        executed = False
        tool_result = None

        if tool:
            # Prefer stage command alias -> still ok via get_tool
            policy = evaluate_tool_call(
                tool,
                args,
                auto_execute=is_readonly_tool(tool) and auto_execute_readonly,
                user_confirmed=user_confirmed,
            )
            policy_reason = policy.reason
            if policy.allow and (is_readonly_tool(tool) or user_confirmed):
                tool_result = invoke(tool, args, root=root, actor="supervisor")
                executed = True
                observation = tool_result.summary_for_llm
                if tool_result.ok and tool_result.metrics:
                    # refresh snapshot lightly
                    if tool == "query_status":
                        snapshot = {**snapshot, **tool_result.metrics}
            elif policy.allow and not is_readonly_tool(tool):
                # mutation allowed by policy but not auto: surface confirm action
                observation = "待确认后执行"
                actions.append(
                    {
                        "type": "run_command",
                        "command": str(args.get("command") or tool),
                        "label": f"确认执行 {args.get('command') or tool}",
                    }
                )
            else:
                observation = f"策略拒绝: {policy.reason}"
                if policy.ask_human:
                    decision["need_confirm"] = True
                    if args.get("command"):
                        actions.append(
                            {
                                "type": "run_command",
                                "command": str(args.get("command")),
                                "label": f"确认执行 {args.get('command')}",
                            }
                        )
        else:
            policy_reason = "无 tool"

        record = append_decision(
            root,
            {
                "goal_id": goal_id,
                "step_index": step_index,
                "thought_summary": decision["thought_summary"],
                "selected_tool": tool,
                "tool_args": args,
                "policy_reason": policy_reason,
                "observation_summary": (observation or "")[:1000],
                "executed": executed,
                "ok": bool(tool_result.ok) if tool_result is not None else True,
                "message": message[:500],
            },
        )
        step_view = {
            "step": step_index,
            "thought_summary": decision["thought_summary"],
            "tool": tool,
            "args": args,
            "executed": executed,
            "observation": (observation or "")[:500],
            "trace_id": record.get("trace_id"),
        }
        steps.append(step_view)

        reply = decision["reply"]
        if executed and tool_result is not None and tool_result.summary_for_llm:
            # merge observation into reply for user
            if reply:
                reply = f"{reply}\n\n{tool_result.summary_for_llm}"
            else:
                reply = tool_result.summary_for_llm
        if reply:
            final_reply_parts.append(reply)

        if decision["done"] or not tool or decision["need_confirm"]:
            break
        # only continue loop if readonly tool ran and LLM asked not done (rare in PR-3)
        if not (executed and is_readonly_tool(tool) and not decision["done"]):
            break

    reply = "\n".join(part for part in final_reply_parts if part).strip()
    if not reply:
        reply = "已处理完本轮。"

    # default actions if empty
    if not actions:
        next_step = snapshot.get("next_step") if isinstance(snapshot.get("next_step"), dict) else None
        if next_step and next_step.get("command"):
            actions.append(
                {
                    "type": "run_command",
                    "command": str(next_step.get("command")),
                    "label": f"执行 {next_step.get('label') or next_step.get('command')}",
                }
            )
        actions.append({"type": "auto_run", "label": "一键跑完剩余"})

    try:
        goal = reevaluate_goal(root)
        reply = (reply + "\n\n" + goal_summary(goal)).strip()
        goal_id = str(goal.get("goal_id") or goal_id)
    except Exception:
        goal = load_goal(root)

    payload = {
        "reply": reply,
        "actions": actions,
        "steps": steps,
        "goal_id": goal_id,
        "goal": {
            "status": (goal or {}).get("status"),
            "all_criteria_ok": (goal or {}).get("all_criteria_ok"),
            "criteria_results": (goal or {}).get("criteria_results") or [],
        },
        "supervisor": True,
        "action": "chat",
        "auto_execute": False,
        "intent": "supervisor_turn",
    }
    save_last_plan(root, payload)
    return payload


def plan_with_supervisor(
    message: str,
    history: list[dict[str, Any]],
    status: dict[str, Any],
    llm_chat=None,
    review_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """If supervisor flag enabled, return a plan-like dict for session_orchestrator; else None."""
    if not agent_supervisor_enabled():
        return None
    root = project_root()
    # Prefer rule-based first for reliability in tests; still try LLM when available
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
            user_confirmed=False,
            max_steps=3,
        )
    except Exception:
        return None

    # Map to legacy plan shape so resolve_execution keeps working.
    # Readonly already executed inside supervisor; do not auto trigger mutate.
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
        "supervisor": True,
    }
    # If last step suggested run_stage with need confirm, keep as chat + button
    return plan
