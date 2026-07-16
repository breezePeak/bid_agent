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
    """When false (default), chat/pipeline stay on legacy behavior."""
    return _parse_bool(os.environ.get("AGENT_SUPERVISOR_ENABLED"), default=False)


def agent_use_tool_runtime() -> bool:
    """When true (default), explicit tool invoke uses tool_runtime."""
    return _parse_bool(os.environ.get("AGENT_USE_TOOL_RUNTIME"), default=True)
