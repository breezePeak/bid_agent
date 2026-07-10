from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_template_evidence_map
from project_profile_registry import load_project_profile
from utils import project_root, read_json, stringify, write_json


MANUAL_REVIEW_FILES = {
    "summary": "summary.json",
    "template_evidence": "template_evidence_overrides.json",
    "score_coverage": "score_coverage_overrides.json",
    "chapter_review": "chapter_actions.json",
    "global_review": "global_review_actions.json",
    "replay_requests": "replay_requests.json",
}

DEFAULT_STATUS = {
    "template_evidence": "pending",
    "score_coverage": "pending",
    "chapter_review": "pending",
    "global_review": "pending",
}

REPLAY_STAGE_BY_CATEGORY = {
    "template_evidence": "select_contexts",
    "score_coverage": "plan_chapter_jobs",
    "chapter_review": "write_chapters",
    "global_review": "global_review",
}


def manual_review_dir(root: Path) -> Path:
    path = root / "workspace" / "manual_review"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manual_path(root: Path, category: str) -> Path:
    filename = MANUAL_REVIEW_FILES[category]
    return manual_review_dir(root) / filename


def _read_json_or_default(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = read_json(path)
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _load_indexed_overrides(root: Path, category: str) -> dict[str, Any]:
    data = _read_json_or_default(_manual_path(root, category), {"items": {}, "updated_at": ""})
    items = data.get("items")
    if not isinstance(items, dict):
        items = {}
    return {"items": items, "updated_at": str(data.get("updated_at", ""))}


def _save_indexed_overrides(root: Path, category: str, items: dict[str, Any]) -> Path:
    path = _manual_path(root, category)
    write_json(
        path,
        {
            "items": items,
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    return path


def _load_list_overrides(root: Path, category: str) -> list[dict[str, Any]]:
    data = _read_json_or_default(_manual_path(root, category), {"items": [], "updated_at": ""})
    items = data.get("items")
    return items if isinstance(items, list) else []


def _save_list_overrides(root: Path, category: str, items: list[dict[str, Any]]) -> Path:
    path = _manual_path(root, category)
    write_json(
        path,
        {
            "items": items,
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    return path


def recommended_replay_stage(category: str, payload: dict[str, Any]) -> str:
    if category == "score_coverage" and stringify(payload.get("target_chapter_id")):
        return "plan_chapter_jobs"
    if category == "template_evidence" and (payload.get("preferred_tender_chunk_ids") or payload.get("preferred_company_chunk_ids")):
        return "select_contexts"
    return REPLAY_STAGE_BY_CATEGORY.get(category, "global_review")


def _record_replay_request(root: Path, category: str, item_id: str, stage: str) -> None:
    items = _load_list_overrides(root, "replay_requests")
    items.append(
        {
            "category": category,
            "item_id": item_id,
            "recommended_stage": stage,
            "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    _save_list_overrides(root, "replay_requests", items[-100:])


def apply_manual_review_update(root: Path | None, category: str, payload: dict[str, Any]) -> dict[str, Any]:
    root = root or project_root()
    item_id = stringify(payload.get("item_id"))
    if not item_id:
        raise ValueError("缺少 item_id。")
    status = stringify(payload.get("status")) or DEFAULT_STATUS.get(category, "pending")
    note = stringify(payload.get("operator_note"))
    operator_instruction = stringify(payload.get("operator_instruction"))
    target_chapter_id = stringify(payload.get("target_chapter_id"))
    replacement_notes = stringify(payload.get("replacement_notes"))
    suggested_evidence_sources = payload.get("suggested_evidence_sources", [])
    preferred_tender_chunk_ids = payload.get("preferred_tender_chunk_ids", [])
    preferred_company_chunk_ids = payload.get("preferred_company_chunk_ids", [])

    indexed = _load_indexed_overrides(root, category)
    items = indexed["items"]
    items[item_id] = {
        "item_id": item_id,
        "status": status,
        "operator_note": note,
        "operator_instruction": operator_instruction,
        "target_chapter_id": target_chapter_id,
        "replacement_notes": replacement_notes,
        "suggested_evidence_sources": suggested_evidence_sources if isinstance(suggested_evidence_sources, list) else [],
        "preferred_tender_chunk_ids": preferred_tender_chunk_ids if isinstance(preferred_tender_chunk_ids, list) else [],
        "preferred_company_chunk_ids": preferred_company_chunk_ids if isinstance(preferred_company_chunk_ids, list) else [],
        "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_indexed_overrides(root, category, items)
    stage = recommended_replay_stage(category, payload)
    _record_replay_request(root, category, item_id, stage)
    return {"item_id": item_id, "recommended_stage": stage, "status": status}


def manual_review_context_for_chapter(root: Path | None, chapter_id: str) -> dict[str, Any]:
    root = root or project_root()
    chapter_key = stringify(chapter_id)
    chapter_actions = _load_indexed_overrides(root, "chapter_review")["items"]
    template_overrides = _load_indexed_overrides(root, "template_evidence")["items"]
    evidence_map = load_template_evidence_map(root)
    items = evidence_map.get("items") if isinstance(evidence_map.get("items"), list) else []

    operator_instructions: list[str] = []
    preferred_tender_chunk_ids: list[str] = []
    preferred_company_chunk_ids: list[str] = []

    for action in chapter_actions.values():
        if not isinstance(action, dict):
            continue
        if stringify(action.get("chapter_id")) != chapter_key:
            continue
        text = stringify(action.get("operator_instruction"))
        if text and text not in operator_instructions:
            operator_instructions.append(text)
        for chunk_id in action.get("preferred_tender_chunk_ids", []):
            chunk_id = stringify(chunk_id)
            if chunk_id and chunk_id not in preferred_tender_chunk_ids:
                preferred_tender_chunk_ids.append(chunk_id)
        for chunk_id in action.get("preferred_company_chunk_ids", []):
            chunk_id = stringify(chunk_id)
            if chunk_id and chunk_id not in preferred_company_chunk_ids:
                preferred_company_chunk_ids.append(chunk_id)

    for item in items:
        if not isinstance(item, dict):
            continue
        heading_id = stringify(item.get("heading_id"))
        if not heading_id or not (heading_id == chapter_key or heading_id.startswith(chapter_key + ".")):
            continue
        override = template_overrides.get(stringify(item.get("id")))
        if not isinstance(override, dict):
            continue
        for chunk_id in override.get("preferred_tender_chunk_ids", []):
            chunk_id = stringify(chunk_id)
            if chunk_id and chunk_id not in preferred_tender_chunk_ids:
                preferred_tender_chunk_ids.append(chunk_id)
        for chunk_id in override.get("preferred_company_chunk_ids", []):
            chunk_id = stringify(chunk_id)
            if chunk_id and chunk_id not in preferred_company_chunk_ids:
                preferred_company_chunk_ids.append(chunk_id)
        replacement = stringify(override.get("replacement_notes"))
        if replacement and replacement not in operator_instructions:
            operator_instructions.append(f"模板任务人工说明：{replacement}")

    return {
        "operator_instructions": operator_instructions,
        "preferred_tender_chunk_ids": preferred_tender_chunk_ids,
        "preferred_company_chunk_ids": preferred_company_chunk_ids,
    }


def score_coverage_assignment_overrides(root: Path | None) -> dict[str, dict[str, Any]]:
    root = root or project_root()
    items = _load_indexed_overrides(root, "score_coverage")["items"]
    return {
        key: value
        for key, value in items.items()
        if isinstance(value, dict) and stringify(value.get("status")) in {"assigned", "resolved"} and stringify(value.get("target_chapter_id"))
    }


def filter_global_review_with_actions(root: Path | None, review: dict[str, Any]) -> dict[str, Any]:
    root = root or project_root()
    if not isinstance(review, dict):
        return review
    actions = _load_indexed_overrides(root, "global_review")["items"]
    if not actions:
        return review

    filtered = dict(review)
    for key in ("chapter_conflicts", "uncovered_score_points", "fabrication_risks", "suggestions"):
        items = filtered.get(key)
        if not isinstance(items, list):
            continue
        kept: list[Any] = []
        for item in items:
            text = stringify(item)
            matched = False
            for action in actions.values():
                if not isinstance(action, dict):
                    continue
                status = stringify(action.get("status"))
                if status not in {"accepted", "resolved"}:
                    continue
                risk_type = stringify(action.get("risk_type"))
                target_scope = stringify(action.get("target_scope"))
                if risk_type and risk_type != key:
                    continue
                if target_scope and target_scope in text:
                    matched = True
                    break
            if not matched:
                kept.append(item)
        filtered[key] = kept
    filtered["need_manual_review"] = bool(filtered.get("chapter_conflicts") or filtered.get("uncovered_score_points") or filtered.get("fabrication_risks"))
    return filtered


def _template_evidence_items(root: Path) -> list[dict[str, Any]]:
    evidence_map = load_template_evidence_map(root)
    items = evidence_map.get("items")
    return items if isinstance(items, list) else []


def manual_review_items(root: Path | None, category: str) -> list[dict[str, Any]]:
    root = root or project_root()
    overrides = _load_indexed_overrides(root, category)["items"]
    if category == "template_evidence":
        rows: list[dict[str, Any]] = []
        for item in _template_evidence_items(root):
            if not isinstance(item, dict):
                continue
            status = stringify(item.get("status"))
            if status not in {"weak", "missing"}:
                continue
            item_id = stringify(item.get("id"))
            override = overrides.get(item_id, {})
            evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
            rows.append(
                {
                    "item_id": item_id,
                    "category": category,
                    "heading_id": stringify(item.get("heading_id")),
                    "title": stringify(item.get("title")) or stringify(item.get("label")),
                    "status": status,
                    "analysis": item.get("analysis", {}),
                    "notes": item.get("notes", []),
                    "evidence_preview": {
                        "tender_chunks": [stringify(chunk.get("id")) for chunk in evidence.get("tender_chunks", []) if isinstance(chunk, dict)][:5],
                        "company_chunks": [stringify(chunk.get("id")) for chunk in evidence.get("company_chunks", []) if isinstance(chunk, dict)][:5],
                        "score_points": [stringify(point.get("id")) for point in evidence.get("score_points", []) if isinstance(point, dict)][:5],
                    },
                    "override": override,
                }
            )
        return rows
    if category == "score_coverage":
        matrix_path = root / "workspace" / "score_coverage_matrix.json"
        data = _read_json_or_default(matrix_path, {})
        matrix = data.get("matrix") if isinstance(data.get("matrix"), list) else []
        rows = []
        for item in matrix:
            if not isinstance(item, dict):
                continue
            if stringify(item.get("risk_level")) not in {"high", "medium"}:
                continue
            item_id = stringify(item.get("score_point_id"))
            rows.append(
                {
                    "item_id": item_id,
                    "category": category,
                    "score_point_id": item_id,
                    "title": stringify(item.get("score_point_title")),
                    "risk_level": stringify(item.get("risk_level")),
                    "bound_chapters": item.get("bound_chapters", []),
                    "review_coverage": item.get("review_coverage", []),
                    "override": overrides.get(item_id, {}),
                }
            )
        return rows
    if category == "chapter_review":
        reviews_dir = root / "workspace" / "reviews"
        rows = []
        for path in sorted(reviews_dir.glob("*_review.json")) if reviews_dir.exists() else []:
            review = _read_json_or_default(path, {})
            if not isinstance(review, dict):
                continue
            chapter_id = stringify(review.get("chapter_id")) or path.stem.replace("_review", "")
            for index, problem in enumerate(review.get("problems", []) if isinstance(review.get("problems"), list) else [], start=1):
                if not isinstance(problem, dict):
                    continue
                item_id = f"{chapter_id}:P{index:02d}"
                rows.append(
                    {
                        "item_id": item_id,
                        "category": category,
                        "chapter_id": chapter_id,
                        "chapter_title": stringify(review.get("chapter_title")),
                        "problem_type": stringify(problem.get("type")),
                        "description": stringify(problem.get("description")),
                        "suggestion": stringify(problem.get("suggestion")),
                        "override": overrides.get(item_id, {}),
                    }
                )
        return rows
    if category == "global_review":
        review = _read_json_or_default(root / "workspace" / "global_review.json", {})
        rows = []
        if not isinstance(review, dict):
            return rows
        for key in ("chapter_conflicts", "uncovered_score_points", "fabrication_risks", "suggestions"):
            values = review.get(key) if isinstance(review.get(key), list) else []
            for index, value in enumerate(values, start=1):
                text = stringify(value)
                item_id = f"{key}:{index:02d}"
                rows.append(
                    {
                        "item_id": item_id,
                        "category": category,
                        "risk_type": key,
                        "target_scope": text,
                        "description": text,
                        "override": overrides.get(item_id, {}),
                    }
                )
        return rows
    return []


def manual_review_summary(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    template_items = manual_review_items(root, "template_evidence")
    score_items = manual_review_items(root, "score_coverage")
    chapter_items = manual_review_items(root, "chapter_review")
    global_items = manual_review_items(root, "global_review")
    replay_requests = _load_list_overrides(root, "replay_requests")
    project_profile = load_project_profile(root)
    summary = {
        "project_type": project_profile.get("project_type", "general"),
        "template_evidence_pending": len(template_items),
        "score_coverage_pending": len(score_items),
        "chapter_review_pending": len(chapter_items),
        "global_review_pending": len(global_items),
        "total_pending": len(template_items) + len(score_items) + len(chapter_items) + len(global_items),
        "latest_replay_requests": replay_requests[-8:],
    }
    write_json(_manual_path(root, "summary"), summary)
    return summary

