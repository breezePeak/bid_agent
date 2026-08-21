"""Content-unit research decisions made by the shared chapter planner."""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any, Callable

from control_plane import ControlPlaneError, WorkspaceContext
from utils import read_json, write_json

from .chapter_research_planner import plan_chapter_research
from .contracts import EvidenceNeed, ResearchDecision, ResearchQuery, WriterInputBundle
from .input_manifest import V3_ROOT
from .research_adapters import create_research_adapter
from .research_service import ResearchService
from .writer_policy import RESEARCH_DECISION_POLICY_VERSION


WRITER_RESEARCH_REPORT_PATH = V3_ROOT / "evidence" / "writer_research.json"
_PROHIBITED_SCOPES = [
    "企业资质与资格",
    "企业业绩与案例",
    "人员身份、履历、证书与社保",
    "报价、财务、承诺与投标函事实",
]
_EXPLICIT_RESEARCH_RE = re.compile(
    r"(?:查(?:资料|一下|一查)?|检索|搜索|联网|网上查|帮我找|查找|再搜|重搜|重新搜).{0,24}"
    r"|(?:资料|政策|规范|标准|文件).{0,12}(?:查|检索|搜索|找)",
    re.I,
)


def _research_max_attempts() -> int:
    """Return the bounded number of full retrieval attempts for one query."""

    raw = str(os.environ.get("BID_AGENT_WRITER_RESEARCH_MAX_ATTEMPTS", "3")).strip()
    try:
        value = int(raw)
    except ValueError:
        value = 3
    return max(1, min(value, 5))


def _need_for_attempt(need: EvidenceNeed, attempt: int) -> tuple[EvidenceNeed, str]:
    """Give each outer retry a materially different retrieval strategy."""

    strategies = {
        1: (
            "current_official_exact",
            "优先检索本年度自然资源主管部门公开的正式通知、实施方案、技术规程及附件原文。",
        ),
        2: (
            "latest_effective_official",
            "如果本年度正式文件尚未公开，改查自然资源主管部门最新可公开取得的相邻年度正式文件和现行国家或行业标准，核实仍适用的技术依据。",
        ),
        3: (
            "workflow_components_official",
            "拆分检索内业核查、外业质量控制、成果复核、问题反馈和再次提交复核等流程节点；优先政府官网、标准发布机构和正式附件原文，不使用聚合转载替代原始来源。",
        ),
    }
    strategy_id, instruction = strategies.get(
        attempt,
        (
            f"official_variant_{attempt}",
            "更换关键词组合，按具体流程节点检索政府官网或标准发布机构的公开原文。",
        ),
    )
    return (
        need.model_copy(update={"question": f"{need.question}\n检索策略：{instruction}"}),
        strategy_id,
    )


def writer_research_enabled() -> bool:
    """Whether ChapterWritingService may auto-search public sources."""

    flag = str(os.environ.get("BID_AGENT_WRITER_RESEARCH_ENABLED", "1")).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    provider = str(os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "tavily")).strip().lower()
    return provider == "tavily"


class WriterResearchCoordinator:
    """Run the one research-planning path for a content unit.

    This coordinator owns persistence and execution only. Research need and
    query wording come from ``plan_chapter_research``.
    """

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        operation_id: str = "",
        deterministic_test: bool = False,
        decision_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.context = context
        self.operation_id = str(operation_id or "standalone")
        self.deterministic_test = bool(deterministic_test)
        self.decision_provider = decision_provider

    def resolve_for_bundle(self, bundle: WriterInputBundle) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        decision = self.plan_for_bundle(bundle)
        return self.execute_plan(bundle, decision)

    def plan_for_bundle(self, bundle: WriterInputBundle) -> ResearchDecision:
        """Decide first so streaming callers can disclose yes/no before searching."""
        decision = self._decision(bundle)
        payload = decision.model_dump(mode="json")
        self._upsert(payload)
        return decision

    def execute_plan(
        self,
        bundle: WriterInputBundle,
        decision: ResearchDecision,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Execute one already-disclosed research decision."""
        payload = decision.model_dump(mode="json")
        if not decision.needs_research:
            return payload, []

        adapter = create_research_adapter()
        runtime = getattr(adapter, "runtime_status", lambda: {"ready": True})()
        decision.runtime = dict(runtime or {})
        if str(decision.runtime.get("reason") or "") == "WEB_AUTOMATION_DISABLED":
            decision.decision_status = "skipped"
            for query in decision.queries:
                query.status = "skipped"
                query.error = "网页账号自动操作已禁用；请配置合规的搜索 API 后再执行联网检索。"
            payload = decision.model_dump(mode="json")
            self._upsert(payload)
            return payload, []
        if not decision.runtime.get("ready", True):
            decision.decision_status = "blocked_human"
            runtime_reason = str(
                decision.runtime.get("reason") or "TAVILY_RUNTIME_NOT_READY"
            )
            for query in decision.queries:
                query.status = "blocked_human"
                query.error = runtime_reason
            payload = decision.model_dump(mode="json")
            self._upsert(payload)
            raise ControlPlaneError(
                "WRITER_RESEARCH_ACTION_REQUIRED",
                "当前网页检索 Provider 的写作检索环境未就绪，请按当前单元调用记录处理后重试。",
                details={"research": payload},
            )

        decision.decision_status = "researching"
        self._upsert(decision.model_dump(mode="json"))
        snapshots: list[dict[str, Any]] = []
        from .global_project_context import GlobalProjectContextService

        project_anchors, task_anchors = GlobalProjectContextService.research_anchors(
            bundle.global_project_context or bundle.project_context or {}
        )
        relevance_context = self._relevance_context(bundle)
        for query in decision.queries:
            query.status = "researching"
            self._upsert(decision.model_dump(mode="json"))
            need = EvidenceNeed(
                need_id="EN-WR-" + hashlib.sha256(
                    (
                        f"{bundle.unit_id}:{RESEARCH_DECISION_POLICY_VERSION}:"
                        f"{query.question}:{'|'.join(sorted(query.target_node_ids))}"
                    ).encode("utf-8")
                ).hexdigest()[:16],
                question=query.question,
                topic_id=f"writer-unit:{bundle.unit_id}:{query.query_id}",
                priority="high",
                blocking_scope="content_unit",
                deadline_stage="chapter_writing",
                query_budget=3,
                project_anchors=project_anchors,
                task_anchors=task_anchors,
                relevance_context=relevance_context,
                max_adopted_items=3,
            )
            reviewer = self._deterministic_review if self.deterministic_test else None
            service = ResearchService(
                self.context,
                adapter,
                semantic_reviewer=reviewer,
            )
            batch = None
            valid_sources: list[dict[str, Any]] = []
            success = False
            max_attempts = _research_max_attempts()
            for attempt in range(1, max_attempts + 1):
                started = time.perf_counter()
                attempt_need, query_strategy = _need_for_attempt(need, attempt)
                batch = service.resolve(attempt_need, force_refresh=attempt > 1)
                valid_sources = self._valid_sources(batch)
                accepted_partial = self._accept_verified_partial_batch(batch, valid_sources)
                if accepted_partial:
                    batch = service.publish_verified_subset(attempt_need, batch)
                    valid_sources = self._valid_sources(batch)
                success = bool(
                    batch.items
                    and valid_sources
                    and batch.status == "published"
                )
                provider_errors = [
                    str(item or "").strip()
                    for item in batch.research_run.get("provider_errors") or []
                    if str(item or "").strip()
                ]
                attempt_error = (
                    ""
                    if success
                    else provider_errors[0]
                    if provider_errors
                    else batch.error or ("" if success else "回答未形成可核验公开来源")
                )
                query.attempts.append(
                    {
                        "attempt": attempt,
                        "query_strategy": query_strategy,
                        "submitted_question": attempt_need.question,
                        "status": (
                            "published_partial" if accepted_partial else "published"
                        )
                        if success
                        else batch.status,
                        "accepted_partial_evidence": accepted_partial,
                        "batch_id": batch.batch_id,
                        "evidence_count": len(batch.items),
                        "source_count": len(valid_sources),
                        "error": attempt_error,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "at": datetime.now(UTC).isoformat(),
                        "provider_id": str(getattr(service.provider, "provider_id", "") or ""),
                        "research_run": dict(batch.research_run),
                    }
                )
                self._upsert(decision.model_dump(mode="json"))
                if success or batch.error == "PROHIBITED_SCOPE":
                    break

            assert batch is not None
            query.batch_id = batch.batch_id
            query.evidence_count = len(batch.items)
            query.sources = valid_sources
            query.error = str(
                query.attempts[-1].get("error")
                or (
                    ""
                    if success
                    else f"连续检索 {len(query.attempts)} 次仍未形成可核验公开来源"
                )
            )
            query.status = "published" if success else "blocked_human"
            if not success:
                decision.decision_status = "blocked_human"
                payload = decision.model_dump(mode="json")
                self._upsert(payload)
                raise ControlPlaneError(
                    "WRITER_RESEARCH_ACTION_REQUIRED",
                    "当前网页检索 Provider 未取得可用于写作的可核验来源，请处理后重试当前内容单元。",
                    details={"research": payload},
                )
            snapshots.append(self._snapshot(batch, need, query))
            self._upsert(decision.model_dump(mode="json"))

        decision.decision_status = "published"
        payload = decision.model_dump(mode="json")
        self._upsert(payload)
        return payload, snapshots

    def _decision(self, bundle: WriterInputBundle) -> ResearchDecision:
        targets = [
            item
            for item in bundle.document_target_constraints
            if isinstance(item, dict) and str(item.get("content_policy") or "full") == "full"
        ]
        target_ids = [str(item.get("node_id") or item.get("output_target") or "") for item in targets]
        target_ids = [item for item in target_ids if item]
        first = targets[0] if targets else {}
        chapter_id = str(bundle.chapter_id or (target_ids[0] if target_ids else bundle.unit_id))
        chapter = {
            "chapter_id": chapter_id,
            "title": str(first.get("title") or ""),
            "blueprint_node": dict(first),
            "context": {"items": list(bundle.chapter_context_items or [])},
        }
        requirement_ids = {str(value) for value in first.get("primary_requirement_ids") or []}
        requirements = [
            item
            for item in bundle.requirement_excerpts
            if isinstance(item, dict)
            and (not requirement_ids or str(item.get("requirement_id") or "") in requirement_ids)
        ]
        orientation = dict(bundle.chapter_grounding_context or {})
        orientation["chapter_writing_plan"] = dict(bundle.chapter_writing_plan or {})
        orientation.setdefault(
            "writing_purpose",
            {
                "title": str(first.get("title") or ""),
                "purpose": str(first.get("purpose") or first.get("title") or "完成本章节写作目标"),
                "writing_objectives": list(first.get("writing_objectives") or []),
                "role": "goal_driven",
            },
        )
        provider = self._planner_decision_provider()
        planned = plan_chapter_research(
            chapter,
            project_context=(bundle.global_project_context or bundle.project_context or {}),
            writing_orientation=orientation,
            tender_requirements=requirements,
            scoring_requirements=list(bundle.score_obligations or []),
            instruction=str(bundle.user_instruction or ""),
            force_research=bool(
                _EXPLICIT_RESEARCH_RE.search(str(bundle.user_instruction or ""))
            ),
            decision_provider=provider,
        )
        search_query = str(planned.get("search_query") or "").strip()
        needs_research = bool(planned.get("need_research") and search_query and target_ids)
        fallback_to_existing_materials = bool(
            needs_research
            and planned.get("existing_materials_sufficient")
            and not planned.get("research_required_by_writing_plan")
            and not _EXPLICIT_RESEARCH_RE.search(str(bundle.user_instruction or ""))
        )
        queries: list[ResearchQuery] = []
        if needs_research:
            query_seed = f"{bundle.unit_id}:{RESEARCH_DECISION_POLICY_VERSION}:{search_query}:{','.join(target_ids)}"
            queries.append(
                ResearchQuery(
                    query_id="WRQ-" + hashlib.sha256(query_seed.encode("utf-8")).hexdigest()[:16],
                    question=search_query,
                    target_node_ids=target_ids,
                    applicability="、".join(str(item.get("title") or "") for item in targets),
                )
            )
        decision_seed = f"{bundle.unit_id}:{RESEARCH_DECISION_POLICY_VERSION}:{search_query}:{needs_research}"
        return ResearchDecision(
            decision_id="WRD-" + hashlib.sha256(decision_seed.encode("utf-8")).hexdigest()[:16],
            operation_id=self.operation_id,
            unit_id=bundle.unit_id,
            applicable_chapter_ids=target_ids,
            applicable_chapter_titles=[str(item.get("title") or "") for item in targets],
            needs_research=needs_research,
            fallback_to_existing_materials=fallback_to_existing_materials,
            reason=str(planned.get("reason") or "").strip(),
            queries=queries,
            prohibited_research_scopes=list(_PROHIBITED_SCOPES),
            decision_status="planned" if needs_research else "skipped",
            created_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _relevance_context(bundle: WriterInputBundle) -> dict[str, Any]:
        targets = [
            item
            for item in bundle.document_target_constraints
            if isinstance(item, dict) and str(item.get("content_policy") or "full") == "full"
        ]
        target = targets[0] if targets else {}
        grounding = dict(bundle.chapter_grounding_context or {})
        purpose = grounding.get("writing_purpose")
        purpose = purpose if isinstance(purpose, dict) else {}
        project = dict(bundle.global_project_context or bundle.project_context or {})

        def statements(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[str]:
            values: list[str] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                value = next(
                    (str(row.get(key) or "").strip() for key in keys if row.get(key)),
                    "",
                )
                if value and value not in values:
                    values.append(value[:1000])
            return values[:20]

        objectives = list(
            target.get("writing_objectives")
            or purpose.get("writing_objectives")
            or grounding.get("writing_objectives")
            or []
        )
        project_scope = (
            project.get("project_scope")
            or project.get("scope")
            or project.get("service_scope")
            or project.get("summary")
            or project.get("background")
            or ""
        )
        return {
            "chapter_title": str(target.get("title") or purpose.get("title") or "").strip(),
            "chapter_purpose": str(target.get("purpose") or purpose.get("purpose") or "").strip(),
            "writing_objectives": [
                str(item).strip()[:500]
                for item in objectives
                if str(item).strip()
            ][:20],
            "tender_requirements": statements(
                list(bundle.requirement_excerpts or []),
                (
                    "text",
                    "normalized_requirement",
                    "requirement_text",
                    "title",
                    "summary",
                ),
            ),
            "scoring_requirements": statements(
                list(bundle.score_obligations or []),
                (
                    "text",
                    "response_expectation",
                    "description",
                    "title",
                    "scoring_rule",
                ),
            ),
            "project_scope": project_scope,
        }

    @staticmethod
    def _deterministic_review(_need: EvidenceNeed, candidate: Any) -> dict[str, Any]:
        content = str(candidate.content or "").strip()
        return {
            "verdict": "relevant",
            "confidence": 0.9,
            "reason": "deterministic_test",
            "supporting_excerpts": [content[:800]],
            "extracted_points": [content[:500]],
            "usage_category": "industry_standard",
        }

    def _planner_decision_provider(
        self,
    ) -> Callable[[dict[str, Any]], dict[str, Any] | None] | None:
        """Adapt only the test seam; production always uses planner's Agent."""
        if self.decision_provider is not None:
            def provide(brief: dict[str, Any]) -> dict[str, Any] | None:
                value = self.decision_provider(brief)
                if not isinstance(value, dict):
                    return None
                if "need_research" in value:
                    return value
                queries = value.get("queries") or []
                query = queries[0] if queries and isinstance(queries[0], dict) else {}
                return {
                    "orientation_confirmed": True,
                    "orientation_summary": "基于已确认的章节目标完成研究决策。",
                    "existing_materials_sufficient": not bool(value.get("needs_research")),
                    "need_research": bool(value.get("needs_research")),
                    "reason": str(value.get("reason") or ""),
                    "search_query": str(query.get("question") or ""),
                }
            return provide
        if not self.deterministic_test:
            return None

        def deterministic(brief: dict[str, Any]) -> dict[str, Any]:
            text = str(brief.get("brief_text") or "")
            prohibited = ("资质", "资格", "业绩", "案例", "人员", "证书", "社保", "报价", "财务")
            enterprise_only = any(term in text for term in prohibited)
            need = not enterprise_only and not bool(
                (brief.get("existing_materials") or {}).get("has_local_materials")
            )
            query = " ".join(
                [
                    str(brief.get("project_name") or ""),
                    str(brief.get("chapter_title") or ""),
                    " ".join(brief.get("requirement_focus") or []),
                    " ".join(brief.get("writing_objectives") or []),
                    " ".join(brief.get("focus_keywords") or []),
                    "公开标准 同类方法",
                ]
            ).strip()
            return {
                "orientation_confirmed": True,
                "orientation_summary": "基于已确认的章节目标和现有资料完成写作。",
                "existing_materials_sufficient": not need,
                "need_research": need,
                "reason": "deterministic_test",
                "search_query": query if need else "",
            }

        return deterministic

    @staticmethod
    def _accept_verified_partial_batch(
        batch: Any,
        valid_sources: list[dict[str, Any]],
    ) -> bool:
        """Accept a verified authoritative subset without weakening Deep Research.

        Deep Research may split one chapter supplement into several ambitious
        claims.  A missing optional method detail must not discard authoritative
        original sources that already fill the WritingPlan's public-basis gap.
        The immutable source batch remains ``gap`` and keeps every missing
        claim for audit.  Accepted items are copied into a derived published
        batch before its id is exposed to downstream chapter editing.
        """

        if str(getattr(batch, "status", "") or "") != "gap":
            return False
        if str(getattr(batch, "error", "") or "") != "budget_exhausted":
            return False
        run = getattr(batch, "research_run", {})
        run = run if isinstance(run, dict) else {}
        if not list(run.get("satisfied_claim_ids") or []):
            return False
        authoritative = {"official", "standard", "academic"}
        return any(
            str(item.get("source_type") or "") in authoritative
            for item in valid_sources
            if isinstance(item, dict)
        )

    @staticmethod
    def _valid_sources(batch: Any) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for item in batch.items:
            parsed = urllib.parse.urlparse(str(item.source_url or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            sources.append(
                {
                    "evidence_id": item.evidence_id,
                    "title": item.title,
                    "publisher": item.publisher,
                    "source_url": item.source_url,
                    "source_type": item.source_type.value,
                    "retrieved_at": item.retrieved_at,
                    "relevance_tier": item.relevance_tier.value,
                    "matched_project_anchors": list(item.matched_project_anchors),
                    "matched_task_anchors": list(item.matched_task_anchors),
                    "usage_constraints": list(item.usage_constraints),
                }
            )
        return sources

    @staticmethod
    def _snapshot(batch: Any, need: EvidenceNeed, query: ResearchQuery) -> dict[str, Any]:
        contents = list(
            dict.fromkeys(
                str(item.content or "").strip()
                for item in batch.items
                if str(item.content or "").strip()
            )
        )
        return {
            "need_id": need.need_id,
            "topic_id": need.topic_id,
            "query_id": query.query_id,
            "target_ids": list(query.target_node_ids),
            "question": need.question,
            "batch_id": batch.batch_id,
            "evidence_ids": [item.evidence_id for item in batch.items],
            "content": "\n\n".join(contents)[:8000],
            "sources": list(query.sources),
            "research_run": dict(batch.research_run),
        }

    def _report(self) -> dict[str, Any]:
        path = self.context.root / WRITER_RESEARCH_REPORT_PATH
        value = read_json(path) if path.is_file() else {}
        return value if isinstance(value, dict) else {}

    def _write(self, report: dict[str, Any]) -> None:
        report["schema_version"] = "v3.writer_research.v2"
        report["policy_version"] = RESEARCH_DECISION_POLICY_VERSION
        report["updated_at"] = datetime.now(UTC).isoformat()
        write_json(self.context.root / WRITER_RESEARCH_REPORT_PATH, report)

    def _upsert(self, decision: dict[str, Any]) -> None:
        report = self._report()
        rows = report.setdefault("operations", {}).setdefault(self.operation_id, [])
        for index, item in enumerate(rows):
            if item.get("decision_id") == decision.get("decision_id"):
                rows[index] = decision
                break
        else:
            rows.append(decision)
        self._write(report)

    def mark_used(self, decision: dict[str, Any], chapter_id: str, evidence_ids: list[str]) -> None:
        if not decision.get("needs_research"):
            return
        used = decision.setdefault("used_evidence_by_chapter", {})
        used[str(chapter_id)] = sorted({str(item) for item in evidence_ids if str(item)})
        self._upsert(decision)
