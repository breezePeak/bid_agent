from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    InputRole,
    ProjectFact,
    RequirementLedger,
    ScoreModel,
    SourceAnchor,
    SourceBlock,
    SourceIndex,
)
from document_pipeline.planning_agent import PlanningAgent  # noqa: E402
from document_pipeline.planning_inference import (  # noqa: E402
    ChapterOutlineCandidate,
    CitedStatementCandidate,
    FileOutlineFragmentCache,
    LLMOutlineDecompositionProvider,
    LLMProjectUnderstandingProvider,
    OutlineDecompositionInput,
    PlanningInferenceValidationError,
    ProjectUnderstandingCandidate,
    ProjectUnderstandingInput,
)
from document_pipeline.project_model import (  # noqa: E402
    _is_text_supported,
    audit_project_model,
)


def _project_request() -> ProjectUnderstandingInput:
    return ProjectUnderstandingInput(
        requirement_ledger={
            "requirements": [
                {
                    "requirement_id": "R-1",
                    "status": "active",
                    "original_text": "完成调查监测数据核实处理",
                    "normalized_requirement": "完成调查监测数据核实处理",
                }
            ]
        },
        score_model={
            "groups": [{"group_id": "SG-1"}],
            "points": [
                {
                    "score_point_id": "SP-1",
                    "title": "调查监测数据核实处理",
                    "criterion": "完成调查监测数据核实处理",
                    "score_conditions": [
                        {"condition_id": "SP-1-C01"}
                    ],
                    "response_units": [],
                }
            ],
        },
        source_context=[
            {
                "block_id": "B-1",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "B-1"},
                "content": "全国国土变更调查核实项目",
            }
        ],
    )


def _project_candidate(*, source_ref: str = "SourceIndex:B-1") -> dict:
    return {
        "project_name": {
            "text": "全国国土变更调查核实项目",
            "upstream_refs": [source_ref],
            "confidence": 0.99,
        },
        "goals": [
            {
                "text": "完成调查监测数据核实处理",
                "upstream_refs": [
                    "RequirementLedger:R-1",
                    "ScoreModel:SP-1",
                ],
                "confidence": 0.98,
            }
        ],
        "covered_requirement_ids": ["R-1"],
        "covered_score_point_ids": ["SP-1"],
        "review_status": "confirmed",
    }


def _outline_request() -> OutlineDecompositionInput:
    return OutlineDecompositionInput(
        requirement_ledger={
            "requirements": [
                {
                    "requirement_id": "R-1",
                    "severity": "major",
                    "status": "confirmed",
                }
            ]
        },
        score_model={
            "groups": [{"group_id": "SG-1", "title": "评分组"}],
            "points": [
                {
                    "score_point_id": "SP-1",
                    "group_id": "SG-1",
                    "response_scope": "section",
                    "linked_requirement_ids": ["R-1"],
                    "score_conditions": [
                        {
                            "condition_id": "SP-1-C01",
                            "condition_role": "content",
                            "response_intent": "完整说明评分条件",
                        }
                    ],
                    "response_units": [
                        {
                            "unit_id": "SP-1-U01",
                            "title": "评分任务",
                            "condition_ids": ["SP-1-C01"],
                            "linked_requirement_ids": ["R-1"],
                            "response_scope": "section",
                            "response_expectation": "完整承接评分任务",
                        }
                    ],
                }
            ]
        },
    )


def _outline_candidate(*, include_condition: bool) -> dict:
    return {
        "nodes": [
            {
                "local_id": "chapter-special",
                "parent_local_id": None,
                "order": 0,
                "title": "评分组",
                "purpose": "完整承接评分任务",
                "writing_objectives": ["逐项响应满分要求"],
                "primary_response_unit_ids": ["SP-1-U01"],
                "supporting_response_unit_ids": [],
                "score_condition_ids": (
                    ["SP-1-C01"] if include_condition else []
                ),
                "requirement_ids": ["R-1"],
                "required_mentions": [],
                "planned_tables": [],
                "planned_figures": [],
                "target_size": 800,
                "template_slot_ids": [],
                "confidence": 0.99,
                "needs_human": False,
            }
        ],
        "document_quality_response_unit_ids": [],
        "review_status": "draft",
    }


def _large_outline_request(point_count: int = 9) -> OutlineDecompositionInput:
    requirements = []
    points = []
    for index in range(point_count):
        suffix = f"{index + 1:02d}"
        requirement_id = f"R-{suffix}"
        point_id = f"SP-{suffix}"
        condition_id = f"{point_id}-C01"
        unit_id = f"{point_id}-U01"
        requirements.append(
            {
                "requirement_id": requirement_id,
                "severity": "major",
                "status": "confirmed",
            }
        )
        points.append(
            {
                "score_point_id": point_id,
                "group_id": "SG-1",
                "max_points": 1,
                "linked_requirement_ids": [requirement_id],
                "score_conditions": [
                    {
                        "condition_id": condition_id,
                        "condition_role": "content",
                        "response_intent": f"完整说明评分条件 {suffix}",
                    }
                ],
                "response_units": [
                    {
                        "unit_id": unit_id,
                        "title": f"评分任务 {suffix}",
                        "condition_ids": [condition_id],
                        "linked_requirement_ids": [requirement_id],
                        "response_scope": "section",
                        "response_expectation": f"完整承接评分任务 {suffix}",
                    }
                ],
            }
        )
    return OutlineDecompositionInput(
        requirement_ledger={"requirements": requirements},
        score_model={
            "groups": [{"group_id": "SG-1", "title": "评分组"}],
            "points": points,
            "total_points": point_count,
        },
    )


def _outline_fragment_for_messages(messages: list[dict[str, str]]) -> str:
    frozen = json.loads(
        messages[1]["content"].split(
            "只能引用其中已有的 ID 和事实：\n",
            1,
        )[1]
    )
    nodes = [
        {
            "local_id": "root",
            "parent_local_id": None,
            "order": 0,
            "title": frozen["score_model"]["groups"][0]["title"],
            "purpose": "承接当前评分组",
            "confidence": 1.0,
        }
    ]
    for point in frozen["score_model"]["points"]:
        unit = point["response_units"][0]
        condition_id = unit["condition_ids"][0]
        requirement_id = unit["linked_requirement_ids"][0]
        nodes.append(
            {
                "local_id": f"node-{unit['unit_id']}",
                "parent_local_id": "root",
                "order": len(nodes),
                "title": unit["title"],
                "purpose": unit["response_expectation"],
                "primary_response_unit_ids": [unit["unit_id"]],
                "score_condition_ids": [condition_id],
                "requirement_ids": [requirement_id],
                "confidence": 1.0,
            }
        )
    return json.dumps(
        {
            "nodes": nodes,
            "document_quality_response_unit_ids": [],
            "review_status": "draft",
        },
        ensure_ascii=False,
    )


def test_forged_project_reference_gets_one_controlled_repair() -> None:
    outputs = iter(
        [
            json.dumps(
                _project_candidate(source_ref="SourceIndex:invented"),
                ensure_ascii=False,
            ),
            json.dumps(_project_candidate(), ensure_ascii=False),
        ]
    )
    calls: list[list[dict[str, str]]] = []

    def fake(
        messages: list[dict[str, str]],
        *,
        temperature: float,
    ) -> str:
        assert temperature == 0.1
        calls.append(messages)
        return next(outputs)

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(_project_request())

    assert len(calls) == 2
    assert result.attempt_count == 2
    assert result.candidate.project_name is not None
    assert result.candidate.project_name.text == "全国国土变更调查核实项目"
    assert len(result.provider_fingerprint) == 64
    assert "唯一一次受控修复机会" in calls[1][-1]["content"]
    assert "SourceIndex:invented" in calls[1][-1]["content"]


def test_forged_project_reference_fails_closed_after_repair() -> None:
    invalid = json.dumps(
        _project_candidate(source_ref="SourceIndex:invented"),
        ensure_ascii=False,
    )
    calls = 0

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        nonlocal calls
        calls += 1
        return invalid

    provider = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    )
    with pytest.raises(PlanningInferenceValidationError):
        provider.understand(_project_request())

    assert calls == 2


def test_unsupported_confirmed_project_fact_gets_one_controlled_repair() -> None:
    unsupported = _project_candidate()
    unsupported["project_name"]["text"] = "凭空编造的项目名称"
    outputs = iter(
        [
            json.dumps(unsupported, ensure_ascii=False),
            json.dumps(_project_candidate(), ensure_ascii=False),
        ]
    )
    calls: list[list[dict[str, str]]] = []

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(_project_request())

    assert len(calls) == 2
    assert result.attempt_count == 2
    assert result.candidate.project_name is not None
    assert result.candidate.project_name.text == "全国国土变更调查核实项目"
    repair_prompt = calls[1][-1]["content"]
    assert '"project_name"' in repair_prompt
    assert "凭空编造的项目名称" in repair_prompt
    assert "全国国土变更调查核实项目" in repair_prompt


def test_project_coverage_ids_without_semantic_refs_fail_closed() -> None:
    empty_shell = {
        "covered_requirement_ids": ["R-1"],
        "covered_score_point_ids": ["SP-1"],
        "review_status": "confirmed",
    }
    calls = 0

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(empty_shell, ensure_ascii=False)

    provider = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    )
    with pytest.raises(PlanningInferenceValidationError):
        provider.understand(_project_request())

    assert calls == 2


def test_project_repair_feedback_contains_complete_coverage_contract() -> None:
    request = _project_request()
    request.requirement_ledger["requirements"].append(
        {
            "requirement_id": "R-blocked",
            "status": "blocked",
            "normalized_requirement": "已阻断要求",
        }
    )
    first = _project_candidate()
    first["covered_requirement_ids"] = []
    repaired = _project_candidate()
    calls: list[list[dict[str, str]]] = []
    outputs = iter(
        [
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(request)

    assert result.attempt_count == 2
    repair_prompt = calls[1][-1]["content"]
    assert '"R-1"' in repair_prompt
    assert '"SP-1"' in repair_prompt
    assert '"RequirementLedger:R-1"' in repair_prompt
    assert '"ScoreModel:SP-1"' in repair_prompt
    assert "R-blocked" not in repair_prompt
    assert "semantic-coverage" in repair_prompt


def test_project_final_validation_error_exposes_last_failure() -> None:
    invalid = _project_candidate(source_ref="SourceIndex:invented")

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(invalid, ensure_ascii=False)

    provider = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    )
    with pytest.raises(
        PlanningInferenceValidationError,
        match="最后校验错误：项目理解引用未知上游 ID",
    ):
        provider.understand(_project_request())


def _multi_source_scope_request() -> ProjectUnderstandingInput:
    return ProjectUnderstandingInput(
        requirement_ledger={"requirements": []},
        score_model={"groups": [], "points": []},
        source_context=[
            {
                "block_id": "B-package",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "C-package"},
                "content": (
                    "投标人应对所投分包招标文件中所有服务进行投标，"
                    "如仅响应相应分包中的部分服务，则其投标将被拒绝。"
                ),
            },
            {
                "block_id": "B-allocation",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "C-allocation"},
                "content": (
                    "分包涉及县区图斑根据回避内容在项目开展后随机分配。"
                ),
            },
        ],
    )


def _multi_source_scope_candidate(*, fabricated_suffix: str = "") -> dict:
    return {
        "scope": [
            {
                "text": (
                    "所投分包须完整响应该分包的全部服务；"
                    "分包涉及的县区图斑将在项目开展后按回避内容随机分配。"
                    + fabricated_suffix
                ),
                "upstream_refs": [
                    "SourceIndex:B-package",
                    "SourceIndex:B-allocation",
                ],
                "confidence": 0.98,
            }
        ],
        "covered_requirement_ids": [],
        "covered_score_point_ids": [],
        "review_status": "confirmed",
    }


def test_multi_source_scope_synthesis_passes_without_repair() -> None:
    calls = 0

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            _multi_source_scope_candidate(),
            ensure_ascii=False,
        )

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(_multi_source_scope_request())

    assert calls == 1
    assert result.attempt_count == 1
    assert len(result.candidate.scope) == 1


def test_multi_source_scope_does_not_hide_unsupported_appended_claim() -> None:
    invalid = json.dumps(
        _multi_source_scope_candidate(
            fabricated_suffix="中标人还必须自费建设卫星发射基地。"
        ),
        ensure_ascii=False,
    )

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return invalid

    provider = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    )
    with pytest.raises(
        PlanningInferenceValidationError,
        match="scope.1 与其引用来源缺少可核验文本关联",
    ):
        provider.understand(_multi_source_scope_request())


def test_short_heading_does_not_prove_a_longer_fabricated_claim() -> None:
    assert not _is_text_supported(
        "测绘资质还要求中标人自费建设卫星发射基地",
        ["测绘资质"],
    )
    assert not _is_text_supported(
        "类似业绩还要求投标人拥有月球基地",
        ["类似业绩"],
    )


def test_controlled_repair_preserves_verified_items_and_drops_regressions() -> None:
    request = ProjectUnderstandingInput(
        requirement_ledger={"requirements": []},
        score_model={"groups": [], "points": []},
        source_context=[
            {
                "block_id": "B-goal",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "C-goal"},
                "content": "完成调查成果核查",
            },
            {
                "block_id": "B-team",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "C-team"},
                "content": (
                    "技术负责人（1分）。驻场人员（3分）：包5-包6按一档"
                    "计分，包7-包9按另一档计分。"
                ),
            },
            {
                "block_id": "B-qualification",
                "input_id": "tender",
                "source_anchor": {"chunk_id": "C-qualification"},
                "content": (
                    "投标人具有甲级测绘资质（专业含摄影测量与遥感、"
                    "或地理信息系统工程）的，得3分。"
                ),
            },
        ],
    )
    first = {
        "goals": [
            {
                "text": "完成调查成果核查",
                "upstream_refs": ["SourceIndex:B-goal"],
                "confidence": 1.0,
            }
        ],
        "scope": [
            {
                "text": (
                    "团队评分覆盖技术负责人和驻场人员；驻场人员适用"
                    "包5至包6或包7至包9的差异化计分规则。"
                ),
                "upstream_refs": ["SourceIndex:B-team"],
                "confidence": 0.98,
            },
            {
                "text": (
                    "投标人具有甲级测绘资质，专业含摄影测量与遥感"
                    "或地理信息系统工程。"
                ),
                "upstream_refs": ["SourceIndex:B-qualification"],
                "confidence": 0.98,
            },
        ],
        "covered_requirement_ids": [],
        "covered_score_point_ids": [],
        "review_status": "confirmed",
    }
    repaired = json.loads(json.dumps(first, ensure_ascii=False))
    repaired["scope"][1]["text"] = "中标人必须自费建设卫星发射基地"
    outputs = iter(
        [
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )
    calls = 0

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        nonlocal calls
        calls += 1
        return next(outputs)

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(request)

    assert calls == 2
    assert result.attempt_count == 2
    assert result.candidate.review_status == "needs_review"
    assert [item.text for item in result.candidate.scope] == [
        (
            "投标人具有甲级测绘资质，专业含摄影测量与遥感"
            "或地理信息系统工程。"
        )
    ]


def test_compiled_project_fact_retains_all_scope_references(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    (runs / "alpha").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "alpha")
    package_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="C-package",
        location="paragraph:1",
    )
    allocation_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="C-allocation",
        location="paragraph:2",
    )
    blocks = [
        SourceBlock(
            block_id="B-package",
            input_id="tender",
            input_role=InputRole.TENDER,
            block_kind="paragraph",
            ordinal=0,
            content=(
                "投标人应对所投分包招标文件中所有服务进行投标，"
                "如仅响应相应分包中的部分服务，则其投标将被拒绝。"
            ),
            source_anchor=package_anchor,
            content_hash="hash-package",
        ),
        SourceBlock(
            block_id="B-allocation",
            input_id="tender",
            input_role=InputRole.TENDER,
            block_kind="paragraph",
            ordinal=1,
            content="分包涉及县区图斑根据回避内容在项目开展后随机分配。",
            source_anchor=allocation_anchor,
            content_hash="hash-allocation",
        ),
    ]
    ledger = RequirementLedger(source_hashes={"tender": "hash-tender"})
    scores = ScoreModel(
        source_hashes={"tender": "hash-tender"},
        model_id="SM-empty",
        source_input_ids=[],
        total_points=0,
    )
    candidate = ProjectUnderstandingCandidate(
        scope=[
            CitedStatementCandidate.model_validate(
                _multi_source_scope_candidate()["scope"][0]
            )
        ],
        review_status="confirmed",
    )

    model = PlanningAgent(context).compile_project_candidate(
        candidate,
        ledger,
        scores,
        blocks,
        revision=1,
    )
    fact = next(
        item
        for item in model.confirmed_facts
        if item.statement == candidate.scope[0].text
    )
    assert fact.upstream_refs == [
        "SourceIndex:B-package",
        "SourceIndex:B-allocation",
    ]
    assert fact.source_anchor == package_anchor
    assert "upstream_refs" not in ProjectFact(
        fact_id="legacy",
        statement="legacy",
    ).model_dump(mode="json")

    source_index = SourceIndex(
        source_hashes={"tender": "hash-tender"},
        input_manifest_revision=1,
        blocks=blocks,
    )
    audit = audit_project_model(model, ledger, scores, source_index)
    assert audit["passed"] is True, audit["findings"]


def test_project_understanding_batches_on_score_point_groups() -> None:
    request = ProjectUnderstandingInput(
        requirement_ledger={
            "requirements": [
                {
                    "requirement_id": "R-1",
                    "status": "active",
                    "normalized_requirement": "完成数据核实",
                },
                {
                    "requirement_id": "R-2",
                    "status": "active",
                    "normalized_requirement": "形成质量报告",
                },
                {
                    "requirement_id": "R-global",
                    "status": "active",
                    "normalized_requirement": "遵守总体服务要求",
                },
            ]
        },
        score_model={
            "groups": [
                {"group_id": "SG-1", "title": "数据核实"},
                {"group_id": "SG-2", "title": "成果质量"},
            ],
            "points": [
                {
                    "score_point_id": "SP-1",
                    "group_id": "SG-1",
                    "title": "数据核实",
                    "criterion": "完成数据核实",
                    "response_expectation": "完整说明数据核实方法",
                    "linked_requirement_ids": ["R-1"],
                    "source_anchors": [
                        {"source_input_id": "tender", "chunk_id": "C-1"}
                    ],
                },
                {
                    "score_point_id": "SP-2",
                    "group_id": "SG-2",
                    "title": "成果质量",
                    "criterion": "形成质量报告",
                    "response_expectation": "完整说明质量报告",
                    "linked_requirement_ids": ["R-2"],
                    "source_anchors": [
                        {"source_input_id": "tender", "chunk_id": "C-2"}
                    ],
                },
            ],
        },
        source_context=[
            {
                "block_id": "B-core",
                "input_id": "tender",
                "content": "全国调查监测项目",
                "source_anchor": {"chunk_id": "C-core"},
            },
            {
                "block_id": "B-1",
                "input_id": "tender",
                "content": "完成数据核实",
                "source_anchor": {"chunk_id": "C-1"},
            },
            {
                "block_id": "B-2",
                "input_id": "tender",
                "content": "形成质量报告",
                "source_anchor": {"chunk_id": "C-2"},
            },
        ],
    )
    seen_batches: list[dict] = []

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        frozen = json.loads(
            messages[1]["content"].split(
                "只能引用其中已有的 ID 和事实：\n",
                1,
            )[1]
        )
        seen_batches.append(frozen)
        batch_kind = frozen["requirement_ledger"]["batch_kind"]
        if batch_kind == "project_core":
            return json.dumps(
                {
                    "project_name": {
                        "text": "全国调查监测项目",
                        "upstream_refs": ["SourceIndex:B-core"],
                        "confidence": 1.0,
                    },
                    "covered_requirement_ids": [],
                    "covered_score_point_ids": [],
                    "review_status": "confirmed",
                },
                ensure_ascii=False,
            )
        point = frozen["score_model"]["points"][0]
        requirement = frozen["requirement_ledger"]["requirements"][0]
        return json.dumps(
            {
                "goals": [
                    {
                        "text": requirement["normalized_requirement"],
                        "upstream_refs": [
                            f"RequirementLedger:{requirement['requirement_id']}",
                            f"ScoreModel:{point['score_point_id']}",
                        ],
                        "confidence": 1.0,
                    }
                ],
                "scope": [
                    {
                        "text": requirement["normalized_requirement"],
                        "upstream_refs": [
                            f"RequirementLedger:{requirement['requirement_id']}",
                            f"ScoreModel:{point['score_point_id']}",
                        ],
                        "confidence": 1.0,
                    }
                ],
                "covered_requirement_ids": [requirement["requirement_id"]],
                "covered_score_point_ids": [point["score_point_id"]],
                "review_status": "confirmed",
            },
            ensure_ascii=False,
        )

    result = LLMProjectUnderstandingProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).understand(request)

    assert len(seen_batches) == 3
    assert [
        [
            point["score_point_id"]
            for point in batch["score_model"]["points"]
        ]
        for batch in seen_batches
    ] == [[], ["SP-1"], ["SP-2"]]
    assert result.candidate.covered_requirement_ids == [
        "R-1",
        "R-2",
        "R-global",
    ]
    assert result.candidate.covered_score_point_ids == ["SP-1", "SP-2"]
    assert result.candidate.facts[-1].local_id == "semantic-coverage"
    assert result.candidate.facts[-1].upstream_refs == [
        "RequirementLedger:R-global"
    ]
    assert result.candidate.scope == []


def test_missing_score_condition_fails_after_one_repair() -> None:
    # Single-batch auto-outline paths now get one controlled repair attempt
    # (repair_attempts=1, matching template_strict and single-point batches).
    # The fake always returns the same invalid output, so both the initial call
    # and the repair call are made before the provider raises the error.
    output = json.dumps(
        _outline_candidate(include_condition=False),
        ensure_ascii=False,
    )
    calls = 0

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        nonlocal calls
        calls += 1
        return output

    with pytest.raises(
        PlanningInferenceValidationError,
        match="目录未精确覆盖可见评分条件",
    ):
        LLMOutlineDecompositionProvider(
            chat_callable=fake,
            model_fingerprint="fake-model:v1",
        ).split(_outline_request())

    assert calls == 2


def test_outline_duplicate_local_orders_are_normalized_depth_first() -> None:
    candidate = _outline_candidate(include_condition=True)
    candidate["nodes"][0]["order"] = 1
    candidate["nodes"].extend(
        [
            {
                "local_id": "grandchild",
                "parent_local_id": "child-a",
                "order": 1,
                "title": "子项 A.1",
                "purpose": "细化子项 A",
                "confidence": 0.9,
            },
            {
                "local_id": "child-a",
                "parent_local_id": "chapter-special",
                "order": 1,
                "title": "子项 A",
                "purpose": "承接第一项说明",
                "confidence": 0.9,
            },
            {
                "local_id": "child-b",
                "parent_local_id": "chapter-special",
                "order": 1,
                "title": "子项 B",
                "purpose": "承接第二项说明",
                "confidence": 0.9,
            },
        ]
    )

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).split(_outline_request())

    assert {
        node.local_id: node.order for node in result.candidate.nodes
    } == {
        "chapter-special": 0,
        "grandchild": 2,
        "child-a": 1,
        "child-b": 3,
    }


def test_outline_valid_global_orders_are_not_rewritten() -> None:
    candidate = _outline_candidate(include_condition=True)
    candidate["nodes"][0]["order"] = 4
    candidate["nodes"].append(
        {
            "local_id": "child",
            "parent_local_id": "chapter-special",
            "order": 7,
            "title": "响应细化",
            "purpose": "细化评分响应",
            "confidence": 0.9,
        }
    )

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).split(_outline_request())

    assert [node.order for node in result.candidate.nodes] == [4, 7]


def test_large_outline_is_batched_and_cached(tmp_path: Path) -> None:
    calls: list[list[dict[str, str]]] = []

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        calls.append(messages)
        return _outline_fragment_for_messages(messages)

    cache = FileOutlineFragmentCache(tmp_path / "outline-cache")
    provider = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
        batch_cache=cache,
    )
    request = _large_outline_request()
    first = provider.split(request)

    assert len(calls) == 2
    assert all(len(call[1]["content"]) <= 12_500 for call in calls)
    assert len(first.candidate.nodes) == 10
    assert provider.last_batch_summary == {
        "outline_batch_count": 2,
        "outline_batch_generated_count": 2,
        "outline_batch_reused_count": 0,
        "outline_batch_failed_count": 0,
    }

    second_provider = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
        batch_cache=cache,
    )
    second = second_provider.split(request)

    assert len(calls) == 2
    assert second.candidate == first.candidate
    assert second_provider.last_batch_summary[
        "outline_batch_reused_count"
    ] == 2


def test_batched_outline_reuses_shared_factor_parent_across_fragments() -> None:
    request_payload = _large_outline_request().model_dump(mode="json")
    for point in request_payload["score_model"]["points"]:
        point["outline_path"] = ["技术方法（43分）"]
        point["response_units"][0]["outline_path"] = ["技术方法（43分）"]
    request = OutlineDecompositionInput.model_validate(request_payload)

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        frozen = json.loads(
            messages[1]["content"].split(
                "只能引用其中已有的 ID 和事实：\n",
                1,
            )[1]
        )
        nodes = [
            {
                "local_id": "root",
                "parent_local_id": None,
                "order": 0,
                "title": "评分组",
                "purpose": "承接评分组",
                "confidence": 1.0,
            },
            {
                "local_id": "technical-method",
                "parent_local_id": "root",
                "order": 1,
                "title": "技术方法（43分）",
                "purpose": "组织技术方法下的评分任务",
                "primary_response_unit_ids": [
                    point["response_units"][0]["unit_id"]
                    for point in frozen["score_model"]["points"]
                ],
                "requirement_ids": [
                    point["response_units"][0]["linked_requirement_ids"][0]
                    for point in frozen["score_model"]["points"]
                ],
                "confidence": 1.0,
            },
        ]
        for point in frozen["score_model"]["points"]:
            unit = point["response_units"][0]
            nodes.append(
                {
                    "local_id": f"node-{unit['unit_id']}",
                    "parent_local_id": "technical-method",
                    "order": len(nodes),
                    "title": unit["title"],
                    "purpose": unit["response_expectation"],
                    "score_condition_ids": unit["condition_ids"],
                    "confidence": 1.0,
                }
            )
        return json.dumps(
            {
                "nodes": nodes,
                "document_quality_response_unit_ids": [],
                "review_status": "draft",
            },
            ensure_ascii=False,
        )

    result = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).split(request)

    factor_nodes = [
        node
        for node in result.candidate.nodes
        if node.title == "技术方法（43分）"
    ]
    assert len(factor_nodes) == 1
    factor_id = factor_nodes[0].local_id
    assert set(factor_nodes[0].primary_response_unit_ids) == {
        f"SP-{index:02d}-U01" for index in range(1, 10)
    }
    assert all(
        node.parent_local_id == factor_id
        for node in result.candidate.nodes
        if node.score_condition_ids
    )


def test_outline_rejects_redundant_task_level_after_source_factor_path() -> None:
    request_payload = _outline_request().model_dump(mode="json")
    point = request_payload["score_model"]["points"][0]
    point["outline_path"] = ["核查准备工作（6分）"]
    point["response_units"][0]["outline_path"] = ["核查准备工作（6分）"]
    request = OutlineDecompositionInput.model_validate(request_payload)
    candidate = {
        "nodes": [
            {
                "local_id": "root",
                "parent_local_id": None,
                "order": 0,
                "title": "评分组",
                "purpose": "承接评分组",
                "confidence": 1.0,
            },
            {
                "local_id": "preparation",
                "parent_local_id": "root",
                "order": 1,
                "title": "核查准备工作（6分）",
                "purpose": "承接来源评分因素",
                "confidence": 1.0,
            },
            {
                "local_id": "redundant-task",
                "parent_local_id": "preparation",
                "order": 2,
                "title": "核查准备与数据检查方法",
                "purpose": "冗余改写任务层",
                "primary_response_unit_ids": ["SP-1-U01"],
                "requirement_ids": ["R-1"],
                "confidence": 1.0,
            },
            {
                "local_id": "condition",
                "parent_local_id": "redundant-task",
                "order": 3,
                "title": "检查方法",
                "purpose": "覆盖原子评分条件",
                "score_condition_ids": ["SP-1-C01"],
                "confidence": 1.0,
            },
        ],
        "document_quality_response_unit_ids": [],
        "review_status": "draft",
    }

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(
        PlanningInferenceValidationError,
        match="未保留 outline_path",
    ):
        LLMOutlineDecompositionProvider(
            chat_callable=fake,
            model_fingerprint="fake-model:v1",
        ).split(request)


def test_truncated_outline_batch_is_split_and_split_checkpoint_is_reused(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    truncate_first_large_batch = True

    def fake(
        messages: list[dict[str, str]],
        *,
        temperature: float,
    ) -> str | dict[str, str]:
        nonlocal truncate_first_large_batch
        frozen = json.loads(
            messages[1]["content"].split(
                "只能引用其中已有的 ID 和事实：\n",
                1,
            )[1]
        )
        point_ids = tuple(
            point["score_point_id"]
            for point in frozen["score_model"]["points"]
        )
        calls.append(point_ids)
        if len(point_ids) > 4 and truncate_first_large_batch:
            truncate_first_large_batch = False
            return {
                "content": '{"nodes":[{"local_id":"root","title":"评分组',
                "finish_reason": "length",
            }
        return _outline_fragment_for_messages(messages)

    cache = FileOutlineFragmentCache(tmp_path / "outline-cache")
    request = _large_outline_request()
    provider = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
        batch_cache=cache,
    )
    first = provider.split(request)

    assert [len(batch) for batch in calls] == [8, 4, 4, 1]
    assert len(first.candidate.nodes) == 10

    calls.clear()
    second_provider = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
        batch_cache=cache,
    )
    second = second_provider.split(request)

    assert calls == []
    assert second.candidate == first.candidate
    assert second_provider.last_batch_summary[
        "outline_batch_reused_count"
    ] == 3


def test_outline_candidate_rejects_duplicate_or_child_first_order() -> None:
    base = _outline_candidate(include_condition=True)
    base["nodes"].append(
        {
            "local_id": "child",
            "parent_local_id": "chapter-special",
            "order": 0,
            "title": "响应细化",
            "purpose": "细化评分响应",
            "confidence": 0.9,
        }
    )
    with pytest.raises(ValueError, match="order 必须全局唯一"):
        ChapterOutlineCandidate.model_validate(base)

    base["nodes"][0]["order"] = 2
    base["nodes"][1]["order"] = 1
    with pytest.raises(ValueError, match="必须排在其父章节之后"):
        ChapterOutlineCandidate.model_validate(base)


def test_document_unit_requirement_is_not_forced_into_visible_chapter() -> None:
    request_payload = _outline_request().model_dump(mode="json")
    request_payload["requirement_ledger"]["requirements"].append(
        {
            "requirement_id": "R-document",
            "severity": "major",
            "status": "confirmed",
        }
    )
    point = request_payload["score_model"]["points"][0]
    point["linked_requirement_ids"].append("R-document")
    point["score_conditions"].append(
        {
            "condition_id": "SP-1-C-document",
            "condition_role": "document",
            "response_intent": "执行全文一致性检查",
        }
    )
    point["response_units"].append(
        {
            "unit_id": "SP-1-U-document",
            "title": "全文一致性",
            "condition_ids": ["SP-1-C-document"],
            "linked_requirement_ids": ["R-document"],
            "response_scope": "document",
            "response_expectation": "执行全文一致性检查",
        }
    )
    request = OutlineDecompositionInput.model_validate(request_payload)
    candidate = _outline_candidate(include_condition=True)
    candidate["document_quality_response_unit_ids"] = [
        "SP-1-U-document"
    ]

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    result = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).split(request)

    assert result.candidate.nodes[0].requirement_ids == ["R-1"]
    assert result.candidate.document_quality_response_unit_ids == [
        "SP-1-U-document"
    ]


def test_hollow_quality_adjective_heading_fails_closed() -> None:
    candidate = _outline_candidate(include_condition=True)
    candidate["nodes"][0]["title"] = "完整性、合理性与针对性"

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        return json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(
        PlanningInferenceValidationError,
        match="标题仅包含空洞质量形容词",
    ):
        LLMOutlineDecompositionProvider(
            chat_callable=fake,
            model_fingerprint="fake-model:v1",
        ).split(_outline_request())


def test_multipoint_batch_node_sharing_triggers_one_controlled_repair() -> None:
    """多点批次首次输出中出现节点共用错误时，必须触发一次受控修复（attempt_count==2）
    并在修复后的合法输出中正常晋级，而不是直接抛出"未触发全对象修复"。
    """
    # 构造一个含 2 个评分点的请求（会生成多点批次 len(spec.point_ids) > 1）
    request = OutlineDecompositionInput(
        requirement_ledger={
            "requirements": [
                {"requirement_id": "R-01", "severity": "major", "status": "confirmed"},
                {"requirement_id": "R-02", "severity": "major", "status": "confirmed"},
            ]
        },
        score_model={
            "groups": [{"group_id": "SG-1", "title": "技术部分"}],
            "points": [
                {
                    "score_point_id": "SP-01",
                    "group_id": "SG-1",
                    "max_points": 5,
                    "linked_requirement_ids": ["R-01"],
                    "score_conditions": [
                        {
                            "condition_id": "SP-01-C01",
                            "condition_role": "content",
                            "response_intent": "说明 A",
                        },
                        {
                            "condition_id": "SP-01-C02",
                            "condition_role": "content",
                            "response_intent": "说明 B",
                        },
                    ],
                    "response_units": [
                        {
                            "unit_id": "SP-01-U01",
                            "title": "组织结构及成员",
                            "condition_ids": ["SP-01-C01", "SP-01-C02"],
                            "linked_requirement_ids": ["R-01"],
                            "response_scope": "section",
                        }
                    ],
                },
                {
                    "score_point_id": "SP-02",
                    "group_id": "SG-1",
                    "max_points": 5,
                    "linked_requirement_ids": ["R-02"],
                    "score_conditions": [
                        {
                            "condition_id": "SP-02-C01",
                            "condition_role": "content",
                            "response_intent": "说明 C",
                        }
                    ],
                    "response_units": [
                        {
                            "unit_id": "SP-02-U01",
                            "title": "技术方案",
                            "condition_ids": ["SP-02-C01"],
                            "linked_requirement_ids": ["R-02"],
                            "response_scope": "section",
                        }
                    ],
                },
            ],
        },
    )

    # 第一次输出：SP-01 的两个 condition 共用同一个节点（非法）
    bad_output = json.dumps(
        {
            "nodes": [
                {
                    "local_id": "root",
                    "parent_local_id": None,
                    "order": 0,
                    "title": "技术部分",
                    "purpose": "技术部分根节点",
                    "primary_response_unit_ids": [],
                    "score_condition_ids": [],
                    "requirement_ids": [],
                    "confidence": 1.0,
                },
                {
                    # 同一个节点塞了 C01 和 C02，触发节点共用错误
                    "local_id": "org-node",
                    "parent_local_id": "root",
                    "order": 1,
                    "title": "组织结构及成员",
                    "purpose": "描述项目组织",
                    "primary_response_unit_ids": ["SP-01-U01"],
                    "score_condition_ids": ["SP-01-C01", "SP-01-C02"],
                    "requirement_ids": ["R-01"],
                    "confidence": 0.9,
                },
                {
                    "local_id": "tech-node",
                    "parent_local_id": "root",
                    "order": 2,
                    "title": "技术方案",
                    "purpose": "描述技术方案",
                    "primary_response_unit_ids": ["SP-02-U01"],
                    "score_condition_ids": ["SP-02-C01"],
                    "requirement_ids": ["R-02"],
                    "confidence": 0.9,
                },
            ],
            "document_quality_response_unit_ids": [],
            "review_status": "draft",
        },
        ensure_ascii=False,
    )

    # 第二次输出（修复后）：C01 和 C02 分别独占独立子节点
    good_output = json.dumps(
        {
            "nodes": [
                {
                    "local_id": "root",
                    "parent_local_id": None,
                    "order": 0,
                    "title": "技术部分",
                    "purpose": "技术部分根节点",
                    "primary_response_unit_ids": [],
                    "score_condition_ids": [],
                    "requirement_ids": [],
                    "confidence": 1.0,
                },
                {
                    "local_id": "org-parent",
                    "parent_local_id": "root",
                    "order": 1,
                    "title": "组织结构及成员",
                    "purpose": "描述项目组织",
                    "primary_response_unit_ids": ["SP-01-U01"],
                    "score_condition_ids": [],
                    "requirement_ids": ["R-01"],
                    "confidence": 0.9,
                },
                {
                    # C01 独占子节点
                    "local_id": "org-c01",
                    "parent_local_id": "org-parent",
                    "order": 2,
                    "title": "组织结构说明 A",
                    "purpose": "说明 A",
                    "primary_response_unit_ids": [],
                    "score_condition_ids": ["SP-01-C01"],
                    "requirement_ids": [],
                    "confidence": 0.9,
                },
                {
                    # C02 独占子节点
                    "local_id": "org-c02",
                    "parent_local_id": "org-parent",
                    "order": 3,
                    "title": "组织结构说明 B",
                    "purpose": "说明 B",
                    "primary_response_unit_ids": [],
                    "score_condition_ids": ["SP-01-C02"],
                    "requirement_ids": [],
                    "confidence": 0.9,
                },
                {
                    "local_id": "tech-node",
                    "parent_local_id": "root",
                    "order": 4,
                    "title": "技术方案",
                    "purpose": "描述技术方案",
                    "primary_response_unit_ids": ["SP-02-U01"],
                    "score_condition_ids": ["SP-02-C01"],
                    "requirement_ids": ["R-02"],
                    "confidence": 0.9,
                },
            ],
            "document_quality_response_unit_ids": [],
            "review_status": "draft",
        },
        ensure_ascii=False,
    )

    outputs = iter([bad_output, good_output])
    calls: list[list[dict[str, str]]] = []

    def fake(messages: list[dict[str, str]], *, temperature: float) -> str:
        calls.append(messages)
        return next(outputs)

    result = LLMOutlineDecompositionProvider(
        chat_callable=fake,
        model_fingerprint="fake-model:v1",
    ).split(request)

    # 必须调用了 2 次（初始 + 修复）
    assert len(calls) == 2, f"预期 2 次调用，实际 {len(calls)} 次"
    # 修复反馈中应包含节点共用修复指导
    repair_prompt = calls[1][-1]["content"]
    assert "节点共用修复" in repair_prompt or "满分条件" in repair_prompt
    # 最终结果应通过校验
    node_ids = {node.local_id for node in result.candidate.nodes}
    assert "org-c01" in node_ids or "org-parent" in node_ids
    # SP-01 的两个条件最终分布在不同节点上
    cond_nodes: dict[str, list[str]] = {}
    for node in result.candidate.nodes:
        for cid in node.score_condition_ids:
            cond_nodes.setdefault(cid, []).append(node.local_id)
    assert cond_nodes.get("SP-01-C01") != cond_nodes.get("SP-01-C02"), (
        "C01 和 C02 仍然共用了同一节点，修复未生效"
    )

