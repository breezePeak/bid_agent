from __future__ import annotations

"""Downstream invalidation helpers for partial chapter rewrites (Phase 3)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from utils import project_root


INVALIDATION_MAP: dict[str, tuple[str, ...]] = {
    "write_chapters": (
        "workspace/reviews",
        "workspace/summaries",
        "workspace/source_trace_index.json",
        "workspace/score_coverage_matrix.json",
        "workspace/final_score_estimate.json",
        "workspace/global_review.json",
        "outputs/final.md",
        "outputs/final.docx",
        "workspace/format_check_report.json",
    ),
    "review_fix_chapters": (
        "workspace/score_coverage_matrix.json",
        "workspace/final_score_estimate.json",
        "workspace/global_review.json",
        "outputs/final.md",
        "outputs/final.docx",
    ),
    "parse_score": (
        "workspace/outline.json",
        "workspace/jobs",
        "workspace/score_coverage_matrix.json",
        "workspace/final_score_estimate.json",
    ),
    "generate_outline": (
        "workspace/jobs",
        "workspace/contexts",
        "workspace/chapters",
    ),
    "plan_chapter_jobs": (
        "workspace/contexts",
        "workspace/chapters",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stale_path(root: Path) -> Path:
    return root / "workspace" / "agent" / "stale_artifacts.json"


def load_stale(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    path = stale_path(root)
    if not path.exists():
        return {"updated_at": None, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"updated_at": None, "items": {}}
    if not isinstance(data, dict):
        return {"updated_at": None, "items": {}}
    data.setdefault("items", {})
    return data


def save_stale(root: Path, payload: dict[str, Any]) -> Path:
    path = stale_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def mark_invalidated(
    root: Path | None,
    *,
    reason: str,
    chapter_ids: Iterable[str] | None = None,
    extra_paths: Iterable[str] | None = None,
    source_stage: str = "",
) -> dict[str, Any]:
    root = root or project_root()
    paths: list[str] = list(INVALIDATION_MAP.get(source_stage, ()))
    if extra_paths:
        paths.extend(str(p) for p in extra_paths)
    chapters = [str(c) for c in (chapter_ids or [])]
    if chapters:
        for cid in chapters:
            paths.append(f"workspace/reviews/{cid}_review.json")
            paths.append(f"workspace/summaries/{cid}_summary.json")
        paths.extend(
            [
                "outputs/final.md",
                "outputs/final.docx",
                "workspace/score_coverage_matrix.json",
                "workspace/source_trace_index.json",
            ]
        )

    uniq = sorted(set(paths))
    state = load_stale(root)
    items = state.setdefault("items", {})
    for rel in uniq:
        items[rel] = {
            "reason": reason,
            "source_stage": source_stage,
            "chapter_ids": chapters,
            "marked_at": _now(),
        }
    state["updated_at"] = _now()
    state["last_reason"] = reason
    save_stale(root, state)
    return state


def clear_stale_if_rebuilt(root: Path | None, relative_paths: Iterable[str]) -> None:
    root = root or project_root()
    state = load_stale(root)
    items = state.get("items") or {}
    changed = False
    for rel in relative_paths:
        if rel in items:
            del items[rel]
            changed = True
    if changed:
        state["items"] = items
        state["updated_at"] = _now()
        save_stale(root, state)


def is_stale(root: Path | None, relative_path: str) -> bool:
    state = load_stale(root)
    return relative_path in (state.get("items") or {})


def stale_summary(root: Path | None = None) -> str:
    state = load_stale(root)
    items = state.get("items") or {}
    if not items:
        return "无失效产物记录"
    keys = sorted(items.keys())[:12]
    more = "" if len(items) <= 12 else f" 等共 {len(items)} 项"
    return "失效产物: " + ", ".join(keys) + more
