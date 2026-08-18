from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Literal


ArtifactKind = Literal["file", "glob", "virtual"]


@dataclass(frozen=True)
class RunArtifact:
    path: str
    kind: ArtifactKind = "file"
    required_nonempty: bool = True
    previewable: bool = True


@dataclass(frozen=True)
class StageSpec:
    id: str
    label: str
    command: str
    kind: str
    requires: tuple[RunArtifact, ...] = ()
    produces: tuple[RunArtifact, ...] = ()
    runner: str = ""
    resume_check: str = "artifacts"
    max_context_chars: int = 0
    max_chunks: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)
    prompt_agents: tuple[str, ...] = field(default_factory=tuple)
    validator: str = ""
    # A registered stage may be available for explicit diagnostics while not yet
    # being safe for the production automatic pipeline.  This is deliberately
    # explicit so an experimental stage cannot be scheduled by accident.
    auto_run: bool = True


def _artifact(path: str, *, kind: ArtifactKind = "file", required_nonempty: bool = True, previewable: bool = True) -> RunArtifact:
    return RunArtifact(path=path, kind=kind, required_nonempty=required_nonempty, previewable=previewable)


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        id="init_workspace",
        label="初始化项目",
        command="init",
        kind="utility",
        produces=(
            _artifact("基础目录", kind="virtual", required_nonempty=False, previewable=False),
            _artifact("默认提示词", kind="virtual", required_nonempty=False, previewable=False),
        ),
        runner="main.init_project",
    ),
    StageSpec(
        id="prepare_inputs",
        label="导入资料",
        command="prepare-inputs",
        kind="core",
        requires=(
            _artifact("sources/tender", required_nonempty=False),
            _artifact("sources/company", required_nonempty=False),
            _artifact("sources/template", required_nonempty=False),
            _artifact("sources/reference", required_nonempty=False),
            _artifact("sources/guidance", required_nonempty=False),
        ),
        produces=(
            _artifact("inputs/tender.md"),
            _artifact("inputs/score.md"),
            _artifact("inputs/company.md", required_nonempty=False),
            _artifact("inputs/reference.md", required_nonempty=False),
            _artifact("inputs/writing_brief.md", required_nonempty=False),
            _artifact("workspace/imported/*", kind="glob", required_nonempty=False),
            _artifact("workspace/template_schema.json", required_nonempty=False),
            _artifact("inputs/template.docx", required_nonempty=False),
        ),
        runner="input_preparer.prepare_inputs",
        prompt_agents=("tender_block_classifier",),
    ),
    StageSpec(
        id="split_docs",
        label="切分文档",
        command="split-docs",
        kind="core",
        requires=(
            _artifact("inputs/tender.md"),
            _artifact("inputs/company.md", required_nonempty=False),
            _artifact("inputs/reference.md", required_nonempty=False),
        ),
        produces=(
            _artifact("workspace/chunks/tender_chunks.json"),
            _artifact("workspace/chunks/company_chunks.json"),
            _artifact("workspace/chunks/reference_chunks.json", required_nonempty=False),
        ),
        runner="document_splitter.split_docs",
    ),
    StageSpec(
        id="parse_score",
        label="解析评分",
        command="parse-score",
        kind="core",
        requires=(_artifact("inputs/score.md"),),
        produces=(
            _artifact("workspace/score_requirements.json"),
            _artifact("workspace/score_points.json"),
        ),
        runner="score_parser.parse_score",
        prompt_agents=("score_requirement_extractor", "score_point_parser"),
    ),
    StageSpec(
        id="extract_facts",
        label="提取事实",
        command="extract-facts",
        kind="core",
        requires=(
            _artifact("inputs/tender.md"),
            _artifact("inputs/company.md", required_nonempty=False),
        ),
        produces=(
            _artifact("workspace/tender_requirements.json"),
            _artifact("workspace/company_facts.json", required_nonempty=False),
            _artifact("workspace/global_facts.json"),
        ),
        runner="fact_extractor.extract_facts",
        prompt_agents=("tender_requirement_extractor", "company_facts_extractor"),
    ),
    StageSpec(
        id="analyze_project_understanding",
        label="整体理解项目",
        command="analyze-project",
        kind="core",
        requires=(
            _artifact("workspace/tender_requirements.json"),
            _artifact("workspace/score_points.json"),
            _artifact("workspace/global_facts.json"),
        ),
        produces=(_artifact("workspace/project_understanding.json"),),
        runner="project_understanding.analyze_project_understanding",
        notes=(
            "在写大纲和章节前统一理解项目背景、范围、任务、成果、验收、约束与未知项",
            "输出可直接执行的联网检索问题",
        ),
        prompt_agents=("project_understanding",),
        auto_run=False,
    ),
    StageSpec(
        id="build_materials_checklist",
        label="材料资格清单",
        command="build-materials-checklist",
        kind="core",
        requires=(
            _artifact("workspace/tender_requirements.json"),
            _artifact("workspace/company_facts.json", required_nonempty=False),
            _artifact("workspace/global_facts.json"),
            _artifact("inputs/tender.md"),
            _artifact("inputs/score.md", required_nonempty=False),
            _artifact("inputs/company.md", required_nonempty=False),
        ),
        produces=(_artifact("control.db:material_states", kind="virtual", required_nonempty=False, previewable=False),),
        runner="materials_checklist.derive_materials_checklist",
        notes=(
            "解析后生成资格/废标/必交材料清单；缺材料默认 deferred，写作时结构化留白",
        ),
    ),
    StageSpec(
        id="build_template_evidence",
        label="生成模板依据",
        command="build-template-evidence",
        kind="core",
        requires=(
            _artifact("workspace/template_schema.json", required_nonempty=False),
            _artifact("workspace/chunks/tender_chunks.json"),
            _artifact("workspace/chunks/company_chunks.json"),
            _artifact("workspace/score_points.json"),
            _artifact("workspace/global_facts.json"),
        ),
        produces=(
            _artifact("workspace/template_evidence_map.json"),
            _artifact("workspace/template_quality_report.json"),
        ),
        runner="template_evidence.build_template_evidence",
    ),
    StageSpec(
        id="generate_outline",
        label="生成大纲",
        command="generate-outline",
        kind="core",
        requires=(
            _artifact("workspace/score_points.json"),
            _artifact("workspace/global_facts.json"),
            _artifact("workspace/template_evidence_map.json", required_nonempty=False),
            _artifact("inputs/tender.md"),
            _artifact("inputs/reference.md", required_nonempty=False),
            _artifact("inputs/writing_brief.md", required_nonempty=False),
        ),
        produces=(_artifact("workspace/outline.json"),),
        runner="outline_generator.generate_outline",
        prompt_agents=("outline_generator",),
    ),
    StageSpec(
        id="plan_chapter_jobs",
        label="生成任务",
        command="plan-jobs",
        kind="core",
        requires=(_artifact("workspace/outline.json"),),
        produces=(_artifact("workspace/jobs/*.json", kind="glob"),),
        runner="job_planner.plan_chapter_jobs",
        validator="collection",
    ),
    StageSpec(
        id="select_contexts",
        label="选择上下文",
        command="select-context-all",
        kind="core",
        requires=(
            _artifact("workspace/jobs/*.json", kind="glob"),
            _artifact("workspace/chunks/tender_chunks.json"),
            _artifact("workspace/chunks/company_chunks.json"),
            _artifact("workspace/chunks/reference_chunks.json", required_nonempty=False),
            _artifact("inputs/writing_brief.md", required_nonempty=False),
        ),
        produces=(
            _artifact("workspace/contexts/*_context.json", kind="glob"),
            _artifact("workspace/contexts/*_ranked_chunks.json", kind="glob", required_nonempty=False),
        ),
        runner="agents.context_agent.run",
        max_context_chars=18000,
        max_chunks=30,
        prompt_agents=("chapter_context_selector",),
        validator="collection",
    ),
    StageSpec(
        id="build_source_trace_index",
        label="生成来源追溯",
        command="build-source-trace",
        kind="core",
        requires=(
            _artifact("workspace/chapters/*.md", kind="glob"),
            _artifact("workspace/jobs/*.json", kind="glob"),
            _artifact("workspace/contexts/*_context.json", kind="glob"),
        ),
        produces=(_artifact("workspace/source_trace_index.json"),),
        runner="source_trace.build_source_trace_index",
    ),
    StageSpec(
        id="build_score_coverage_matrix",
        label="生成评分覆盖矩阵",
        command="build-score-coverage",
        kind="core",
        requires=(
            _artifact("workspace/score_points.json"),
            _artifact("workspace/jobs/*.json", kind="glob"),
            _artifact("workspace/reviews/*_review.json", kind="glob", required_nonempty=False),
            _artifact("workspace/chapters/*.md", kind="glob"),
        ),
        produces=(_artifact("workspace/score_coverage_matrix.json"),),
        runner="score_coverage_matrix.build_score_coverage_matrix",
    ),
    StageSpec(
        id="estimate_final_score",
        label="终稿估分",
        command="estimate-score",
        kind="core",
        requires=(_artifact("workspace/score_coverage_matrix.json"),),
        produces=(
            _artifact("workspace/final_score_estimate.json"),
            _artifact("outputs/score_estimate.md"),
        ),
        runner="score_estimator.estimate_final_score",
    ),
    StageSpec(
        id="summarize_chapters",
        label="生成摘要",
        command="summarize-all",
        kind="core",
        requires=(
            _artifact("workspace/chapters/*.md", kind="glob"),
            _artifact("workspace/reviews/*_review.json", kind="glob", required_nonempty=False),
        ),
        produces=(_artifact("workspace/summaries/*_summary.json", kind="glob"),),
        runner="chapter_summarizer.summarize_all_chapters",
        validator="collection",
        prompt_agents=("chapter_summarizer",),
    ),
    StageSpec(
        id="global_review",
        label="全文审核",
        command="global-review",
        kind="core",
        requires=(
            _artifact("workspace/summaries/*_summary.json", kind="glob", required_nonempty=False),
            _artifact("workspace/score_points.json"),
            _artifact("workspace/global_facts.json"),
            _artifact("workspace/outline.json"),
        ),
        produces=(_artifact("workspace/global_review.json"),),
        runner="global_reviewer.run_global_review",
        max_context_chars=20000,
        prompt_agents=("global_reviewer",),
    ),
    StageSpec(
        id="compliance_check",
        label="专项合规检查",
        command="compliance-check",
        kind="core",
        requires=(
            _artifact("workspace/global_facts.json"),
            _artifact("workspace/tender_requirements.json"),
            _artifact("workspace/outline.json"),
            _artifact("workspace/chapters/*.md", kind="glob", required_nonempty=False),
            _artifact("workspace/global_review.json", required_nonempty=False),
            _artifact("outputs/final.md", required_nonempty=False),
        ),
        produces=(_artifact("workspace/compliance_report.json"),),
        runner="compliance_checker.run_compliance_check",
        notes=(
            "规则优先的专项合规检查：资格/废标/强制参数/签章/保证金/有效期/完整性/一致性",
            "fatal/critical 失败标记 blocking，不改变写稿流程，仅作为独立门禁阶段",
        ),
    ),
    StageSpec(
        id="build_markdown",
        label="拼接 MD",
        command="build-md",
        kind="core",
        requires=(
            _artifact("workspace/chapters/*.md", kind="glob"),
            _artifact("workspace/outline.json"),
            _artifact("workspace/source_trace_index.json"),
            _artifact("workspace/score_coverage_matrix.json"),
            _artifact("workspace/final_score_estimate.json"),
            _artifact("workspace/summaries/*_summary.json", kind="glob"),
        ),
        produces=(_artifact("outputs/final.md"),),
        runner="docx_builder.build_markdown",
    ),
    StageSpec(
        id="build_docx",
        label="生成 Word",
        command="build-docx",
        kind="core",
        requires=(
            _artifact("outputs/final.md"),
            _artifact("inputs/template.docx", required_nonempty=False),
        ),
        produces=(
            _artifact("outputs/final.docx"),
            _artifact("workspace/template_fill_report.json", required_nonempty=False),
        ),
        runner="docx_builder.build_docx",
    ),
    StageSpec(
        id="check_format",
        label="检查格式",
        command="check-format",
        kind="core",
        requires=(
            _artifact("outputs/final.md"),
            _artifact("outputs/final.docx"),
        ),
        produces=(_artifact("workspace/format_check_report.json"),),
        runner="format_checker.check_output_format",
    ),
)


def chapter_review_enabled() -> bool:
    value = str(os.environ.get("BID_AGENT_CHAPTER_REVIEW_ENABLED", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def workflow_stage_specs(include_utility: bool = True) -> list[StageSpec]:
    # Chapter generation is owned by the V3 writing kernel.  The legacy
    # write/rewrite stages are intentionally absent from this registry.
    review_enabled = chapter_review_enabled()
    # 摘要仅供审核链压缩上下文；关闭审核后不再调用模型生成摘要，
    # 避免一个已关闭的审核辅助阶段反过来中断正常出稿。
    draft_optional_stages = {
        "summarize_chapters",
        "global_review",
        "compliance_check",
    }
    stages = [
        _stage_for_current_review_policy(stage)
        for stage in STAGE_SPECS
        if stage.auto_run
        and (review_enabled or stage.id not in draft_optional_stages)
    ]
    if include_utility:
        return stages
    return [stage for stage in stages if stage.kind != "utility"]


def stage_spec_by_id(stage_id: str) -> StageSpec:
    for stage in STAGE_SPECS:
        if stage.id == stage_id:
            return _stage_for_current_review_policy(stage)
    raise KeyError(f"未知 stage id: {stage_id}")


def stage_spec_by_command(command: str) -> StageSpec:
    for stage in STAGE_SPECS:
        if stage.command == command:
            return _stage_for_current_review_policy(stage)
    raise KeyError(f"未知 stage command: {command}")


def stage_command_map() -> dict[str, str]:
    return {stage.id: stage.command for stage in STAGE_SPECS}


def auto_run_commands() -> list[str]:
    return [stage.command for stage in workflow_stage_specs(include_utility=False)]


def next_enabled_command_after(requested: str) -> str:
    """Map a disabled historical stage to the next stage in the current policy."""
    requested = str(requested or "").strip()
    active = auto_run_commands()
    if requested in active:
        return requested
    full_order = [stage.command for stage in STAGE_SPECS if stage.kind != "utility"]
    if requested not in full_order:
        return ""
    requested_index = full_order.index(requested)
    return next(
        (
            command
            for command in full_order[requested_index + 1 :]
            if command in active
        ),
        "",
    )


def _stage_for_current_review_policy(stage: StageSpec) -> StageSpec:
    """Return the effective dependencies for the current review policy."""
    if chapter_review_enabled() or stage.id != "build_markdown":
        return stage
    return replace(
        stage,
        requires=tuple(
            artifact
            for artifact in stage.requires
            if artifact.path != "workspace/summaries/*_summary.json"
        ),
    )


def artifact_exists(root: Path, artifact: RunArtifact) -> bool:
    if artifact.kind == "virtual":
        return True
    target = root / artifact.path
    if artifact.kind == "glob":
        directory = root / Path(artifact.path).parent
        if not directory.exists():
            return False
        matches = list(directory.glob(Path(artifact.path).name))
        if not matches:
            return False
        if not artifact.required_nonempty:
            return True
        for item in matches:
            if item.is_dir():
                return True
            if item.exists() and item.stat().st_size > 0:
                return True
        return False
    if target.is_dir():
        return target.exists() and (not artifact.required_nonempty or any(target.iterdir()))
    return target.exists() and target.is_file() and (not artifact.required_nonempty or target.stat().st_size > 0)


def stage_outputs_ready(root: Path, stage_id: str) -> bool:
    stage = stage_spec_by_id(stage_id)
    if stage.validator == "collection":
        from stage_validation import stage_collection_status

        return bool(stage_collection_status(root, stage_id)["complete"])
    return all(artifact_exists(root, artifact) for artifact in stage.produces)
