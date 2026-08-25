from __future__ import annotations

import re
from typing import Any


_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def semantic_terms(value: Any) -> set[str]:
    """Stable mixed Chinese/ASCII terms for controlled local reranking."""

    text = str(value or "").strip().lower()
    terms: set[str] = set()
    for token in _WORD_RE.findall(text):
        if token.isascii():
            if len(token) >= 2:
                terms.add(token)
            continue
        if len(token) == 1:
            terms.add(token)
        else:
            terms.update(token[index : index + 2] for index in range(len(token) - 1))
    return terms


def semantic_similarity(left: Any, right: Any) -> float:
    left_terms = semantic_terms(left)
    right_terms = semantic_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    return overlap / max(1, min(len(left_terms), len(right_terms)))


class LegacyBidSemanticReranker:
    """Controlled second-stage reranker over already recalled raw blocks."""

    provider_id = "controlled_local_semantic.v1"

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            content = str(candidate.get("content") or "")
            heading = " ".join(str(item) for item in candidate.get("heading_path") or [])
            score = max(
                semantic_similarity(query, content),
                semantic_similarity(query, heading) * 1.08,
            )
            if score <= 0:
                continue
            ranked.append({**candidate, "semantic_score": round(min(score, 1.0), 6)})
        ranked.sort(
            key=lambda item: (
                -float(item.get("semantic_score") or 0),
                int(item.get("ordinal") or 0),
                str(item.get("block_id") or ""),
            )
        )
        return ranked[: max(1, int(limit))]
