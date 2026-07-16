from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_outline, load_score_points
from utils import project_root, read_json, read_text, stringify, write_json


def _load_jobs(root: Path) -> dict[str, dict[str, Any]]:
    jobs_dir = root / "workspace" / "jobs"
    jobs: dict[str, dict[str, Any]] = {}
    if not jobs_dir.exists():
        return jobs
    for job_file in sorted(jobs_dir.glob("*.json")):
        data = read_json(job_file)
        if isinstance(data, dict):
            jobs[job_file.stem] = data
    return jobs


def _load_reviews(root: Path) -> dict[str, dict[str, Any]]:
    reviews_dir = root / "workspace" / "reviews"
    reviews: dict[str, dict[str, Any]] = {}
    if not reviews_dir.exists():
        return reviews
    for review_file in sorted(reviews_dir.glob("*_review.json")):
        data = read_json(review_file)
        if isinstance(data, dict):
            reviews[review_file.stem.replace("_review", "")] = data
    return reviews


def _chapter_title_map(root: Path) -> dict[str, str]:
    outline = load_outline(root)
    mapping: dict[str, str] = {}
    for chapter in outline.get("chapters", []):
        chapter_id = stringify(chapter.get("id"))
        if chapter_id:
            mapping[chapter_id] = stringify(chapter.get("title"))
    return mapping


def _chapter_preview(root: Path, chapter_id: str, limit: int = 180) -> str:
    chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    if not chapter_path.exists():
        return ""
    text = " ".join(line.strip() for line in read_text(chapter_path).splitlines() if line.strip())
    return text[:limit]


def _job_sections(job: dict[str, Any]) -> list[dict[str, Any]]:
    sections = job.get("sections")
    return sections if isinstance(sections, list) else []


def build_score_coverage_matrix(root: Path | None = None) -> Path:
    root = root or project_root()
    score_points = load_score_points(root)
    jobs = _load_jobs(root)
    reviews = _load_reviews(root)
    chapter_titles = _chapter_title_map(root)

    chapter_bindings: dict[str, list[dict[str, Any]]] = {}
    for chapter_id, job in jobs.items():
        for score_point_id in job.get("score_point_ids", []) if isinstance(job.get("score_point_ids"), list) else []:
            spid = stringify(score_point_id)
            if not spid:
                continue
            chapter_bindings.setdefault(spid, []).append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": stringify(job.get("chapter_title")) or chapter_titles.get(chapter_id, ""),
                    "description": stringify(job.get("description")),
                    "section_ids": [stringify(section.get("id")) for section in _job_sections(job) if stringify(section.get("id"))],
                    "section_titles": [
                        stringify(section.get("title"))
                        for section in _job_sections(job)
                        if stringify(section.get("title"))
                    ],
                }
            )

    review_coverage: dict[str, list[dict[str, Any]]] = {}
    for chapter_id, review in reviews.items():
        items = review.get("score_coverage")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            spid = stringify(item.get("score_point_id"))
            if not spid:
                continue
            review_coverage.setdefault(spid, []).append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": stringify(review.get("chapter_title")) or chapter_titles.get(chapter_id, ""),
                    "covered": bool(item.get("covered", False)),
                    "coverage_level": stringify(item.get("coverage_level")) or "none",
                    "evidence": stringify(item.get("evidence")),
                    "suggestion": stringify(item.get("suggestion")),
                    "review_path": str((root / "workspace" / "reviews" / f"{chapter_id}_review.json").relative_to(root)),
                    "chapter_path": str((root / "workspace" / "chapters" / f"{chapter_id}.md").relative_to(root)),
                    "chapter_preview": _chapter_preview(root, chapter_id),
                }
            )

    matrix_rows: list[dict[str, Any]] = []
    uncovered_score_points: list[str] = []
    weak_score_points: list[str] = []
    fully_covered_score_points: list[str] = []

    for score_point in score_points:
        score_point_id = stringify(score_point.get("id"))
        bindings = chapter_bindings.get(score_point_id, [])
        coverages = review_coverage.get(score_point_id, [])
        coverage_levels = [stringify(item.get("coverage_level")) for item in coverages if stringify(item.get("coverage_level"))]
        covered_any = any(bool(item.get("covered")) for item in coverages)
        high_or_medium = any(level in {"high", "medium"} for level in coverage_levels)

        if not covered_any:
            uncovered_score_points.append(score_point_id)
        elif not high_or_medium:
            weak_score_points.append(score_point_id)
        else:
            fully_covered_score_points.append(score_point_id)

        matrix_rows.append(
            {
                "score_point_id": score_point_id,
                "score_point_title": stringify(score_point.get("title")),
                "category": stringify(score_point.get("category")),
                "score": score_point.get("score"),
                "requirement": stringify(score_point.get("requirement")),
                "keywords": score_point.get("keywords", []) if isinstance(score_point.get("keywords"), list) else [],
                "bound_chapters": bindings,
                "review_coverage": coverages,
                "covered": covered_any,
                "coverage_levels": coverage_levels,
                "risk_level": "high" if not covered_any else "medium" if not high_or_medium else "low",
            }
        )

    matrix = {
        "summary": {
            "score_point_count": len(score_points),
            "bound_score_point_count": len([row for row in matrix_rows if row.get("bound_chapters")]),
            "reviewed_score_point_count": len([row for row in matrix_rows if row.get("review_coverage")]),
            "fully_covered_score_point_count": len(fully_covered_score_points),
            "weak_score_point_count": len(weak_score_points),
            "uncovered_score_point_count": len(uncovered_score_points),
        },
        "uncovered_score_points": uncovered_score_points,
        "weak_score_points": weak_score_points,
        "fully_covered_score_points": fully_covered_score_points,
        "matrix": matrix_rows,
    }

    # 硬指标：关键词命中/要求词重叠，可下调 LLM 乐观覆盖
    try:
        from score_hard_metrics import enrich_matrix_with_hard_metrics

        matrix = enrich_matrix_with_hard_metrics(root, matrix)
        # 用硬指标修正 uncovered/weak 列表（更严）
        hard_uncovered = matrix.get("hard_uncovered_score_points") or []
        hard_weak = matrix.get("hard_weak_score_points") or []
        if hard_uncovered:
            matrix["uncovered_score_points"] = sorted(set(matrix.get("uncovered_score_points") or []) | set(hard_uncovered))
        if hard_weak:
            matrix["weak_score_points"] = sorted(set(matrix.get("weak_score_points") or []) | set(hard_weak))
        # fully_covered 去掉硬指标 none/low 冲突项
        conflict = set(hard_uncovered) | set(hard_weak)
        matrix["fully_covered_score_points"] = [
            spid for spid in (matrix.get("fully_covered_score_points") or []) if spid not in conflict
        ]
        summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
        summary["fully_covered_score_point_count"] = len(matrix.get("fully_covered_score_points") or [])
        summary["weak_score_point_count"] = len(matrix.get("weak_score_points") or [])
        summary["uncovered_score_point_count"] = len(matrix.get("uncovered_score_points") or [])
        matrix["summary"] = summary
    except Exception as exc:
        matrix["hard_metrics_error"] = str(exc)

    output_path = root / "workspace" / "score_coverage_matrix.json"
    write_json(output_path, matrix)
    print(f"[完成] 已生成评分点覆盖矩阵: {output_path}")
    return output_path
