from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_outline, load_score_points
from utils import project_root, select_score_points, stringify, write_json


def _build_job(chapter: dict[str, Any], root: Path) -> dict[str, Any]:
    chapter_id = stringify(chapter.get("id"))
    return {
        "job_id": f"chapter_{chapter_id}",
        "chapter_id": chapter_id,
        "chapter_title": stringify(chapter.get("title")),
        "score_point_ids": [stringify(item) for item in chapter.get("score_point_ids", [])],
        "sections": chapter.get("sections", []),
        "description": stringify(chapter.get("description")),
        "output_path": str(root / "workspace" / "chapters" / f"{chapter_id}.md"),
        "review_path": str(root / "workspace" / "reviews" / f"{chapter_id}_review.json"),
        "context_path": str(root / "workspace" / "contexts" / f"{chapter_id}_context.json"),
    }


def plan_chapter_jobs(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or project_root()
    outline = load_outline(root)
    score_points = load_score_points(root)
    score_ids = {str(item.get("id")) for item in score_points}
    jobs_dir = root / "workspace" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[dict[str, Any]] = []
    for chapter in outline.get("chapters", []):
        job = _build_job(chapter, root)
        if not job["score_point_ids"]:
            raise ValueError(f"章节 {job['chapter_id']} 缺少 score_point_ids。")
        invalid_ids = [score_id for score_id in job["score_point_ids"] if score_id not in score_ids]
        if invalid_ids:
            raise ValueError(f"章节 {job['chapter_id']} 绑定了不存在的评分点: {invalid_ids}")
        if not select_score_points(score_points, job["score_point_ids"]):
            raise ValueError(f"章节 {job['chapter_id']} 没有可用评分点。")

        job_path = jobs_dir / f"{job['chapter_id']}.json"
        write_json(job_path, job)
        jobs.append(job)

    print(f"[完成] 已生成 {len(jobs)} 个章节任务: {jobs_dir}")
    return jobs
