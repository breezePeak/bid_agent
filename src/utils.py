from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_dirs(root: Path, relative_dirs: Iterable[str]) -> None:
    for relative_dir in relative_dirs:
        (root / relative_dir).mkdir(parents=True, exist_ok=True)


def ensure_file(path: Path, default_content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(default_content, encoding="utf-8")


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"缺少文件: {path}")
    return path.read_text(encoding="utf-8")


def read_nonempty_text(path: Path, purpose: str) -> str:
    content = read_text(path)
    if not content.strip():
        raise ValueError(f"{purpose} 为空，请先填写文件: {path}")
    return content


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 文件解析失败: {path} ({exc})") from exc


def write_json(path: Path, data: Any) -> None:
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    json.loads(encoded)
    write_text(path, encoded + "\n")


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    fence_match = re.match(r"^```[a-zA-Z0-9_-]*\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


def extract_json_text(text: str) -> str:
    cleaned = strip_code_fences(text)
    decoder = json.JSONDecoder()

    for start, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        return cleaned[start : start + end]

    raise ValueError("模型输出中未找到合法 JSON。")


def parse_json_from_model(raw_text: str, debug_path: Path) -> Any:
    try:
        return json.loads(extract_json_text(raw_text))
    except Exception as exc:
        write_text(debug_path, raw_text)
        raise ValueError(f"模型 JSON 解析失败，原始输出已保存到: {debug_path}") from exc


def load_prompt(root: Path, filename: str) -> str:
    return read_nonempty_text(root / "prompts" / filename, f"提示词 {filename}")


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [value]


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def find_chapter(outline: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    for chapter in outline.get("chapters", []):
        if str(chapter.get("id")) == str(chapter_id):
            return chapter
    raise ValueError(f"未在 outline.json 中找到章节: {chapter_id}")


def score_points_by_id(score_points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in score_points}


def select_score_points(score_points: list[dict[str, Any]], ids: Iterable[str]) -> list[dict[str, Any]]:
    index = score_points_by_id(score_points)
    selected = []
    for score_id in ids:
        if score_id in index:
            selected.append(index[score_id])
    return selected
