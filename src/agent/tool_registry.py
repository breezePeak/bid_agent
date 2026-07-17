from __future__ import annotations

from typing import Any

from pipeline_registry import STAGE_SPECS, StageSpec, workflow_stage_specs
from agent.types import ArtifactRef, ToolSpec


# Human-readable descriptions for stages (also used by subagent_registry historically).
_STAGE_DESCRIPTIONS: dict[str, str] = {
    "init_workspace": "初始化项目工作空间目录和默认提示词。",
    "prepare_inputs": "导入招标文件、公司资料、Word 模板，生成标准化输入。",
    "split_docs": "切分招标文件和公司资料为 chunk。",
    "parse_score": "解析评分要求和评分点。",
    "extract_facts": "提取招标需求、公司事实和全局事实。",
    "build_materials_checklist": "生成材料/资格/废标待补清单，供写作留白与人工补料。",
    "build_template_evidence": "生成模板依据映射和质量报告。",
    "generate_outline": "基于评分点和事实生成标书大纲。",
    "plan_chapter_jobs": "将大纲拆解为章节任务包。",
    "select_contexts": "为每个章节任务选择上下文。",
    "write_chapters": "生成全部章节正文（可后续参数化 chapter_ids）。",
    "review_fix_chapters": "审核章节并在需要时自动改稿。",
    "build_source_trace_index": "生成来源追溯索引。",
    "build_score_coverage_matrix": "生成评分覆盖矩阵。",
    "estimate_final_score": "按覆盖档位估算终稿得分与失分项。",
    "summarize_chapters": "为每个章节生成摘要。",
    "global_review": "全文一致性审核。",
    "compliance_check": "专项合规检查（资格/废标/强制参数等）。",
    "build_markdown": "拼接章节 Markdown 为 final.md。",
    "build_docx": "基于 Word 模板生成 final.docx。",
    "check_format": "格式检查与质量门禁。",
}

_MUTATION_STAGE_IDS = {
    "prepare_inputs",
    "parse_score",
    "extract_facts",
    "build_materials_checklist",
    "build_template_evidence",
    "generate_outline",
    "plan_chapter_jobs",
    "select_contexts",
    "write_chapters",
    "review_fix_chapters",
    "summarize_chapters",
    "global_review",
    "compliance_check",
}

_EXPORT_STAGE_IDS = {"build_markdown", "build_docx", "check_format"}

_HIGH_COST_STAGE_IDS = {
    "prepare_inputs",
    "parse_score",
    "extract_facts",
    "generate_outline",
    "write_chapters",
    "review_fix_chapters",
    "global_review",
}


def _artifact_refs(stage: StageSpec, *, which: str) -> tuple[ArtifactRef, ...]:
    items = stage.requires if which == "requires" else stage.produces
    return tuple(
        ArtifactRef(path=a.path, kind=a.kind, required_nonempty=a.required_nonempty) for a in items
    )


def _risk_for_stage(stage: StageSpec) -> str:
    if stage.id in _EXPORT_STAGE_IDS:
        return "high"
    if stage.id in _HIGH_COST_STAGE_IDS:
        return "medium"
    return "low"


def _kind_for_stage(stage: StageSpec) -> str:
    if stage.kind == "utility":
        return "utility"
    if stage.id in _EXPORT_STAGE_IDS:
        return "export"
    if stage.id in _MUTATION_STAGE_IDS:
        return "mutation"
    return "core"


def _side_effects_for_stage(stage: StageSpec) -> tuple[str, ...]:
    effects: list[str] = ["write_files"]
    if stage.prompt_agents:
        effects.append("llm")
    if stage.id in {"write_chapters", "review_fix_chapters", "select_contexts"}:
        effects.append("concurrency")
    return tuple(effects)


def stage_to_tool_spec(stage: StageSpec) -> ToolSpec:
    """Map one StageSpec to a stage-bound tool (name == stage command or stage id)."""
    description = _STAGE_DESCRIPTIONS.get(stage.id, stage.label)
    return ToolSpec(
        id=f"stage:{stage.id}",
        name=stage.command or stage.id,
        label=stage.label,
        description=description,
        kind=_kind_for_stage(stage),  # type: ignore[arg-type]
        command=stage.command,
        stage_id=stage.id,
        requires=_artifact_refs(stage, which="requires"),
        produces=_artifact_refs(stage, which="produces"),
        runner=stage.runner,
        params_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "忽略幂等 skip，强制重跑"},
                "workers": {"type": "integer", "minimum": 1, "description": "并发 worker（章节类阶段）"},
                "max_retries": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        risk_level=_risk_for_stage(stage),  # type: ignore[arg-type]
        idempotent=True,
        side_effects=_side_effects_for_stage(stage),
        human_confirm_required=stage.id in _EXPORT_STAGE_IDS and stage.id == "build_docx",
        tags=("stage", stage.kind, stage.id),
        prompt_agents=tuple(stage.prompt_agents),
    )


def run_stage_tool_spec() -> ToolSpec:
    commands = [s.command for s in STAGE_SPECS if s.command]
    return ToolSpec(
        id="run_stage",
        name="run_stage",
        label="运行流水线阶段",
        description="按 command 或 stage_id 执行已注册的流水线阶段（兼容旧 CLI/Web 命令）。",
        kind="meta",
        command="",
        stage_id="",
        requires=(),
        produces=(),
        runner="agent.tool_runtime._execute_run_stage",
        params_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "阶段 CLI command，如 parse-score / write-all",
                    "enum": commands,
                },
                "stage_id": {
                    "type": "string",
                    "description": "阶段 id，如 parse_score / write_chapters",
                },
                "force": {"type": "boolean", "default": False},
                "workers": {"type": "integer", "minimum": 1, "default": 1},
                "max_retries": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=True,
        side_effects=("write_files", "llm", "dispatch_stage"),
        human_confirm_required=False,
        tags=("meta", "stage"),
    )




def query_status_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="query_status",
        name="query_status",
        label="查询运行状态",
        description="只读：汇总 workflow 进度、run_state、下一步与失败信息。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["workflow", "errors", "summary"],
                    "description": "视图类型",
                }
            },
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        human_confirm_required=False,
        tags=("query", "readonly"),
    )


def query_artifacts_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="query_artifacts",
        name="query_artifacts",
        label="查询产物内容",
        description="只读：在项目 root 白名单目录内读取文本/JSON 摘要（防路径穿越）。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对 root 的路径，如 workspace/run_state.json"},
                "max_chars": {"type": "integer", "minimum": 100, "description": "最大返回字符数"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        human_confirm_required=False,
        tags=("query", "readonly"),
    )


def diagnose_failure_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="diagnose_failure",
        name="diagnose_failure",
        label="诊断失败",
        description="只读：聚合 run_state / 最近事件 / 可选阶段，给出失败摘要与建议 tool。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "可选，聚焦某阶段 command"},
                "tail_events": {"type": "integer", "minimum": 1, "description": "读取最近事件条数"},
            },
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        human_confirm_required=False,
        tags=("query", "diagnose", "readonly"),
    )




def write_chapters_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="write_chapters",
        name="write_chapters",
        label="定向章节写作",
        description="参数化写作：可指定 chapter_ids；默认写全部任务章。底层 subagent_runner.run_write_all。",
        kind="mutation",
        command="write-all",
        stage_id="write_chapters",
        params_schema={
            "type": "object",
            "properties": {
                "chapter_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "章节 id 列表；省略则全部",
                },
                "workers": {"type": "integer", "minimum": 1},
                "max_retries": {"type": "integer", "minimum": 0},
                "force": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=False,
        side_effects=("write_files", "llm", "concurrency"),
        human_confirm_required=False,
        tags=("chapter", "mutation", "parameterized"),
    )


def review_chapters_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="review_chapters",
        name="review_chapters",
        label="定向章节审核",
        description="参数化审核：可指定 chapter_ids。底层 subagent_runner.run_review_all。",
        kind="mutation",
        command="review-all",
        stage_id="review_fix_chapters",
        params_schema={
            "type": "object",
            "properties": {
                "chapter_ids": {"type": "array", "items": {"type": "string"}},
                "workers": {"type": "integer", "minimum": 1},
                "max_retries": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=False,
        side_effects=("write_files", "llm", "concurrency"),
        tags=("chapter", "mutation", "parameterized"),
    )


def rewrite_chapters_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="rewrite_chapters",
        name="rewrite_chapters",
        label="定向章节改稿",
        description="参数化改稿：可指定 chapter_ids。底层 subagent_runner.run_rewrite_all。",
        kind="mutation",
        command="rewrite-all",
        stage_id="review_fix_chapters",
        params_schema={
            "type": "object",
            "properties": {
                "chapter_ids": {"type": "array", "items": {"type": "string"}},
                "workers": {"type": "integer", "minimum": 1},
                "max_retries": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=False,
        side_effects=("write_files", "llm", "concurrency"),
        tags=("chapter", "mutation", "parameterized"),
    )




def build_export_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="build_export",
        name="build_export",
        label="导出终稿",
        description=(
            "导出 final.md / final.docx 并可选格式检查。"
            "若存在 stale 终稿/聚合产物，会先强制重建相关阶段，避免旧 Word 静默留下。"
        ),
        kind="export",
        params_schema={
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "子集: md, docx, format；默认全部",
                },
                "force": {"type": "boolean", "description": "强制重建，即使未标记 stale"},
                "skip_if_gate_fail": {"type": "boolean", "description": "预留：门禁失败时跳过（默认 false）"},
            },
            "additionalProperties": False,
        },
        risk_level="high",
        idempotent=False,
        side_effects=("write_files",),
        human_confirm_required=True,
        tags=("export", "mutation"),
    )




def analyze_coverage_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="analyze_coverage",
        name="analyze_coverage",
        label="分析评分覆盖",
        description="只读：读取 score_coverage_matrix，汇总未覆盖/弱覆盖评分点及建议改写章节。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "rebuild": {"type": "boolean", "description": "若矩阵缺失是否先重建"},
                "max_chapters": {"type": "integer", "minimum": 1, "description": "建议改写章节上限"},
            },
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        tags=("query", "readonly", "coverage"),
    )


def fix_coverage_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="fix_coverage",
        name="fix_coverage",
        label="覆盖率驱动改稿计划",
        description=(
            "分析覆盖缺口并生成定向 rewrite 计划（默认不自动改稿；"
            "confirm_execute=true 时才会调用 rewrite_chapters；"
            "max_rounds>1 时在预算内多轮分析-改稿，直到无缺口或达上限）。"
        ),
        kind="mutation",
        params_schema={
            "type": "object",
            "properties": {
                "max_chapters": {"type": "integer", "minimum": 1},
                "confirm_execute": {"type": "boolean", "description": "true 时执行 rewrite_chapters"},
                "workers": {"type": "integer", "minimum": 1},
                "rebuild_matrix": {"type": "boolean"},
                "max_rounds": {"type": "integer", "minimum": 1, "description": "自动改稿轮数上限，默认 1"},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=False,
        side_effects=("write_files", "llm"),
        human_confirm_required=True,
        tags=("chapter", "mutation", "coverage"),
    )




def analyze_compliance_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="analyze_compliance",
        name="analyze_compliance",
        label="分析合规缺口",
        description="只读：读取 compliance_report / rewrite hints，汇总可自动改写章节与人工项。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "sync": {"type": "boolean", "description": "是否先 sync_compliance_findings"},
            },
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        tags=("query", "readonly", "compliance"),
    )


def fix_compliance_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="fix_compliance",
        name="fix_compliance",
        label="合规定向改稿计划",
        description=(
            "根据合规失败项生成定向 rewrite 计划；默认不执行。"
            "confirm_execute=true 时 rewrite 相关章节；rerun_check=true 时再跑 compliance-check。"
        ),
        kind="mutation",
        params_schema={
            "type": "object",
            "properties": {
                "confirm_execute": {"type": "boolean"},
                "rerun_check": {"type": "boolean"},
                "max_chapters": {"type": "integer", "minimum": 1},
                "workers": {"type": "integer", "minimum": 1},
                "sync": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        risk_level="medium",
        idempotent=False,
        side_effects=("write_files", "llm"),
        human_confirm_required=True,
        tags=("chapter", "mutation", "compliance"),
    )




def list_issues_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="list_issues",
        name="list_issues",
        label="列出质量问题",
        description="只读：列出 workspace 中 open/block 质量问题单。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open|block|all"},
            },
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        tags=("query", "readonly", "issues"),
    )


def explain_issue_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="explain_issue",
        name="explain_issue",
        label="解释问题根因",
        description="规则归因 + 可选 LLM 白名单归因。",
        kind="analysis",
        params_schema={
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
            },
            "required": ["issue_id"],
            "additionalProperties": False,
        },
        risk_level="low",
        idempotent=True,
        side_effects=(),
        tags=("query", "issues"),
    )


def repair_issue_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="repair_issue",
        name="repair_issue",
        label="最小修复问题",
        description="预览或执行单条 Issue 的最小修复计划（confirm_execute=true 才执行）。",
        kind="mutation",
        params_schema={
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "confirm_execute": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["issue_id"],
            "additionalProperties": False,
        },
        risk_level="high",
        idempotent=False,
        side_effects=("write_files", "llm"),
        human_confirm_required=True,
        tags=("mutation", "issues"),
    )


def export_preflight_tool_spec() -> ToolSpec:
    return ToolSpec(
        id="export_preflight",
        name="export_preflight",
        label="出稿前检查清单",
        description="只读：检查全文审核/合规/open block/final.md 是否允许出正式稿。",
        kind="analysis",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level="low",
        idempotent=True,
        side_effects=(),
        tags=("query", "readonly", "export"),
    )


def _build_tool_index() -> dict[str, ToolSpec]:
    tools: dict[str, ToolSpec] = {}
    for meta in (
        run_stage_tool_spec(),
        query_status_tool_spec(),
        query_artifacts_tool_spec(),
        diagnose_failure_tool_spec(),
        write_chapters_tool_spec(),
        review_chapters_tool_spec(),
        rewrite_chapters_tool_spec(),
        build_export_tool_spec(),
        analyze_coverage_tool_spec(),
        fix_coverage_tool_spec(),
        analyze_compliance_tool_spec(),
        fix_compliance_tool_spec(),
        list_issues_tool_spec(),
        explain_issue_tool_spec(),
        repair_issue_tool_spec(),
        export_preflight_tool_spec(),
    ):
        tools[meta.name] = meta
        tools[meta.id] = meta

    for stage in workflow_stage_specs(include_utility=True):
        spec = stage_to_tool_spec(stage)
        # Primary keys
        tools[spec.id] = spec
        if spec.command:
            tools[spec.command] = spec
        tools[spec.stage_id] = spec
        tools[f"stage_{spec.stage_id}"] = spec

    # Parameterized chapter tools win over bare stage aliases on the same name.
    for specialized in (
        write_chapters_tool_spec(),
        review_chapters_tool_spec(),
        rewrite_chapters_tool_spec(),
        build_export_tool_spec(),
        analyze_coverage_tool_spec(),
        fix_coverage_tool_spec(),
        analyze_compliance_tool_spec(),
        fix_compliance_tool_spec(),
        list_issues_tool_spec(),
        explain_issue_tool_spec(),
        repair_issue_tool_spec(),
        export_preflight_tool_spec(),
    ):
        tools[specialized.name] = specialized
        tools[specialized.id] = specialized
    return tools


_TOOL_INDEX: dict[str, ToolSpec] | None = None


def _index() -> dict[str, ToolSpec]:
    global _TOOL_INDEX
    if _TOOL_INDEX is None:
        _TOOL_INDEX = _build_tool_index()
    return _TOOL_INDEX


def reset_tool_index() -> None:
    """Test helper: rebuild index after registry changes."""
    global _TOOL_INDEX
    _TOOL_INDEX = None


def list_tools(*, include_stage_aliases: bool = False) -> list[ToolSpec]:
    """Return unique tools. By default meta tools + one entry per stage."""
    seen: set[str] = set()
    ordered: list[ToolSpec] = []
    for meta in (
        run_stage_tool_spec(),
        query_status_tool_spec(),
        query_artifacts_tool_spec(),
        diagnose_failure_tool_spec(),
        write_chapters_tool_spec(),
        review_chapters_tool_spec(),
        rewrite_chapters_tool_spec(),
        build_export_tool_spec(),
        analyze_coverage_tool_spec(),
        fix_coverage_tool_spec(),
        analyze_compliance_tool_spec(),
        fix_compliance_tool_spec(),
        list_issues_tool_spec(),
        explain_issue_tool_spec(),
        repair_issue_tool_spec(),
        export_preflight_tool_spec(),
    ):
        ordered.append(meta)
        seen.add(meta.id)
    for stage in workflow_stage_specs(include_utility=True):
        spec = stage_to_tool_spec(stage)
        if spec.id in seen:
            continue
        ordered.append(spec)
        seen.add(spec.id)
    if include_stage_aliases:
        return list(_index().values())
    return ordered


def get_tool(name: str) -> ToolSpec | None:
    if not name:
        return None
    key = str(name).strip()
    return _index().get(key)


def tool_manifest() -> list[dict[str, Any]]:
    return [t.to_manifest() for t in list_tools()]


def stage_tools() -> list[ToolSpec]:
    return [stage_to_tool_spec(s) for s in workflow_stage_specs(include_utility=True)]
