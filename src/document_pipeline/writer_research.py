"""Content-unit research decisions made by the shared chapter planner."""

from __future__ import annotations

import hashlib
import os
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


def writer_research_enabled() -> bool:
    """Whether ChapterWritingService may auto-search public sources."""

    flag = str(os.environ.get("BID_AGENT_WRITER_RESEARCH_ENABLED", "1")).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    provider = str(os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "doubao_web")).strip().lower()
    return provider not in {"", "disabled", "manual"}


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
                max_adopted_items=3,
            )
            started = time.perf_counter()
            reviewer = self._deterministic_review if self.deterministic_test else None
            batch = ResearchService(
                self.context,
                adapter,
                semantic_reviewer=reviewer,
            ).resolve(need)
            valid_sources = self._valid_sources(batch)
            success = batch.status == "published" and bool(batch.items) and bool(valid_sources)
            query.attempts.append(
                {
                    "attempt": len(query.attempts) + 1,
                    "status": "published" if success else batch.status,
                    "batch_id": batch.batch_id,
                    "evidence_count": len(batch.items),
                    "source_count": len(valid_sources),
                    "error": batch.error or ("" if success else "回答未形成可核验公开来源"),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            query.batch_id = batch.batch_id
            query.evidence_count = len(batch.items)
            query.sources = valid_sources
            query.error = str(batch.error or ("" if success else "回答未形成可核验公开来源"))
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
            decision_provider=provider,
        )
        search_query = str(planned.get("search_query") or "").strip()
        needs_research = bool(planned.get("need_research") and search_query and target_ids)
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
            reason=str(planned.get("reason") or "").strip(),
            queries=queries,
            prohibited_research_scopes=list(_PROHIBITED_SCOPES),
            decision_status="planned" if needs_research else "skipped",
            created_at=datetime.now(UTC).isoformat(),
        )

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
