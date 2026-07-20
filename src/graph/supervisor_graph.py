from __future__ import annotations

"""Optional LangGraph supervisor loop (PR-9 multi-step).

Deterministic pipeline remains build_bid_graph(). This graph wraps
agent.supervisor decision + tool_runtime.invoke under a step budget and
can continue after readonly / confirmed tools until goal/budget stops.
"""

from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.goal import load_goal, reevaluate_goal
from agent.policy import is_readonly_tool
from agent.snapshot import build_snapshot
from agent.supervisor import _normalize_decision, _rule_based_decision
from agent.tool_runtime import invoke
from agent.trace import append_decision, max_steps_default, new_trace_id
from utils import project_root


class SupervisorGraphState(TypedDict, total=False):
    root_dir: str
    goal: str
    max_steps: int
    step: int
    use_llm: bool
    user_confirmed: bool
    done: bool
    need_confirm: bool
    last_tool: str
    last_args: dict[str, Any]
    last_observation: str
    reply: str
    steps: list[dict[str, Any]]
    error: str
    goal_id: str
    thought: str
    terminal_status: str
    same_tool_streak: int
    last_tool_fp: str


def _root(state: SupervisorGraphState) -> Path:
    return Path(state.get("root_dir") or project_root())


def supervisor_node(state: SupervisorGraphState) -> dict[str, Any]:
    message = str(state.get("goal") or "")
    step = int(state.get("step") or 0) + 1
    max_steps = int(state.get("max_steps") or max_steps_default())
    root = _root(state)

    if step > max_steps:
        return {
            "step": step,
            "done": True,
            "need_confirm": False,
            "terminal_status": "budget_exceeded",
            "reply": (state.get("reply") or "") + f"\n已达 max_steps={max_steps}，停止。",
            "last_observation": "budget_exceeded",
            "last_tool": "",
        }

    # goal success stop
    try:
        goal_obj = load_goal(root)
        if goal_obj:
            goal_obj = reevaluate_goal(root, goal_obj)
            if str(goal_obj.get("status")) == "succeeded" or goal_obj.get("all_criteria_ok"):
                return {
                    "step": step,
                    "done": True,
                    "terminal_status": "succeeded",
                    "reply": (state.get("reply") or "") + "\n目标已完成。",
                    "last_tool": "",
                    "last_observation": "goal_succeeded",
                    "goal_id": str(goal_obj.get("goal_id") or state.get("goal_id") or ""),
                }
    except Exception:
        goal_obj = None

    snapshot = build_snapshot(root, goal=goal_obj, for_llm=True)

    use_llm = bool(state.get("use_llm", False))
    if use_llm:
        try:
            from agent.supervisor import _llm_decision

            decision_raw = _llm_decision(message, snapshot, [], None)
        except Exception:
            decision_raw = _rule_based_decision(message, snapshot, goal=goal_obj, prefer_plan=True)
    else:
        decision_raw = _rule_based_decision(message, snapshot, goal=goal_obj, prefer_plan=True)

    decision = _normalize_decision(decision_raw)
    tool = str(decision.get("tool") or "")
    args = dict(decision.get("args") or {})
    need_confirm = bool(decision.get("need_confirm"))
    if tool and not is_readonly_tool(tool) and not state.get("user_confirmed"):
        need_confirm = True

    # no-progress: same tool streak
    tool_fp = f"{tool}:{sorted((args or {}).items())}"
    same = int(state.get("same_tool_streak") or 0)
    if tool and tool_fp == str(state.get("last_tool_fp") or ""):
        same += 1
    else:
        same = 1 if tool else 0
    if same >= 3:
        return {
            "step": step,
            "done": True,
            "terminal_status": "budget_exceeded",
            "last_observation": "same_tool_streak",
            "reply": (state.get("reply") or "") + "\n同一 tool 连续重复，停止自动循环。",
            "last_tool": tool,
            "last_args": args,
            "same_tool_streak": same,
            "last_tool_fp": tool_fp,
        }

    return {
        "step": step,
        "last_tool": tool,
        "last_args": args,
        "reply": str(decision.get("reply") or state.get("reply") or ""),
        "need_confirm": need_confirm,
        "done": False if tool and not need_confirm else bool(decision.get("done") or not tool),
        "thought": str(decision.get("thought_summary") or ""),
        "same_tool_streak": same,
        "last_tool_fp": tool_fp,
        "goal_id": str((goal_obj or {}).get("goal_id") or state.get("goal_id") or new_trace_id()),
    }


def tool_node(state: SupervisorGraphState) -> dict[str, Any]:
    tool = str(state.get("last_tool") or "")
    args = dict(state.get("last_args") or {})
    root = _root(state)
    if not tool:
        return {"last_observation": "no_tool", "done": True, "terminal_status": "in_progress"}

    if not is_readonly_tool(tool) and not state.get("user_confirmed"):
        return {
            "last_observation": "mutation_blocked_without_confirm",
            "done": True,
            "need_confirm": True,
            "terminal_status": "awaiting_confirmation",
        }

    result = invoke(tool, args, root=root, actor="supervisor_graph")
    step_view = {
        "step": state.get("step"),
        "tool": tool,
        "args": args,
        "executed": True,
        "ok": result.ok,
        "observation": result.summary_for_llm,
        "thought_summary": state.get("thought") or "",
    }
    steps = list(state.get("steps") or [])
    steps.append(step_view)
    append_decision(
        root,
        {
            "goal_id": state.get("goal_id") or new_trace_id(),
            "step_index": state.get("step"),
            "thought_summary": state.get("thought") or "",
            "selected_tool": tool,
            "tool_args": args,
            "observation_summary": (result.summary_for_llm or "")[:1000],
            "executed": True,
            "ok": result.ok,
            "source": "supervisor_graph",
        },
    )
    reply = state.get("reply") or ""
    if result.summary_for_llm:
        reply = f"{reply}\n\n{result.summary_for_llm}".strip()

    # reevaluate goal after tool
    terminal = "in_progress"
    done = False
    try:
        goal_obj = load_goal(root)
        if goal_obj:
            goal_obj = reevaluate_goal(root, goal_obj)
            if str(goal_obj.get("status")) == "succeeded" or goal_obj.get("all_criteria_ok"):
                terminal = "succeeded"
                done = True
    except Exception:
        pass

    # continue loop for readonly / confirmed unless done
    if not done:
        done = False  # allow route back to supervisor
    return {
        "last_observation": result.summary_for_llm,
        "steps": steps,
        "reply": reply,
        "done": done,
        "need_confirm": False,
        "terminal_status": terminal,
    }


def human_node(state: SupervisorGraphState) -> dict[str, Any]:
    return {
        "done": True,
        "need_confirm": True,
        "terminal_status": "awaiting_confirmation",
        "last_observation": "human_required",
        "reply": ((state.get("reply") or "") + "\n需要你确认后才能执行变更类操作。").strip(),
    }


def route_after_supervisor(state: SupervisorGraphState) -> Literal["tool", "human", "end"]:
    if state.get("done") and not state.get("last_tool"):
        return "end"
    if state.get("terminal_status") in {"succeeded", "budget_exceeded", "blocked_human"}:
        return "end"
    tool = str(state.get("last_tool") or "")
    if not tool:
        return "end"
    if state.get("need_confirm") and not state.get("user_confirmed") and not is_readonly_tool(tool):
        return "human"
    return "tool"


def route_after_tool(state: SupervisorGraphState) -> Literal["supervisor", "end"]:
    if state.get("done") or state.get("terminal_status") in {"succeeded", "budget_exceeded", "awaiting_confirmation"}:
        return "end"
    # multi-step: continue observe/decide
    step = int(state.get("step") or 0)
    max_steps = int(state.get("max_steps") or max_steps_default())
    if step >= max_steps:
        return "end"
    return "supervisor"


def build_supervisor_graph():
    graph = StateGraph(SupervisorGraphState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("tool", tool_node)
    graph.add_node("human", human_node)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"tool": "tool", "human": "human", "end": END},
    )
    graph.add_conditional_edges("tool", route_after_tool, {"supervisor": "supervisor", "end": END})
    graph.add_edge("human", END)
    return graph.compile()


def run_supervisor_graph(
    goal: str,
    root: Path | None = None,
    *,
    max_steps: int | None = None,
    use_llm: bool = False,
    user_confirmed: bool = False,
) -> SupervisorGraphState:
    root = root or project_root()
    graph = build_supervisor_graph()
    initial: SupervisorGraphState = {
        "root_dir": str(root),
        "goal": goal,
        "max_steps": int(max_steps or max_steps_default()),
        "step": 0,
        "use_llm": use_llm,
        "user_confirmed": user_confirmed,
        "done": False,
        "need_confirm": False,
        "steps": [],
        "reply": "",
        "goal_id": new_trace_id(),
        "last_tool": "",
        "last_args": {},
        "terminal_status": "in_progress",
        "same_tool_streak": 0,
        "last_tool_fp": "",
    }
    return graph.invoke(initial)
