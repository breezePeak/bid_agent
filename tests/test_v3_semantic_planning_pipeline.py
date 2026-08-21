from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import (  # noqa: E402
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.canonicalization import (  # noqa: E402
    canonical_hash,
    canonical_json,
)
from document_pipeline.contracts import (  # noqa: E402
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    ScoreCondition,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    ScoreResponseUnit,
    SourceAnchor,
)
from document_pipeline.deterministic_outline import (  # noqa: E402
    build_deterministic_outline_candidate,
)
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.planning_agent import PlanningAgent  # noqa: E402
from document_pipeline.planning_inference import (  # noqa: E402
    OUTLINE_CAPABILITY_VERSION,
    OUTLINE_PROMPT_VERSION,
    OUTLINE_SCHEMA_VERSION,
    OUTLINE_SKILL_ID,
    ChapterOutlineCandidate,
    ChapterOutlineNodeCandidate,
    PlanningInferenceValidationError,
    StructuredInferenceResult,
)
from document_pipeline.planning_skill_registry import (  # noqa: E402
    CHAPTER_OUTLINE_SPLIT_SKILL,
)
from document_pipeline.score_semantic import (  # noqa: E402
    SCORE_SEMANTIC_CAPABILITY_ID,
    SCORE_SEMANTIC_CAPABILITY_VERSION,
    SCORE_SEMANTIC_SCHEMA_VERSION,
    IndependentScoreUnitCandidate,
    ScoreBandSemanticCandidate,
    ScoreConditionCandidate,
    ScoreRuleSemanticCandidate,
    ScoreSemanticCandidate,
    ScoreSemanticInferenceError,
    ScoreSemanticInferenceResult,
)
from document_pipeline.scoring_outline_policy import (  # noqa: E402
    audit_chapter_blueprint,
)
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


def _planning_result(
    candidate,
    request,
    *,
    capability_id: str,
    prompt_version: str,
    prompt_hash: str,
    schema_version: str,
    provider_fingerprint: str,
    model_fingerprint: str,
):
    raw = canonical_json(candidate.model_dump(mode="json"))
    return StructuredInferenceResult(
        candidate=candidate,
        raw_output=raw,
        normalized_output=raw,
        reasoning="",
        input_snapshot=canonical_json(request.model_dump(mode="json")),
        attempt_count=1,
        capability_id=capability_id,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        schema_version=schema_version,
        provider_fingerprint=provider_fingerprint,
        model_fingerprint=model_fingerprint,
        temperature=0.1,
    )


class _FakeScoreProvider:
    capability_id = SCORE_SEMANTIC_CAPABILITY_ID
    capability_version = SCORE_SEMANTIC_CAPABILITY_VERSION
    prompt_version = "test.score.semantic.v1"
    prompt_hash = canonical_hash({"prompt": prompt_version})
    schema_version = SCORE_SEMANTIC_SCHEMA_VERSION
    provider_fingerprint = "test-provider"
    model_fingerprint = "test-score-model"
    temperature = 0.1

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def interpret(self, semantic_input):
        self.calls.append("score")
        interpretations = []
        for rule in semantic_input.rules:
            anchor_text = rule.source_anchors[0].source_text
            scored_levels = [
                level for level in rule.levels if level.points is not None
            ]
            if scored_levels:
                highest = max(float(level.points) for level in scored_levels)
                full_levels = {
                    level.level_id
                    for level in scored_levels
                    if float(level.points) == highest
                }
            elif rule.levels:
                full_levels = {
                    min(
                        rule.levels,
                        key=lambda level: level.source_order,
                    ).level_id
                }
            else:
                full_levels = set()
            band_semantics = [
                ScoreBandSemanticCandidate(
                    level_id=level.level_id,
                    attainment=(
                        "full"
                        if level.level_id in full_levels
                        else (
                            "zero"
                            if level.points is not None
                            and float(level.points) == 0
                            else "partial"
                        )
                    ),
                    semantic_summary=level.criterion,
                )
                for level in rule.levels
            ]
            condition_sources = (
                [
                    (level.level_id, level.criterion)
                    for level in rule.levels
                    if level.level_id in full_levels
                ]
                if rule.levels
                else [(None, rule.raw_criterion)]
            )
            conditions = []
            for condition_index, (
                source_level_id,
                condition_source,
            ) in enumerate(condition_sources, start=1):
                source_start = anchor_text.find(condition_source)
                assert source_start >= 0
                conditions.append(
                    ScoreConditionCandidate(
                        condition_key=(
                            f"{rule.rule_id}-condition-{condition_index}"
                        ),
                        text=f"完整响应{rule.title}",
                        normalized_condition=condition_source,
                        condition_role="content",
                        source_excerpt=condition_source,
                        source_anchor_index=0,
                        source_span_start=source_start,
                        source_span_end=source_start + len(condition_source),
                        source_level_id=source_level_id,
                        semantic_subject=rule.title,
                        response_intent="完整说明评分要求",
                        confidence=0.99,
                    )
                )
            unit = IndependentScoreUnitCandidate(
                unit_key=f"{rule.rule_id}-semantic-unit",
                title=f"语义任务-{rule.title}",
                source_excerpt=rule.raw_criterion,
                outline_path=rule.source_hierarchy or [rule.title],
                band_semantics=band_semantics,
                full_score_conditions=conditions,
                condition_join="all",
                linked_requirement_ids=list(
                    dict.fromkeys(
                        (
                            *rule.linked_requirement_ids,
                            *rule.context_requirement_ids,
                        )
                    )
                ),
                response_scope="section",
                response_expectation="完整响应评分要求",
                confidence=0.99,
                review_status="confirmed",
            )
            interpretations.append(
                ScoreRuleSemanticCandidate(
                    rule_id=rule.rule_id,
                    shared_context="按项目语义组织评分响应",
                    units=[unit],
                    context_requirement_ids=list(
                        rule.context_requirement_ids
                    ),
                    confidence=0.99,
                    review_status="confirmed",
                )
            )
        candidate = ScoreSemanticCandidate(interpretations=interpretations)
        raw = canonical_json(candidate.model_dump(mode="json"))
        return ScoreSemanticInferenceResult(
            candidate=candidate,
            raw_output=raw,
            normalized_output=raw,
            input_snapshot=canonical_json(
                semantic_input.model_dump(mode="json")
            ),
            attempt_count=1,
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
            temperature=self.temperature,
        )


class _FakeOutlineProvider:
    skill_id = OUTLINE_SKILL_ID
    capability_id = OUTLINE_SKILL_ID
    capability_version = OUTLINE_CAPABILITY_VERSION
    prompt_version = OUTLINE_PROMPT_VERSION
    prompt_hash = CHAPTER_OUTLINE_SPLIT_SKILL.prompt_hash
    schema_version = OUTLINE_SCHEMA_VERSION
    provider_fingerprint = "test-outline-provider"
    model_fingerprint = "test-outline-model"
    temperature = 0.1

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def split(self, request):
        self.calls.append("outline")
        score_model = request.score_model
        active_requirement_ids = {
            item["requirement_id"]
            for item in request.requirement_ledger["requirements"]
            if item["status"] not in {"blocked", "waived"}
        }
        nodes = []
        document_quality_response_unit_ids = []
        next_order = 0
        group_parent_ids = {}
        section_group_ids = {
            point["group_id"]
            for point in score_model["points"]
            if any(
                unit["review_status"] != "blocked"
                and unit["response_scope"] == "section"
                for unit in point["response_units"]
            )
        }
        for group in score_model.get("groups", []):
            if group["group_id"] not in section_group_ids:
                continue
            group_parent_ids[group["group_id"]] = f"group-{group['group_id']}"
            nodes.append(
                ChapterOutlineNodeCandidate(
                    local_id=group_parent_ids[group["group_id"]],
                    order=next_order,
                    title=group["title"],
                    purpose="组织该评分组的独立得分任务",
                    confidence=0.99,
                )
            )
            next_order += 1
        for point in score_model["points"]:
            conditions = {
                condition["condition_id"]: condition
                for condition in point["score_conditions"]
                if condition["review_status"] != "blocked"
            }
            for unit in point["response_units"]:
                if unit["review_status"] == "blocked":
                    continue
                if unit["response_scope"] == "document":
                    document_quality_response_unit_ids.append(
                        unit["unit_id"]
                    )
                    continue

                unit_condition_ids = [
                    condition_id
                    for condition_id in unit["condition_ids"]
                    if condition_id in conditions
                ]
                primary_condition_ids = []
                child_condition_ids = []
                substantive_count = 0
                for condition_id in unit_condition_ids:
                    role = conditions[condition_id]["condition_role"]
                    if role in {"content", "evidence"}:
                        substantive_count += 1
                        if substantive_count > 1:
                            child_condition_ids.append(condition_id)
                            continue
                    primary_condition_ids.append(condition_id)

                primary_local_id = f"unit-{unit['unit_id']}"
                nodes.append(
                    ChapterOutlineNodeCandidate(
                        local_id=primary_local_id,
                        parent_local_id=group_parent_ids[point["group_id"]],
                        order=next_order,
                        title=unit["title"],
                        purpose="证明最终目录来自章节拆分 Skill",
                        primary_response_unit_ids=[unit["unit_id"]],
                        score_condition_ids=primary_condition_ids,
                        requirement_ids=[
                            requirement_id
                            for requirement_id in unit[
                                "linked_requirement_ids"
                            ]
                            if requirement_id in active_requirement_ids
                        ],
                        confidence=0.99,
                    )
                )
                next_order += 1
                for condition_id in child_condition_ids:
                    nodes.append(
                        ChapterOutlineNodeCandidate(
                            local_id=f"condition-{condition_id}",
                            parent_local_id=primary_local_id,
                            order=next_order,
                            title=conditions[condition_id][
                                "normalized_condition"
                            ],
                            purpose="逐项覆盖满分条件",
                            score_condition_ids=[condition_id],
                            confidence=0.99,
                        )
                    )
                    next_order += 1
        candidate = ChapterOutlineCandidate(
            nodes=nodes,
            document_quality_response_unit_ids=(
                document_quality_response_unit_ids
            ),
            review_status="draft",
        )
        return _planning_result(
            candidate,
            request,
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
        )


class _ReviewOnlyScoreProvider(_FakeScoreProvider):
    """Return a source-valid candidate with intentionally incomplete semantics."""

    def interpret(self, semantic_input):
        result = super().interpret(semantic_input)
        interpretations = []
        for interpretation in result.candidate.interpretations:
            units = [
                unit.model_copy(
                    update={
                        "full_score_conditions": [],
                        "review_status": "needs_human",
                        "review_reason": "程序语义审核未完全通过",
                    }
                )
                for unit in interpretation.units
            ]
            interpretations.append(
                interpretation.model_copy(
                    update={
                        "units": units,
                        "review_status": "needs_human",
                        "review_reason": "程序语义审核未完全通过",
                    }
                )
            )
        candidate = result.candidate.model_copy(
            update={"interpretations": interpretations}
        )
        normalized = canonical_json(candidate.model_dump(mode="json"))
        return replace(
            result,
            candidate=candidate,
            raw_output=normalized,
            normalized_output=normalized,
            warnings=("最高得分档语义覆盖需人工复核",),
        )


class _InvalidScoreProvider(_FakeScoreProvider):
    def interpret(self, semantic_input):
        del semantic_input
        self.calls.append("score")
        raise ScoreSemanticInferenceError(
            code="score_semantic_candidate_invalid",
            attempts=2,
            errors=["最终候选未通过程序审核"],
        )


class _ValidationFailureOutlineProvider(_FakeOutlineProvider):
    def __init__(self, calls: list[str], cause_message: str) -> None:
        super().__init__(calls)
        self.cause_message = cause_message

    def split(self, request):
        del request
        self.calls.append("outline")
        try:
            raise ValueError(self.cause_message)
        except ValueError as exc:
            raise PlanningInferenceValidationError(
                "章节目录候选未通过最终程序校验"
            ) from exc


def _prepare_outline_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace_id: str,
    calls: list[str],
    outline_provider,
) -> tuple[WorkspaceContext, V3StageRunner]:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    runs = tmp_path / "runs"
    (runs / workspace_id).mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, workspace_id)
    tender = tmp_path / f"{workspace_id}-tender.md"
    score = tmp_path / f"{workspace_id}-score.md"
    tender.write_text(
        "投标人须制定项目实施方案，并确保数据处理质量。",
        encoding="utf-8",
    )
    score.write_text(
        "技术方案完整、可行，得4分；较完整得2分；不完整得0分。",
        encoding="utf-8",
    )
    inputs = InputManifestService(context)
    inputs.register_local_file(tender, "tender")
    inputs.register_local_file(score, "score")
    SourceNormalizer(context).normalize_active_inputs()
    runner = V3StageRunner(
        context,
        score_semantic_provider=_FakeScoreProvider(calls),
        outline_decomposition_provider=outline_provider,
    )
    runner.run("analyze_requirements")
    runner.run("analyze_scores")
    return context, runner


def test_stage_runner_uses_direct_score_and_outline_providers_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    runs = tmp_path / "runs"
    (runs / "pipeline").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "pipeline")
    tender = tmp_path / "tender.md"
    score = tmp_path / "score.md"
    tender.write_text(
        "投标人须制定项目实施方案，并确保数据处理质量。",
        encoding="utf-8",
    )
    score.write_text(
        "技术方案完整、可行，得4分；较完整得2分；不完整得0分。",
        encoding="utf-8",
    )
    inputs = InputManifestService(context)
    inputs.register_local_file(tender, "tender")
    inputs.register_local_file(score, "score")
    SourceNormalizer(context).normalize_active_inputs()

    calls: list[str] = []
    runner = V3StageRunner(
        context,
        score_semantic_provider=_FakeScoreProvider(calls),
        outline_decomposition_provider=_FakeOutlineProvider(calls),
    )

    def forbidden_legacy_outline(*args, **kwargs):
        raise AssertionError("旧规则型 chapter_blueprint 不得被调用")

    monkeypatch.setattr(
        PlanningAgent,
        "chapter_blueprint",
        forbidden_legacy_outline,
    )
    runner.run("analyze_requirements")
    runner.run("analyze_scores")
    blueprint = runner.run("compile_chapter_blueprint")

    assert calls == ["score", "outline"]
    assert blueprint.planning_model == "score_direct"
    assert blueprint.assignments == []
    assert blueprint.nodes[0].parent_chapter_id is None
    assert blueprint.nodes[0].title == "未分组评分项"
    store = ControlStore(context)
    for kind, expected_provider in (
        ("ScoreModel", _FakeScoreProvider.provider_fingerprint),
        ("ChapterBlueprint", _FakeOutlineProvider.provider_fingerprint),
    ):
        artifact = store.v3_active_artifact(kind)
        assert artifact is not None
        proposal = store.v3_proposal(str(artifact["proposal_id"]))
        assert proposal is not None
        receipt = store.v3_inference_receipt(
            proposal["inference_receipt_refs"][0]["receipt_id"]
        )
        assert receipt is not None
        assert receipt["provider_fingerprint"] == expected_provider


def test_outline_semantic_validation_fails_closed_without_rule_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    provider = _ValidationFailureOutlineProvider(
        calls,
        "目录 primary ScoreResponseUnit 覆盖不完整；missing=['SP-U01']",
    )
    context, runner = _prepare_outline_stage(
        tmp_path,
        monkeypatch,
        workspace_id="outline-semantic-fallback",
        calls=calls,
        outline_provider=provider,
    )

    with pytest.raises(ControlPlaneError) as raised:
        runner.run(
            "compile_chapter_blueprint",
            operation_id="outline-fallback-1",
        )
    assert calls == ["score", "outline"]
    assert raised.value.code == "V3_OUTLINE_INFERENCE_INVALID"
    assert ControlStore(context).v3_active_artifact("ChapterBlueprint") is None


def test_outline_final_semantic_audit_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    provider = _FakeOutlineProvider(calls)
    _, runner = _prepare_outline_stage(
        tmp_path,
        monkeypatch,
        workspace_id="outline-audit-fallback",
        calls=calls,
        outline_provider=provider,
    )
    import document_pipeline.stage_runner as stage_runner_module

    real_audit = stage_runner_module.audit_chapter_blueprint
    audit_calls = 0

    def fail_semantic_audits(*args, **kwargs):
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls <= 2:
            return {
                "passed": False,
                "findings": [
                    {
                        "code": "HOLLOW_QUALITY_HEADING",
                        "message": "最终目录包含空洞质量标题",
                    }
                ],
            }
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(
        stage_runner_module,
        "audit_chapter_blueprint",
        fail_semantic_audits,
    )

    with pytest.raises(ControlPlaneError) as raised:
        runner.run("compile_chapter_blueprint")
    assert calls == ["score", "outline"]
    assert audit_calls == 1
    assert raised.value.code == "V3_BLUEPRINT_COVERAGE_BLOCKED"


def test_outline_fallback_audit_keeps_template_conflict_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    provider = _FakeOutlineProvider(calls)
    context, runner = _prepare_outline_stage(
        tmp_path,
        monkeypatch,
        workspace_id="outline-fallback-hard-template",
        calls=calls,
        outline_provider=provider,
    )
    import document_pipeline.stage_runner as stage_runner_module

    audit_calls = 0

    def semantic_then_template_conflict(*args, **kwargs):
        del args, kwargs
        nonlocal audit_calls
        audit_calls += 1
        return {
            "passed": False,
            "findings": [
                {
                    "code": "TEMPLATE_STRUCTURE_CHANGED",
                    "message": "严格模板结构发生变化",
                }
            ],
        }

    monkeypatch.setattr(
        stage_runner_module,
        "audit_chapter_blueprint",
        semantic_then_template_conflict,
    )

    with pytest.raises(ControlPlaneError) as raised:
        runner.run("compile_chapter_blueprint")

    assert raised.value.code == "V3_BLUEPRINT_TEMPLATE_BLOCKED"
    assert audit_calls == 1
    assert ControlStore(context).v3_active_artifact("ChapterBlueprint") is None


@pytest.mark.parametrize(
    ("workspace_id", "cause_message", "expected_code"),
    [
        (
            "outline-hard-source-error",
            "章节 node-1 引用未知 ScoreResponseUnit: ['U-missing']",
            "V3_OUTLINE_SOURCE_REFERENCE_INVALID",
        ),
        (
            "outline-hard-template-error",
            "严格模板标题或顺序发生变化",
            "V3_OUTLINE_TEMPLATE_INVALID",
        ),
    ],
)
def test_outline_source_and_template_validation_remain_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: str,
    cause_message: str,
    expected_code: str,
) -> None:
    calls: list[str] = []
    provider = _ValidationFailureOutlineProvider(
        calls,
        cause_message,
    )
    context, runner = _prepare_outline_stage(
        tmp_path,
        monkeypatch,
        workspace_id=workspace_id,
        calls=calls,
        outline_provider=provider,
    )

    with pytest.raises(ControlPlaneError) as raised:
        runner.run("compile_chapter_blueprint")

    assert raised.value.code == expected_code
    assert calls == ["score", "outline"]
    assert ControlStore(context).v3_active_artifact("ChapterBlueprint") is None


def test_score_program_audit_warning_does_not_block_outline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    runs = tmp_path / "runs"
    (runs / "warning-flow").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "warning-flow")
    tender = tmp_path / "tender-warning.md"
    score = tmp_path / "score-warning.md"
    tender.write_text(
        "投标人须制定项目实施方案，并确保数据处理质量。",
        encoding="utf-8",
    )
    score.write_text(
        "技术方案完整、可行，得4分；较完整得2分；不完整得0分。",
        encoding="utf-8",
    )
    inputs = InputManifestService(context)
    inputs.register_local_file(tender, "tender")
    inputs.register_local_file(score, "score")
    SourceNormalizer(context).normalize_active_inputs()

    calls: list[str] = []
    runner = V3StageRunner(
        context,
        score_semantic_provider=_ReviewOnlyScoreProvider(calls),
        outline_decomposition_provider=_FakeOutlineProvider(calls),
    )
    runner.run("analyze_requirements")
    operation_id = "score-warning-operation"
    store = ControlStore(context)
    store.record_stage_run(
        operation_id,
        "analyze_scores",
        "running",
        disposition="started",
    )

    score_model = runner.run("analyze_scores", operation_id=operation_id)
    blueprint = runner.run("compile_chapter_blueprint")

    assert calls == ["score", "outline"]
    assert score_model.points
    assert all(
        point.review_status == "needs_review"
        for point in score_model.points
    )
    assert blueprint.nodes
    stage_run = store.latest_stage_run(operation_id, "analyze_scores") or {}
    products = (stage_run.get("output") or {}).get("products") or []
    semantic_product = next(
        item for item in products if item.get("kind") == "ScoreSemanticResult"
    )
    assert semantic_product["status"] == "warning"
    assert semantic_product["summary"]["warning_count"] >= 1
    assert any(
        "继续生成目录" in warning
        for warning in semantic_product["warnings"]
    )
    cached_operation_id = "score-warning-cached-operation"
    store.record_stage_run(
        cached_operation_id,
        "analyze_scores",
        "running",
        disposition="started",
    )
    cached_score_model = runner.run(
        "analyze_scores",
        operation_id=cached_operation_id,
    )
    assert cached_score_model.revision == score_model.revision
    assert calls == ["score", "outline"]
    cached_stage_run = (
        store.latest_stage_run(
            cached_operation_id,
            "analyze_scores",
        )
        or {}
    )
    cached_semantic_product = next(
        item
        for item in (
            (cached_stage_run.get("output") or {}).get("products") or []
        )
        if item.get("kind") == "ScoreSemanticResult"
    )
    assert cached_semantic_product["status"] == "warning"
    assert any(
        "不阻塞后续流程" in warning
        for warning in cached_semantic_product["warnings"]
    )


def test_invalid_score_candidate_fails_operation_without_rule_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    runs = tmp_path / "runs"
    (runs / "score-fallback").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "score-fallback")
    tender = tmp_path / "tender-fallback.md"
    score = tmp_path / "score-fallback.md"
    tender.write_text("投标人须制定项目实施方案。", encoding="utf-8")
    score.write_text(
        "技术方案完整、可行，得4分；较完整得2分；不完整得0分。",
        encoding="utf-8",
    )
    inputs = InputManifestService(context)
    inputs.register_local_file(tender, "tender")
    inputs.register_local_file(score, "score")
    SourceNormalizer(context).normalize_active_inputs()

    calls: list[str] = []
    runner = V3StageRunner(
        context,
        score_semantic_provider=_InvalidScoreProvider(calls),
        outline_decomposition_provider=_FakeOutlineProvider(calls),
    )

    def cannot_locate_condition_source(*args, **kwargs):
        del args, kwargs
        raise ValueError("来源措辞无法逐字定位")

    monkeypatch.setattr(
        V3StageRunner,
        "_locate_deterministic_condition_source",
        staticmethod(cannot_locate_condition_source),
    )
    runner.run("analyze_requirements")
    operation_id = "score-fallback-operation"
    store = ControlStore(context)
    store.record_stage_run(
        operation_id,
        "analyze_scores",
        "running",
        disposition="started",
    )

    with pytest.raises(ControlPlaneError, match="score_semantic_candidate_invalid"):
        runner.run("analyze_scores", operation_id=operation_id)

    assert calls == ["score"]
    assert store.v3_active_artifact("ScoreModel") is None
    semantic_run = store.latest_stage_run(operation_id, "score_semantic") or {}
    assert semantic_run["status"] == "failed"
    assert semantic_run["error"]["code"] == "score_semantic_candidate_invalid"


def _score_direct_fixture(
    *,
    score_point_id: str,
    point_title: str,
    group_title: str,
    condition_texts: list[str],
    max_points: float,
) -> tuple[ScoreModel, RequirementLedger, list[str]]:
    anchor = SourceAnchor(
        source_input_id="score-source",
        chunk_id=f"chunk-{score_point_id}",
        location="table[0]/row[1]",
    )
    conditions = [
        ScoreCondition(
            condition_id=f"{score_point_id}-C{index:02d}",
            text=text,
            normalized_condition=text,
            condition_role="content",
            source_excerpt=text,
            subject=text,
            response_intent=f"完整说明{text}",
            source_anchor=anchor,
        )
        for index, text in enumerate(condition_texts, start=1)
    ]
    score_model = ScoreModel(
        revision=1,
        source_hashes={"score-source": "sha256-score"},
        model_id=f"SM-{score_point_id}",
        source_input_ids=["score-source"],
        total_points=max_points,
        groups=[
            ScoreGroup(
                group_id="technical",
                title=group_title,
            )
        ],
        points=[
            ScorePoint(
                score_point_id=score_point_id,
                group_id="technical",
                title=point_title,
                criterion="；".join(condition_texts),
                max_points=max_points,
                score_conditions=conditions,
                response_units=[
                    ScoreResponseUnit(
                        unit_id=f"{score_point_id}-U01",
                        title=point_title,
                        condition_ids=[
                            condition.condition_id
                            for condition in conditions
                        ],
                        response_expectation=(
                            "完整覆盖全部满分条件"
                        ),
                    )
                ],
                response_expectation="完整覆盖全部满分条件",
                source_anchors=[anchor],
                confidence=1.0,
            )
        ],
    )
    ledger = RequirementLedger(
        revision=1,
        source_hashes={},
        requirements=[],
    )
    return (
        score_model,
        ledger,
        [condition.condition_id for condition in conditions],
    )


def _chapter_depths(blueprint) -> dict[str, int]:
    nodes = {node.chapter_id: node for node in blueprint.nodes}
    depths: dict[str, int] = {}
    for chapter_id in nodes:
        depth = 1
        cursor = nodes[chapter_id].parent_chapter_id
        while cursor is not None:
            depth += 1
            cursor = nodes[cursor].parent_chapter_id
        depths[chapter_id] = depth
    return depths


def test_deterministic_outline_groups_and_expands_concrete_quality_topics() -> None:
    anchor = SourceAnchor(
        source_input_id="score-source",
        chunk_id="chunk-outline",
        location="table[0]",
    )

    def point(
        point_id: str,
        group_id: str,
        title: str,
        path: list[str],
        topics: list[str],
    ) -> ScorePoint:
        conditions = [
            ScoreCondition(
                condition_id=f"{point_id}-C{index}",
                text=f"{topic}描述清楚",
                normalized_condition=f"{topic}描述清楚",
                condition_role="quality",
                source_excerpt=f"{topic}描述清楚",
                subject=topic,
                response_intent=f"完整说明{topic}",
                source_anchor=anchor,
            )
            for index, topic in enumerate(topics, start=1)
        ]
        return ScorePoint(
            score_point_id=point_id,
            group_id=group_id,
            title=title,
            criterion="；".join(item.text for item in conditions),
            max_points=4,
            outline_path=path,
            score_conditions=conditions,
            response_units=[
                ScoreResponseUnit(
                    unit_id=f"{point_id}-U01",
                    title=title,
                    outline_path=path,
                    condition_ids=[item.condition_id for item in conditions],
                    response_expectation=f"完整响应{title}",
                )
            ],
            response_expectation=f"完整响应{title}",
            source_anchors=[anchor],
            confidence=1.0,
        )

    scores = ScoreModel(
        revision=1,
        source_hashes={"score-source": "sha256-score"},
        model_id="SM-deterministic-outline",
        source_input_ids=["score-source"],
        total_points=16,
        groups=[
            ScoreGroup(group_id="price", title="价格部分（10分）"),
            ScoreGroup(group_id="business", title="商务部分（明标，25分）"),
            ScoreGroup(group_id="technical", title="技术部分（暗标，65分）"),
        ],
        points=[
            point("SP-price", "price", "报价响应", ["价格部分"], ["报价构成"]),
            point("SP-business", "business", "商务响应", ["商务部分"], ["商务承诺"]),
            point(
                "SP-target",
                "technical",
                "目标任务",
                ["技术部分", "目标任务"],
                ["项目任务背景", "工作必要性和可行性", "工作目标", "工作内容"],
            ),
            point(
                "SP-method",
                "technical",
                "独立任务",
                ["技术部分", "技术方法", "核查准备工作", "独立任务"],
                ["核查准备工作", "数据接收内容", "检查方法"],
            ),
        ],
    )
    ledger = RequirementLedger(revision=1, source_hashes={}, requirements=[])

    candidate = build_deterministic_outline_candidate(ledger, scores, None)
    orders = {node.local_id: node.order for node in candidate.nodes}
    assert sorted(orders.values()) == list(range(len(candidate.nodes)))
    assert all(
        node.parent_local_id is None
        or orders[node.parent_local_id] < node.order
        for node in candidate.nodes
    )
    roots = [node for node in candidate.nodes if node.parent_local_id is None]
    assert [node.title for node in roots] == [
        "价格部分（10分）",
        "商务部分（明标，25分）",
        "技术部分（暗标，65分）",
    ]
    by_title = {node.title: node for node in candidate.nodes}
    assert [
        by_title[title].score_condition_ids
        for title in ("项目任务背景", "工作必要性和可行性", "工作目标", "工作内容")
    ] == [[f"SP-target-C{index}"] for index in range(1, 5)]

    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        candidate,
        ledger,
        scores,
        revision=1,
    )
    assert audit_chapter_blueprint(blueprint, ledger, scores)["passed"] is True
    depths = _chapter_depths(blueprint)
    blueprint_by_title = {node.title: node for node in blueprint.nodes}
    assert depths[blueprint_by_title["检查方法"].chapter_id] == 5


def test_target_task_golden_has_four_condition_bound_second_level_titles() -> None:
    condition_texts = [
        "项目任务背景描述清楚",
        "工作必要性和可行性理由充分、逻辑清晰",
        "工作目标明确、可行",
        "工作内容具体、翔实",
    ]
    scores, ledger, condition_ids = _score_direct_fixture(
        score_point_id="SP-target",
        point_title="目标任务",
        group_title="技术部分（65分）",
        condition_texts=condition_texts,
        max_points=4,
    )
    candidate = ChapterOutlineCandidate(
        nodes=[
            ChapterOutlineNodeCandidate(
                local_id="target",
                order=0,
                title="目标任务",
                purpose="完整响应目标任务评分点",
                primary_response_unit_ids=["SP-target-U01"],
                confidence=1.0,
            ),
            *[
                ChapterOutlineNodeCandidate(
                    local_id=f"condition-{index}",
                    parent_local_id="target",
                    order=index,
                    title=title,
                    purpose="逐项覆盖满分条件",
                    score_condition_ids=[condition_id],
                    confidence=1.0,
                )
                for index, (title, condition_id) in enumerate(
                    zip(
                        (
                            "项目任务背景",
                            "工作必要性和可行性",
                            "工作目标",
                            "工作内容",
                        ),
                        condition_ids,
                        strict=True,
                    ),
                    start=1,
                )
            ],
        ]
    )
    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        candidate,
        ledger,
        scores,
        revision=1,
    )
    depths = _chapter_depths(blueprint)
    children = [
        node
        for node in blueprint.nodes
        if node.parent_chapter_id == blueprint.nodes[0].chapter_id
    ]

    assert blueprint.nodes[0].title == "目标任务"
    assert [node.title for node in children] == [
        "项目任务背景",
        "工作必要性和可行性",
        "工作目标",
        "工作内容",
    ]
    assert [node.score_condition_ids for node in children] == [
        [condition_id] for condition_id in condition_ids
    ]
    assert {depths[node.chapter_id] for node in children} == {2}


def test_technical_method_golden_preserves_four_heading_levels() -> None:
    condition_texts = [
        "核查准备工作全面细致",
        "数据接收内容全面、具体",
        "检查方法科学、重点突出、方法可行",
    ]
    scores, ledger, condition_ids = _score_direct_fixture(
        score_point_id="SP-preparation",
        point_title="数据接收内容与检查方法",
        group_title="技术方法（43分）",
        condition_texts=condition_texts,
        max_points=6,
    )
    nodes = [
        ChapterOutlineNodeCandidate(
            local_id="technical-method",
            order=0,
            title="技术方法（43分）",
            purpose="组织技术方法评分响应",
            confidence=1.0,
        ),
        ChapterOutlineNodeCandidate(
            local_id="preparation",
            parent_local_id="technical-method",
            order=1,
            title="核查准备工作（6分）",
            purpose="组织核查准备工作评分响应",
            confidence=1.0,
        ),
        ChapterOutlineNodeCandidate(
            local_id="independent-task",
            parent_local_id="preparation",
            order=2,
            title="数据接收内容与检查方法",
            purpose="响应独立计分任务",
            primary_response_unit_ids=["SP-preparation-U01"],
            confidence=1.0,
        ),
    ]
    nodes.extend(
        ChapterOutlineNodeCandidate(
            local_id=f"atomic-{index}",
            parent_local_id="independent-task",
            order=index + 2,
            title=title,
            purpose="逐项覆盖满分原子条件",
            score_condition_ids=[condition_id],
            confidence=1.0,
        )
        for index, (title, condition_id) in enumerate(
            zip(
                ("核查准备工作", "数据接收内容", "检查方法"),
                condition_ids,
                strict=True,
            ),
            start=1,
        )
    )
    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        ChapterOutlineCandidate(nodes=nodes),
        ledger,
        scores,
        revision=1,
    )
    depths = _chapter_depths(blueprint)
    by_title = {node.title: node for node in blueprint.nodes}

    assert depths[by_title["技术方法（43分）"].chapter_id] == 1
    assert depths[by_title["核查准备工作（6分）"].chapter_id] == 2
    assert depths[by_title["数据接收内容与检查方法"].chapter_id] == 3
    assert {
        depths[by_title[title].chapter_id]
        for title in ("核查准备工作", "数据接收内容", "检查方法")
    } == {4}
    assert [
        by_title[title].score_condition_ids
        for title in ("核查准备工作", "数据接收内容", "检查方法")
    ] == [[condition_id] for condition_id in condition_ids]


def test_g2_requires_section_quality_condition_writing_objective() -> None:
    scores, ledger, condition_ids = _score_direct_fixture(
        score_point_id="SP-quality-objective",
        point_title="实施方案",
        group_title="技术部分",
        condition_texts=["方案完整、合理、可行且针对性强"],
        max_points=4,
    )
    condition = scores.points[0].score_conditions[0].model_copy(
        update={
            "condition_role": "quality",
            "response_intent": "确保实施方案完整、合理、可行且具有针对性",
        }
    )
    scores = scores.model_copy(
        update={
            "points": [
                scores.points[0].model_copy(
                    update={"score_conditions": [condition]}
                )
            ]
        }
    )
    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        ChapterOutlineCandidate(
            nodes=[
                ChapterOutlineNodeCandidate(
                    local_id="technical-group",
                    order=0,
                    title="技术部分",
                    purpose="组织技术评分响应",
                    confidence=1.0,
                ),
                ChapterOutlineNodeCandidate(
                    local_id="implementation",
                    parent_local_id="technical-group",
                    order=1,
                    title="实施方案",
                    purpose="响应实施方案评分任务",
                    primary_response_unit_ids=[
                        "SP-quality-objective-U01"
                    ],
                    score_condition_ids=condition_ids,
                    confidence=1.0,
                )
            ]
        ),
        ledger,
        scores,
        revision=1,
    )

    implementation = next(
        node for node in blueprint.nodes if node.title == "实施方案"
    )
    assert condition.response_intent in implementation.writing_objectives
    assert audit_chapter_blueprint(blueprint, ledger, scores)["passed"] is True

    tampered = blueprint.model_copy(
        update={
            "nodes": [
                implementation.model_copy(
                    update={"writing_objectives": []}
                ),
                blueprint.nodes[0],
            ]
        }
    )
    audit = audit_chapter_blueprint(tampered, ledger, scores)
    assert audit["passed"] is False
    assert {
        finding["code"] for finding in audit["findings"]
    } >= {"QUALITY_CONDITION_OBJECTIVE_MISSING"}


def test_related_conditions_can_share_one_business_chapter() -> None:
    scores, ledger, condition_ids = _score_direct_fixture(
        score_point_id="SP-shared-topic",
        point_title="样本影像",
        group_title="技术部分",
        condition_texts=["核查样本影像分类方法合理", "核查样本影像使用说明细致"],
        max_points=4,
    )
    candidate = ChapterOutlineCandidate(
        nodes=[
            ChapterOutlineNodeCandidate(
                local_id="technical-group",
                order=0,
                title="技术部分",
                purpose="组织技术评分响应",
                confidence=1.0,
            ),
            ChapterOutlineNodeCandidate(
                local_id="topic",
                parent_local_id="technical-group",
                order=1,
                title="样本影像",
                purpose="响应样本影像评分任务",
                primary_response_unit_ids=["SP-shared-topic-U01"],
                confidence=1.0,
            ),
            ChapterOutlineNodeCandidate(
                local_id="classification-and-use",
                parent_local_id="topic",
                order=2,
                title="核查样本影像分类与使用",
                purpose="说明分类方法和使用方式",
                supporting_response_unit_ids=["SP-shared-topic-U01"],
                score_condition_ids=condition_ids,
                writing_objectives=["分类方法合理", "使用说明细致"],
                confidence=1.0,
            ),
        ]
    )

    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        candidate,
        ledger,
        scores,
        revision=1,
    )

    chapter = next(node for node in blueprint.nodes if node.title == "核查样本影像分类与使用")
    assert chapter.score_condition_ids == condition_ids
    assert audit_chapter_blueprint(blueprint, ledger, scores)["passed"] is True


def test_mixed_score_point_routes_only_document_unit_to_quality_gate() -> None:
    anchor = SourceAnchor(
        source_input_id="score-source",
        chunk_id="chunk-mixed",
        location="table[0]/row[2]",
    )
    content_condition = ScoreCondition(
        condition_id="SP-mixed-C-content",
        text="技术方案内容完整",
        normalized_condition="技术方案内容完整",
        condition_role="content",
        source_excerpt="技术方案内容完整",
        subject="技术方案",
        response_intent="编写技术方案章节",
        source_anchor=anchor,
    )
    quality_condition = ScoreCondition(
        condition_id="SP-mixed-C-quality",
        text="全文逻辑清晰、格式规范",
        normalized_condition="全文逻辑清晰、格式规范",
        condition_role="document",
        source_excerpt="全文逻辑清晰、格式规范",
        subject="投标文件整体质量",
        response_intent="实施全文质量检查",
        source_anchor=anchor,
    )
    scores = ScoreModel(
        revision=1,
        source_hashes={"score-source": "sha256-score"},
        model_id="SM-mixed",
        source_input_ids=["score-source"],
        total_points=6,
        groups=[ScoreGroup(group_id="technical", title="技术部分（6分）")],
        points=[
            ScorePoint(
                score_point_id="SP-mixed",
                group_id="technical",
                title="技术方案与文件质量",
                criterion="技术方案内容完整；全文逻辑清晰、格式规范",
                max_points=6,
                score_conditions=[content_condition, quality_condition],
                linked_requirement_ids=["R-document-quality"],
                response_units=[
                    ScoreResponseUnit(
                        unit_id="SP-mixed-U-content",
                        title="技术方案",
                        condition_ids=[content_condition.condition_id],
                        response_scope="section",
                        response_expectation="编写技术方案章节",
                    ),
                    ScoreResponseUnit(
                        unit_id="SP-mixed-U-quality",
                        title="文件整体质量",
                        condition_ids=[quality_condition.condition_id],
                        linked_requirement_ids=["R-document-quality"],
                        response_scope="document",
                        response_expectation="实施全文质量检查",
                    ),
                ],
                response_expectation="分别响应内容与全文质量要求",
                source_anchors=[anchor],
                confidence=1.0,
            )
        ],
    )
    ledger = RequirementLedger(
        revision=1,
        source_hashes={},
        requirements=[
            RequirementItem(
                requirement_id="R-document-quality",
                kind=RequirementKind.MANDATORY,
                source_anchor=anchor,
                original_text="投标文件应保持格式规范、前后一致",
                normalized_requirement="保持投标文件格式规范、前后一致",
                response_type="document_quality",
                evidence_policy="tender_traceable",
            )
        ],
    )
    candidate = ChapterOutlineCandidate(
        nodes=[
            ChapterOutlineNodeCandidate(
                local_id="technical-group",
                order=0,
                title="技术部分（6分）",
                purpose="组织技术评分响应",
                confidence=1.0,
            ),
            ChapterOutlineNodeCandidate(
                local_id="technical-plan",
                parent_local_id="technical-group",
                order=1,
                title="技术方案",
                purpose="响应技术方案内容评分要求",
                primary_response_unit_ids=["SP-mixed-U-content"],
                score_condition_ids=[content_condition.condition_id],
                confidence=1.0,
            )
        ],
        document_quality_response_unit_ids=["SP-mixed-U-quality"],
    )

    blueprint = object.__new__(PlanningAgent).compile_outline_candidate(
        candidate,
        ledger,
        scores,
        revision=1,
    )
    gate = blueprint.document_quality_gates[0]

    assert blueprint.planning_model == "score_direct"
    assert blueprint.assignments == []
    technical_plan = next(
        node for node in blueprint.nodes if node.title == "技术方案"
    )
    assert technical_plan.score_condition_ids == [
        content_condition.condition_id
    ]
    assert gate.duty_id is None
    assert gate.response_unit_ids == ["SP-mixed-U-quality"]
    assert gate.score_condition_ids == [quality_condition.condition_id]
    assert gate.requirement_ids == ["R-document-quality"]
    assert blueprint.nodes[0].requirement_ids == []
    assert blueprint.coverage_summary["required_requirement_count"] == 1
    assert blueprint.coverage_summary["covered_required_requirement_count"] == 1
    assert audit_chapter_blueprint(blueprint, ledger, scores)["passed"] is True

    tampered_gate = gate.model_copy(update={"score_condition_ids": []})
    tampered_blueprint = blueprint.model_copy(
        update={"document_quality_gates": [tampered_gate]}
    )
    tampered_audit = audit_chapter_blueprint(
        tampered_blueprint,
        ledger,
        scores,
    )
    assert tampered_audit["passed"] is False
    assert "DOCUMENT_QUALITY_CONDITION_MISMATCH" in {
        finding["code"] for finding in tampered_audit["findings"]
    }

    tamper_cases = [
        (
            {"requirement_ids": []},
            "DOCUMENT_QUALITY_REQUIREMENT_MISMATCH",
        ),
        (
            {"criteria": ["与评分原文无关的质量目标"]},
            "DOCUMENT_QUALITY_CRITERIA_MISMATCH",
        ),
        (
            {"check_items": ["与 criteria 无关的检查项"]},
            "DOCUMENT_QUALITY_CHECK_ITEMS_MISMATCH",
        ),
    ]
    for update, expected_code in tamper_cases:
        tampered_gate = gate.model_copy(update=update)
        tampered_blueprint = blueprint.model_copy(
            update={"document_quality_gates": [tampered_gate]}
        )
        tampered_audit = audit_chapter_blueprint(
            tampered_blueprint,
            ledger,
            scores,
        )
        assert tampered_audit["passed"] is False
        assert expected_code in {
            finding["code"] for finding in tampered_audit["findings"]
        }
