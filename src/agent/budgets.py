from __future__ import annotations

"""Agent step / LLM / no-progress budgets (PR-9)."""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def max_steps_budget() -> int:
    return max(1, _env_int("AGENT_MAX_STEPS", 12))


def max_llm_calls_budget() -> int:
    return max(1, _env_int("AGENT_MAX_LLM_CALLS", 20))


def max_same_tool_streak() -> int:
    return max(1, _env_int("AGENT_MAX_SAME_TOOL_STREAK", 2))


def max_no_progress_steps() -> int:
    return max(1, _env_int("AGENT_MAX_NO_PROGRESS_STEPS", 2))


def max_repair_rounds() -> int:
    return max(1, _env_int("AGENT_MAX_REPAIR_ROUNDS", 2))


def max_chapters_per_invoke() -> int:
    return max(1, _env_int("AGENT_MAX_CHAPTERS_PER_INVOKE", 5))


def observation_max_chars() -> int:
    return max(200, _env_int("AGENT_OBSERVATION_MAX_CHARS", 2000))


def snapshot_max_chars() -> int:
    return max(1000, _env_int("AGENT_SNAPSHOT_MAX_CHARS", 12000))


def tool_call_fingerprint(tool: str, args: dict[str, Any] | None) -> str:
    payload = {"tool": str(tool or ""), "args": args or {}}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def criteria_fingerprint(criteria_results: list[dict[str, Any]] | None) -> str:
    items = []
    for row in criteria_results or []:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "check": row.get("check"),
                "ok": bool(row.get("ok")),
                "detail": str(row.get("detail") or "")[:200],
            }
        )
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def issues_fingerprint(open_blocks: list[Any] | None, open_warnings: list[Any] | None = None) -> str:
    parts: list[str] = []
    for item in open_blocks or []:
        if isinstance(item, dict):
            parts.append(f"b:{item.get('code')}:{item.get('id')}:{item.get('status')}")
        else:
            parts.append(f"b:{item}")
    for item in open_warnings or []:
        if isinstance(item, dict):
            parts.append(f"w:{item.get('code')}:{item.get('id')}")
        else:
            parts.append(f"w:{item}")
    raw = "|".join(sorted(parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class AgentBudget:
    max_steps: int = field(default_factory=max_steps_budget)
    max_llm_calls: int = field(default_factory=max_llm_calls_budget)
    max_same_tool_streak: int = field(default_factory=max_same_tool_streak)
    max_no_progress_steps: int = field(default_factory=max_no_progress_steps)

    steps_used: int = 0
    llm_calls_used: int = 0
    same_tool_streak: int = 0
    no_progress_steps: int = 0
    last_tool_fp: str = ""
    last_observation_fp: str = ""
    last_criteria_fp: str = ""
    last_issues_fp: str = ""
    stop_reason: str = ""

    def allow_next_step(self) -> bool:
        if self.steps_used >= self.max_steps:
            self.stop_reason = "budget_exceeded"
            return False
        if self.llm_calls_used >= self.max_llm_calls:
            self.stop_reason = "budget_exceeded"
            return False
        if self.no_progress_steps >= self.max_no_progress_steps:
            self.stop_reason = "budget_exceeded"
            return False
        if self.same_tool_streak >= self.max_same_tool_streak:
            self.stop_reason = "budget_exceeded"
            return False
        return True

    def record_llm_call(self) -> None:
        self.llm_calls_used += 1

    def record_step(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None,
        observation: str = "",
        criteria_fp: str = "",
        issues_fp: str = "",
        executed: bool = False,
        ok: bool = True,
    ) -> None:
        self.steps_used += 1
        tool_fp = tool_call_fingerprint(tool, args)
        obs_fp = hashlib.sha1((observation or "").encode("utf-8")).hexdigest()[:16]

        if tool and tool_fp == self.last_tool_fp and (not observation or obs_fp == self.last_observation_fp):
            self.same_tool_streak += 1
        elif tool and tool_fp == self.last_tool_fp and executed and not ok:
            # same failing tool counts as streak
            self.same_tool_streak += 1
        else:
            self.same_tool_streak = 1 if tool else 0
        self.last_tool_fp = tool_fp if tool else ""
        self.last_observation_fp = obs_fp

        progressed = False
        if criteria_fp and criteria_fp != self.last_criteria_fp:
            progressed = True
            self.last_criteria_fp = criteria_fp
        if issues_fp and issues_fp != self.last_issues_fp:
            progressed = True
            self.last_issues_fp = issues_fp
        if executed and ok and observation and tool:
            # successful mutation / useful observation counts as progress once
            if not criteria_fp and not issues_fp:
                progressed = True

        if progressed:
            self.no_progress_steps = 0
        else:
            self.no_progress_steps += 1

        if self.same_tool_streak >= self.max_same_tool_streak:
            self.stop_reason = "budget_exceeded"
        elif self.no_progress_steps >= self.max_no_progress_steps:
            self.stop_reason = "budget_exceeded"
        elif self.steps_used >= self.max_steps or self.llm_calls_used >= self.max_llm_calls:
            self.stop_reason = "budget_exceeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_llm_calls": self.max_llm_calls,
            "max_same_tool_streak": self.max_same_tool_streak,
            "max_no_progress_steps": self.max_no_progress_steps,
            "steps_used": self.steps_used,
            "llm_calls_used": self.llm_calls_used,
            "same_tool_streak": self.same_tool_streak,
            "no_progress_steps": self.no_progress_steps,
            "stop_reason": self.stop_reason,
            "allow_next_step": self.allow_next_step() if not self.stop_reason else False,
        }


# Back-compat alias used by older trace.max_steps_default callers via budgets
def max_steps_default() -> int:
    return max_steps_budget()
