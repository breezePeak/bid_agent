from __future__ import annotations

import urllib.parse
from collections import defaultdict

from ..contracts import EvidenceNeed
from .contracts import (
    ClaimAssessment,
    EvidenceSufficiencyReport,
    ExtractedWebSource,
    ResearchClaim,
)


def _official(source: ExtractedWebSource) -> bool:
    host = (urllib.parse.urlsplit(source.final_url).hostname or "").lower()
    text = f"{source.title} {source.publisher}".lower()
    return (
        host.endswith(".gov.cn")
        or host.endswith(".gov")
        or host.endswith(".org.cn") and any(word in text for word in ("标准", "协会", "学会"))
        or any(word in text for word in ("人民政府", "政府部门", "国家标准", "标准化管理委员会"))
    )


def _standard_or_academic(source: ExtractedWebSource) -> bool:
    host = (urllib.parse.urlsplit(source.final_url).hostname or "").lower()
    text = f"{source.title} {source.publisher}".lower()
    return _official(source) or host.endswith(".edu.cn") or any(
        word in text for word in ("标准", "规范", "规程", "学报", "大学", "研究院")
    )


class EvidenceSufficiencyGate:
    """Deterministic final authority gate; model completion is only a request."""

    policy_version = "v3.deep-research-sufficiency.v1"

    def assess(
        self,
        *,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        sources: list[ExtractedWebSource],
        support_by_claim: dict[str, list[str]] | None = None,
        conflict_claim_ids: set[str] | None = None,
        prohibited_claim_ids: set[str] | None = None,
        budget_exhausted: bool = False,
        provider_failed: bool = False,
        model_output_invalid: bool = False,
    ) -> EvidenceSufficiencyReport:
        support_by_claim = support_by_claim or {}
        conflict_claim_ids = conflict_claim_ids or set()
        prohibited_claim_ids = prohibited_claim_ids or set()
        source_by_id = {source.source_id: source for source in sources}
        assessments: list[ClaimAssessment] = []
        for claim in claims:
            source_ids = list(
                dict.fromkeys(
                    source_id
                    for source_id in support_by_claim.get(claim.claim_id, [])
                    if source_id in source_by_id
                )
            )
            supported = [source_by_id[source_id] for source_id in source_ids]
            if claim.claim_id in prohibited_claim_ids:
                status, explanation = "prohibited", "该 Claim 属于禁止联网补充的企业事实范围。"
            elif claim.claim_id in conflict_claim_ids:
                status, explanation = "conflict", "权威来源之间存在尚未消除的关键事实冲突。"
            elif not supported:
                status, explanation = "missing", "没有成功 Extract 的网页原文支持该 Claim。"
            else:
                status, explanation = self._authority_status(need, claim, supported)
            assessments.append(
                ClaimAssessment(
                    claim_id=claim.claim_id,
                    status=status,
                    supporting_source_ids=source_ids,
                    explanation=explanation,
                )
            )
        required = [item for item in assessments if next(c for c in claims if c.claim_id == item.claim_id).required]
        sufficient = bool(required) and all(item.status == "satisfied" for item in required)
        missing = [item.claim_id for item in assessments if item.status == "missing"]
        weak = [item.claim_id for item in assessments if item.status == "weak"]
        conflict = [item.claim_id for item in assessments if item.status == "conflict"]
        if sufficient:
            reason = "sufficient"
        elif prohibited_claim_ids:
            reason = "prohibited_scope"
        elif model_output_invalid:
            reason = "model_output_invalid"
        elif provider_failed:
            reason = "provider_failed"
        elif not sources:
            reason = "extract_failed" if support_by_claim else "no_relevant_sources"
        elif budget_exhausted:
            reason = "budget_exhausted"
        else:
            reason = "no_relevant_sources"
        return EvidenceSufficiencyReport(
            sufficient=sufficient,
            claim_assessments=assessments,
            missing_claim_ids=missing,
            weak_claim_ids=weak,
            conflict_claim_ids=conflict,
            completion_reason=reason,
        )

    @staticmethod
    def _authority_status(
        need: EvidenceNeed,
        claim: ResearchClaim,
        sources: list[ExtractedWebSource],
    ) -> tuple[str, str]:
        if claim.claim_kind == "project_fact":
            anchors = [item.casefold() for item in need.project_anchors if len(item.strip()) >= 3]
            matched = any(
                any(anchor in f"{source.title}\n{source.raw_content}".casefold() for anchor in anchors)
                for source in sources
            )
            if not anchors or not matched:
                return "weak", "项目事实未与当前项目 anchor 直接匹配，类似项目不能替代。"
        if claim.claim_kind == "policy" and not any(_official(source) for source in sources):
            return "weak", "政策 Claim 缺少政府或正式发布机构来源。"
        if claim.claim_kind == "standard" and not any(_standard_or_academic(source) for source in sources):
            return "weak", "标准 Claim 缺少标准站、政府站或正式标准发布机构来源。"
        if claim.claim_kind == "industry_status":
            publishers = {source.publisher.casefold() for source in sources}
            if len(publishers) < 2 and not any(_official(source) for source in sources):
                return "weak", "行业现状需要两个独立来源或一个权威统计来源。"
        if claim.claim_kind in {"method", "risk_control", "acceptance_practice"} and not any(
            _standard_or_academic(source) for source in sources
        ):
            return "weak", "方法、风险或验收 Claim 缺少标准、官方、学术或高相关来源。"
        if claim.minimum_support_rule == "two_independent_sources":
            hosts = {
                urllib.parse.urlsplit(source.final_url).hostname or source.publisher.casefold()
                for source in sources
            }
            if len(hosts) < 2:
                return "weak", "Claim 要求两个相互独立的来源。"
        if claim.minimum_support_rule == "one_authoritative_source" and not any(
            _official(source) or _standard_or_academic(source) for source in sources
        ):
            return "weak", "Claim 缺少权威来源。"
        return "satisfied", "已由成功 Extract 的原文及所需权威级别支持。"
