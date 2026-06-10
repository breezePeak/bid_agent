from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_outline, load_score_points
from llm_client import chat
from utils import compact_json, load_prompt, parse_json_from_model, project_root, read_json, read_text, write_json


def _load_chapter_summaries(root: Path) -> list[dict[str, Any]]:
    summaries_dir = root / "workspace" / "summaries"
    summaries: list[dict[str, Any]] = []
    if summaries_dir.exists():
        for summary_path in sorted(summaries_dir.glob("*_summary.json")):
            try:
                summaries.append(read_json(summary_path))
            except Exception:
                pass
    return summaries


def _load_generated_chapters(root: Path, outline: dict[str, Any]) -> list[dict[str, str]]:
    chapters: list[dict[str, str]] = []
    for chapter in outline.get("chapters", []):
        chapter_id = str(chapter.get("id"))
        chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
        chapters.append(
            {
                "chapter_id": chapter_id,
                "chapter_title": str(chapter.get("title", "")),
                "path": str(chapter_path),
                "content": read_text(chapter_path) if chapter_path.exists() else "",
            }
        )
    return chapters


def _load_reviews(root: Path) -> list[dict[str, Any]]:
    reviews_dir = root / "workspace" / "reviews"
    if not reviews_dir.exists():
        return []

    reviews: list[dict[str, Any]] = []
    for review_path in sorted(reviews_dir.glob("*_review.json")):
        try:
            data = read_json(review_path)
            if isinstance(data, dict):
                data["path"] = str(review_path.relative_to(root))
                reviews.append(data)
            else:
                reviews.append(
                    {
                        "path": str(review_path.relative_to(root)),
                        "error": "review json 不是对象",
                    }
                )
        except Exception as exc:
            reviews.append({"path": str(review_path.relative_to(root)), "error": str(exc)})
    return reviews


def normalize_global_review(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("全文一致性审核结果必须是 JSON 对象。")
    return {
        "project_name_consistent": bool(data.get("project_name_consistent", False)),
        "bidder_name_consistent": bool(data.get("bidder_name_consistent", False)),
        "service_period_consistent": bool(data.get("service_period_consistent", False)),
        "warranty_period_consistent": bool(data.get("warranty_period_consistent", False)),
        "chapter_conflicts": data.get("chapter_conflicts", []) if isinstance(data.get("chapter_conflicts"), list) else [],
        "uncovered_score_points": data.get("uncovered_score_points", [])
        if isinstance(data.get("uncovered_score_points"), list)
        else [],
        "missing_chapters": data.get("missing_chapters", []) if isinstance(data.get("missing_chapters"), list) else [],
        "fabrication_risks": data.get("fabrication_risks", [])
        if isinstance(data.get("fabrication_risks"), list)
        else [],
        "suggestions": data.get("suggestions", []) if isinstance(data.get("suggestions"), list) else [],
        "need_manual_review": bool(data.get("need_manual_review", False)),
    }


def run_global_review(root: Path | None = None) -> Path:
    root = root or project_root()
    global_facts = load_global_facts(root)
    outline = load_outline(root)
    score_points = load_score_points(root)
    reviews = _load_reviews(root)
    prompt = load_prompt(root, "global_review.md")

    summaries = _load_chapter_summaries(root)
    if summaries:
        chapters_section_label = "## 章节摘要\n\n"
        chapters_data = compact_json(summaries)
    else:
        chapters = _load_generated_chapters(root, outline)
        chapters_section_label = "## 章节正文\n\n"
        chapters_data = compact_json(chapters)
        print("[提示] 未找到章节摘要，回退到完整章节正文进行全文审核。")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请对当前标书进行全文一致性审核。\n\n"
                    "## 全局事实\n\n"
                    f"{compact_json(global_facts)}\n\n"
                    "## 大纲\n\n"
                    f"{compact_json(outline)}\n\n"
                    "## 评分点\n\n"
                    f"{compact_json(score_points)}\n\n"
                    "## 章节审核结果\n\n"
                    f"{compact_json(reviews)}\n\n"
                    f"{chapters_section_label}"
                    f"{chapters_data}"
                ),
            },
        ],
        temperature=0.1,
    )
    data = parse_json_from_model(raw, root / "workspace" / "debug_global_review_raw.txt")
    review = normalize_global_review(data)

    output_path = root / "workspace" / "global_review.json"
    write_json(output_path, review)
    print(f"[完成] 已完成全文一致性审核: {output_path}")
    return output_path
