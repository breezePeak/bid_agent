"""Agent-facing API helpers (PR-A7 extraction seed).

Full FastAPI routers will mount here as web_app is split.
"""

from __future__ import annotations

from typing import Any


def agent_mode_payload() -> dict[str, Any]:
    from agent.flags import agent_supervisor_enabled, agent_use_tool_runtime
    from concurrency import concurrency_snapshot

    supervisor = agent_supervisor_enabled()
    return {
        "ok": True,
        "supervisor_enabled": supervisor,
        "use_tool_runtime": agent_use_tool_runtime(),
        "mode": "agent" if supervisor else "legacy",
        "mode_label": "Agent 模式" if supervisor else "流水线模式",
        "concurrency": concurrency_snapshot(),
    }
