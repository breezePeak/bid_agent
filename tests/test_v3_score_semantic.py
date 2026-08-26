from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import (
    ScoreCondition,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    ScoreResponseUnit,
    SourceAnchor,
)
from document_pipeline.score_agent import ScoreAgent
from document_pipeline.score_semantic import (
    DeterministicScoreGroupInput,
    DeterministicScoreLevelInput,
    DeterministicScoreRuleInput,
    FileScoreSemanticBatchCache,
    LLMScoreSemanticProvider,
    ScoreConditionCandidate,
    ScoreLinkedRequirementInput,
    ScoreSemanticInferenceError,
    ScoreSemanticInput,
    ScoreSourceAnchorInput,
    _require_atomic_substantive_enumerations,
    _substantive_enumeration_spans,
    build_score_semantic_batches,
    semantic_coverage_text,
    uncovered_semantic_source_text,
)

_SOURCE_TEXT = (
    "准备工作全面，数据内容具体，检查方法科学，得2分；"
    "准备工作不足且方法不可行，得0分。"
)
_ENUMERATED_SOURCE_TEXT = (
    "方案内容包括项目背景、工作目标、工作内容、技术方法、质量控制，"
    "方案内容完整、合理、可行、针对性强。"
)


def _source_ref(excerpt: str) -> dict[str, int]:
    start = _SOURCE_TEXT.index(excerpt)
    return {
        "source_anchor_index": 0,
        "source_span_start": start,
        "source_span_end": start + len(excerpt),
    }


def _semantic_input() -> ScoreSemanticInput:
    return ScoreSemanticInput(
        source_snapshot_hash="source-sha256",
        deterministic_structure_hash="structure-sha256",
        total_points=2.0,
        groups=[
            DeterministicScoreGroupInput(
                group_id="SG-1",
                title="技术方法（2分）",
                source_order=0,
                declared_points=2.0,
            )
        ],
        rules=[
            DeterministicScoreRuleInput(
                rule_id="SR-1",
                group_id="SG-1",
                source_order=0,
                title="核查准备工作",
                raw_criterion=_SOURCE_TEXT,
                max_points=2.0,
                source_hierarchy=["技术方法（2分）", "核查准备工作（2分）"],
                levels=[
                    DeterministicScoreLevelInput(
                        level_id="SL-full",
                        label="满分",
                        points=2.0,
                        criterion="准备工作全面，数据内容具体，检查方法科学，得2分",
                        source_order=0,
                    ),
                    DeterministicScoreLevelInput(
                        level_id="SL-zero",
                        label="零分",
                        points=0.0,
                        criterion="准备工作不足且方法不可行，得0分",
                        source_order=1,
                    ),
                ],
                source_anchors=[
                    ScoreSourceAnchorInput(
                        source_input_id="score-doc",
                        chunk_id="chunk-9",
                        page=3,
                        location="table[0]/row[2]",
                        source_text=_SOURCE_TEXT,
                    )
                ],
            )
        ],
    )


def _enumerated_semantic_input() -> ScoreSemanticInput:
    return ScoreSemanticInput(
        source_snapshot_hash="enumerated-source-sha256",
        deterministic_structure_hash="enumerated-structure-sha256",
        total_points=10.0,
        groups=[
            DeterministicScoreGroupInput(
                group_id="SG-ENUM",
                title="技术方案（10分）",
                source_order=0,
                declared_points=10.0,
            )
        ],
        rules=[
            DeterministicScoreRuleInput(
                rule_id="SR-ENUM",
                group_id="SG-ENUM",
                source_order=0,
                title="技术方案",
                raw_criterion=_ENUMERATED_SOURCE_TEXT,
                max_points=10.0,
                levels=[
                    DeterministicScoreLevelInput(
                        level_id="SL-ENUM-FULL",
                        label="满分",
                        points=10.0,
                        criterion=_ENUMERATED_SOURCE_TEXT,
                        source_order=0,
                    )
                ],
                source_anchors=[
                    ScoreSourceAnchorInput(
                        source_input_id="score-doc",
                        chunk_id="chunk-enumerated",
                        page=6,
                        location="table[1]/row[3]",
                        source_text=_ENUMERATED_SOURCE_TEXT,
                    )
                ],
            )
        ],
    )


def _enumerated_source_ref(excerpt: str) -> dict[str, int]:
    start = _ENUMERATED_SOURCE_TEXT.index(excerpt)
    return {
        "source_anchor_index": 0,
        "source_span_start": start,
        "source_span_end": start + len(excerpt),
    }


def _enumerated_condition(
    *,
    key: str,
    excerpt: str,
    role: str,
    subject: str,
) -> dict:
    return {
        "condition_key": key,
        "text": excerpt,
        "normalized_condition": excerpt,
        "condition_role": role,
        "source_excerpt": excerpt,
        **_enumerated_source_ref(excerpt),
        "source_level_id": "SL-ENUM-FULL",
        "semantic_subject": subject,
        "response_intent": f"响应{excerpt}",
        "required_evidence_types": [],
        "confidence": 0.98,
    }


def _enumerated_candidate(*, collapsed: bool) -> dict:
    if collapsed:
        content_conditions = [
            _enumerated_condition(
                key="SR-ENUM-C1",
                excerpt=(
                    "方案内容包括项目背景、工作目标、工作内容、技术方法、质量控制"
                ),
                role="content",
                subject="方案内容",
            )
        ]
    else:
        content_conditions = [
            _enumerated_condition(
                key="SR-ENUM-C1",
                excerpt="方案内容包括项目背景",
                role="content",
                subject="项目背景",
            ),
            _enumerated_condition(
                key="SR-ENUM-C2",
                excerpt="工作目标",
                role="content",
                subject="工作目标",
            ),
            _enumerated_condition(
                key="SR-ENUM-C3",
                excerpt="工作内容",
                role="content",
                subject="工作内容",
            ),
            _enumerated_condition(
                key="SR-ENUM-C4",
                excerpt="技术方法",
                role="content",
                subject="技术方法",
            ),
            _enumerated_condition(
                key="SR-ENUM-C5",
                excerpt="质量控制",
                role="content",
                subject="质量控制",
            ),
        ]
    quality_key = "SR-ENUM-C2" if collapsed else "SR-ENUM-C6"
    conditions = [
        *content_conditions,
        _enumerated_condition(
            key=quality_key,
            excerpt="方案内容完整、合理、可行、针对性强",
            role="quality",
            subject="方案质量",
        ),
    ]
    return {
        "schema_version": "v3-score-semantic-candidate-6",
        "interpretations": [
            {
                "rule_id": "SR-ENUM",
                "shared_context": "技术方案满分响应",
                "units": [
                    {
                        "unit_key": "SR-ENUM-U1",
                        "title": "技术方案",
                        "source_excerpt": "技术方案内容与质量",
                        "outline_path": ["技术方案（10分）", "技术方案"],
                        "linked_requirement_ids": [],
                        "band_semantics": [
                            {
                                "level_id": "SL-ENUM-FULL",
                                "attainment": "full",
                                "semantic_summary": "全部要求满足时得满分",
                            }
                        ],
                        "full_score_conditions": conditions,
                        "condition_join": "all",
                        "response_scope": "section",
                        "response_expectation": "逐项响应方案内容并满足质量标准",
                        "required_evidence_types": [],
                        "confidence": 0.98,
                        "review_status": "confirmed",
                        "review_reason": None,
                    }
                ],
                "confidence": 0.98,
                "review_status": "confirmed",
                "review_reason": None,
            }
        ],
    }


def _valid_candidate() -> dict:
    return {
        "schema_version": "v3-score-semantic-candidate-6",
        "interpretations": [
            {
                "rule_id": "SR-1",
                "shared_context": "核查准备工作的完整性与可执行性",
                "units": [
                    {
                        "unit_key": "SR-1-U1",
                        "title": "核查准备内容与检查方法",
                        "source_excerpt": "准备工作全面，数据内容具体，检查方法科学",
                        "outline_path": ["技术方法（2分）", "核查准备工作（2分）"],
                        "linked_requirement_ids": [],
                        "band_semantics": [
                            {
                                "level_id": "SL-full",
                                "attainment": "full",
                                "semantic_summary": "三项要求全部满足时得满分",
                            },
                            {
                                "level_id": "SL-zero",
                                "attainment": "zero",
                                "semantic_summary": "准备不足且方法不可行时不得分",
                            },
                        ],
                        "full_score_conditions": [
                            {
                                "condition_key": "SR-1-C1",
                                "text": "核查准备工作全面",
                                "normalized_condition": "核查准备工作全面",
                                "condition_role": "quality",
                                "source_excerpt": "准备工作全面",
                                **_source_ref("准备工作全面"),
                                "source_level_id": "SL-full",
                                "semantic_subject": "核查准备工作",
                                "response_intent": "说明准备工作的完整安排",
                                "required_evidence_types": ["工作方案"],
                                "confidence": 0.98,
                            },
                            {
                                "condition_key": "SR-1-C2",
                                "text": "数据接收内容具体",
                                "normalized_condition": "数据接收内容具体",
                                "condition_role": "quality",
                                "source_excerpt": "数据内容具体",
                                **_source_ref("数据内容具体"),
                                "source_level_id": "SL-full",
                                "semantic_subject": "数据接收内容",
                                "response_intent": "列明拟接收的数据内容",
                                "required_evidence_types": ["数据清单"],
                                "confidence": 0.96,
                            },
                            {
                                "condition_key": "SR-1-C3",
                                "text": "检查方法科学",
                                "normalized_condition": "检查方法科学",
                                "condition_role": "quality",
                                "source_excerpt": "检查方法科学",
                                **_source_ref("检查方法科学"),
                                "source_level_id": "SL-full",
                                "semantic_subject": "检查方法",
                                "response_intent": "说明检查方法及科学依据",
                                "required_evidence_types": ["方法说明"],
                                "confidence": 0.97,
                            },
                        ],
                        "condition_join": "all",
                        "response_scope": "section",
                        "response_expectation": "完整描述准备内容和科学检查方法",
                        "required_evidence_types": ["工作方案", "数据清单", "方法说明"],
                        "confidence": 0.97,
                        "review_status": "confirmed",
                        "review_reason": None,
                    }
                ],
                "confidence": 0.97,
                "review_status": "confirmed",
                "review_reason": None,
            }
        ],
    }


def _multi_rule_input() -> ScoreSemanticInput:
    semantic_input = _semantic_input()
    first_rule = semantic_input.rules[0]
    second_rule = first_rule.model_copy(
        update={
            "rule_id": "SR-2",
            "source_order": 1,
            "title": "第二项核查准备工作",
        }
    )
    return semantic_input.model_copy(update={"rules": [first_rule, second_rule]})


def _candidate_for_rule(rule_id: str) -> dict:
    candidate = json.loads(json.dumps(_valid_candidate(), ensure_ascii=False))
    interpretation = candidate["interpretations"][0]
    interpretation["rule_id"] = rule_id
    interpretation["units"][0]["unit_key"] = f"{rule_id}-U1"
    for index, condition in enumerate(
        interpretation["units"][0]["full_score_conditions"],
        start=1,
    ):
        condition["condition_key"] = f"{rule_id}-C{index}"
    return candidate


def _multi_rule_candidate() -> dict:
    first = _candidate_for_rule("SR-1")
    second = _candidate_for_rule("SR-2")
    return {
        "schema_version": "v3-score-semantic-candidate-6",
        "interpretations": [
            first["interpretations"][0],
            second["interpretations"][0],
        ],
    }


def test_strict_candidate_is_returned_without_repair() -> None:
    calls: list[list[dict[str, str]]] = []

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        assert temperature == 0.1
        calls.append(messages)
        return json.dumps(_valid_candidate(), ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert len(calls) == 1
    assert "ALLOWED_REQUIREMENT_IDS_BY_RULE" in calls[0][1]["content"]
    assert '{"SR-1":[]}' in calls[0][1]["content"]
    assert result.candidate.interpretations[0].units[0].title == "核查准备内容与检查方法"
    assert provider.prompt_hash
    assert provider.model_fingerprint == "fake-model:v1"


def test_semantic_subject_quality_has_no_length_limit() -> None:
    condition = json.loads(json.dumps(
        _valid_candidate()["interpretations"][0]["units"][0][
            "full_score_conditions"
        ][0],
        ensure_ascii=False,
    ))
    long_subject = "跨区域多源异构时空数据治理与成果一致性检查实施方案专题"
    condition["semantic_subject"] = f"  {long_subject}  "
    assert (
        ScoreConditionCandidate.model_validate(condition).semantic_subject
        == long_subject
    )
    condition["semantic_subject"] = "可行性分析"
    assert ScoreConditionCandidate.model_validate(
        condition
    ).semantic_subject == "可行性分析"

    for invalid_subject in (
        "业务对象。",
        "业务对象得2分",
        "业务对象描述清楚",
    ):
        condition["semantic_subject"] = invalid_subject
        with pytest.raises(ValueError, match="semantic_subject"):
            ScoreConditionCandidate.model_validate(condition)


def test_repeated_quote_is_grounded_by_level_and_score_heading_is_not_a_condition() -> None:
    repeated_excerpt = "且具有3个及以上调查成果核查类似项目经验"
    full_criterion = (
        "技术负责人（1分）\n"
        "作为负责人从事调查相关技术工作3年（不含）以上，"
        f"{repeated_excerpt}，且相关专业高级及以上职称，得1分"
    )
    source_text = (
        f"{full_criterion}；\n"
        "驻场人员（3分）\n"
        f"从事调查相关工作3年以上，{repeated_excerpt}，得0.6分。"
    )
    semantic_input = ScoreSemanticInput(
        source_snapshot_hash="repeated-source",
        deterministic_structure_hash="repeated-structure",
        total_points=1.0,
        groups=[
            DeterministicScoreGroupInput(
                group_id="SG-LEAD",
                title="商务部分",
                source_order=0,
                declared_points=1.0,
            )
        ],
        rules=[
            DeterministicScoreRuleInput(
                rule_id="SR-LEAD",
                group_id="SG-LEAD",
                source_order=0,
                title="组织结构及成员",
                raw_criterion=source_text,
                max_points=1.0,
                levels=[
                    DeterministicScoreLevelInput(
                        level_id="SL-LEAD-FULL",
                        label="1分档",
                        points=1.0,
                        criterion=full_criterion,
                        source_order=0,
                    ),
                    DeterministicScoreLevelInput(
                        level_id="SL-LEAD-ZERO",
                        label="0分档",
                        points=0.0,
                        criterion="不满足要求得0分",
                        source_order=1,
                    ),
                ],
                source_anchors=[
                    ScoreSourceAnchorInput(
                        source_input_id="score-doc",
                        chunk_id="chunk-lead",
                        page=1,
                        location="table[0]/row[1]",
                        source_text=source_text,
                    )
                ],
            )
        ],
    )

    def condition(
        key: str,
        excerpt: str,
        role: str,
        *,
        wrong_end: bool = False,
    ) -> dict:
        start = source_text.index(excerpt)
        return {
            "condition_key": key,
            "text": excerpt,
            "normalized_condition": excerpt,
            "condition_role": role,
            "source_excerpt": excerpt,
            "source_anchor_index": 0,
            "source_span_start": start,
            "source_span_end": start + len(excerpt) - int(wrong_end),
            "source_level_id": "SL-LEAD-FULL",
            "semantic_subject": "技术负责人",
            "response_intent": f"响应{excerpt}",
            "required_evidence_types": [],
            "confidence": 0.98,
        }

    candidate = {
        "schema_version": "v3-score-semantic-candidate-6",
        "interpretations": [
            {
                "rule_id": "SR-LEAD",
                "shared_context": "技术负责人评分",
                "units": [
                    {
                        "unit_key": "SR-LEAD-U1",
                        "title": "技术负责人",
                        "source_excerpt": "技术负责人满分条件",
                        "outline_path": ["商务部分", "技术负责人"],
                        "linked_requirement_ids": [],
                        "band_semantics": [
                            {
                                "level_id": "SL-LEAD-FULL",
                                "attainment": "full",
                                "semantic_summary": "满足全部条件得满分",
                            },
                            {
                                "level_id": "SL-LEAD-ZERO",
                                "attainment": "zero",
                                "semantic_summary": "不满足要求不得分",
                            },
                        ],
                        "full_score_conditions": [
                            condition(
                                "SR-LEAD-C1",
                                "作为负责人从事调查相关技术工作3年（不含）以上",
                                "constraint",
                            ),
                            condition(
                                "SR-LEAD-C2",
                                repeated_excerpt,
                                "content",
                                wrong_end=True,
                            ),
                            condition(
                                "SR-LEAD-C3",
                                "且相关专业高级及以上职称",
                                "content",
                            ),
                        ],
                        "condition_join": "all",
                        "response_scope": "section",
                        "response_expectation": "逐项响应技术负责人要求",
                        "required_evidence_types": [],
                        "confidence": 0.98,
                        "review_status": "confirmed",
                        "review_reason": None,
                    }
                ],
                "confidence": 0.98,
                "review_status": "confirmed",
                "review_reason": None,
            }
        ],
    }
    repeated_candidate = candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][1]
    first_candidate = candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0]
    first_candidate["source_excerpt"] = (
        "作为负责人从事调查相关技术工作3年(不含)以上"
    )
    for condition_candidate in (first_candidate, repeated_candidate):
        for location_field in (
            "source_anchor_index",
            "source_span_start",
            "source_span_end",
        ):
            condition_candidate.pop(location_field)

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)

    repeated_condition = result.candidate.interpretations[0].units[
        0
    ].full_score_conditions[1]
    assert repeated_condition.source_span_start == source_text.index(
        repeated_excerpt
    )
    assert repeated_condition.source_span_end == (
        repeated_condition.source_span_start + len(repeated_excerpt)
    )
    first_condition = result.candidate.interpretations[0].units[
        0
    ].full_score_conditions[0]
    assert first_condition.source_excerpt == (
        "作为负责人从事调查相关技术工作3年（不含）以上"
    )


def test_collapsed_substantive_enumeration_warns_without_blocking() -> None:
    collapsed = _enumerated_candidate(collapsed=True)
    calls = 0

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(collapsed, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_enumerated_semantic_input())

    assert calls == 2
    assert any(
        "合并了多个应独立响应的实质性枚举要求" in error
        and "项目背景" in error
        and "质量控制" in error
        for error in result.warnings
    )
    assert result.candidate.interpretations[0].review_status == "needs_human"


def test_one_rule_reports_coverage_and_atomic_defects_in_same_attempt() -> None:
    candidate = _enumerated_candidate(collapsed=True)
    collapsed_excerpt = (
        "方案内容包括项目背景、工作目标、工作内容"
    )
    collapsed_condition = candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0]
    collapsed_condition.update(
        {
            "text": collapsed_excerpt,
            "normalized_condition": collapsed_excerpt,
            "source_excerpt": collapsed_excerpt,
            **_enumerated_source_ref(collapsed_excerpt),
        }
    )

    valid, repair_ids, errors, _ = (
        LLMScoreSemanticProvider._partition_raw_candidate(
            json.dumps(candidate, ensure_ascii=False),
            _enumerated_semantic_input(),
        )
    )

    assert list(valid) == ["SR-ENUM"]
    assert repair_ids == ["SR-ENUM"]
    combined = "\n".join(errors)
    assert "未无损覆盖最高档" in combined
    assert "技术方法质量控制" in combined
    assert "合并了多个应独立响应的实质性枚举要求" in combined
    assert "项目背景" in combined
    assert "工作内容" in combined


def test_one_rule_reports_all_unresolved_source_quotes_without_loosening() -> None:
    candidate = _valid_candidate()
    conditions = candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ]
    for condition, invented_excerpt in zip(
        conditions[:2],
        ["准备安排非常完善", "数据清单十分详实"],
        strict=True,
    ):
        condition["source_excerpt"] = invented_excerpt
        for field in (
            "source_anchor_index",
            "source_span_start",
            "source_span_end",
        ):
            condition.pop(field)

    valid, repair_ids, errors, _ = (
        LLMScoreSemanticProvider._partition_raw_candidate(
            json.dumps(candidate, ensure_ascii=False),
            _semantic_input(),
        )
    )

    assert valid == {}
    assert repair_ids == ["SR-1"]
    combined = "\n".join(errors)
    assert "SR-1-U01-C01" in combined
    assert "SR-1-U01-C02" in combined
    assert combined.count("尚未完成确定性来源定位") >= 2


def test_substantive_enumeration_is_atomic_but_quality_adjectives_may_stay_grouped() -> None:
    candidate = _enumerated_candidate(collapsed=False)

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(_enumerated_semantic_input())

    conditions = result.candidate.interpretations[0].units[0].full_score_conditions
    assert [condition.source_excerpt for condition in conditions[:5]] == [
        "方案内容包括项目背景",
        "工作目标",
        "工作内容",
        "技术方法",
        "质量控制",
    ]
    quality_conditions = [
        condition for condition in conditions if condition.condition_role == "quality"
    ]
    assert len(quality_conditions) == 1
    assert quality_conditions[0].source_excerpt == "方案内容完整、合理、可行、针对性强"


def test_atomic_guard_does_not_false_split_ambiguous_noun_or_quality_phrases() -> None:
    assert not _substantive_enumeration_spans("系统包括软、硬件、配套设施")
    assert not _substantive_enumeration_spans(
        "方案包括完整、合理、可行、针对性强"
    )


def test_unit_source_excerpt_is_semantic_context_not_a_hidden_exact_quote_gate() -> None:
    candidate = _valid_candidate()
    candidate["interpretations"][0]["units"][0]["source_excerpt"] = (
        "围绕核查准备完整性和可执行性的综合响应"
    )

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 1
    assert result.candidate.interpretations[0].rule_id == "SR-1"


def test_partial_repair_reuses_independently_valid_rules_and_repairs_only_rejected_rule() -> None:
    initial = _multi_rule_candidate()
    initial["interpretations"][1]["units"][0][
        "full_score_conditions"
    ].pop(0)
    repaired = _candidate_for_rule("SR-2")
    calls: list[list[dict[str, str]]] = []
    outputs = iter(
        [
            json.dumps(initial, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_multi_rule_input())

    assert result.attempt_count == 2
    assert [item.rule_id for item in result.candidate.interpretations] == [
        "SR-1",
        "SR-2",
    ]
    assert len(calls) == 2
    repair_prompt = calls[1][1]["content"]
    assert "[SR-2]" in repair_prompt
    assert "SR-1" not in repair_prompt
    assert '{"SR-2":[]}' in repair_prompt
    assert '"rule_id":"SR-2"' in repair_prompt
    assert '"rule_id":"SR-1"' not in repair_prompt
    assert [item["rule_id"] for item in json.loads(result.input_snapshot)["rules"]] == [
        "SR-1",
        "SR-2",
    ]
    assert json.loads(result.raw_output)["repair_rule_ids"] == ["SR-2"]


def test_invalid_subject_repairs_only_that_rule() -> None:
    initial = _multi_rule_candidate()
    initial["interpretations"][1]["units"][0][
        "full_score_conditions"
    ][0]["semantic_subject"] = "核查准备工作全面"
    repaired = _candidate_for_rule("SR-2")
    calls: list[list[dict[str, str]]] = []
    outputs = iter(
        [
            json.dumps(initial, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    result = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(_multi_rule_input())

    assert result.attempt_count == 2
    assert [item.rule_id for item in result.candidate.interpretations] == [
        "SR-1",
        "SR-2",
    ]
    repair_prompt = calls[1][1]["content"]
    assert "[SR-2]" in repair_prompt
    assert "SR-1" not in repair_prompt
    assert "semantic_subject" in repair_prompt
    assert json.loads(result.raw_output)["repair_rule_ids"] == ["SR-2"]


def test_unique_fenced_json_is_mechanically_extracted_without_repair() -> None:
    outputs = iter(
        [
            "```json\n"
            f"{json.dumps(_valid_candidate(), ensure_ascii=False)}"
            "\n```"
        ]
    )
    calls: list[list[dict[str, str]]] = []

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.candidate.interpretations[0].rule_id == "SR-1"
    assert len(calls) == 1


def test_empty_conditions_after_repair_warns_without_blocking() -> None:
    repaired = _valid_candidate()
    repaired["interpretations"][0]["units"][0]["full_score_conditions"] = []
    outputs = iter(
        [
            '{"schema_version":"v3-score-semantic-candidate-6"',
            json.dumps(repaired, ensure_ascii=False),
        ]
    )
    calls = 0

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal calls
        calls += 1
        return next(outputs)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 2
    assert calls == 2
    assert any(
        "缺少满分原子条件" in error
        for error in result.warnings
    )
    assert result.candidate.interpretations[0].review_status == "needs_human"


def test_second_invalid_candidate_fails_closed() -> None:
    calls = 0

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal calls
        calls += 1
        return "not-json"

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    with pytest.raises(ScoreSemanticInferenceError) as caught:
        provider.interpret(_semantic_input())

    assert calls == 2
    assert caught.value.attempts == 2
    assert caught.value.code == "score_semantic_candidate_invalid"


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda candidate: candidate["interpretations"][0].update(
                {"rule_id": "SR-UNKNOWN"}
            ),
            "未知 rule_id",
        ),
        (
            lambda candidate: candidate["interpretations"][0]["units"][
                0
            ].update({"linked_requirement_ids": ["REQ-UNKNOWN"]}),
            "未提供的 requirement_id",
        ),
        (
            lambda candidate: candidate["interpretations"][0]["units"][
                0
            ]["band_semantics"][0].update({"level_id": "SL-UNKNOWN"}),
            "未知 level_id",
        ),
    ],
)
def test_unknown_ids_remain_hard_failures(
    mutate: Callable[[dict[str, object]], None],
    expected_error: str,
) -> None:
    invalid = _valid_candidate()
    mutate(invalid)

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    with pytest.raises(ScoreSemanticInferenceError) as caught:
        LLMScoreSemanticProvider(
            fake,
            model_fingerprint="fake-model:v1",
        ).interpret(_semantic_input())

    assert caught.value.attempts == 2
    assert any(expected_error in error for error in caught.value.errors)


def test_invented_source_excerpt_never_passes_validation() -> None:
    invalid = _valid_candidate()
    invalid["interpretations"][0]["units"][0]["full_score_conditions"][0][
        "source_excerpt"
    ] = "采购人从未提出的五星级证书"

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    with pytest.raises(ScoreSemanticInferenceError) as caught:
        provider.interpret(_semantic_input())

    assert caught.value.attempts == 2
    assert any("SourceBlock span 不一致" in item for item in caught.value.errors)


def test_omitted_highest_band_condition_gets_one_controlled_repair() -> None:
    incomplete = _valid_candidate()
    incomplete["interpretations"][0]["units"][0]["full_score_conditions"].pop(1)
    outputs = iter(
        [
            json.dumps(incomplete, ensure_ascii=False),
            json.dumps(_valid_candidate(), ensure_ascii=False),
        ]
    )
    calls = 0

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal calls
        calls += 1
        return next(outputs)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 2
    assert calls == 2


def test_omitted_highest_band_condition_warns_after_repair() -> None:
    incomplete = _valid_candidate()
    incomplete["interpretations"][0]["units"][0]["full_score_conditions"].pop(1)

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(incomplete, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 2
    assert any("未无损覆盖最高档" in item for item in result.warnings)
    assert any("数据内容具体" in item for item in result.warnings)
    assert result.candidate.interpretations[0].review_status == "needs_human"


def test_initial_output_recomputes_uniquely_grounded_source_span() -> None:
    invalid = _valid_candidate()
    condition = invalid["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0]
    condition["source_span_start"] += 1
    condition["source_span_end"] += 1

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 1
    repaired = result.candidate.interpretations[0].units[0].full_score_conditions[0]
    assert repaired.source_span_start == _SOURCE_TEXT.index("准备工作全面")
    assert repaired.source_span_end == repaired.source_span_start + len("准备工作全面")


def test_repair_output_restores_layout_whitespace_from_unique_source_quote() -> None:
    source_text = _SOURCE_TEXT.replace("数据内容具体", "数据内容\n具体")
    semantic_input = _semantic_input()
    rule = semantic_input.rules[0].model_copy(
        update={
            "source_anchors": [
                semantic_input.rules[0].source_anchors[0].model_copy(
                    update={"source_text": source_text}
                )
            ]
        }
    )
    semantic_input = semantic_input.model_copy(update={"rules": [rule]})
    invalid = _valid_candidate()

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(semantic_input)

    repaired = result.candidate.interpretations[0].units[0].full_score_conditions[1]
    assert repaired.source_excerpt == "数据内容\n具体"
    assert source_text[
        repaired.source_span_start : repaired.source_span_end
    ] == repaired.source_excerpt


def test_repeated_source_quote_is_assigned_first_unused_exact_span() -> None:
    semantic_input = _semantic_input()
    duplicated = _SOURCE_TEXT.replace(
        "准备工作全面",
        "准备工作全面，准备工作全面",
        1,
    )
    levels = list(semantic_input.rules[0].levels)
    levels[0] = levels[0].model_copy(
        update={
            "criterion": levels[0].criterion.replace(
                "准备工作全面",
                "准备工作全面，准备工作全面",
                1,
            )
        }
    )
    rule = semantic_input.rules[0].model_copy(
        update={
            "raw_criterion": duplicated,
            "levels": levels,
            "source_anchors": [
                semantic_input.rules[0].source_anchors[0].model_copy(
                    update={"source_text": duplicated}
                )
            ]
        }
    )
    semantic_input = semantic_input.model_copy(update={"rules": [rule]})
    invalid = _valid_candidate()
    condition = invalid["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0]
    condition["source_span_start"] += 1
    condition["source_span_end"] += 1

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    provider = LLMScoreSemanticProvider(fake, model_fingerprint="fake-model:v1")
    result = provider.interpret(semantic_input)

    grounded = result.candidate.interpretations[0].units[
        0
    ].full_score_conditions[0]
    assert grounded.source_span_start == duplicated.index(
        grounded.source_excerpt
    )
    assert duplicated[
        grounded.source_span_start : grounded.source_span_end
    ] == grounded.source_excerpt


def test_legacy_score_condition_and_response_unit_receive_safe_defaults() -> None:
    condition = ScoreCondition.model_validate(
        {
            "condition_id": "SP-1-C01",
            "text": "提供项目合同复印件",
            "source_excerpt": "提供项目合同复印件",
            "subject": "项目合同",
            "response_intent": "提供证明材料",
        }
    )
    unit = ScoreResponseUnit(
        unit_id="SP-1-U01",
        title="项目合同",
        response_expectation="提供项目合同复印件",
    )

    assert condition.normalized_condition == condition.text
    assert condition.condition_role == "content"
    assert unit.linked_requirement_ids == []


def test_batches_respect_group_and_complete_rule_boundaries() -> None:
    semantic_input = _multi_rule_input()
    second_group = DeterministicScoreGroupInput(
        group_id="SG-2",
        title="商务评分",
        source_order=1,
        declared_points=2.0,
    )
    second_rule = semantic_input.rules[1].model_copy(
        update={"group_id": "SG-2"}
    )
    by_group = semantic_input.model_copy(
        update={
            "groups": [semantic_input.groups[0], second_group],
            "rules": [semantic_input.rules[0], second_rule],
        }
    )

    group_batches = build_score_semantic_batches(
        by_group,
        max_input_chars=100_000,
    )
    per_rule_chars = []
    for rule in semantic_input.rules:
        single = semantic_input.model_copy(update={"rules": [rule]})
        per_rule_chars.append(
            build_score_semantic_batches(
                single,
                max_input_chars=100_000,
            )[0].input_chars
        )
    budget_batches = build_score_semantic_batches(
        semantic_input,
        max_input_chars=max(per_rule_chars),
    )

    assert [
        [rule.rule_id for rule in batch.semantic_input.rules]
        for batch in group_batches
    ] == [["SR-1"], ["SR-2"]]
    assert [batch.batch_group_id for batch in group_batches] == ["SG-1", "SG-2"]
    assert [
        [rule.rule_id for rule in batch.semantic_input.rules]
        for batch in budget_batches
    ] == [["SR-1"], ["SR-2"]]
    assert all(
        len(batch.semantic_input.rules[0].source_anchors) == 1
        for batch in budget_batches
    )
    assert all(
        batch.input_chars <= max(per_rule_chars)
        for batch in budget_batches
    )


def test_unchanged_batch_fingerprint_survives_unrelated_rule_change() -> None:
    semantic_input = _multi_rule_input()
    per_rule_budget = max(
        build_score_semantic_batches(
            semantic_input.model_copy(update={"rules": [rule]}),
            max_input_chars=100_000,
        )[0].input_chars
        for rule in semantic_input.rules
    )
    original = build_score_semantic_batches(
        semantic_input,
        max_input_chars=per_rule_budget,
    )
    changed_second = semantic_input.rules[1].model_copy(
        update={"title": "第二项已修订"}
    )
    changed_input = semantic_input.model_copy(
        update={
            "source_snapshot_hash": "new-global-source-hash",
            "deterministic_structure_hash": "new-global-structure-hash",
            "rules": [semantic_input.rules[0], changed_second],
        }
    )
    changed = build_score_semantic_batches(
        changed_input,
        max_input_chars=per_rule_budget,
    )

    assert original[0].fingerprint == changed[0].fingerprint
    assert original[1].fingerprint != changed[1].fingerprint


def _linked_requirement(requirement_id: str) -> ScoreLinkedRequirementInput:
    return ScoreLinkedRequirementInput(
        requirement_id=requirement_id,
        kind="mandatory",
        normalized_requirement=f"{requirement_id} 对应采购要求",
        status="confirmed",
        severity="normal",
        original_text=f"{requirement_id} 对应采购要求原文",
        source_input_id="tender",
        chunk_id=f"chunk-{requirement_id}",
        location=f"paragraph:{requirement_id}",
    )


def _semantic_input_with_requirements() -> ScoreSemanticInput:
    semantic_input = _semantic_input()
    rule = semantic_input.rules[0].model_copy(
        update={
            "linked_requirement_ids": ["REQ-DIRECT"],
            "context_requirement_ids": [
                "REQ-C1",
                "REQ-C2",
                "REQ-C3",
                "REQ-C4",
                "REQ-C5",
            ],
        }
    )
    return semantic_input.model_copy(
        update={
            "rules": [rule],
            "linked_requirements": [
                _linked_requirement(requirement_id)
                for requirement_id in (
                    "REQ-DIRECT",
                    "REQ-C1",
                    "REQ-C2",
                    "REQ-C3",
                    "REQ-C4",
                    "REQ-C5",
                )
            ],
        }
    )


def test_batch_trims_only_low_priority_context_tails_before_splitting() -> None:
    semantic_input = _semantic_input_with_requirements()
    minimum_rule = semantic_input.rules[0].model_copy(
        update={
            "context_requirement_ids": ["REQ-C1", "REQ-C2", "REQ-C3"],
        }
    )
    minimum_input = semantic_input.model_copy(
        update={
            "rules": [minimum_rule],
            "linked_requirements": semantic_input.linked_requirements[:4],
        }
    )
    minimum_chars = build_score_semantic_batches(
        minimum_input,
        max_input_chars=100_000,
    )[0].input_chars

    batches = build_score_semantic_batches(
        semantic_input,
        max_input_chars=minimum_chars,
    )

    assert len(batches) == 1
    batch = batches[0]
    assert batch.input_chars <= minimum_chars
    assert batch.semantic_input.rules[0].linked_requirement_ids == [
        "REQ-DIRECT"
    ]
    assert batch.semantic_input.rules[0].context_requirement_ids == [
        "REQ-C1",
        "REQ-C2",
        "REQ-C3",
    ]
    assert [
        item.requirement_id for item in batch.semantic_input.linked_requirements
    ] == ["REQ-DIRECT", "REQ-C1", "REQ-C2", "REQ-C3"]


def test_single_complete_score_point_over_budget_fails_closed() -> None:
    with pytest.raises(ValueError, match="为避免超限请求已阻断"):
        build_score_semantic_batches(
            _semantic_input(),
            max_input_chars=100,
        )


def test_technical_subheadings_are_preferred_batch_boundaries() -> None:
    semantic_input = _semantic_input()
    rules = [
        semantic_input.rules[0].model_copy(
            update={
                "rule_id": f"SR-{index}",
                "source_order": index - 1,
                "title": f"技术任务{index}",
                "source_hierarchy": [
                    "技术评分",
                    "技术路线" if index <= 2 else "质量控制",
                    f"技术任务{index}",
                ],
            }
        )
        for index in range(1, 5)
    ]
    semantic_input = semantic_input.model_copy(update={"rules": rules})
    pair_budgets = [
        build_score_semantic_batches(
            semantic_input.model_copy(update={"rules": pair}),
            max_input_chars=100_000,
        )[0].input_chars
        for pair in (rules[:2], rules[2:])
    ]
    budget = max(pair_budgets)

    batches = build_score_semantic_batches(
        semantic_input,
        max_input_chars=budget,
    )

    assert [
        [rule.rule_id for rule in batch.semantic_input.rules]
        for batch in batches
    ] == [["SR-1", "SR-2"], ["SR-3", "SR-4"]]
    assert all(batch.input_chars <= budget for batch in batches)


def test_large_technical_group_balances_output_at_natural_prefixes() -> None:
    semantic_input = _semantic_input()
    prefixes = [
        "目标任务",
        "技术路线",
        "核查准备工作",
        "核查准备工作",
        *(
            ["年度全国国土变更调查成果核查"]
            * 5
        ),
        "相关内业核查工作",
        "相关内业核查工作",
        "预期成果",
        "项目组织实施",
        "项目组织实施",
        "意见建议",
        "投标文件整体评价",
    ]
    rules = []
    for index, prefix in enumerate(prefixes, start=1):
        title = (
            prefix
            if prefixes.count(prefix) == 1
            else f"{prefix}—任务{index}"
        )
        rules.append(
            semantic_input.rules[0].model_copy(
                update={
                    "rule_id": f"SR-TECH-{index:02d}",
                    "source_order": index - 1,
                    "title": title,
                    "source_hierarchy": ["技术部分", title],
                }
            )
        )
    technical = semantic_input.model_copy(update={"rules": rules})

    batches = build_score_semantic_batches(
        technical,
        max_input_chars=1_000_000,
    )

    assert [len(batch.semantic_input.rules) for batch in batches] == [5, 5, 5, 1]
    assert all(
        len(batch.semantic_input.rules) <= 5 for batch in batches
    )
    assert batches[0].semantic_input.rules[-1].title.startswith(
        "年度全国国土变更调查成果核查"
    )
    assert batches[1].semantic_input.rules[-1].title.startswith(
        "相关内业核查工作"
    )


def test_unit_requirement_field_is_required_but_retrieval_context_is_optional() -> None:
    semantic_input = _semantic_input_with_requirements()
    legacy = _valid_candidate()
    del legacy["interpretations"][0]["units"][0]["linked_requirement_ids"]

    def legacy_fake(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        return json.dumps(legacy, ensure_ascii=False)

    with pytest.raises(ScoreSemanticInferenceError) as missing:
        LLMScoreSemanticProvider(
            legacy_fake,
            model_fingerprint="fake-model:v1",
        ).interpret(semantic_input)
    assert any(
        "linked_requirement_ids" in error and "Field required" in error
        for error in missing.value.errors
    )

    incomplete = _valid_candidate()
    incomplete["interpretations"][0]["units"][0][
        "linked_requirement_ids"
    ] = ["REQ-DIRECT"]

    def incomplete_fake(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        return json.dumps(incomplete, ensure_ascii=False)

    result = LLMScoreSemanticProvider(
        incomplete_fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    assert result.candidate.interpretations[0].units[
        0
    ].linked_requirement_ids == ["REQ-DIRECT"]


def test_award_cleanup_preserves_minutes_and_ignores_only_terminal_particle() -> None:
    assert "40分钟" in semantic_coverage_text(
        "投标人须在40分钟内提供书面说明，满足要求得4分"
    )
    assert (
        uncovered_semantic_source_text(
            "投标人具有甲级资质（专业含地理信息系统工程）的，得3分",
            ["投标人具有甲级资质（专业含地理信息系统工程）"],
        )
        == ""
    )
    assert uncovered_semantic_source_text(
        "投标人须在40分钟内提供书面说明，满足要求得4分",
        ["投标人须提供书面说明"],
    )


def test_coverage_excludes_award_mechanics_and_redundant_common_wrappers() -> None:
    price = (
        "满足招标文件要求且有效投标价格最低的投标报价，"
        "为评标基准价，其价格分为满分。"
    )
    assert (
        uncovered_semantic_source_text(
            price,
            [
                "满足招标文件要求",
                "有效投标价格最低的投标报价",
            ],
        )
        == ""
    )

    common = (
        "投标人应在投标文件中提供的业绩证明材料包括以下两项，"
        "各业绩须同时提供①+②证明材料，方可得分："
        "①业绩合同主要页；②项目验收意见复印件。"
        "未提供完整资料的人员不得分。"
        "不符合要求的人员不得分。"
    )
    complete = [
        "各业绩须同时提供①+②证明材料，方可得分",
        "①业绩合同主要页",
        "②项目验收意见复印件",
    ]
    assert uncovered_semantic_source_text(common, complete) == ""
    assert "项目验收意见复印件" in uncovered_semantic_source_text(
        common,
        complete[:-1],
    )


def test_mechanics_cleanup_preserves_real_business_evidence_constraints() -> None:
    performance_common = (
        "所有业绩项目的开始时间和验收时间必须在2023年1月1日至"
        "2026年4月30日之间。投标人应在投标文件中提供的业绩证明"
        "材料包括以下两项，各业绩须同时提供①+②证明材料，方可得分："
        "①业绩合同主要页（包括合同首页、合同金额页、合同主要内容"
        "所在页及签字盖章页）；②项目验收意见复印件。证明资料①中的"
        "工作内容应和②中的工作内容一致。"
    )
    personnel_common = (
        "投标人须同时提供①《项目组组成表》、②所有团队人员的"
        "《工作简历表》、③团队人员应为投标人单位在职员工，需提供"
        "人员2025年11月至今任意一个月的社保证明或劳动合同复印件。"
        "未提供完整资料的人员不得分。“职称”以职称证书复印件为准。"
    )
    price_common = (
        "评标委员会启动异常低价投标审查后，投标人不能提供书面说明、"
        "证明材料，或者提供的书面说明、证明材料不能证明其报价合理性"
        "的，其投标将作为无效投标被拒绝。"
    )

    retained = semantic_coverage_text(
        performance_common + personnel_common + price_common
    )
    for material_requirement in (
        "2023年1月1日至2026年4月30日",
        "各业绩须同时提供①+②证明材料",
        "合同金额页",
        "签字盖章页",
        "项目验收意见复印件",
        "工作内容一致",
        "项目组组成表",
        "工作简历表",
        "2025年11月至今任意一个月",
        "社保证明",
        "劳动合同复印件",
        "职称证书复印件",
        "书面说明",
        "证明材料不能证明其报价合理性",
        "无效投标被拒绝",
    ):
        assert semantic_coverage_text(material_requirement) in retained


def test_evidence_bundle_may_keep_its_internal_page_enumeration() -> None:
    excerpt = (
        "业绩合同主要页（包括合同首页、合同金额页、"
        "合同主要内容所在页及签字盖章页）"
    )
    evidence = ScoreConditionCandidate(
        condition_key="SP-E-C01",
        text=excerpt,
        normalized_condition=excerpt,
        condition_role="evidence",
        source_excerpt=excerpt,
        source_level_id=None,
        semantic_subject="业绩合同主要页",
        response_intent="提供完整业绩合同主要页",
        required_evidence_types=["业绩合同主要页"],
        confidence=1.0,
    )
    _require_atomic_substantive_enumerations(
        excerpt,
        [evidence],
        label="业绩证明材料",
    )

    content = evidence.model_copy(
        update={"condition_role": "content"}
    )
    with pytest.raises(ValueError, match="合并了多个"):
        _require_atomic_substantive_enumerations(
            excerpt,
            [content],
            label="正文内容",
        )


def test_shared_predicate_quote_expands_only_to_unique_frozen_source_atom() -> None:
    source = (
        "“作为负责人从事调查相关技术工作”、"
        "“从事调查相关工作”以《项目组组成表》中填写"
        "“从事相关技术工作年限”的年限为准；"
    )
    virtual_quote = (
        "“作为负责人从事调查相关技术工作”"
        "以《项目组组成表》中填写"
        "“从事相关技术工作年限”的年限为准"
    )
    matches = LLMScoreSemanticProvider._shared_predicate_envelope_matches(
        source,
        virtual_quote,
        scope_start=0,
        scope_end=len(source),
    )
    assert matches == [(0, len(source) - 1)]
    assert source[matches[0][0] : matches[0][1]].startswith(
        "“作为负责人从事调查相关技术工作”、"
    )
    assert (
        LLMScoreSemanticProvider._shared_predicate_envelope_matches(
            source,
            "负责人工作年限以项目组组成表为准",
            scope_start=0,
            scope_end=len(source),
        )
        == []
    )


def _qualification_semantic_case(
    *,
    condition_join: str,
    include_common: bool,
) -> tuple[ScoreSemanticInput, dict]:
    source = (
        "投标人具有甲级测绘资质（专业含摄影测量与遥感、"
        "或地理信息系统工程）的，得3分；没有不得分，"
        "本项最高3分。注：提供有效期内证书复印件加盖公章，否则不得分。"
    )
    full_level = (
        "投标人具有甲级测绘资质（专业含摄影测量与遥感、"
        "或地理信息系统工程）的，得3分"
    )
    zero_level = "没有不得分"
    common = "提供有效期内证书复印件加盖公章，否则不得分"
    semantic_input = ScoreSemanticInput(
        source_snapshot_hash="qualification-source",
        deterministic_structure_hash="qualification-structure",
        total_points=3.0,
        groups=[
            DeterministicScoreGroupInput(
                group_id="SG-Q",
                title="商务部分",
                source_order=0,
                declared_points=3.0,
            )
        ],
        rules=[
            DeterministicScoreRuleInput(
                rule_id="SR-Q",
                group_id="SG-Q",
                source_order=0,
                title="测绘资质",
                raw_criterion=source,
                common_criterion=common,
                max_points=3.0,
                levels=[
                    DeterministicScoreLevelInput(
                        level_id="SL-Q-FULL",
                        label="3分档",
                        points=3.0,
                        criterion=full_level,
                        source_order=0,
                    ),
                    DeterministicScoreLevelInput(
                        level_id="SL-Q-ZERO",
                        label="0分档",
                        points=0.0,
                        criterion=zero_level,
                        source_order=1,
                    ),
                ],
                source_anchors=[
                    ScoreSourceAnchorInput(
                        source_input_id="score-doc",
                        chunk_id="qualification",
                        page=1,
                        location="table[0]/row[1]",
                        source_text=source,
                    )
                ],
            )
        ],
    )

    def condition(
        key: str,
        excerpt: str,
        role: str,
        level_id: str | None,
    ) -> dict:
        start = source.index(excerpt)
        return {
            "condition_key": key,
            "text": excerpt,
            "normalized_condition": excerpt,
            "condition_role": role,
            "source_excerpt": excerpt,
            "source_anchor_index": 0,
            "source_span_start": start,
            "source_span_end": start + len(excerpt),
            "source_level_id": level_id,
            "semantic_subject": "测绘资质",
            "response_intent": "提供并核验测绘资质",
            "required_evidence_types": (
                ["测绘资质证书"] if role == "evidence" else []
            ),
            "confidence": 0.99,
        }

    conditions = [
        condition(
            "SR-Q-C1",
            "投标人具有甲级测绘资质（专业含摄影测量与遥感",
            "constraint",
            "SL-Q-FULL",
        ),
        condition(
            "SR-Q-C2",
            "地理信息系统工程）的",
            "constraint",
            "SL-Q-FULL",
        ),
    ]
    if include_common:
        conditions.append(
            condition(
                "SR-Q-C3",
                common,
                "evidence",
                None,
            )
        )
    candidate = {
        "schema_version": "v3-score-semantic-candidate-6",
        "interpretations": [
            {
                "rule_id": "SR-Q",
                "shared_context": "测绘资质及证明材料",
                "units": [
                    {
                        "unit_key": "SR-Q-U1",
                        "title": "测绘资质",
                        "source_excerpt": "甲级测绘资质与证明材料",
                        "outline_path": ["商务部分", "测绘资质"],
                        "band_semantics": [
                            {
                                "level_id": "SL-Q-FULL",
                                "attainment": "full",
                                "semantic_summary": "满足甲级资质条件得满分",
                            },
                            {
                                "level_id": "SL-Q-ZERO",
                                "attainment": "zero",
                                "semantic_summary": "没有资质不得分",
                            },
                        ],
                        "full_score_conditions": conditions,
                        "condition_join": condition_join,
                        "linked_requirement_ids": [],
                        "response_scope": "section",
                        "response_expectation": "证明满足甲级测绘资质要求",
                        "required_evidence_types": (
                            ["测绘资质证书"] if include_common else []
                        ),
                        "confidence": 0.99,
                        "review_status": "confirmed",
                        "review_reason": None,
                    }
                ],
                "confidence": 0.99,
                "review_status": "confirmed",
                "review_reason": None,
            }
        ],
    }
    return semantic_input, candidate


def test_or_relationship_mismatch_warns_without_blocking() -> None:
    semantic_input, candidate = _qualification_semantic_case(
        condition_join="all",
        include_common=True,
    )

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    warned = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    assert any("含“或”关系" in error for error in warned.warnings)
    assert warned.candidate.interpretations[0].review_status == "needs_human"

    candidate["interpretations"][0]["units"][0]["condition_join"] = "mixed"
    result = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    assert (
        result.candidate.interpretations[0].units[0].condition_join
        == "mixed"
    )


def test_missing_common_evidence_warns_and_condition_join_is_persisted() -> None:
    semantic_input, missing_common = _qualification_semantic_case(
        condition_join="mixed",
        include_common=False,
    )

    def missing_fake(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        return json.dumps(missing_common, ensure_ascii=False)

    warned = LLMScoreSemanticProvider(
        missing_fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    assert any(
        "共同资格或证明要求" in error for error in warned.warnings
    )
    assert warned.candidate.interpretations[0].review_status == "needs_human"

    semantic_input, complete = _qualification_semantic_case(
        condition_join="mixed",
        include_common=True,
    )
    wrong_role = json.loads(json.dumps(complete, ensure_ascii=False))
    wrong_role["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][-1]["condition_role"] = "constraint"

    def wrong_role_fake(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        return json.dumps(wrong_role, ensure_ascii=False)

    constraint_result = LLMScoreSemanticProvider(
        wrong_role_fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    constraint_condition = constraint_result.candidate.interpretations[
        0
    ].units[0].full_score_conditions[-1]
    assert constraint_condition.condition_role == "constraint"
    assert constraint_condition.required_evidence_types == [
        "测绘资质证书"
    ]

    def complete_fake(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        return json.dumps(complete, ensure_ascii=False)

    result = LLMScoreSemanticProvider(
        complete_fake,
        model_fingerprint="fake-model:v1",
    ).interpret(semantic_input)
    anchor = SourceAnchor(
        source_input_id="score-doc",
        chunk_id="qualification",
        location="table[0]/row[1]",
        page=1,
    )
    structural = ScoreModel(
        model_id="SM-Q",
        source_input_ids=["score-doc"],
        total_points=3.0,
        groups=[ScoreGroup(group_id="SG-Q", title="商务部分")],
        points=[
            ScorePoint(
                score_point_id="SR-Q",
                group_id="SG-Q",
                title="测绘资质",
                criterion=semantic_input.rules[0].raw_criterion,
                max_points=3.0,
                response_expectation="响应测绘资质",
                source_anchors=[anchor],
                confidence=1.0,
            )
        ],
    )
    compiled = ScoreAgent.apply_semantic_candidate(
        structural,
        result.candidate,
    )
    assert compiled.points[0].response_units[0].condition_join == "mixed"
    assert (
        compiled.points[0].score_conditions[0].subject
        == result.candidate.interpretations[0].units[0]
        .full_score_conditions[0].semantic_subject
    )
    assert len(compiled.points[0].scoring_levels) == 2
    warned_compiled = ScoreAgent.apply_semantic_candidate(
        structural,
        warned.candidate,
    )
    assert warned_compiled.points[0].review_status == "needs_review"


def test_condition_evidence_types_are_deterministically_aggregated_to_unit() -> None:
    candidate = _valid_candidate()
    candidate["interpretations"][0]["units"][0][
        "required_evidence_types"
    ] = []

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    ).interpret(_semantic_input())
    assert result.candidate.interpretations[0].units[
        0
    ].required_evidence_types == ["工作方案", "数据清单", "方法说明"]


@pytest.mark.parametrize(
    "missing_field",
    ["normalized_condition", "condition_role"],
)
def test_v4_condition_classification_fields_are_required(
    missing_field: str,
) -> None:
    candidate = _valid_candidate()
    del candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0][missing_field]

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(ScoreSemanticInferenceError) as caught:
        LLMScoreSemanticProvider(
            fake,
            model_fingerprint="fake-model:v1",
        ).interpret(_semantic_input())
    assert any(missing_field in error for error in caught.value.errors)


def test_evidence_condition_requires_explicit_evidence_type() -> None:
    candidate = _valid_candidate()
    condition = candidate["interpretations"][0]["units"][0][
        "full_score_conditions"
    ][0]
    condition["condition_role"] = "evidence"
    condition["required_evidence_types"] = []

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(ScoreSemanticInferenceError) as caught:
        LLMScoreSemanticProvider(
            fake,
            model_fingerprint="fake-model:v1",
        ).interpret(_semantic_input())
    assert any(
        "必须显式提供 required_evidence_types" in error
        for error in caught.value.errors
    )


def test_batch_cache_write_failure_is_fail_closed() -> None:
    class BrokenWriteCache:
        def get(self, **kwargs: object) -> None:
            return None

        def put(self, **kwargs: object) -> None:
            raise OSError("disk full")

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(_valid_candidate(), ensure_ascii=False)

    with pytest.raises(ScoreSemanticInferenceError) as caught:
        LLMScoreSemanticProvider(
            fake,
            model_fingerprint="fake-model:v1",
            batch_cache=BrokenWriteCache(),
        ).interpret(_semantic_input())
    assert caught.value.code == "score_semantic_batch_cache_write_failed"


def test_batch_cache_read_failure_is_an_observable_miss() -> None:
    class BrokenReadCache:
        def get(self, **kwargs: object) -> None:
            raise OSError("corrupt cache")

        def put(self, **kwargs: object) -> None:
            return None

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return json.dumps(_valid_candidate(), ensure_ascii=False)

    with pytest.warns(RuntimeWarning, match="可观测 miss"):
        result = LLMScoreSemanticProvider(
            fake,
            model_fingerprint="fake-model:v1",
            batch_cache=BrokenReadCache(),
        ).interpret(_semantic_input())
    assert result.attempt_count == 1


def test_llm_calls_publish_initial_and_repair_batch_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata: list[dict[str, object]] = []

    @contextmanager
    def capture_metadata(**value: object):
        metadata.append(value)
        yield

    monkeypatch.setattr(
        "document_pipeline.llm_telemetry.llm_request_metadata",
        capture_metadata,
    )
    outputs = iter(
        [
            "not-json",
            json.dumps(_valid_candidate(), ensure_ascii=False),
        ]
    )

    def fake(messages: list[dict[str, str]], temperature: float) -> str:
        return next(outputs)

    provider = LLMScoreSemanticProvider(
        fake,
        model_fingerprint="fake-model:v1",
    )
    result = provider.interpret(_semantic_input())

    assert result.attempt_count == 2
    assert [item["attempt_kind"] for item in metadata] == [
        "initial",
        "repair",
    ]
    assert metadata[0]["batch_id"] == metadata[1]["batch_id"]
    assert metadata[0]["batch_group_id"] == "SG-1"
    assert metadata[0]["input_hash"] == metadata[1]["input_hash"]
    assert all(
        int(item["rendered_request_chars"]) > 0 for item in metadata
    )
    assert (
        int(metadata[1]["rendered_request_chars"])
        > int(metadata[0]["rendered_request_chars"])
    )


def test_repair_context_never_truncates_json_mid_token() -> None:
    oversized = _valid_candidate()
    oversized["interpretations"][0]["units"][0][
        "full_score_conditions"
    ] *= 120
    raw = json.dumps(oversized, ensure_ascii=False)
    assert len(raw) > 12_000

    label, context = LLMScoreSemanticProvider._bounded_repair_context(raw)

    assert label == "待修复输出摘要"
    decoded = json.loads(context)
    assert decoded["output_chars"] == len(raw)
    assert len(context) <= 12_000


def test_file_batch_cache_reuses_only_strictly_matching_input() -> None:
    with tempfile.TemporaryDirectory(prefix="score-semantic-cache-") as directory:
        _assert_file_batch_cache(Path(directory))


def test_completed_group_is_reused_after_later_group_failure() -> None:
    semantic_input = _multi_rule_input()
    second_group = DeterministicScoreGroupInput(
        group_id="SG-2",
        title="商务评分",
        source_order=1,
        declared_points=2.0,
    )
    second_rule = semantic_input.rules[1].model_copy(
        update={"group_id": second_group.group_id}
    )
    grouped_input = semantic_input.model_copy(
        update={
            "groups": [semantic_input.groups[0], second_group],
            "rules": [semantic_input.rules[0], second_rule],
        }
    )

    with tempfile.TemporaryDirectory(prefix="score-semantic-partial-cache-") as directory:
        cache = FileScoreSemanticBatchCache(Path(directory) / "score-cache")
        first_outputs = iter(
            [
                json.dumps(_candidate_for_rule("SR-1"), ensure_ascii=False),
                "not-json",
                "still-not-json",
            ]
        )
        first_calls = 0

        def fail_second_group(
            messages: list[dict[str, str]],
            temperature: float,
        ) -> str:
            nonlocal first_calls
            first_calls += 1
            return next(first_outputs)

        with pytest.raises(ScoreSemanticInferenceError):
            LLMScoreSemanticProvider(
                fail_second_group,
                model_fingerprint="fake-model:v1",
                provider_fingerprint="fake-provider:v1",
                batch_cache=cache,
            ).interpret(grouped_input)

        assert first_calls == 3
        assert len(list((Path(directory) / "score-cache").rglob("*.json"))) == 1

        retry_calls = 0

        def retry_only_failed_group(
            messages: list[dict[str, str]],
            temperature: float,
        ) -> str:
            nonlocal retry_calls
            retry_calls += 1
            return json.dumps(_candidate_for_rule("SR-2"), ensure_ascii=False)

        result = LLMScoreSemanticProvider(
            retry_only_failed_group,
            model_fingerprint="fake-model:v1",
            provider_fingerprint="fake-provider:v1",
            batch_cache=cache,
        ).interpret(grouped_input)

        assert retry_calls == 1
        assert result.attempt_count == 1
        assert [
            interpretation.rule_id
            for interpretation in result.candidate.interpretations
        ] == ["SR-1", "SR-2"]


def _assert_file_batch_cache(tmp_path: Path) -> None:
    cache = FileScoreSemanticBatchCache(tmp_path / "score-cache")
    calls = 0

    def first_fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(_valid_candidate(), ensure_ascii=False)

    first = LLMScoreSemanticProvider(
        first_fake,
        model_fingerprint="fake-model:v1",
        provider_fingerprint="fake-provider:v1",
        batch_cache=cache,
    ).interpret(_semantic_input())

    def cache_miss_is_failure(
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        raise AssertionError("strict cache hit should avoid the transport")

    second = LLMScoreSemanticProvider(
        cache_miss_is_failure,
        model_fingerprint="fake-model:v1",
        provider_fingerprint="fake-provider:v1",
        batch_cache=cache,
    ).interpret(_semantic_input())

    assert first.attempt_count == 1
    assert second.attempt_count == 0
    assert calls == 1
    cache_path = next((tmp_path / "score-cache").rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["input_hash"] = "stale-input"
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    third_calls = 0

    def third_fake(messages: list[dict[str, str]], temperature: float) -> str:
        nonlocal third_calls
        third_calls += 1
        return json.dumps(_valid_candidate(), ensure_ascii=False)

    third = LLMScoreSemanticProvider(
        third_fake,
        model_fingerprint="fake-model:v1",
        provider_fingerprint="fake-provider:v1",
        batch_cache=cache,
    ).interpret(_semantic_input())

    assert third.attempt_count == 1
    assert third_calls == 1
