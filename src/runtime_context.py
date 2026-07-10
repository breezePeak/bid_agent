from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import get_settings
from project_profile_registry import load_project_profile
from utils import project_root, stringify, write_json


_LOCAL = threading.local()


def _stack() -> list[dict[str, Any]]:
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _LOCAL.stack = stack
    return stack


def current_agent_context() -> dict[str, Any] | None:
    stack = _stack()
    return stack[-1] if stack else None


def register_prompt_metadata(
    agent_name: str,
    prompt_file: str,
    version: str,
    checksum: str,
    *,
    project_type: str = "",
) -> None:
    context = current_agent_context()
    if context is None or context.get("agent_name") != agent_name:
        return
    context["prompt_file"] = prompt_file
    context["prompt_version"] = version
    context["prompt_checksum"] = checksum
    if project_type:
        context["project_type"] = project_type


def record_llm_call(messages: list[dict[str, Any]], response_text: str, model: str, temperature: float) -> None:
    context = current_agent_context()
    if context is None:
        return
    context["llm_calls"] = int(context.get("llm_calls", 0)) + 1
    input_chars = sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)
    output_chars = len(response_text or "")
    context["input_tokens_est"] = int(context.get("input_tokens_est", 0)) + max(1, input_chars // 4)
    context["output_tokens_est"] = int(context.get("output_tokens_est", 0)) + max(1, output_chars // 4)
    context["model"] = model
    context["temperature"] = temperature


def _artifact_basename(context: dict[str, Any]) -> str:
    stage_id = stringify(context.get("stage_id")) or "unknown_stage"
    agent_name = stringify(context.get("agent_name")) or "unknown_agent"
    chapter_id = stringify(context.get("chapter_id")) or "global"
    return f"{stage_id}__{agent_name}__{chapter_id}"


def _persist_agent_run(context: dict[str, Any]) -> None:
    root = Path(context.get("root_dir") or project_root())
    stage_id = stringify(context.get("stage_id"))
    chapter_id = stringify(context.get("chapter_id"))
    try:
        settings = get_settings(root)
        default_model = settings.model
    except Exception:
        default_model = ""
    payload = {
        "run_id": stringify(context.get("run_id")),
        "stage": stage_id,
        "chapter_id": chapter_id,
        "agent_name": stringify(context.get("agent_name")),
        "prompt_file": stringify(context.get("prompt_file")),
        "prompt_version": stringify(context.get("prompt_version")),
        "prompt_checksum": stringify(context.get("prompt_checksum")),
        "project_type": stringify(context.get("project_type")),
        "input_contract": context.get("input_contract", {}),
        "output_contract": context.get("output_contract", {}),
        "input_summary": context.get("input_summary", {}),
        "context_budget": context.get("context_budget", {}),
        "temperature": context.get("temperature"),
        "model": stringify(context.get("model")) or default_model,
        "llm_calls": int(context.get("llm_calls", 0)),
        "input_tokens_est": int(context.get("input_tokens_est", 0)),
        "output_tokens_est": int(context.get("output_tokens_est", 0)),
        "started_at": context.get("started_at"),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "duration_ms": int((time.time() - float(context.get("start_ts", time.time()))) * 1000),
    }
    artifact_dir = root / "workspace" / "agent_runs"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    base = _artifact_basename(context)
    latest_path = artifact_dir / f"{base}.json"
    history_path = artifact_dir / f"{base}__{int(time.time() * 1000)}.json"
    write_json(latest_path, payload)
    write_json(history_path, payload)
    try:
        from graph.state_recorder import record_agent_run_artifact

        record_agent_run_artifact(root, stage_id, payload, artifact_path=latest_path, chapter_id=chapter_id)
    except Exception:
        pass


@contextmanager
def agent_run(
    root: Path | None,
    stage_id: str,
    agent_name: str,
    *,
    input_summary: dict[str, Any] | None = None,
    chapter_id: str = "",
    temperature: float | None = None,
) -> Iterator[dict[str, Any]]:
    resolved_root = Path(root or project_root())
    from prompt_registry import agent_spec_for

    spec = agent_spec_for(agent_name)
    profile = load_project_profile(resolved_root)
    now = time.time()
    try:
        settings = get_settings(resolved_root)
        model_name = settings.model
    except Exception:
        model_name = ""
    context = {
        "root_dir": str(resolved_root),
        "run_id": "",
        "stage_id": stage_id,
        "agent_name": agent_name,
        "chapter_id": chapter_id,
        "project_type": stringify(profile.get("project_type")),
        "input_summary": input_summary or {},
        "input_contract": spec.input_contract,
        "output_contract": spec.output_contract,
        "context_budget": spec.context_budget,
        "temperature": temperature,
        "model": model_name,
        "llm_calls": 0,
        "input_tokens_est": 0,
        "output_tokens_est": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "start_ts": now,
    }
    _stack().append(context)
    try:
        yield context
    finally:
        popped = _stack().pop()
        _persist_agent_run(popped)
