from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_outline, load_score_points, load_template_evidence_map
from manual_review import manual_review_context_for_chapter, score_coverage_assignment_overrides
from materials_checklist import items_for_chapter, writing_requirement_lines
from utils import project_root, select_score_points, stringify, write_json


def _evidence_for_chapter(chapter: dict[str, Any], evidence_map: dict[str, Any]) -> list[dict[str, Any]]:
    chapter_id = stringify(chapter.get("id"))
    titles = {stringify(chapter.get("title"))}
    for section in chapter.get("sections", []):
        if isinstance(section, dict):
            titles.add(stringify(section.get("title")))
    items = evidence_map.get("items") if isinstance(evidence_map, dict) else []
    matched: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return matched
    for item in items:
        if not isinstance(item, dict):
            continue
        heading_id = stringify(item.get("heading_id"))
        title = stringify(item.get("title"))
        should_attach = bool(heading_id and (heading_id == chapter_id or heading_id.startswith(chapter_id + ".")))
        if not should_attach and title:
            should_attach = any(title == candidate or title in candidate or candidate in title for candidate in titles if candidate)
        if not should_attach:
            continue
        evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        matched.append(
            {
                "id": stringify(item.get("id")),
                "type": stringify(item.get("type")),
                "heading_id": heading_id,
                "title": title,
                "label": stringify(item.get("label")),
                "semantic_key": stringify(item.get("semantic_key")),
                "status": stringify(item.get("status")),
                "confidence": item.get("confidence"),
                "evidence_sources": item.get("evidence_sources", []),
                "tender_chunk_ids": [
                    stringify(chunk.get("id"))
                    for chunk in evidence.get("tender_chunks", [])
                    if isinstance(chunk, dict)
                ][:5],
                "company_chunk_ids": [
                    stringify(chunk.get("id"))
                    for chunk in evidence.get("company_chunks", [])
                    if isinstance(chunk, dict)
                ][:5],
                "score_point_ids": [
                    stringify(point.get("id"))
                    for point in evidence.get("score_points", [])
                    if isinstance(point, dict)
                ][:5],
                "notes": item.get("notes", []),
            }
        )
    return matched


def _build_job(
    chapter: dict[str, Any],
    root: Path,
    evidence_map: dict[str, Any],
    *,
    material_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    chapter_id = stringify(chapter.get("id"))
    review_context = manual_review_context_for_chapter(root, chapter_id)
    writing_requirements = [stringify(item) for item in chapter.get("writing_requirements", []) if stringify(item)]
    materials_items = items_for_chapter(root, chapter=chapter, material_items=material_items)
    compact_materials = [
        {
            "item_id": stringify(item.get("item_id")),
            "category": stringify(item.get("category")),
            "requirement": stringify(item.get("requirement")),
            "response_status": stringify(item.get("response_status")),
            "evidence_status": stringify(item.get("evidence_status")),
            "reason": stringify(item.get("reason")),
            "suggested_attachment": stringify(item.get("suggested_attachment")),
            "suggested_placeholder_language": stringify(item.get("suggested_placeholder_language")),
            "severity": stringify(item.get("severity")),
        }
        for item in materials_items
        if isinstance(item, dict)
    ]
    for line in writing_requirement_lines(materials_items):
        if line not in writing_requirements:
            writing_requirements.append(line)
    return {
        "job_id": f"chapter_{chapter_id}",
        "chapter_id": chapter_id,
        "chapter_title": stringify(chapter.get("title")),
        "heading_level": int(chapter.get("level", 1) or 1),
        "template_order": int(chapter.get("template_order", 0) or 0),
        "parent_id": stringify(chapter.get("parent_id")),
        "score_point_ids": [stringify(item) for item in chapter.get("score_point_ids", [])],
        "writing_requirements": writing_requirements,
        "sections": chapter.get("sections", []),
        "description": stringify(chapter.get("description")),
        "output_path": str(root / "workspace" / "chapters" / f"{chapter_id}.md"),
        "review_path": str(root / "workspace" / "reviews" / f"{chapter_id}_review.json"),
        "context_path": str(root / "workspace" / "contexts" / f"{chapter_id}_context.json"),
        "template_tasks": _evidence_for_chapter(chapter, evidence_map),
        "manual_review": review_context,
        "materials_checklist_items": compact_materials,
    }


def _apply_score_coverage_overrides(jobs: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    jobs_by_id = {stringify(job.get("chapter_id")): job for job in jobs}
    for score_point_id, override in overrides.items():
        target_chapter_id = stringify(override.get("target_chapter_id"))
        if not target_chapter_id or target_chapter_id not in jobs_by_id:
            continue
        job = jobs_by_id[target_chapter_id]
        if score_point_id not in job["score_point_ids"]:
            job["score_point_ids"].append(score_point_id)
        instruction = stringify(override.get("operator_instruction"))
        if instruction and instruction not in job["writing_requirements"]:
            job["writing_requirements"].append(f"人工复核要求：{instruction}")
    return jobs


def plan_chapter_jobs(
    root: Path | None = None,
    *,
    material_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    root = root or project_root()
    outline = load_outline(root)
    score_points = load_score_points(root)
    evidence_map = load_template_evidence_map(root)
    score_ids = {str(item.get("id")) for item in score_points}
    jobs_dir = root / "workspace" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    for old_job in jobs_dir.glob("*.json"):
        old_job.unlink()

    jobs: list[dict[str, Any]] = []
    for chapter in outline.get("chapters", []):
        job = _build_job(chapter, root, evidence_map, material_items=material_items)
        invalid_ids = [score_id for score_id in job["score_point_ids"] if score_id not in score_ids]
        if invalid_ids:
            raise ValueError(f"章节 {job['chapter_id']} 绑定了不存在的评分点: {invalid_ids}")
        if job["score_point_ids"] and not select_score_points(score_points, job["score_point_ids"]):
            raise ValueError(f"章节 {job['chapter_id']} 没有可用评分点。")
        if not job["score_point_ids"] and not job["template_tasks"] and not job["writing_requirements"]:
            raise ValueError(f"章节 {job['chapter_id']} 缺少评分点、模板任务和写作要求。")

        job_path = jobs_dir / f"{job['chapter_id']}.json"
        jobs.append(job)

    jobs = _apply_score_coverage_overrides(jobs, score_coverage_assignment_overrides(root))
    for job in jobs:
        job_path = jobs_dir / f"{job['chapter_id']}.json"
        write_json(job_path, job)

    print(f"[完成] 已生成 {len(jobs)} 个章节任务: {jobs_dir}")
    return jobs
