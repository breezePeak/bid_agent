"""Autonomous, source-scoped research before V3 content writing.

The coordinator belongs to the evidence service layer, not the Writer.  It
derives a small number of public-research questions from the promoted bid
plan, invokes the configured provider without attachments, and publishes the
results through the existing immutable EvidenceBatch path.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import write_json

from .chapter_blueprint import load_promoted_chapter_blueprint
from .contracts import EvidenceBatch, EvidenceNeed
from .input_manifest import V3_ROOT
from .pipeline_policy import validation_failure_blocks
from .project_model import load_promoted_project_model
from .requirement_ledger import load_promoted_requirement_ledger
from .research_adapters import ResearchProviderAdapter, create_research_adapter
from .research_service import ResearchService, SemanticReviewer
from .score_model import load_promoted_score_model


AUTO_RESEARCH_REPORT_PATH = V3_ROOT / "evidence" / "auto_research.json"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_TECHNICAL_CUE = re.compile(
    r"方案|设计|架构|实施|技术|系统|平台|数据|接口|集成|安全|运维|服务|"
    r"质量|测试|验收|培训|迁移|部署|应急|保障|项目管理|风险|进度"
)
_ENTERPRISE_ONLY_CUE = re.compile(
    r"资质|资格|业绩|案例|人员证书|社保|财务|报价|投标函|法定代表人|"
    r"授权委托|保证金|企业实力|公司简介"
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _clean_excerpt(value: str, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


@dataclass(frozen=True)
class PlannedResearchNeed:
    need: EvidenceNeed
    chapter_id: str
    chapter_title: str
    score: int
    decision: dict[str, Any] | None = None
    round_index: int = 1


class AutonomousResearchCoordinator:
    """Plan and resolve a bounded set of public research needs."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        provider: ResearchProviderAdapter | None = None,
        semantic_reviewer: SemanticReviewer | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.provider = provider
        self.semantic_reviewer = semantic_reviewer
        self.enabled = (
            _env_flag("BID_AGENT_AUTO_RESEARCH_ENABLED", True)
            if enabled is None
            else bool(enabled)
        )

    def plan(self) -> list[PlannedResearchNeed]:
        """Create chapter-scoped questions from promoted artifacts."""

        if not self.enabled:
            return []
        blueprint = load_promoted_chapter_blueprint(self.context)
        ledger = load_promoted_requirement_ledger(self.context)
        scores = load_promoted_score_model(self.context)
        try:
            project = load_promoted_project_model(self.context)
        except Exception:
            project = None
        requirements = {
            item.requirement_id: item for item in ledger.requirements
        }
        score_points = {
            item.score_point_id: item for item in scores.points
        }
        self._last_decisions: list[dict[str, Any]] = []
        candidates: list[PlannedResearchNeed] = []
        for node in blueprint.nodes:
            if str(getattr(node, "content_policy", "full")) != "full":
                self._last_decisions.append(
                    self._decision(
                        node=node,
                        requirement_rows=[],
                        score_rows=[],
                        project=project,
                        needs=False,
                        reasons=["section_deferred"],
                        missing=[],
                        questions=[],
                    )
                )
                continue
            requirement_rows = [
                requirements[item]
                for item in node.requirement_ids
                if item in requirements
            ]
            score_rows = [
                score_points[item]
                for item in node.score_point_ids
                if item in score_points
            ]
            if not requirement_rows and not score_rows:
                decision = self._decision(
                    node=node,
                    requirement_rows=[],
                    score_rows=[],
                    project=project,
                    needs=False,
                    reasons=["no_bound_requirements_or_scores"],
                    missing=[],
                    questions=[],
                )
                self._last_decisions.append(decision)
                continue
            context_text = " ".join(
                [
                    node.title,
                    node.purpose,
                    *[
                        item.normalized_requirement
                        for item in requirement_rows
                    ],
                    *[item.title for item in score_rows],
                    *[item.response_expectation for item in score_rows],
                ]
            )
            if _ENTERPRISE_ONLY_CUE.search(context_text) and not _TECHNICAL_CUE.search(
                context_text
            ):
                self._last_decisions.append(
                    self._decision(
                        node=node,
                        requirement_rows=requirement_rows,
                        score_rows=score_rows,
                        project=project,
                        needs=False,
                        reasons=["enterprise_only_or_commercial_fact"],
                        missing=[],
                        questions=[],
                    )
                )
                continue
            needs, reasons, missing = self._needs_research(
                node=node,
                context_text=context_text,
                project=project,
            )
            questions = (
                self._chapter_questions(
                    node=node,
                    requirement_rows=requirement_rows,
                    score_rows=score_rows,
                    project=project,
                    missing=missing,
                )
                if needs
                else []
            )
            decision = self._decision(
                node=node,
                requirement_rows=requirement_rows,
                score_rows=score_rows,
                project=project,
                needs=needs,
                reasons=reasons,
                missing=missing,
                questions=questions,
            )
            self._last_decisions.append(decision)
            if not needs:
                continue
            score = (
                5 * len(score_rows)
                + 2 * len(requirement_rows)
                + (4 if _TECHNICAL_CUE.search(context_text) else 0)
                + min(int(node.target_size or 0) // 500, 3)
            )
            for question_index, question in enumerate(questions[:3], start=1):
                seed = (
                    f"{blueprint.revision}:{node.chapter_id}:{question_index}:{question}"
                )
                need_id = (
                    "EN-AUTO-"
                    + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
                )
                candidates.append(
                    PlannedResearchNeed(
                        need=EvidenceNeed(
                            need_id=need_id,
                            question=question,
                            topic_id=f"chapter:{node.chapter_id}",
                            priority="normal",
                            blocking_scope="none",
                            deadline_stage="chapter_writing",
                            query_budget=_env_int(
                                "BID_AGENT_AUTO_RESEARCH_SOURCES_PER_QUERY",
                                5,
                                minimum=1,
                                maximum=10,
                            ),
                        ),
                        chapter_id=node.chapter_id,
                        chapter_title=node.title,
                        score=score,
                        decision=decision,
                    )
                )
        maximum = _env_int(
            "BID_AGENT_AUTO_RESEARCH_MAX_QUERIES",
            24,
            minimum=0,
            maximum=60,
        )
        return sorted(
            candidates,
            key=lambda item: (-item.score, item.chapter_id),
        )[:maximum]

    def _needs_research(
        self,
        *,
        node: Any,
        context_text: str,
        project: Any | None,
    ) -> tuple[bool, list[str], list[str]]:
        reasons: list[str] = []
        missing: list[str] = []
        title = str(getattr(node, "title", "") or "")
        public_identity = bool(
            project
            and (
                getattr(project, "identity", {})
                or getattr(project, "background", [])
                or getattr(project, "scope", [])
            )
        )
        if re.search(r"项目背景|项目理解|需求理解|背景分析", title):
            reasons.append("project_background_or_understanding")
            if public_identity:
                missing.append("公开项目背景、采购人公开信息或地区建设背景")
        if re.search(r"法律|法规|标准|规范|指南|行业|现行|政策", context_text):
            reasons.append("current_standard_or_policy")
            missing.append("现行标准规范、行业指南或政策依据")
        if re.search(r"未知|待核实|调研|背景|现状|趋势", context_text):
            reasons.append("unknown_public_fact")
            missing.append("待核实的公开项目事实或行业现状")
        if _TECHNICAL_CUE.search(context_text) and not reasons:
            reasons.append("technical_chapter_with_public_context")
            missing.append("与项目范围相关的公开方法、风险和最佳实践")
        return bool(reasons), reasons or ["no_external_fact_gap"], missing

    def _decision(
        self,
        *,
        node: Any,
        requirement_rows: list[Any],
        score_rows: list[Any],
        project: Any | None,
        needs: bool,
        reasons: list[str],
        missing: list[str],
        questions: list[str],
    ) -> dict[str, Any]:
        known = []
        if project is not None:
            identity = getattr(project, "identity", {}) or {}
            known.extend(
                f"{key}:{value}"
                for key, value in list(identity.items())[:6]
                if str(value).strip()
            )
            known.extend(
                _clean_excerpt(item, 180)
                for item in list(getattr(project, "scope", []) or [])[:3]
            )
        known.extend(
            _clean_excerpt(item.normalized_requirement, 180)
            for item in requirement_rows[:3]
        )
        return {
            "chapter_id": str(getattr(node, "chapter_id", "")),
            "chapter_title": str(getattr(node, "title", "")),
            "needs_research": bool(needs),
            "reasons": list(reasons),
            "known_facts": [item for item in known if item],
            "missing_facts": list(missing),
            "questions": list(questions),
            "prohibited_queries": [
                "投标企业资质、业绩、人员、财务和报价不得联网补造",
                "不得上传完整招标文件或企业材料",
                "不得用公开网页证明本企业能力",
            ],
            "decision_trace": [
                f"requirement_count={len(requirement_rows)}",
                f"score_point_count={len(score_rows)}",
                f"policy={getattr(node, 'content_policy', 'full')}",
            ],
        }

    def _chapter_questions(
        self,
        *,
        node: Any,
        requirement_rows: list[Any],
        score_rows: list[Any],
        project: Any | None,
        missing: list[str],
    ) -> list[str]:
        context = self._sanitized_project_context(project)
        base = [
            f"拟编写章节：{_clean_excerpt(getattr(node, 'title', ''), 180)}。",
            f"章节目的：{_clean_excerpt(getattr(node, 'purpose', ''), 260)}。",
        ]
        if context:
            base.append(context)
        reqs = [
            _clean_excerpt(item.normalized_requirement, 220)
            for item in requirement_rows[:3]
            if str(item.normalized_requirement or "").strip()
        ]
        if reqs:
            base.append("招标摘录：" + "；".join(reqs) + "。")
        expectations = [
            _clean_excerpt(
                f"{item.title}：{item.response_expectation}",
                220,
            )
            for item in score_rows[:2]
        ]
        if expectations:
            base.append("内部响应目标：" + "；".join(expectations) + "。")
        suffix = (
            "只返回可用于技术方案写作的公开项目背景、采购人公开信息、"
            "地区/行业现状、现行标准规范、官方指南或可核验最佳实践，"
            "并逐项附公开来源 URL；不得推断企业资质、业绩、人员、财务、报价或承诺。"
        )
        topics = missing[:3] or ["公开项目背景和可核验实施依据"]
        return ["\n".join([*base, f"待核实问题：{topic}。", suffix]) for topic in topics]

    @staticmethod
    def _sanitized_project_context(project: Any | None) -> str:
        if project is None:
            return ""
        identity = getattr(project, "identity", {}) or {}
        pieces = [
            f"{key}:{value}"
            for key, value in identity.items()
            if str(value).strip()
            and re.search(r"项目|采购|招标|地区|区域|建设|单位|采购人|招标人|名称|project|region|buyer", str(key), re.I)
        ][:8]
        scope = [
            _clean_excerpt(item, 160)
            for item in list(getattr(project, "scope", []) or [])[:3]
        ]
        background = [
            _clean_excerpt(item, 160)
            for item in list(getattr(project, "background", []) or [])[:2]
        ]
        parts = []
        if pieces:
            parts.append("项目公开检索线索：" + "；".join(pieces) + "。")
        if scope:
            parts.append("建设范围摘要：" + "；".join(scope) + "。")
        if background:
            parts.append("背景线索摘要：" + "；".join(background) + "。")
        return "\n".join(parts)

    @staticmethod
    def _question(
        *,
        chapter_title: str,
        purpose: str,
        requirement_rows: list[Any],
        score_rows: list[Any],
    ) -> str:
        requirements = [
            _clean_excerpt(item.normalized_requirement)
            for item in requirement_rows[:4]
            if str(item.normalized_requirement or "").strip()
        ]
        score_expectations = [
            _clean_excerpt(
                f"{item.title}：{item.response_expectation}",
                360,
            )
            for item in score_rows[:3]
        ]
        sections = [
            "请基于以下当前投标任务上下文开展联网研究，"
            "给出可用于编写针对性实施方案的公开标准、官方指南、"
            "通行技术方法和可核验最佳实践，并逐项附公开来源 URL。",
            f"拟编写章节：{_clean_excerpt(chapter_title, 180)}。",
            f"章节目的：{_clean_excerpt(purpose, 300)}。",
        ]
        if requirements:
            sections.append("招标要求：" + "；".join(requirements) + "。")
        if score_expectations:
            sections.append(
                "评分响应目标：" + "；".join(score_expectations) + "。"
            )
        sections.append(
            "只研究项目背景、方案方法、标准和风险控制；"
            "不得推断或编造投标企业的资质、业绩、人员、产品实绩、"
            "报价、工期承诺或其他企业事实。"
        )
        return "\n".join(sections)

    def resolve(self) -> dict[str, Any]:
        """Resolve planned needs with bounded retries and a command policy gate."""

        planned = self.plan()
        decisions = list(getattr(self, "_last_decisions", []))
        blocking_policy = (
            "stop_on_validation_failure"
            if validation_failure_blocks()
            else "continue_with_warnings"
        )
        max_retries = _env_int(
            "BID_AGENT_AUTO_RESEARCH_MAX_RETRIES",
            3,
            minimum=0,
            maximum=5,
        )
        if not self.enabled:
            return self._write_report(
                {
                    "enabled": False,
                    "provider_id": "disabled",
                    "planned_count": 0,
                    "published_count": 0,
                    "gap_count": 0,
                    "failed_count": 0,
                    "max_retries": max_retries,
                    "max_rounds": 2,
                    "blocking_policy": blocking_policy,
                    "decisions": decisions,
                    "warnings": [],
                    "results": [],
                }
            )
        if not planned:
            return self._write_report(
                {
                    "enabled": True,
                    "provider_id": str(
                        os.environ.get(
                            "BID_AGENT_RESEARCH_PROVIDER",
                            "tavily",
                        )
                    ),
                    "planned_count": 0,
                    "published_count": 0,
                    "gap_count": 0,
                    "failed_count": 0,
                    "max_retries": max_retries,
                    "max_rounds": 2,
                    "blocking_policy": blocking_policy,
                    "decisions": decisions,
                    "warnings": [],
                    "results": [],
                }
            )
        provider = self.provider or create_research_adapter()
        results: list[dict[str, Any]] = []
        for item in planned:
            # Register the need before calling the provider so the UI can show
            # the currently researched chapter.  No attachment IDs are ever
            # supplied by this autonomous path.
            self.store.upsert_evidence_need(
                item.need.model_dump(mode="json")
            )
            attempts: list[dict[str, Any]] = []
            batch: EvidenceBatch | None = None
            for attempt in range(1, max_retries + 2):
                started = time.perf_counter()
                batch = ResearchService(
                    self.context,
                    provider,
                    semantic_reviewer=self.semantic_reviewer,
                ).resolve(item.need)
                duration_ms = int((time.perf_counter() - started) * 1000)
                attempts.append(
                    {
                        "attempt": attempt,
                        "round": item.round_index,
                        "query": item.need.question,
                        "status": batch.status,
                        "batch_id": batch.batch_id,
                        "item_count": len(batch.items),
                        "evidence_count": len(batch.items),
                        "error": batch.error,
                        "duration_ms": duration_ms,
                    }
                )
                if batch.status == "published":
                    break
            if batch is None:  # pragma: no cover - loop always runs at least once
                raise RuntimeError("AUTO_RESEARCH_INTERNAL_NO_ATTEMPT")
            results.append(
                {
                    "need_id": item.need.need_id,
                    "chapter_id": item.chapter_id,
                    "chapter_title": item.chapter_title,
                    "status": batch.status,
                    "batch_id": batch.batch_id,
                    "item_count": len(batch.items),
                    "evidence_count": len(batch.items),
                    "error": batch.error,
                    "attempt_count": len(attempts),
                    "exhausted": batch.status != "published",
                    "attempts": attempts,
                    "query": item.need.question,
                    "round": item.round_index,
                }
            )
        report = {
            "enabled": True,
            "provider_id": provider.provider_id,
            "planned_count": len(planned),
            "published_count": sum(
                item["status"] == "published" for item in results
            ),
            "gap_count": sum(item["status"] == "gap" for item in results),
            "failed_count": sum(
                item["status"] == "failed" for item in results
            ),
            "max_retries": max_retries,
            "max_rounds": 2,
            "blocking_policy": blocking_policy,
            "decisions": decisions,
            "results": results,
        }
        warnings: list[dict[str, Any]] = []
        if report["published_count"] == 0:
            warnings.append(
                {
                    "code": "AUTO_RESEARCH_EXHAUSTED",
                    "message": (
                        "Tavily 公开资料检索重试耗尽，"
                        "没有取得可核验来源。"
                    ),
                    "details": {
                        "planned_count": len(planned),
                        "max_retries": max_retries,
                    },
                    "policy_override": (
                        "blocked"
                        if validation_failure_blocks()
                        else "continue_with_warnings"
                    ),
                }
            )
        report["warnings"] = warnings
        written = self._write_report(report)
        if warnings and validation_failure_blocks():
            raise ControlPlaneError(
                "AUTO_RESEARCH_EXHAUSTED",
                warnings[0]["message"],
                status_code=409,
                details={"auto_research": written},
            )
        return written

    def _write_report(self, report: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "schema_version": "v3.auto_research.v2",
            "updated_at": datetime.now(UTC).isoformat(),
            **report,
        }
        write_json(
            self.context.root / AUTO_RESEARCH_REPORT_PATH,
            payload,
        )
        return payload


def published_batch_payload(
    context: WorkspaceContext,
    batch_id: str,
) -> EvidenceBatch | None:
    """Load a published batch by its validated, path-safe identifier."""

    normalized = str(batch_id or "").strip()
    if not re.fullmatch(r"EB-[0-9a-f]{16}(?:-R[1-9][0-9]*)?", normalized):
        return None
    path = (
        context.root
        / V3_ROOT
        / "evidence"
        / "batches"
        / f"{normalized}.json"
    )
    if not path.is_file():
        return None
    from utils import read_json

    try:
        batch = EvidenceBatch.model_validate(read_json(path))
    except Exception:
        return None
    return batch if batch.status == "published" else None
