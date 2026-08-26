"""BidAgent's scoring-to-outline domain policy.

This is product runtime logic, not a Codex ``SKILL.md``.  It keeps the
experience learned from real bid documents on the canonical V3 path:
RequirementLedger + ScoreModel -> ChapterBlueprint -> G1/G2.  The
ResponseTopicGraph/ResponseDuty branch remains only for explicit legacy calls.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from .contracts import (
    ChapterBlueprint,
    DocumentMode,
    ProjectModel,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreModel,
    ScoringLevel,
    SourceIndex,
    TemplateStructureContract,
)


SCORING_OUTLINE_POLICY_VERSION = "v3-scoring-outline-policy-6"

_DOCUMENT_TERMS = ("投标文件", "响应文件", "技术文件", "技术标", "文件编制")
_QUALITY_TERMS = (
    "整体评价",
    "整体质量",
    "编制质量",
    "结构完整",
    "条理",
    "逻辑",
    "排版",
    "格式",
    "图表",
    "前后一致",
)
_HOLLOW_QUALITY_TERMS = {
    "完整",
    "完整性",
    "全面",
    "全面性",
    "合理",
    "合理性",
    "可行",
    "可行性",
    "针对",
    "针对性",
    "科学",
    "科学性",
    "规范",
    "规范性",
    "清晰",
    "清晰性",
    "逻辑",
    "逻辑性",
    "一致",
    "一致性",
    "先进",
    "先进性",
    "实用性",
    "操作性",
    "可操作性",
    "充分性",
    "有效性",
    "详实性",
}
_GENERIC_QUALITY_SUBJECTS = (
    "投标文件",
    "响应文件",
    "技术文件",
    "技术方案",
    "服务方案",
    "实施方案",
    "方案",
    "响应",
    "内容",
    "材料",
)


def is_hollow_quality_heading(value: str) -> bool:
    """Return true when a heading contains only generic quality adjectives."""

    normalized = re.sub(
        r"^\s*(?:(?:第[一二三四五六七八九十\d]+[章节条款])|"
        r"(?:[一二三四五六七八九十\d]+[、.)．]))\s*",
        "",
        str(value or ""),
    )
    normalized = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", normalized)
    normalized = re.sub(r"\s+", "", normalized).strip("：:、，,。；;/-—")
    normalized = re.sub(r"(?:要求|评价|说明|指标|标准)$", "", normalized)
    for subject in _GENERIC_QUALITY_SUBJECTS:
        if normalized.startswith(subject):
            normalized = normalized[len(subject) :]
            break
    tokens = [
        token
        for token in re.split(r"[、，,和及与/]+", normalized)
        if token
    ]
    return bool(tokens) and all(token in _HOLLOW_QUALITY_TERMS for token in tokens)


def is_sectionable_quality_condition(condition: Any) -> bool:
    """Whether a quality-labelled condition has its own concrete writing topic.

    Semantic extraction historically labelled many source sentences as ``quality``
    merely because they contain words such as ``清楚`` or ``可行``.  The quality
    adjective is not a reason to hide a concrete subject (for example, 项目任务
    背景、工作目标或检查方法) inside its parent chapter.  Only generic document or
    scheme-quality constraints remain parent-level writing objectives.
    """

    value = condition if isinstance(condition, dict) else None
    role = (
        value.get("condition_role", "")
        if value is not None
        else getattr(condition, "condition_role", "")
    )
    if str(role) != "quality":
        return False
    subject = str(
        (value.get("subject", "") if value is not None else getattr(condition, "subject", ""))
        or ""
    ).strip()
    if not subject or is_hollow_quality_heading(subject):
        return False
    normalized = re.sub(r"\s+", "", subject).strip("：:、，,。；;/-—")
    if not normalized:
        return False
    if any(normalized == item for item in _GENERIC_QUALITY_SUBJECTS):
        return False
    # A generic scheme/document title plus evaluation words is still a pure
    # quality constraint, not an independently writable section.
    remainder = normalized
    for generic in _GENERIC_QUALITY_SUBJECTS:
        if remainder.startswith(generic):
            remainder = remainder[len(generic) :]
            break
    quality_tokens = re.sub(r"[、，,；;]", "", remainder)
    if re.fullmatch(
        r"(?:完整|全面|合理|可行|针对性|科学|规范|清晰|明确|具体|翔实|详实|"
        r"逻辑|条理|充分|强|性|且|和|及|与)+",
        quality_tokens,
    ):
        return False
    return bool(remainder) and not is_hollow_quality_heading(remainder)
_EVALUATIVE_CUE = re.compile(
    r"(?:完整|全面|详细|合理|科学|准确|清晰|可行|正确)(?!性)|能够|满足|"
    r"重点突出|逻辑清楚|条理清楚|描述清楚|分工具体|目标明确"
)
_CONDITION_BOUNDARY = re.compile(
    r"[；;]|[，,、](?=(?:项目|工作|总体|服务|实施|质量|进度|成果|数据|人员|"
    r"组织|技术|方案|系统|平台|安全|保密|培训|运维|验收|交付|风险|应急|"
    r"目标|内容|背景|任务|措施|流程|建议|检查|使用|对|各|采用|建立|"
    r"制定|提供|编制|形成|完成|保障))"
)
_HEADING_PREDICATE = re.compile(
    r"(?:描述|阐述|说明)(?=清楚|清晰|全面|完整|准确|具体)|"
    r"理由(?=充分|合理|清楚|清晰)|"
    r"(?=(?:明确|具体|翔实|详实|细致|全面|完整|合理|科学|准确|清晰|可行|正确)(?!性)|"
    r"重点突出|逻辑清楚|条理清楚|层次清楚)"
)

_HEADING_EVALUATIVE_SUFFIX = re.compile(
    r"(?:条理清楚|逻辑清晰|重点突出|全面具体|全面细致|科学|合理|可行|"
    r"清楚|清晰|细致|具体|全面|完整|突出|可操作性强|操作性强)$"
)


def is_evaluative_sentence_heading(value: str) -> bool:
    """Whether a heading is a score criterion sentence rather than a topic.

    A concrete subject is allowed to contain terms such as ``可行性``.  The
    check is intentionally limited to evaluation predicates at the end of a
    heading, which are the phrases that make a table-of-contents entry read
    like a scoring rule.
    """

    normalized = re.sub(r"\s+", "", str(value or "")).strip("：:、，,。；;/-—")
    if not normalized:
        return False
    if normalized.startswith("对") and ("有具体实例" in normalized or "有实例" in normalized):
        return True
    if re.search(r"(?:得|计)\d+(?:\.\d+)?分", normalized):
        return True
    return bool(_HEADING_EVALUATIVE_SUFFIX.search(normalized))


def is_contextless_heading(value: str) -> bool:
    """Return true for labels that need their business object in a TOC."""

    normalized = re.sub(r"\s+", "", str(value or "")).strip("：:、，,。；;/-—")
    return normalized in {"使用说明", "操作说明", "实例", "案例", "方法", "分析"}


def is_document_quality_score(title: str, criterion: str = "") -> bool:
    """Return True only for a criterion that evaluates the document as a whole."""

    text = re.sub(r"\s+", "", f"{title} {criterion}")
    if not any(term in text for term in _DOCUMENT_TERMS):
        return False
    return any(term in text for term in _QUALITY_TERMS)


def document_quality_check_items(text: str) -> list[str]:
    """Project a whole-document scoring rule into deterministic QA dimensions."""

    mapping = (
        (("完整", "齐全", "覆盖"), "内容完整并逐项覆盖"),
        (("逻辑", "条理", "层次", "衔接"), "逻辑清楚、层次合理、章节衔接一致"),
        (("格式", "排版", "规范", "目录"), "格式、目录与排版符合招标要求"),
        (("图表", "图示", "流程图"), "图表清晰且与正文一致"),
        (("一致", "矛盾", "前后"), "术语、数据、承诺与引用前后一致"),
        (("针对", "可操作", "可行"), "方案具有项目针对性和可操作性"),
    )
    items = [label for terms, label in mapping if any(term in text for term in terms)]
    return items or ["按评分原文执行全文完整性、逻辑性、规范性和一致性核查"]


def document_quality_criteria(
    *,
    unit: Any,
    point: Any,
    conditions: dict[str, Any],
    condition_ids: list[str],
) -> list[str]:
    """Derive the immutable criterion text for one document-scoped unit."""

    criteria = [
        str(
            getattr(condition, "normalized_condition", "")
            or getattr(condition, "text", "")
        ).strip()
        for condition_id in condition_ids
        if (condition := conditions.get(condition_id)) is not None
    ]
    if not criteria:
        criteria = [
            str(getattr(unit, "response_expectation", "")).strip(),
            str(getattr(point, "criterion", "")).strip(),
        ]
    return list(dict.fromkeys(item for item in criteria if item))


def score_group_category(title: str) -> str:
    if any(token in title for token in ("价格", "报价")):
        return "price"
    if any(token in title for token in ("商务", "资信")):
        return "business"
    if any(token in title for token in ("技术", "方案", "服务")):
        return "technical"
    return "other"


def score_group_chapter_title(title: str, declared_points: object, category: str) -> str:
    base = {
        "price": "报价响应",
        "business": "商务评分响应",
        "technical": "技术方案",
    }.get(category)
    if base is None:
        cleaned = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", title).strip()
        base = f"{cleaned or '专项评分'}响应"
    qualifier_match = re.search(r"(明标|暗标)", title)
    qualifier = qualifier_match.group(1) if qualifier_match is not None else ""
    if isinstance(declared_points, (int, float)):
        points = f"{declared_points:g}"
        label = f"{qualifier}，{points}分" if qualifier else f"{points}分"
        return f"{base}（{label}）"
    return f"{base}（{qualifier}）" if qualifier else base


def score_point_chapter_title(title: str, fallback_index: int) -> str:
    """Create a concise, project-agnostic outline title from one scoring leaf."""

    cleaned = re.sub(r"^[\s\d一二三四五六七八九十、.．()（）-]+", "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:；;、")
    parts = re.split(r"[—:：]", cleaned, maxsplit=1)
    if len(parts) == 2:
        parent = parts[0].strip()
        detail = parts[1].strip()
        if detail.startswith(parent):
            detail = detail[len(parent) :].lstrip(" ：:—-")
        detail = re.split(r"[，,；;。]", detail, maxsplit=1)[0].strip()
        detail = _EVALUATIVE_CUE.split(detail, maxsplit=1)[0].strip()
        parent = parent if len(parent) <= 28 else ""
        if parent and detail:
            cleaned = f"{parent}—{detail[:28]}"
        else:
            cleaned = parent or detail
    else:
        cleaned = re.split(r"[，,；;。]", cleaned, maxsplit=1)[0].strip()
        cleaned = _EVALUATIVE_CUE.split(cleaned, maxsplit=1)[0].strip()
    return (cleaned or f"评分响应{fallback_index}")[:56].rstrip("，,；;：:")


def outline_subject(label: str) -> str:
    """Normalize a scoring-factor label for parent/leaf hierarchy comparison."""

    value = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", label)
    return re.sub(r"[\s：:；;、，,。.\-—]", "", value)


def outline_structure_key(label: str) -> str:
    """Normalize extraction noise without erasing scoring-table semantics."""

    value = unicodedata.normalize("NFKC", str(label or ""))
    return re.sub(r"\s+", "", value).strip()


def is_applicability_scope_heading(label: str) -> bool:
    """Return whether a path label describes bid-lot applicability, not a score factor."""

    value = outline_structure_key(label).strip("：:；;")
    if not value:
        return False
    if re.search(r"(?:适用范围|适用于|适用包|适用标段)", value):
        return True
    number = r"[0-9一二三四五六七八九十百]+"
    return bool(
        re.fullmatch(
            rf"(?:(?:第?{number})(?:包|标包|标段)|(?:包|标包|标段)第?{number})"
            rf"(?:至|到|[-~—–])"
            rf"(?:(?:第?{number})(?:包|标包|标段)|(?:包|标包|标段)第?{number})",
            value,
        )
    )


def score_leaf_title(title: str, parent_label: str, fallback_index: int) -> str:
    """Remove a repeated scoring-factor prefix once that factor is a parent node."""

    cleaned = score_point_chapter_title(title, fallback_index)
    parent = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", parent_label).strip()
    for separator in ("—", "：", ":"):
        prefix = f"{parent}{separator}"
        if parent and cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip() or cleaned
    return cleaned


def highest_score_conditions(
    criterion: str,
    levels: list[ScoringLevel],
    max_points: float | None,
) -> list[str]:
    """Preserve the highest scoring band as executable response conditions."""

    if max_points is not None:
        candidates = [
            level.criterion.strip()
            for level in levels
            if level.points is not None and abs(level.points - max_points) <= 1e-6
        ]
        if candidates:
            return _atomic_condition_clauses(candidates)
    text = re.sub(r"\s+", " ", criterion).strip()
    if not text:
        return []
    first_band = re.split(r"(?:得|计)\s*\d+(?:\.\d+)?\s*分", text, maxsplit=1)[0]
    return _atomic_condition_clauses([first_band.strip(" ；;。.")] or [text])


def full_score_condition_heading(condition: str, fallback_index: int) -> str:
    """Turn one highest-band condition into a concise subordinate heading."""

    text = re.sub(r"^[（(]?\s*\d+(?:\.\d+)?[）).、．]?\s*", "", condition)
    text = re.sub(r"(?:得|计)\s*\d+(?:\.\d+)?\s*分.*$", "", text).strip()
    instance_match = re.search(r"^对(.+?)(?:有|提供)(?:具体|典型)?实例", text)
    if instance_match is not None:
        subject = instance_match.group(1)
        subject = subject.replace("容易混淆的", "易混淆").replace("的类型", "类型")
        return f"{subject.strip()}判别实例与操作指引"[:40]
    heading = _HEADING_PREDICATE.split(text, maxsplit=1)[0]
    heading = heading.strip(" ，,；;、。.:：-—")
    # The generic predicate splitter deliberately preserves nouns ending in
    # “性” (for example “可行性分析”).  Strip only the remaining sentence-style
    # score suffixes after a concrete topic has been retained.
    heading = _HEADING_EVALUATIVE_SUFFIX.sub("", heading).strip(" ，,；;、。.:：-—")
    if len(heading) > 32:
        heading = re.split(r"[，,；;。]", heading, maxsplit=1)[0].strip()
    return (heading or f"满分条件{fallback_index}")[:40]


def audit_response_topic_graph(
    score_model: ScoreModel,
    graph: ResponseTopicGraph,
    requirement_ledger: RequirementLedger | None = None,
    project_model: ProjectModel | None = None,
    source_index: SourceIndex | None = None,
) -> dict[str, Any]:
    """G1 audit for exact refs, complete Duties and non-degenerate semantics."""

    findings: list[dict[str, str]] = []
    if graph.score_model_revision != score_model.revision:
        findings.append(
            _finding(
                "SCORE_GRAPH_REVISION_MISMATCH",
                f"TopicGraph score_model_revision={graph.score_model_revision}，当前 ScoreModel={score_model.revision}",
            )
        )
    if (
        requirement_ledger is not None
        and graph.requirement_ledger_revision != requirement_ledger.revision
    ):
        findings.append(
            _finding(
                "REQUIREMENT_GRAPH_REVISION_MISMATCH",
                "TopicGraph requirement_ledger_revision 与当前 RequirementLedger 不一致",
            )
        )
    if (
        project_model is not None
        and graph.project_model_revision != project_model.revision
    ):
        findings.append(
            _finding(
                "PROJECT_GRAPH_REVISION_MISMATCH",
                "TopicGraph project_model_revision 与当前 ProjectModel 不一致",
            )
        )

    expected_source_hashes: dict[str, str] = {}
    upstream_hash_sets = [("ScoreModel", score_model.source_hashes)]
    if requirement_ledger is not None:
        upstream_hash_sets.append(
            ("RequirementLedger", requirement_ledger.source_hashes)
        )
    if project_model is not None:
        upstream_hash_sets.append(("ProjectModel", project_model.source_hashes))
    for artifact_name, source_hashes in upstream_hash_sets:
        for source_id, source_hash in source_hashes.items():
            previous = expected_source_hashes.get(source_id)
            if previous is not None and previous != source_hash:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_UPSTREAM_SOURCE_CONFLICT",
                        f"{artifact_name} 对来源 {source_id} 的 hash 与其他上游冲突",
                    )
                )
            expected_source_hashes[source_id] = source_hash
    for source_id, source_hash in expected_source_hashes.items():
        if graph.source_hashes.get(source_id) != source_hash:
            findings.append(
                _finding(
                    "SCORE_GRAPH_SOURCE_MISMATCH",
                    f"TopicGraph 未绑定上游来源 {source_id} 的当前 hash",
                )
            )
    if source_index is not None:
        for source_id, source_hash in graph.source_hashes.items():
            if source_index.source_hashes.get(source_id) != source_hash:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_SOURCE_MISMATCH",
                        f"TopicGraph 来源 {source_id} 的 hash 无法由当前 SourceIndex 证明",
                    )
                )

    points = {point.score_point_id: point for point in score_model.points}
    score_unit_owner = {
        unit.unit_id: point.score_point_id
        for point in score_model.points
        for unit in point.response_units
    }
    score_units = {
        unit.unit_id: unit
        for point in score_model.points
        for unit in point.response_units
    }
    requirements = (
        {
            item.requirement_id: item
            for item in requirement_ledger.requirements
        }
        if requirement_ledger is not None
        else {}
    )
    active_requirement_ids = (
        {
            item.requirement_id
            for item in requirement_ledger.requirements
            if item.status not in {"blocked", "waived"}
        }
        if requirement_ledger is not None
        else set()
    )
    topics = {topic.topic_id: topic for topic in graph.topics}
    duties_by_score: dict[str, list[str]] = defaultdict(list)
    duties_by_score_unit: dict[str, list[str]] = defaultdict(list)
    duties_by_requirement: dict[str, list[str]] = defaultdict(list)
    duties_by_formal_ref: dict[str, list[str]] = defaultdict(list)
    evidence_need_ids = (
        {need.need_id for need in project_model.evidence_needs}
        if project_model is not None
        else set()
    )

    for duty in graph.duties:
        if len(duty.requirement_ids) != len(set(duty.requirement_ids)):
            findings.append(
                _finding(
                    "TOPIC_DUTY_DUPLICATE_REQUIREMENT",
                    f"Duty {duty.duty_id} 重复绑定同一 Requirement",
                )
            )
        if len(duty.score_point_ids) != len(set(duty.score_point_ids)):
            findings.append(
                _finding(
                    "TOPIC_DUTY_DUPLICATE_SCORE_POINT",
                    f"Duty {duty.duty_id} 重复绑定同一 ScorePoint",
                )
            )
        if len(duty.score_response_unit_ids) != len(
            set(duty.score_response_unit_ids)
        ):
            findings.append(
                _finding(
                    "TOPIC_DUTY_DUPLICATE_SCORE_UNIT",
                    f"Duty {duty.duty_id} 重复绑定同一 ScoreResponseUnit",
                )
            )
        if len(duty.score_response_unit_ids) > 1:
            findings.append(
                _finding(
                    "TOPIC_DUTY_SCORE_UNIT_COLLAPSED",
                    f"Duty {duty.duty_id} 压缩了多个独立得分任务",
                )
            )
        for unit_id in duty.score_response_unit_ids:
            owner_score_id = score_unit_owner.get(unit_id)
            if owner_score_id is None:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_UNKNOWN_SCORE_UNIT",
                        f"Duty {duty.duty_id} 指向未知 ScoreResponseUnit {unit_id}",
                    )
                )
                continue
            duties_by_score_unit[unit_id].append(duty.duty_id)
            if owner_score_id not in duty.score_point_ids:
                findings.append(
                    _finding(
                        "TOPIC_DUTY_SCORE_UNIT_OWNER_MISMATCH",
                        f"Duty {duty.duty_id} 的 ScoreResponseUnit {unit_id} "
                        f"属于 {owner_score_id}",
                    )
                )
        if len(duty.evidence_need_ids) != len(set(duty.evidence_need_ids)):
            findings.append(
                _finding(
                    "TOPIC_DUTY_DUPLICATE_EVIDENCE_NEED",
                    f"Duty {duty.duty_id} 重复绑定同一 EvidenceNeed",
                )
            )
        for requirement_id in duty.requirement_ids:
            duties_by_formal_ref[f"RequirementLedger:{requirement_id}"].append(
                duty.duty_id
            )
            if requirement_ledger is None:
                continue
            requirement = requirements.get(requirement_id)
            if requirement is None:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_UNKNOWN_REQUIREMENT",
                        f"Duty {duty.duty_id} 指向未知 Requirement {requirement_id}",
                    )
                )
            elif requirement_id not in active_requirement_ids:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_INACTIVE_REQUIREMENT_BINDING",
                        f"Duty {duty.duty_id} 绑定了非活动 Requirement {requirement_id}",
                    )
                )
            else:
                duties_by_requirement[requirement_id].append(duty.duty_id)
        for score_point_id in duty.score_point_ids:
            duties_by_formal_ref[f"ScoreModel:{score_point_id}"].append(
                duty.duty_id
            )
            duties_by_score[score_point_id].append(duty.duty_id)
            point = points.get(score_point_id)
            if point is None:
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_UNKNOWN_SCORE_POINT",
                        f"Duty {duty.duty_id} 指向未知 ScorePoint {score_point_id}",
                    )
                )
                continue
            if (
                point.response_units
                and not duty.score_response_unit_ids
            ):
                findings.append(
                    _finding(
                        "TOPIC_DUTY_SCORE_UNIT_MISSING",
                        f"Duty {duty.duty_id} 绑定 ScorePoint {score_point_id} "
                        "但未绑定其独立得分任务",
                    )
                )
            topic = topics.get(duty.topic_id)
            if topic is None:
                continue
            declared_group = str(
                topic.attributes.get("score_group_id") or ""
            )
            known_duty_groups = {
                points[item].group_id
                for item in duty.score_point_ids
                if item in points
            }
            if (
                declared_group
                and len(known_duty_groups) == 1
                and declared_group not in known_duty_groups
            ):
                findings.append(
                    _finding(
                        "TOPIC_GRAPH_SCORE_GROUP_MISMATCH",
                        f"Duty {duty.duty_id} 的评分组元数据与 ScoreModel 不一致",
                    )
                )
            expected_document_scope = (
                any(
                    score_units[unit_id].response_scope == "document"
                    for unit_id in duty.score_response_unit_ids
                    if unit_id in score_units
                )
                if duty.score_response_unit_ids
                else point.response_scope == "document"
            )
            if (
                expected_document_scope
                and topic.attributes.get("planning_role")
                not in {
                    "document_quality_gate",
                    "mixed_response_scope",
                }
            ):
                findings.append(
                    _finding(
                        "DOCUMENT_QUALITY_SCOPE_LOST",
                        f"全文评分点 {score_point_id} 未标记为 document_quality_gate",
                    )
                )
        if project_model is not None:
            for need_id in duty.evidence_need_ids:
                if need_id not in evidence_need_ids:
                    findings.append(
                        _finding(
                            "TOPIC_GRAPH_UNKNOWN_EVIDENCE_NEED",
                            f"Duty {duty.duty_id} 指向未知 EvidenceNeed {need_id}",
                        )
                    )

    for score_point_id in points:
        if not duties_by_score.get(score_point_id):
            findings.append(
                _finding(
                    "SCORE_POINT_WITHOUT_DUTY",
                    f"ScorePoint {score_point_id} 未生成 ResponseDuty",
                )
            )
    for unit_id in score_unit_owner:
        duty_ids = duties_by_score_unit.get(unit_id, [])
        if len(duty_ids) != 1:
            findings.append(
                _finding(
                    "SCORE_RESPONSE_UNIT_DUTY_CARDINALITY",
                    f"ScoreResponseUnit {unit_id} 必须且只能进入一个 Duty，"
                    f"当前为 {len(duty_ids)}",
                )
            )
    for requirement_id in active_requirement_ids:
        if not duties_by_requirement.get(requirement_id):
            findings.append(
                _finding(
                    "REQUIREMENT_WITHOUT_DUTY",
                    f"Requirement {requirement_id} 未生成 ResponseDuty",
                )
            )

    expected_roots = {
        topic.topic_id
        for topic in graph.topics
        if topic.parent_topic_id is None
    }
    declared_roots = set(graph.root_topic_ids)
    if len(graph.root_topic_ids) != len(declared_roots):
        findings.append(
            _finding("TOPIC_ROOT_DUPLICATE", "root_topic_ids 存在重复 Topic")
        )
    if declared_roots != expected_roots:
        findings.append(
            _finding(
                "TOPIC_ROOT_SET_MISMATCH",
                "root_topic_ids 与实际无父 Topic 集合不一致；"
                f"missing={sorted(expected_roots - declared_roots)}, "
                f"extra={sorted(declared_roots - expected_roots)}",
            )
        )

    _audit_topic_parent_cycles(graph, topics, findings)
    reference_catalog, formal_reference_owner = _topic_reference_catalog(
        score_model,
        requirement_ledger=requirement_ledger,
        project_model=project_model,
        source_index=source_index,
    )
    formal_refs_by_topic: dict[str, set[str]] = defaultdict(set)
    source_blocks = (
        {
            (block.input_id, block.source_anchor.chunk_id): block
            for block in source_index.blocks
        }
        if source_index is not None
        else {}
    )
    for topic in graph.topics:
        raw_refs = topic.attributes.get("upstream_refs", [])
        refs: list[str] = []
        if isinstance(raw_refs, list) and all(
            isinstance(item, str) and item.strip() for item in raw_refs
        ):
            refs = [str(item) for item in raw_refs]
        elif raw_refs:
            findings.append(
                _finding(
                    "TOPIC_UPSTREAM_REF_INVALID",
                    f"Topic {topic.topic_id} 的 upstream_refs 不是非空字符串列表",
                )
            )
        for ref in refs:
            if ref not in reference_catalog:
                findings.append(
                    _finding(
                        "TOPIC_UPSTREAM_REF_UNKNOWN",
                        f"Topic {topic.topic_id} 存在悬空上游引用 {ref}",
                    )
                )
            owner = formal_reference_owner.get(ref)
            if owner is not None:
                formal_refs_by_topic[topic.topic_id].add(owner)
        valid_anchor_count = 0
        for anchor in topic.source_anchors:
            if source_index is None:
                valid_anchor_count += 1
                continue
            block = source_blocks.get(
                (anchor.source_input_id, anchor.chunk_id)
            )
            if block is None:
                findings.append(
                    _finding(
                        "TOPIC_SOURCE_ANCHOR_UNKNOWN",
                        f"Topic {topic.topic_id} 的 SourceAnchor 无法解析",
                    )
                )
                continue
            if (
                graph.source_hashes.get(block.input_id)
                != source_index.source_hashes.get(block.input_id)
            ):
                findings.append(
                    _finding(
                        "TOPIC_SOURCE_ANCHOR_UNBOUND",
                        f"Topic {topic.topic_id} 的来源 {block.input_id} 未绑定当前 hash",
                    )
                )
                continue
            valid_anchor_count += 1
        valid_ref_count = sum(1 for ref in refs if ref in reference_catalog)
        if (
            topic.review_status == "confirmed"
            and valid_ref_count == 0
            and valid_anchor_count == 0
        ):
            findings.append(
                _finding(
                    "TOPIC_CONFIRMED_WITHOUT_VALID_SOURCE",
                    f"已确认 Topic {topic.topic_id} 没有有效来源或上游引用",
                )
            )

    for edge in graph.edges:
        if requirement_ledger is not None:
            for requirement_id in edge.requirement_ids:
                if requirement_id not in requirements:
                    findings.append(
                        _finding(
                            "TOPIC_EDGE_UNKNOWN_REQUIREMENT",
                            f"TopicEdge {edge.edge_id} 指向未知 Requirement {requirement_id}",
                        )
                    )
                elif requirement_id not in active_requirement_ids:
                    findings.append(
                        _finding(
                            "TOPIC_EDGE_INACTIVE_REQUIREMENT",
                            f"TopicEdge {edge.edge_id} 绑定非活动 Requirement {requirement_id}",
                        )
                    )
    _audit_dependency_cycles(graph, findings)

    formal_item_count = len(active_requirement_ids) + len(points)
    if _is_degenerate_one_to_one_graph(
        graph,
        formal_refs_by_topic=formal_refs_by_topic,
        formal_item_count=formal_item_count,
    ):
        findings.append(
            _finding(
                "TOPIC_GRAPH_DEGENERATE_ONE_TO_ONE",
                "TopicGraph 退化为“一 Requirement/Score 一个根 Topic”的规则投影",
            )
        )
    _audit_abnormal_duty_bindings(
        graph,
        formal_item_count=formal_item_count,
        duties_by_formal_ref=duties_by_formal_ref,
        legitimate_fanout_by_ref={
            f"ScoreModel:{point.score_point_id}": max(
                1,
                len(point.response_units),
            )
            for point in score_model.points
        },
        findings=findings,
    )

    return {
        "passed": not findings,
        "findings": findings,
        "requirement_count": len(active_requirement_ids),
        "score_point_count": len(points),
        "topic_count": len(graph.topics),
        "duty_count": len(graph.duties),
    }


def audit_chapter_blueprint(
    blueprint: ChapterBlueprint,
    planning_input: ResponseTopicGraph | RequirementLedger,
    score_model: ScoreModel | None = None,
    template_structure: TemplateStructureContract | None = None,
) -> dict[str, Any]:
    """Audit either the direct ScoreModel blueprint or the legacy graph path."""

    if isinstance(planning_input, RequirementLedger):
        if score_model is None:
            return {
                "passed": False,
                "findings": [
                    _finding(
                        "BLUEPRINT_SCORE_MODEL_MISSING",
                        "score_direct G2 必须提供 ScoreModel",
                    )
                ],
                "planning_model": "score_direct",
            }
        return _audit_chapter_blueprint_direct(
            blueprint,
            planning_input,
            score_model,
            template_structure=template_structure,
        )
    return _audit_chapter_blueprint_legacy(
        blueprint,
        planning_input,
        score_model=score_model,
        template_structure=template_structure,
    )


def _audit_chapter_blueprint_direct(
    blueprint: ChapterBlueprint,
    ledger: RequirementLedger,
    score_model: ScoreModel,
    *,
    template_structure: TemplateStructureContract | None,
) -> dict[str, Any]:
    """G2 direct mode: exact ID, tree and coverage checks only."""

    findings: list[dict[str, str]] = []
    if blueprint.planning_model not in {"score_direct", "rewrite_merge"}:
        findings.append(
            _finding(
                "BLUEPRINT_PLANNING_MODEL_MISMATCH",
                "RequirementLedger + ScoreModel G2 要求 score_direct 或 rewrite_merge 规划模型",
            )
        )
    if blueprint.requirement_ledger_revision != ledger.revision:
        findings.append(
            _finding(
                "BLUEPRINT_LEDGER_REVISION_MISMATCH",
                "Blueprint requirement_ledger_revision 与当前 Ledger 不一致",
            )
        )
    if blueprint.score_model_revision != score_model.revision:
        findings.append(
            _finding(
                "BLUEPRINT_SCORE_MODEL_REVISION_MISMATCH",
                "Blueprint score_model_revision 与当前 ScoreModel 不一致",
            )
        )
    expected_source_hashes = dict(ledger.source_hashes)
    for source_id, source_hash in score_model.source_hashes.items():
        existing = expected_source_hashes.get(source_id)
        if existing is not None and existing != source_hash:
            findings.append(
                _finding(
                    "BLUEPRINT_UPSTREAM_SOURCE_CONFLICT",
                    f"Ledger 与 ScoreModel 的来源 {source_id} hash 冲突",
                )
            )
        expected_source_hashes[source_id] = source_hash
    if blueprint.source_hashes != expected_source_hashes:
        findings.append(
            _finding(
                "BLUEPRINT_SOURCE_MISMATCH",
                "Blueprint 未精确绑定 Ledger + ScoreModel 来源 hash",
            )
        )

    points: dict[str, Any] = {}
    units: dict[str, Any] = {}
    unit_owner: dict[str, str] = {}
    conditions: dict[str, Any] = {}
    condition_owner_point: dict[str, str] = {}
    condition_owner_unit: dict[str, str] = {}
    condition_unit_counts: dict[str, int] = defaultdict(int)
    for point in score_model.points:
        if point.score_point_id in points:
            findings.append(
                _finding(
                    "SCORE_POINT_ID_DUPLICATE",
                    f"ScorePoint ID 非全局唯一: {point.score_point_id}",
                )
            )
        points[point.score_point_id] = point
        for condition in point.score_conditions:
            if condition.condition_id in conditions:
                findings.append(
                    _finding(
                        "SCORE_CONDITION_ID_DUPLICATE",
                        f"ScoreCondition ID 非全局唯一: {condition.condition_id}",
                    )
                )
            conditions[condition.condition_id] = condition
            condition_owner_point[
                condition.condition_id
            ] = point.score_point_id
        for unit in point.response_units:
            if unit.unit_id in units:
                findings.append(
                    _finding(
                        "SCORE_RESPONSE_UNIT_ID_DUPLICATE",
                        f"ScoreResponseUnit ID 非全局唯一: {unit.unit_id}",
                    )
                )
            units[unit.unit_id] = unit
            unit_owner[unit.unit_id] = point.score_point_id
            for condition_id in unit.condition_ids:
                condition_unit_counts[condition_id] += 1
                condition_owner_unit[condition_id] = unit.unit_id

    active_point_ids = {
        point.score_point_id
        for point in score_model.points
        if point.review_status != "blocked"
    }
    active_unit_ids = {
        unit_id
        for unit_id, point_id in unit_owner.items()
        if point_id in active_point_ids
        and units[unit_id].review_status != "blocked"
    }
    section_unit_ids = {
        unit_id
        for unit_id in active_unit_ids
        if units[unit_id].response_scope == "section"
    }
    document_unit_ids = {
        unit_id
        for unit_id in active_unit_ids
        if units[unit_id].response_scope == "document"
    }
    for point_id in active_point_ids:
        if not any(
            unit_owner.get(unit_id) == point_id
            for unit_id in active_unit_ids
        ):
            findings.append(
                _finding(
                    "SCORE_POINT_RESPONSE_UNIT_MISSING",
                    f"活动 ScorePoint {point_id} 缺少活动 ScoreResponseUnit",
                )
            )

    active_condition_ids = {
        condition_id
        for condition_id, condition in conditions.items()
        if condition.review_status != "blocked"
        and condition_owner_point.get(condition_id) in active_point_ids
    }
    visible_condition_ids: set[str] = set()
    document_condition_ids: set[str] = set()
    for condition_id in active_condition_ids:
        count = condition_unit_counts.get(condition_id, 0)
        if count != 1:
            findings.append(
                _finding(
                    "SCORE_CONDITION_UNIT_CARDINALITY",
                    f"condition_id {condition_id} 必须由且仅由一个 "
                    f"ScoreResponseUnit 绑定，当前为 {count}",
                )
            )
            continue
        unit_id = condition_owner_unit[condition_id]
        if unit_owner.get(unit_id) != condition_owner_point[condition_id]:
            findings.append(
                _finding(
                    "SCORE_CONDITION_UNIT_POINT_MISMATCH",
                    f"condition_id {condition_id} 与 Unit {unit_id} "
                    "不属于同一 ScorePoint",
                )
            )
        role = getattr(conditions[condition_id], "condition_role", "content")
        if role == "document" and unit_id not in document_unit_ids:
            findings.append(
                _finding(
                    "DOCUMENT_CONDITION_SCOPE_MISMATCH",
                    f"document condition {condition_id} 不属于 document Unit",
                )
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
    linked_requirement_ids = {
        str(requirement_id)
        for point in score_model.points
        if point.review_status != "blocked"
        for requirement_id in (
            *point.linked_requirement_ids,
            *point.context_requirement_ids,
            *(
                requirement_id
                for unit in point.response_units
                if unit.review_status != "blocked"
                for requirement_id in getattr(
                    unit,
                    "linked_requirement_ids",
                    [],
                )
            ),
        )
    }
    linked_requirements_by_unit = {
        unit_id: {
            str(requirement_id)
            for requirement_id in units[unit_id].linked_requirement_ids
        }
        for unit_id in active_unit_ids
    }
    section_linked_requirements_by_unit = {
        unit_id: {
            str(requirement_id)
            for requirement_id in units[unit_id].linked_requirement_ids
        }
        for unit_id in section_unit_ids
    }
    section_linked_requirement_ids = {
        requirement_id
        for requirement_ids in section_linked_requirements_by_unit.values()
        for requirement_id in requirement_ids
    }
    all_unit_linked_requirement_ids = {
        requirement_id
        for requirement_ids in linked_requirements_by_unit.values()
        for requirement_id in requirement_ids
    }
    if unknown_links := linked_requirement_ids - set(requirements):
        findings.append(
            _finding(
                "SCORE_REQUIREMENT_UNKNOWN",
                "ScoreModel 引用未知 Requirement: "
                f"{sorted(unknown_links)}",
            )
        )
    section_required_requirement_ids = (
        section_linked_requirement_ids & active_requirement_ids
    )
    required_requirement_ids = (
        all_unit_linked_requirement_ids & active_requirement_ids
    )

    nodes = {node.chapter_id: node for node in blueprint.nodes}
    for node in blueprint.nodes:
        if (
            node.parent_chapter_id is not None
            and node.parent_chapter_id not in nodes
        ):
            findings.append(
                _finding(
                    "BLUEPRINT_PARENT_MISSING",
                    f"章节 {node.chapter_id} 的父章节不存在",
                )
            )
    for node_id in nodes:
        seen: set[str] = set()
        cursor = node_id
        while cursor in nodes and nodes[cursor].parent_chapter_id:
            if cursor in seen:
                findings.append(
                    _finding(
                        "BLUEPRINT_PARENT_CYCLE",
                        f"章节树在 {cursor} 形成父子环",
                    )
                )
                break
            seen.add(cursor)
            cursor = str(nodes[cursor].parent_chapter_id)

    primary_chapters_by_unit: dict[str, list[str]] = defaultdict(list)
    condition_nodes: dict[str, list[str]] = defaultdict(list)
    covered_requirement_ids: set[str] = set()
    for node in blueprint.nodes:
        bound_units = {
            *node.primary_response_unit_ids,
            *node.supporting_response_unit_ids,
        }
        if unknown := bound_units - section_unit_ids:
            findings.append(
                _finding(
                    "BLUEPRINT_UNKNOWN_SECTION_UNIT",
                    f"章节 {node.chapter_id} 绑定未知、blocked 或 document Unit: "
                    f"{sorted(unknown)}",
                )
            )
        expected_score_ids = {
            unit_owner[unit_id]
            for unit_id in bound_units
            if unit_id in unit_owner
        }
        if set(node.score_point_ids) != expected_score_ids:
            findings.append(
                _finding(
                    "BLUEPRINT_SCORE_POINT_DERIVATION_MISMATCH",
                    f"章节 {node.chapter_id} 的 score_point_ids "
                    "未由其 Unit 绑定精确派生",
                )
            )
        if unknown := set(node.requirement_ids) - active_requirement_ids:
            findings.append(
                _finding(
                    "BLUEPRINT_UNKNOWN_REQUIREMENT",
                    f"章节 {node.chapter_id} 绑定未知或非活动 Requirement: "
                    f"{sorted(unknown)}",
                )
            )
        covered_requirement_ids.update(node.requirement_ids)
        for unit_id in node.primary_response_unit_ids:
            primary_chapters_by_unit[unit_id].append(node.chapter_id)
        for condition_id in node.score_condition_ids:
            condition_nodes[condition_id].append(node.chapter_id)
            if condition_id not in visible_condition_ids:
                findings.append(
                    _finding(
                        "BLUEPRINT_UNKNOWN_SCORE_CONDITION",
                        f"章节 {node.chapter_id} 绑定未知或全文级 condition_id "
                        f"{condition_id}",
                    )
                )
    for unit_id in section_unit_ids:
        count = len(primary_chapters_by_unit.get(unit_id, []))
        if count != 1:
            findings.append(
                _finding(
                    "RESPONSE_UNIT_PRIMARY_CARDINALITY",
                    f"section Unit {unit_id} 必须且只能有一个 primary，当前为 {count}",
                )
            )
    for condition_id, chapter_ids in condition_nodes.items():
        if len(chapter_ids) != 1:
            findings.append(
                _finding(
                    "SCORE_CONDITION_MULTIPLE_BINDINGS",
                    f"condition_id {condition_id} 被重复绑定: {chapter_ids}",
                )
            )
    if missing := visible_condition_ids - set(condition_nodes):
        findings.append(
            _finding(
                "SCORE_CONDITION_COVERAGE_MISSING",
                f"可见评分条件未完整覆盖: {sorted(missing)}",
            )
        )
    if missing := section_required_requirement_ids - covered_requirement_ids:
        findings.append(
            _finding(
                "REQUIREMENT_COVERAGE_MISSING",
                "目录遗漏评分关联 Requirement: "
                f"{sorted(missing)}",
            )
        )

    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for node in blueprint.nodes:
        if node.parent_chapter_id is not None:
            children_by_parent[node.parent_chapter_id].append(
                node.chapter_id
            )

    if template_structure is None:
        def root_for(chapter_id: str) -> str | None:
            current = chapter_id
            seen: set[str] = set()
            while current in nodes and nodes[current].parent_chapter_id is not None:
                if current in seen:
                    return None
                seen.add(current)
                current = str(nodes[current].parent_chapter_id)
            return current if current in nodes else None

        def path_titles(chapter_id: str) -> list[str]:
            chain: list[str] = []
            current = chapter_id
            seen: set[str] = set()
            while current in nodes and current not in seen:
                seen.add(current)
                chain.append(nodes[current].title)
                parent_id = nodes[current].parent_chapter_id
                if parent_id is None:
                    break
                current = parent_id
            return list(reversed(chain))

        expected_groups = [
            group
            for group in score_model.groups
            if any(
                unit_id in section_unit_ids
                and points[unit_owner[unit_id]].group_id == group.group_id
                for unit_id in section_unit_ids
            )
        ]
        actual_roots = sorted(
            (
                node
                for node in blueprint.nodes
                if node.parent_chapter_id is None
            ),
            key=lambda node: node.order,
        )
        if expected_groups and len(actual_roots) != len(expected_groups):
            findings.append(
                _finding(
                    "SCORE_GROUP_ROOT_CARDINALITY",
                    "自动目录的评分组根章节数量与含 section Unit 的评分组不一致",
                )
            )
        if expected_groups and [node.title for node in actual_roots] != [
            group.title for group in expected_groups
        ]:
            findings.append(
                _finding(
                    "SCORE_GROUP_ROOT_ORDER_MISMATCH",
                    "自动目录根章节必须按 ScoreModel.groups 顺序保留评分组来源标题",
                )
            )
        for group in expected_groups:
            group_unit_ids = {
                unit_id
                for unit_id in section_unit_ids
                if points[unit_owner[unit_id]].group_id == group.group_id
            }
            group_roots = {
                root_for(primary_chapters_by_unit[unit_id][0])
                for unit_id in group_unit_ids
                if len(primary_chapters_by_unit.get(unit_id, [])) == 1
            }
            if len(group_roots) != 1 or None in group_roots:
                findings.append(
                    _finding(
                        "SCORE_GROUP_ROOT_MISSING_OR_MIXED",
                        f"评分组 {group.title} 的 Unit 未恰好归入一个独立根子树",
                    )
                )
                continue
            root_id = next(iter(group_roots))
            root_node = nodes.get(root_id)
            if root_node is None or root_node.title != group.title:
                findings.append(
                    _finding(
                        "SCORE_GROUP_ROOT_TITLE_MISMATCH",
                        f"评分组 {group.title} 未保留为其独立根章节标题",
                    )
                )
            for unit_id in group_unit_ids:
                primaries = primary_chapters_by_unit.get(unit_id, [])
                if len(primaries) != 1:
                    continue
                point = points[unit_owner[unit_id]]
                expected_path = [
                    str(title).strip()
                    for title in point.outline_path
                    if str(title).strip()
                ]
                if (
                    expected_path
                    and outline_structure_key(expected_path[0])
                    == outline_structure_key(group.title)
                ):
                    expected_path.pop(0)
                compact_path: list[str] = []
                for label in expected_path:
                    if is_applicability_scope_heading(label):
                        continue
                    if (
                        not compact_path
                        or outline_structure_key(compact_path[-1])
                        != outline_structure_key(label)
                    ):
                        compact_path.append(label)
                if not compact_path:
                    continue
                actual_path = path_titles(primaries[0])[1:]
                actual_keys = [outline_structure_key(label) for label in actual_path]
                expected_keys = [
                    outline_structure_key(label) for label in compact_path
                ]
                if actual_keys != expected_keys:
                    findings.append(
                        _finding(
                            "OUTLINE_PATH_HIERARCHY_MISSING",
                            f"ScoreResponseUnit {unit_id} 未保留 outline_path: {compact_path}",
                        )
                    )
    covered_score_condition_ids: set[str] = set()
    for unit_id in section_unit_ids:
        primaries = primary_chapters_by_unit.get(unit_id, [])
        if len(primaries) != 1:
            continue
        subtree = _chapter_subtree(primaries[0], children_by_parent)
        covered_in_subtree = {
            requirement_id
            for chapter_id in subtree
            for requirement_id in nodes[chapter_id].requirement_ids
        }
        required_for_unit = (
            section_linked_requirements_by_unit.get(unit_id, set())
            & active_requirement_ids
        )
        if missing := required_for_unit - covered_in_subtree:
            findings.append(
                _finding(
                    "UNIT_REQUIREMENT_OUTSIDE_PRIMARY_SUBTREE",
                    f"ScoreResponseUnit {unit_id} 的关联 Requirement "
                    "未进入其 primary 子树: "
                    f"{sorted(missing)}",
                )
            )

    for condition_id in visible_condition_ids:
        unit_id = condition_owner_unit.get(condition_id)
        primaries = primary_chapters_by_unit.get(unit_id or "", [])
        if len(primaries) != 1:
            continue
        subtree = _chapter_subtree(primaries[0], children_by_parent)
        bound_nodes = set(condition_nodes.get(condition_id, []))
        if not bound_nodes:
            continue
        if not bound_nodes <= subtree:
            findings.append(
                _finding(
                    "SCORE_CONDITION_OUTSIDE_PRIMARY_SUBTREE",
                    f"condition_id {condition_id} 未落在 Unit {unit_id} "
                    "的 primary 子树",
                )
            )
        else:
            covered_score_condition_ids.add(condition_id)
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
            and bound_nodes != {primaries[0]}
        ):
            findings.append(
                _finding(
                    "QUALITY_CONDITION_STANDALONE_CHAPTER",
                    f"quality condition {condition_id} 必须绑定 Unit "
                    f"{unit_id} 的 primary 章节并转为写作要求",
                )
            )
        if role == "quality" and not sectionable_quality:
            expected_objective = str(
                getattr(conditions[condition_id], "response_intent", "")
                or getattr(
                    conditions[condition_id],
                    "normalized_condition",
                    "",
                )
                or getattr(conditions[condition_id], "text", "")
            )
            primary_node = nodes.get(primaries[0])
            if (
                expected_objective
                and primary_node is not None
                and expected_objective
                not in primary_node.writing_objectives
            ):
                findings.append(
                    _finding(
                        "QUALITY_CONDITION_OBJECTIVE_MISSING",
                        f"quality condition {condition_id} 未进入 Unit "
                        f"{unit_id} primary 章节的 writing_objectives",
                    )
                )

    gates_by_unit: dict[str, list[Any]] = defaultdict(list)
    gate_covered_requirement_ids: set[str] = set()
    for gate in blueprint.document_quality_gates:
        if gate.duty_id is not None:
            findings.append(
                _finding(
                    "DIRECT_GATE_LEGACY_DUTY_PRESENT",
                    f"score_direct 质量门 {gate.gate_id} 不应绑定 legacy Duty",
                )
            )
        for unit_id in gate.response_unit_ids:
            gates_by_unit[unit_id].append(gate)
            if unit_id not in document_unit_ids:
                findings.append(
                    _finding(
                        "DOCUMENT_QUALITY_GATE_UNKNOWN_UNIT",
                        f"质量门 {gate.gate_id} 绑定非 document Unit {unit_id}",
                    )
                )
    for unit_id in document_unit_ids:
        unit_gates = gates_by_unit.get(unit_id, [])
        if len(unit_gates) != 1:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_GATE_CARDINALITY",
                    f"document Unit {unit_id} 必须且只能有一个质量门，当前为 "
                    f"{len(unit_gates)}",
                )
            )
            continue
        gate = unit_gates[0]
        if gate.response_unit_ids != [unit_id]:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_GATE_UNIT_MISMATCH",
                    f"质量门 {gate.gate_id} 必须仅绑定 document Unit "
                    f"{unit_id}",
                )
            )
        expected_condition_ids = {
            condition_id
            for condition_id in units[unit_id].condition_ids
            if condition_id in active_condition_ids
        }
        ordered_condition_ids = sorted(expected_condition_ids)
        expected_requirement_ids = sorted(
            linked_requirements_by_unit.get(unit_id, set())
            & active_requirement_ids
        )
        expected_criteria = document_quality_criteria(
            unit=units[unit_id],
            point=points[unit_owner[unit_id]],
            conditions=conditions,
            condition_ids=ordered_condition_ids,
        )
        expected_check_items = document_quality_check_items(
            " ".join(expected_criteria)
        )
        if set(gate.score_point_ids) != {unit_owner[unit_id]}:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_SCORE_MISMATCH",
                    f"质量门 {gate.gate_id} 未精确绑定 Unit 的 ScorePoint",
                )
            )
        if set(gate.score_condition_ids) != expected_condition_ids:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_CONDITION_MISMATCH",
                    f"质量门 {gate.gate_id} 未精确绑定 Unit 的 condition_id",
                )
            )
        if gate.requirement_ids != expected_requirement_ids:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_REQUIREMENT_MISMATCH",
                    f"质量门 {gate.gate_id} 未精确绑定 Unit 的 Requirement",
                )
            )
        if gate.criteria != expected_criteria:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_CRITERIA_MISMATCH",
                    f"质量门 {gate.gate_id} 的 criteria 未由满分条件精确派生",
                )
            )
        if gate.check_items != expected_check_items:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_CHECK_ITEMS_MISMATCH",
                    f"质量门 {gate.gate_id} 的 check_items 未由 criteria "
                    "精确派生",
                )
            )
        gate_covered_requirement_ids.update(gate.requirement_ids)
        covered_score_condition_ids.update(
            set(gate.score_condition_ids) & document_condition_ids
        )
    all_covered_requirement_ids = (
        covered_requirement_ids | gate_covered_requirement_ids
    )
    if missing := required_requirement_ids - all_covered_requirement_ids:
        findings.append(
            _finding(
                "REQUIREMENT_COVERAGE_MISSING",
                "目录或全文质量门遗漏评分关联 Requirement: "
                f"{sorted(missing)}",
            )
        )
    if blueprint.assignments:
        findings.append(
            _finding(
                "DIRECT_BLUEPRINT_LEGACY_ASSIGNMENTS_PRESENT",
                "score_direct Blueprint 不应再持久化 Duty assignments",
            )
        )

    summary_points = blueprint.coverage_summary.get("score_group_points")
    expected_group_points = {
        group.group_id: group.declared_points
        for group in score_model.groups
        if group.declared_points is not None
    }
    if summary_points != expected_group_points:
        findings.append(
            _finding(
                "SCORE_GROUP_POINTS_SUMMARY_MISMATCH",
                "coverage_summary 未精确保留评分组分值",
            )
        )
    _audit_template_structure(
        blueprint,
        template_structure=template_structure,
        findings=findings,
    )
    return {
        "passed": not findings,
        "findings": findings,
        "planning_model": "score_direct",
        "response_unit_count": len(active_unit_ids),
        "primary_response_unit_count": len(primary_chapters_by_unit),
        "score_point_count": len(active_point_ids),
        "score_condition_count": len(active_condition_ids),
        "covered_score_condition_count": len(
            covered_score_condition_ids
        ),
        "required_requirement_count": len(required_requirement_ids),
        "covered_required_requirement_count": len(
            required_requirement_ids & all_covered_requirement_ids
        ),
        "document_quality_gate_count": len(
            blueprint.document_quality_gates
        ),
    }


def _audit_chapter_blueprint_legacy(
    blueprint: ChapterBlueprint,
    graph: ResponseTopicGraph,
    score_model: ScoreModel | None = None,
    template_structure: TemplateStructureContract | None = None,
) -> dict[str, Any]:
    """G2 audit for a score-driven, source-bound and usable chapter blueprint."""

    findings: list[dict[str, str]] = []
    if blueprint.topic_graph_revision != graph.revision:
        findings.append(
            _finding(
                "BLUEPRINT_GRAPH_REVISION_MISMATCH",
                f"Blueprint topic_graph_revision={blueprint.topic_graph_revision}，当前 TopicGraph={graph.revision}",
            )
        )
    if blueprint.source_hashes != graph.source_hashes:
        findings.append(
            _finding("BLUEPRINT_SOURCE_MISMATCH", "Blueprint 与 TopicGraph 的来源 hash 不一致")
        )

    nodes = {node.chapter_id: node for node in blueprint.nodes}
    duties = {duty.duty_id: duty for duty in graph.duties}
    topics = {topic.topic_id: topic for topic in graph.topics}
    roots = {node.chapter_id for node in blueprint.nodes if node.parent_chapter_id is None}
    audit_score_points = (
        {point.score_point_id: point for point in score_model.points}
        if score_model is not None
        else {}
    )
    audit_score_units = {
        unit.unit_id: unit
        for point in audit_score_points.values()
        for unit in point.response_units
    }
    quality_duties = {}
    for duty in graph.duties:
        topic = topics.get(duty.topic_id)
        quality_by_topic = (
            not duty.score_response_unit_ids
            and topic is not None
            and topic.attributes.get("planning_role")
            == "document_quality_gate"
        )
        quality_by_score = any(
            unit_id in audit_score_units
            and audit_score_units[unit_id].response_scope
            == "document"
            for unit_id in duty.score_response_unit_ids
        ) or (
            not duty.score_response_unit_ids
            and any(
                score_point_id in audit_score_points
                and audit_score_points[score_point_id].response_scope
                == "document"
                for score_point_id in duty.score_point_ids
            )
        )
        if quality_by_topic or quality_by_score:
            quality_duties[duty.duty_id] = duty
    quality_condition_ids_by_duty: dict[str, set[str]] = {}
    for duty_id, duty in quality_duties.items():
        condition_ids: set[str] = set()
        if duty.score_response_unit_ids:
            for unit_id in duty.score_response_unit_ids:
                unit = audit_score_units.get(unit_id)
                if unit is not None:
                    condition_ids.update(unit.condition_ids)
        else:
            for score_point_id in duty.score_point_ids:
                point = audit_score_points.get(score_point_id)
                if point is not None:
                    condition_ids.update(
                        condition.condition_id
                        for condition in point.score_conditions
                    )
        quality_condition_ids_by_duty[duty_id] = condition_ids

    for node in blueprint.nodes:
        if node.parent_chapter_id and node.parent_chapter_id not in nodes:
            findings.append(
                _finding("BLUEPRINT_PARENT_MISSING", f"章节 {node.chapter_id} 的父章节不存在")
            )
    for node_id in nodes:
        seen: set[str] = set()
        cursor = node_id
        while cursor in nodes and nodes[cursor].parent_chapter_id:
            if cursor in seen:
                findings.append(_finding("BLUEPRINT_PARENT_CYCLE", f"章节树在 {cursor} 形成父子环"))
                break
            seen.add(cursor)
            cursor = str(nodes[cursor].parent_chapter_id)

    primaries_by_duty: dict[str, list[str]] = defaultdict(list)
    for assignment in blueprint.assignments:
        if assignment.duty_id not in duties:
            findings.append(
                _finding(
                    "BLUEPRINT_UNKNOWN_DUTY",
                    f"Assignment {assignment.assignment_id} 指向未知 Duty {assignment.duty_id}",
                )
            )
            continue
        if assignment.role == "primary":
            primaries_by_duty[assignment.duty_id].append(assignment.chapter_id)

    core_duties = [
        duty
        for duty in graph.duties
        if duty.review_status != "blocked"
        and duty.duty_id not in quality_duties
    ]
    for duty in core_duties:
        count = len(primaries_by_duty.get(duty.duty_id, []))
        if count != 1:
            findings.append(
                _finding(
                    "DUTY_PRIMARY_CARDINALITY",
                    f"核心 Duty {duty.duty_id} 必须且只能有一个 primary，当前为 {count}",
                )
            )

    primary_chapters_by_score: dict[str, set[str]] = defaultdict(set)
    graph_score_ids = {
        score_point_id
        for duty in graph.duties
        for score_point_id in duty.score_point_ids
    }
    for duty in graph.duties:
        for chapter_id in primaries_by_duty.get(duty.duty_id, []):
            node = nodes.get(chapter_id)
            for score_point_id in duty.score_point_ids:
                primary_chapters_by_score[score_point_id].add(chapter_id)
                if node is not None and score_point_id not in node.required_mentions:
                    findings.append(
                        _finding(
                            "SCORE_POINT_NOT_MENTIONED_BY_PRIMARY",
                            f"ScorePoint {score_point_id} 的主责章节未声明该评分点",
                        )
                    )

    quality_gates_by_duty = {
        gate.duty_id: gate for gate in blueprint.document_quality_gates
    }
    for duty_id, duty in quality_duties.items():
        primary_ids = primaries_by_duty.get(duty_id, [])
        if primary_ids:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_AS_VISIBLE_CHAPTER",
                    f"全文质量评分 Duty {duty_id} 不得绑定可见 primary 章节",
                )
            )
        gate = quality_gates_by_duty.get(duty_id)
        if gate is None:
            findings.append(
                _finding("DOCUMENT_QUALITY_GATE_MISSING", f"全文质量评分 Duty {duty_id} 未进入全文质量门")
            )
        elif set(gate.score_point_ids) != set(duty.score_point_ids):
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_SCORE_MISMATCH",
                    f"全文质量门 {gate.gate_id} 未完整绑定其 ScorePoint",
                )
            )
        elif set(gate.score_condition_ids) != quality_condition_ids_by_duty[
            duty_id
        ]:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_CONDITION_MISMATCH",
                    f"全文质量门 {gate.gate_id} 未精确绑定其评分条件",
                )
            )
    for gate in blueprint.document_quality_gates:
        if gate.duty_id not in quality_duties:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_GATE_UNKNOWN",
                    f"全文质量门 {gate.gate_id} 未绑定 document_quality_gate Duty",
                )
            )
    for node in blueprint.nodes:
        if node.parent_chapter_id is not None and "document_quality_gate" in node.required_mentions:
            findings.append(
                _finding(
                    "DOCUMENT_QUALITY_MARKER_ON_CHILD",
                    f"全文质量门标记不得放在独立子章节 {node.chapter_id}",
                )
            )

    score_condition_count = 0
    covered_score_condition_ids: set[str] = set()
    if score_model is not None:
        if graph.score_model_revision != score_model.revision:
            findings.append(
                _finding(
                    "BLUEPRINT_SCORE_MODEL_REVISION_MISMATCH",
                    "G2 使用的 ScoreModel 与 TopicGraph revision 不一致",
                )
            )
        condition_owner: dict[str, str] = {}
        condition_owner_unit: dict[str, str] = {}
        duplicate_condition_ids: set[str] = set()
        points = {point.score_point_id: point for point in score_model.points}
        for point in score_model.points:
            for unit in point.response_units:
                for condition_id in unit.condition_ids:
                    if condition_id in condition_owner_unit:
                        findings.append(
                            _finding(
                                "SCORE_CONDITION_UNIT_CARDINALITY",
                                f"condition_id {condition_id} 被多个 "
                                "ScoreResponseUnit 绑定",
                            )
                        )
                    condition_owner_unit[condition_id] = unit.unit_id
            for condition in point.score_conditions:
                if condition.condition_id in condition_owner:
                    duplicate_condition_ids.add(condition.condition_id)
                condition_owner[condition.condition_id] = point.score_point_id
        score_condition_count = len(condition_owner)
        for condition_id in sorted(duplicate_condition_ids):
            findings.append(
                _finding(
                    "SCORE_CONDITION_ID_DUPLICATE",
                    f"ScoreModel condition_id 非全局唯一: {condition_id}",
                )
            )

        condition_nodes: dict[str, list[str]] = defaultdict(list)
        for node in blueprint.nodes:
            if len(node.score_condition_ids) != len(
                set(node.score_condition_ids)
            ):
                findings.append(
                    _finding(
                        "SCORE_CONDITION_NODE_DUPLICATE",
                        f"章节 {node.chapter_id} 重复声明 condition_id",
                    )
                )
            for condition_id in node.score_condition_ids:
                condition_nodes[condition_id].append(node.chapter_id)
                if condition_id not in condition_owner:
                    findings.append(
                        _finding(
                            "BLUEPRINT_UNKNOWN_SCORE_CONDITION",
                            f"章节 {node.chapter_id} 指向未知 condition_id {condition_id}",
                        )
                    )
        for condition_id, chapter_ids in condition_nodes.items():
            if len(chapter_ids) > 1:
                findings.append(
                    _finding(
                        "SCORE_CONDITION_MULTIPLE_BINDINGS",
                        f"condition_id {condition_id} 被多个章节重复绑定: {chapter_ids}",
                    )
                )

        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for node in blueprint.nodes:
            if node.parent_chapter_id is not None:
                children_by_parent[node.parent_chapter_id].append(
                    node.chapter_id
                )
        quality_condition_ids = {
            condition_id
            for condition_ids in quality_condition_ids_by_duty.values()
            for condition_id in condition_ids
        }
        quality_gate_condition_ids = {
            condition_id
            for gate in blueprint.document_quality_gates
            for condition_id in gate.score_condition_ids
        }
        duties_by_score_unit: dict[str, list[str]] = defaultdict(list)
        for duty in graph.duties:
            for unit_id in duty.score_response_unit_ids:
                duties_by_score_unit[unit_id].append(duty.duty_id)
        for condition_id, score_point_id in condition_owner.items():
            if condition_id in quality_condition_ids:
                if condition_nodes.get(condition_id):
                    findings.append(
                        _finding(
                            "DOCUMENT_QUALITY_CONDITION_VISIBLE",
                            f"全文质量 condition_id {condition_id} 不得变成可见章节绑定",
                        )
                    )
                if condition_id in quality_gate_condition_ids:
                    covered_score_condition_ids.add(condition_id)
                else:
                    findings.append(
                        _finding(
                            "SCORE_CONDITION_COVERAGE_MISSING",
                            f"全文质量 ScorePoint {score_point_id} 的 "
                            f"condition_id {condition_id} 未由全文质量门承接",
                        )
                    )
                continue

            unit_id = condition_owner_unit.get(condition_id)
            unit_duties = duties_by_score_unit.get(unit_id or "", [])
            if unit_id is None or len(unit_duties) != 1:
                findings.append(
                    _finding(
                        "SCORE_CONDITION_DUTY_CHAIN_MISSING",
                        f"condition_id {condition_id} 缺少唯一 "
                        "ScoreResponseUnit/Duty 链路",
                    )
                )
                continue
            duty_id = unit_duties[0]
            primary_chapters = primaries_by_duty.get(duty_id, [])
            if len(primary_chapters) != 1:
                findings.append(
                    _finding(
                        "SCORE_CONDITION_DUTY_CHAIN_MISSING",
                        f"condition_id {condition_id} 对应 Duty {duty_id} "
                        "缺少唯一 primary",
                    )
                )
                continue
            primary_chapter_id = primary_chapters[0]
            subtree = _chapter_subtree(
                primary_chapter_id,
                children_by_parent,
            )
            bound_nodes = set(condition_nodes.get(condition_id, []))
            if not bound_nodes:
                findings.append(
                    _finding(
                        "SCORE_CONDITION_COVERAGE_MISSING",
                        f"Duty {duty_id} 的主责章节子树遗漏 "
                        f"condition_id {condition_id}",
                    )
                )
            elif not (bound_nodes & subtree):
                findings.append(
                    _finding(
                        "SCORE_CONDITION_OUTSIDE_PRIMARY_SUBTREE",
                        f"condition_id {condition_id} 未落在 Duty {duty_id} "
                        "的主责章节子树",
                    )
                )
            else:
                covered_score_condition_ids.add(condition_id)

        groups = {group.group_id: group for group in score_model.groups}
        summary_points = blueprint.coverage_summary.get("score_group_points")
        for score_point_id, chapter_ids in primary_chapters_by_score.items():
            point = points.get(score_point_id)
            if point is None or len(chapter_ids) != 1:
                continue
            group = groups.get(point.group_id)
            if group is None or not isinstance(
                group.declared_points,
                (int, float),
            ):
                continue
            chapter_id = next(iter(chapter_ids))
            lineage_titles = _chapter_lineage_titles(chapter_id, nodes)
            summary_value = (
                summary_points.get(group.group_id)
                if isinstance(summary_points, dict)
                else None
            )
            preserved_in_summary = (
                isinstance(summary_value, (int, float))
                and abs(float(summary_value) - float(group.declared_points))
                <= 1e-6
            )
            points_label = f"{group.declared_points:g}分"
            if not preserved_in_summary and not any(
                points_label in title for title in lineage_titles
            ):
                findings.append(
                    _finding(
                        "SCORE_GROUP_POINTS_LOST",
                        f"ScorePoint {score_point_id} 的章节路径未保留评分组 {points_label}",
                    )
                )
    else:
        # Backward-compatible G2 callers can still validate structural duties.
        # Condition-title equality is intentionally not used: titles are model
        # authored; stable condition IDs are the only valid coverage key.
        for duty in graph.duties:
            if (
                len(duty.score_point_ids) != 1
                or not primaries_by_duty.get(duty.duty_id)
            ):
                continue
            topic = topics.get(duty.topic_id)
            if topic is None:
                continue
            declared = topic.attributes.get("score_group_declared_points")
            if not isinstance(declared, (int, float)):
                continue
            chapter_id = primaries_by_duty[duty.duty_id][0]
            lineage_titles = _chapter_lineage_titles(chapter_id, nodes)
            points_label = f"{declared:g}分"
            group_id = str(topic.attributes.get("score_group_id") or "")
            summary_points = blueprint.coverage_summary.get(
                "score_group_points"
            )
            summary_value = (
                summary_points.get(group_id)
                if isinstance(summary_points, dict)
                else None
            )
            preserved_in_summary = (
                isinstance(summary_value, (int, float))
                and abs(float(summary_value) - float(declared)) <= 1e-6
            )
            if not preserved_in_summary and not any(
                points_label in title for title in lineage_titles
            ):
                findings.append(
                    _finding(
                        "SCORE_GROUP_POINTS_LOST",
                        f"ScorePoint {duty.score_point_ids[0]} 的章节路径未保留评分组 {points_label}",
                    )
                )

    _audit_template_structure(
        blueprint,
        template_structure=template_structure,
        findings=findings,
    )

    return {
        "passed": not findings,
        "findings": findings,
        "duty_count": len(graph.duties),
        "primary_duty_count": len(primaries_by_duty),
        "score_point_count": len(graph_score_ids),
        "score_condition_count": score_condition_count,
        "covered_score_condition_count": len(covered_score_condition_ids),
        "document_quality_gate_count": len(blueprint.document_quality_gates),
    }


def _topic_reference_catalog(
    score_model: ScoreModel,
    *,
    requirement_ledger: RequirementLedger | None,
    project_model: ProjectModel | None,
    source_index: SourceIndex | None,
) -> tuple[set[str], dict[str, str]]:
    catalog: set[str] = set()
    formal_owner: dict[str, str] = {}
    if requirement_ledger is not None:
        for requirement in requirement_ledger.requirements:
            ref = f"RequirementLedger:{requirement.requirement_id}"
            catalog.add(ref)
            formal_owner[ref] = ref
    for group in score_model.groups:
        catalog.add(f"ScoreModel:{group.group_id}")
    for point in score_model.points:
        point_ref = f"ScoreModel:{point.score_point_id}"
        catalog.add(point_ref)
        formal_owner[point_ref] = point_ref
        for condition in point.score_conditions:
            ref = f"ScoreModel:{condition.condition_id}"
            catalog.add(ref)
            formal_owner[ref] = point_ref
        for unit in point.response_units:
            ref = f"ScoreModel:{unit.unit_id}"
            catalog.add(ref)
            formal_owner[ref] = point_ref
    if project_model is not None:
        catalog.add(f"ProjectModel:{project_model.project_id}")
        for fact in (
            *project_model.confirmed_facts,
            *project_model.inferences,
            *project_model.conflicts,
        ):
            catalog.add(f"ProjectModel:{fact.fact_id}")
    if source_index is not None:
        for block in source_index.blocks:
            catalog.add(f"SourceIndex:{block.block_id}")
            catalog.add(
                f"SourceIndex:{block.input_id}:{block.source_anchor.chunk_id}"
            )
    return catalog, formal_owner


def _audit_topic_parent_cycles(
    graph: ResponseTopicGraph,
    topics: dict[str, object],
    findings: list[dict[str, str]],
) -> None:
    reported: set[str] = set()
    for topic in graph.topics:
        seen: set[str] = set()
        cursor: str | None = topic.topic_id
        while cursor is not None and cursor in topics:
            if cursor in seen:
                if cursor not in reported:
                    findings.append(
                        _finding(
                            "TOPIC_PARENT_CYCLE",
                            f"Topic 父子层级在 {cursor} 形成环",
                        )
                    )
                    reported.add(cursor)
                break
            seen.add(cursor)
            cursor = getattr(topics[cursor], "parent_topic_id", None)


def _audit_dependency_cycles(
    graph: ResponseTopicGraph,
    findings: list[dict[str, str]],
) -> None:
    dependencies: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.relation == "depends_on":
            dependencies[edge.source_topic_id].add(edge.target_topic_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(topic_id: str) -> bool:
        if topic_id in visiting:
            return True
        if topic_id in visited:
            return False
        visiting.add(topic_id)
        for dependency in dependencies.get(topic_id, set()):
            if visit(dependency):
                return True
        visiting.remove(topic_id)
        visited.add(topic_id)
        return False

    for topic_id in dependencies:
        if visit(topic_id):
            findings.append(
                _finding(
                    "TOPIC_EXECUTION_DEPENDENCY_CYCLE",
                    f"Topic 执行依赖在 {topic_id} 形成环",
                )
            )
            break


def _is_degenerate_one_to_one_graph(
    graph: ResponseTopicGraph,
    *,
    formal_refs_by_topic: dict[str, set[str]],
    formal_item_count: int,
) -> bool:
    if formal_item_count < 4 or len(graph.topics) < 4:
        return False
    root_count = sum(
        1 for topic in graph.topics if topic.parent_topic_id is None
    )
    singleton_count = sum(
        1
        for topic in graph.topics
        if len(formal_refs_by_topic.get(topic.topic_id, set())) == 1
    )
    topic_count = len(graph.topics)
    has_semantic_hierarchy = any(
        topic.parent_topic_id is not None for topic in graph.topics
    )
    if has_semantic_hierarchy:
        return False
    size_ratio = topic_count / formal_item_count
    return (
        root_count / topic_count >= 0.9
        and singleton_count / topic_count >= 0.8
        and 0.75 <= size_ratio <= 1.35
        and len(graph.duties) >= formal_item_count * 0.75
    )


def _audit_abnormal_duty_bindings(
    graph: ResponseTopicGraph,
    *,
    formal_item_count: int,
    duties_by_formal_ref: dict[str, list[str]],
    legitimate_fanout_by_ref: dict[str, int],
    findings: list[dict[str, str]],
) -> None:
    if formal_item_count >= 8:
        for duty in graph.duties:
            bound_count = len(
                set(duty.requirement_ids) | set(duty.score_point_ids)
            )
            if (
                bound_count >= 16
                or (
                    bound_count >= 8
                    and bound_count / formal_item_count >= 0.75
                )
            ):
                findings.append(
                    _finding(
                        "TOPIC_DUTY_ABNORMAL_BINDING",
                        f"Duty {duty.duty_id} 异常集中绑定 {bound_count}/{formal_item_count} 个 Requirement/Score",
                    )
                )
    duty_count = len(graph.duties)
    if duty_count >= 6:
        for formal_ref, duty_ids in duties_by_formal_ref.items():
            unique_duty_count = len(set(duty_ids))
            if (
                unique_duty_count >= 6
                and unique_duty_count / duty_count >= 0.6
                and unique_duty_count
                > legitimate_fanout_by_ref.get(formal_ref, 1)
            ):
                findings.append(
                    _finding(
                        "TOPIC_UPSTREAM_ABNORMAL_FANOUT",
                        f"{formal_ref} 异常重复绑定 {unique_duty_count}/{duty_count} 个 Duty",
                    )
                )


def _chapter_subtree(
    root_id: str,
    children_by_parent: dict[str, list[str]],
) -> set[str]:
    result: set[str] = set()
    pending = [root_id]
    while pending:
        chapter_id = pending.pop()
        if chapter_id in result:
            continue
        result.add(chapter_id)
        pending.extend(children_by_parent.get(chapter_id, []))
    return result


def _chapter_lineage_titles(
    chapter_id: str,
    nodes: dict[str, object],
) -> list[str]:
    titles: list[str] = []
    cursor = chapter_id
    while cursor in nodes:
        node = nodes[cursor]
        titles.append(str(getattr(node, "title", "")))
        parent = getattr(node, "parent_chapter_id", None)
        if not parent:
            break
        cursor = str(parent)
    return titles


def _audit_template_structure(
    blueprint: ChapterBlueprint,
    *,
    template_structure: TemplateStructureContract | None,
    findings: list[dict[str, str]],
) -> None:
    if template_structure is None:
        if blueprint.mode is DocumentMode.TEMPLATE_STRICT:
            findings.append(
                _finding(
                    "TEMPLATE_STRUCTURE_CONTRACT_MISSING",
                    "template_strict Blueprint 缺少当前 TemplateStructureContract",
                )
            )
        return
    if blueprint.mode is not DocumentMode.TEMPLATE_STRICT:
        findings.append(
            _finding(
                "TEMPLATE_MODE_MISMATCH",
                "工作空间存在活动 TemplateStructureContract，Blueprint 必须使用 template_strict",
            )
        )
        return
    if blueprint.template_structure_revision != template_structure.revision:
        findings.append(
            _finding(
                "TEMPLATE_STRUCTURE_REVISION_MISMATCH",
                "Blueprint 未绑定当前 TemplateStructureContract revision",
            )
        )

    template_node_ids = [node.node_id for node in template_structure.nodes]
    template_slot_ids = [slot.slot_id for slot in template_structure.slots]
    if len(template_node_ids) != len(set(template_node_ids)):
        findings.append(
            _finding(
                "TEMPLATE_STRUCTURE_INVALID",
                "TemplateStructureContract 存在重复 node_id",
            )
        )
    if len(template_slot_ids) != len(set(template_slot_ids)):
        findings.append(
            _finding(
                "TEMPLATE_STRUCTURE_INVALID",
                "TemplateStructureContract 存在重复 slot_id",
            )
        )
    template_nodes = {node.node_id: node for node in template_structure.nodes}
    blueprint_nodes = {node.chapter_id: node for node in blueprint.nodes}
    mapped_template_ids = [
        node.template_node_id
        for node in blueprint.nodes
        if node.template_node_id is not None
    ]
    unmapped_chapter_ids = [
        node.chapter_id
        for node in blueprint.nodes
        if node.template_node_id is None
    ]
    if unmapped_chapter_ids:
        findings.append(
            _finding(
                "TEMPLATE_MAPPING_GAP",
                f"严格模板章节缺少 template_node_id: {sorted(unmapped_chapter_ids)}",
            )
        )
    if len(mapped_template_ids) != len(set(mapped_template_ids)):
        findings.append(
            _finding(
                "TEMPLATE_STRUCTURE_CHANGED",
                "严格模板 Blueprint 重复映射同一 template_node_id",
            )
        )
    blueprint_nodes_by_template = {
        node.template_node_id: node
        for node in blueprint.nodes
        if node.template_node_id is not None
    }
    missing = set(template_nodes) - set(blueprint_nodes_by_template)
    extra = set(blueprint_nodes_by_template) - set(template_nodes)
    if missing:
        findings.append(
            _finding(
                "TEMPLATE_MAPPING_GAP",
                f"严格模板节点未全部映射: {sorted(missing)}",
            )
        )
    if extra:
        findings.append(
            _finding(
                "TEMPLATE_STRUCTURE_CHANGED",
                f"严格模板模式新增了未授权章节: {sorted(extra)}",
            )
        )

    slots_by_template_node: dict[str, list[str]] = defaultdict(list)
    for slot in template_structure.slots:
        slots_by_template_node[slot.node_id].append(slot.slot_id)

    for node_id in sorted(
        set(template_nodes) & set(blueprint_nodes_by_template)
    ):
        expected = template_nodes[node_id]
        actual = blueprint_nodes_by_template[node_id]
        changes: list[str] = []
        if actual.title != expected.title:
            changes.append("title")
        actual_parent_template_id = None
        if actual.parent_chapter_id is not None:
            actual_parent = blueprint_nodes.get(actual.parent_chapter_id)
            actual_parent_template_id = (
                actual_parent.template_node_id
                if actual_parent is not None
                else None
            )
        if actual_parent_template_id != expected.parent_node_id:
            changes.append("parent")
        if actual.order != expected.order:
            changes.append("order")
        if actual.template_level != expected.level:
            changes.append("level")
        if actual.template_numbering != expected.numbering:
            changes.append("numbering")
        if actual.template_slot_ids != slots_by_template_node.get(node_id, []):
            changes.append("slots")
        if actual.template_target != expected.writable_target:
            changes.append("writable_target")
        if changes:
            findings.append(
                _finding(
                    "TEMPLATE_STRUCTURE_CHANGED",
                    f"严格模板节点 {node_id} 未授权改变字段: {', '.join(changes)}",
                )
            )

    _audit_template_levels(template_structure, findings)


def _audit_template_levels(
    template_structure: TemplateStructureContract,
    findings: list[dict[str, str]],
) -> None:
    nodes = {node.node_id: node for node in template_structure.nodes}
    for node in template_structure.nodes:
        depth = 1
        seen = {node.node_id}
        cursor = node.parent_node_id
        while cursor is not None:
            parent = nodes.get(cursor)
            if parent is None:
                findings.append(
                    _finding(
                        "TEMPLATE_STRUCTURE_INVALID",
                        f"Template 节点 {node.node_id} 指向未知父节点 {cursor}",
                    )
                )
                break
            if cursor in seen:
                findings.append(
                    _finding(
                        "TEMPLATE_STRUCTURE_INVALID",
                        f"Template 节点 {node.node_id} 的父子层级形成环",
                    )
                )
                break
            seen.add(cursor)
            depth += 1
            cursor = parent.parent_node_id
        else:
            if node.level != depth:
                findings.append(
                    _finding(
                        "TEMPLATE_STRUCTURE_INVALID",
                        f"Template 节点 {node.node_id} 的 level={node.level} 与父子层级 {depth} 不一致",
                    )
                )


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _atomic_condition_clauses(values: list[str]) -> list[str]:
    clauses: list[str] = []
    for value in values:
        text = re.sub(r"(?:得|计)\s*\d+(?:\.\d+)?\s*分.*$", "", value).strip()
        for clause in _CONDITION_BOUNDARY.split(text):
            cleaned = clause.strip(" ，,；;、。.")
            if cleaned:
                clauses.append(cleaned)
    return _unique_nonempty(clauses)


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
