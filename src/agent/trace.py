from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils import project_root


def agent_dir(root: Path | None = None) -> Path:
    root = root or project_root()
    path = root / "workspace" / "agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def decisions_path(root: Path | None = None) -> Path:
    return agent_dir(root) / "decisions.jsonl"


def last_plan_path(root: Path | None = None) -> Path:
    return agent_dir(root) / "last_plan.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    return uuid4().hex[:12]


def append_decision(root: Path | None, record: dict[str, Any]) -> dict[str, Any]:
    """Append one decision record. Never stores secrets."""
    root = root or project_root()
    payload = dict(record)
    payload.setdefault("created_at", _now())
    if "trace_id" not in payload:
        payload["trace_id"] = new_trace_id()
    # redaction
    text = json.dumps(payload, ensure_ascii=False)
    for key in ("OPENAI_API_KEY", "api_key", "authorization"):
        if key in text.lower():
            payload["redacted"] = True
    path = decisions_path(root)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def load_decisions(root: Path | None = None, *, tail: int = 20) -> list[dict[str, Any]]:
    path = decisions_path(root)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, tail) :]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def save_last_plan(root: Path | None, plan: dict[str, Any]) -> Path:
    path = last_plan_path(root)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def max_steps_default() -> int:
    try:
        return max(1, int(os.environ.get("AGENT_MAX_STEPS", "12")))
    except ValueError:
        return 12
