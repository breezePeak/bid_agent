from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.deep_research.config import DeepResearchConfig
from document_pipeline.deep_research.tavily_tools import TavilyWebTools, normalize_public_url


def _config() -> DeepResearchConfig:
    return DeepResearchConfig(True, 4, 3, 4, 6, 8, 4, 12, 60_000, "basic", 30, "")


def test_search_is_metadata_only_and_answer_is_ignored() -> None:
    requests = []

    def transport(url, payload, timeout, api_key):
        requests.append((url, payload, api_key))
        return {
            "answer": "不得进入证据",
            "results": [{"title": "标题", "url": "https://example.com/a#x", "content": "只允许作为 snippet", "raw_content": "搜索阶段也不得读取", "score": 0.9}],
        }

    tools = TavilyWebTools(api_key="secret", config=_config(), transport=transport)
    hits = tools.web_search("测试查询")
    assert hits[0].snippet == "只允许作为 snippet"
    assert hits[0].url == "https://example.com/a"
    assert requests[0][1]["include_answer"] is False
    assert requests[0][1]["include_raw_content"] is False
    assert "chunks_per_source" not in requests[0][1]


def test_extract_uses_only_raw_content_and_records_failures() -> None:
    def transport(url, payload, timeout, api_key):
        assert "query" not in payload
        assert "chunks_per_source" not in payload
        return {
            "results": [{"url": "https://example.com/a", "raw_content": "原文" * 60}],
            "failed_results": [{"url": "https://example.com/b", "error": "blocked"}],
        }

    result = TavilyWebTools(api_key="secret", config=_config(), transport=transport).web_extract(
        ["https://example.com/a#x", "https://example.com/b"]
    )
    assert result.sources[0].raw_content == "原文" * 60
    assert len(result.sources[0].content_hash) == 64
    assert any(item["reason"] == "blocked" for item in result.rejected_urls)


def test_url_safety_and_normalization() -> None:
    assert normalize_public_url("http://localhost/x") == ""
    assert normalize_public_url("http://127.0.0.1/x") == ""
    assert normalize_public_url("http://10.0.0.1/x") == ""
    assert normalize_public_url("https://service.local/x") == ""
    assert normalize_public_url("javascript:alert(1)") == ""
    assert normalize_public_url("https://example.com/a#fragment") == "https://example.com/a"


@pytest.mark.skipif(
    os.environ.get("BID_AGENT_RUN_TAVILY_SMOKE") != "1"
    or not os.environ.get("BID_AGENT_TAVILY_API_KEY"),
    reason="requires explicit Tavily smoke opt-in and API key",
)
def test_optional_real_tavily_search_and_extract_smoke() -> None:
    tools = TavilyWebTools(config=_config())
    hits = tools.web_search("中国政府网 政策", limit=1)
    assert hits
    extracted = tools.web_extract([hits[0].url])
    assert extracted.sources or extracted.rejected_urls
