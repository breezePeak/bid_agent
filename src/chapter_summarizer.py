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
    parse_json_from_model,
    project_root,
    read_json,
    read_text,
    select_score_points,
    stringify,
    write_json,
)


def _coerce_summary_object(data: Any, chapter_id: str) -> dict[str, Any]:
    if isinstance(data, dict):
        for key in ("summary", "chapter_summary", "data"):
            nested = data.get(key)
            if isinstance(nested, dict):
                return nested
        return data

    if isinstance(data, list):
        dict_items = [item for item in data if isinstance(item, dict)]
        if len(dict_items) == 1:
            return dict_items[0]
        for item in dict_items:
            if str(item.get("chapter_id", "")).strip() == chapter_id:
                return item

    raise ValueError("章节摘要必须是 JSON 对象。")


def _normalize_summary(data: Any, chapter_id: str, chapter_title: str, source_path: str) -> dict[str, Any]:
    data = _coerce_summary_object(data, chapter_id)
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

    prompt = load_agent_prompt(root, "chapter_summarizer")
    chapter_title = stringify(job.get("chapter_title") or chapter_id)
    source_path = str(chapter_path.relative_to(root))

    with agent_run(
        root,
        "summarize_chapters",
        "chapter_summarizer",
        input_summary={"chapter_id": chapter_id, "chapter_chars": len(chapter_md), "score_point_count": len(related_sps)},
        chapter_id=chapter_id,
        temperature=0.1,
    ):
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
                        "## 上下文摘要\n\n"
                        f"{summarize_for_prompt({'chapter_chars': len(chapter_md)}, 400)}\n\n"
                        "## 章节正文\n\n"
                        f"{trim_text(chapter_md, 12000)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    debug_path = root / "workspace" / f"debug_summarize_{chapter_id}_raw.txt"
    data = parse_json_from_model(raw, debug_path)
    try:
        summary = _normalize_summary(data, chapter_id, chapter_title, source_path)
    except Exception:
        write_text(debug_path, raw)
        raise

    summaries_dir = root / "workspace" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    output_path = summaries_dir / f"{chapter_id}_summary.json"
    write_json(output_path, summary)
    print(f"[完成] 章节 {chapter_id} 摘要: {output_path}")
    return output_path


def _existing_summary_path(root: Path, chapter_id: str) -> Path | None:
    output_path = root / "workspace" / "summaries" / f"{chapter_id}_summary.json"
    if not output_path.exists():
        return None
    try:
        data = read_json(output_path)
        _coerce_summary_object(data, chapter_id)
    except Exception:
        return None
    return output_path


def summarize_all_chapters(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    outline = load_outline(root)
    paths: list[Path] = []
    errors: list[str] = []
    for chapter in outline.get("chapters", []):
        chapter_id = str(chapter.get("id"))
        try:
            existing_path = _existing_summary_path(root, chapter_id)
            if existing_path:
                print(f"[跳过] 章节 {chapter_id} 摘要已存在: {existing_path}")
                paths.append(existing_path)
                continue
            paths.append(summarize_chapter(chapter_id, root))
        except Exception as exc:
            errors.append(f"章节 {chapter_id} 摘要生成失败: {exc}")
            print(f"[错误] {errors[-1]}")
    if errors:
        raise RuntimeError("；".join(errors[:5]))
    print(f"[完成] 已生成 {len(paths)} 个章节摘要")
    return paths
