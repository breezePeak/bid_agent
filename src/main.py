from __future__ import annotations

import argparse
from pathlib import Path

from chapter_reviewer import review_all, review_chapter
from chapter_writer import write_all, write_chapter
from docx_builder import build_docx, build_markdown
from fact_extractor import extract_facts
from outline_generator import generate_outline
from score_parser import parse_score
from utils import ensure_dirs, ensure_file, project_root


DEFAULT_PROMPTS = {
    "parse_score.md": """你是资深投标文件评分标准分析专家。

任务：从用户提供的评分标准 Markdown 中抽取所有评分点，输出合法 JSON 数组。

硬性要求：
1. 不允许丢失任何评分项。
2. 每个评分点必须包含 id、category、title、score、requirement、keywords、response_strategy 字段。
3. id 使用 S001、S002、S003 递增。
4. 如果无法识别分值，score 填 null。
5. keywords 必须是字符串数组。
6. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "extract_facts.md": """你是严谨的投标资料事实抽取助手。

任务：只根据用户提供的招标文件和公司资料提取全局事实，输出合法 JSON 对象。

输出结构必须为：
{
  "project_name": "",
  "bidder_name": "",
  "service_period": "",
  "warranty_period": "",
  "project_location": "",
  "core_products": [],
  "company_advantages": [],
  "similar_cases": [],
  "team_roles": []
}

硬性要求：
1. 只能从输入资料中提取，不能编造。
2. 不确定的字段填空字符串或空数组。
3. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "generate_outline.md": """你是资深标书架构师。

任务：根据招标文件、评分点 JSON 和全局事实 JSON 生成标书大纲，输出合法 JSON 对象。

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
1. 一级目录尽量对应评分大类。
2. 每个章节都必须绑定 score_point_ids。
3. 每个评分点至少要被一个章节覆盖。
4. sections 至少包含一个二级目录。
5. 不要生成无关目录。
6. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "write_chapter.md": """你是资深标书写作专家。

任务：根据当前章节、绑定评分点、全局事实和已提供的资料片段，生成当前章节 Markdown 正文。

硬性要求：
1. 只写当前章节，不要写其他章节。
2. 必须覆盖当前章节绑定的评分点。
3. 必须结合已提供的招标文件片段和公司资料片段。
4. 不允许编造公司资质、案例、证书、人员或未提供的承诺。
5. 内容要专业、正式、适合投标文件。
6. 表格使用 Markdown 表格。
7. 章节开头包含一级标题，例如 # 01 项目理解与需求分析。
8. 不要使用 Markdown 代码块包裹全文。
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
      "description": "部分内容偏通用",
      "suggestion": "增加招标文件中的具体业务场景"
    }
  ],
  "need_rewrite": false
}

硬性要求：
1. 只审核当前章节绑定评分点。
2. coverage_level 只能使用 high、medium、low、none。
3. 第一版只审核，不自动重写。
4. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "select_context.md": """你是标书章节资料选择助手。

任务：根据章节任务、绑定评分点、全局事实、招标文件 chunk 目录和公司资料 chunk 目录，为当前章节选择最相关的资料片段。

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
3. 优先选择能直接支撑评分点响应的片段。
4. 不要编造 chunk id。
5. 只输出 JSON，不要输出解释，不要使用 Markdown 代码块。
""",
    "global_review.md": """你是严谨的标书全文一致性审核专家。

任务：根据全局事实、大纲、评分点、章节正文和章节审核结果，输出全文一致性审核 JSON。

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
}


def init_project(root: Path | None = None) -> None:
    root = root or project_root()
    ensure_dirs(
        root,
        [
            "inputs",
            "workspace",
            "workspace/chunks",
            "workspace/jobs",
            "workspace/contexts",
            "workspace/chapters",
            "workspace/reviews",
            "outputs",
            "prompts",
        ],
    )
    ensure_file(root / "inputs" / "tender.md")
    ensure_file(root / "inputs" / "score.md")
    ensure_file(root / "inputs" / "company.md")
    for filename, content in DEFAULT_PROMPTS.items():
        ensure_file(root / "prompts" / filename, content)
    print(f"[完成] 项目已初始化: {root}")


def run_pipeline(root: Path | None = None) -> None:
    root = root or project_root()
    print("[1/7] 解析评分标准...")
    parse_score(root)
    print("[2/7] 提取全局事实...")
    extract_facts(root)
    print("[3/7] 生成大纲...")
    generate_outline(root)
    print("[4/7] 生成章节...")
    write_all(root)
    print("[5/7] 审核章节...")
    review_all(root)
    print("[6/7] 拼接 Markdown...")
    build_markdown(root)
    print("[7/7] 生成 Word...")
    build_docx(root)


def run_graph_pipeline(root: Path | None = None, workers: int = 1) -> None:
    from graph.bid_graph import run_bid_graph

    root = root or project_root()
    final_state = run_bid_graph(root, workers=workers)
    failed = final_state.get("failed_chapters", [])
    errors = final_state.get("errors", [])
    if failed:
        print(f"[警告] 有 {len(failed)} 个章节执行失败，详情已写入 LangGraph state。")
    if errors:
        print(f"[警告] 流程累计 {len(errors)} 条错误/警告。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="标书写作 Agent MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="初始化目录、输入文件和默认提示词")
    subparsers.add_parser("parse-score", help="解析评分标准")
    subparsers.add_parser("extract-facts", help="提取全局事实")
    subparsers.add_parser("generate-outline", help="生成标书大纲")

    write_chapter_parser = subparsers.add_parser("write-chapter", help="生成单个章节")
    write_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")
    subparsers.add_parser("write-all", help="串行生成所有章节")

    review_chapter_parser = subparsers.add_parser("review-chapter", help="审核单个章节")
    review_chapter_parser.add_argument("--chapter", required=True, help="章节 ID，例如 01")
    subparsers.add_parser("review-all", help="串行审核所有章节")

    subparsers.add_parser("build-md", help="拼接最终 Markdown")
    subparsers.add_parser("build-docx", help="生成 Word 文件")
    run_parser = subparsers.add_parser("run", help="按 LangGraph 主图运行完整流程")
    run_parser.add_argument("--workers", type=int, default=1, help="章节任务 worker 数，当前版本保留参数")

    graph_run_parser = subparsers.add_parser("graph-run", help="按 LangGraph 主图运行完整流程")
    graph_run_parser.add_argument("--workers", type=int, default=1, help="章节任务 worker 数，当前版本保留参数")
    return parser


def main() -> int:
    root = project_root()
    args = build_parser().parse_args()

    if args.command == "init":
        init_project(root)
    elif args.command == "parse-score":
        print("[执行] 解析评分标准...")
        parse_score(root)
    elif args.command == "extract-facts":
        print("[执行] 提取全局事实...")
        extract_facts(root)
    elif args.command == "generate-outline":
        print("[执行] 生成大纲...")
        generate_outline(root)
    elif args.command == "write-chapter":
        print(f"[执行] 生成章节 {args.chapter}...")
        write_chapter(args.chapter, root)
    elif args.command == "write-all":
        print("[执行] 生成所有章节...")
        write_all(root)
    elif args.command == "review-chapter":
        print(f"[执行] 审核章节 {args.chapter}...")
        review_chapter(args.chapter, root)
    elif args.command == "review-all":
        print("[执行] 审核所有章节...")
        review_all(root)
    elif args.command == "build-md":
        print("[执行] 拼接 Markdown...")
        build_markdown(root)
    elif args.command == "build-docx":
        print("[执行] 生成 Word...")
        build_docx(root)
    elif args.command == "run":
        run_graph_pipeline(root, workers=args.workers)
    elif args.command == "graph-run":
        run_graph_pipeline(root, workers=args.workers)
    else:
        raise ValueError(f"未知命令: {args.command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
