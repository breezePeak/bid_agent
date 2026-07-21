from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from chapter_reviewer import review_all, review_chapter
from chapter_rewriter import review_fix_all, rewrite_all, rewrite_chapter
from chapter_summarizer import summarize_all_chapters, summarize_chapter
from chapter_writer import write_all, write_chapter
from docx_builder import build_docx, build_markdown
from fact_extractor import extract_facts
from materials_checklist import build_materials_checklist
from compliance_checker import run_compliance_check
from global_reviewer import run_global_review
from format_checker import check_output_format
from outline_generator import generate_outline
from pipeline_registry import workflow_stage_specs
from project_profile_registry import project_profile_choices, save_project_profile
from prompt_registry import required_prompt_files
from score_coverage_matrix import build_score_coverage_matrix
from score_estimator import estimate_final_score
from source_trace import build_source_trace_index
from score_parser import parse_score
from template_analyzer import analyze_template
from template_evidence import build_template_evidence
from concurrency import clamp_workers, workers_default, workers_max
from utils import ensure_dirs, ensure_file, project_root, read_json
from project_validator import validate_project


DEFAULT_PROMPTS = {
    "score_requirement_extractor.md": """你是投标评分标准原文抽取专家。

任务：从用户提供的评分标准 Markdown 中逐条抽取“原始评分要求”，输出合法 JSON 数组。

每个元素必须包含：
- category
- title
- score
- requirement
- scoring_criteria
- keywords
- source_excerpt

硬性要求：
1. 不允许遗漏任何独立评分要求、评分因素、打分项、评审项。
2. 如果原文是一行一个评分项，就按一行一项抽取；不要把多个评分项合并。
3. requirement 保留评分要求本身，scoring_criteria 保留打分细则/分档说明。
4. score 无法识别时填 null。
5. keywords 必须是字符串数组。
6. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "score_point_parser.md": """你是投标评分点结构化专家。

任务：根据已抽取的原始评分要求 JSON，整理出最终评分点 JSON 数组。

硬性要求：
1. 不允许丢失任何评分项，不允许合并不同的原始评分要求。
2. 每个评分点必须包含 id、category、title、score、requirement、keywords、response_strategy 字段。
3. id 使用 S001、S002、S003 递增。
4. title 要短、准，能直接代表评分点。
5. response_strategy 要写成“投标文件应如何响应该评分点”的具体建议。
6. 如果无法识别分值，score 填 null。
7. keywords 必须是字符串数组。
8. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "tender_requirement_extractor.md": """你是招标需求抽取专家。

任务：只根据用户提供的招标文件提取项目需求、交付约束和资格要求摘要，输出合法 JSON 对象。

输出结构必须为：
{
  "project_name": "",
  "project_location": "",
  "service_period": "",
  "warranty_period": "",
  "procurement_scope": [],
  "functional_requirements": [],
  "service_requirements": [],
  "delivery_requirements": [],
  "implementation_requirements": [],
  "acceptance_requirements": [],
  "qualification_requirements": [],
  "evidence_notes": []
}

硬性要求：
1. 只能从招标文件中提取，不能编造。
2. 列表字段必须是字符串数组。
3. 提取“可指导后续写作”的明确要求，不要写泛泛表述。
4. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "company_facts_extractor.md": """你是公司资料事实抽取专家。

任务：只根据用户提供的公司资料提取可复用的公司事实，输出合法 JSON 对象。

输出结构必须为：
{
  "bidder_name": "",
  "core_products": [],
  "company_advantages": [],
  "similar_cases": [],
  "team_roles": []
}

硬性要求：
1. 只能从公司资料中提取，不能编造。
2. 不确定的字段填空字符串或空数组。
3. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
4. bidder_name 只有在公司资料中出现明确投标人/供应商/公司全称时才填写，不能把招标人、采购人或项目名称误填为 bidder_name。
5. 优先提取明确、稳定、可全篇复用的事实；不要把口号、评价性语言写入事实字段。
""",
    "generate_outline.md": """你是资深标书架构师。

任务：根据招标文件、评分点 JSON、全局事实 JSON 和模板分析结果生成标书大纲，输出合法 JSON 对象。

输出结构必须为：
{
  "chapters": [
    {
      "id": "01",
      "title": "项目理解与需求分析",
      "score_point_ids": ["S001"],
      "description": "回应项目背景、建设目标、业务需求理解等评分要求",
      "sections": [
        {
          "id": "01.01",
          "title": "项目背景理解",
          "score_point_ids": ["S001"],
          "writing_requirements": [
            "结合招标文件描述项目建设背景"
          ]
        }
      ]
    }
  ]
}

硬性要求：
1. 如果提供了模板章节目录，必须严格沿用模板章节 id、标题和层级，不允许新造章节标题替换模板标题。
2. 每个章节都必须绑定 score_point_ids；每个评分点至少要被一个章节覆盖。
3. 必须覆盖模板 schema 中的 writing_tasks；fill_slots 对应的事实字段要在相关章节或最终 Word 填充阶段能找到依据。
4. sections 至少包含一个二级目录；如果模板已有二级标题，优先沿用模板二级标题。
5. 不要生成无关目录，不要因为某个固定表格而假设所有模板都存在相同结构。
6. 模板依据映射中 weak/missing 的任务，要在 writing_requirements 里提示谨慎表述或补充证据。
7. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "write_chapter.md": """你是资深标书写作专家。

任务：根据当前章节、绑定评分点、全局事实、当前章节相关模板任务和已提供的资料片段，生成当前章节 Markdown 正文。

硬性要求：
1. 只写当前章节，不要写其他章节。
2. 必须覆盖当前章节绑定的评分点。
3. 必须结合已提供的招标文件片段和公司资料片段。
4. 不允许编造公司资质、案例、证书、人员或未提供的承诺。
5. 内容要专业、正式、适合投标文件。
6. 表格使用 Markdown 表格。
7. 章节开头包含一级标题，例如 # 01 项目理解与需求分析。
8. 不要使用 Markdown 代码块包裹全文。
9. 模板任务优先级高于通用扩写：writing_task 要落成章节内容，fill_slot 要保证后续可被事实填充。
10. 对模板任务状态为 weak/missing 的内容，只能写成拟响应、按要求提交、随投标文件附后或需人工补证，不能写成已具备、已提供、已完成。
""",
    "review_chapter.md": """你是严谨的标书章节审核专家。

任务：审核当前章节是否覆盖绑定评分点，并检查内容是否空泛、是否存在明显编造、是否与全局事实冲突。

输出结构必须为：
{
  "chapter_id": "01",
  "chapter_title": "项目理解与需求分析",
  "score_coverage": [
    {
      "score_point_id": "S001",
      "covered": true,
      "coverage_level": "high",
      "evidence": "正文已说明项目背景、建设目标和业务需求",
      "suggestion": ""
    }
  ],
  "problems": [
    {
      "type": "content_too_generic",
      "severity": "major",
      "description": "部分内容偏通用",
      "suggestion": "增加招标文件中的具体业务场景"
    }
  ],
  "priority_fixes": [
    {
      "id": "fix_01",
      "severity": "major",
      "source": "problem",
      "score_point_id": "",
      "problem_type": "content_too_generic",
      "target": "部分内容偏通用",
      "action": "增加招标文件中的具体业务场景",
      "acceptance": "关键段落出现可核验的招标业务场景描述"
    }
  ],
  "need_rewrite": false,
  "need_evidence": false
}

硬性要求：
1. 只审核当前章节绑定评分点。
2. coverage_level 只能使用 high、medium、low、none。
3. problems.severity 与 priority_fixes.severity 只能使用 blocker、major、minor。
4. need_rewrite 仅在存在 blocker/major 时为 true。
5. priority_fixes 只列本轮最优先的 1-5 项。
6. 缺材料时 need_evidence=true，纯缺证问题 type 用 missing_evidence。
7. 第一版只审核，不自动重写。
8. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "select_context.md": """你是标书章节资料选择助手。

任务：根据章节任务、绑定评分点、全局事实、当前章节相关模板任务、招标文件 chunk 目录和公司资料 chunk 目录，为当前章节选择最相关的资料片段。

输出结构必须为：
{
  "chapter_id": "01",
  "selected_tender_chunks": [
    {
      "id": "TENDER_001",
      "reason": "包含项目背景和建设目标"
    }
  ],
  "selected_company_chunks": [
    {
      "id": "COMPANY_003",
      "reason": "包含相关项目经验"
    }
  ]
}

硬性要求：
1. 每章最多选择 8 个 tender chunks 和 8 个 company chunks。
2. 只能选择输入目录中真实存在的 chunk id。
3. 优先选择能直接支撑模板 writing_task、fill_slot 和评分点响应的片段。
4. 如果模板任务给出了 tender_chunk_ids/company_chunk_ids，除非明显无关，否则优先保留这些片段。
5. 对 weak/missing 的模板任务，优先寻找能补足证据的片段。
6. 不要编造 chunk id。
7. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "global_review.md": """你是严谨的标书全文一致性审核专家。

任务：根据全局事实、大纲、评分点、章节摘要（或章节正文）和章节审核结果，输出全文一致性审核 JSON。

输出结构必须为：
{
  "project_name_consistent": true,
  "bidder_name_consistent": true,
  "service_period_consistent": true,
  "warranty_period_consistent": true,
  "chapter_conflicts": [],
  "uncovered_score_points": [],
  "missing_chapters": [],
  "fabrication_risks": [],
  "suggestions": [],
  "need_manual_review": false
}

审核重点：
1. 项目名称是否一致。
2. 投标人名称是否一致。
3. 服务周期是否一致。
4. 质保期是否一致。
5. 章节之间是否有明显冲突。
6. 是否有评分点未覆盖。
7. 是否有章节缺失。
8. 是否存在明显编造风险。
9. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "summarize_chapter.md": """你是标书章节摘要和风险识别助手。

任务：根据输入的章节正文、章节任务、绑定评分点、全局事实和章节审核结果，生成结构化章节摘要 JSON。

输出结构：
{
  "chapter_id": "01",
  "chapter_title": "",
  "source_chapter_path": "",
  "covered_score_points": [],
  "main_claims": [],
  "key_solutions": [],
  "project_names": [],
  "bidder_names": [],
  "service_periods": [],
  "warranty_periods": [],
  "dates": [],
  "amounts": [],
  "personnel": [],
  "qualifications": [],
  "case_references": [],
  "risks": [],
  "possible_conflicts": [],
  "fabrication_risks": [],
  "need_manual_review": false
}

硬性要求：
1. 只根据输入中的章节正文、任务、评分点、全局事实和审核结果提取，不要补充输入中没有的信息。
2. 不要编造案例、人员、资质、金额。
3. 如果发现疑似编造或事实来源不足，写入 fabrication_risks。
4. 如果发现项目名称、投标人名称、服务周期、质保期和全局事实不一致，写入 possible_conflicts。
5. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "rewrite_chapter.md": """你是资深标书写作专家，负责根据审核意见修改章节。

任务：根据审核结果中的具体问题，对原章节正文进行针对性修改和补充，输出修改后的完整章节 Markdown。

硬性要求：
1. 只输出当前章节 Markdown 正文，不要输出其他章节。
2. 必须保留章节标题，格式为 # 01 章节标题。
3. 优先处理 priority_fixes 中的 blocker/major，逐项落实 action 与 acceptance。
4. 必须针对 review 中的 problems 修复 blocker/major 项。
5. 对 coverage_level 为 none 或 low 的评分点，必须重点补充。
6. 必须结合已提供的招标文件片段和公司资料片段，但不能编造。
7. 不允许编造未在资料中出现的资质、案例、证书、人员、金额、日期。
8. 表格使用 Markdown 表格。
9. 不要输出解释，不要使用 Markdown 代码块包裹全文。
""",
}


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def init_project(root: Path | None = None) -> None:
    root = root or project_root()
    ensure_dirs(
        root,
        [
            "sources/tender",
            "sources/company",
            "sources/template",
            "inputs",
            "workspace",
            "workspace/chunks",
            "workspace/imported",
            "workspace/jobs",
            "workspace/contexts",
            "workspace/chapters",
            "workspace/reviews",
            "workspace/rewrites",
            "workspace/summaries",
            "outputs",
            "prompts",
        ],
    )
    ensure_file(root / "inputs" / "tender.md")
    ensure_file(root / "inputs" / "score.md")
    ensure_file(root / "inputs" / "company.md")
    save_project_profile(root, None)
    for filename in required_prompt_files():
        content = DEFAULT_PROMPTS.get(filename, "")
        ensure_file(root / "prompts" / filename, content)
    print(f"[完成] 项目已初始化: {root}")


def _run_prepare_inputs(root: Path) -> None:
    from input_preparer import prepare_inputs

    print("[执行] 导入原始资料...")
    prepare_inputs(root)


def _run_split_docs(root: Path) -> None:
    from document_splitter import split_docs

    print("[执行] 切分文档...")
    split_docs(root)


def _run_plan_jobs(root: Path) -> None:
    from job_planner import plan_chapter_jobs

    print("[执行] 生成章节任务...")
    plan_chapter_jobs(root)


def _run_select_context_all(root: Path) -> None:
    from context_selector import select_contexts_for_jobs
    from stage_validation import missing_ids_for_stage

    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists() or not list(jobs_dir.glob("*.json")):
        raise FileNotFoundError(
            f"缺少章节任务目录: {jobs_dir}，请先执行 plan-jobs"
        )
    missing_ids = set(missing_ids_for_stage(root, "select_contexts"))
    jobs = [
        read_json(f)
        for f in sorted(jobs_dir.glob("*.json"))
        if f.stem in missing_ids
    ]
    if not jobs:
        print("[跳过] 所有章节上下文均已存在且有效。")
        return
    print(f"[执行] 补齐 {len(jobs)} 个缺失章节上下文...")
    select_contexts_for_jobs(jobs, root)


def _run_select_context(root: Path, chapter_id: str) -> None:
    from context_selector import select_context_for_job

    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    if not job_path.exists():
        raise FileNotFoundError(
            f"缺少章节任务: {job_path}，请先执行 plan-jobs"
        )
    job = read_json(job_path)
    print(f"[执行] 选择章节 {chapter_id} 上下文...")
    select_context_for_job(job, root)


def _run_write_all(root: Path, workers: int | None = None, max_retries: int = 0) -> None:
    from stage_validation import context_ids, missing_ids_for_stage
    from subagent_runner import run_write_all as concurrent_write_all

    pending_ids = missing_ids_for_stage(root, "write_chapters")
    missing_contexts = sorted(set(pending_ids) - context_ids(root))
    if missing_contexts:
        raise FileNotFoundError(
            f"仍有 {len(missing_contexts)} 个章节缺少上下文，请先执行 select-context-all: {missing_contexts[:10]}"
        )
    if not pending_ids:
        print("[跳过] 所有章节正文均已存在且有效。")
        return
    print(f"[执行] 补写 {len(pending_ids)} 个缺失章节...")
    result = concurrent_write_all(
        root,
        workers=workers,
        chapter_ids=pending_ids,
        max_retries=max_retries,
    )
    failed = result.get("failed", [])
    if failed:
        messages = [
            f"章节 {item['chapter_id']} 写作失败(已重试 {item.get('attempts', 1)} 次): {item['error']}"
            for item in failed
        ]
        raise RuntimeError("；".join(messages))


def run_pipeline(root: Path | None = None, workers: int | None = None, max_retries: int = 0) -> None:
    root = root or project_root()
    workers = clamp_workers(workers)
    core_specs = workflow_stage_specs()
    total = len(core_specs)
    stage_runners = {
        "init_workspace": lambda: init_project(root),
        "prepare_inputs": lambda: _run_prepare_inputs(root),
        "split_docs": lambda: _run_split_docs(root),
        "parse_score": lambda: parse_score(root),
        "extract_facts": lambda: extract_facts(root),
        "build_materials_checklist": lambda: build_materials_checklist(root),
        "build_template_evidence": lambda: build_template_evidence(root),
        "generate_outline": lambda: generate_outline(root),
        "plan_chapter_jobs": lambda: _run_plan_jobs(root),
        "select_contexts": lambda: _run_select_context_all(root),
        "write_chapters": lambda: _run_write_all(root, workers=workers, max_retries=max_retries),
        "review_fix_chapters": lambda: review_fix_all(root, workers=workers),
        "build_source_trace_index": lambda: build_source_trace_index(root),
        "build_score_coverage_matrix": lambda: build_score_coverage_matrix(root),
        "estimate_final_score": lambda: estimate_final_score(root),
        "summarize_chapters": lambda: summarize_all_chapters(root),
        "global_review": lambda: run_global_review(root),
        "compliance_check": lambda: run_compliance_check(root),
        "build_markdown": lambda: build_markdown(root),
        "build_docx": lambda: build_docx(root),
        "check_format": lambda: check_output_format(root),
    }
    for index, spec in enumerate(core_specs, start=1):
        runner = stage_runners.get(spec.id)
        if runner is None:
            continue
        print(f"[{index}/{total}] {spec.label}...")
        runner()


def run_graph_pipeline(
    root: Path | None = None,
    workers: int | None = None,
    resume: bool = False,
    max_retries: int = 0,
) -> None:
    from graph.bid_graph import run_bid_graph

    root = root or project_root()
    run_bid_graph(root, workers=clamp_workers(workers), resume=resume, max_retries=max_retries)


def set_project_profile(root: Path | None = None, project_type: str | None = None) -> None:
    root = root or project_root()
    path = save_project_profile(root, project_type)
    print(f"[完成] 已设置项目类型: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="标书写作 Agent MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)
    project_type_help = "项目类型，可选: " + ", ".join(choice["project_type"] for choice in project_profile_choices())

    subparsers.add_parser("init", help="初始化目录、输入文件和默认提示词")
    set_profile_parser = subparsers.add_parser("set-project-profile", help="设置当前工作空间项目类型")
    set_profile_parser.add_argument("--project-type", default="general", help=project_type_help)

    subparsers.add_parser("init-demo", help="生成最小演示招标文件和公司资料")

    subparsers.add_parser("prepare-inputs", help="导入原始资料：将 sources/ 下的 PDF/DOCX/MD 转为 inputs/ 下标准文件")
    subparsers.add_parser("analyze-template", help="解析 inputs/template.docx，生成模板 schema")

    subparsers.add_parser("split-docs", help="切分招标文件和公司资料为 chunk")
    subparsers.add_parser("parse-score", help="解析评分标准")
    subparsers.add_parser("extract-facts", help="提取全局事实")
    subparsers.add_parser("build-materials-checklist", help="生成材料/资格待补清单（解析后、写作前）")
    subparsers.add_parser("build-template-evidence", help="根据模板 schema 生成依据映射和质量报告")
    subparsers.add_parser("generate-outline", help="生成标书大纲")
    subparsers.add_parser("plan-jobs", help="生成章节任务包")

    select_context_parser = subparsers.add_parser("select-context", help="为单个章节选择上下文")
    select_context_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")

    subparsers.add_parser("select-context-all", help="为所有章节选择上下文")

    write_chapter_parser = subparsers.add_parser("write-chapter", help="生成单个章节（需要先执行 select-context）")
    write_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")

    write_all_parser = subparsers.add_parser("write-all", help="生成所有章节（支持并发）")
    write_all_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"章节写作 worker 数，默认 {workers_default()}，最大 {workers_max()}（BID_AGENT_WORKERS_*）",
    )
    write_all_parser.add_argument("--max-retries", type=int, default=0, help="章节写作失败后的最大重试次数，默认 0")

    review_chapter_parser = subparsers.add_parser("review-chapter", help="审核单个章节")
    review_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")
    review_all_parser = subparsers.add_parser("review-all", help="并发审核所有章节")
    review_all_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"章节审核 worker 数，默认 {workers_default()}，最大 {workers_max()}（BID_AGENT_WORKERS_*）",
    )

    rewrite_chapter_parser = subparsers.add_parser("rewrite-chapter", help="根据审核意见重写单个章节")
    rewrite_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")
    subparsers.add_parser("rewrite-all", help="重写所有 need_rewrite=true 的章节")
    review_fix_all_parser = subparsers.add_parser("review-fix-all", help="审核所有章节并自动改稿（最多 2 轮，并发）")
    review_fix_all_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"审核/改稿 worker 数，默认 {workers_default()}，最大 {workers_max()}（BID_AGENT_WORKERS_*）",
    )

    summarize_chapter_parser = subparsers.add_parser("summarize-chapter", help="为单个章节生成结构化摘要")
    summarize_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")

    subparsers.add_parser("summarize-all", help="为所有章节生成结构化摘要")
    subparsers.add_parser("build-source-trace", help="生成章节来源追溯索引")
    subparsers.add_parser("build-score-coverage", help="生成评分点覆盖矩阵")
    subparsers.add_parser("estimate-score", help="根据覆盖矩阵估算终稿得分")
    subparsers.add_parser("global-review", help="全文一致性审核（优先使用章节摘要）")
    subparsers.add_parser("compliance-check", help="专项合规检查（资格/废标/强制参数/签章/保证金/有效期/完整性/一致性/报价/偏离）")
    subparsers.add_parser("check-price-tables", help="报价表确定性验算（数量×单价）")
    subparsers.add_parser("check-deviation-tables", help="偏离表逐行检查")
    subparsers.add_parser("validate-claims", help="claim 防编造与 source_trace 对齐")

    subparsers.add_parser("build-md", help="拼接最终 Markdown")
    subparsers.add_parser("build-docx", help="生成 Word 文件")
    subparsers.add_parser("check-format", help="检查最终 Markdown/Word 格式")

    run_parser = subparsers.add_parser("run", help="按完整流水线运行（CLI 模式）")
    run_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"章节写作 worker 数，默认 {workers_default()}，最大 {workers_max()}（BID_AGENT_WORKERS_*）",
    )
    run_parser.add_argument("--max-retries", type=int, default=0, help="章节写作失败后的最大重试次数，默认 0")
    run_parser.add_argument("--project-type", default="", help=project_type_help)

    graph_run_parser = subparsers.add_parser("graph-run", help="按 LangGraph 主图运行完整流程")
    graph_run_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"章节写作 worker 数，默认 {workers_default()}，最大 {workers_max()}（BID_AGENT_WORKERS_*）",
    )
    graph_run_parser.add_argument("--resume", action="store_true", help="从 workspace/run_state.json 和已有产物断点续跑")
    graph_run_parser.add_argument("--max-retries", type=int, default=0, help="章节写作失败后的最大重试次数，默认 0")
    graph_run_parser.add_argument("--project-type", default="", help=project_type_help)

    subparsers.add_parser("validate", help="项目功能闭环检查：验证文件、环境变量、中间产物完整性")

    tool_parser = subparsers.add_parser("tool", help="调用 Agent Tool 层（PR-1: run_stage / 阶段 command）")
    tool_parser.add_argument("--name", required=False, default="", help="tool 名，如 run_stage / parse-score")
    tool_parser.add_argument("--args", default="{}", help="JSON 参数对象")
    tool_parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    tool_parser.add_argument("--list", action="store_true", dest="list_tools", help="列出可用 tools 后退出")

    agent_graph_parser = subparsers.add_parser("agent-graph-run", help="LangGraph Supervisor 短循环（只读自动，变更需 --yes）")
    agent_graph_parser.add_argument("--goal", required=True, help="用户目标自然语言")
    agent_graph_parser.add_argument("--max-steps", type=int, default=5, help="最大步数")
    agent_graph_parser.add_argument("--yes", action="store_true", help="确认执行变更类 tool")
    agent_graph_parser.add_argument("--use-llm", action="store_true", help="使用 LLM 决策（默认规则）")

    control_parser = subparsers.add_parser("control", help="通过 V2 HTTP CommandGateway 控制工作区")
    control_parser.add_argument("control_args", nargs=argparse.REMAINDER)


    return parser


def main() -> int:
    _configure_console_encoding()
    if len(sys.argv) > 1 and sys.argv[1] == "control":
        from control_cli import main as control_main

        return control_main(sys.argv[2:])
    root = project_root()
    args = build_parser().parse_args()
    runs_root = Path(
        os.environ.get("BID_AGENT_RUNS_ROOT")
        or (Path(__file__).resolve().parent.parent / "runs")
    ).resolve()
    managed_workspace = root.resolve() != runs_root and root.resolve().is_relative_to(runs_root)
    execution_worker = str(os.environ.get("BID_AGENT_EXECUTION_WORKER") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    if managed_workspace and not execution_worker and args.command != "validate":
        print(
            "[拒绝] 受管 V2 工作区禁止通过旧阶段 CLI 直接执行 mutation；"
            "请使用 `python src/main.py control ...` 提交 Command。"
        )
        return 2

    if args.command == "init":
        init_project(root)
    elif args.command == "set-project-profile":
        set_project_profile(root, args.project_type)
    elif args.command == "init-demo":
        from demo_initializer import init_demo

        init_demo(root)
    elif args.command == "prepare-inputs":
        _run_prepare_inputs(root)
    elif args.command == "analyze-template":
        print("[执行] 解析模板结构...")
        analyze_template(root)
    elif args.command == "split-docs":
        _run_split_docs(root)
    elif args.command == "parse-score":
        print("[执行] 解析评分标准...")
        parse_score(root)
    elif args.command == "extract-facts":
        print("[执行] 提取全局事实...")
        extract_facts(root)
    elif args.command == "build-materials-checklist":
        print("[执行] 生成材料/资格清单...")
        build_materials_checklist(root)
    elif args.command == "build-template-evidence":
        print("[执行] 生成模板依据映射...")
        build_template_evidence(root)
    elif args.command == "generate-outline":
        print("[执行] 生成大纲...")
        generate_outline(root)
    elif args.command == "plan-jobs":
        _run_plan_jobs(root)
    elif args.command == "select-context":
        _run_select_context(root, args.chapter)
    elif args.command == "select-context-all":
        _run_select_context_all(root)
    elif args.command == "write-chapter":
        print(f"[执行] 生成章节 {args.chapter}...")
        write_chapter(args.chapter, root)
    elif args.command == "write-all":
        _run_write_all(root, workers=clamp_workers(args.workers), max_retries=args.max_retries)
    elif args.command == "review-chapter":
        print(f"[执行] 审核章节 {args.chapter}...")
        review_chapter(args.chapter, root)
    elif args.command == "review-all":
        print("[执行] 审核所有章节（并发子 agent）...")
        from subagent_runner import run_review_all

        run_review_all(root, workers=clamp_workers(args.workers))
    elif args.command == "rewrite-chapter":
        print(f"[执行] 根据审核意见重写章节 {args.chapter}...")
        rewrite_chapter(args.chapter, root)
    elif args.command == "rewrite-all":
        print("[执行] 重写所有需改稿章节...")
        rewrite_all(root)
    elif args.command == "review-fix-all":
        print("[执行] 审核并自动改稿（并发子 agent）...")
        review_fix_all(root, workers=clamp_workers(args.workers))
    elif args.command == "summarize-chapter":
        print(f"[执行] 生成章节 {args.chapter} 摘要...")
        summarize_chapter(args.chapter, root)
    elif args.command == "summarize-all":
        print("[执行] 生成所有章节摘要...")
        summarize_all_chapters(root)
    elif args.command == "build-source-trace":
        print("[执行] 生成章节来源追溯索引...")
        build_source_trace_index(root)
    elif args.command == "build-score-coverage":
        print("[执行] 生成评分点覆盖矩阵...")
        build_score_coverage_matrix(root)
    elif args.command == "estimate-score":
        print("[执行] 终稿估分...")
        estimate_final_score(root)
    elif args.command == "global-review":
        print("[执行] 全文一致性审核...")
        run_global_review(root)
    elif args.command == "compliance-check":
        print("[执行] 专项合规检查...")
        run_compliance_check(root)
    elif args.command == "check-price-tables":
        print("[执行] 报价表确定性验算...")
        from price_table_parser import parse_price_tables

        report = parse_price_tables(root)
        print(
            f"[结果] tables={report.get('table_count')} issues={report.get('issue_count')} "
            f"ok={report.get('ok')} -> workspace/price_table_report.json"
        )
    elif args.command == "check-deviation-tables":
        print("[执行] 偏离表逐行检查...")
        from deviation_table_checker import check_deviation_tables

        report = check_deviation_tables(root)
        print(
            f"[结果] tables={report.get('table_count')} fail_rows={report.get('fail_row_count')} "
            f"ok={report.get('ok')} -> workspace/deviation_table_report.json"
        )
    elif args.command == "validate-claims":
        print("[执行] claim 防编造与对齐...")
        from claim_validator import validate_all_chapter_claims

        path = validate_all_chapter_claims(root)
        print(f"[结果] {path}")
    elif args.command == "build-md":
        print("[执行] 拼接 Markdown...")
        build_markdown(root)
    elif args.command == "build-docx":
        print("[执行] 生成 Word...")
        build_docx(root)
    elif args.command == "check-format":
        print("[执行] 检查输出格式...")
        check_output_format(root)
    elif args.command == "run":
        if args.project_type:
            save_project_profile(root, args.project_type)
        run_pipeline(root, workers=args.workers, max_retries=args.max_retries)
    elif args.command == "graph-run":
        if args.project_type:
            save_project_profile(root, args.project_type)
        run_graph_pipeline(root, workers=args.workers, resume=args.resume, max_retries=args.max_retries)
    elif args.command == "agent-graph-run":
        from graph.supervisor_graph import run_supervisor_graph
        import json

        result = run_supervisor_graph(
            args.goal,
            root=root,
            max_steps=int(getattr(args, "max_steps", 5) or 5),
            use_llm=bool(getattr(args, "use_llm", False)),
            user_confirmed=bool(getattr(args, "yes", False)),
        )
        print(json.dumps({k: result.get(k) for k in ("reply", "steps", "need_confirm", "done", "last_tool", "goal_id", "last_observation")}, ensure_ascii=False, indent=2))
        return 0 if result.get("done") or result.get("need_confirm") else 1
    elif args.command == "control":
        from control_cli import main as control_main

        return control_main(getattr(args, "control_args", []))
    elif args.command == "tool":
        import json
        from agent.tool_registry import tool_manifest
        from agent.tool_runtime import invoke as tool_invoke

        if getattr(args, "list_tools", False):
            for item in tool_manifest():
                print(f"{item['name']}\t{item['label']}\t{item['id']}")
            return 0
        if not args.name:
            print("[失败] 请提供 --name，或使用 --list 查看 tools")
            return 1
        try:
            payload = json.loads(args.args or "{}")
        except json.JSONDecodeError as exc:
            print(f"[失败] --args 不是合法 JSON: {exc}")
            return 1
        if not isinstance(payload, dict):
            print("[失败] --args 必须是 JSON 对象")
            return 1
        result = tool_invoke(
            args.name,
            payload,
            root=root,
            dry_run=bool(getattr(args, "dry_run", False)),
            actor="cli",
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    elif args.command == "validate":
        report = validate_project(root)
        for item in report["results"]:
            tag = item["level"].upper()
            if item["level"] == "ok":
                print(f"[{tag}] {item['message']}")
            elif item["level"] == "warn":
                print(f"[{tag}] {item['message']}")
                if item.get("suggestion"):
                    print(f"      建议: {item['suggestion']}")
            elif item["level"] == "fail":
                print(f"[{tag}] {item['message']}")
                if item.get("suggestion"):
                    print(f"      建议: {item['suggestion']}")

        print()
        print(f"验证结果：")
        print(f"OK: {report['ok']}")
        print(f"WARN: {report['warn']}")
        print(f"FAIL: {report['fail']}")

        if report["fail"] > 0:
            return 1
        return 0
    else:
        raise ValueError(f"未知命令: {args.command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
