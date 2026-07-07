from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from file_loader import read_required_input
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import compact_json, listify, parse_json_from_model, project_root, stringify, write_json


MAX_SCORE_CHUNK_CHARS = 3200


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


def _coerce_items(data: Any, *keys: str) -> list[Any]:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        for key in ("items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []


def _split_markdown_blocks(markdown: str, max_chars: int = MAX_SCORE_CHUNK_CHARS) -> list[str]:
    parts = re.split(r"(?m)^---\s*$", markdown)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in parts:
        block = part.strip()
        if not block:
            continue
        block_text = block + "\n\n---\n\n"
        if current and current_len + len(block_text) > max_chars:
            chunks.append("".join(current).strip())
            current = []
            current_len = 0
        current.append(block_text)
        current_len += len(block_text)

    if current:
        chunks.append("".join(current).strip())

    return chunks or [markdown]


def normalize_score_requirements(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("score_requirements") or data.get("items") or data.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("评分要求抽取结果必须是非空 JSON 数组。")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个评分要求不是 JSON 对象。")

        title = stringify(item.get("title")) or stringify(item.get("requirement"))[:30] or f"评分要求{index:03d}"
        requirement = stringify(item.get("requirement")) or title
        keywords = [stringify(keyword) for keyword in listify(item.get("keywords")) if stringify(keyword)]

        normalized.append(
            {
                "id": f"R{index:03d}",
                "category": stringify(item.get("category")),
                "title": title,
                "score": _parse_score(item.get("score")),
                "requirement": requirement,
                "scoring_criteria": stringify(item.get("scoring_criteria") or item.get("rubric")),
                "keywords": keywords,
                "source_excerpt": stringify(item.get("source_excerpt")),
            }
        )

    return normalized


def parse_score(root: Path | None = None) -> Path:
    root = root or project_root()
    score_markdown = read_required_input(root, "score.md", "评分标准 inputs/score.md")
    requirement_prompt = load_agent_prompt(root, "score_requirement_extractor")
    score_chunks = _split_markdown_blocks(score_markdown)
    requirement_items: list[Any] = []
    for index, chunk in enumerate(score_chunks, start=1):
        with agent_run(
            root,
            "parse_score",
            "score_requirement_extractor",
            input_summary={"chunk_index": index, "chunk_count": len(score_chunks), "chunk_chars": len(chunk)},
            chapter_id=f"chunk_{index:02d}",
            temperature=0.1,
        ):
            raw_requirements = chat(
                [
                    {"role": "system", "content": requirement_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"请抽取以下评分标准 Markdown 分片中的原始评分要求，输出结构化 JSON。"
                            f"这是第 {index}/{len(score_chunks)} 个分片；只抽取本分片实际包含的要求，不要补充其他分片内容。\n\n"
                            + chunk
                        ),
                    },
                ],
                temperature=0.1,
            )
        requirement_data = parse_json_from_model(
            raw_requirements,
            root / "workspace" / f"debug_score_requirements_raw_{index:02d}.txt",
        )
        requirement_items.extend(_coerce_items(requirement_data, "score_requirements"))

    score_requirements = normalize_score_requirements(requirement_items)

    write_json(root / "workspace" / "score_requirements.json", score_requirements)

    score_point_prompt = load_agent_prompt(root, "score_point_parser")
    with agent_run(
        root,
        "parse_score",
        "score_point_parser",
        input_summary={"score_requirement_count": len(score_requirements)},
        temperature=0.1,
    ):
        raw_score_points = chat(
            [
                {"role": "system", "content": score_point_prompt},
                {
                    "role": "user",
                    "content": (
                        "请基于下列原始评分要求，整理为最终评分点 JSON 数组。"
                        "不得遗漏条目，不得合并不同评分要求。\n\n"
                        "## 原始评分要求 JSON\n\n"
                        f"{compact_json(score_requirements)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    data = parse_json_from_model(raw_score_points, root / "workspace" / "debug_parse_score_raw.txt")
    score_points = normalize_score_points(data)

    if len(score_points) < len(score_requirements):
        raise ValueError(
            f"评分点数量不足：原始评分要求 {len(score_requirements)} 条，最终评分点仅 {len(score_points)} 条。"
        )

    output_path = root / "workspace" / "score_points.json"
    write_json(output_path, score_points)
    print(f"[完成] 已解析 {len(score_points)} 个评分点: {output_path}")
    return output_path
