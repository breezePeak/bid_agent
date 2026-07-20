from __future__ import annotations

import os


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def agent_supervisor_enabled() -> bool:
    """When true (PR-A3 default), Web/CLI chat uses Supervisor as product entry.

    Set AGENT_SUPERVISOR_ENABLED=false for emergency rollback to legacy orchestrator.
    """
    return _parse_bool(os.environ.get("AGENT_SUPERVISOR_ENABLED"), default=True)


def agent_use_tool_runtime() -> bool:
    """When true (default), explicit tool invoke uses tool_runtime."""
    return _parse_bool(os.environ.get("AGENT_USE_TOOL_RUNTIME"), default=True)
