from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import EvidenceNeed
from document_pipeline.deep_research.config import DeepResearchConfig
from document_pipeline.deep_research.contracts import (
    ExtractedWebSource,
    ResearchClaim,
    ResearchUnit,
    SupervisorPlan,
    WebExtractResult,
    WebSearchHit,
)
from document_pipeline.deep_research.engine import DeepResearchEngine


def _config(search_calls: int = 4) -> DeepResearchConfig:
    return DeepResearchConfig(True, 4, 3, search_calls, 6, 8, 4, 12, 60_000, "basic", 30, "fake")


class _Actions:
    def plan(self, need, *, max_claims, max_units):
        claim = ResearchClaim(claim_id="C1", statement="政策要求", required=True, claim_kind="policy", expected_source_types=["official"], minimum_support_rule="one_authoritative_source")
        return SupervisorPlan(claims=[claim], research_units=[ResearchUnit(unit_id="U1", claim_ids=["C1"], question=need.question)])

    def next_query(self, need, claims, report, *, iteration, searched_queries):
        return f"政策查询 第{iteration}轮"

    def select_urls(self, need, claims, hits, *, limit):
        return [item.url for item in hits[:limit]]

    def assess_support(self, need, claims, sources):
        return {"C1": [item.source_id for item in sources]}, set()


class _Tools:
    search_depth = "basic"

    def __init__(self):
        self.config = _config()
        self.round = 0

    def web_search(self, query, *, limit):
        self.round += 1
        host = "blog.example" if self.round == 1 else "agency.gov.cn"
        return [WebSearchHit(hit_id=f"H{self.round}", query=query, title="结果", url=f"https://{host}/a", snippet="摘要不能作为证据", score=1.0, publisher=host, provider_id="fake")]

    def web_extract(self, urls):
        url = urls[0]
        content = ("政府正式政策原文" if "gov.cn" in url else "博客政策解读") * 30
        digest = hashlib.sha256(content.encode()).hexdigest()
        source = ExtractedWebSource(source_id=f"S{self.round}", requested_url=url, final_url=url, title=("人民政府" if "gov.cn" in url else "博客"), publisher=("人民政府" if "gov.cn" in url else "博客"), raw_content=content, content_hash=digest, content_type="text/markdown", extraction_provider="fake", extracted_at="2026-08-19T00:00:00+00:00")
        return WebExtractResult(sources=[source], rejected_urls=[])


def _need(question: str = "查询公开政策") -> EvidenceNeed:
    return EvidenceNeed(need_id="N1", question=question, topic_id="T1", deadline_stage="write", query_budget=4)


def test_second_query_fills_gap_and_stops_early() -> None:
    tools = _Tools()
    result = DeepResearchEngine(tools, actions=_Actions(), config=_config()).run(_need(), limit=8)
    assert result.status == "completed"
    assert result.search_call_count == 2
    assert result.extract_call_count == 2
    assert result.sufficiency.sufficient is True
    assert all(candidate.content != "摘要不能作为证据" for candidate in result.candidates)


def test_budget_exhaustion_is_partial_gap() -> None:
    tools = _Tools()
    result = DeepResearchEngine(tools, actions=_Actions(), config=_config(1)).run(_need(), limit=8)
    assert result.status == "partial"
    assert result.sufficiency.sufficient is False
    assert result.sufficiency.completion_reason == "budget_exhausted"


def test_enterprise_fact_is_blocked_before_search() -> None:
    tools = _Tools()
    result = DeepResearchEngine(tools, actions=_Actions(), config=_config()).run(_need("查询本企业资质和人员证书"), limit=8)
    assert result.status == "prohibited"
    assert result.search_call_count == 0
    assert tools.round == 0
