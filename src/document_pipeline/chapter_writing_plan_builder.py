"""Build the PR-03 shadow writing plan without authorizing Writer consumption."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from .chapter_writing_outline import compile_chapter_writing_plan
from .contracts import (
    ChapterPlanContentUnit,
    ChapterPlanMaterialSource,
    ChapterPlanResearchDecision,
    ChapterPlanSourceBinding,
    ChapterPlanSourceType,
    ChapterPlanUsageType,
    ChapterWritingPlanCandidate,
    ResearchDecision,
    ResearchQuery,
)
from .writer_research import ResearchExecutionSubject


_PROHIBITED_RE = re.compile(
    r"企业|本公司|我方|资质|资格|业绩|案例|人员|证书|社保|报价|财务|承诺"
)
_PUBLIC_GAP_RE = re.compile(r"政策|法规|标准|规范|依据|背景|行业方法|通用方法")


class ResearchExecutor(Protocol):
    def execute(
        self,
        subject: ResearchExecutionSubject,
        decision: ResearchDecision,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source(
    source_type: ChapterPlanSourceType,
    reference_id: str,
    title: str,
    content: Any,
    *,
    preview: str = "",
    snapshot_ref: str = "",
) -> ChapterPlanMaterialSource:
    content_hash = _digest(content)
    source_id = "PS-" + _digest(
        {
            "source_type": source_type.value,
            "reference_id": reference_id,
            "content_hash": content_hash,
        }
    )[:20]
    return ChapterPlanMaterialSource(
        source_id=source_id,
        source_type=source_type,
        reference_id=reference_id,
        content_hash=content_hash,
        title=title,
        preview=str(preview or "")[:240],
        snapshot_ref=snapshot_ref or f"authority:{source_type.value}:{reference_id}",
    )


def _text(item: Any) -> str:
    if isinstance(item, dict):
        for key in (
            "statement",
            "text",
            "normalized_requirement",
            "criterion",
            "description",
            "title",
            "body",
            "summary",
        ):
            if str(item.get(key) or "").strip():
                return str(item[key]).strip()
        return _canonical(item)
    return str(item or "").strip()


def _related(unit_text: str, item: dict[str, Any], unit_id: str) -> bool:
    explicit = {
        str(value)
        for value in (
            item.get("content_unit_ids")
            or item.get("target_unit_ids")
            or item.get("target_node_ids")
            or []
        )
    }
    if explicit:
        return unit_id in explicit
    left = re.sub(r"\s+", "", unit_text)
    right = re.sub(r"\s+", "", _text(item))
    if len(left) < 2 or len(right) < 2:
        return False
    left_grams = {left[index : index + 2] for index in range(len(left) - 1)}
    right_grams = {right[index : index + 2] for index in range(len(right) - 1)}
    return len(left_grams & right_grams) >= 2


class ChapterWritingPlanBuilder:
    """Deterministic source -> content-unit binding builder used only in shadow mode."""

    def __init__(self, research_executor: ResearchExecutor | None = None) -> None:
        self.research_executor = research_executor

    def build(
        self,
        *,
        chapter: dict[str, Any],
        writing_plan: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
        project_context: dict[str, Any] | None = None,
        chapter_context_items: list[dict[str, Any]] | None = None,
        user_material_blocks: list[dict[str, Any]] | None = None,
        sibling_references: list[dict[str, Any]] | None = None,
    ) -> ChapterWritingPlanCandidate:
        tender = [item for item in (tender_requirements or []) if isinstance(item, dict)]
        scoring = [item for item in (scoring_requirements or []) if isinstance(item, dict)]
        project = project_context if isinstance(project_context, dict) else {}
        local_items = [item for item in (chapter_context_items or []) if isinstance(item, dict)]
        material_blocks = [
            item for item in (user_material_blocks or []) if isinstance(item, dict)
        ]
        sibling_rows = [
            item for item in (sibling_references or []) if isinstance(item, dict)
        ]
        legacy = writing_plan or compile_chapter_writing_plan(
            chapter,
            tender_requirements=tender,
            scoring_requirements=scoring,
            chapter_context_items=local_items,
            project_context=project,
        )
        blocks = [item for item in legacy.get("blocks") or [] if isinstance(item, dict)]
        if not blocks:
            raise ValueError("shadow plan builder requires at least one writing block")

        units: list[ChapterPlanContentUnit] = []
        sources: dict[str, ChapterPlanMaterialSource] = {}
        bindings: list[ChapterPlanSourceBinding] = []
        decisions: list[ChapterPlanResearchDecision] = []
        requirement_by_id = {
            str(item.get("requirement_id") or ""): item
            for item in tender
            if str(item.get("requirement_id") or "").strip()
        }
        score_by_id = {
            str(item.get("score_point_id") or ""): item
            for item in scoring
            if str(item.get("score_point_id") or "").strip()
        }

        def bind(
            source: ChapterPlanMaterialSource,
            unit_id: str,
            usage: ChapterPlanUsageType,
            instruction: str,
            *,
            required: bool = False,
        ) -> None:
            sources[source.source_id] = source
            bindings.append(
                ChapterPlanSourceBinding(
                    source_id=source.source_id,
                    content_unit_id=unit_id,
                    usage_type=usage,
                    instruction=instruction,
                    required=required,
                )
            )

        for index, block in enumerate(blocks):
            unit_id = str(block.get("block_id") or f"WO-{index + 1}").strip()
            requirement_ids = [
                str(value) for value in block.get("requirement_ids") or [] if str(value).strip()
            ]
            score_point_ids = [
                str(block.get("score_point_id"))
            ] if str(block.get("score_point_id") or "").strip() else []
            condition_ids = [
                str(block.get("condition_id"))
            ] if str(block.get("condition_id") or "").strip() else []
            units.append(
                ChapterPlanContentUnit(
                    unit_id=unit_id,
                    title=str(block.get("heading") or f"写作要点 {index + 1}"),
                    purpose=str(legacy.get("purpose") or ""),
                    must_answer=str(block.get("must_answer") or ""),
                    instructions="\n".join(
                        value for value in (
                            str(block.get("must_answer") or "").strip(),
                            str(block.get("write_as") or "").strip(),
                        ) if value
                    ),
                    order=index,
                    requirement_ids=requirement_ids,
                    score_point_ids=score_point_ids,
                    condition_ids=condition_ids,
                )
            )

            for requirement_id in requirement_ids:
                item = requirement_by_id.get(requirement_id)
                if item:
                    bind(
                        _source(
                            ChapterPlanSourceType.TENDER_REQUIREMENT,
                            requirement_id,
                            str(item.get("title") or requirement_id),
                            item,
                            preview=_text(item),
                        ),
                        unit_id,
                        ChapterPlanUsageType.CONSTRAINT,
                        "作为招标约束逐项响应，不扩写未声明要求。",
                        required=True,
                    )
            for score_id in score_point_ids:
                item = score_by_id.get(score_id)
                if item:
                    bind(
                        _source(
                            ChapterPlanSourceType.SCORE_OBLIGATION,
                            score_id,
                            str(item.get("title") or score_id),
                            item,
                            preview=_text(item),
                        ),
                        unit_id,
                        ChapterPlanUsageType.CONSTRAINT,
                        "覆盖评分义务，但正文不得出现内部评分术语。",
                        required=True,
                    )

            fact_refs = [str(value) for value in block.get("project_fact_refs") or []]
            for fact_ref in fact_refs:
                match = re.fullmatch(r"work_packages\[(\d+)\]", fact_ref)
                packages = list(project.get("work_packages") or [])
                fact = packages[int(match.group(1))] if match and int(match.group(1)) < len(packages) else fact_ref
                bind(
                    _source(
                        ChapterPlanSourceType.GLOBAL_PROJECT_FACT,
                        fact_ref,
                        fact_ref,
                        fact,
                        preview=_text(fact),
                    ),
                    unit_id,
                    ChapterPlanUsageType.BASE_FACT,
                    "按项目事实原意使用，禁止以公开资料替换。",
                    required=True,
                )

            for local_index, item in enumerate(local_items):
                reference_id = str(item.get("item_id") or item.get("id") or f"local-{local_index + 1}")
                bind(
                    _source(
                        ChapterPlanSourceType.CHAPTER_CONTEXT_ITEM,
                        reference_id,
                        str(item.get("title") or reference_id),
                        item,
                        preview=_text(item),
                    ),
                    unit_id,
                    ChapterPlanUsageType.SUPPORT,
                    "仅在与本内容块直接相关时使用。",
                )

            unit_text = " ".join(
                str(block.get(key) or "") for key in ("heading", "must_answer", "write_as")
            )
            for material_index, item in enumerate(material_blocks):
                if not _related(unit_text, item, unit_id):
                    continue
                reference_id = str(
                    item.get("block_id")
                    or item.get("source_id")
                    or f"material-{material_index + 1}"
                )
                bind(
                    _source(
                        ChapterPlanSourceType.USER_MATERIAL_BLOCK,
                        reference_id,
                        str(item.get("title") or reference_id),
                        item,
                        preview=_text(item),
                        snapshot_ref=f"source-index:{reference_id}",
                    ),
                    unit_id,
                    ChapterPlanUsageType.SUPPORT,
                    "仅提取与本内容块直接相关的用户材料，不扩展章节职责。",
                )
            for sibling_index, item in enumerate(sibling_rows):
                if not item.get("has_content") or not _related(
                    unit_text, item, unit_id
                ):
                    continue
                sibling_id = str(
                    item.get("chapter_id") or f"sibling-{sibling_index + 1}"
                )
                revision = int(item.get("content_revision") or 0)
                reference_id = f"{sibling_id}@{revision}"
                bind(
                    _source(
                        ChapterPlanSourceType.SIBLING_REFERENCE,
                        reference_id,
                        str(item.get("title") or sibling_id),
                        item,
                        preview=str(item.get("summary") or item.get("purpose") or ""),
                        snapshot_ref=f"chapter-content:{reference_id}",
                    ),
                    unit_id,
                    ChapterPlanUsageType.CROSS_REFERENCE,
                    "只用于对齐兄弟章边界和已形成骨架，禁止复制正文。",
                )
            prohibited = bool(_PROHIBITED_RE.search(unit_text))
            needs_research = bool(
                not prohibited
                and not fact_refs
                and (
                    block.get("needs_public_research")
                    or _PUBLIC_GAP_RE.search(unit_text)
                )
            )
            query = (
                f"{chapter.get('title') or legacy.get('chapter_title') or ''} "
                f"{block.get('heading') or ''} {block.get('must_answer') or ''} 官方政策 标准 规范"
            ).strip()[:400] if needs_research else ""
            status = "planned" if needs_research else "skipped"
            evidence_ids: list[str] = []
            reason = (
                "企业事实或项目承诺属于禁搜范围。"
                if prohibited
                else "已绑定项目事实，公开资料不得替代。"
                if fact_refs
                else "内容块存在公开政策、标准或规范缺口。"
                if needs_research
                else "现有约束足以规划本内容块。"
            )
            if needs_research and self.research_executor is not None:
                decision_seed = _digest({"unit_id": unit_id, "query": query})[:16]
                research = ResearchDecision(
                    decision_id=f"WRD-{decision_seed}",
                    operation_id="chapter-plan-shadow",
                    unit_id=unit_id,
                    applicable_chapter_ids=[str(chapter.get("chapter_id") or legacy.get("chapter_id") or unit_id)],
                    applicable_chapter_titles=[str(chapter.get("title") or legacy.get("chapter_title") or "")],
                    needs_research=True,
                    reason=reason,
                    queries=[ResearchQuery(
                        query_id=f"WRQ-{decision_seed}",
                        question=query,
                        target_node_ids=[unit_id],
                        applicability=str(block.get("heading") or unit_id),
                    )],
                    prohibited_research_scopes=["enterprise_fact", "project_commitment"],
                    decision_status="planned",
                    created_at=datetime.now(UTC).isoformat(),
                )
                try:
                    executed, snapshots = self.research_executor.execute(
                        ResearchExecutionSubject(
                            unit_id=unit_id,
                            global_project_context=project,
                            project_context=project,
                            document_target_constraints=[{
                                "node_id": unit_id,
                                "title": str(block.get("heading") or unit_id),
                                "purpose": str(block.get("must_answer") or ""),
                                "content_policy": "full",
                            }],
                            chapter_context_items=local_items,
                            requirement_excerpts=tender,
                            score_obligations=scoring,
                        ),
                        research,
                    )
                    status = "published" if executed.get("decision_status") == "published" else "failed"
                    for snapshot in snapshots:
                        evidence_ids.extend(str(value) for value in snapshot.get("evidence_ids") or [])
                        for evidence in snapshot.get("sources") or []:
                            evidence_id = str(evidence.get("evidence_id") or "").strip()
                            if not evidence_id:
                                continue
                            web_source = _source(
                                ChapterPlanSourceType.WEB_EVIDENCE,
                                evidence_id,
                                str(evidence.get("title") or evidence_id),
                                evidence,
                                preview=str(evidence.get("publisher") or ""),
                                snapshot_ref=f"evidence:{snapshot.get('batch_id') or ''}:{evidence_id}",
                            )
                            bind(
                                web_source,
                                unit_id,
                                ChapterPlanUsageType.EVIDENCE,
                                "仅用于其可核验支持范围，并保留来源引用。",
                            )
                except Exception as exc:
                    status = "failed"
                    reason = f"公开资料搜索失败: {type(exc).__name__}"
            decisions.append(
                ChapterPlanResearchDecision(
                    decision_id="PRD-" + _digest({"unit_id": unit_id, "query": query})[:16],
                    content_unit_id=unit_id,
                    needs_research=needs_research,
                    prohibited=prohibited,
                    reason=reason,
                    query=query,
                    status=status,
                    evidence_ids=sorted(set(evidence_ids)),
                )
            )

        source_rows = sorted(sources.values(), key=lambda item: item.source_id)
        bound_ids: dict[str, list[str]] = {unit.unit_id: [] for unit in units}
        for binding in bindings:
            if binding.source_id not in bound_ids[binding.content_unit_id]:
                bound_ids[binding.content_unit_id].append(binding.source_id)
        units = [
            unit.model_copy(update={"source_refs": sorted(bound_ids[unit.unit_id])})
            for unit in units
        ]
        project_bound = sum(bool(block.get("project_fact_refs")) for block in blocks)
        return ChapterWritingPlanCandidate(
            content_units=units,
            sources=source_rows,
            source_bindings=sorted(
                bindings,
                key=lambda item: (item.content_unit_id, item.source_id, item.usage_type.value),
            ),
            research_decisions=decisions,
            metadata={
                "projection": "shadow_builder",
                "legacy_schema_version": str(legacy.get("schema_version") or ""),
                "legacy_plan_hash": _digest(legacy),
                "shadow_status": "failed" if any(item.status == "failed" for item in decisions) else "ready",
                "shadow_diff": {
                    "legacy_block_count": len(blocks),
                    "content_unit_count": len(units),
                    "search_decision_count": sum(item.needs_research for item in decisions),
                    "published_evidence_count": sum(len(item.evidence_ids) for item in decisions),
                    "project_fact_coverage": project_bound / len(blocks),
                },
            },
        )
