from __future__ import annotations

from dataclasses import dataclass, field
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
        ),
        produces=(
            _artifact("inputs/tender.md"),
            _artifact("inputs/score.md"),
            _artifact("inputs/company.md", required_nonempty=False),
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
        ),
        produces=(
            _artifact("workspace/chunks/tender_chunks.json"),
            _artifact("workspace/chunks/company_chunks.json"),
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
        ),
        produces=(
            _artifact("workspace/contexts/*_context.json", kind="glob"),
            _artifact("workspace/contexts/*_ranked_chunks.json", kind="glob", required_nonempty=False),
        ),
        runner="context_selector.select_contexts_for_jobs",
        max_context_chars=18000,
        max_chunks=30,
        prompt_agents=("chapter_context_selector",),
        validator="collection",
    ),
    StageSpec(
        id="write_chapters",
        label="生成章节",
        command="write-all",
        kind="core",
        requires=(_artifact("workspace/contexts/*_context.json", kind="glob"),),
        produces=(_artifact("workspace/chapters/*.md", kind="glob"),),
        runner="chapter_writer.write_all",
        max_context_chars=16000,
        max_chunks=16,
        prompt_agents=("chapter_writer",),
        validator="collection",
    ),
    StageSpec(
        id="review_fix_chapters",
        label="审核改稿",
        command="review-fix-all",
        kind="core",
        requires=(_artifact("workspace/chapters/*.md", kind="glob"),),
        produces=(
            _artifact("workspace/reviews/*_review.json", kind="glob"),
            _artifact("workspace/rewrites/*_rewrite_log.json", kind="glob", required_nonempty=False),
        ),
        runner="chapter_rewriter.review_fix_all",
        prompt_agents=("chapter_reviewer", "chapter_rewriter"),
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
            _artifact("workspace/reviews/*_review.json", kind="glob"),
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
            _artifact("workspace/reviews/*_review.json", kind="glob"),
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


def workflow_stage_specs(include_utility: bool = True) -> list[StageSpec]:
    if include_utility:
        return list(STAGE_SPECS)
    return [stage for stage in STAGE_SPECS if stage.kind != "utility"]


def stage_spec_by_id(stage_id: str) -> StageSpec:
    for stage in STAGE_SPECS:
        if stage.id == stage_id:
            return stage
    raise KeyError(f"未知 stage id: {stage_id}")


def stage_spec_by_command(command: str) -> StageSpec:
    for stage in STAGE_SPECS:
        if stage.command == command:
            return stage
    raise KeyError(f"未知 stage command: {command}")


def stage_command_map() -> dict[str, str]:
    return {stage.id: stage.command for stage in STAGE_SPECS}


def auto_run_commands() -> list[str]:
    return [stage.command for stage in STAGE_SPECS if stage.kind != "utility"]


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
