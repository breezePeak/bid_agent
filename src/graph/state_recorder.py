from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_registry import StageSpec, artifact_exists, stage_spec_by_id


_LARGE_TEXT_KEYS = {
    "chapter_markdown",
    "content",
    "text",
    "raw",
    "prompt",
}

_JOB_KEYS = {
    "chapter_id",
    "chapter_title",
    "score_point_ids",
    "description",
    "sections",
}

_JSONL_LOCK = threading.Lock()
_METRICS_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _workspace_dir(root: Path) -> Path:
    workspace = Path(root) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _run_state_path(root: Path) -> Path:
    return _workspace_dir(root) / "run_state.json"


def _run_history_path(root: Path) -> Path:
    return _workspace_dir(root) / "run_state_history.jsonl"


def _run_events_path(root: Path) -> Path:
    return _workspace_dir(root) / "run_events.jsonl"


def _run_metrics_path(root: Path) -> Path:
    return _workspace_dir(root) / "run_metrics.json"


def _stringify_error_items(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items]


def _sanitize_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {
            str(sub_key): _sanitize_value(str(sub_key), sub_value)
            for sub_key, sub_value in value.items()
            if str(sub_key).lower() not in _LARGE_TEXT_KEYS
        }

    if isinstance(value, list):
        if key == "chapter_jobs":
            return [_sanitize_job_item(item) for item in value if isinstance(item, dict)]
        return [_sanitize_value(key, item) for item in value]

    if isinstance(value, tuple):
        return [_sanitize_value(key, item) for item in value]

    if isinstance(value, set):
        return [_sanitize_value(key, item) for item in sorted(value, key=str)]

    return str(value)


def _sanitize_job_item(job: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in _JOB_KEYS:
        if key not in job:
            continue
        if key == "sections":
            sections = job.get("sections")
            sanitized["section_count"] = len(sections) if isinstance(sections, list) else 0
            continue
        sanitized[key] = _sanitize_value(key, job.get(key))
    return sanitized


def _build_summary(state: dict[str, Any]) -> dict[str, int]:
    chapter_jobs = state.get("chapter_jobs")
    completed_chapters = state.get("completed_chapters")
    failed_chapters = state.get("failed_chapters")
    errors = state.get("errors")

    return {
        "chapter_job_count": len(chapter_jobs) if isinstance(chapter_jobs, list) else 0,
        "completed_chapter_count": len(completed_chapters) if isinstance(completed_chapters, list) else 0,
        "failed_chapter_count": len(failed_chapters) if isinstance(failed_chapters, list) else 0,
        "error_count": len(errors) if isinstance(errors, list) else 0,
    }


def _sanitize_state(state: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in state.items():
        if key.lower() in _LARGE_TEXT_KEYS:
            continue
        sanitized[key] = _sanitize_value(key, value)

    sanitized["completed_chapters"] = _stringify_error_items(sanitized.get("completed_chapters"))
    sanitized["errors"] = _stringify_error_items(sanitized.get("errors"))

    failed = sanitized.get("failed_chapters")
    if isinstance(failed, list):
        normalized_failed: list[dict[str, Any]] = []
        for item in failed:
            if isinstance(item, dict):
                normalized = {str(key): _sanitize_value(str(key), value) for key, value in item.items()}
                normalized["chapter_id"] = str(item.get("chapter_id", ""))
                normalized["error"] = str(item.get("error", ""))
                normalized_failed.append(normalized)
            else:
                normalized_failed.append({"chapter_id": "", "error": str(item)})
        sanitized["failed_chapters"] = normalized_failed
    else:
        sanitized["failed_chapters"] = []

    return sanitized


def _load_metrics(root: Path) -> dict[str, Any]:
    path = _run_metrics_path(root)
    if not path.exists():
        return {"run_id": "", "stages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"run_id": "", "stages": {}}
    if not isinstance(data, dict):
        return {"run_id": "", "stages": {}}
    data.setdefault("stages", {})
    return data


def _save_metrics(root: Path, metrics: dict[str, Any]) -> None:
    _run_metrics_path(root).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def _rotate_jsonl(path: Path, *, max_bytes: int = 5 * 1024 * 1024, keep: int = 3) -> None:
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for index in range(max(1, keep), 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        if not source.exists():
            continue
        if target.exists():
            target.unlink()
        source.replace(target)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with _JSONL_LOCK:
        _rotate_jsonl(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")


def _metrics_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    stages = metrics.get("stages", {}) if isinstance(metrics, dict) else {}
    if not isinstance(stages, dict):
        return result
    for stage, value in stages.items():
        if not isinstance(value, dict):
            continue
        result[str(stage)] = {
            key: value.get(key, 0)
            for key in ("attempts", "duration_ms", "llm_calls", "input_tokens_est", "output_tokens_est")
        }
    return result


def _ensure_run_id(root: Path, state: dict[str, Any] | None = None) -> str:
    if isinstance(state, dict):
        run_id = str(state.get("run_id", "")).strip()
        if run_id:
            return run_id
    loaded = load_run_state(root)
    run_id = str(loaded.get("run_id", "")).strip()
    if run_id:
        return run_id
    metrics = _load_metrics(root)
    run_id = str(metrics.get("run_id", "")).strip()
    if run_id:
        return run_id
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    metrics["run_id"] = run_id
    _save_metrics(root, metrics)
    return run_id


def record_stage_event(
    root: Path,
    stage: str,
    event_type: str,
    *,
    message: str = "",
    chapter_id: str = "",
    artifact_path: str = "",
    status: str = "",
    metrics: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ts": _now(),
        "run_id": _ensure_run_id(root, state),
        "stage": stage,
        "event_type": event_type,
        "status": status,
        "message": message,
        "chapter_id": chapter_id,
        "artifact_path": artifact_path,
        "metrics": metrics or {},
    }
    _append_jsonl(_run_events_path(root), payload)
    return payload


def record_agent_run_artifact(
    root: Path,
    stage: str,
    payload: dict[str, Any],
    *,
    artifact_path: Path,
    chapter_id: str = "",
) -> None:
    # Agent workers finish concurrently. Keep the complete read-modify-write
    # transaction under one lock so token counts and agent_runs cannot be lost.
    with _METRICS_LOCK:
        metrics = _load_metrics(root)
        run_id = _ensure_run_id(root)
        metrics["run_id"] = run_id
        stages = metrics.setdefault("stages", {})
        stage_metrics = stages.setdefault(
            stage,
            {"attempts": 0, "duration_ms": 0, "llm_calls": 0, "input_tokens_est": 0, "output_tokens_est": 0, "agent_runs": []},
        )
        stage_metrics["llm_calls"] = int(stage_metrics.get("llm_calls", 0)) + int(payload.get("llm_calls", 0))
        stage_metrics["input_tokens_est"] = int(stage_metrics.get("input_tokens_est", 0)) + int(payload.get("input_tokens_est", 0))
        stage_metrics["output_tokens_est"] = int(stage_metrics.get("output_tokens_est", 0)) + int(payload.get("output_tokens_est", 0))
        stage_metrics["duration_ms"] = int(stage_metrics.get("duration_ms", 0)) + int(payload.get("duration_ms", 0))
        agent_runs = stage_metrics.setdefault("agent_runs", [])
        agent_runs.append(
            {
                "agent_name": payload.get("agent_name", ""),
                "chapter_id": chapter_id,
                "artifact_path": str(artifact_path),
                "prompt_file": payload.get("prompt_file", ""),
                "prompt_version": payload.get("prompt_version", ""),
                "prompt_checksum": payload.get("prompt_checksum", ""),
                "model": payload.get("model", ""),
                "temperature": payload.get("temperature"),
            }
        )
        _save_metrics(root, metrics)
    record_stage_event(
        root,
        stage,
        "agent_artifact",
        message=f"agent={payload.get('agent_name', '')}",
        chapter_id=chapter_id,
        artifact_path=str(artifact_path),
        metrics={
            "llm_calls": payload.get("llm_calls", 0),
            "input_tokens_est": payload.get("input_tokens_est", 0),
            "output_tokens_est": payload.get("output_tokens_est", 0),
        },
    )


def stage_resume_ready(root: Path, stage_id: str) -> bool:
    if not stage_outputs_valid(root, stage_id):
        return False
    for event in reversed(load_run_events(root)):
        if str(event.get("stage")) != stage_id:
            continue
        return str(event.get("event_type")) in {"success", "skip", "reuse"}
    return False


def stage_outputs_valid(root: Path, stage_id: str) -> bool:
    try:
        spec = stage_spec_by_id(stage_id)
    except KeyError:
        return False
    if spec.validator == "collection":
        from stage_validation import stage_collection_status

        return bool(stage_collection_status(root, stage_id)["complete"])
    return all(artifact_exists(root, artifact) for artifact in spec.produces)


def record_stage_start(root: Path, stage: str, *, state: dict[str, Any] | None = None, message: str = "") -> None:
    record_stage_event(root, stage, "start", state=state, message=message or "stage_start")
    metrics = _load_metrics(root)
    metrics["run_id"] = _ensure_run_id(root, state)
    stages = metrics.setdefault("stages", {})
    stage_metrics = stages.setdefault(
        stage,
        {"attempts": 0, "duration_ms": 0, "llm_calls": 0, "input_tokens_est": 0, "output_tokens_est": 0, "agent_runs": []},
    )
    stage_metrics["attempts"] = int(stage_metrics.get("attempts", 0)) + 1
    stage_metrics["started_at"] = _now()
    _save_metrics(root, metrics)


def record_stage_finish(root: Path, stage: str, event_type: str, *, message: str = "", artifact_path: str = "", status: str = "") -> None:
    metrics = _load_metrics(root)
    stage_metrics = metrics.setdefault("stages", {}).setdefault(
        stage,
        {"attempts": 0, "duration_ms": 0, "llm_calls": 0, "input_tokens_est": 0, "output_tokens_est": 0, "agent_runs": []},
    )
    started_at = stage_metrics.get("started_at")
    duration_ms = 0
    if isinstance(started_at, str):
        try:
            start_dt = datetime.fromisoformat(started_at)
            duration_ms = int((datetime.now() - start_dt).total_seconds() * 1000)
        except Exception:
            duration_ms = 0
    if duration_ms:
        stage_metrics["duration_ms"] = duration_ms
    _save_metrics(root, metrics)
    record_stage_event(
        root,
        stage,
        event_type,
        message=message,
        artifact_path=artifact_path,
        status=status,
        metrics={
            "attempts": stage_metrics.get("attempts", 0),
            "duration_ms": stage_metrics.get("duration_ms", 0),
            "llm_calls": stage_metrics.get("llm_calls", 0),
            "input_tokens_est": stage_metrics.get("input_tokens_est", 0),
            "output_tokens_est": stage_metrics.get("output_tokens_est", 0),
        },
    )


def save_run_state(
    root: Path,
    state: dict[str, Any],
    stage: str,
    status: str = "ok",
    message: str = "",
) -> Path:
    workspace_dir = _workspace_dir(root)
    run_state_path = workspace_dir / "run_state.json"

    try:
        sanitized_state = _sanitize_state(dict(state))
        run_id = _ensure_run_id(root, sanitized_state)
        sanitized_state["run_id"] = run_id
        metrics = _load_metrics(root)
        metrics["run_id"] = run_id
        _save_metrics(root, metrics)
        payload = {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "message": message,
            "updated_at": _now(),
            "state": sanitized_state,
            "summary": _build_summary(sanitized_state),
            "metrics": _metrics_snapshot(metrics),
        }

        run_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _append_jsonl(_run_history_path(root), payload)
    except Exception as exc:
        print(f"[警告] 运行状态记录失败(stage={stage}): {exc}")

    return run_state_path


def load_run_state(root: Path) -> dict[str, Any]:
    run_state_path = _run_state_path(root)
    if not run_state_path.exists():
        return {}

    try:
        data = json.loads(run_state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[警告] 运行状态读取失败: {run_state_path} ({exc})")
        return {}

    if not isinstance(data, dict):
        return {}
    state = data.get("state")
    if isinstance(state, dict) and "run_id" not in state and data.get("run_id"):
        state["run_id"] = data.get("run_id")
    return data


def load_run_events(root: Path) -> list[dict[str, Any]]:
    path = _run_events_path(root)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def load_run_history(root: Path) -> list[dict[str, Any]]:
    path = _run_history_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_stage_metrics(root: Path) -> dict[str, Any]:
    return _load_metrics(root).get("stages", {})
