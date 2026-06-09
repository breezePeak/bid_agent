from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from file_loader import read_required_input
from llm_client import chat
from utils import listify, load_prompt, parse_json_from_model, project_root, stringify, write_json


def _parse_score(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def normalize_score_points(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("score_points") or data.get("items") or data.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("评分点解析结果必须是非空 JSON 数组。")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个评分点不是 JSON 对象。")

        requirement = stringify(item.get("requirement"))
        title = stringify(item.get("title")) or requirement[:30] or f"评分点{index:03d}"
        keywords = [stringify(keyword) for keyword in listify(item.get("keywords")) if stringify(keyword)]

        normalized.append(
            {
                "id": f"S{index:03d}",
                "category": stringify(item.get("category")),
                "title": title,
                "score": _parse_score(item.get("score")),
                "requirement": requirement,
                "keywords": keywords,
                "response_strategy": stringify(item.get("response_strategy")),
            }
        )

    return normalized


def parse_score(root: Path | None = None) -> Path:
    root = root or project_root()
    score_markdown = read_required_input(root, "score.md", "评分标准 inputs/score.md")
    prompt = load_prompt(root, "parse_score.md")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "请解析以下评分标准 Markdown，输出评分点 JSON 数组：\n\n" + score_markdown,
            },
        ],
        temperature=0.1,
    )
    data = parse_json_from_model(raw, root / "workspace" / "debug_parse_score_raw.txt")
    score_points = normalize_score_points(data)

    output_path = root / "workspace" / "score_points.json"
    write_json(output_path, score_points)
    print(f"[完成] 已解析 {len(score_points)} 个评分点: {output_path}")
    return output_path
