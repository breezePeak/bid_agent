"""Planning Agent: controlled projections from promoted requirements and scoring logic."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from control_plane import ControlStore, WorkspaceContext

from .artifact_promotion import build_declared_dependency_fingerprint
from .contracts import (
    BlueprintNode,
    ChapterBlueprint,
    ContractNode,
    DocumentQualityGate,
    EvidenceNeed,
    InputRole,
    ProjectFact,
    ProjectModel,
    RequirementKind,
    RequirementLedger,
    ResponseDuty,
    ResponseTopic,
    ResponseTopicGraph,
    ScoreModel,
    SourceAnchor,
    SourceBlock,
    SourceIndex,
    TemplateStructureContract,
    TopicChapterAssignment,
    TopicEdge,
)
from .planning_inference import (
    ChapterOutlineCandidate,
    ProjectUnderstandingCandidate,
    TopicDutyPlanningCandidate,
    _normalize_project_source_refs,
)
from .proposals import DependencyRef, ProposalEnvelope
from .proposals import InferenceReceiptRef
from .project_model import is_enterprise_claim
from .scoring_outline_policy import (
    SCORING_OUTLINE_POLICY_VERSION,
    is_sectionable_quality_condition,
    document_quality_check_items,
    document_quality_criteria,
    full_score_condition_heading,
    is_document_quality_score,
    outline_structure_key,
    outline_subject,
    score_group_category,
    score_group_chapter_title,
    score_leaf_title,
    score_point_chapter_title,
)


class PlanningCandidateCompilationError(ValueError):
    """A model candidate cannot be compiled without changing its semantics."""


def _stable_planning_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _dedupe_anchors(anchors: list[SourceAnchor]) -> list[SourceAnchor]:
    unique: list[SourceAnchor] = []
    seen: set[tuple[str, str, int | None, str]] = set()
    for anchor in anchors:
        key = (
            anchor.source_input_id,
            anchor.chunk_id,
            anchor.page,
            anchor.location,
        )
        if key not in seen:
            seen.add(key)
            unique.append(anchor)
    return unique


def _merged_source_hashes(*sources: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source_hashes in sources:
        for source_id, source_hash in source_hashes.items():
            existing = merged.get(source_id)
            if existing is not None and existing != source_hash:
                raise PlanningCandidateCompilationError(
                    f"上游 Artifact 对来源 {source_id} 的 hash 不一致"
                )
            merged[source_id] = source_hash
    return merged


_PRICE_SECTION_RE = re.compile(r"报价|价格|投标报价|开标一览|报价表|费用|金额|计价")
_COMMERCIAL_SECTION_RE = re.compile(
    r"商务|资信|资格|资质|业绩|案例|人员|财务|信用|合同|授权|声明|承诺|偏离|保证金|投标函"
)
_TECHNICAL_SECTION_RE = re.compile(
    r"技术|方案|实施|服务|系统|平台|数据|接口|安全|运维|培训|验收|测试|质量|进度|风险|项目管理|设计"
)


def _domain_policy(
    *,
    title: str,
    purpose: str = "",
    score_categories: list[str] | None = None,
    requirement_kinds: list[RequirementKind] | None = None,
) -> tuple[str, str, str | None]:
    text = f"{title} {purpose}"
    categories = {str(item or "") for item in (score_categories or [])}
    kinds = set(requirement_kinds or [])
    technical = bool(_TECHNICAL_SECTION_RE.search(text))
    if "price" in categories or _PRICE_SECTION_RE.search(text):
        return "price", "deferred_title_only", "本期价格部分仅保留标题层级，不生成正文。"
    commercial_kind = bool(
        kinds
        & {
            RequirementKind.QUALIFICATION,
            RequirementKind.CONTRACT,
        }
    )
    if (
        "business" in categories
        or commercial_kind
        or (_COMMERCIAL_SECTION_RE.search(text) and not technical)
    ):
        return "commercial", "deferred_title_only", "本期商务部分仅保留标题层级，不生成正文。"
    return "technical", "full", None


def _reference_catalog(
    ledger: RequirementLedger,
    scores: ScoreModel,
    source_blocks: list[SourceBlock],
    *,
    project: ProjectModel | None = None,
) -> dict[str, list[SourceAnchor]]:
    catalog: dict[str, list[SourceAnchor]] = {}
    for requirement in ledger.requirements:
        catalog[f"RequirementLedger:{requirement.requirement_id}"] = [
            requirement.source_anchor
        ]
    for group in scores.groups:
        catalog[f"ScoreModel:{group.group_id}"] = []
    for point in scores.points:
        catalog[f"ScoreModel:{point.score_point_id}"] = list(point.source_anchors)
        for condition in point.score_conditions:
            catalog[f"ScoreModel:{condition.condition_id}"] = (
                [condition.source_anchor] if condition.source_anchor else []
            )
        for unit in point.response_units:
            catalog[f"ScoreModel:{unit.unit_id}"] = list(point.source_anchors)
    for block in source_blocks:
        anchors = [block.source_anchor]
        catalog[f"SourceIndex:{block.block_id}"] = anchors
        catalog[
            f"SourceIndex:{block.input_id}:{block.source_anchor.chunk_id}"
        ] = anchors
    if project is not None:
        catalog[f"ProjectModel:{project.project_id}"] = []
        for fact in (
            *project.confirmed_facts,
            *project.inferences,
            *project.conflicts,
        ):
            catalog[f"ProjectModel:{fact.fact_id}"] = _dedupe_anchors(
                [
                    *([fact.source_anchor] if fact.source_anchor else []),
                    *(
                        anchor
                        for ref in fact.upstream_refs
                        for anchor in catalog.get(ref, [])
                    ),
                ]
            )
    return catalog


def _project_reference_catalog(
    ledger: RequirementLedger,
    source_blocks: list[SourceBlock],
) -> dict[str, list[SourceAnchor]]:
    """References allowed in the score-independent ProjectModel stage."""

    catalog: dict[str, list[SourceAnchor]] = {
        f"RequirementLedger:{item.requirement_id}": [item.source_anchor]
        for item in ledger.requirements
    }
    for block in source_blocks:
        anchors = [block.source_anchor]
        catalog[f"SourceIndex:{block.block_id}"] = anchors
        catalog[
            f"SourceIndex:{block.input_id}:{block.source_anchor.chunk_id}"
        ] = anchors
    return catalog


def _anchors_for_refs(
    refs: list[str],
    catalog: dict[str, list[SourceAnchor]],
    *,
    owner: str,
) -> list[SourceAnchor]:
    if unknown := set(refs) - set(catalog):
        raise PlanningCandidateCompilationError(
            f"{owner} 引用了未知上游 ID: {sorted(unknown)}"
        )
    return _dedupe_anchors(
        [anchor for ref in refs for anchor in catalog[ref]]
    )


def _require_unique(values: list[str], *, owner: str) -> None:
    if len(values) != len(set(values)):
        raise PlanningCandidateCompilationError(f"{owner} 不允许重复 ID")


class PlanningAgent:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def project_model(
        self, ledger: RequirementLedger, scores: ScoreModel, source_blocks: list[SourceBlock], *, revision: int,
    ) -> ProjectModel:
        mandatory = [item for item in ledger.requirements if item.kind is RequirementKind.MANDATORY]
        qualifications = [item for item in ledger.requirements if item.kind is RequirementKind.QUALIFICATION]
        company_blocks = [block for block in source_blocks if block.input_role is InputRole.COMPANY]
        tender_blocks = [block for block in source_blocks if block.input_role is InputRole.TENDER]
        evidence_needs: list[EvidenceNeed] = []
        unknowns: list[str] = []
        if qualifications and not company_blocks:
            unknowns.append("尚未提供可核验的企业资质、人员或业绩材料")
            evidence_needs.append(EvidenceNeed(need_id="EN-company-qualification", question="请补充与资格要求对应的企业资质、人员和业绩材料。", topic_id="company_qualification", priority="blocking", blocking_scope="content_unit", deadline_stage="write_content", query_budget=0))
        for candidate in scores.evidence_need_candidates:
            evidence_needs.append(EvidenceNeed(need_id=candidate.need_id, question=candidate.question, topic_id=f"score:{candidate.score_point_id}", priority=candidate.priority, blocking_scope="content_unit" if candidate.priority == "blocking" else "none", deadline_stage="chapter_writing", query_budget=0))
        deliverables = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.DELIVERABLE]
        acceptance = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.ACCEPTANCE]
        if not deliverables:
            unknowns.append("招标来源未识别出明确交付物")
        if not acceptance:
            unknowns.append("招标来源未识别出明确验收条件")
        source_hashes = {**ledger.source_hashes, **scores.source_hashes}
        return ProjectModel(
            revision=revision, source_hashes=source_hashes,
            project_id=f"project-{hashlib.sha256('|'.join(item.requirement_id for item in ledger.requirements).encode()).hexdigest()[:12]}",
            identity={"project_name": tender_blocks[0].content[:80] if tender_blocks else "未命名项目"},
            background=[tender_blocks[0].content] if tender_blocks else [],
            goals=[item.normalized_requirement for item in mandatory[:3]],
            scope=[item.normalized_requirement for item in mandatory], work_packages=[item.normalized_requirement for item in mandatory],
            deliverables=deliverables, acceptance_conditions=acceptance,
            milestones=[item.normalized_requirement for item in ledger.requirements if any(token in item.normalized_requirement for token in ("工期", "期限", "工作日", "个月", "年度"))],
            roles=[item.normalized_requirement for item in qualifications],
            constraints=[item.normalized_requirement for item in ledger.requirements if item.kind in {RequirementKind.CONTRACT, RequirementKind.QUALIFICATION}],
            confirmed_facts=[ProjectFact(fact_id=f"F-{hashlib.sha256(block.block_id.encode()).hexdigest()[:12]}", statement=block.content, source_anchor=block.source_anchor) for block in company_blocks],
            unknowns=unknowns, requirement_ids=[item.requirement_id for item in ledger.requirements],
            score_point_ids=[point.score_point_id for point in scores.points], evidence_needs=evidence_needs,
        )

    def compile_project_candidate(
        self,
        candidate: ProjectUnderstandingCandidate,
        ledger: RequirementLedger,
        source_index: SourceIndex,
        *,
        revision: int,
    ) -> ProjectModel:
        """Compile a source-grounded understanding without rewriting its prose."""

        source_blocks = list(source_index.blocks)

        # Accept the documented bare SourceIndex shorthand only when it maps to
        # one exact block/chunk in the authoritative frozen SourceIndex.  The
        # normalized candidate is then subject to the existing strict catalog
        # and anchor checks below.
        candidate = ProjectUnderstandingCandidate.model_validate(
            _normalize_project_source_refs(
                candidate.model_dump(mode="json"),
                [
                    {
                        "block_id": block.block_id,
                        "input_id": block.input_id,
                        "source_anchor": block.source_anchor.model_dump(mode="json"),
                    }
                    for block in source_blocks
                ],
            )
        )
        if candidate.review_status == "blocked":
            raise PlanningCandidateCompilationError(
                "ProjectUnderstandingCandidate 已标记 blocked"
            )
        active_requirement_ids = {
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        }
        # Requirement and score coverage are compiler-owned metadata.

        catalog = _project_reference_catalog(ledger, source_blocks)
        cited_statements: list[tuple[str, object]] = []
        if candidate.project_name is not None:
            cited_statements.append(("project_name", candidate.project_name))
        cited_statements.extend(
            (f"identity.{item.field}", item) for item in candidate.identity
        )
        semantic_fields = (
            "background",
            "goals",
            "scope",
            "boundaries",
            "work_packages",
            "dependencies",
            "inputs",
            "processing",
            "outputs",
            "deliverables",
            "acceptance_conditions",
            "milestones",
            "roles",
            "risks",
            "constraints",
        )
        for field_name in semantic_fields:
            cited_statements.extend(
                (f"{field_name}.{index}", statement)
                for index, statement in enumerate(
                    getattr(candidate, field_name),
                    start=1,
                )
            )
        cited_statements.extend(
            (f"terminology.{item.term}", item)
            for item in candidate.terminology
        )
        semantic_upstream_refs = list(
            dict.fromkeys(
                [
                    ref
                    for _, item in cited_statements
                    for ref in item.upstream_refs
                ]
                + [
                    ref
                    for fact in candidate.facts
                    for ref in fact.upstream_refs
                ]
                + [
                    ref
                    for need in candidate.evidence_needs
                    for ref in need.upstream_refs
                ]
            )
        )
        if forbidden := sorted(
            ref for ref in semantic_upstream_refs if ref.startswith("ScoreModel:")
        ):
            raise PlanningCandidateCompilationError(
                "ProjectModel 新候选不允许引用 ScoreModel: "
                f"{forbidden}"
            )
        for owner, item in cited_statements:
            _anchors_for_refs(
                item.upstream_refs,
                catalog,
                owner=f"ProjectUnderstandingCandidate.{owner}",
            )

        known_requirement_ids = {
            item.requirement_id for item in ledger.requirements
        }
        for fact in candidate.facts:
            _anchors_for_refs(
                fact.upstream_refs,
                catalog,
                owner=f"ProjectFactCandidate {fact.local_id}",
            )
            if unknown := set(fact.requirement_ids) - known_requirement_ids:
                raise PlanningCandidateCompilationError(
                    f"ProjectFactCandidate {fact.local_id} 引用未知 Requirement: "
                    f"{sorted(unknown)}"
                )
        for need in candidate.evidence_needs:
            if need.review_status == "blocked":
                raise PlanningCandidateCompilationError(
                    f"ProjectEvidenceNeedCandidate {need.local_id} 已标记 blocked"
                )
            _anchors_for_refs(
                need.upstream_refs,
                catalog,
                owner=f"ProjectEvidenceNeedCandidate {need.local_id}",
            )

        identity: dict[str, str] = {}
        for item in candidate.identity:
            if item.field in identity:
                raise PlanningCandidateCompilationError(
                    f"项目身份字段重复: {item.field}"
                )
            identity[item.field] = item.value
        if candidate.project_name is not None:
            existing_name = identity.get("project_name")
            if existing_name is not None and existing_name != candidate.project_name.text:
                raise PlanningCandidateCompilationError(
                    "project_name 与 identity.project_name 不一致"
                )
            identity["project_name"] = candidate.project_name.text

        terminology: dict[str, str] = {}
        for item in candidate.terminology:
            if item.term in terminology:
                raise PlanningCandidateCompilationError(
                    f"项目术语重复: {item.term}"
                )
            terminology[item.term] = item.definition

        explicit_statements = {fact.statement for fact in candidate.facts}
        derived_confirmed_facts: list[ProjectFact] = []
        derived_inference_facts: list[ProjectFact] = []
        source_roles_by_anchor = {
            (block.input_id, block.source_anchor.chunk_id): block.input_role
            for block in source_blocks
        }
        for owner, item in cited_statements:
            statement = getattr(item, "text", None) or getattr(
                item,
                "value",
                None,
            )
            if statement is None:
                statement = f"{item.term}：{item.definition}"
            if statement in explicit_statements:
                continue
            anchors = _anchors_for_refs(
                item.upstream_refs,
                catalog,
                owner=f"ProjectUnderstandingCandidate.{owner}",
            )
            requirement_refs = [
                ref.removeprefix("RequirementLedger:")
                for ref in item.upstream_refs
                if ref.startswith("RequirementLedger:")
            ]
            has_company_source = any(
                source_roles_by_anchor.get(
                    (anchor.source_input_id, anchor.chunk_id)
                ) is InputRole.COMPANY
                for anchor in anchors
            )
            unverified_enterprise_claim = (
                is_enterprise_claim(statement) and not has_company_source
            )
            target_facts = (
                derived_inference_facts
                if unverified_enterprise_claim
                else derived_confirmed_facts
            )
            compiled_statement = (
                f"待企业材料核验：{statement}"
                if unverified_enterprise_claim
                else statement
            )
            target_facts.append(
                ProjectFact(
                    fact_id=_stable_planning_id(
                        "F",
                        owner,
                        compiled_statement,
                        *item.upstream_refs,
                    ),
                    statement=compiled_statement,
                    source_anchor=anchors[0] if anchors else None,
                    requirement_ids=requirement_refs,
                    upstream_refs=list(item.upstream_refs),
                )
            )

        fact_groups: dict[str, list[ProjectFact]] = {
            "confirmed": derived_confirmed_facts,
            "inference": derived_inference_facts,
            "conflict": [],
        }
        for fact in candidate.facts:
            anchors = _anchors_for_refs(
                fact.upstream_refs,
                catalog,
                owner=f"ProjectFactCandidate {fact.local_id}",
            )
            has_company_source = any(
                source_roles_by_anchor.get(
                    (anchor.source_input_id, anchor.chunk_id)
                ) is InputRole.COMPANY
                for anchor in anchors
            )
            unverified_enterprise_claim = (
                is_enterprise_claim(fact.statement) and not has_company_source
            )
            classification = (
                "inference"
                if unverified_enterprise_claim
                and fact.classification == "confirmed"
                else fact.classification
            )
            statement = (
                f"待企业材料核验：{fact.statement}"
                if unverified_enterprise_claim
                and fact.classification != "conflict"
                and not fact.statement.startswith("待企业材料核验：")
                else fact.statement
            )
            fact_groups[classification].append(
                ProjectFact(
                    fact_id=_stable_planning_id(
                        "F",
                        classification,
                        fact.local_id,
                        statement,
                        *fact.upstream_refs,
                    ),
                    statement=statement,
                    source_anchor=anchors[0] if anchors else None,
                    requirement_ids=fact.requirement_ids,
                    upstream_refs=list(fact.upstream_refs),
                )
            )

        evidence_needs = [
            EvidenceNeed(
                need_id=_stable_planning_id(
                    "EN",
                    item.local_id,
                    item.question,
                    *item.upstream_refs,
                ),
                question=item.question,
                topic_id=item.topic_id,
                priority=item.priority,
                blocking_scope=item.blocking_scope,
                deadline_stage=item.deadline_stage,
                query_budget=item.query_budget,
            )
            for item in candidate.evidence_needs
        ]
        source_hashes = _merged_source_hashes(
            ledger.source_hashes,
            source_index.source_hashes,
        )
        candidate_hash = hashlib.sha256(
            candidate.model_dump_json().encode("utf-8")
        ).hexdigest()
        return ProjectModel(
            revision=revision,
            source_hashes=source_hashes,
            project_id=_stable_planning_id(
                "project",
                ledger.revision,
                source_index.revision,
                candidate_hash,
            ),
            identity=identity,
            **{
                field_name: [
                    statement.text
                    for statement in getattr(candidate, field_name)
                ]
                for field_name in semantic_fields
            },
            terminology=terminology,
            confirmed_facts=fact_groups["confirmed"],
            inferences=fact_groups["inference"],
            conflicts=fact_groups["conflict"],
            unknowns=candidate.unknowns,
            requirement_ids=sorted(active_requirement_ids),
            score_point_ids=[],
            semantic_upstream_refs=semantic_upstream_refs,
            evidence_needs=evidence_needs,
        )

    def compile_topic_candidate(
        self,
        candidate: TopicDutyPlanningCandidate,
        ledger: RequirementLedger,
        scores: ScoreModel,
        project: ProjectModel,
        source_blocks: list[SourceBlock],
        *,
        revision: int,
    ) -> ResponseTopicGraph:
        """Compile semantic Topic/Duty candidates while preserving all prose."""

        if candidate.review_status == "blocked":
            raise PlanningCandidateCompilationError(
                "TopicDutyPlanningCandidate 已标记 blocked"
            )
        if project.requirement_ids != [
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        ] and set(project.requirement_ids) != {
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        }:
            raise PlanningCandidateCompilationError(
                "ProjectModel 与当前 RequirementLedger 覆盖范围不一致"
            )
        if set(project.score_point_ids) != {
            point.score_point_id for point in scores.points
        }:
            raise PlanningCandidateCompilationError(
                "ProjectModel 与当前 ScoreModel 覆盖范围不一致"
            )

        requirements = {
            item.requirement_id: item for item in ledger.requirements
        }
        score_points = {
            point.score_point_id: point for point in scores.points
        }
        score_response_units = {
            unit.unit_id: (point.score_point_id, unit)
            for point in scores.points
            for unit in point.response_units
        }
        score_groups = {group.group_id: group for group in scores.groups}
        group_order = {
            group.group_id: index for index, group in enumerate(scores.groups)
        }
        evidence_need_ids = {
            item.need_id for item in project.evidence_needs
        }
        catalog = _reference_catalog(
            ledger,
            scores,
            source_blocks,
            project=project,
        )

        _require_unique(
            candidate.root_topic_local_ids,
            owner="TopicDutyPlanningCandidate.root_topic_local_ids",
        )
        local_topics = {topic.local_id: topic for topic in candidate.topics}
        for topic in candidate.topics:
            _anchors_for_refs(
                topic.upstream_refs,
                catalog,
                owner=f"ResponseTopicCandidate {topic.local_id}",
            )
            if unknown := set(topic.requirement_ids) - set(requirements):
                raise PlanningCandidateCompilationError(
                    f"Topic {topic.local_id} 引用未知 Requirement: {sorted(unknown)}"
                )
            if unknown := set(topic.score_point_ids) - set(score_points):
                raise PlanningCandidateCompilationError(
                    f"Topic {topic.local_id} 引用未知 ScorePoint: {sorted(unknown)}"
                )
            if len(topic.score_point_ids) > 1:
                raise PlanningCandidateCompilationError(
                    f"Topic {topic.local_id} 同时绑定多个 ScorePoint；"
                    "请用语义父 Topic 聚合，并为每个评分责任建立子 Topic"
                )
            for requirement_id in topic.requirement_ids:
                ref = f"RequirementLedger:{requirement_id}"
                if ref not in topic.upstream_refs:
                    raise PlanningCandidateCompilationError(
                        f"Topic {topic.local_id} 缺少直接 Requirement 引用 {ref}"
                    )
            for score_point_id in topic.score_point_ids:
                ref = f"ScoreModel:{score_point_id}"
                if ref not in topic.upstream_refs:
                    raise PlanningCandidateCompilationError(
                        f"Topic {topic.local_id} 缺少直接 ScorePoint 引用 {ref}"
                    )

        parent_by_topic = {
            topic.local_id: topic.parent_local_id for topic in candidate.topics
        }
        for local_id in parent_by_topic:
            seen: set[str] = set()
            cursor: str | None = local_id
            while cursor is not None:
                if cursor in seen:
                    raise PlanningCandidateCompilationError(
                        "Topic 父子关系存在环"
                    )
                seen.add(cursor)
                cursor = parent_by_topic[cursor]

        covered_requirements: set[str] = set()
        covered_scores: set[str] = set()
        covered_score_units: list[str] = []
        for duty in candidate.duties:
            topic = local_topics[duty.topic_local_id]
            _require_unique(
                duty.score_response_unit_ids,
                owner=f"ResponseDutyCandidate {duty.local_id}.score_response_unit_ids",
            )
            if unknown := set(duty.requirement_ids) - set(requirements):
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 引用未知 Requirement: {sorted(unknown)}"
                )
            if unknown := set(duty.score_point_ids) - set(score_points):
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 引用未知 ScorePoint: {sorted(unknown)}"
                )
            if unknown := set(duty.score_response_unit_ids) - set(
                score_response_units
            ):
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 引用未知 ScoreResponseUnit: "
                    f"{sorted(unknown)}"
                )
            if len(duty.score_response_unit_ids) > 1:
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 压缩了多个独立 ScoreResponseUnit；"
                    "每个独立得分任务必须建立一个 Duty"
                )
            for unit_id in duty.score_response_unit_ids:
                owner_score_id = score_response_units[unit_id][0]
                if owner_score_id not in duty.score_point_ids:
                    raise PlanningCandidateCompilationError(
                        f"Duty {duty.local_id} 的 ScoreResponseUnit {unit_id} "
                        f"属于 {owner_score_id}，但 Duty 未绑定该 ScorePoint"
                    )
            if unknown := set(duty.evidence_need_ids) - evidence_need_ids:
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 引用未知 EvidenceNeed: {sorted(unknown)}"
                )
            if not set(duty.requirement_ids) <= set(topic.requirement_ids):
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 的 Requirement 未在所属 Topic 声明"
                )
            if not set(duty.score_point_ids) <= set(topic.score_point_ids):
                raise PlanningCandidateCompilationError(
                    f"Duty {duty.local_id} 的 ScorePoint 未在所属 Topic 声明"
                )
            covered_requirements.update(duty.requirement_ids)
            covered_scores.update(duty.score_point_ids)
            covered_score_units.extend(duty.score_response_unit_ids)

        active_requirements = {
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        }
        if covered_requirements != active_requirements:
            raise PlanningCandidateCompilationError(
                "Topic/Duty 未精确覆盖有效 Requirement；"
                f"missing={sorted(active_requirements - covered_requirements)}, "
                f"extra={sorted(covered_requirements - active_requirements)}"
            )
        if covered_scores != set(score_points):
            raise PlanningCandidateCompilationError(
                "Topic/Duty 未精确覆盖 ScorePoint；"
                f"missing={sorted(set(score_points) - covered_scores)}, "
                f"extra={sorted(covered_scores - set(score_points))}"
            )
        expected_score_units = set(score_response_units)
        if (
            set(covered_score_units) != expected_score_units
            or len(covered_score_units) != len(set(covered_score_units))
        ):
            duplicate_score_units = sorted(
                unit_id
                for unit_id in set(covered_score_units)
                if covered_score_units.count(unit_id) > 1
            )
            raise PlanningCandidateCompilationError(
                "Topic/Duty 未将每个 ScoreResponseUnit 精确绑定一次；"
                f"missing={sorted(expected_score_units - set(covered_score_units))}, "
                f"duplicates={duplicate_score_units}"
            )

        topic_ids = {
            topic.local_id: _stable_planning_id(
                "T",
                topic.local_id,
                topic.canonical_name,
                topic.summary,
            )
            for topic in candidate.topics
        }
        topics: list[ResponseTopic] = []
        for topic in candidate.topics:
            attributes: dict[str, object] = {
                "upstream_refs": list(topic.upstream_refs),
                "candidate_local_id": topic.local_id,
            }
            if topic.score_point_ids:
                point = score_points[topic.score_point_ids[0]]
                group = score_groups[point.group_id]
                topic_unit_scopes = {
                    score_response_units[unit_id][1].response_scope
                    for duty in candidate.duties
                    if duty.topic_local_id == topic.local_id
                    for unit_id in duty.score_response_unit_ids
                }
                if topic_unit_scopes == {"document"}:
                    planning_role = "document_quality_gate"
                    response_scope = "document"
                elif "document" in topic_unit_scopes:
                    planning_role = "mixed_response_scope"
                    response_scope = "mixed"
                else:
                    planning_role = (
                        "document_quality_gate"
                        if point.response_scope == "document"
                        else "content_section"
                    )
                    response_scope = point.response_scope
                attributes.update(
                    {
                        "score_group_id": point.group_id,
                        "score_group_title": group.title,
                        "score_group_order": group_order[point.group_id],
                        "score_group_declared_points": group.declared_points,
                        "score_max_points": point.max_points,
                        "score_outline_path": point.outline_path,
                        "full_score_conditions": point.full_score_conditions,
                        "score_condition_ids": [
                            item.condition_id
                            for item in point.score_conditions
                        ],
                        "response_scope": response_scope,
                        "planning_role": planning_role,
                        "response_shape": (
                            "form/table"
                            if score_group_category(group.title) == "price"
                            or any(
                                token in point.title
                                for token in ("报价", "价格")
                            )
                            else "narrative"
                        ),
                    }
                )
            anchors = _anchors_for_refs(
                topic.upstream_refs,
                catalog,
                owner=f"ResponseTopicCandidate {topic.local_id}",
            )
            topics.append(
                ResponseTopic(
                    topic_id=topic_ids[topic.local_id],
                    parent_topic_id=(
                        topic_ids[topic.parent_local_id]
                        if topic.parent_local_id
                        else None
                    ),
                    topic_type=topic.topic_type,
                    canonical_name=topic.canonical_name,
                    intent=topic.intent,
                    summary=topic.summary,
                    aliases=topic.aliases,
                    attributes=attributes,
                    source_anchors=anchors,
                    confidence=topic.confidence,
                    review_status=topic.review_status,
                )
            )

        duty_ids = {
            duty.local_id: _stable_planning_id(
                "D",
                duty.local_id,
                duty.topic_local_id,
                duty.duty_type,
            )
            for duty in candidate.duties
        }
        duties = [
            ResponseDuty(
                duty_id=duty_ids[duty.local_id],
                topic_id=topic_ids[duty.topic_local_id],
                duty_type=duty.duty_type,
                requirement_ids=duty.requirement_ids,
                score_point_ids=duty.score_point_ids,
                score_response_unit_ids=duty.score_response_unit_ids,
                response_expectations=duty.response_expectations,
                evidence_need_ids=duty.evidence_need_ids,
                priority=duty.priority,
                confidence=duty.confidence,
                review_status=duty.review_status,
            )
            for duty in candidate.duties
        ]
        edges = [
            TopicEdge(
                edge_id=_stable_planning_id(
                    "E",
                    edge.local_id,
                    edge.source_topic_local_id,
                    edge.target_topic_local_id,
                    edge.relation,
                ),
                source_topic_id=topic_ids[edge.source_topic_local_id],
                target_topic_id=topic_ids[edge.target_topic_local_id],
                relation=edge.relation,
                order=edge.order,
                requirement_ids=edge.requirement_ids,
                rationale=edge.rationale,
                confidence=edge.confidence,
            )
            for edge in candidate.edges
        ]
        for edge, compiled in zip(candidate.edges, edges, strict=True):
            if unknown := set(edge.requirement_ids) - set(requirements):
                raise PlanningCandidateCompilationError(
                    f"Edge {edge.local_id} 引用未知 Requirement: {sorted(unknown)}"
                )
            if (
                compiled.relation == "depends_on"
                and compiled.source_topic_id == compiled.target_topic_id
            ):
                raise PlanningCandidateCompilationError(
                    f"Edge {edge.local_id} 形成自依赖"
                )

        source_hashes = _merged_source_hashes(
            ledger.source_hashes,
            scores.source_hashes,
            project.source_hashes,
        )
        candidate_hash = hashlib.sha256(
            candidate.model_dump_json().encode("utf-8")
        ).hexdigest()
        return ResponseTopicGraph(
            revision=revision,
            source_hashes=source_hashes,
            graph_id=_stable_planning_id(
                "TG",
                project.project_id,
                candidate_hash,
            ),
            requirement_ledger_revision=ledger.revision,
            score_model_revision=scores.revision,
            project_model_revision=project.revision,
            root_topic_ids=[
                topic_ids[local_id]
                for local_id in candidate.root_topic_local_ids
            ],
            topics=topics,
            duties=duties,
            edges=edges,
            review_status=candidate.review_status,
        )

    def topic_graph(
        self,
        ledger: RequirementLedger,
        scores: ScoreModel,
        project: ProjectModel,
        source_blocks: list[SourceBlock] | None = None,
        *,
        revision: int,
    ) -> ResponseTopicGraph:
        topics: list[ResponseTopic] = []
        duties: list[ResponseDuty] = []
        edges: list[TopicEdge] = []
        section_by_anchor = {
            (block.input_id, block.source_anchor.chunk_id): block.heading_path[0]
            for block in (source_blocks or [])
            if block.heading_path
        }
        need_ids_by_score = defaultdict(list)
        score_groups = {group.group_id: group for group in scores.groups}
        score_group_order = {group.group_id: order for order, group in enumerate(scores.groups)}
        active_requirement_ids = {
            item.requirement_id
            for item in ledger.requirements
            if item.status not in {"blocked", "waived"}
        }
        for need in project.evidence_needs:
            if need.topic_id.startswith("score:"):
                need_ids_by_score[need.topic_id.removeprefix("score:")].append(need.need_id)
        for index, requirement in enumerate(ledger.requirements):
            if requirement.status in {"blocked", "waived"}:
                continue
            topic_id = f"T-R-{requirement.requirement_id.removeprefix('R-')}"
            topic_type, duty_type = self._requirement_topic_type(requirement.kind, requirement.normalized_requirement)
            attributes = {"upstream_refs": [f"RequirementLedger:{requirement.requirement_id}"]}
            source_section = section_by_anchor.get((requirement.source_anchor.source_input_id, requirement.source_anchor.chunk_id))
            if source_section:
                attributes["source_section"] = source_section
            topics.append(ResponseTopic(topic_id=topic_id, topic_type=topic_type, canonical_name=requirement.normalized_requirement[:80], intent="响应采购义务", summary=requirement.normalized_requirement, attributes=attributes, source_anchors=[requirement.source_anchor], confidence=1.0, review_status="confirmed"))
            duties.append(ResponseDuty(duty_id=f"D-R-{requirement.requirement_id.removeprefix('R-')}", topic_id=topic_id, duty_type=duty_type, requirement_ids=[requirement.requirement_id], response_expectations=[requirement.response_type], priority="blocking" if requirement.severity == "blocking" else "normal", confidence=1.0, review_status="confirmed"))
        for index, point in enumerate(scores.points):
            topic_id = f"T-S-{point.score_point_id.removeprefix('SP-')}"
            score_group = score_groups.get(point.group_id)
            score_group_title = score_group.title if score_group is not None else point.group_id
            score_group_kind = score_group_category(score_group_title)
            score_attributes = {
                "upstream_refs": [f"ScoreModel:{point.score_point_id}"],
                "score_group_id": point.group_id,
                "score_group_title": score_group_title,
                "score_group_order": score_group_order.get(point.group_id, len(score_groups)),
                "score_group_declared_points": score_group.declared_points if score_group is not None else None,
                "score_point_order": index,
                "score_max_points": point.max_points,
                "score_outline_path": point.outline_path,
                "full_score_conditions": point.full_score_conditions,
                "response_scope": point.response_scope,
                "planning_role": (
                    "document_quality_gate"
                    if point.response_scope == "document"
                    or is_document_quality_score(point.title, point.criterion)
                    else "content_section"
                ),
                "response_shape": (
                    "form/table"
                    if score_group_kind == "price" or any(token in point.title for token in ("报价", "价格"))
                    else "narrative"
                ),
            }
            topics.append(ResponseTopic(topic_id=topic_id, topic_type=self._score_topic_type(point.title), canonical_name=point.title, intent="响应评分逻辑", summary=point.criterion, attributes=score_attributes, source_anchors=point.source_anchors, confidence=point.confidence, review_status=point.review_status))
            response_units = list(point.response_units) or [None]
            for unit_index, response_unit in enumerate(
                response_units,
                start=1,
            ):
                duties.append(
                    ResponseDuty(
                        duty_id=(
                            f"D-S-{point.score_point_id.removeprefix('SP-')}"
                            + (
                                f"-U{unit_index:02d}"
                                if response_unit is not None
                                else ""
                            )
                        ),
                        topic_id=topic_id,
                        duty_type=(
                            "verify" if point.disqualifying else "explain"
                        ),
                        requirement_ids=[
                            requirement_id
                            for requirement_id in point.linked_requirement_ids
                            if requirement_id in active_requirement_ids
                        ],
                        score_point_ids=[point.score_point_id],
                        score_response_unit_ids=(
                            [response_unit.unit_id]
                            if response_unit is not None
                            else []
                        ),
                        response_expectations=[
                            response_unit.response_expectation
                            if response_unit is not None
                            else point.response_expectation
                        ],
                        evidence_need_ids=need_ids_by_score[
                            point.score_point_id
                        ],
                        priority=(
                            "blocking"
                            if point.disqualifying
                            else (
                                "high"
                                if point.max_points
                                and point.max_points >= 10
                                else "normal"
                            )
                        ),
                        confidence=(
                            response_unit.confidence
                            if response_unit is not None
                            else point.confidence
                        ),
                        review_status=(
                            response_unit.review_status
                            if response_unit is not None
                            else point.review_status
                        ),
                    )
                )
            for requirement_id in point.linked_requirement_ids:
                if requirement_id not in active_requirement_ids:
                    continue
                requirement_topic = f"T-R-{requirement_id.removeprefix('R-')}"
                edges.append(TopicEdge(edge_id=f"E-{point.score_point_id}-{requirement_id}", source_topic_id=topic_id, target_topic_id=requirement_topic, relation="supports_score", order=index, requirement_ids=[requirement_id], rationale="评分点通过 Requirement ID 引用采购义务", confidence=1.0))
        return ResponseTopicGraph(revision=revision, source_hashes={**ledger.source_hashes, **scores.source_hashes}, graph_id=f"TG-{hashlib.sha256((project.project_id + str(revision)).encode()).hexdigest()[:12]}", requirement_ledger_revision=ledger.revision, score_model_revision=scores.revision, project_model_revision=project.revision, root_topic_ids=[topic.topic_id for topic in topics], topics=topics, duties=duties, edges=edges)

    def proposal(
        self,
        artifact_kind: str,
        payload: ProjectModel | ResponseTopicGraph | ChapterBlueprint,
        *,
        base_revision: int,
        operation_id: str,
        upstream_revisions: tuple[int, ...] = (),
        prompt_version: str | None = None,
        model_fingerprint: str | None = None,
        inference_receipt_refs: list[InferenceReceiptRef] | None = None,
    ) -> ProposalEnvelope:
        from .artifact_registry import ARTIFACT_REGISTRY

        source_ids = sorted(payload.source_hashes)
        prompt_version = prompt_version or "v3_planning_agent_v1.4"
        model_fingerprint = model_fingerprint or (
            f"deterministic_v3_agent_score_driven_outline_v4:{SCORING_OUTLINE_POLICY_VERSION}"
        )
        registration = ARTIFACT_REGISTRY.get(artifact_kind)
        store = ControlStore(self.context)
        resolved: dict = {}
        declared: list[DependencyRef] = []
        for kind in (
            *registration.dependency_kinds,
            *registration.optional_dependency_kinds,
        ):
            active = store.v3_active_artifact(kind)
            if active is None:
                continue
            resolved[kind] = {
                "artifact_kind": kind,
                "artifact_id": str(active["artifact_id"]),
                "revision": int(active["revision"]),
                "artifact_hash": str(active["artifact_hash"]),
            }
            declared.append(
                DependencyRef(
                    artifact_kind=kind,
                    expected_revision=int(active["revision"]),
                    expected_hash=str(active["artifact_hash"]),
                )
            )
        dep_fp = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=resolved,
            artifact_kind=artifact_kind,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )
        return ProposalEnvelope(
            workspace_id=self.context.workspace_id,
            artifact_kind=artifact_kind,
            producer_role="planning_agent",
            operation_id=operation_id,
            base_revision=base_revision,
            declared_dependencies=declared,
            dependency_fingerprint=dep_fp,
            payload=payload.model_dump(mode="json"),
            cited_source_ids=source_ids,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
            inference_receipt_refs=inference_receipt_refs or [],
        )

    def compile_outline_candidate(
        self,
        candidate: ChapterOutlineCandidate,
        ledger: RequirementLedger,
        scores: ScoreModel,
        *,
        revision: int,
        template_structure: TemplateStructureContract | None = None,
    ) -> ChapterBlueprint:
        """Compile a direct ScoreModel outline using stable catalog IDs only."""

        if candidate.review_status == "blocked":
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 已标记 blocked"
            )
        # Topology is owned by the registered planning.chapter_outline_split
        # Skill.  The compiler validates and canonicalizes its result but must
        # never rebuild a second, potentially divergent tree.
        source_hashes = _merged_source_hashes(
            ledger.source_hashes,
            scores.source_hashes,
        )

        points: dict[str, object] = {}
        units: dict[str, object] = {}
        unit_owner: dict[str, str] = {}
        unit_order: list[str] = []
        conditions: dict[str, object] = {}
        condition_owner_point: dict[str, str] = {}
        condition_owner_unit: dict[str, str] = {}
        condition_unit_counts: dict[str, int] = defaultdict(int)
        for point in scores.points:
            if point.score_point_id in points:
                raise PlanningCandidateCompilationError(
                    "ScoreModel score_point_id 非全局唯一: "
                    f"{point.score_point_id}"
                )
            points[point.score_point_id] = point
            for condition in point.score_conditions:
                if condition.condition_id in conditions:
                    raise PlanningCandidateCompilationError(
                        "ScoreModel condition_id 非全局唯一: "
                        f"{condition.condition_id}"
                    )
                conditions[condition.condition_id] = condition
                condition_owner_point[
                    condition.condition_id
                ] = point.score_point_id
            for unit in point.response_units:
                if unit.unit_id in units:
                    raise PlanningCandidateCompilationError(
                        "ScoreModel response unit ID 非全局唯一: "
                        f"{unit.unit_id}"
                    )
                units[unit.unit_id] = unit
                unit_owner[unit.unit_id] = point.score_point_id
                unit_order.append(unit.unit_id)
                for condition_id in unit.condition_ids:
                    condition_unit_counts[condition_id] += 1
                    condition_owner_unit[condition_id] = unit.unit_id

        active_point_ids = {
            point.score_point_id
            for point in scores.points
            if point.review_status != "blocked"
        }
        active_unit_ids = {
            unit_id
            for unit_id, point_id in unit_owner.items()
            if point_id in active_point_ids
            and getattr(units[unit_id], "review_status") != "blocked"
        }
        section_unit_ids = {
            unit_id
            for unit_id in active_unit_ids
            if getattr(units[unit_id], "response_scope") == "section"
        }
        document_unit_ids = {
            unit_id
            for unit_id in active_unit_ids
            if getattr(units[unit_id], "response_scope") == "document"
        }
        units_by_point: dict[str, set[str]] = defaultdict(set)
        for unit_id in active_unit_ids:
            units_by_point[unit_owner[unit_id]].add(unit_id)
            for condition_id in getattr(units[unit_id], "condition_ids"):
                if condition_id not in conditions:
                    raise PlanningCandidateCompilationError(
                        f"ScoreResponseUnit {unit_id} 引用未知 condition_id "
                        f"{condition_id}"
                    )
                if condition_owner_point[condition_id] != unit_owner[unit_id]:
                    raise PlanningCandidateCompilationError(
                        f"ScoreCondition {condition_id} 与其 "
                        f"ScoreResponseUnit {unit_id} 不属于同一 ScorePoint"
                    )
        if missing_units := active_point_ids - set(units_by_point):
            raise PlanningCandidateCompilationError(
                "活动 ScorePoint 缺少活动 ScoreResponseUnit: "
                f"{sorted(missing_units)}"
            )

        active_condition_ids = {
            condition_id
            for condition_id, condition in conditions.items()
            if getattr(condition, "review_status") != "blocked"
            and condition_owner_point[condition_id] in active_point_ids
        }
        invalid_condition_cardinality = {
            condition_id: condition_unit_counts.get(condition_id, 0)
            for condition_id in active_condition_ids
            if condition_unit_counts.get(condition_id, 0) != 1
        }
        if invalid_condition_cardinality:
            raise PlanningCandidateCompilationError(
                "每个活动 ScoreCondition 必须由且仅由一个 "
                "ScoreResponseUnit 绑定: "
                f"{invalid_condition_cardinality}"
            )
        visible_condition_ids: set[str] = set()
        document_condition_ids: set[str] = set()
        for condition_id in active_condition_ids:
            condition = conditions[condition_id]
            role = getattr(condition, "condition_role", "content")
            unit_id = condition_owner_unit[condition_id]
            if role not in {
                "content",
                "evidence",
                "constraint",
                "quality",
                "document",
            }:
                raise PlanningCandidateCompilationError(
                    f"ScoreCondition {condition_id} condition_role 非法: "
                    f"{role}"
                )
            if role == "document" and unit_id not in document_unit_ids:
                raise PlanningCandidateCompilationError(
                    f"document condition {condition_id} 必须属于 document "
                    "ScoreResponseUnit"
                )
            if unit_id in document_unit_ids or role == "document":
                document_condition_ids.add(condition_id)
            else:
                visible_condition_ids.add(condition_id)

        requirements = {
            requirement.requirement_id: requirement
            for requirement in ledger.requirements
        }
        active_requirement_ids = {
            requirement_id
            for requirement_id, requirement in requirements.items()
            if requirement.status not in {"blocked", "waived"}
        }
        linked_requirement_ids: set[str] = set()
        linked_requirements_by_unit: dict[str, set[str]] = {}
        for unit_id in unit_order:
            if unit_id not in active_unit_ids:
                continue
            unit = units[unit_id]
            linked = {
                str(requirement_id)
                for requirement_id in getattr(
                    unit,
                    "linked_requirement_ids",
                    [],
                )
            }
            linked_requirements_by_unit[unit_id] = linked
            linked_requirement_ids.update(linked)
        if unknown_links := linked_requirement_ids - set(requirements):
            raise PlanningCandidateCompilationError(
                "ScoreModel 引用未知 Requirement: "
                f"{sorted(unknown_links)}"
            )
        section_required_requirement_ids = {
            requirement_id
            for unit_id in section_unit_ids
            for requirement_id in linked_requirements_by_unit.get(
                unit_id,
                set(),
            )
            if requirement_id in active_requirement_ids
        }
        document_required_requirement_ids = {
            requirement_id
            for unit_id in document_unit_ids
            for requirement_id in linked_requirements_by_unit.get(
                unit_id,
                set(),
            )
            if requirement_id in active_requirement_ids
        }
        required_requirement_ids = (
            section_required_requirement_ids
            | document_required_requirement_ids
        )

        orders = [node.order for node in candidate.nodes]
        if len(orders) != len(set(orders)):
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 的 order 必须全局唯一"
            )
        ordered_nodes = sorted(candidate.nodes, key=lambda item: item.order)
        local_nodes = {node.local_id: node for node in candidate.nodes}
        primary_node_by_unit: dict[str, str] = {}
        visible_condition_bindings: list[str] = []
        covered_requirement_ids: set[str] = set()
        for node in candidate.nodes:
            if (
                node.parent_local_id is not None
                and local_nodes[node.parent_local_id].order >= node.order
            ):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 必须排在其父章节之后"
                )
            for field_name in (
                "primary_response_unit_ids",
                "supporting_response_unit_ids",
                "score_condition_ids",
                "requirement_ids",
                "template_slot_ids",
            ):
                _require_unique(
                    list(getattr(node, field_name)),
                    owner=(
                        f"ChapterOutlineNodeCandidate "
                        f"{node.local_id}.{field_name}"
                    ),
                )
            if overlap := set(node.primary_response_unit_ids) & set(
                node.supporting_response_unit_ids
            ):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 同时 primary/supporting 绑定 "
                    f"ScoreResponseUnit: {sorted(overlap)}"
                )
            referenced_units = {
                *node.primary_response_unit_ids,
                *node.supporting_response_unit_ids,
            }
            if unknown := referenced_units - section_unit_ids:
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知、blocked 或全文级 "
                    f"ScoreResponseUnit: {sorted(unknown)}"
                )
            if unknown := set(node.score_condition_ids) - visible_condition_ids:
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知或全文级 condition_id: "
                    f"{sorted(unknown)}"
                )
            if unknown := set(node.requirement_ids) - active_requirement_ids:
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知或非活动 Requirement: "
                    f"{sorted(unknown)}"
                )
            covered_requirement_ids.update(node.requirement_ids)
            visible_condition_bindings.extend(node.score_condition_ids)
            for unit_id in node.primary_response_unit_ids:
                if unit_id in primary_node_by_unit:
                    raise PlanningCandidateCompilationError(
                        f"ScoreResponseUnit {unit_id} 出现多个 primary 章节"
                    )
                primary_node_by_unit[unit_id] = node.local_id
        if set(primary_node_by_unit) != section_unit_ids:
            raise PlanningCandidateCompilationError(
                "目录候选未让每个 section ScoreResponseUnit 恰好拥有一个 "
                "primary；missing="
                f"{sorted(section_unit_ids - set(primary_node_by_unit))}, "
                "extra="
                f"{sorted(set(primary_node_by_unit) - section_unit_ids)}"
            )
        _require_unique(
            candidate.document_quality_response_unit_ids,
            owner=(
                "ChapterOutlineCandidate."
                "document_quality_response_unit_ids"
            ),
        )
        if (
            set(candidate.document_quality_response_unit_ids)
            != document_unit_ids
        ):
            raise PlanningCandidateCompilationError(
                "全文质量 ScoreResponseUnit 识别不一致；"
                f"expected={sorted(document_unit_ids)}, "
                "actual="
                f"{sorted(candidate.document_quality_response_unit_ids)}"
            )
        _require_unique(
            visible_condition_bindings,
            owner="ChapterOutlineCandidate.score_condition_ids",
        )
        if set(visible_condition_bindings) != visible_condition_ids:
            raise PlanningCandidateCompilationError(
                "目录候选未精确覆盖可见评分条件；missing="
                f"{sorted(visible_condition_ids - set(visible_condition_bindings))}, "
                "extra="
                f"{sorted(set(visible_condition_bindings) - visible_condition_ids)}"
            )
        if missing_requirements := (
            section_required_requirement_ids - covered_requirement_ids
        ):
            raise PlanningCandidateCompilationError(
                "目录候选遗漏评分关联 Requirement: "
                f"{sorted(missing_requirements)}"
            )

        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for node in candidate.nodes:
            if node.parent_local_id is not None:
                children_by_parent[node.parent_local_id].append(node.local_id)

        def subtree_ids(root_id: str) -> set[str]:
            result: set[str] = set()
            pending = [root_id]
            while pending:
                node_id = pending.pop()
                if node_id in result:
                    continue
                result.add(node_id)
                pending.extend(children_by_parent.get(node_id, []))
            return result

        for unit_id in section_unit_ids:
            primary_node_id = primary_node_by_unit.get(unit_id)
            if primary_node_id is None:
                continue
            required_for_unit = (
                linked_requirements_by_unit.get(unit_id, set())
                & active_requirement_ids
            )
            covered_in_subtree = {
                requirement_id
                for node_id in subtree_ids(primary_node_id)
                for requirement_id in local_nodes[
                    node_id
                ].requirement_ids
            }
            if missing := required_for_unit - covered_in_subtree:
                raise PlanningCandidateCompilationError(
                    f"ScoreResponseUnit {unit_id} 的关联 Requirement "
                    "未进入其主责章节子树: "
                    f"{sorted(missing)}"
                )

        for condition_id in visible_condition_ids:
            unit_id = condition_owner_unit[condition_id]
            primary_node_id = primary_node_by_unit.get(unit_id)
            if primary_node_id is None:
                raise PlanningCandidateCompilationError(
                    f"condition_id {condition_id} 缺少 "
                    "ScoreResponseUnit/primary 链路"
                )
            subtree = subtree_ids(primary_node_id)
            covered_nodes = {
                node_id
                for node_id in subtree
                if condition_id
                in local_nodes[node_id].score_condition_ids
            }
            if not covered_nodes:
                raise PlanningCandidateCompilationError(
                    f"condition_id {condition_id} 未进入 "
                    f"ScoreResponseUnit {unit_id} 主责章节子树"
                )
            role = getattr(
                conditions[condition_id],
                "condition_role",
                "content",
            )
            sectionable_quality = is_sectionable_quality_condition(
                conditions[condition_id]
            )
            if (
                role == "quality"
                and not sectionable_quality
                and covered_nodes != {primary_node_id}
            ):
                raise PlanningCandidateCompilationError(
                    f"quality condition {condition_id} 必须绑定 Unit "
                    f"{unit_id} 的 primary 章节并转为写作要求，"
                    "不得单独生成空洞质量章节"
                )
        template_node_by_local: dict[str, ContractNode] = {}
        if template_structure is None:
            nodes_with_slots = [
                node.local_id
                for node in candidate.nodes
                if node.template_slot_ids
            ]
            if nodes_with_slots:
                raise PlanningCandidateCompilationError(
                    "auto_outline 模式不得声明 template_slot_ids: "
                    f"{nodes_with_slots}"
                )
        else:
            template_orders = [node.order for node in template_structure.nodes]
            if len(template_orders) != len(set(template_orders)):
                raise PlanningCandidateCompilationError(
                    "TemplateStructureContract 的 order 不唯一"
                )
            template_nodes = sorted(
                template_structure.nodes,
                key=lambda item: item.order,
            )
            if len(template_nodes) != len(ordered_nodes):
                raise PlanningCandidateCompilationError(
                    "严格模板模式的章节节点数量发生变化"
                )
            if [node.order for node in ordered_nodes] != [
                node.order for node in template_nodes
            ]:
                raise PlanningCandidateCompilationError(
                    "严格模板模式的章节顺序发生变化"
                )
            template_node_by_local = {
                candidate_node.local_id: template_node
                for candidate_node, template_node in zip(
                    ordered_nodes,
                    template_nodes,
                    strict=True,
                )
            }
            slots_by_node: dict[str, list[str]] = defaultdict(list)
            known_slot_ids: set[str] = set()
            for slot in template_structure.slots:
                slots_by_node[slot.node_id].append(slot.slot_id)
                known_slot_ids.add(slot.slot_id)
            for candidate_node, template_node in zip(
                ordered_nodes,
                template_nodes,
                strict=True,
            ):
                if candidate_node.title != template_node.title:
                    raise PlanningCandidateCompilationError(
                        f"严格模板标题发生变化: {template_node.title!r} -> "
                        f"{candidate_node.title!r}"
                    )
                expected_parent = (
                    template_node_by_local[
                        candidate_node.parent_local_id
                    ].node_id
                    if candidate_node.parent_local_id
                    else None
                )
                if expected_parent != template_node.parent_node_id:
                    raise PlanningCandidateCompilationError(
                        f"严格模板节点 {template_node.node_id} 的父子层级发生变化"
                    )
                if unknown := (
                    set(candidate_node.template_slot_ids) - known_slot_ids
                ):
                    raise PlanningCandidateCompilationError(
                        f"章节 {candidate_node.local_id} 引用未知模板 Slot: "
                        f"{sorted(unknown)}"
                    )
                if candidate_node.template_slot_ids != slots_by_node.get(
                    template_node.node_id,
                    [],
                ):
                    raise PlanningCandidateCompilationError(
                        f"严格模板节点 {template_node.node_id} 的 Slot 发生变化"
                    )

        chapter_ids = {
            node.local_id: (
                template_node_by_local[node.local_id].node_id
                if template_structure is not None
                else _stable_planning_id(
                    "chapter",
                    scores.model_id,
                    ledger.revision,
                    node.local_id,
                )
            )
            for node in candidate.nodes
        }
        root_nodes = [
            node for node in ordered_nodes if node.parent_local_id is None
        ]
        if not root_nodes:
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 必须至少有一个根章节"
            )
        quality_target_local_id = root_nodes[0].local_id
        normalized_order = {
            node.local_id: index
            for index, node in enumerate(ordered_nodes)
        }
        groups_by_id = {group.group_id: group for group in scores.groups}
        direct_policy_by_local: dict[str, tuple[str, str, str | None]] = {}
        for node in ordered_nodes:
            bound_unit_ids = [
                *node.primary_response_unit_ids,
                *node.supporting_response_unit_ids,
            ]
            score_categories = []
            for unit_id in bound_unit_ids:
                point = points[unit_owner[unit_id]]
                group = groups_by_id.get(getattr(point, "group_id", ""))
                score_categories.append(
                    score_group_category(
                        getattr(group, "title", "") if group else ""
                    )
                )
            requirement_kinds = [
                requirements[requirement_id].kind
                for requirement_id in node.requirement_ids
                if requirement_id in requirements
            ]
            direct_policy_by_local[node.local_id] = _domain_policy(
                title=node.title,
                purpose=node.purpose,
                score_categories=score_categories,
                requirement_kinds=requirement_kinds,
            )
        policy_by_local: dict[str, tuple[str, str, str | None]] = {}
        for node in ordered_nodes:
            domain, policy, reason = direct_policy_by_local[node.local_id]
            if (
                node.parent_local_id
                and node.parent_local_id in policy_by_local
                and policy_by_local[node.parent_local_id][0]
                in {"price", "commercial"}
                and domain != "technical"
            ):
                domain, policy, reason = policy_by_local[
                    node.parent_local_id
                ]
            policy_by_local[node.local_id] = (domain, policy, reason)
        blueprint_nodes: list[BlueprintNode] = []
        for node in ordered_nodes:
            bound_unit_ids = [
                *node.primary_response_unit_ids,
                *node.supporting_response_unit_ids,
            ]
            score_point_ids = list(
                dict.fromkeys(
                    unit_owner[unit_id]
                    for unit_id in bound_unit_ids
                )
            )
            writing_objectives = list(node.writing_objectives)
            for unit_id in node.primary_response_unit_ids:
                for condition_id in getattr(
                    units[unit_id],
                    "condition_ids",
                ):
                    condition = conditions[condition_id]
                    if (
                        getattr(condition, "condition_role", "content")
                        != "quality"
                    ):
                        continue
                    objective = str(
                        getattr(condition, "response_intent", "")
                        or getattr(
                            condition,
                            "normalized_condition",
                            "",
                        )
                        or getattr(condition, "text")
                    )
                    if objective not in writing_objectives:
                        writing_objectives.append(objective)
            required_mentions = list(node.required_mentions)
            for item in (
                *score_point_ids,
                *node.requirement_ids,
            ):
                if item not in required_mentions:
                    required_mentions.append(item)
            if (
                node.local_id == quality_target_local_id
                and document_unit_ids
            ):
                for item in (
                    "document_quality_gate",
                    *sorted(
                        {
                            unit_owner[unit_id]
                            for unit_id in document_unit_ids
                        }
                    ),
                ):
                    if item not in required_mentions:
                        required_mentions.append(item)
            template_node = (
                template_node_by_local[node.local_id]
                if template_structure is not None
                else None
            )
            blueprint_nodes.append(
                BlueprintNode(
                    chapter_id=chapter_ids[node.local_id],
                    parent_chapter_id=(
                        chapter_ids[node.parent_local_id]
                        if node.parent_local_id
                        else None
                    ),
                    order=(
                        template_node.order
                        if template_node is not None
                        else normalized_order[node.local_id]
                    ),
                    title=node.title,
                    purpose=node.purpose,
                    writing_objectives=writing_objectives,
                    primary_response_unit_ids=list(
                        node.primary_response_unit_ids
                    ),
                    supporting_response_unit_ids=list(
                        node.supporting_response_unit_ids
                    ),
                    score_point_ids=score_point_ids,
                    score_condition_ids=list(node.score_condition_ids),
                    requirement_ids=list(node.requirement_ids),
                    required_mentions=required_mentions,
                    planned_tables=node.planned_tables,
                    planned_figures=node.planned_figures,
                    target_size=node.target_size,
                    section_domain=policy_by_local[node.local_id][0],
                    content_policy=policy_by_local[node.local_id][1],
                    deferred_reason=policy_by_local[node.local_id][2],
                    template_node_id=(
                        template_node.node_id
                        if template_node is not None
                        else None
                    ),
                    template_level=(
                        template_node.level
                        if template_node is not None
                        else None
                    ),
                    template_numbering=(
                        template_node.numbering
                        if template_node is not None
                        else None
                    ),
                    template_slot_ids=list(node.template_slot_ids),
                    template_target=(
                        template_node.writable_target
                        if template_node is not None
                        else None
                    ),
                )
            )

        document_quality_gates: list[DocumentQualityGate] = []
        for unit_id in sorted(document_unit_ids):
            unit = units[unit_id]
            point_id = unit_owner[unit_id]
            point = points[point_id]
            condition_ids = sorted(
                condition_id
                for condition_id in getattr(unit, "condition_ids")
                if condition_id in active_condition_ids
            )
            criteria = document_quality_criteria(
                unit=unit,
                point=point,
                conditions=conditions,
                condition_ids=condition_ids,
            )
            gate_requirement_ids = sorted(
                linked_requirements_by_unit.get(unit_id, set())
                & active_requirement_ids
            )
            document_quality_gates.append(
                DocumentQualityGate(
                    gate_id=_stable_planning_id("DQG", unit_id),
                    response_unit_ids=[unit_id],
                    score_point_ids=[point_id],
                    score_condition_ids=condition_ids,
                    requirement_ids=gate_requirement_ids,
                    criteria=criteria,
                    check_items=document_quality_check_items(
                        " ".join(criteria)
                    ),
                )
            )

        candidate_hash = hashlib.sha256(
            candidate.model_dump_json().encode("utf-8")
        ).hexdigest()
        gate_covered_requirement_ids = {
            requirement_id
            for gate in document_quality_gates
            for requirement_id in gate.requirement_ids
        }
        all_covered_requirement_ids = (
            covered_requirement_ids | gate_covered_requirement_ids
        )
        return ChapterBlueprint(
            revision=revision,
            source_hashes=source_hashes,
            blueprint_id=_stable_planning_id(
                "BP",
                scores.model_id,
                ledger.revision,
                scores.revision,
                candidate_hash,
                (
                    template_structure.structural_fingerprint
                    if template_structure is not None
                    else "auto_outline"
                ),
            ),
            mode=(
                "template_strict"
                if template_structure is not None
                else "auto_outline"
            ),
            planning_model="score_direct",
            requirement_ledger_revision=ledger.revision,
            score_model_revision=scores.revision,
            template_structure_revision=(
                template_structure.revision
                if template_structure is not None
                else None
            ),
            nodes=blueprint_nodes,
            assignments=[],
            document_quality_gates=document_quality_gates,
            coverage_summary={
                "response_unit_count": len(active_unit_ids),
                "section_response_unit_count": len(section_unit_ids),
                "primary_response_unit_count": len(
                    primary_node_by_unit
                ),
                "document_response_unit_count": len(document_unit_ids),
                "score_point_count": len(active_point_ids),
                "score_condition_count": len(active_condition_ids),
                "visible_score_condition_count": len(
                    visible_condition_ids
                ),
                "document_score_condition_count": len(
                    document_condition_ids
                ),
                "required_requirement_count": len(
                    required_requirement_ids
                ),
                "covered_required_requirement_count": len(
                    required_requirement_ids & all_covered_requirement_ids
                ),
                "document_quality_gate_count": len(
                    document_quality_gates
                ),
                "score_group_points": {
                    group.group_id: group.declared_points
                    for group in scores.groups
                    if group.declared_points is not None
                },
                "uncovered_response_unit_ids": sorted(
                    section_unit_ids - set(primary_node_by_unit)
                ),
                "uncovered_requirement_ids": sorted(
                    required_requirement_ids - all_covered_requirement_ids
                ),
            },
            review_status="draft",
        )

    def _compile_outline_candidate_legacy(
        self,
        candidate: ChapterOutlineCandidate,
        graph: ResponseTopicGraph,
        scores: ScoreModel,
        *,
        revision: int,
        template_structure: TemplateStructureContract | None = None,
    ) -> ChapterBlueprint:
        """Compile the model-authored outline without rewriting its structure."""

        if candidate.review_status == "blocked":
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 已标记 blocked"
            )
        if graph.score_model_revision != scores.revision:
            raise PlanningCandidateCompilationError(
                "ResponseTopicGraph 与当前 ScoreModel revision 不一致"
            )
        for source_id, source_hash in scores.source_hashes.items():
            if graph.source_hashes.get(source_id) != source_hash:
                raise PlanningCandidateCompilationError(
                    f"ResponseTopicGraph 未绑定当前评分来源 {source_id}"
                )

        duties = {duty.duty_id: duty for duty in graph.duties}
        topics = {topic.topic_id: topic for topic in graph.topics}
        score_points = {
            point.score_point_id: point for point in scores.points
        }
        condition_owner: dict[str, str] = {}
        condition_owner_unit: dict[str, str] = {}
        score_unit_owner: dict[str, str] = {}
        score_units: dict[str, object] = {}
        conditions: dict[str, object] = {}
        for point in scores.points:
            for unit in point.response_units:
                score_unit_owner[unit.unit_id] = point.score_point_id
                score_units[unit.unit_id] = unit
                for condition_id in unit.condition_ids:
                    if condition_id in condition_owner_unit:
                        raise PlanningCandidateCompilationError(
                            f"condition_id 被多个 ScoreResponseUnit 绑定: "
                            f"{condition_id}"
                        )
                    condition_owner_unit[condition_id] = unit.unit_id
            for condition in point.score_conditions:
                if condition.condition_id in condition_owner:
                    raise PlanningCandidateCompilationError(
                        f"ScoreModel condition_id 重复: {condition.condition_id}"
                    )
                condition_owner[condition.condition_id] = point.score_point_id
                conditions[condition.condition_id] = condition
        duty_by_score_unit: dict[str, str] = {}
        for duty in graph.duties:
            for unit_id in duty.score_response_unit_ids:
                if unit_id not in score_unit_owner:
                    raise PlanningCandidateCompilationError(
                        f"Duty {duty.duty_id} 引用未知 ScoreResponseUnit "
                        f"{unit_id}"
                    )
                if score_unit_owner[unit_id] not in duty.score_point_ids:
                    raise PlanningCandidateCompilationError(
                        f"Duty {duty.duty_id} 的 ScoreResponseUnit {unit_id} "
                        "与 ScorePoint 绑定不一致"
                    )
                if unit_id in duty_by_score_unit:
                    raise PlanningCandidateCompilationError(
                        f"ScoreResponseUnit {unit_id} 被多个 Duty 绑定"
                    )
                duty_by_score_unit[unit_id] = duty.duty_id
        if set(duty_by_score_unit) != set(score_unit_owner):
            raise PlanningCandidateCompilationError(
                "ResponseTopicGraph 未精确覆盖全部 ScoreResponseUnit"
            )

        orders = [node.order for node in candidate.nodes]
        if len(orders) != len(set(orders)):
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 的 order 必须全局唯一"
            )
        ordered_nodes = sorted(candidate.nodes, key=lambda item: item.order)
        local_nodes = {node.local_id: node for node in candidate.nodes}
        for node in candidate.nodes:
            if (
                node.parent_local_id is not None
                and local_nodes[node.parent_local_id].order >= node.order
            ):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 必须排在其父章节之后"
                )
            _require_unique(
                node.primary_duty_ids,
                owner=f"ChapterOutlineNodeCandidate {node.local_id}.primary_duty_ids",
            )
            _require_unique(
                node.supporting_duty_ids,
                owner=f"ChapterOutlineNodeCandidate {node.local_id}.supporting_duty_ids",
            )
            _require_unique(
                node.score_point_ids,
                owner=f"ChapterOutlineNodeCandidate {node.local_id}.score_point_ids",
            )
            _require_unique(
                node.score_condition_ids,
                owner=f"ChapterOutlineNodeCandidate {node.local_id}.score_condition_ids",
            )
            _require_unique(
                node.template_slot_ids,
                owner=f"ChapterOutlineNodeCandidate {node.local_id}.template_slot_ids",
            )
            if overlap := set(node.primary_duty_ids) & set(
                node.supporting_duty_ids
            ):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 同时 primary/supporting 绑定 Duty: "
                    f"{sorted(overlap)}"
                )
            if unknown := (
                set(node.primary_duty_ids)
                | set(node.supporting_duty_ids)
            ) - set(duties):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知 Duty: {sorted(unknown)}"
                )
            if unknown := set(node.score_point_ids) - set(score_points):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知 ScorePoint: {sorted(unknown)}"
                )
            if unknown := set(node.score_condition_ids) - set(conditions):
                raise PlanningCandidateCompilationError(
                    f"章节 {node.local_id} 引用未知 condition_id: {sorted(unknown)}"
                )

        expected_quality_duties: set[str] = set()
        for duty in graph.duties:
            topic = topics[duty.topic_id]
            quality_by_topic = (
                not duty.score_response_unit_ids
                and topic.attributes.get("planning_role")
                == "document_quality_gate"
            )
            quality_by_score = any(
                getattr(score_units[unit_id], "response_scope")
                == "document"
                for unit_id in duty.score_response_unit_ids
                if unit_id in score_units
            ) or (
                not duty.score_response_unit_ids
                and any(
                    score_points[score_point_id].response_scope
                    == "document"
                    for score_point_id in duty.score_point_ids
                )
            )
            if quality_by_topic or quality_by_score:
                expected_quality_duties.add(duty.duty_id)
        _require_unique(
            candidate.document_quality_duty_ids,
            owner="ChapterOutlineCandidate.document_quality_duty_ids",
        )
        if set(candidate.document_quality_duty_ids) != expected_quality_duties:
            raise PlanningCandidateCompilationError(
                "全文质量 Duty 识别不一致；"
                f"expected={sorted(expected_quality_duties)}, "
                f"actual={sorted(candidate.document_quality_duty_ids)}"
            )

        visible_bound_duties = {
            duty_id
            for node in candidate.nodes
            for duty_id in (
                *node.primary_duty_ids,
                *node.supporting_duty_ids,
            )
        }
        if visible_quality := expected_quality_duties & visible_bound_duties:
            raise PlanningCandidateCompilationError(
                "全文质量 Duty 不得绑定可见章节: "
                f"{sorted(visible_quality)}"
            )
        quality_score_ids = {
            score_point_id
            for duty_id in expected_quality_duties
            for score_point_id in duties[duty_id].score_point_ids
        }
        quality_unit_ids = {
            unit_id
            for duty_id in expected_quality_duties
            for unit_id in duties[duty_id].score_response_unit_ids
        }
        quality_condition_ids = {
            condition_id
            for unit_id in quality_unit_ids
            for condition_id in getattr(
                score_units[unit_id],
                "condition_ids",
            )
        }
        fallback_quality_score_ids = {
            score_point_id
            for duty_id in expected_quality_duties
            if not duties[duty_id].score_response_unit_ids
            for score_point_id in duties[duty_id].score_point_ids
        }
        quality_condition_ids.update(
            condition_id
            for condition_id, score_point_id in condition_owner.items()
            if score_point_id in fallback_quality_score_ids
        )
        visible_condition_ids = [
            condition_id
            for node in candidate.nodes
            for condition_id in node.score_condition_ids
        ]
        _require_unique(
            visible_condition_ids,
            owner="ChapterOutlineCandidate.score_condition_ids",
        )
        if visible_quality_conditions := quality_condition_ids & set(
            visible_condition_ids
        ):
            raise PlanningCandidateCompilationError(
                "全文质量 condition 不得生成可见章节绑定: "
                f"{sorted(visible_quality_conditions)}"
            )

        primary_node_by_duty: dict[str, str] = {}
        for node in candidate.nodes:
            for duty_id in node.primary_duty_ids:
                if duty_id in primary_node_by_duty:
                    raise PlanningCandidateCompilationError(
                        f"Duty {duty_id} 出现多个 primary 章节"
                    )
                primary_node_by_duty[duty_id] = node.local_id
                missing_score_mentions = set(
                    duties[duty_id].score_point_ids
                ) - set(node.score_point_ids)
                if missing_score_mentions:
                    raise PlanningCandidateCompilationError(
                        f"主责章节 {node.local_id} 未声明 Duty {duty_id} 的 "
                        f"ScorePoint: {sorted(missing_score_mentions)}"
                    )

        core_non_quality_duties = {
            duty.duty_id
            for duty in graph.duties
            if duty.review_status != "blocked"
            and duty.duty_id not in expected_quality_duties
        }
        if set(primary_node_by_duty) & expected_quality_duties:
            raise PlanningCandidateCompilationError(
                "全文质量 Duty 不得由目录候选创建 primary 章节"
            )
        if missing := core_non_quality_duties - set(primary_node_by_duty):
            raise PlanningCandidateCompilationError(
                f"目录候选遗漏核心 Duty 的 primary 章节: {sorted(missing)}"
            )

        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for node in candidate.nodes:
            if node.parent_local_id is not None:
                children_by_parent[node.parent_local_id].append(node.local_id)

        def subtree_ids(root_id: str) -> set[str]:
            result: set[str] = set()
            pending = [root_id]
            while pending:
                node_id = pending.pop()
                if node_id in result:
                    continue
                result.add(node_id)
                pending.extend(children_by_parent.get(node_id, []))
            return result

        expected_visible_conditions = (
            set(condition_owner) - quality_condition_ids
        )
        if set(visible_condition_ids) != expected_visible_conditions:
            raise PlanningCandidateCompilationError(
                "目录候选未精确覆盖可见满分条件；"
                f"missing={sorted(expected_visible_conditions - set(visible_condition_ids))}, "
                f"extra={sorted(set(visible_condition_ids) - expected_visible_conditions)}"
            )
        for condition_id, score_point_id in condition_owner.items():
            if condition_id in quality_condition_ids:
                continue
            unit_id = condition_owner_unit.get(condition_id)
            duty_id = duty_by_score_unit.get(unit_id or "")
            primary_node_id = primary_node_by_duty.get(duty_id or "")
            if unit_id is None or duty_id is None or primary_node_id is None:
                raise PlanningCandidateCompilationError(
                    f"condition_id {condition_id} 缺少对应 "
                    "ScoreResponseUnit/Duty/primary 链路"
                )
            subtree = subtree_ids(primary_node_id)
            if not any(
                condition_id in local_nodes[node_id].score_condition_ids
                for node_id in subtree
            ):
                raise PlanningCandidateCompilationError(
                    f"condition_id {condition_id} 未进入 Duty {duty_id} "
                    "主责章节子树"
                )
        for node in candidate.nodes:
            bound_score_ids = set(node.score_point_ids)
            for duty_id in (
                *node.primary_duty_ids,
                *node.supporting_duty_ids,
            ):
                bound_score_ids.update(duties[duty_id].score_point_ids)
            for condition_id in node.score_condition_ids:
                if condition_owner[condition_id] not in bound_score_ids:
                    raise PlanningCandidateCompilationError(
                        f"章节 {node.local_id} 的 condition_id {condition_id} "
                        "与该章节绑定的 ScorePoint 不一致"
                    )

        template_node_by_local: dict[str, ContractNode] = {}
        if template_structure is None:
            nodes_with_slots = [
                node.local_id
                for node in candidate.nodes
                if node.template_slot_ids
            ]
            if nodes_with_slots:
                raise PlanningCandidateCompilationError(
                    "auto_outline 模式不得声明 template_slot_ids: "
                    f"{nodes_with_slots}"
                )
        else:
            template_orders = [node.order for node in template_structure.nodes]
            if len(template_orders) != len(set(template_orders)):
                raise PlanningCandidateCompilationError(
                    "TemplateStructureContract 的 order 不唯一"
                )
            template_nodes = sorted(
                template_structure.nodes,
                key=lambda item: item.order,
            )
            if len(template_nodes) != len(ordered_nodes):
                raise PlanningCandidateCompilationError(
                    "严格模板模式的章节节点数量发生变化"
                )
            if [node.order for node in ordered_nodes] != [
                node.order for node in template_nodes
            ]:
                raise PlanningCandidateCompilationError(
                    "严格模板模式的章节顺序发生变化"
                )
            template_node_by_local = {
                candidate_node.local_id: template_node
                for candidate_node, template_node in zip(
                    ordered_nodes,
                    template_nodes,
                    strict=True,
                )
            }
            slots_by_node: dict[str, list[str]] = defaultdict(list)
            known_slot_ids: set[str] = set()
            for slot in template_structure.slots:
                slots_by_node[slot.node_id].append(slot.slot_id)
                known_slot_ids.add(slot.slot_id)
            for candidate_node, template_node in zip(
                ordered_nodes,
                template_nodes,
                strict=True,
            ):
                if candidate_node.title != template_node.title:
                    raise PlanningCandidateCompilationError(
                        f"严格模板标题发生变化: {template_node.title!r} -> "
                        f"{candidate_node.title!r}"
                    )
                expected_parent = (
                    template_node_by_local[
                        candidate_node.parent_local_id
                    ].node_id
                    if candidate_node.parent_local_id
                    else None
                )
                if expected_parent != template_node.parent_node_id:
                    raise PlanningCandidateCompilationError(
                        f"严格模板节点 {template_node.node_id} 的父子层级发生变化"
                    )
                if unknown := set(candidate_node.template_slot_ids) - known_slot_ids:
                    raise PlanningCandidateCompilationError(
                        f"章节 {candidate_node.local_id} 引用未知模板 Slot: "
                        f"{sorted(unknown)}"
                    )
                expected_slots = slots_by_node.get(
                    template_node.node_id,
                    [],
                )
                if candidate_node.template_slot_ids != expected_slots:
                    raise PlanningCandidateCompilationError(
                        f"严格模板节点 {template_node.node_id} 的 Slot 发生变化"
                    )

        chapter_ids = {
            node.local_id: (
                template_node_by_local[node.local_id].node_id
                if template_structure is not None
                else _stable_planning_id(
                    "chapter",
                    graph.graph_id,
                    node.local_id,
                )
            )
            for node in candidate.nodes
        }
        root_nodes = [
            node for node in ordered_nodes if node.parent_local_id is None
        ]
        if not root_nodes:
            raise PlanningCandidateCompilationError(
                "ChapterOutlineCandidate 必须至少有一个根章节"
            )
        quality_target_local_id = root_nodes[0].local_id

        blueprint_nodes: list[BlueprintNode] = []
        normalized_order = {
            node.local_id: index for index, node in enumerate(ordered_nodes)
        }
        for node in ordered_nodes:
            required_mentions = list(node.required_mentions)
            metadata_score_ids = list(node.score_point_ids)
            for duty_id in node.primary_duty_ids:
                metadata_score_ids.extend(duties[duty_id].score_point_ids)
            for score_point_id in metadata_score_ids:
                if score_point_id not in required_mentions:
                    required_mentions.append(score_point_id)
            if (
                node.local_id == quality_target_local_id
                and expected_quality_duties
            ):
                for item in (
                    "document_quality_gate",
                    *sorted(quality_score_ids),
                ):
                    if item not in required_mentions:
                        required_mentions.append(item)
            template_node = (
                template_node_by_local[node.local_id]
                if template_structure is not None
                else None
            )
            blueprint_nodes.append(
                BlueprintNode(
                    chapter_id=chapter_ids[node.local_id],
                    parent_chapter_id=(
                        chapter_ids[node.parent_local_id]
                        if node.parent_local_id
                        else None
                    ),
                    order=(
                        template_node_by_local[node.local_id].order
                        if template_structure is not None
                        else normalized_order[node.local_id]
                    ),
                    title=node.title,
                    purpose=node.purpose,
                    writing_objectives=node.writing_objectives,
                    score_condition_ids=node.score_condition_ids,
                    required_mentions=required_mentions,
                    planned_tables=node.planned_tables,
                    planned_figures=node.planned_figures,
                    target_size=node.target_size,
                    template_node_id=(
                        template_node.node_id if template_node is not None else None
                    ),
                    template_level=(
                        template_node.level if template_node is not None else None
                    ),
                    template_numbering=(
                        template_node.numbering if template_node is not None else None
                    ),
                    template_slot_ids=list(node.template_slot_ids),
                    template_target=(
                        template_node.writable_target
                        if template_node is not None
                        else None
                    ),
                )
            )

        assignments: list[TopicChapterAssignment] = []
        for node in ordered_nodes:
            chapter_id = chapter_ids[node.local_id]
            for role, duty_ids_for_role in (
                ("primary", node.primary_duty_ids),
                ("supporting", node.supporting_duty_ids),
            ):
                for duty_id in duty_ids_for_role:
                    assignments.append(
                        TopicChapterAssignment(
                            assignment_id=_stable_planning_id(
                                "A",
                                duty_id,
                                chapter_id,
                                role,
                            ),
                            duty_id=duty_id,
                            chapter_id=chapter_id,
                            role=role,
                            response_scope=(
                                "完整主责响应"
                                if role == "primary"
                                else "支撑主责章节"
                            ),
                            rationale=(
                                "原样编译自受控章节拆分 Skill 的 Duty 绑定"
                            ),
                            confidence=min(
                                node.confidence,
                                duties[duty_id].confidence,
                            ),
                            needs_human=node.needs_human,
                        )
                    )

        document_quality_gates: list[DocumentQualityGate] = []
        for duty_id in sorted(expected_quality_duties):
            duty = duties[duty_id]
            topic = topics[duty.topic_id]
            duty_condition_ids = {
                condition_id
                for unit_id in duty.score_response_unit_ids
                for condition_id in getattr(
                    score_units[unit_id],
                    "condition_ids",
                )
            }
            if not duty.score_response_unit_ids:
                duty_condition_ids.update(
                    condition.condition_id
                    for score_point_id in duty.score_point_ids
                    for condition in score_points[
                        score_point_id
                    ].score_conditions
                )
            criteria: list[str] = []
            criteria.extend(
                str(getattr(conditions[condition_id], "text"))
                for condition_id in sorted(duty_condition_ids)
            )
            if not criteria:
                criteria.extend(
                    score_points[score_point_id].criterion
                    for score_point_id in duty.score_point_ids
                )
            criteria.extend([topic.summary, *duty.response_expectations])
            criteria = list(dict.fromkeys(item for item in criteria if item))
            document_quality_gates.append(
                DocumentQualityGate(
                    gate_id=_stable_planning_id("DQG", duty_id),
                    duty_id=duty_id,
                    score_point_ids=duty.score_point_ids,
                    score_condition_ids=sorted(duty_condition_ids),
                    criteria=criteria,
                    check_items=document_quality_check_items(
                        " ".join(criteria)
                    ),
                )
            )

        primary_duty_ids = {
            assignment.duty_id
            for assignment in assignments
            if assignment.role == "primary"
        }
        score_primary_chapter_ids = {
            assignment.chapter_id
            for assignment in assignments
            if assignment.role == "primary"
            and duties[assignment.duty_id].score_point_ids
        }
        coverage_summary = {
            "duty_count": len(duties),
            "primary_duty_count": len(primary_duty_ids),
            "score_point_count": len(score_points),
            "score_primary_chapter_count": len(
                score_primary_chapter_ids
            ),
            "score_condition_count": len(condition_owner),
            "visible_score_condition_count": len(
                visible_condition_ids
            ),
            "document_quality_gate_count": len(
                document_quality_gates
            ),
            "score_group_points": {
                group.group_id: group.declared_points
                for group in scores.groups
                if group.declared_points is not None
            },
            "uncovered_duty_ids": sorted(
                {
                    duty.duty_id
                    for duty in graph.duties
                    if duty.review_status != "blocked"
                }
                - primary_duty_ids
                - expected_quality_duties
            ),
        }
        candidate_hash = hashlib.sha256(
            candidate.model_dump_json().encode("utf-8")
        ).hexdigest()
        return ChapterBlueprint(
            revision=revision,
            source_hashes=graph.source_hashes,
            blueprint_id=_stable_planning_id(
                "BP",
                graph.graph_id,
                candidate_hash,
                (
                    template_structure.structural_fingerprint
                    if template_structure is not None
                    else "auto_outline"
                ),
            ),
            mode=(
                "template_strict"
                if template_structure is not None
                else "auto_outline"
            ),
            topic_graph_revision=graph.revision,
            template_structure_revision=(
                template_structure.revision
                if template_structure is not None
                else None
            ),
            nodes=blueprint_nodes,
            assignments=assignments,
            document_quality_gates=document_quality_gates,
            coverage_summary=coverage_summary,
            review_status="draft",
        )

    def chapter_blueprint(self, graph: ResponseTopicGraph, *, revision: int) -> ChapterBlueprint:
        duties = {duty.duty_id: duty for duty in graph.duties}
        topics = {topic.topic_id: topic for topic in graph.topics}
        nodes: list[BlueprintNode] = []
        assignments: list[TopicChapterAssignment] = []
        document_quality_gates: list[DocumentQualityGate] = []
        score_duties = [
            duty
            for duty in graph.duties
            if duty.score_point_ids or topics[duty.topic_id].attributes.get("score_group_id")
        ]
        requirement_duties = [duty for duty in graph.duties if duty not in score_duties]
        document_quality_duties = [
            duty
            for duty in score_duties
            if topics[duty.topic_id].attributes.get("planning_role") == "document_quality_gate"
        ]
        document_quality_items: list[str] = []
        for duty in document_quality_duties:
            topic = topics[duty.topic_id]
            criteria = [topic.summary, *duty.response_expectations]
            check_items = document_quality_check_items(" ".join(criteria))
            for item in check_items:
                if item not in document_quality_items:
                    document_quality_items.append(item)
            document_quality_gates.append(
                DocumentQualityGate(
                    gate_id=f"DQG-{duty.duty_id}",
                    duty_id=duty.duty_id,
                    score_point_ids=duty.score_point_ids,
                    criteria=criteria,
                    check_items=check_items,
                )
            )

        score_groups: dict[str, list[ResponseDuty]] = defaultdict(list)
        group_meta: dict[str, dict] = {}
        for duty in score_duties:
            topic = topics[duty.topic_id]
            attributes = topic.attributes
            group_id = str(attributes.get("score_group_id") or "ungrouped")
            score_groups[group_id].append(duty)
            group_meta.setdefault(
                group_id,
                {
                    "title": str(attributes.get("score_group_title") or group_id),
                    "declared_points": attributes.get("score_group_declared_points"),
                    "order": int(attributes.get("score_group_order") or 0),
                    "category": score_group_category(
                        str(attributes.get("score_group_title") or group_id)
                    ),
                },
            )

        score_duty_id_by_requirement: dict[str, str] = {}
        for score_duty in sorted(
            score_duties,
            key=lambda item: int(topics[item.topic_id].attributes.get("score_point_order") or 0),
        ):
            for requirement_id in score_duty.requirement_ids:
                score_duty_id_by_requirement.setdefault(requirement_id, score_duty.duty_id)

        root_specs: list[tuple[str, str, str, int]] = []
        qualification_duties = [
            duty
            for duty in requirement_duties
            if topics[duty.topic_id].topic_type == "qualification"
            and not any(requirement_id in score_duty_id_by_requirement for requirement_id in duty.requirement_ids)
        ]
        if qualification_duties:
            root_specs.append(("qualification", "资格与合规响应", "证明投标资格并逐项响应实质性合规要求", 800))

        for group_id, meta in group_meta.items():
            root_specs.append(
                (
                    f"score:{group_id}",
                    score_group_chapter_title(
                        meta["title"],
                        meta["declared_points"],
                        meta["category"],
                    ),
                    "围绕评分标准组织可核验、可得分的投标响应",
                    meta["order"],
                )
            )

        routed_requirement_keys: dict[str, str] = {}
        score_key_by_category = {
            meta["category"]: f"score:{group_id}"
            for group_id, meta in sorted(group_meta.items(), key=lambda item: item[1]["order"])
        }
        for duty in requirement_duties:
            topic_type = topics[duty.topic_id].topic_type
            linked_score_duty_id = next(
                (
                    score_duty_id_by_requirement[requirement_id]
                    for requirement_id in duty.requirement_ids
                    if requirement_id in score_duty_id_by_requirement
                ),
                None,
            )
            if linked_score_duty_id is not None:
                routed_requirement_keys[duty.duty_id] = f"score-duty:{linked_score_duty_id}"
            elif topic_type == "qualification":
                routed_requirement_keys[duty.duty_id] = "qualification"
            elif topic_type in {"commercial", "compliance"}:
                routed_requirement_keys[duty.duty_id] = score_key_by_category.get("business", "business")
            else:
                routed_requirement_keys[duty.duty_id] = score_key_by_category.get("technical", "technical")

        if "business" in routed_requirement_keys.values():
            root_specs.append(("business", "商务响应", "响应商务条款、合同条件及偏离说明", 900))
        if "technical" in routed_requirement_keys.values():
            root_specs.append(("technical", "技术响应", "响应采购需求、实施交付及验收要求", 910))
        if not root_specs:
            root_specs.append(("response", "投标响应", "承载后续人工确认的投标响应目录", 999))

        root_specs.sort(key=lambda item: (item[3], item[0]))
        order = 0
        for root_index, (root_key, title, purpose, _) in enumerate(root_specs, start=1):
            root_id = f"chapter-{root_index}"
            score_group_id = root_key.removeprefix("score:") if root_key.startswith("score:") else ""
            flatten_score_group = bool(
                score_group_id
                and group_meta.get(score_group_id, {}).get("category") == "technical"
            )
            visible_group_root_ids: list[str] = []
            root_duties = [
                duty
                for duty in requirement_duties
                if routed_requirement_keys.get(duty.duty_id) == root_key
            ]
            root_score_duties = (
                score_groups.get(score_group_id, [])
                if root_key.startswith("score:")
                else []
            )
            root_quality_duties = [
                duty
                for duty in root_score_duties
                if topics[duty.topic_id].attributes.get("planning_role") == "document_quality_gate"
            ]
            content_score_duties = [
                duty for duty in root_score_duties if duty not in root_quality_duties
            ]
            root_required_mentions = ["document_quality_gate"] if document_quality_gates else []
            if not flatten_score_group:
                root_required_mentions.extend(
                    score_point_id
                    for duty in root_quality_duties
                    for score_point_id in duty.score_point_ids
                )
                nodes.append(
                    BlueprintNode(
                        chapter_id=root_id,
                        order=order,
                        title=title,
                        purpose=purpose,
                        writing_objectives=[
                            "逐项覆盖责任与评分标准",
                            "引用真实证据并保持跨章节一致",
                            *(f"全文质量门：{item}" for item in document_quality_items),
                        ],
                        required_mentions=root_required_mentions,
                        target_size=min(
                            12_000,
                            max(
                                800,
                                sum(800 for _ in root_duties)
                                + sum(
                                    1200 if duty.priority == "blocking" else 800
                                    for duty in content_score_duties
                                ),
                            ),
                        ),
                    )
                )
                visible_group_root_ids.append(root_id)
                order += 1
            elif root_duties:
                requirement_root_id = f"{root_id}-requirements"
                nodes.append(
                    BlueprintNode(
                        chapter_id=requirement_root_id,
                        order=order,
                        title="技术条款响应",
                        purpose="响应未被评分叶子直接承接的技术、交付与验收义务",
                        writing_objectives=[
                            "逐项响应实质性技术条款和偏离要求",
                            *(f"全文质量门：{item}" for item in document_quality_items),
                        ],
                        required_mentions=root_required_mentions,
                        target_size=max(800, 800 * len(root_duties)),
                    )
                )
                visible_group_root_ids.append(requirement_root_id)
                order += 1
            for duty in root_duties:
                assignments.append(
                    TopicChapterAssignment(
                        assignment_id=f"A-{duty.duty_id}",
                        duty_id=duty.duty_id,
                        chapter_id=(
                            f"{root_id}-requirements"
                            if flatten_score_group
                            else root_id
                        ),
                        role="primary",
                        response_scope="在受控投标响应章节中完整覆盖此采购责任",
                        rationale="采购原文章节仅作为事实来源，按响应责任归入受控章节",
                        confidence=duty.confidence,
                    )
                )

            for duty in (root_quality_duties if not flatten_score_group else []):
                assignments.append(
                    TopicChapterAssignment(
                        assignment_id=f"A-{duty.duty_id}",
                        duty_id=duty.duty_id,
                        chapter_id=root_id,
                        role="primary",
                        response_scope="全文质量评分由文档级质量门统一执行，不生成独立正文章节",
                        rationale="整体评价是全篇约束，不是可单独作答的章节主题",
                        confidence=duty.confidence,
                    )
                )
                for requirement_duty in requirement_duties:
                    if routed_requirement_keys.get(requirement_duty.duty_id) != f"score-duty:{duty.duty_id}":
                        continue
                    assignments.append(
                        TopicChapterAssignment(
                            assignment_id=f"A-{requirement_duty.duty_id}",
                            duty_id=requirement_duty.duty_id,
                            chapter_id=root_id,
                            role="primary",
                            response_scope="在全文质量门所在根章节承接对应采购责任",
                            rationale="该 Requirement 由全文评分点引用，不另生空洞章节",
                            confidence=requirement_duty.confidence,
                        )
                    )

            title_counts: dict[tuple[str, str], int] = defaultdict(int)
            intermediate_by_path: dict[tuple[str, ...], str] = {}
            for point_index, duty in enumerate(
                sorted(
                    content_score_duties,
                    key=lambda item: int(topics[item.topic_id].attributes.get("score_point_order") or 0),
                ),
                start=1,
            ):
                topic = topics[duty.topic_id]
                raw_path = [
                    str(item).strip()
                    for item in (topic.attributes.get("score_outline_path") or [])
                    if str(item).strip()
                ]
                canonical_title = score_point_chapter_title(
                    topic.canonical_name,
                    point_index,
                )
                repeated_path_parent = False
                if raw_path:
                    last_subject = outline_subject(raw_path[-1])
                    canonical_subject = outline_subject(canonical_title)
                    repeated_path_parent = (
                        bool(last_subject)
                        and canonical_subject != last_subject
                        and canonical_subject.startswith(last_subject)
                    )
                parent_path = raw_path if repeated_path_parent else raw_path[:-1]
                parent_chapter_id = None if flatten_score_group else root_id
                path_key: list[str] = []
                for path_index, raw_parent_title in enumerate(parent_path, start=1):
                    parent_title = score_point_chapter_title(raw_parent_title, path_index)
                    if not parent_title or parent_title == title:
                        continue
                    path_key.append(parent_title)
                    frozen_key = tuple(path_key)
                    existing_parent = intermediate_by_path.get(frozen_key)
                    if existing_parent is not None:
                        parent_chapter_id = existing_parent
                        continue
                    intermediate_id = f"{root_id}-factor-{len(intermediate_by_path) + 1}"
                    nodes.append(
                        BlueprintNode(
                            chapter_id=intermediate_id,
                            parent_chapter_id=parent_chapter_id,
                            order=order,
                            title=parent_title,
                            purpose="组织同一评分因素下的原子得分响应",
                            writing_objectives=[
                                "说明本评分因素的总体方法、边界和各子项关系",
                                *(
                                    [f"全文质量门：{item}" for item in document_quality_items]
                                    if parent_chapter_id is None
                                    else []
                                ),
                            ],
                            required_mentions=(
                                ["document_quality_gate"]
                                if parent_chapter_id is None and document_quality_gates
                                else []
                            ),
                            target_size=800,
                        )
                    )
                    if parent_chapter_id is None:
                        visible_group_root_ids.append(intermediate_id)
                    order += 1
                    intermediate_by_path[frozen_key] = intermediate_id
                    parent_chapter_id = intermediate_id

                base_title = (
                    score_leaf_title(topic.canonical_name, raw_path[-1], point_index)
                    if repeated_path_parent
                    else canonical_title
                )
                title_key = (str(parent_chapter_id or ""), base_title)
                title_counts[title_key] += 1
                child_title = (
                    base_title
                    if title_counts[title_key] == 1
                    else f"{base_title}（第{title_counts[title_key]}项）"
                )
                chapter_id = f"{root_id}-score-{point_index}"
                response_shape = str(topic.attributes.get("response_shape") or "narrative")
                required_mentions = [*duty.score_point_ids]
                if response_shape == "form/table":
                    required_mentions.append("form/table")
                if parent_chapter_id is None and document_quality_gates:
                    required_mentions.append("document_quality_gate")
                full_score_conditions = [
                    str(item)
                    for item in (topic.attributes.get("full_score_conditions") or [])
                    if str(item).strip()
                ]
                nodes.append(
                    BlueprintNode(
                        chapter_id=chapter_id,
                        parent_chapter_id=parent_chapter_id,
                        order=order,
                        title=child_title,
                        purpose="直接响应一个可独立得分的评分叶子",
                        writing_objectives=[
                            *full_score_conditions,
                            *duty.response_expectations,
                            *(
                                [f"全文质量门：{item}" for item in document_quality_items]
                                if parent_chapter_id is None
                                else []
                            ),
                        ],
                        required_mentions=required_mentions,
                        planned_tables=["报价一览表"] if response_shape == "form/table" else [],
                        target_size=1200 if duty.priority == "blocking" else 800,
                    )
                )
                if parent_chapter_id is None:
                    visible_group_root_ids.append(chapter_id)
                order += 1
                assignments.append(
                    TopicChapterAssignment(
                        assignment_id=f"A-{duty.duty_id}",
                        duty_id=duty.duty_id,
                        chapter_id=chapter_id,
                        role="primary",
                        response_scope="完整响应对应评分点，不在其他章节重复主写",
                        rationale="评分叶子是覆盖单元；父级评分因素只负责组织目录层级",
                        confidence=duty.confidence,
                    )
                )
                condition_titles: dict[str, int] = defaultdict(int)
                for condition_index, condition in enumerate(full_score_conditions, start=1):
                    condition_title = full_score_condition_heading(condition, condition_index)
                    if (
                        len(full_score_conditions) == 1
                        and condition_title.replace(" ", "") == child_title.replace(" ", "")
                    ):
                        continue
                    condition_titles[condition_title] += 1
                    if condition_titles[condition_title] > 1:
                        condition_title = (
                            f"{condition_title}（{condition_titles[condition_title]}）"
                        )
                    condition_chapter_id = f"{chapter_id}-condition-{condition_index}"
                    nodes.append(
                        BlueprintNode(
                            chapter_id=condition_chapter_id,
                            parent_chapter_id=chapter_id,
                            order=order,
                            title=condition_title,
                            purpose="逐项展开该评分叶子的满分条件",
                            writing_objectives=[condition],
                            required_mentions=[*duty.score_point_ids],
                            target_size=600,
                        )
                    )
                    order += 1
                    assignments.append(
                        TopicChapterAssignment(
                            assignment_id=(
                                f"A-{duty.duty_id}-condition-{condition_index}"
                            ),
                            duty_id=duty.duty_id,
                            chapter_id=condition_chapter_id,
                            role="supporting",
                            response_scope="展开一个可核验的满分档原子条件",
                            rationale="二级标题由评分标准最高档的并列要求确定性反推",
                            confidence=duty.confidence,
                        )
                    )
                for requirement_duty in requirement_duties:
                    if routed_requirement_keys.get(requirement_duty.duty_id) != f"score-duty:{duty.duty_id}":
                        continue
                    assignments.append(
                        TopicChapterAssignment(
                            assignment_id=f"A-{requirement_duty.duty_id}",
                            duty_id=requirement_duty.duty_id,
                            chapter_id=chapter_id,
                            role="primary",
                            response_scope="与对应评分点在同一章节完整覆盖采购责任",
                            rationale="评分点已引用该 Requirement，避免另生重复响应章节",
                            confidence=requirement_duty.confidence,
                        )
                    )

            if flatten_score_group and root_quality_duties:
                if not visible_group_root_ids:
                    fallback_root_id = f"{root_id}-response"
                    nodes.append(
                        BlueprintNode(
                            chapter_id=fallback_root_id,
                            order=order,
                            title="技术响应",
                            purpose="承载技术评分与全文质量约束",
                            writing_objectives=[
                                *(f"全文质量门：{item}" for item in document_quality_items),
                            ],
                            required_mentions=["document_quality_gate"],
                            target_size=800,
                        )
                    )
                    visible_group_root_ids.append(fallback_root_id)
                    order += 1
                quality_target_id = next(
                    (
                        chapter_id
                        for chapter_id in visible_group_root_ids
                        if not chapter_id.endswith("-requirements")
                    ),
                    visible_group_root_ids[0],
                )
                quality_score_ids = [
                    score_point_id
                    for duty in root_quality_duties
                    for score_point_id in duty.score_point_ids
                ]
                for node_index, node in enumerate(nodes):
                    if node.chapter_id != quality_target_id:
                        continue
                    nodes[node_index] = node.model_copy(
                        update={
                            "required_mentions": list(
                                dict.fromkeys(
                                    [
                                        *node.required_mentions,
                                        "document_quality_gate",
                                        *quality_score_ids,
                                    ]
                                )
                            )
                        }
                    )
                    break
                for duty in root_quality_duties:
                    assignments.append(
                        TopicChapterAssignment(
                            assignment_id=f"A-{duty.duty_id}",
                            duty_id=duty.duty_id,
                            chapter_id=quality_target_id,
                            role="primary",
                            response_scope="全文质量评分由文档级质量门统一执行，不生成独立正文章节",
                            rationale="整体评价是全篇约束，不是可单独作答的章节主题",
                            confidence=duty.confidence,
                        )
                    )
                    for requirement_duty in requirement_duties:
                        if (
                            routed_requirement_keys.get(requirement_duty.duty_id)
                            != f"score-duty:{duty.duty_id}"
                        ):
                            continue
                        assignments.append(
                            TopicChapterAssignment(
                                assignment_id=f"A-{requirement_duty.duty_id}",
                                duty_id=requirement_duty.duty_id,
                                chapter_id=quality_target_id,
                                role="primary",
                                response_scope="在全文质量门所在技术章节承接对应采购责任",
                                rationale="该 Requirement 由全文评分点引用，不另生空洞章节",
                                confidence=requirement_duty.confidence,
                            )
                        )

        primary_duty_ids = {item.duty_id for item in assignments if item.role == "primary"}
        score_primary_chapter_ids = [
            item.chapter_id
            for item in assignments
            if item.role == "primary" and duties[item.duty_id].score_point_ids
        ]
        coverage = {
            "duty_count": len(duties),
            "primary_duty_count": len(primary_duty_ids),
            "score_point_count": len(
                {
                    score_point_id
                    for duty in score_duties
                    for score_point_id in duty.score_point_ids
                }
            ),
            "score_primary_chapter_count": len(set(score_primary_chapter_ids)),
            "document_quality_gate_count": len(document_quality_gates),
            "score_group_points": {
                group_id: meta["declared_points"]
                for group_id, meta in group_meta.items()
                if isinstance(meta.get("declared_points"), (int, float))
            },
            "uncovered_duty_ids": sorted(set(duties) - primary_duty_ids),
        }
        return ChapterBlueprint(
            revision=revision,
            source_hashes=graph.source_hashes,
            blueprint_id=(
                f"BP-{hashlib.sha256((graph.graph_id + str(revision)).encode()).hexdigest()[:12]}"
            ),
            mode="auto_outline",
            topic_graph_revision=graph.revision,
            nodes=nodes,
            assignments=assignments,
            document_quality_gates=document_quality_gates,
            coverage_summary=coverage,
        )

    @staticmethod
    def _requirement_topic_type(kind: RequirementKind, text: str) -> tuple[str, str]:
        if kind is RequirementKind.QUALIFICATION: return "qualification", "verify"
        if kind is RequirementKind.DELIVERABLE: return "deliverable", "commit"
        if kind is RequirementKind.ACCEPTANCE: return "acceptance", "accept"
        if kind is RequirementKind.CONTRACT: return "commercial", "commit"
        if any(token in text for token in ("安全", "保密", "权限")): return "security", "design"
        if any(token in text for token in ("数据", "库", "接口")): return "data", "design"
        if any(token in text for token in ("实施", "部署", "培训")): return "implementation", "implement"
        if any(token in text for token in ("运维", "服务", "响应")): return "service_operation", "operate"
        if any(token in text for token in ("架构", "平台", "系统")): return "architecture", "design"
        return "function", "explain"

    @staticmethod
    def _score_topic_type(title: str) -> str:
        return PlanningAgent._requirement_topic_type(RequirementKind.MANDATORY, title)[0]
