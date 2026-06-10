from __future__ import annotations

from pathlib import Path

from agents.fact_agent import run as fact_agent
from agents.global_review_agent import run as global_review_agent
from agents.outline_agent import run as outline_agent
from agents.score_agent import run as score_agent
from chapter_reviewer import review_chapter
from chapter_summarizer import summarize_chapter
from context_selector import select_context_for_job
from docx_builder import build_docx, build_markdown
from document_splitter import split_docs
from input_preparer import prepare_inputs
from job_planner import plan_chapter_jobs
from subagent_runner import run_write_all as concurrent_write_all
from utils import ensure_dirs, ensure_file, project_root, stringify


def _root(state) -> Path:
    from graph.state import BidState
    return Path(state.get("root_dir") or project_root())


def init_workspace(state) -> dict:
    from graph.state import BidState
    root = Path(state.get("root_dir") or project_root())
    print("[1/13] 初始化工作区...")
    ensure_dirs(
        root,
        [
            "sources/tender",
            "sources/company",
            "sources/template",
            "inputs",
            "workspace",
            "workspace/chunks",
            "workspace/jobs",
            "workspace/contexts",
            "workspace/chapters",
            "workspace/reviews",
            "workspace/summaries",
            "outputs",
            "prompts",
        ],
    )
    ensure_file(root / "inputs" / "tender.md")
    ensure_file(root / "inputs" / "score.md")
    ensure_file(root / "inputs" / "company.md")
    return {
        "root_dir": str(root),
        "workers": int(state.get("workers") or 1),
        "tender_path": str(root / "inputs" / "tender.md"),
        "score_path": str(root / "inputs" / "score.md"),
        "company_path": str(root / "inputs" / "company.md"),
        "template_path": str(root / "inputs" / "template.docx"),
        "jobs_dir": str(root / "workspace" / "jobs"),
        "contexts_dir": str(root / "workspace" / "contexts"),
        "chapters_dir": str(root / "workspace" / "chapters"),
        "reviews_dir": str(root / "workspace" / "reviews"),
    }


def prepare_inputs_node(state) -> dict:
    root = _root(state)
    print("[2/13] 导入原始资料...")
    prepare_inputs(root)
    return {
        "tender_path": str(root / "inputs" / "tender.md"),
        "company_path": str(root / "inputs" / "company.md"),
    }


def split_docs_node(state) -> dict:
    root = _root(state)
    print("[3/13] 切分文档...")
    tender_chunks_path, company_chunks_path = split_docs(root)
    return {"tender_chunks_path": str(tender_chunks_path), "company_chunks_path": str(company_chunks_path)}


def parse_score_node(state) -> dict:
    root = _root(state)
    print("[4/13] 解析评分标准...")
    score_points_path = score_agent(root)
    return {"score_points_path": str(score_points_path)}


def extract_facts_node(state) -> dict:
    root = _root(state)
    print("[5/13] 提取全局事实...")
    global_facts_path = fact_agent(root)
    return {"global_facts_path": str(global_facts_path)}


def generate_outline_node(state) -> dict:
    root = _root(state)
    print("[6/13] 生成大纲...")
    outline_path = outline_agent(root)
    return {"outline_path": str(outline_path)}


def plan_chapter_jobs_node(state) -> dict:
    root = _root(state)
    print("[7/13] 生成章节任务...")
    jobs = plan_chapter_jobs(root)
    return {"chapter_jobs": jobs, "jobs_dir": str(root / "workspace" / "jobs")}


def select_contexts_node(state) -> dict:
    root = _root(state)
    print("[8/13] 选择章节上下文...")
    errors: list[str] = []
    for job in state.get("chapter_jobs", []):
        try:
            select_context_for_job(job, root)
        except Exception as exc:
            chapter_id = stringify(job.get("chapter_id"))
            message = f"章节 {chapter_id} 上下文选择失败: {exc}"
            print(f"[警告] {message}")
            errors.append(message)
    return {"contexts_dir": str(root / "workspace" / "contexts"), "errors": errors}


def write_chapters_node(state) -> dict:
    root = _root(state)
    workers = int(state.get("workers") or 2)
    print(f"[9/13] 章节 SubAgent 写作... workers={workers}")
    effective_workers = max(1, min(workers, 5))
    try:
        result = concurrent_write_all(root, workers=effective_workers)
    except Exception as exc:
        errors = [f"并发写入异常: {exc}"]
        return {"errors": errors}

    completed = result.get("completed", [])
    failed = result.get("failed", [])
    errors: list[str] = [f"章节 {f['chapter_id']} 写作失败: {f['error']}" for f in failed]
    return {
        "chapters_dir": str(root / "workspace" / "chapters"),
        "completed_chapters": completed,
        "failed_chapters": failed,
        "errors": errors,
    }


def review_chapters_node(state) -> dict:
    root = _root(state)
    print("[10/13] 章节审核...")
    failed: list[dict[str, str]] = []
    errors: list[str] = []
    completed_set = set(state.get("completed_chapters", []))
    for job in state.get("chapter_jobs", []):
        chapter_id = stringify(job.get("chapter_id"))
        if chapter_id not in completed_set:
            continue
        try:
            review_chapter(chapter_id, root)
        except Exception as exc:
            message = f"章节 {chapter_id} 审核失败: {exc}"
            print(f"[警告] {message}")
            failed.append({"chapter_id": chapter_id, "error": str(exc), "stage": "review_chapter"})
            errors.append(message)
    return {"reviews_dir": str(root / "workspace" / "reviews"), "failed_chapters": failed, "errors": errors}


def summarize_chapters_node(state) -> dict:
    root = _root(state)
    print("[11/13] 生成章节摘要...")
    errors: list[str] = []
    completed_set = set(state.get("completed_chapters", []))
    for job in state.get("chapter_jobs", []):
        chapter_id = stringify(job.get("chapter_id"))
        if chapter_id not in completed_set:
            continue
        try:
            summarize_chapter(chapter_id, root)
        except Exception as exc:
            message = f"章节 {chapter_id} 摘要生成失败: {exc}"
            print(f"[警告] {message}")
            errors.append(message)
    return {"summaries_dir": str(root / "workspace" / "summaries"), "errors": errors}


def global_review_node(state) -> dict:
    root = _root(state)
    print("[12/13] 全文一致性审核...")
    global_review_path = global_review_agent(root)
    return {"global_review_path": str(global_review_path)}


def build_markdown_node(state) -> dict:
    root = _root(state)
    print("[13/13] 拼接 Markdown...")
    final_md_path = build_markdown(root)
    return {"final_md_path": str(final_md_path)}


def build_docx_node(state) -> dict:
    root = _root(state)
    print("[13/13] 生成 Word...")
    final_docx_path = build_docx(root)
    return {"final_docx_path": str(final_docx_path)}
