"""Content-unit research decisions made inside the writing stage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any, Callable

from control_plane import ControlPlaneError, WorkspaceContext
from utils import read_json, write_json

from .contracts import (
    EvidenceNeed,
    ResearchDecision,
    ResearchQuery,
    WriterInputBundle,
)
from .input_manifest import V3_ROOT
from .research_adapters import create_research_adapter
from .research_service import ResearchService
from .writer_policy import RESEARCH_DECISION_POLICY_VERSION


WRITER_RESEARCH_REPORT_PATH = V3_ROOT / "evidence" / "writer_research.json"


def writer_research_enabled() -> bool:
    """Whether execute_content_plan may auto-search public sources.

    Enterprise facts remain operator-supplied. This only unlocks public
    policy/method research when a non-disabled research provider is configured.
    """

    flag = str(
        os.environ.get("BID_AGENT_WRITER_RESEARCH_ENABLED", "1")
    ).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    provider = str(
        os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "doubao_web")
    ).strip().lower()
    return provider not in {"", "disabled", "manual"}
_MANDATORY_RESEARCH_CUES = re.compile(
    r"项目背景|任务背景|行业现状|发展现状|政策|法律|法规|标准|规范|"
    r"专业方法|技术方法|技术路线|工艺|风险控制",
)
_PUBLIC_RESEARCH_CUES = re.compile(
    r"背景|现状|趋势|标准|规范|指南|政策|法律|法规|技术|方法|实施|架构|"
    r"接口|集成|安全|运维|质量|测试|验收|培训|迁移|部署|应急|风险",
)
_ENTERPRISE_ONLY_CUES = re.compile(
    r"资质|资格|业绩|案例|人员|证书|社保|财务|报价|投标函|法定代表人|"
    r"授权委托|保证金|企业实力|公司简介",
)
_PROHIBITED_SCOPES = [
    "企业资质与资格",
    "企业业绩与案例",
    "人员身份、履历、证书与社保",
    "报价、财务、承诺与投标函事实",
]


class WriterResearchCoordinator:
    """Create one structured decision and at most three queries per content unit."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        operation_id: str = "",
        deterministic_test: bool = False,
        decision_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.context = context
        self.operation_id = str(operation_id or "standalone")
        self.deterministic_test = bool(deterministic_test)
        self.decision_provider = decision_provider

    def resolve_for_bundle(
        self,
        bundle: WriterInputBundle,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        decision = self._decision(bundle)
        payload = decision.model_dump(mode="json")
        self._upsert(payload)
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
                deadline_stage="execute_content_plan",
                query_budget=5,
            )
            started = time.perf_counter()
            batch = ResearchService(self.context, adapter).resolve(need)
            valid_sources = self._valid_sources(batch)
            success = batch.status == "published" and bool(batch.items) and bool(valid_sources)
            query.attempts.append(
                {
                    "attempt": len(query.attempts) + 1,
                    "status": "published" if success else batch.status,
                    "batch_id": batch.batch_id,
                    "evidence_count": len(batch.items),
                    "source_count": len(valid_sources),
                    "error": batch.error or (
                        "" if success else "回答未形成可核验公开来源"
                    ),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            query.batch_id = batch.batch_id
            query.evidence_count = len(batch.items)
            query.sources = valid_sources
            query.error = str(
                batch.error or ("" if success else "回答未形成可核验公开来源")
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
        target_rows = self._target_rows(bundle)
        raw = (
            self._deterministic_candidate(target_rows)
            if self.deterministic_test
            else self._model_candidate(bundle, target_rows)
        )
        return self._project_policy(bundle, target_rows, raw)

    def _target_rows(self, bundle: WriterInputBundle) -> list[dict[str, Any]]:
        has_local_evidence = any(
            isinstance(item, dict)
            and (
                item.get("evidence_ids")
                or item.get("content")
                or item.get("source_url")
            )
            for item in bundle.evidence_snapshot
        )
        requirements = {
            str(item.get("requirement_id") or ""): str(
                item.get("normalized_requirement")
                or item.get("statement")
                or ""
            )
            for item in bundle.requirement_excerpts
            if isinstance(item, dict)
        }
        rows: list[dict[str, Any]] = []
        for target in bundle.document_target_constraints:
            if str(target.get("content_policy") or "full") != "full":
                continue
            requirement_texts = [
                requirements.get(str(requirement_id), "")
                for requirement_id in target.get("primary_requirement_ids") or []
            ]
            text = " ".join(
                [
                    str(target.get("title") or ""),
                    *[item for item in requirement_texts if item],
                ]
            )
            mandatory = bool(_MANDATORY_RESEARCH_CUES.search(text))
            public = bool(_PUBLIC_RESEARCH_CUES.search(text))
            enterprise_only = (
                bool(_ENTERPRISE_ONLY_CUES.search(text))
            )
            rows.append(
                {
                    "node_id": str(target.get("node_id") or ""),
                    "title": str(target.get("title") or ""),
                    "requirements": requirement_texts,
                    "mandatory": mandatory,
                    "public": public,
                    "enterprise_only": enterprise_only,
                    "missing_public_support": (
                        not has_local_evidence and not enterprise_only
                    ),
                }
            )
        return rows

    @staticmethod
    def _deterministic_candidate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        targets = [
            item
            for item in rows
            if (
                item["mandatory"]
                or item["public"]
                or item["missing_public_support"]
            )
            and not item["enterprise_only"]
        ]
        if not targets:
            return {
                "needs_research": False,
                "reason": "当前单元仅涉及招标原文或企业事实，不需要公开检索。",
                "queries": [],
            }
        titles = "、".join(item["title"] for item in targets[:6])
        return {
            "needs_research": True,
            "reason": "当前单元缺少可用资料或包含公开依据、专业方法，需要联网检索公开来源支撑。",
            "queries": [
                {
                    "question": (
                        f"围绕{titles}，检索现行官方政策、标准规范、专业实施方法、"
                        "质量控制和验收实践，并逐项提供公开来源 URL。"
                    ),
                    "target_node_ids": [item["node_id"] for item in targets],
                    "applicability": titles,
                }
            ],
        }

    def _model_candidate(
        self,
        bundle: WriterInputBundle,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request = {
            "unit_id": bundle.unit_id,
            "project_context": bundle.project_context,
            "chapters": rows,
            "rules": {
                "prohibited": _PROHIBITED_SCOPES,
                "decision_authority": (
                    "由你结合当前章节、招标要求、已有资料独立判断是否需要联网；"
                    "不要因为系统预设主题或缺少资料就机械检索。"
                ),
                "query_count": "如确有必要，按你识别出的事实缺口输出 1 到 3 个完整研究任务，而非搜索关键词",
                "query_quality": (
                    "question 必须是可直接交给联网研究 Agent 的完整中文任务，不得只堆砌关键词；"
                    "必须写明用于哪个章节、待核验的具体问题、需要形成的写作结论和优先来源。"
                ),
            },
            "output_schema": {
                "needs_research": "boolean",
                "reason": "string",
                "queries": [
                    {
                        "question": "string",
                        "target_node_ids": ["chapter id"],
                        "applicability": "string",
                    }
                ],
            },
        }
        try:
            if self.decision_provider is not None:
                value = self.decision_provider(request)
            else:
                from llm_client import chat

                text = chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是标书写作 Agent 的检索决策器。只判断当前内容单元是否需要"
                                "公开检索，并独立分析需要核验的具体事实缺口。企业资质、业绩、"
                                "人员、报价和承诺禁止联网补造。不得把任何章节或主题当作默认必搜项；"
                                "已有招标资料足够支撑的内容不要检索。"
                                "检索问题必须是完整、可读的中文研究任务，说明检索目的、待核验事项、"
                                "优先来源和所需写作结论；严禁输出关键词堆砌。"
                                "只输出严格 JSON，不要解释或 Markdown。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(request, ensure_ascii=False),
                        },
                    ],
                    temperature=0.1,
                )
                value = self._decode_json(text)
        except ControlPlaneError:
            raise
        except Exception as exc:
            raise ControlPlaneError(
                "WRITER_MODEL_ACTION_REQUIRED",
                "写作 Agent 无法完成结构化检索决策，生成已暂停。",
                details={
                    "unit_id": bundle.unit_id,
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                },
            ) from exc
        if not isinstance(value, dict):
            raise ControlPlaneError(
                "WRITER_MODEL_ACTION_REQUIRED",
                "写作 Agent 的检索决策不是有效 JSON 对象，生成已暂停。",
                details={"unit_id": bundle.unit_id},
            )
        return value

    @staticmethod
    def _decode_json(text: str) -> dict[str, Any]:
        value = str(text or "").strip()
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            start = value.find("{")
            end = value.rfind("}")
            if start < 0 or end <= start:
                raise
            parsed = json.loads(value[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("ResearchDecision 输出必须是 JSON 对象")
        return parsed

    def _project_policy(
        self,
        bundle: WriterInputBundle,
        rows: list[dict[str, Any]],
        raw: dict[str, Any],
    ) -> ResearchDecision:
        allowed = {
            item["node_id"]: item
            for item in rows
            if item["node_id"] and not item["enterprise_only"]
        }
        mandatory_ids = {
            item["node_id"]
            for item in rows
            if item["mandatory"] and not item["enterprise_only"]
        }
        public_ids = {
            item["node_id"]
            for item in rows
            if item["public"] and not item["enterprise_only"]
        }
        missing_support_ids = {
            item["node_id"]
            for item in rows
            if item["missing_public_support"] and not item["enterprise_only"]
        }
        # The model owns the decision to search.  Policy only prevents it from
        # searching enterprise-only chapters; it must not turn a lack of local
        # material into an automatic web query.
        required_research_ids: set[str] = set()
        candidates: list[dict[str, Any]] = []
        seen_questions: set[str] = set()
        for item in raw.get("queries") or []:
            if not isinstance(item, dict):
                continue
            question = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
            key = re.sub(r"\W+", "", question).lower()
            target_ids = sorted(
                {
                    str(target_id)
                    for target_id in (item.get("target_node_ids") or [])
                    if str(target_id) in allowed
                }
            )
            if not question or not target_ids or key in seen_questions:
                continue
            seen_questions.add(key)
            candidates.append(
                {
                    "question": self._readable_research_question(
                        question,
                        str(item.get("applicability") or "、".join(
                            allowed[target_id]["title"] for target_id in target_ids
                        )),
                        project_context=str(bundle.project_context or ""),
                        requirements=[
                            requirement
                            for target_id in target_ids
                            for requirement in allowed[target_id].get("requirements", [])
                            if requirement
                        ],
                    ),
                    "target_node_ids": target_ids,
                    "applicability": str(
                        item.get("applicability")
                        or "、".join(allowed[target_id]["title"] for target_id in target_ids)
                    ),
                }
            )

        covered = {
            target_id
            for item in candidates
            for target_id in item["target_node_ids"]
        }
        uncovered_required = sorted(required_research_ids - covered)
        if uncovered_required:
            titles = "、".join(allowed[item]["title"] for item in uncovered_required)
            candidates.insert(
                0,
                {
                    "question": (
                        f"围绕{titles}，检索可直接支撑本章写作的现行官方政策、标准规范、"
                        "专业方法或实施实践，并逐项提供公开来源 URL。"
                    ),
                    "target_node_ids": uncovered_required,
                    "applicability": titles,
                },
            )
        wants_research = bool(raw.get("needs_research"))
        if wants_research and not candidates and public_ids:
            titles = "、".join(allowed[item]["title"] for item in sorted(public_ids))
            candidates.append(
                {
                    "question": (
                        f"检索与{titles}相关的现行标准、专业方法和风险控制实践，"
                        "逐项提供公开来源 URL。"
                    ),
                    "target_node_ids": sorted(public_ids),
                    "applicability": titles,
                }
            )
        candidates = candidates[:3]
        needs_research = bool(candidates)
        decision_seed = (
            f"{bundle.unit_id}:{RESEARCH_DECISION_POLICY_VERSION}:"
            + "|".join(
                f"{item['question']}:{','.join(item['target_node_ids'])}"
                for item in candidates
            )
        )
        queries = [
            ResearchQuery(
                query_id="WRQ-" + hashlib.sha256(
                    (
                        f"{bundle.unit_id}:{RESEARCH_DECISION_POLICY_VERSION}:"
                        f"{item['question']}:{','.join(item['target_node_ids'])}"
                    ).encode("utf-8")
                ).hexdigest()[:16],
                question=item["question"],
                target_node_ids=item["target_node_ids"],
                applicability=item["applicability"],
            )
            for item in candidates
        ]
        return ResearchDecision(
            decision_id="WRD-" + hashlib.sha256(
                decision_seed.encode("utf-8")
            ).hexdigest()[:16],
            operation_id=self.operation_id,
            unit_id=bundle.unit_id,
            applicable_chapter_ids=[item["node_id"] for item in rows if item["node_id"]],
            applicable_chapter_titles=[item["title"] for item in rows if item["node_id"]],
            needs_research=needs_research,
            reason=str(
                raw.get("reason")
                or (
                    "当前单元缺少可用资料，已检索公开来源支撑章节写作。"
                    if missing_support_ids
                    else "当前单元需要公开背景、现行依据或专业方法支撑。"
                    if needs_research
                    else "当前单元仅使用招标资料或企业事实，无需公开检索。"
                )
            ),
            queries=queries,
            prohibited_research_scopes=list(_PROHIBITED_SCOPES),
            decision_status="planned" if needs_research else "skipped",
            created_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _readable_research_question(
        question: str,
        applicability: str,
        *,
        project_context: str = "",
        requirements: list[str] | None = None,
    ) -> str:
        """Turn an LLM intent into a full web-research brief for bid writing."""
        intent = re.sub(r"\s+", " ", str(question or "")).strip("。；; ")
        chapter = re.sub(r"\s+", " ", str(applicability or "当前技术章节")).strip()
        context = re.sub(r"\s+", " ", project_context).strip()[:800]
        requirement_text = "；".join(
            re.sub(r"\s+", " ", str(item)).strip()
            for item in (requirements or [])
            if str(item).strip()
        )[:1_000]
        return (
            f"你正在为“{context or '本项目'}”编制技术标书。当前需完成“{chapter}”章节。\n"
            f"本次研究目标：{intent}。\n"
            f"招标要求与写作边界：{requirement_text or '围绕本章形成可执行、可验收的技术方案。'}\n\n"
            "请先自行分析当前章节哪些事实或技术要点确实需要外部核验，再据此制定检索方向；"
            "不要套用固定的政策、标准或质量控制关键词。仅在确有必要时检索并交叉阅读相关公开资料，"
            "优先选择与你识别出的缺口最相关的权威来源。\n"
            "最后只输出以下可审计结果：\n"
            "1. 研究结论：逐条说明已核验的事实或技术要求；\n"
            "2. 可直接写入标书：按本章节组织为可执行的方法、步骤、质量控制和交付成果；\n"
            "3. 来源依据：每条结论对应文件名称、关键内容摘要和 URL。\n"
            "不要只给搜索词、URL 或文件名；不要编造企业资质、人员、业绩、报价或未核验事实。"
        )[:4_000]

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
        rows = report.setdefault("operations", {}).setdefault(
            self.operation_id,
            [],
        )
        for index, item in enumerate(rows):
            if item.get("decision_id") == decision.get("decision_id"):
                rows[index] = decision
                break
        else:
            rows.append(decision)
        self._write(report)

    def mark_used(
        self,
        decision: dict[str, Any],
        chapter_id: str,
        evidence_ids: list[str],
    ) -> None:
        if not decision.get("needs_research"):
            return
        used = decision.setdefault("used_evidence_by_chapter", {})
        used[str(chapter_id)] = sorted(
            {str(item) for item in evidence_ids if str(item)}
        )
        self._upsert(decision)
