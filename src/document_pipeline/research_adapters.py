"""Tavily-only external research adapter for V3 evidence resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .deep_research.config import DeepResearchConfig
from .deep_research.engine import DeepResearchEngine
from .deep_research.tavily_tools import TavilyWebTools
from .research_service import ResearchCandidate


class ResearchProviderAdapter(Protocol):
    provider_id: str

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]: ...


class DisabledResearchAdapter:
    provider_id = "disabled"

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        return []

    def runtime_status(self) -> dict[str, object]:
        return {
            "ready": True,
            "provider_id": self.provider_id,
            "reason": "WEB_AUTOMATION_DISABLED",
        }


def _configured_env_value(*keys: str) -> str:
    """Read an API secret from process settings or the authoritative env file."""

    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    configured_root = os.environ.get("BID_AGENT_CONFIG_ROOT", "").strip()
    root = Path(configured_root).resolve() if configured_root else Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in keys:
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    except OSError:
        pass
    return ""


class TavilySearchAdapter:
    """Retrieve and extract verifiable public source text through Tavily."""

    provider_id = "tavily"

    def __init__(self) -> None:
        self.api_key = _configured_env_value("BID_AGENT_TAVILY_API_KEY", "TAVILY_API_KEY")
        self.config = DeepResearchConfig.from_env()
        self.tools = TavilyWebTools(api_key=self.api_key, config=self.config)
        self.engine = DeepResearchEngine(self.tools, config=self.config)
        self.search_depth = self.tools.search_depth
        self.cache_fingerprint = self.engine.cache_fingerprint

    def runtime_status(self) -> dict[str, object]:
        return self.tools.runtime_status()

    def research_need(self, need, *, limit: int):
        return self.engine.run(need, limit=limit)

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        """Compatibility path whose evidence always comes from extracted pages."""

        if limit <= 0:
            return []
        hits = self.tools.web_search(question, limit=limit)
        extracted = self.tools.web_extract([hit.url for hit in hits])
        if not extracted.sources:
            raise RuntimeError("TAVILY_EXTRACT_NO_READABLE_SOURCE")
        from .deep_research.authority import classify_source_type

        return [
            ResearchCandidate(
                title=source.title,
                publisher=source.publisher,
                content=source.raw_content,
                source_url=source.final_url,
                source_type=classify_source_type(source.final_url),
                claim_types=("project_context", "method"),
            )
            for source in extracted.sources
        ]


class DeepResearchTavilyAdapter(TavilySearchAdapter):
    """EvidenceNeed-aware Tavily adapter backed by the deep research engine."""


def create_research_adapter(
    provider_id: str | None = None,
    *,
    attachment_paths: list[Path] | tuple[Path, ...] | None = None,
) -> ResearchProviderAdapter:
    """Create the configured adapter while enforcing the Tavily-only invariant."""

    selected = str(
        provider_id or os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "tavily")
    ).strip().lower()
    if selected == "tavily":
        if attachment_paths:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        return DeepResearchTavilyAdapter()
    if selected in {"", "disabled", "manual"}:
        if attachment_paths:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        return DisabledResearchAdapter()
    raise ValueError(f"V3_RESEARCH_PROVIDER_UNSUPPORTED: {selected}; only tavily is allowed")
