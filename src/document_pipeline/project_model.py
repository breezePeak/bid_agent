from __future__ import annotations

import re
from collections.abc import Iterable

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import (
    InputRole,
    ProjectFact,
    ProjectModel,
    RequirementLedger,
    ScoreModel,
    SourceBlock,
    SourceIndex,
)


_SEMANTIC_LIST_FIELDS = (
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
_MECHANICAL_COPY_FIELDS = ("goals", "scope", "work_packages")
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:未命名项目|未提供|未知|待确认|待补充|不详|暂无|无|n/?a|[-—/]+)$",
    re.IGNORECASE,
)
_ENTERPRISE_CLAIM = re.compile(
    r"(?:本公司|本企业|我公司|我司|我方|我单位|我企业|我方团队)|"
    r"(?:拥有|已取得|已通过|已完成|已实施|已配备|现有|具备)"
    r".{0,18}(?:资质|资格|能力|经验|案例|业绩|人员|团队|证书|专利|著作权)",
)
_OBLIGATION_SUBJECT = re.compile(r"(?:投标人|供应商|承包人|中标人).{0,8}(?:应|须|需|必须)")
_BIDDER_IDENTITY_KEYS = (
    "bidder",
    "supplier",
    "vendor",
    "company",
    "enterprise",
    "投标人",
    "供应商",
    "投标单位",
    "企业名称",
    "公司名称",
)
_PURCHASER_IDENTITY_KEYS = (
    "purchaser",
    "procurer",
    "buyer",
    "client",
    "owner",
    "agency",
    "采购人",
    "采购单位",
    "招标人",
    "招标单位",
    "代理机构",
    "业主",
)
_PROJECT_IDENTITY_KEYS = (
    "project",
    "procurement",
    "tender",
    "package",
    "lot",
    "section",
    "year",
    "region",
    "项目",
    "采购",
    "招标",
    "标书",
    "包号",
    "包件",
    "标段",
    "年度",
    "区域",
)


def audit_project_model(
    model: ProjectModel,
    requirement_ledger: RequirementLedger,
    score_model: ScoreModel,
    source_index: SourceIndex,
) -> dict[str, object]:
    """Audit that ProjectModel remains a traceable projection, not a fact store.

    The validator deliberately checks provenance and structural invariants only.
    It does not attempt to replace semantic inference with rules, but it does
    reject facts and identities that cannot be tied back to the frozen inputs.
    """

    findings: list[dict[str, str]] = []
    requirements = {
        item.requirement_id: item for item in requirement_ledger.requirements
    }
    active_requirement_ids = {
        item.requirement_id
        for item in requirement_ledger.requirements
        if item.status not in {"blocked", "waived"}
    }
    score_point_ids = {point.score_point_id for point in score_model.points}
    blocks_by_anchor: dict[tuple[str, str], SourceBlock] = {
        (block.input_id, block.source_anchor.chunk_id): block
        for block in source_index.blocks
    }
    fact_reference_texts, fact_reference_blocks = (
        _project_fact_reference_catalog(
            requirement_ledger,
            score_model,
            source_index,
            blocks_by_anchor=blocks_by_anchor,
        )
    )

    expected_hashes: dict[str, str] = {}
    for artifact_name, source_hashes in (
        ("RequirementLedger", requirement_ledger.source_hashes),
        ("ScoreModel", score_model.source_hashes),
    ):
        for source_id, source_hash in source_hashes.items():
            previous = expected_hashes.get(source_id)
            if previous is not None and previous != source_hash:
                findings.append(
                    _finding(
                        "PROJECT_UPSTREAM_SOURCE_CONFLICT",
                        f"{artifact_name} 对来源 {source_id} 的 hash 与其他上游不一致",
                    )
                )
            expected_hashes[source_id] = source_hash
    for source_id, source_hash in expected_hashes.items():
        if model.source_hashes.get(source_id) != source_hash:
            findings.append(
                _finding(
                    "PROJECT_SOURCE_HASH_MISMATCH",
                    f"ProjectModel 未绑定上游来源 {source_id} 的当前 hash",
                )
            )
    for source_id, source_hash in model.source_hashes.items():
        indexed_hash = source_index.source_hashes.get(source_id)
        if indexed_hash is None or indexed_hash != source_hash:
            findings.append(
                _finding(
                    "PROJECT_SOURCE_HASH_MISMATCH",
                    f"ProjectModel 来源 {source_id} 的 hash 无法由当前 SourceIndex 证明",
                )
            )

    _audit_exact_id_coverage(
        declared_ids=model.requirement_ids,
        expected_ids=active_requirement_ids,
        known_ids=set(requirements),
        duplicate_code="PROJECT_REQUIREMENT_REFERENCE_DUPLICATE",
        unknown_code="PROJECT_UNKNOWN_REQUIREMENT",
        missing_code="PROJECT_REQUIREMENT_COVERAGE_MISSING",
        extra_code="PROJECT_INACTIVE_REQUIREMENT_INCLUDED",
        label="Requirement",
        findings=findings,
    )
    _audit_exact_id_coverage(
        declared_ids=model.score_point_ids,
        expected_ids=score_point_ids,
        known_ids=score_point_ids,
        duplicate_code="PROJECT_SCORE_REFERENCE_DUPLICATE",
        unknown_code="PROJECT_UNKNOWN_SCORE_POINT",
        missing_code="PROJECT_SCORE_COVERAGE_MISSING",
        extra_code="PROJECT_UNKNOWN_SCORE_POINT",
        label="ScorePoint",
        findings=findings,
    )

    required_semantic_refs = {
        *(f"RequirementLedger:{item_id}" for item_id in active_requirement_ids),
        *(f"ScoreModel:{item_id}" for item_id in score_point_ids),
    }
    semantic_refs = set(model.semantic_upstream_refs)
    missing_semantic_refs = required_semantic_refs - semantic_refs
    if missing_semantic_refs:
        findings.append(
            _finding(
                "PROJECT_SEMANTIC_COVERAGE_MISSING",
                "ProjectModel 的语义结论或明确证据缺口未覆盖全部有效上游："
                f"{sorted(missing_semantic_refs)}",
            )
        )

    known_semantic_refs = {
        *(f"RequirementLedger:{item_id}" for item_id in requirements),
        *(f"ScoreModel:{group.group_id}" for group in score_model.groups),
        *(f"ScoreModel:{point.score_point_id}" for point in score_model.points),
        *(
            f"ScoreModel:{condition.condition_id}"
            for point in score_model.points
            for condition in point.score_conditions
        ),
        *(
            f"ScoreModel:{unit.unit_id}"
            for point in score_model.points
            for unit in point.response_units
        ),
        *(f"SourceIndex:{block.block_id}" for block in source_index.blocks),
        *(
            f"SourceIndex:{block.input_id}:{block.source_anchor.chunk_id}"
            for block in source_index.blocks
        ),
    }
    unknown_semantic_refs = semantic_refs - known_semantic_refs
    if unknown_semantic_refs:
        findings.append(
            _finding(
                "PROJECT_SEMANTIC_REFERENCE_UNKNOWN",
                "ProjectModel.semantic_upstream_refs 含未知上游引用："
                f"{sorted(unknown_semantic_refs)}",
            )
        )
    semantic_item_count = sum(
        len(getattr(model, field_name))
        for field_name in _SEMANTIC_LIST_FIELDS
    ) + len(model.identity) + len(model.terminology)
    semantic_item_count += sum(
        len(group)
        for group in (
            model.confirmed_facts,
            model.inferences,
            model.conflicts,
        )
    )
    semantic_item_count += len(model.evidence_needs)
    if required_semantic_refs and semantic_item_count == 0:
        findings.append(
            _finding(
                "PROJECT_SEMANTIC_UNDERSTANDING_EMPTY",
                "ProjectModel 仅声明覆盖 ID，未形成带来源的项目语义结论或证据缺口",
            )
        )
    if required_semantic_refs and not any(
        (
            model.goals,
            model.scope,
            model.work_packages,
            model.evidence_needs,
            model.unknowns,
        )
    ):
        findings.append(
            _finding(
                "PROJECT_CORE_UNDERSTANDING_MISSING",
                "ProjectModel 未形成目标、范围、工作包，也未明确 unknown/evidence_need",
            )
        )

    for left_index, left_field in enumerate(_MECHANICAL_COPY_FIELDS):
        left_values = getattr(model, left_field)
        if not left_values:
            continue
        for right_field in _MECHANICAL_COPY_FIELDS[left_index + 1 :]:
            right_values = getattr(model, right_field)
            if right_values and _lists_are_mechanical_copies(
                left_values,
                right_values,
            ):
                findings.append(
                    _finding(
                        "PROJECT_MODEL_MECHANICAL_COPY",
                        f"ProjectModel.{left_field} 与 {right_field} 为相同或近似复制列表",
                    )
                )

    fact_count = 0
    confirmed_fact_count = 0
    for classification, facts in (
        ("confirmed", model.confirmed_facts),
        ("inference", model.inferences),
        ("conflict", model.conflicts),
    ):
        fact_count += len(facts)
        if classification == "confirmed":
            confirmed_fact_count += len(facts)
        for fact in facts:
            _audit_project_fact(
                fact,
                classification=classification,
                model=model,
                requirements=requirements,
                blocks_by_anchor=blocks_by_anchor,
                source_index=source_index,
                reference_texts=fact_reference_texts,
                reference_blocks=fact_reference_blocks,
                known_fact_refs=known_semantic_refs,
                semantic_refs=semantic_refs,
                findings=findings,
            )

    for field_name, value in model.identity.items():
        if _is_placeholder(value):
            continue
        allowed_roles = _identity_authority_roles(field_name)
        supporting_blocks = [
            block
            for block in source_index.blocks
            if block.input_role in allowed_roles
            and model.source_hashes.get(block.input_id)
            == source_index.source_hashes.get(block.input_id)
        ]
        if not _is_text_supported(value, (block.content for block in supporting_blocks)):
            findings.append(
                _finding(
                    "PROJECT_IDENTITY_UNSUPPORTED",
                    f"项目身份字段 {field_name}={value!r} 无法由对应权威来源证明",
                )
            )

    # Every semantic projection emitted by the current compiler is mirrored as
    # a ProjectFact.  This catches manually submitted payloads that add prose
    # after provenance compilation while tolerating concise paraphrases.
    traced_statements = [
        fact.statement
        for fact in (
            *model.confirmed_facts,
            *model.inferences,
            *model.conflicts,
        )
    ]
    for field_name in _SEMANTIC_LIST_FIELDS:
        for index, statement in enumerate(getattr(model, field_name), start=1):
            if not _is_text_supported(statement, traced_statements):
                findings.append(
                    _finding(
                        "PROJECT_SEMANTIC_PROJECTION_UNTRACEABLE",
                        f"ProjectModel.{field_name}[{index}] 未绑定可追溯 ProjectFact",
                    )
                )

    return {
        "passed": not findings,
        "findings": findings,
        "fact_count": fact_count,
        "confirmed_fact_count": confirmed_fact_count,
        "requirement_count": len(active_requirement_ids),
        "score_point_count": len(score_point_ids),
    }


def _audit_project_fact(
    fact: ProjectFact,
    *,
    classification: str,
    model: ProjectModel,
    requirements: dict[str, object],
    blocks_by_anchor: dict[tuple[str, str], SourceBlock],
    source_index: SourceIndex,
    reference_texts: dict[str, list[str]],
    reference_blocks: dict[str, list[SourceBlock]],
    known_fact_refs: set[str],
    semantic_refs: set[str],
    findings: list[dict[str, str]],
) -> None:
    unknown_requirement_ids = set(fact.requirement_ids) - set(requirements)
    if unknown_requirement_ids:
        findings.append(
            _finding(
                "PROJECT_FACT_REFERENCE_UNKNOWN",
                f"ProjectFact {fact.fact_id} 引用未知 Requirement: {sorted(unknown_requirement_ids)}",
            )
        )

    unknown_upstream_refs = set(fact.upstream_refs) - known_fact_refs
    if unknown_upstream_refs:
        findings.append(
            _finding(
                "PROJECT_FACT_REFERENCE_UNKNOWN",
                f"ProjectFact {fact.fact_id} 引用未知上游来源: "
                f"{sorted(unknown_upstream_refs)}",
            )
        )
    undeclared_upstream_refs = set(fact.upstream_refs) - semantic_refs
    if undeclared_upstream_refs:
        findings.append(
            _finding(
                "PROJECT_FACT_SEMANTIC_REFERENCE_UNDECLARED",
                f"ProjectFact {fact.fact_id} 的上游来源未纳入 "
                "ProjectModel.semantic_upstream_refs: "
                f"{sorted(undeclared_upstream_refs)}",
            )
        )

    block: SourceBlock | None = None
    if fact.source_anchor is not None:
        anchor_key = (
            fact.source_anchor.source_input_id,
            fact.source_anchor.chunk_id,
        )
        block = blocks_by_anchor.get(anchor_key)
        if block is None:
            findings.append(
                _finding(
                    "PROJECT_FACT_ANCHOR_INVALID",
                    f"ProjectFact {fact.fact_id} 的 SourceAnchor 无法解析",
                )
            )
        else:
            canonical_anchor = block.source_anchor
            if (
                canonical_anchor.location != fact.source_anchor.location
                or (
                    fact.source_anchor.page is not None
                    and canonical_anchor.page != fact.source_anchor.page
                )
            ):
                findings.append(
                    _finding(
                        "PROJECT_FACT_ANCHOR_INVALID",
                        f"ProjectFact {fact.fact_id} 的 SourceAnchor 定位与 SourceIndex 不一致",
                    )
                )
    known_requirements = [
        requirements[requirement_id]
        for requirement_id in fact.requirement_ids
        if requirement_id in requirements
    ]
    if fact.upstream_refs:
        evidence_groups = [
            reference_texts.get(ref, [])
            for ref in fact.upstream_refs
            if ref in known_fact_refs
        ]
        supporting_blocks = _dedupe_source_blocks(
            block
            for ref in fact.upstream_refs
            for block in reference_blocks.get(ref, [])
        )
    else:
        evidence_groups = [
            *([[block.content]] if block is not None else []),
            *(
                [
                    text
                    for text in (
                        str(getattr(requirement, "original_text", "")),
                        str(
                            getattr(
                                requirement,
                                "normalized_requirement",
                                "",
                            )
                        ),
                    )
                    if text
                ]
                for requirement in known_requirements
            ),
        ]
        supporting_blocks = [block] if block is not None else []

    if (
        fact.upstream_refs
        and block is not None
        and supporting_blocks
        and block.block_id
        not in {supporting.block_id for supporting in supporting_blocks}
    ):
        findings.append(
            _finding(
                "PROJECT_FACT_ANCHOR_REFERENCE_MISMATCH",
                f"ProjectFact {fact.fact_id} 的兼容 SourceAnchor "
                "不属于 upstream_refs 所解析的来源",
            )
        )

    for supporting_block in supporting_blocks:
        if (
            model.source_hashes.get(supporting_block.input_id)
            != source_index.source_hashes.get(supporting_block.input_id)
        ):
            findings.append(
                _finding(
                    "PROJECT_FACT_SOURCE_UNBOUND",
                    f"ProjectFact {fact.fact_id} 的来源 "
                    f"{supporting_block.input_id} 未绑定当前 hash",
                )
            )

    traceable = any(group for group in evidence_groups)
    if classification == "confirmed":
        if not traceable:
            findings.append(
                _finding(
                    "PROJECT_CONFIRMED_FACT_UNSUPPORTED",
                    f"已确认 ProjectFact {fact.fact_id} 没有可解析的来源或 Requirement",
                )
            )
        elif not _is_text_supported_by_groups(
            fact.statement,
            evidence_groups,
        ):
            findings.append(
                _finding(
                    "PROJECT_CONFIRMED_FACT_UNSUPPORTED",
                    f"已确认 ProjectFact {fact.fact_id} 与其引用来源缺少可核验文本关联",
                )
            )
        if (
            supporting_blocks
            and all(
                supporting.input_role
                in {
                    InputRole.REFERENCE,
                    InputRole.GUIDANCE,
                    InputRole.TEMPLATE,
                }
                for supporting in supporting_blocks
            )
            and not known_requirements
        ):
            findings.append(
                _finding(
                    "PROJECT_CONFIRMED_FACT_AUTHORITY_INVALID",
                    f"已确认 ProjectFact {fact.fact_id} 仅由非当前项目事实来源证明",
                )
            )
    elif not traceable:
        findings.append(
            _finding(
                "PROJECT_INFERENCE_UNTRACEABLE",
                f"ProjectFact {fact.fact_id}（{classification}）没有可解析的上游依据",
            )
        )

    if _is_enterprise_claim(fact.statement) and not any(
        supporting.input_role is InputRole.COMPANY
        for supporting in supporting_blocks
    ):
        findings.append(
            _finding(
                "PROJECT_ENTERPRISE_CLAIM_AUTHORITY_INVALID",
                f"ProjectFact {fact.fact_id} 的企业能力声明未由 company 来源证明",
            )
        )


def _project_fact_reference_catalog(
    requirement_ledger: RequirementLedger,
    score_model: ScoreModel,
    source_index: SourceIndex,
    *,
    blocks_by_anchor: dict[tuple[str, str], SourceBlock],
) -> tuple[dict[str, list[str]], dict[str, list[SourceBlock]]]:
    """Resolve exact per-reference evidence retained by compiled ProjectFacts."""

    texts: dict[str, list[str]] = {}
    blocks: dict[str, list[SourceBlock]] = {}

    def add(
        ref: str,
        *values: object,
        anchors: Iterable[object] = (),
    ) -> None:
        ref_texts = [
            str(value)
            for value in values
            if value is not None and str(value).strip()
        ]
        if ref_texts:
            texts.setdefault(ref, []).extend(ref_texts)
        resolved = _dedupe_source_blocks(
            blocks_by_anchor.get(
                (
                    str(getattr(anchor, "source_input_id", "")),
                    str(getattr(anchor, "chunk_id", "")),
                )
            )
            for anchor in anchors
        )
        if resolved:
            blocks.setdefault(ref, []).extend(resolved)
            blocks[ref] = _dedupe_source_blocks(blocks[ref])

    for requirement in requirement_ledger.requirements:
        add(
            f"RequirementLedger:{requirement.requirement_id}",
            requirement.original_text,
            requirement.normalized_requirement,
            anchors=[requirement.source_anchor],
        )
    for group in score_model.groups:
        add(f"ScoreModel:{group.group_id}", group.title)
    for point in score_model.points:
        add(
            f"ScoreModel:{point.score_point_id}",
            point.title,
            point.criterion,
            point.response_expectation,
            *point.full_score_conditions,
            *(level.criterion for level in point.scoring_levels),
            anchors=point.source_anchors,
        )
        for condition in point.score_conditions:
            add(
                f"ScoreModel:{condition.condition_id}",
                condition.text,
                condition.source_excerpt,
                condition.subject,
                condition.response_intent,
                anchors=(
                    [condition.source_anchor]
                    if condition.source_anchor is not None
                    else []
                ),
            )
        for unit in point.response_units:
            add(
                f"ScoreModel:{unit.unit_id}",
                unit.title,
                unit.response_expectation,
                anchors=point.source_anchors,
            )
    for source_block in source_index.blocks:
        for ref in (
            f"SourceIndex:{source_block.block_id}",
            "SourceIndex:"
            f"{source_block.input_id}:{source_block.source_anchor.chunk_id}",
        ):
            add(
                ref,
                source_block.content,
                anchors=[source_block.source_anchor],
            )

    for ref, values in texts.items():
        texts[ref] = list(dict.fromkeys(values))
    return texts, blocks


def _dedupe_source_blocks(
    blocks: Iterable[SourceBlock | None],
) -> list[SourceBlock]:
    result: list[SourceBlock] = []
    seen: set[str] = set()
    for block in blocks:
        if block is None or block.block_id in seen:
            continue
        seen.add(block.block_id)
        result.append(block)
    return result


def _audit_exact_id_coverage(
    *,
    declared_ids: list[str],
    expected_ids: set[str],
    known_ids: set[str],
    duplicate_code: str,
    unknown_code: str,
    missing_code: str,
    extra_code: str,
    label: str,
    findings: list[dict[str, str]],
) -> None:
    declared = set(declared_ids)
    if len(declared_ids) != len(declared):
        findings.append(
            _finding(duplicate_code, f"ProjectModel 存在重复 {label} 引用")
        )
    if unknown := declared - known_ids:
        findings.append(
            _finding(unknown_code, f"ProjectModel 引用未知 {label}: {sorted(unknown)}")
        )
    if missing := expected_ids - declared:
        findings.append(
            _finding(missing_code, f"ProjectModel 遗漏 {label}: {sorted(missing)}")
        )
    if extra := (declared & known_ids) - expected_ids:
        findings.append(
            _finding(extra_code, f"ProjectModel 错误纳入非活动 {label}: {sorted(extra)}")
        )


def _identity_authority_roles(field_name: str) -> set[InputRole]:
    normalized_key = _normalize_text(field_name)
    if any(
        _normalize_text(token) in normalized_key
        for token in _PURCHASER_IDENTITY_KEYS
    ):
        return {
            InputRole.TENDER,
            InputRole.SCORE,
            InputRole.AMENDMENT,
        }
    if any(_normalize_text(token) in normalized_key for token in _BIDDER_IDENTITY_KEYS):
        return {InputRole.COMPANY}
    if any(
        _normalize_text(token) in normalized_key
        for token in _PROJECT_IDENTITY_KEYS
    ):
        return {
            InputRole.TENDER,
            InputRole.SCORE,
            InputRole.AMENDMENT,
        }
    return {
        InputRole.TENDER,
        InputRole.SCORE,
        InputRole.AMENDMENT,
        InputRole.COMPANY,
    }


def _lists_are_mechanical_copies(left: list[str], right: list[str]) -> bool:
    left_normalized = [_normalize_text(item) for item in left if _normalize_text(item)]
    right_normalized = [_normalize_text(item) for item in right if _normalize_text(item)]
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized or set(left_normalized) == set(right_normalized):
        return True
    if min(len(left_normalized), len(right_normalized)) < 2:
        return False
    overlap = len(set(left_normalized) & set(right_normalized))
    return overlap / min(len(set(left_normalized)), len(set(right_normalized))) >= 0.9


def _is_enterprise_claim(text: str) -> bool:
    return bool(_ENTERPRISE_CLAIM.search(text)) and not bool(
        _OBLIGATION_SUBJECT.search(text)
    )


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_VALUE.fullmatch(value.strip()))


def _is_text_supported(value: str, candidates: Iterable[str]) -> bool:
    target = _normalize_text(value)
    if not target:
        return False
    for candidate in candidates:
        source = _normalize_text(candidate)
        if not source:
            continue
        if target in source:
            return True
        # A short heading such as "类似业绩" or "测绘资质" is not evidence for
        # every longer sentence that happens to contain those words.  The old
        # four-character substring shortcut let an unsupported suffix bypass
        # the n-gram checks entirely.  Reverse containment is safe only when
        # the cited text accounts for most of the claim.
        if (
            len(source) >= 4
            and source in target
            and len(source) / len(target) >= 0.72
        ):
            return True
        target_bigrams = _ngrams(target, 2)
        source_bigrams = _ngrams(source, 2)
        if not target_bigrams or not source_bigrams:
            continue
        overlap = len(target_bigrams & source_bigrams)
        target_recall = overlap / len(target_bigrams)
        shorter_recall = overlap / min(len(target_bigrams), len(source_bigrams))
        if target_recall >= 0.42 and shorter_recall >= 0.55:
            return True
    return False


def _is_text_supported_by_groups(
    value: str,
    candidate_groups: Iterable[Iterable[str]],
) -> bool:
    """Accept a faithful synthesis only when several cited sources jointly support it.

    The ordinary single-source rule remains unchanged.  Aggregate matching is
    deliberately limited to at most four distinct evidence groups, requires the
    same strong 55% target coverage used by the existing matcher, and requires
    at least two groups to contribute unique target text.  This supports a
    sentence whose clauses cite separate sources without enabling reference
    stuffing with a large unrelated source set.
    """

    groups: list[list[str]] = []
    for raw_group in candidate_groups:
        group = list(
            dict.fromkeys(
                str(candidate)
                for candidate in raw_group
                if str(candidate).strip()
            )
        )
        if group:
            groups.append(group)

    flattened = [candidate for group in groups for candidate in group]
    if _is_text_supported(value, flattened):
        return True

    target = _normalize_text(value)
    target_ngrams = _ngrams(target, 2)
    if len(target_ngrams) < 8:
        return False

    group_ngrams: list[set[str]] = []
    seen_groups: set[frozenset[str]] = set()
    for group in groups:
        grams = set().union(
            *(_ngrams(_normalize_text(candidate), 2) for candidate in group)
        )
        frozen = frozenset(grams)
        if not grams or frozen in seen_groups:
            continue
        seen_groups.add(frozen)
        group_ngrams.append(grams)

    if not 2 <= len(group_ngrams) <= 4:
        return False
    combined = set().union(*group_ngrams)
    if len(target_ngrams & combined) / len(target_ngrams) < 0.55:
        return False

    minimum_unique = max(2, (len(target_ngrams) + 19) // 20)
    contributors = 0
    for index, grams in enumerate(group_ngrams):
        other_grams = set().union(
            *(
                other
                for other_index, other in enumerate(group_ngrams)
                if other_index != index
            )
        )
        if len(target_ngrams & (grams - other_grams)) >= minimum_unique:
            contributors += 1
    return contributors >= 2


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def _ngrams(value: str, size: int) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def load_promoted_project_model(context: WorkspaceContext) -> ProjectModel:
    """Return the only runtime ProjectModel: the active promoted revision."""
    artifact = ControlStore(context).v3_active_artifact("ProjectModel")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ProjectModel 尚未晋级。", status_code=409)
    model = ProjectModel.model_validate(artifact["payload"])
    if model.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ProjectModel revision 与晋级记录不一致。", status_code=409)
    return model
