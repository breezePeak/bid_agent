from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..research_service import ResearchCandidate


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WebSearchHit(StrictModel):
    hit_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    snippet: str = ""
    score: float | None = None
    publisher: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)


class ExtractedWebSource(StrictModel):
    source_id: str = Field(min_length=1)
    requested_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    raw_content: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1)
    extraction_provider: str = Field(min_length=1)
    extracted_at: str = Field(min_length=1)


class WebExtractResult(StrictModel):
    sources: list[ExtractedWebSource] = Field(default_factory=list)
    rejected_urls: list[dict] = Field(default_factory=list)


class ResearchClaim(StrictModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    required: bool = True
    claim_kind: Literal[
        "project_fact",
        "policy",
        "standard",
        "industry_status",
        "method",
        "risk_control",
        "acceptance_practice",
    ]
    expected_source_types: list[str] = Field(default_factory=list)
    minimum_support_rule: Literal[
        "one_primary_source",
        "one_authoritative_source",
        "two_independent_sources",
    ]


class ClaimAssessment(StrictModel):
    claim_id: str = Field(min_length=1)
    status: Literal["satisfied", "missing", "weak", "conflict", "prohibited"]
    supporting_source_ids: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class EvidenceSufficiencyReport(StrictModel):
    sufficient: bool
    claim_assessments: list[ClaimAssessment] = Field(default_factory=list)
    missing_claim_ids: list[str] = Field(default_factory=list)
    weak_claim_ids: list[str] = Field(default_factory=list)
    conflict_claim_ids: list[str] = Field(default_factory=list)
    completion_reason: Literal[
        "sufficient",
        "budget_exhausted",
        "no_relevant_sources",
        "no_search_results",
        "extract_failed",
        "prohibited_scope",
        "model_output_invalid",
        "provider_failed",
    ]

    @model_validator(mode="after")
    def required_lists_match_assessments(self) -> "EvidenceSufficiencyReport":
        by_status = {
            status: [item.claim_id for item in self.claim_assessments if item.status == status]
            for status in ("missing", "weak", "conflict")
        }
        if set(self.missing_claim_ids) != set(by_status["missing"]):
            raise ValueError("missing_claim_ids 与 ClaimAssessment 不一致")
        if set(self.weak_claim_ids) != set(by_status["weak"]):
            raise ValueError("weak_claim_ids 与 ClaimAssessment 不一致")
        if set(self.conflict_claim_ids) != set(by_status["conflict"]):
            raise ValueError("conflict_claim_ids 与 ClaimAssessment 不一致")
        return self


class ResearchReflection(StrictModel):
    confirmed_findings: list[str] = Field(default_factory=list)
    missing_claim_ids: list[str] = Field(default_factory=list)
    weak_claim_ids: list[str] = Field(default_factory=list)
    conflict_claim_ids: list[str] = Field(default_factory=list)
    next_action: Literal["search", "extract", "complete"]
    next_query: str | None = None
    selected_urls: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class ResearchUnit(StrictModel):
    unit_id: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1, max_length=4)
    question: str = Field(min_length=1)


class DeepResearchRunResult(StrictModel):
    run_id: str = Field(min_length=1)
    need_id: str = Field(min_length=1)
    status: Literal["completed", "partial", "failed", "prohibited"]
    candidates: list[ResearchCandidate] = Field(default_factory=list)
    sufficiency: EvidenceSufficiencyReport
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=4)
    extracted_sources: list[ExtractedWebSource] = Field(default_factory=list)
    support_by_claim: dict[str, list[str]] = Field(default_factory=dict)
    conflict_claim_ids: list[str] = Field(default_factory=list)
    search_call_count: int = Field(ge=0)
    extract_call_count: int = Field(ge=0)
    searched_queries: list[str] = Field(default_factory=list)
    discovered_urls: list[str] = Field(default_factory=list)
    extracted_urls: list[str] = Field(default_factory=list)
    rejected_urls: list[dict] = Field(default_factory=list)
    iterations: int = Field(ge=0)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)


class SupervisorPlan(StrictModel):
    claims: list[ResearchClaim] = Field(min_length=1, max_length=4)
    research_units: list[ResearchUnit] = Field(min_length=1, max_length=3)
