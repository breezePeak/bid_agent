from __future__ import annotations

"""LangGraph adapter over the unified Supervisor kernel (PR-A2).

Does NOT reimplement budget, confirm, or tool policy rules.
Calls agent.supervisor.run_supervisor_turn as the single execution kernel.
"""

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.supervisor import run_supervisor_turn
from agent.trace import max_steps_default
from utils import project_root


class SupervisorGraphState(TypedDict, total=False):
    root_dir: str
    goal: str
    max_steps: int
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
    budget: dict[str, Any]
    recommended_actions: list[str]
    supervisor: bool


def _root(state: SupervisorGraphState) -> Path:
    return Path(state.get("root_dir") or project_root())


def kernel_node(state: SupervisorGraphState) -> dict[str, Any]:
    """Single node: delegate entire multi-step loop to run_supervisor_turn."""
    message = str(state.get("goal") or "")
    root = _root(state)
    max_steps = int(state.get("max_steps") or max_steps_default())
    use_llm = bool(state.get("use_llm", False))
    user_confirmed = bool(state.get("user_confirmed", False))

    try:
        result = run_supervisor_turn(
            message,
            root=root,
            status={},
            history=[],
            use_llm=use_llm,
            auto_execute_readonly=True,
            user_confirmed=user_confirmed,
            max_steps=max_steps,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "done": True,
            "need_confirm": False,
            "terminal_status": "failed",
            "reply": f"Supervisor 内核执行失败: {exc}",
            "error": str(exc),
            "steps": list(state.get("steps") or []),
            "last_tool": "",
            "last_observation": "kernel_error",
            "supervisor": True,
        }

    steps = list(result.get("steps") or [])
    last = steps[-1] if steps else {}
    terminal = str(result.get("terminal_status") or "in_progress")
    need_confirm = terminal == "awaiting_confirmation" or bool(result.get("need_confirm"))
    done = terminal in {
        "succeeded",
        "blocked_human",
        "blocked_policy",
        "budget_exceeded",
        "failed",
        "awaiting_confirmation",
    } or not steps

    return {
        "done": done,
        "need_confirm": need_confirm,
        "terminal_status": terminal,
        "reply": str(result.get("reply") or ""),
        "steps": steps,
        "last_tool": str(last.get("tool") or ""),
        "last_args": dict(last.get("args") or {}),
        "last_observation": str(last.get("observation") or terminal),
        "goal_id": str(result.get("goal_id") or ""),
        "thought": str(last.get("thought_summary") or ""),
        "budget": dict(result.get("budget") or {}),
        "recommended_actions": list(result.get("recommended_actions") or []),
        "supervisor": True,
        "error": "",
    }


def build_supervisor_graph():
    graph = StateGraph(SupervisorGraphState)
    graph.add_node("kernel", kernel_node)
    graph.add_edge(START, "kernel")
    graph.add_edge("kernel", END)
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
        "use_llm": use_llm,
        "user_confirmed": user_confirmed,
        "done": False,
        "need_confirm": False,
        "steps": [],
        "reply": "",
        "goal_id": "",
        "last_tool": "",
        "last_args": {},
        "terminal_status": "in_progress",
        "supervisor": True,
    }
    return graph.invoke(initial)
