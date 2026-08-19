from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import EvidenceNeed
from document_pipeline.deep_research.contracts import ExtractedWebSource, ResearchClaim
from document_pipeline.deep_research.sufficiency import EvidenceSufficiencyGate


def _source(source_id: str, url: str, publisher: str, content: str) -> ExtractedWebSource:
    return ExtractedWebSource(
        source_id=source_id,
        requested_url=url,
        final_url=url,
        title=publisher,
        publisher=publisher,
        raw_content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        content_type="text/markdown",
        extraction_provider="fake",
        extracted_at="2026-08-19T00:00:00+00:00",
    )


def _need() -> EvidenceNeed:
    return EvidenceNeed(need_id="N1", question="查询政策", topic_id="T1", deadline_stage="write", query_budget=3, project_anchors=["当前项目"])


def test_policy_blog_is_weak_but_government_source_satisfies() -> None:
    claim = ResearchClaim(claim_id="C1", statement="政策要求", required=True, claim_kind="policy", expected_source_types=["official"], minimum_support_rule="one_authoritative_source")
    blog = _source("S1", "https://blog.example/a", "行业博客", "政策解读正文" * 20)
    official = _source("S2", "https://agency.gov.cn/a", "人民政府", "政策正式发布正文" * 20)
    gate = EvidenceSufficiencyGate()
    weak = gate.assess(need=_need(), claims=[claim], sources=[blog], support_by_claim={"C1": ["S1"]})
    assert weak.claim_assessments[0].status == "weak"
    passed = gate.assess(need=_need(), claims=[claim], sources=[blog, official], support_by_claim={"C1": ["S1", "S2"]})
    assert passed.sufficient is True


def test_project_fact_requires_current_project_anchor_and_conflict_blocks() -> None:
    claim = ResearchClaim(claim_id="C1", statement="当前项目事实", required=True, claim_kind="project_fact", expected_source_types=["official"], minimum_support_rule="one_primary_source")
    similar = _source("S1", "https://example.com/a", "案例站", "类似项目资料" * 20)
    gate = EvidenceSufficiencyGate()
    report = gate.assess(need=_need(), claims=[claim], sources=[similar], support_by_claim={"C1": ["S1"]})
    assert report.claim_assessments[0].status == "weak"
    conflict = gate.assess(need=_need(), claims=[claim], sources=[similar], support_by_claim={"C1": ["S1"]}, conflict_claim_ids={"C1"}, budget_exhausted=True)
    assert conflict.sufficient is False
    assert conflict.conflict_claim_ids == ["C1"]
