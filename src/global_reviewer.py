from __future__ import annotations

from pathlib import Path
from typing import Any

from context_budget import summarize_for_prompt
from file_loader import load_global_facts, load_outline, load_score_points
from llm_client import chat
from manual_review import filter_global_review_with_actions, manual_review_summary
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import compact_json, parse_json_from_model, project_root, read_json, read_text, write_json


GLOBAL_REVIEW_CONTEXT_MAX_CHARS = 20000


def _short_text(value: Any, max_chars: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _compact_outline_for_review(outline: dict[str, Any]) -> dict[str, Any]:
    chapters = outline.get("chapters") if isinstance(outline.get("chapters"), list) else []
    return {
        "chapter_count": len(chapters),
        "chapters": [
            {
                "id": str(item.get("id") or ""),
                "title": _short_text(item.get("title"), 80),
                "parent_id": str(item.get("parent_id") or ""),
                "score_point_ids": item.get("score_point_ids", []),
            }
            for item in chapters
            if isinstance(item, dict)
        ],
    }


def _compact_reviews_for_review(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    flagged: list[dict[str, Any]] = []
    for item in reviews:
        problems = item.get("problems") if isinstance(item.get("problems"), list) else []
        if not item.get("need_rewrite") and not problems and not item.get("error"):
            continue
        flagged.append(
            {
                "chapter_id": str(item.get("chapter_id") or ""),
                "chapter_title": _short_text(item.get("chapter_title"), 80),
                "need_rewrite": bool(item.get("need_rewrite")),
                "problems": [_short_text(problem, 220) for problem in problems[:8]],
                "error": _short_text(item.get("error"), 220),
            }
        )
    return {
        "review_count": len(reviews),
        "flagged_review_count": len(flagged),
        "flagged_reviews": flagged,
    }


def _compact_score_context(
    score_points: list[dict[str, Any]],
    score_coverage_matrix: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    uncovered = score_coverage_matrix.get("uncovered_score_points", [])
    weak = score_coverage_matrix.get("weak_score_points", [])
    risk_ids = {str(item) for item in [*uncovered, *weak] if str(item)}
    risk_points = [
        {
            "id": str(item.get("id") or ""),
            "title": _short_text(item.get("title"), 100),
            "category": str(item.get("category") or ""),
            "score": item.get("score"),
            "requirement": _short_text(item.get("requirement"), 240),
        }
        for item in score_points
        if isinstance(item, dict) and str(item.get("id") or "") in risk_ids
    ]
    score_context = {
        "score_point_count": len(score_points),
        "risk_score_points": risk_points,
    }
    coverage_context = {
        "summary": score_coverage_matrix.get("summary", {}),
        "uncovered_score_points": uncovered,
        "weak_score_points": weak,
    }
    return score_context, coverage_context


def _compact_summaries_for_review(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    consistency_fields = (
        "project_names",
        "bidder_names",
        "service_periods",
        "warranty_periods",
        "dates",
        "amounts",
    )
    occurrences: dict[str, dict[str, dict[str, Any]]] = {field: {} for field in consistency_fields}
    issues: list[dict[str, Any]] = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("chapter_id") or "")
        for field in consistency_fields:
            values = item.get(field) if isinstance(item.get(field), list) else []
            for raw_value in values:
                value = _short_text(raw_value, 180)
                if not value:
                    continue
                entry = occurrences[field].setdefault(value, {"count": 0, "chapter_ids": []})
                entry["count"] += 1
                if chapter_id and len(entry["chapter_ids"]) < 20:
                    entry["chapter_ids"].append(chapter_id)
        conflicts = item.get("possible_conflicts") if isinstance(item.get("possible_conflicts"), list) else []
        fabrication = item.get("fabrication_risks") if isinstance(item.get("fabrication_risks"), list) else []
        risks = item.get("risks") if isinstance(item.get("risks"), list) else []
        if conflicts or fabrication or risks or item.get("need_manual_review"):
            issues.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": _short_text(item.get("chapter_title"), 80),
                    "possible_conflicts": [_short_text(value, 200) for value in conflicts[:6]],
                    "fabrication_risks": [_short_text(value, 200) for value in fabrication[:6]],
                    "risks": [_short_text(value, 200) for value in risks[:6]],
                    "need_manual_review": bool(item.get("need_manual_review")),
                }
            )
    normalized_occurrences = {
        field: [
            {"value": value, **details}
            for value, details in sorted(values.items(), key=lambda pair: (-pair[1]["count"], pair[0]))
        ]
        for field, values in occurrences.items()
    }
    return {
        "summary_count": len(summaries),
        "consistency_value_occurrences": normalized_occurrences,
        "chapters_with_risks_or_conflicts": issues,
    }


def _build_global_review_user_content(
    *,
    global_facts: dict[str, Any],
    outline: dict[str, Any],
    score_points: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    score_coverage_matrix: dict[str, Any],
    source_trace_index: dict[str, Any],
    review_summary: dict[str, Any],
    summaries: list[dict[str, Any]],
    chapters_data: str,
    chapters_section_label: str,
) -> str:
    score_context, coverage_context = _compact_score_context(score_points, score_coverage_matrix)
    if summaries:
        chapter_context: Any = _compact_summaries_for_review(summaries)
    else:
        chapter_context = chapters_data
    sections = [
        ("全局事实", global_facts, 1200),
        ("大纲（精简）", _compact_outline_for_review(outline), 3600),
        ("风险评分点（仅未覆盖和弱覆盖）", score_context, 3200),
        ("章节审核问题（仅异常章节）", _compact_reviews_for_review(reviews), 2200),
        ("评分点覆盖概况", coverage_context, 1600),
        (
            "来源追溯概况",
            {
                "summary": source_trace_index.get("summary", {}),
                "missing_chapters": source_trace_index.get("missing_chapters", []),
            },
            900,
        ),
        ("人工复核工作台摘要", review_summary, 700),
        (chapters_section_label.strip("# \n") + "（一致性聚合）", chapter_context, 5200),
    ]
    rendered = ["请对当前标书进行全文一致性审核。以下内容已按审核用途压缩。"]
    for title, value, budget in sections:
        rendered.append(f"## {title}\n\n{summarize_for_prompt(value, budget)}")
    content = "\n\n".join(rendered)
    return summarize_for_prompt(content, GLOBAL_REVIEW_CONTEXT_MAX_CHARS)


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


def _load_score_coverage_matrix(root: Path) -> dict[str, Any]:
    matrix_path = root / "workspace" / "score_coverage_matrix.json"
    if not matrix_path.exists():
        return {}
    try:
        data = read_json(matrix_path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_source_trace_index(root: Path) -> dict[str, Any]:
    trace_index_path = root / "workspace" / "source_trace_index.json"
    if not trace_index_path.exists():
        return {}
    try:
        data = read_json(trace_index_path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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
    score_coverage_matrix = _load_score_coverage_matrix(root)
    source_trace_index = _load_source_trace_index(root)
    review_summary = manual_review_summary(root)
    summaries = _load_chapter_summaries(root)
    if summaries:
        chapters_section_label = "## 章节摘要\n\n"
        chapters_data = ""
    else:
        chapters = _load_generated_chapters(root, outline)
        chapters_section_label = "## 章节正文\n\n"
        chapters_data = compact_json(chapters)
        print("[提示] 未找到章节摘要，回退到完整章节正文进行全文审核。")

    with agent_run(
        root,
        "global_review",
        "global_reviewer",
        input_summary={
            "summary_count": len(summaries),
            "review_count": len(reviews),
            "score_point_count": len(score_points),
            "uses_summaries": bool(summaries),
        },
        temperature=0.1,
    ):
        prompt = load_agent_prompt(root, "global_reviewer")
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": _build_global_review_user_content(
                        global_facts=global_facts,
                        outline=outline,
                        score_points=score_points,
                        reviews=reviews,
                        score_coverage_matrix=score_coverage_matrix,
                        source_trace_index=source_trace_index,
                        review_summary=review_summary,
                        summaries=summaries,
                        chapters_data=chapters_data,
                        chapters_section_label=chapters_section_label,
                    ),
                },
            ],
            temperature=0.1,
        )
    data = parse_json_from_model(raw, root / "workspace" / "debug_global_review_raw.txt")
    review = normalize_global_review(data)
    review = filter_global_review_with_actions(root, review)

    from quality_gates import global_review_blocking_reasons

    reasons = global_review_blocking_reasons(review)
    review["blocking"] = bool(reasons)
    review["blocking_reasons"] = reasons
    if reasons and not review.get("need_manual_review"):
        review["need_manual_review"] = True

    output_path = root / "workspace" / "global_review.json"
    write_json(output_path, review)
    print(f"[完成] 已完成全文一致性审核: {output_path}")
    if reasons:
        print("[阻断] 全文审核质量门禁触发，后续阶段将停止：")
        for item in reasons:
            print(f"  - {item}")
        raise RuntimeError(
            "全文审核质量门禁阻断：存在未解决问题，请先查看全文审核结果并处理后再继续。"
            f" 原因: {'；'.join(reasons)}"
        )
    return output_path
