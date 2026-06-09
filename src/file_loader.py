from __future__ import annotations

from pathlib import Path

from utils import read_json, read_nonempty_text, read_text


def input_path(root: Path, filename: str) -> Path:
    return root / "inputs" / filename


def workspace_path(root: Path, filename: str) -> Path:
    return root / "workspace" / filename


def read_input(root: Path, filename: str) -> str:
    return read_text(input_path(root, filename))


def read_required_input(root: Path, filename: str, purpose: str) -> str:
    return read_nonempty_text(input_path(root, filename), purpose)


def load_score_points(root: Path) -> list[dict]:
    data = read_json(root / "workspace" / "score_points.json")
    if not isinstance(data, list):
        raise ValueError("workspace/score_points.json 必须是 JSON 数组。")
    return data


def load_global_facts(root: Path) -> dict:
    data = read_json(root / "workspace" / "global_facts.json")
    if not isinstance(data, dict):
        raise ValueError("workspace/global_facts.json 必须是 JSON 对象。")
    return data


def load_outline(root: Path) -> dict:
    data = read_json(root / "workspace" / "outline.json")
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise ValueError("workspace/outline.json 必须包含 chapters 数组。")
    return data
