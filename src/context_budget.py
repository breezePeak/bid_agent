from __future__ import annotations

from typing import Any

from utils import compact_json, stringify


def trim_text(text: str, max_chars: int) -> str:
    cleaned = stringify(text)
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + " ...(truncated)"


def summarize_chunk_payload(
    chunks: list[dict[str, Any]],
    *,
    total_max_chars: int,
    per_chunk_chars: int,
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    used = 0
    for chunk in chunks:
        remaining = total_max_chars - used
        if remaining <= 0:
            break
        preview_limit = min(per_chunk_chars, remaining)
        content = trim_text(stringify(chunk.get("content")), preview_limit)
        item = {
            "id": stringify(chunk.get("id")),
            "source": stringify(chunk.get("source")),
            "title_path": chunk.get("title_path", []),
            "keywords": chunk.get("keywords", []),
            "selected_reason": stringify(chunk.get("selected_reason")),
            "content": content,
        }
        summary.append(item)
        used += len(compact_json(item))
    return summary


def summarize_for_prompt(value: Any, max_chars: int) -> str:
    return trim_text(compact_json(value), max_chars)
