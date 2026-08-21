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
from document_pipeline.deep_research.engine import DeepResearchEngine, ModelOutputInvalid


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
    assert result.sufficiency.completion_reason == "no_relevant_sources"


def test_enterprise_fact_is_blocked_before_search() -> None:
    tools = _Tools()
    result = DeepResearchEngine(tools, actions=_Actions(), config=_config()).run(_need("查询本企业资质和人员证书"), limit=8)
    assert result.status == "prohibited"
    assert result.search_call_count == 0
    assert tools.round == 0


class _EmptySearchTools(_Tools):
    def web_search(self, query, *, limit):
        self.round += 1
        return []


class _ExtractFailureTools(_Tools):
    def web_search(self, query, *, limit):
        self.round += 1
        return [WebSearchHit(hit_id="H1", query=query, title="结果", url="https://agency.gov.cn/a", snippet="摘要", score=1.0, publisher="agency.gov.cn", provider_id="fake")]

    def web_extract(self, urls):
        raise RuntimeError("extract failed")


def test_no_results_and_extract_failure_have_precise_reasons_and_counts() -> None:
    empty = DeepResearchEngine(_EmptySearchTools(), actions=_Actions(), config=_config(1)).run(_need(), limit=8)
    assert empty.search_call_count == 1
    assert empty.extract_call_count == 0
    assert empty.sufficiency.completion_reason == "no_search_results"

    failed = DeepResearchEngine(_ExtractFailureTools(), actions=_Actions(), config=_config(1)).run(_need(), limit=8)
    assert failed.search_call_count == 1
    assert failed.extract_call_count == 1
    assert failed.sufficiency.completion_reason == "extract_failed"


class _DisabledActions(_Actions):
    def plan(self, need, *, max_claims, max_units):
        raise AssertionError("disabled deep research must not invoke the supervisor")


def test_disabled_deep_research_runs_one_search_extract_round() -> None:
    config = _config(4)
    config = DeepResearchConfig(False, config.max_supervisor_iterations, config.max_research_units, config.max_search_calls, config.max_tool_calls_per_unit, config.max_search_results, config.max_extract_urls_per_round, config.max_total_extract_urls, config.max_source_chars, config.extract_depth, config.extract_timeout_seconds, config.model)
    tools = _Tools()
    tools.round = 1  # the single call returns the official source
    result = DeepResearchEngine(tools, actions=_DisabledActions(), config=config).run(_need(), limit=8)
    assert result.search_call_count == 1
    assert result.extract_call_count == 1
    assert result.sufficiency.completion_reason != "provider_failed"


class _InvalidPlanActions(_Actions):
    def plan(self, need, *, max_claims, max_units):
        raise ValueError("invalid structured plan")


def test_invalid_supervisor_plan_falls_back_to_real_search() -> None:
    tools = _Tools()
    tools.round = 1  # the fallback search receives the official source
    result = DeepResearchEngine(
        tools, actions=_InvalidPlanActions(), config=_config()
    ).run(_need(), limit=8)
    assert result.search_call_count == 1
    assert result.extract_call_count == 1
    assert result.discovered_urls == ["https://agency.gov.cn/a"]
    assert result.sufficiency.completion_reason != "model_output_invalid"


class _InvalidQueryActions(_Actions):
    def next_query(self, need, claims, report, *, iteration, searched_queries):
        raise ModelOutputInvalid("invalid query JSON")


def test_invalid_query_json_falls_back_to_real_search() -> None:
    tools = _Tools()
    tools.round = 1  # the deterministic fallback receives the official source
    result = DeepResearchEngine(
        tools, actions=_InvalidQueryActions(), config=_config(1)
    ).run(_need("年度调查技术规程"), limit=8)

    assert result.search_call_count == 1
    assert result.extract_call_count == 1
    assert result.searched_queries
    assert "年度调查技术规程" in result.searched_queries[0]
    assert result.sufficiency.completion_reason != "model_output_invalid"


class _InvalidSupportActions(_Actions):
    def assess_support(self, need, claims, sources):
        raise ModelOutputInvalid("invalid support JSON")


def test_invalid_support_json_uses_conservative_text_mapping() -> None:
    tools = _Tools()
    tools.round = 1  # return the official source containing the policy keyword
    result = DeepResearchEngine(
        tools, actions=_InvalidSupportActions(), config=_config(1)
    ).run(_need("年度调查政策要求"), limit=8)

    assert result.search_call_count == 1
    assert result.support_by_claim == {"C1": ["S2"]}
    assert result.sufficiency.completion_reason != "model_output_invalid"


class _MultiActions(_Actions):
    def plan(self, need, *, max_claims, max_units):
        claims = [
            ResearchClaim(claim_id=f"C{i}", statement=f"政策要求{i}", required=True, claim_kind="policy", expected_source_types=["official"], minimum_support_rule="one_authoritative_source")
            for i in range(1, 4)
        ]
        units = [ResearchUnit(unit_id=f"U{i}", claim_ids=[f"C{i}"], question=f"研究单元{i}") for i in range(1, 4)]
        return SupervisorPlan(claims=claims, research_units=units)

    def next_query(self, need, claims, report, *, iteration, searched_queries):
        return f"{need.question}-{'补充' if report else '首轮'}-{iteration}"

    def assess_support(self, need, claims, sources):
        return {claim.claim_id: [source.source_id for source in sources] for claim in claims}, set()


def test_multiple_units_reserve_a_supplemental_search_call() -> None:
    tools = _Tools()
    result = DeepResearchEngine(tools, actions=_MultiActions(), config=_config(3)).run(_need(), limit=8)
    assert result.search_call_count == 3
    assert result.iterations == 2
