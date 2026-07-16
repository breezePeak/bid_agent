"""Agent capability layer: tools, runtime, supervisor (PR-3).

Flags default keep legacy chat/pipeline behavior.
"""

from agent.flags import agent_supervisor_enabled, agent_use_tool_runtime
from agent.tool_registry import get_tool, list_tools, tool_manifest
from agent.tool_runtime import invoke
from agent.types import ToolResult, ToolSpec

try:
    from agent.supervisor import run_supervisor_turn
except Exception:  # pragma: no cover
    run_supervisor_turn = None  # type: ignore

__all__ = [
    "ToolResult",
    "ToolSpec",
    "agent_supervisor_enabled",
    "agent_use_tool_runtime",
    "get_tool",
    "invoke",
    "list_tools",
    "tool_manifest",
    "run_supervisor_turn",
]
