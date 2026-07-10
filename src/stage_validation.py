from __future__ import annotations

import json
from pathlib import Path
from typing import Any


COLLECTION_STAGE_IDS = {
    "plan_chapter_jobs",
    "select_contexts",
    "write_chapters",
    "review_fix_chapters",
    "summarize_chapters",
}


def _json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _valid_file_ids(directory: Path, pattern: str, *, suffix: str = "", id_field: str = "") -> set[str]:
    result: set[str] = set()
    if not directory.exists():
        return result
    for path in directory.glob(pattern):
        item_id = path.stem
        if suffix and item_id.endswith(suffix):
            item_id = item_id[: -len(suffix)]
        if path.suffix.lower() == ".json":
            payload = _json_object(path)
            if payload is None:
                continue
            if id_field and str(payload.get(id_field, "")).strip() not in {"", item_id}:
                continue
        elif not path.is_file() or path.stat().st_size <= 0:
            continue
        result.add(item_id)
    return result


def outline_chapter_ids(root: Path) -> set[str]:
    payload = _json_object(root / "workspace" / "outline.json") or {}
    chapters = payload.get("chapters", [])
    if not isinstance(chapters, list):
        return set()
    return {
        str(chapter.get("id", "")).strip()
        for chapter in chapters
        if isinstance(chapter, dict) and str(chapter.get("id", "")).strip()
    }


def job_ids(root: Path) -> set[str]:
    return _valid_file_ids(root / "workspace" / "jobs", "*.json", id_field="chapter_id")


def context_ids(root: Path) -> set[str]:
    return _valid_file_ids(
        root / "workspace" / "contexts",
        "*_context.json",
        suffix="_context",
        id_field="chapter_id",
    )


def chapter_ids(root: Path) -> set[str]:
    return _valid_file_ids(root / "workspace" / "chapters", "*.md")


def review_ids(root: Path) -> set[str]:
    return _valid_file_ids(
        root / "workspace" / "reviews",
        "*_review.json",
        suffix="_review",
        id_field="chapter_id",
    )


def summary_ids(root: Path) -> set[str]:
    return _valid_file_ids(
        root / "workspace" / "summaries",
        "*_summary.json",
        suffix="_summary",
        id_field="chapter_id",
    )


def stage_collection_status(root: Path, stage_id: str) -> dict[str, Any]:
    if stage_id == "plan_chapter_jobs":
        expected, actual = outline_chapter_ids(root), job_ids(root)
    elif stage_id == "select_contexts":
        expected, actual = job_ids(root), context_ids(root)
    elif stage_id == "write_chapters":
        expected, actual = job_ids(root), chapter_ids(root)
    elif stage_id == "review_fix_chapters":
        expected, actual = chapter_ids(root), review_ids(root)
    elif stage_id == "summarize_chapters":
        expected, actual = chapter_ids(root), summary_ids(root)
    else:
        raise KeyError(f"阶段不是集合型阶段: {stage_id}")

    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return {
        "stage": stage_id,
        "complete": bool(expected) and not missing,
        "expected_count": len(expected),
        "completed_count": len(expected & actual),
        "missing_ids": missing,
        "unexpected_ids": unexpected,
    }


def missing_ids_for_stage(root: Path, stage_id: str) -> list[str]:
    return list(stage_collection_status(root, stage_id)["missing_ids"])
