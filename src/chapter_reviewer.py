from __future__ import annotations

from pathlib import Path
from typing import Any

from context_budget import summarize_for_prompt, trim_text
from file_loader import load_global_facts, load_outline, load_score_points
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import (
    compact_json,
    find_chapter,
    listify,
    parse_json_from_model,
    project_root,
    read_json,
    read_nonempty_text,
    select_score_points,
    stringify,
    write_json,
)

REVIEW_CONTEXT_MAX_CHARS = 14000


def _chapter_from_job_or_outline(root: Path, chapter_id: str, outline: dict[str, Any]) -> dict[str, Any]:
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    if job_path.exists():
        job = read_json(job_path)
        return {
            "id": stringify(job.get("chapter_id")) or chapter_id,
            "title": stringify(job.get("chapter_title")),
            "score_point_ids": job.get("score_point_ids", []),
            "description": stringify(job.get("description")),
            "sections": job.get("sections", []),
        }
    return find_chapter(outline, chapter_id)


def normalize_review(data: Any, chapter: dict[str, Any], score_points: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("章节审核结果必须是 JSON 对象。")

    bound_ids = [str(item.get("id")) for item in score_points]
    raw_coverage = data.get("score_coverage") if isinstance(data.get("score_coverage"), list) else []
    coverage_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_coverage:
        if not isinstance(item, dict):
            continue
        score_id = stringify(item.get("score_point_id"))
        if score_id:
            coverage_by_id[score_id] = item

    score_coverage: list[dict[str, Any]] = []
    for score_id in bound_ids:
        item = coverage_by_id.get(score_id, {})
        score_coverage.append(
            {
                "score_point_id": score_id,
                "covered": bool(item.get("covered", False)),
                "coverage_level": stringify(item.get("coverage_level")) or "unknown",
                "evidence": stringify(item.get("evidence")),
                "suggestion": stringify(item.get("suggestion")),
            }
        )

    problems: list[dict[str, str]] = []
    for item in listify(data.get("problems")):
        if not isinstance(item, dict):
            continue
        problems.append(
            {
                "type": stringify(item.get("type")) or "unknown",
                "description": stringify(item.get("description")),
                "suggestion": stringify(item.get("suggestion")),
            }
        )

    need_rewrite = bool(data.get("need_rewrite", False))
    if any(not item["covered"] for item in score_coverage):
        need_rewrite = True
    if any(item["coverage_level"] in {"low", "none", "unknown"} for item in score_coverage):
        need_rewrite = True
    if problems:
        need_rewrite = True

    return {
        "chapter_id": stringify(chapter.get("id")),
        "chapter_title": stringify(chapter.get("title")),
        "score_coverage": score_coverage,
        "problems": problems,
        "need_rewrite": need_rewrite,
    }


def review_chapter_markdown(
    chapter: dict[str, Any],
    related_score_points: list[dict[str, Any]],
    global_facts: dict[str, Any],
    chapter_markdown: str,
    root: Path | None = None,
    debug_name: str | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    prompt = load_agent_prompt(root, "chapter_reviewer")
    chapter_id = stringify(chapter.get("id"))
    chapter_excerpt = trim_text(chapter_markdown, REVIEW_CONTEXT_MAX_CHARS // 2)

    with agent_run(
        root,
        "review_fix_chapters",
        "chapter_reviewer",
        input_summary={
            "chapter_id": chapter_id,
            "score_point_count": len(related_score_points),
            "chapter_chars": len(chapter_markdown),
        },
        chapter_id=chapter_id,
        temperature=0.1,
    ):
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请审核当前章节是否覆盖绑定评分点，并检查空泛、编造和事实冲突问题。\n\n"
                        "## 当前章节信息\n\n"
                        f"{compact_json(chapter)}\n\n"
                        "## 绑定评分点\n\n"
                        f"{compact_json(related_score_points)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}\n\n"
                        "## 上下文摘要\n\n"
                        f"{summarize_for_prompt({'max_context_chars': REVIEW_CONTEXT_MAX_CHARS, 'chapter_excerpt_chars': len(chapter_excerpt)}, 800)}\n\n"
                        "## 章节正文\n\n"
                        f"{chapter_excerpt}"
                    ),
                },
            ],
            temperature=0.1,
        )
    debug_file = debug_name or f"debug_review_{chapter_id}_raw.txt"
    data = parse_json_from_model(raw, root / "workspace" / debug_file)
    return normalize_review(data, chapter, related_score_points)


def review_chapter(chapter_id: str, root: Path | None = None) -> Path:
    root = root or project_root()
    outline = load_outline(root)
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    chapter = _chapter_from_job_or_outline(root, chapter_id, outline)
    related_score_points = select_score_points(score_points, chapter.get("score_point_ids", []))
    chapter_path = root / "workspace" / "chapters" / f"{chapter['id']}.md"
    chapter_markdown = read_nonempty_text(chapter_path, f"章节文件 {chapter_path}")
    review = review_chapter_markdown(chapter, related_score_points, global_facts, chapter_markdown, root)

    output_path = root / "workspace" / "reviews" / f"{chapter['id']}_review.json"
    write_json(output_path, review)
    print(f"[完成] 已审核章节 {chapter['id']} {chapter['title']}: {output_path}")
    return output_path


def review_all(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    output_paths: list[Path] = []
    jobs_dir = root / "workspace" / "jobs"
    job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.exists() else []
    if job_files:
        for job_file in job_files:
            output_paths.append(review_chapter(job_file.stem, root))
        return output_paths

    outline = load_outline(root)
    for chapter in outline.get("chapters", []):
        output_paths.append(review_chapter(stringify(chapter.get("id")), root))
    return output_paths
