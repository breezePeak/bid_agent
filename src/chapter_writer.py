from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_score_points
from llm_client import chat
from utils import (
    compact_json,
    load_prompt,
    project_root,
    read_json,
    select_score_points,
    stringify,
    write_text,
)


def _ensure_chapter_heading(content: str, chapter: dict[str, Any]) -> str:
    expected = f"# {chapter['id']} {chapter['title']}"
    stripped = content.strip()
    if not stripped:
        return expected + "\n"
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith("# ") and str(chapter["id"]) in first_line:
        return stripped + "\n"
    return expected + "\n\n" + stripped + "\n"


def _chunks_by_id(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {stringify(chunk.get("id")): chunk for chunk in chunks}


def _load_selected_chunks(root: Path, context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tender_chunks = read_json(root / "workspace" / "chunks" / "tender_chunks.json")
    company_chunks = read_json(root / "workspace" / "chunks" / "company_chunks.json")
    tender_index = _chunks_by_id(tender_chunks if isinstance(tender_chunks, list) else [])
    company_index = _chunks_by_id(company_chunks if isinstance(company_chunks, list) else [])

    selected_tender = []
    for item in context.get("selected_tender_chunks", []):
        chunk_id = stringify(item.get("id")) if isinstance(item, dict) else stringify(item)
        if chunk_id in tender_index:
            selected_tender.append(tender_index[chunk_id])
        else:
            print(f"[警告] 招标文件 chunk id 未找到: {chunk_id}")

    selected_company = []
    for item in context.get("selected_company_chunks", []):
        chunk_id = stringify(item.get("id")) if isinstance(item, dict) else stringify(item)
        if chunk_id in company_index:
            selected_company.append(company_index[chunk_id])
        else:
            print(f"[警告] 公司资料 chunk id 未找到: {chunk_id}")

    return selected_tender, selected_company


def write_chapter_from_job_context(
    job: dict[str, Any],
    context: dict[str, Any],
    root: Path | None = None,
) -> str:
    """Generate one chapter from its task package and selected chunks only."""
    root = root or project_root()
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    selected_tender_chunks, selected_company_chunks = _load_selected_chunks(root, context)
    chapter = {
        "id": stringify(job.get("chapter_id")),
        "title": stringify(job.get("chapter_title")),
        "score_point_ids": job.get("score_point_ids", []),
        "description": stringify(job.get("description")),
        "sections": job.get("sections", []),
    }
    prompt = load_prompt(root, "write_chapter.md")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请只基于当前章节任务包和选中的资料片段生成章节 Markdown。"
                    "不要读取或假设未提供的完整招标文件、完整公司资料。\n\n"
                    "## 当前章节任务包\n\n"
                    f"{compact_json(job)}\n\n"
                    "## 当前章节绑定评分点\n\n"
                    f"{compact_json(related_score_points)}\n\n"
                    "## 全局事实\n\n"
                    f"{compact_json(global_facts)}\n\n"
                    "## 选中的招标文件片段\n\n"
                    f"{compact_json(selected_tender_chunks)}\n\n"
                    "## 选中的公司资料片段\n\n"
                    f"{compact_json(selected_company_chunks)}"
                ),
            },
        ],
        temperature=0.35,
    )
    return _ensure_chapter_heading(raw, chapter)


def write_chapter(chapter_id: str, root: Path | None = None) -> Path:
    """Write a single chapter using its job and context files."""
    root = root or project_root()
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    context_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"

    if not job_path.exists():
        raise FileNotFoundError(
            f"缺少章节任务: {job_path}，请先执行 plan-jobs"
        )
    if not context_path.exists():
        raise FileNotFoundError(
            f"缺少上下文文件: {context_path}，请先执行 select-context --chapter {chapter_id}"
        )

    job = read_json(job_path)
    context = read_json(context_path)
    content = write_chapter_from_job_context(job, context, root)

    output_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    write_text(output_path, content)
    print(f"[完成] 已生成章节 {chapter_id}: {output_path}")
    return output_path


def write_all(root: Path | None = None) -> list[Path]:
    """Serial write all chapters from jobs and contexts."""
    root = root or project_root()
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists() or not list(jobs_dir.glob("*.json")):
        raise FileNotFoundError(
            f"缺少章节任务目录: {jobs_dir}，请先执行 plan-jobs"
        )

    output_paths: list[Path] = []
    job_files = sorted(jobs_dir.glob("*.json"))
    for job_file in job_files:
        chapter_id = job_file.stem
        try:
            output_paths.append(write_chapter(chapter_id, root))
        except Exception as exc:
            print(f"[错误] 章节 {chapter_id} 写入失败: {exc}")
    return output_paths
