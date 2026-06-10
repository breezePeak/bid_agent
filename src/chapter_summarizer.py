from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_outline, load_score_points
from llm_client import chat
from utils import (
    compact_json,
    load_prompt,
    parse_json_from_model,
    project_root,
    read_json,
    read_text,
    select_score_points,
    stringify,
    write_json,
)


def _normalize_summary(data: Any, chapter_id: str, chapter_title: str, source_path: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("章节摘要必须是 JSON 对象。")

    return {
        "chapter_id": stringify(data.get("chapter_id") or chapter_id),
        "chapter_title": stringify(data.get("chapter_title") or chapter_title),
        "source_chapter_path": stringify(data.get("source_chapter_path") or source_path),
        "covered_score_points": data.get("covered_score_points", [])
        if isinstance(data.get("covered_score_points"), list)
        else [],
        "main_claims": data.get("main_claims", [])
        if isinstance(data.get("main_claims"), list)
        else [],
        "key_solutions": data.get("key_solutions", [])
        if isinstance(data.get("key_solutions"), list)
        else [],
        "project_names": data.get("project_names", [])
        if isinstance(data.get("project_names"), list)
        else [],
        "bidder_names": data.get("bidder_names", [])
        if isinstance(data.get("bidder_names"), list)
        else [],
        "service_periods": data.get("service_periods", [])
        if isinstance(data.get("service_periods"), list)
        else [],
        "warranty_periods": data.get("warranty_periods", [])
        if isinstance(data.get("warranty_periods"), list)
        else [],
        "dates": data.get("dates", []) if isinstance(data.get("dates"), list) else [],
        "amounts": data.get("amounts", []) if isinstance(data.get("amounts"), list) else [],
        "personnel": data.get("personnel", [])
        if isinstance(data.get("personnel"), list)
        else [],
        "qualifications": data.get("qualifications", [])
        if isinstance(data.get("qualifications"), list)
        else [],
        "case_references": data.get("case_references", [])
        if isinstance(data.get("case_references"), list)
        else [],
        "risks": data.get("risks", []) if isinstance(data.get("risks"), list) else [],
        "possible_conflicts": data.get("possible_conflicts", [])
        if isinstance(data.get("possible_conflicts"), list)
        else [],
        "fabrication_risks": data.get("fabrication_risks", [])
        if isinstance(data.get("fabrication_risks"), list)
        else [],
        "need_manual_review": bool(data.get("need_manual_review", False)),
    }


def summarize_chapter(chapter_id: str, root: Path | None = None) -> Path:
    root = root or project_root()

    chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    if not chapter_path.exists():
        raise FileNotFoundError(f"章节文件不存在: {chapter_path}")

    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    if not job_path.exists():
        raise FileNotFoundError(f"章节任务不存在: {job_path}")

    chapter_md = read_text(chapter_path)
    job = read_json(job_path)
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    related_sps = select_score_points(score_points, job.get("score_point_ids", []))

    review = None
    review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
    if review_path.exists():
        try:
            review = read_json(review_path)
        except Exception:
            pass

    prompt = load_prompt(root, "summarize_chapter.md")
    chapter_title = stringify(job.get("chapter_title") or chapter_id)
    source_path = str(chapter_path.relative_to(root))

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"请为章节 {chapter_id} 生成结构化摘要。\n\n"
                    "## 章节任务\n\n"
                    f"{compact_json(job)}\n\n"
                    "## 绑定评分点\n\n"
                    f"{compact_json(related_sps)}\n\n"
                    "## 全局事实\n\n"
                    f"{compact_json(global_facts)}\n\n"
                    "## 章节审核结果\n\n"
                    f"{compact_json(review)}\n\n"
                    "## 章节正文\n\n"
                    f"{chapter_md}"
                ),
            },
        ],
        temperature=0.1,
    )
    data = parse_json_from_model(raw, root / "workspace" / f"debug_summarize_{chapter_id}_raw.txt")
    summary = _normalize_summary(data, chapter_id, chapter_title, source_path)

    summaries_dir = root / "workspace" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    output_path = summaries_dir / f"{chapter_id}_summary.json"
    write_json(output_path, summary)
    print(f"[完成] 章节 {chapter_id} 摘要: {output_path}")
    return output_path


def summarize_all_chapters(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    outline = load_outline(root)
    paths: list[Path] = []
    for chapter in outline.get("chapters", []):
        chapter_id = str(chapter.get("id"))
        try:
            paths.append(summarize_chapter(chapter_id, root))
        except Exception as exc:
            print(f"[警告] 章节 {chapter_id} 摘要生成失败: {exc}")
    print(f"[完成] 已生成 {len(paths)} 个章节摘要")
    return paths
