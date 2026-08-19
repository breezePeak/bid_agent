from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any, Protocol

from ..contracts import EvidenceNeed, EvidenceSourceType
from ..research_service import ResearchCandidate
from .config import DeepResearchConfig
from .contracts import (
    DeepResearchRunResult,
    EvidenceSufficiencyReport,
    ExtractedWebSource,
    ResearchClaim,
    ResearchUnit,
    SupervisorPlan,
    WebSearchHit,
)
from .prompts import (
    RESEARCHER_SYSTEM_PROMPT,
    SOURCE_ASSESSMENT_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
)
from .sufficiency import EvidenceSufficiencyGate
from .tavily_tools import TavilyWebTools


REFERENCE_COMMIT = "1b7d2e80db9faa586165c60e09096dbbfd483a64"
GRAPH_POLICY_VERSION = "v3.deep-research-control.v1"
PROMPT_VERSION = "v3.deep-research-prompts.v1"
_PROHIBITED_RE = re.compile(
    r"(?:本企业|我公司|投标人|供应商|承建方).{0,12}(?:资质|业绩|案例|人员|履历|证书|社保|财务|报价|承诺|法定代表人|授权|软件|设备|服务能力)"
    r"|(?:企业资质|企业业绩|企业案例|人员身份|人员履历|人员证书|企业财务|投标报价|投标承诺|法定代表人|授权信息)",
    re.IGNORECASE,
)


class ModelOutputInvalid(RuntimeError):
    pass


class DeepResearchActionProvider(Protocol):
    def plan(self, need: EvidenceNeed, *, max_claims: int, max_units: int) -> SupervisorPlan: ...

    def next_query(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        report: EvidenceSufficiencyReport | None,
        *,
        iteration: int,
        searched_queries: list[str],
    ) -> str: ...

    def select_urls(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        hits: list[WebSearchHit],
        *,
        limit: int,
    ) -> list[str]: ...

    def assess_support(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        sources: list[ExtractedWebSource],
    ) -> tuple[dict[str, list[str]], set[str]]: ...


class LLMDeepResearchActionProvider:
    def _json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        from llm_client import chat

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        error = ""
        for attempt in range(2):
            raw = chat(messages, temperature=0.0).strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            elif raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            try:
                parsed = json.loads(raw.strip())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError) as exc:
                error = type(exc).__name__
            messages.append({"role": "user", "content": "上次输出未通过 JSON 校验。仅返回一个符合 schema 的 JSON 对象。"})
        raise ModelOutputInvalid(f"STRUCTURED_MODEL_OUTPUT_INVALID:{error or 'not_object'}")

    def plan(self, need: EvidenceNeed, *, max_claims: int, max_units: int) -> SupervisorPlan:
        payload = {
            "need": need.model_dump(mode="json"),
            "limits": {"max_claims": max_claims, "max_units": max_units},
            "schema": SupervisorPlan.model_json_schema(),
        }
        return SupervisorPlan.model_validate(self._json(SUPERVISOR_SYSTEM_PROMPT, payload))

    def next_query(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        report: EvidenceSufficiencyReport | None,
        *,
        iteration: int,
        searched_queries: list[str],
    ) -> str:
        payload = {
            "need": need.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in claims],
            "last_sufficiency": report.model_dump(mode="json") if report else None,
            "iteration": iteration,
            "searched_queries": searched_queries,
            "schema": {"query": "non-empty search query"},
        }
        value = str(self._json(RESEARCHER_SYSTEM_PROMPT, payload).get("query") or "").strip()
        if not value or value in searched_queries:
            raise ModelOutputInvalid("RESEARCH_QUERY_INVALID_OR_DUPLICATE")
        return value[:500]

    def select_urls(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        hits: list[WebSearchHit],
        *,
        limit: int,
    ) -> list[str]:
        # URL selection is deterministic metadata ranking. Snippets never cross
        # the Extract boundary and cannot support a claim.
        ranked = sorted(hits, key=lambda item: (-(item.score or 0.0), item.url))
        return [item.url for item in ranked[:limit]]

    def assess_support(
        self,
        need: EvidenceNeed,
        claims: list[ResearchClaim],
        sources: list[ExtractedWebSource],
    ) -> tuple[dict[str, list[str]], set[str]]:
        payload = {
            "need": need.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in claims],
            "sources": [
                {
                    "source_id": item.source_id,
                    "url": item.final_url,
                    "title": item.title,
                    "raw_content": item.raw_content,
                }
                for item in sources
            ],
            "schema": {
                "support_by_claim": {"claim_id": ["source_id"]},
                "conflict_claim_ids": ["claim_id"],
            },
        }
        parsed = self._json(SOURCE_ASSESSMENT_SYSTEM_PROMPT, payload)
        known_claims = {item.claim_id for item in claims}
        known_sources = {item.source_id for item in sources}
        mapping: dict[str, list[str]] = {}
        raw_mapping = parsed.get("support_by_claim") or {}
        if not isinstance(raw_mapping, dict):
            raise ModelOutputInvalid("SUPPORT_MAPPING_INVALID")
        for claim_id, source_ids in raw_mapping.items():
            if claim_id not in known_claims or not isinstance(source_ids, list):
                continue
            mapping[claim_id] = [source_id for source_id in source_ids if source_id in known_sources]
        conflicts = {
            item for item in (parsed.get("conflict_claim_ids") or []) if item in known_claims
        }
        return mapping, conflicts


class DeepResearchEngine:
    """Embedded, bounded research control loop invoked only by ResearchService."""

    def __init__(
        self,
        tools: TavilyWebTools,
        *,
        actions: DeepResearchActionProvider | None = None,
        config: DeepResearchConfig | None = None,
        gate: EvidenceSufficiencyGate | None = None,
    ) -> None:
        self.tools = tools
        self.config = config or tools.config
        self.actions = actions or LLMDeepResearchActionProvider()
        self.gate = gate or EvidenceSufficiencyGate()

    @property
    def cache_fingerprint(self) -> str:
        model = self.config.model or os.environ.get("OPENAI_MODEL", "")
        payload = {
            "graph_policy": GRAPH_POLICY_VERSION,
            "sufficiency_policy": self.gate.policy_version,
            "reference_commit": REFERENCE_COMMIT,
            "prompt_version": PROMPT_VERSION,
            "search_depth": self.tools.search_depth,
            "extract_depth": self.config.extract_depth,
            "max_search_calls": self.config.max_search_calls,
            "max_tool_calls_per_unit": self.config.max_tool_calls_per_unit,
            "max_total_extract_urls": self.config.max_total_extract_urls,
            "model": model,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def run(self, need: EvidenceNeed, *, limit: int) -> DeepResearchRunResult:
        started = datetime.now(UTC)
        run_id = f"DR-{uuid.uuid4().hex}"
        empty_report = lambda reason: EvidenceSufficiencyReport(
            sufficient=False,
            claim_assessments=[],
            missing_claim_ids=[],
            weak_claim_ids=[],
            conflict_claim_ids=[],
            completion_reason=reason,
        )
        if _PROHIBITED_RE.search(f"{need.question}\n{json.dumps(need.relevance_context, ensure_ascii=False)}"):
            return self._result(run_id, need, started, "prohibited", [], empty_report("prohibited_scope"), [], [], [], [], [], 0, 0, 0)
        if not self.config.enabled:
            return self._result(run_id, need, started, "failed", [], empty_report("provider_failed"), [], [], [], [], [], 0, 0, 0)
        try:
            plan = self.actions.plan(
                need,
                max_claims=4,
                max_units=self.config.max_research_units,
            )
        except (Exception,) as exc:
            reason = "model_output_invalid" if isinstance(exc, (ModelOutputInvalid, ValueError)) else "provider_failed"
            return self._result(run_id, need, started, "failed", [], empty_report(reason), [], [], [], [], [], 0, 0, 0)
        claims = plan.claims[:4]
        search_budget = min(need.query_budget, self.config.max_search_calls)
        tool_budget = self.config.max_tool_calls_per_unit * max(1, len(plan.research_units))
        searched_queries: list[str] = []
        discovered_urls: list[str] = []
        extracted_urls: list[str] = []
        rejected_urls: list[dict] = []
        sources: list[ExtractedWebSource] = []
        report: EvidenceSufficiencyReport | None = None
        search_calls = 0
        extract_calls = 0
        iterations = 0
        support_by_claim: dict[str, list[str]] = {}
        conflicts: set[str] = set()
        provider_failed = False
        model_invalid = False
        while (
            iterations < self.config.max_supervisor_iterations
            and search_calls < search_budget
            and search_calls + extract_calls < tool_budget
        ):
            iterations += 1
            remaining_search_calls = min(
                search_budget - search_calls,
                tool_budget - search_calls - extract_calls,
            )
            active_units = plan.research_units[:remaining_search_calls]
            search_tasks: list[tuple[str, list[ResearchClaim]]] = []
            try:
                by_claim = {claim.claim_id: claim for claim in claims}
                for unit_index, unit in enumerate(active_units):
                    unit_claims = [by_claim[item] for item in unit.claim_ids if item in by_claim] or claims
                    query = self.actions.next_query(
                        need.model_copy(update={"question": unit.question}),
                        unit_claims,
                        report,
                        iteration=(iterations if unit_index == 0 else iterations * 100 + unit_index),
                        searched_queries=list(searched_queries),
                    )
                    if query in searched_queries:
                        raise ModelOutputInvalid("RESEARCH_QUERY_INVALID_OR_DUPLICATE")
                    searched_queries.append(query)
                    search_tasks.append((query, unit_claims))
            except ModelOutputInvalid:
                model_invalid = True
                break
            hits: list[WebSearchHit] = []
            search_errors: list[Exception] = []
            if len(search_tasks) == 1:
                try:
                    hits.extend(self.tools.web_search(search_tasks[0][0], limit=min(limit, self.config.max_search_results)))
                except Exception as exc:
                    search_errors.append(exc)
                search_calls += 1
            elif search_tasks:
                with ThreadPoolExecutor(max_workers=min(len(search_tasks), self.config.max_research_units)) as pool:
                    futures = {
                        pool.submit(self.tools.web_search, query, limit=min(limit, self.config.max_search_results)): query
                        for query, _unit_claims in search_tasks
                    }
                    for future in as_completed(futures):
                        try:
                            hits.extend(future.result())
                        except Exception as exc:
                            search_errors.append(exc)
                search_calls += len(search_tasks)
            for exc in search_errors:
                rejected_urls.append({"url": "", "reason": f"SEARCH_FAILED:{type(exc).__name__}"})
            if search_errors and not hits:
                provider_failed = True
                break
            unique_hits: list[WebSearchHit] = []
            seen_hit_urls: set[str] = set()
            for hit in hits:
                if hit.url not in seen_hit_urls:
                    unique_hits.append(hit)
                    seen_hit_urls.add(hit.url)
                if hit.url not in discovered_urls:
                    discovered_urls.append(hit.url)
            remaining = self.config.max_total_extract_urls - len(extracted_urls)
            selectable = [hit for hit in unique_hits if hit.url not in extracted_urls]
            selected = self.actions.select_urls(
                need,
                claims,
                selectable,
                limit=min(remaining, self.config.max_extract_urls_per_round),
            )
            if selected and search_calls + extract_calls < tool_budget:
                try:
                    extracted = self.tools.web_extract(selected)
                    extract_calls += 1
                    rejected_urls.extend(extracted.rejected_urls)
                    for source in extracted.sources:
                        if source.final_url not in extracted_urls:
                            sources.append(source)
                            extracted_urls.append(source.final_url)
                except Exception as exc:
                    rejected_urls.extend(
                        {"url": url, "reason": f"EXTRACT_FAILED:{type(exc).__name__}"}
                        for url in selected
                    )
            try:
                support_by_claim, conflicts = self.actions.assess_support(need, claims, sources)
            except ModelOutputInvalid:
                model_invalid = True
                break
            except Exception:
                model_invalid = True
                break
            budget_exhausted = search_calls >= search_budget or iterations >= self.config.max_supervisor_iterations
            report = self.gate.assess(
                need=need,
                claims=claims,
                sources=sources,
                support_by_claim=support_by_claim,
                conflict_claim_ids=conflicts,
                budget_exhausted=budget_exhausted,
            )
            if report.sufficient:
                break
        if report is None or model_invalid or provider_failed:
            report = self.gate.assess(
                need=need,
                claims=claims,
                sources=sources,
                support_by_claim=support_by_claim,
                conflict_claim_ids=conflicts,
                budget_exhausted=True,
                provider_failed=provider_failed,
                model_output_invalid=model_invalid,
            )
        candidates = self._candidates(claims, sources, support_by_claim)
        status = "completed" if report.sufficient else ("partial" if candidates else "failed")
        return self._result(
            run_id, need, started, status, candidates, report, claims,
            searched_queries, discovered_urls, extracted_urls, rejected_urls,
            search_calls, extract_calls, iterations,
        )

    @staticmethod
    def _candidates(
        claims: list[ResearchClaim],
        sources: list[ExtractedWebSource],
        support_by_claim: dict[str, list[str]],
    ) -> list[ResearchCandidate]:
        by_id = {claim.claim_id: claim for claim in claims}
        claims_by_source: dict[str, list[ResearchClaim]] = {}
        for claim_id, source_ids in support_by_claim.items():
            if claim_id not in by_id:
                continue
            for source_id in source_ids:
                claims_by_source.setdefault(source_id, []).append(by_id[claim_id])
        candidates: list[ResearchCandidate] = []
        for source in sources:
            supported = claims_by_source.get(source.source_id, [])
            if not supported:
                continue
            kinds = {item.claim_kind for item in supported}
            source_type = EvidenceSourceType.WEB
            host = source.publisher.lower()
            if host.endswith((".gov.cn", ".gov")):
                source_type = EvidenceSourceType.OFFICIAL
            elif any(kind in {"standard", "policy"} for kind in kinds):
                source_type = EvidenceSourceType.STANDARD
            claim_types = ["method"]
            if kinds & {"policy", "standard"}:
                claim_types.append("standard")
            if "project_fact" in kinds:
                claim_types.append("project_context")
            candidates.append(
                ResearchCandidate(
                    title=source.title,
                    publisher=source.publisher,
                    content=source.raw_content,
                    source_url=source.final_url,
                    source_type=source_type,
                    claim_types=tuple(dict.fromkeys(claim_types)),
                    supporting_excerpt=source.raw_content[:800],
                )
            )
        return candidates

    @staticmethod
    def _result(
        run_id: str,
        need: EvidenceNeed,
        started: datetime,
        status: str,
        candidates: list[ResearchCandidate],
        report: EvidenceSufficiencyReport,
        claims: list[ResearchClaim],
        searched_queries: list[str],
        discovered_urls: list[str],
        extracted_urls: list[str],
        rejected_urls: list[dict],
        search_calls: int,
        extract_calls: int,
        iterations: int,
    ) -> DeepResearchRunResult:
        return DeepResearchRunResult(
            run_id=run_id,
            need_id=need.need_id,
            status=status,
            candidates=candidates,
            sufficiency=report,
            claims=claims,
            search_call_count=search_calls,
            extract_call_count=extract_calls,
            searched_queries=searched_queries,
            discovered_urls=discovered_urls,
            extracted_urls=extracted_urls,
            rejected_urls=rejected_urls,
            iterations=iterations,
            started_at=started.isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
        )
