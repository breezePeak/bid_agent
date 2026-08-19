from __future__ import annotations

import os
from dataclasses import dataclass


def _integer(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 {low}-{high} 的整数") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}-{high} 的整数")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "1" if default else "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值（0/1、true/false）")


@dataclass(frozen=True)
class DeepResearchConfig:
    enabled: bool
    max_supervisor_iterations: int
    max_research_units: int
    max_search_calls: int
    max_tool_calls_per_unit: int
    max_search_results: int
    max_extract_urls_per_round: int
    max_total_extract_urls: int
    max_source_chars: int
    extract_depth: str
    extract_timeout_seconds: int
    model: str

    @classmethod
    def from_env(cls) -> "DeepResearchConfig":
        depth = os.environ.get("BID_AGENT_TAVILY_EXTRACT_DEPTH", "basic").strip().lower()
        if depth not in {"basic", "advanced"}:
            raise ValueError("BID_AGENT_TAVILY_EXTRACT_DEPTH 必须是 basic 或 advanced")
        return cls(
            enabled=_boolean("BID_AGENT_DEEP_RESEARCH_ENABLED", True),
            max_supervisor_iterations=_integer("BID_AGENT_DEEP_RESEARCH_MAX_SUPERVISOR_ITERATIONS", 4, 1, 10),
            max_research_units=_integer("BID_AGENT_DEEP_RESEARCH_MAX_RESEARCH_UNITS", 3, 1, 3),
            max_search_calls=_integer("BID_AGENT_DEEP_RESEARCH_MAX_SEARCH_CALLS", 4, 1, 20),
            max_tool_calls_per_unit=_integer("BID_AGENT_DEEP_RESEARCH_MAX_TOOL_CALLS_PER_UNIT", 6, 2, 20),
            max_search_results=_integer("BID_AGENT_DEEP_RESEARCH_MAX_SEARCH_RESULTS", 8, 1, 20),
            max_extract_urls_per_round=_integer("BID_AGENT_DEEP_RESEARCH_MAX_EXTRACT_URLS_PER_ROUND", 4, 1, 10),
            max_total_extract_urls=_integer("BID_AGENT_DEEP_RESEARCH_MAX_TOTAL_EXTRACT_URLS", 12, 1, 30),
            max_source_chars=_integer("BID_AGENT_DEEP_RESEARCH_MAX_SOURCE_CHARS", 60_000, 1_000, 200_000),
            extract_depth=depth,
            extract_timeout_seconds=_integer("BID_AGENT_TAVILY_EXTRACT_TIMEOUT_SECONDS", 30, 5, 120),
            model=os.environ.get("BID_AGENT_DEEP_RESEARCH_MODEL", "").strip(),
        )

