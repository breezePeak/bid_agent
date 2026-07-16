from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from utils import project_root, read_text, stringify

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9\-_/]{1,}")
STOPWORDS = {
    "以及",
    "或者",
    "进行",
    "相关",
    "要求",
    "应当",
    "可以",
    "如果",
    "需要",
    "提供",
    "包括",
    "根据",
    "对于",
    "一个",
    "我们",
    "项目",
    "服务",
    "系统",
    "内容",
    "工作",
    "方案",
    "情况",
    "其他",
    "及其",
    "并",
    "的",
    "和",
    "与",
    "及",
    "等",
    "为",
    "在",
    "对",
    "中",
    "上",
    "下",
    "将",
    "把",
    "被",
    "由",
    "从",
    "到",
    "后",
    "前",
    "已",
    "未",
    "不",
    "有",
    "无",
    "该",
    "本",
    "其",
    "各",
    "每",
    "应",
    "须",
    "需",
}


def _tokens(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(text or ""):
        t = token.strip().lower()
        if len(t) < 2 or t in STOPWORDS:
            continue
        tokens.append(t)
    return tokens


def _keyword_list(score_point: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    raw = score_point.get("keywords")
    if isinstance(raw, list):
        for item in raw:
            text = stringify(item).strip()
            if text and text not in keywords:
                keywords.append(text)
    # 仅当没有显式 keywords 时，从 title/requirement 补充
    if not keywords:
        for field in ("title", "requirement", "response_strategy"):
            for token in _tokens(stringify(score_point.get(field)))[:12]:
                if token not in keywords and len(token) >= 2:
                    keywords.append(token)
    return keywords[:20]


def _level_from_rate(hit_rate: float, overlap: float, hit_count: int) -> str:
    score = 0.65 * hit_rate + 0.35 * overlap
    if hit_count == 0 and overlap < 0.08:
        return "none"
    if score >= 0.55 and hit_count >= 2:
        return "high"
    if score >= 0.35 or hit_count >= 2:
        return "medium"
    if score >= 0.15 or hit_count >= 1:
        return "low"
    return "none"


def compute_score_point_hard_metrics(
    score_point: dict[str, Any],
    chapter_texts: dict[str, str],
    bound_chapter_ids: list[str],
) -> dict[str, Any]:
    keywords = _keyword_list(score_point)
    requirement = stringify(score_point.get("requirement")) or stringify(score_point.get("title"))
    req_tokens = set(_tokens(requirement))

    # 优先绑定章节，否则全文
    texts: list[tuple[str, str]] = []
    for chapter_id in bound_chapter_ids:
        text = chapter_texts.get(chapter_id, "")
        if text:
            texts.append((chapter_id, text))
    if not texts:
        texts = [(cid, text) for cid, text in chapter_texts.items() if text]

    joined = "\n".join(text for _, text in texts)
    joined_lower = joined.lower()
    chapter_token_set = set(_tokens(joined))

    keyword_hits: list[dict[str, Any]] = []
    hit_count = 0
    for keyword in keywords:
        key = keyword.lower()
        count = joined_lower.count(key) if key else 0
        if count > 0:
            hit_count += 1
            keyword_hits.append({"keyword": keyword, "count": count})
    hit_rate = (hit_count / len(keywords)) if keywords else 0.0
    overlap = 0.0
    if req_tokens:
        overlap = len(req_tokens & chapter_token_set) / max(len(req_tokens), 1)

    level = _level_from_rate(hit_rate, overlap, hit_count)
    return {
        "keyword_count": len(keywords),
        "keyword_hits": keyword_hits[:20],
        "keyword_hit_count": hit_count,
        "keyword_hit_rate": round(hit_rate, 4),
        "requirement_token_count": len(req_tokens),
        "requirement_token_overlap": round(overlap, 4),
        "bound_chapter_ids": bound_chapter_ids,
        "scanned_chapter_ids": [cid for cid, _ in texts],
        "bound_chapter_text_chars": len(joined),
        "level_hint": level,
        "covered_hint": level in {"high", "medium", "low"},
    }


def load_chapter_texts(root: Path) -> dict[str, str]:
    chapters_dir = root / "workspace" / "chapters"
    texts: dict[str, str] = {}
    if not chapters_dir.exists():
        return texts
    for path in sorted(chapters_dir.glob("*.md")):
        try:
            texts[path.stem] = read_text(path)
        except Exception:
            texts[path.stem] = ""
    return texts


def enrich_matrix_with_hard_metrics(root: Path, matrix: dict[str, Any]) -> dict[str, Any]:
    root = root or project_root()
    chapter_texts = load_chapter_texts(root)
    rows = matrix.get("matrix") if isinstance(matrix.get("matrix"), list) else []
    hard_uncovered: list[str] = []
    hard_weak: list[str] = []
    hard_strong: list[str] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        score_point = {
            "id": row.get("score_point_id"),
            "title": row.get("score_point_title"),
            "requirement": row.get("requirement"),
            "keywords": row.get("keywords"),
            "category": row.get("category"),
        }
        bound_ids = [
            stringify(item.get("chapter_id"))
            for item in (row.get("bound_chapters") or [])
            if isinstance(item, dict) and stringify(item.get("chapter_id"))
        ]
        hard = compute_score_point_hard_metrics(score_point, chapter_texts, bound_ids)
        row["hard_metrics"] = hard

        # 融合：硬指标可下调 LLM 乐观覆盖
        llm_covered = bool(row.get("covered"))
        llm_levels = [stringify(x) for x in (row.get("coverage_levels") or [])]
        hard_level = hard.get("level_hint") or "none"
        if hard_level == "none":
            row["hard_covered"] = False
            row["fused_risk_level"] = "high"
            hard_uncovered.append(stringify(row.get("score_point_id")))
            if llm_covered:
                row["coverage_conflict"] = "llm_covered_but_hard_none"
        elif hard_level == "low":
            row["hard_covered"] = True
            row["fused_risk_level"] = "medium"
            hard_weak.append(stringify(row.get("score_point_id")))
        else:
            row["hard_covered"] = True
            row["fused_risk_level"] = "low" if hard_level == "high" or any(l in {"high", "medium"} for l in llm_levels) else "medium"
            hard_strong.append(stringify(row.get("score_point_id")))

        # 最终 risk 取更严
        llm_risk = stringify(row.get("risk_level")) or "medium"
        rank = {"high": 2, "medium": 1, "low": 0}
        row["risk_level"] = max([llm_risk, row.get("fused_risk_level") or "medium"], key=lambda x: rank.get(x, 1))

    summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    summary = dict(summary)
    summary["hard_uncovered_count"] = len(hard_uncovered)
    summary["hard_weak_count"] = len(hard_weak)
    summary["hard_strong_count"] = len(hard_strong)
    matrix["summary"] = summary
    matrix["hard_uncovered_score_points"] = hard_uncovered
    matrix["hard_weak_score_points"] = hard_weak
    matrix["hard_strong_score_points"] = hard_strong
    return matrix
