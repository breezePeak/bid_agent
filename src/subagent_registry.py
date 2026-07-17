from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pipeline_registry import workflow_stage_specs


Instantiation = Literal["per-chapter", "single"]


@dataclass(frozen=True)
class SubagentSpec:
    """子 agent 蓝图（blueprint）。运行时按 instantiation 实例化：
    per-chapter → 每章一个并发实例；single → 全局单实例。"""

    name: str
    label: str
    description: str
    command: str
    instantiation: Instantiation
    concurrency: int = 1
    modes: tuple[str, ...] = field(default_factory=tuple)
    worker_module: str = ""


CHAPTER_WRITER = SubagentSpec(
    name="chapter_writer",
    label="章节写作子 Agent",
    description=(
        "章节写作子 agent 蓝图（注册表只存 1 种类型）。运行时由 subagent_runner.run_write_all / "
        "run_rewrite_all 为每个章节任务实例化一个实例，经 ThreadPoolExecutor 并发执行（上限 5），"
        "每个实例各自 invoke chapter_subgraph（选上下文→写作→自检）。"
        "write 模式对应初写（write-all），rewrite 模式带审核反馈改稿（复用同一蓝图的文件上下文，"
        "即 continue 模式）。"
    ),
    command="write-all",
    instantiation="per-chapter",
    concurrency=5,
    modes=("write", "rewrite"),
    worker_module="agents.chapter_writer_agent",
)

CHAPTER_REVIEWER = SubagentSpec(
    name="chapter_reviewer",
    label="章节审核子 Agent",
    description=(
        "章节审核子 agent 蓝图。每章一个并发实例（上限 5），fresh——与写作子 agent 不同实例，"
        "避免写作偏见。worker 为 review_chapter，只读，产出 workspace/reviews/*_review.json。"
        "对应并发调度 subagent_runner.run_review_all。"
    ),
    command="review-fix-all",
    instantiation="per-chapter",
    concurrency=5,
    modes=("review",),
    worker_module="agents.chapter_review_agent",
)

GLOBAL_REVIEWER = SubagentSpec(
    name="global_reviewer",
    label="全文审核子 Agent",
    description=(
        "全文一致性审核子 agent 蓝图，单实例（不并发）。自带上下文装配：优先用章节摘要，"
        "回退全文，叠加评分矩阵/来源追溯/全局事实，不依赖主 agent 窗口。"
        "worker 为 global_reviewer.run_global_review。对应命令 global-review。"
    ),
    command="global-review",
    instantiation="single",
    concurrency=1,
    modes=("global_review",),
    worker_module="agents.global_review_agent",
)

_SUBAGENTS: tuple[SubagentSpec, ...] = (CHAPTER_WRITER, CHAPTER_REVIEWER, GLOBAL_REVIEWER)
_SUBAGENT_BY_NAME: dict[str, SubagentSpec] = {s.name: s for s in _SUBAGENTS}


def list_subagents() -> list[SubagentSpec]:
    return list(_SUBAGENTS)


def get_subagent(name: str) -> SubagentSpec | None:
    return _SUBAGENT_BY_NAME.get(name)


def subagent_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "label": s.label,
            "description": s.description,
            "command": s.command,
            "instantiation": s.instantiation,
            "concurrency": s.concurrency,
            "modes": list(s.modes),
        }
        for s in _SUBAGENTS
    ]


@dataclass(frozen=True)
class PipelineCapability:
    command: str
    label: str
    description: str


_STAGE_DESCRIPTIONS: dict[str, str] = {
    "init_workspace": "初始化项目工作空间目录和默认提示词。",
    "prepare_inputs": "导入招标文件、公司资料、Word 模板，生成标准化输入。",
    "split_docs": "切分招标文件和公司资料为 chunk。",
    "parse_score": "解析评分要求和评分点。",
    "extract_facts": "提取招标需求、公司事实和全局事实。",
    "build_materials_checklist": "生成材料/资格/废标待补清单，驱动写作留白与补料。",
    "build_template_evidence": "生成模板依据映射和质量报告。",
    "generate_outline": "基于评分点和事实生成标书大纲。",
    "plan_chapter_jobs": "将大纲拆解为章节任务包。",
    "select_contexts": "为每个章节任务选择上下文。",
    "write_chapters": "派发给多个章节写作子 agent 并发生成章节。",
    "review_fix_chapters": "派发给多个章节审核子 agent 并发审核，需要时由写作子 agent 改稿。",
    "build_source_trace_index": "生成来源追溯索引。",
    "build_score_coverage_matrix": "生成评分覆盖矩阵。",
    "estimate_final_score": "按覆盖档位估算终稿得分与失分项。",
    "summarize_chapters": "为每个章节生成摘要。",
    "global_review": "全文一致性审核（单实例子 agent）。",
    "compliance_check": "专项合规检查（资格/废标/强制参数/签章/保证金/有效期/完整性/一致性）。",
    "build_markdown": "拼接章节 Markdown 为 final.md。",
    "build_docx": "基于 Word 模板生成 final.docx。",
    "check_format": "格式检查与质量门禁。",
}


def pipeline_capabilities() -> list[PipelineCapability]:
    caps: list[PipelineCapability] = []
    for stage in workflow_stage_specs(include_utility=True):
        if not stage.command:
            continue
        caps.append(
            PipelineCapability(
                command=stage.command,
                label=stage.label,
                description=_STAGE_DESCRIPTIONS.get(stage.id, stage.label),
            )
        )
    return caps


def pipeline_manifest() -> list[dict[str, Any]]:
    return [
        {"command": c.command, "label": c.label, "description": c.description}
        for c in pipeline_capabilities()
    ]


ActionKind = Literal[
    "query",
    "run_command",
    "dispatch_chapters",
    "dispatch_review",
    "dispatch_rewrite",
    "global_review",
    "auto_run",
    "chat",
]
QueryKind = Literal["status", "manual_review", "score_coverage", "quality_risk", "inputs", "outputs"]
