from __future__ import annotations

"""Goal state machine for supervised agent runs (Phase 3)."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.invalidation import is_stale, load_stale
from pipeline_registry import artifact_exists, stage_outputs_ready, stage_spec_by_id
from utils import project_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def goal_path(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "workspace" / "agent" / "goal_state.json"


def new_goal_id() -> str:
    return uuid4().hex[:12]


def load_goal(root: Path | None = None) -> dict[str, Any] | None:
    path = goal_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def save_goal(root: Path | None, goal: dict[str, Any]) -> Path:
    root = root or project_root()
    path = goal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    goal = dict(goal)
    goal["updated_at"] = _now()
    path.write_text(json.dumps(goal, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _criterion_artifact_exists(root: Path, path: str) -> dict[str, Any]:
    from pipeline_registry import RunArtifact

    art = RunArtifact(path=path, kind="file", required_nonempty=True)
    ok = artifact_exists(root, art) and not is_stale(root, path)
    return {
        "check": "artifact_exists",
        "path": path,
        "ok": ok,
        "detail": "missing_or_stale" if not ok else "ready",
    }


def _criterion_stage_ready(root: Path, stage_id: str) -> dict[str, Any]:
    try:
        ok = stage_outputs_ready(root, stage_id)
    except Exception as exc:  # noqa: BLE001
        return {"check": "stage_ready", "stage_id": stage_id, "ok": False, "detail": str(exc)}
    # if any produce path is stale, not ready for goal success
    stage = stage_spec_by_id(stage_id)
    stale_hits = [a.path for a in stage.produces if a.kind != "virtual" and is_stale(root, a.path)]
    if stale_hits:
        return {
            "check": "stage_ready",
            "stage_id": stage_id,
            "ok": False,
            "detail": f"stale:{','.join(stale_hits[:5])}",
        }
    return {"check": "stage_ready", "stage_id": stage_id, "ok": bool(ok), "detail": "ready" if ok else "incomplete"}


def _criterion_no_stale(root: Path, paths: list[str] | None = None) -> dict[str, Any]:
    state = load_stale(root)
    items = state.get("items") or {}
    if paths:
        hits = [p for p in paths if p in items]
    else:
        hits = sorted(items.keys())
    return {
        "check": "no_stale",
        "ok": len(hits) == 0,
        "detail": "clean" if not hits else f"stale:{','.join(hits[:8])}",
        "stale_paths": hits,
    }



def _criterion_score_coverage_min(root: Path, min_ratio: float = 0.95) -> dict[str, Any]:
    path = root / "workspace" / "score_coverage_matrix.json"
    if not path.exists():
        return {
            "check": "score_coverage_min",
            "ok": False,
            "detail": "missing_matrix",
            "ratio": None,
            "min_ratio": min_ratio,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"check": "score_coverage_min", "ok": False, "detail": "invalid_matrix", "min_ratio": min_ratio}
    summary = data.get("summary") if isinstance(data, dict) else {}
    total = int((summary or {}).get("score_point_count") or 0)
    fully = int((summary or {}).get("fully_covered_score_point_count") or 0)
    uncovered = data.get("uncovered_score_points") if isinstance(data, dict) else []
    if total <= 0:
        # fallback compute
        matrix = data.get("matrix") if isinstance(data, dict) else []
        total = len(matrix) if isinstance(matrix, list) else 0
        fully = total - len(uncovered or [])
    ratio = (fully / total) if total else 0.0
    # also fail if any uncovered remain when min_ratio >= 1
    ok = ratio >= float(min_ratio) and (not uncovered if float(min_ratio) >= 0.999 else True)
    if uncovered and float(min_ratio) < 0.999:
        # soft: ratio based only
        ok = ratio >= float(min_ratio)
    return {
        "check": "score_coverage_min",
        "ok": bool(ok),
        "detail": f"ratio={ratio:.3f} fully={fully}/{total} uncovered={len(uncovered or [])}",
        "ratio": ratio,
        "min_ratio": min_ratio,
    }


def evaluate_criteria(root: Path, criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in criteria:
        check = str(item.get("check") or "")
        if check == "artifact_exists":
            results.append(_criterion_artifact_exists(root, str(item.get("path") or "")))
        elif check == "stage_ready":
            results.append(_criterion_stage_ready(root, str(item.get("stage_id") or "")))
        elif check == "no_stale":
            paths = item.get("paths") if isinstance(item.get("paths"), list) else None
            results.append(_criterion_no_stale(root, paths))
        elif check == "score_coverage_min":
            min_ratio = float(item.get("ratio") or item.get("min_ratio") or 0.95)
            results.append(_criterion_score_coverage_min(root, min_ratio=min_ratio))
        elif check == "chapters_written":
            chapter_ids = item.get("chapter_ids") if isinstance(item.get("chapter_ids"), list) else []
            missing = []
            for cid in chapter_ids:
                p = root / "workspace" / "chapters" / f"{cid}.md"
                if not p.exists() or p.stat().st_size == 0:
                    missing.append(str(cid))
            results.append(
                {
                    "check": "chapters_written",
                    "ok": not missing,
                    "detail": "ready" if not missing else f"missing:{','.join(missing)}",
                    "chapter_ids": chapter_ids,
                }
            )
        else:
            results.append({"check": check or "unknown", "ok": False, "detail": "unsupported_check"})
    return results


def reevaluate_goal(root: Path | None, goal: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or project_root()
    goal = goal or load_goal(root)
    if not goal:
        raise FileNotFoundError("no active goal")
    criteria = goal.get("success_criteria") if isinstance(goal.get("success_criteria"), list) else []
    results = evaluate_criteria(root, criteria)
    all_ok = all(bool(r.get("ok")) for r in results) if results else False
    status = str(goal.get("status") or "pending")
    if status in {"cancelled", "failed"}:
        pass
    elif all_ok:
        status = "succeeded"
    elif status == "blocked_human":
        pass
    else:
        status = "in_progress"
    goal["status"] = status
    goal["criteria_results"] = results
    goal["all_criteria_ok"] = all_ok
    save_goal(root, goal)
    return goal


def create_goal(
    root: Path | None,
    *,
    raw_user_goal: str,
    objectives: list[dict[str, Any]] | None = None,
    success_criteria: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    goal = {
        "goal_id": new_goal_id(),
        "raw_user_goal": raw_user_goal,
        "normalized_objectives": objectives or [],
        "constraints": constraints
        or {
            "allow_skip_compliance": False,
            "require_human_on_critical": True,
        },
        "success_criteria": success_criteria or [],
        "status": "pending",
        "blocked_reason": "",
        "created_at": _now(),
        "updated_at": _now(),
        "criteria_results": [],
        "all_criteria_ok": False,
    }
    save_goal(root, goal)
    return reevaluate_goal(root, goal)


def infer_goal_from_message(message: str) -> dict[str, Any]:
    """Heuristic goal template from user text (no LLM)."""
    text = message or ""
    chapter_ids = re.findall(r"\d+(?:\.\d+)*", text)
    # keep short ids only
    chapter_ids = [c for c in chapter_ids if len(c) <= 8][:20]

    objectives: list[dict[str, Any]] = []
    criteria: list[dict[str, Any]] = []
    constraints: dict[str, Any] = {
        "allow_skip_compliance": False,
        "require_human_on_critical": True,
    }

    wants_export = any(k in text for k in ("出 Word", "生成 Word", "导出", "final.docx", "出稿", "生成docx", "生成 docx"))
    wants_rewrite = any(k in text for k in ("改第", "重写", "改稿", "只修", "定向"))
    wants_full = any(k in text for k in ("一键生成", "全部跑完", "全量生成", "从头生成"))
    wants_status = any(k in text for k in ("状态", "进度", "诊断", "失败"))
    wants_coverage = any(k in text for k in ("覆盖率", "评分点", "未覆盖", "补齐评分", "覆盖缺口"))
    wants_compliance = any(k in text for k in ("合规", "废标", "blocking"))

    if wants_compliance:
        objectives.append({"type": "fix_compliance"})
        criteria.append({"check": "no_stale", "paths": ["workspace/compliance_report.json"]})
    elif wants_coverage:
        objectives.append({"type": "fix_coverage"})
        criteria.append({"check": "score_coverage_min", "ratio": 0.95})
        if wants_export:
            objectives.append({"type": "export", "targets": ["md", "docx"]})
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ]
            )
    elif wants_full:
        objectives.append({"type": "full_generate"})
        criteria.extend(
            [
                {"check": "artifact_exists", "path": "outputs/final.md"},
                {"check": "artifact_exists", "path": "outputs/final.docx"},
                {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
            ]
        )
    elif wants_rewrite and chapter_ids:
        objectives.append({"type": "fix_chapter", "chapter_ids": chapter_ids})
        constraints["chapter_ids"] = chapter_ids
        criteria.append({"check": "chapters_written", "chapter_ids": chapter_ids})
        if wants_export:
            objectives.append({"type": "export", "targets": ["md", "docx"]})
            criteria.extend(
                [
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ]
            )
    elif wants_export:
        objectives.append({"type": "export", "targets": ["md", "docx"]})
        criteria.extend(
            [
                {"check": "artifact_exists", "path": "outputs/final.md"},
                {"check": "artifact_exists", "path": "outputs/final.docx"},
                {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
            ]
        )
    elif wants_status:
        objectives.append({"type": "diagnose"})
        criteria.append({"check": "stage_ready", "stage_id": "init_workspace"})
    else:
        objectives.append({"type": "chat"})
        criteria = []

    return {
        "objectives": objectives,
        "success_criteria": criteria,
        "constraints": constraints,
        "chapter_ids": chapter_ids,
    }


def goal_summary(goal: dict[str, Any] | None) -> str:
    if not goal:
        return "无活动目标"
    status = goal.get("status")
    ok = goal.get("all_criteria_ok")
    results = goal.get("criteria_results") or []
    failed = [r for r in results if not r.get("ok")]
    base = f"目标 {goal.get('goal_id')} 状态={status} criteria_ok={ok}"
    if failed:
        details = "; ".join(f"{r.get('check')}:{r.get('detail')}" for r in failed[:5])
        base += f" 未达成: {details}"
    return base
