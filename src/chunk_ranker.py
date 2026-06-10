from __future__ import annotations

import re
from typing import Any

_STOPWORDS: set[str] = {
    "项目", "方案", "服务", "要求", "进行", "提供", "包括", "相关", "内容",
    "根据", "投标", "招标", "建设", "管理", "系统", "数据", "平台", "技术",
    "工作", "实施", "保障", "支持", "满足", "符合", "应当", "以及", "或者",
    "但是", "如果", "因为", "所以", "可以", "能够", "已经", "其中", "同时",
    "对于", "关于", "通过", "需要", "应该", "必须", "确保", "实现", "完成",
    "主要", "具体", "明确", "负责", "参与", "承担", "具有", "拥有", "适用",
    "一个", "这个", "那个", "这些", "那些", "不是", "没有", "什么", "怎么",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "dare",
    "and", "or", "but", "nor", "not", "yet", "so", "for", "with", "from",
    "into", "onto", "upon", "out", "off", "over", "under", "above", "below",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
}

_PUNCTUATION_RE = re.compile(
    r"[\s，。、；：？！""''（）【】《》…—～\u3000"
    r"\n\r,.;:?!()\[\]{}<>\/\\|@#$%^&*+=~`\-_+]+"
)

_TITLE_HIT = 5
_TIER_SCORE: dict[str, int] = {
    "score_keyword": 4,
    "chapter_title": 4,
    "requirement": 2,
    "ordinary": 1,
}
_MAX_HITS = 3


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or (0x20000 <= cp <= 0x2A6DF)


def _extract_terms(text: str) -> list[str]:
    if not text:
        return []
    segments = _PUNCTUATION_RE.split(text)
    terms: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if len(seg) < 2:
            continue
        low = seg.lower()
        if len(low) <= 8:
            if low not in _STOPWORDS:
                terms.append(low)
        else:
            has_cjk = any(_is_cjk(c) for c in seg)
            if has_cjk:
                for size in (4, 3, 2):
                    for start in range(0, len(seg) - size + 1, 2):
                        sub = seg[start : start + size].lower()
                        if sub not in _STOPWORDS:
                            terms.append(sub)
            else:
                for word in low.split():
                    if len(word) >= 2 and word not in _STOPWORDS:
                        terms.append(word)
    return terms


def _collect_keywords(
    job: dict[str, Any],
    score_points: list[dict[str, Any]],
) -> dict[str, str]:
    keyword_tier: dict[str, str] = {}

    def _add(terms: list[str], tier: str) -> None:
        for t in terms:
            existing = keyword_tier.get(t)
            if existing is None or _TIER_SCORE.get(tier, 0) > _TIER_SCORE.get(existing, 0):
                keyword_tier[t] = tier

    for sp in score_points:
        kws = sp.get("keywords")
        if isinstance(kws, list):
            for kw in kws:
                kw_str = str(kw).strip().lower()
                if kw_str and len(kw_str) >= 2:
                    _add([kw_str], "score_keyword")

    chapter_title = str(job.get("chapter_title", "")).strip()
    _add(_extract_terms(chapter_title), "chapter_title")

    description = str(job.get("description", "")).strip()
    _add(_extract_terms(description), "requirement")

    for sp in score_points:
        _add(_extract_terms(str(sp.get("title", ""))), "requirement")
        _add(_extract_terms(str(sp.get("requirement", ""))), "requirement")
        _add(_extract_terms(str(sp.get("response_strategy", ""))), "requirement")
        _add(_extract_terms(str(sp.get("category", ""))), "ordinary")

    for section in job.get("sections", []):
        if not isinstance(section, dict):
            continue
        _add(_extract_terms(str(section.get("title", ""))), "chapter_title")
        wr = section.get("writing_requirements")
        if isinstance(wr, list):
            for item in wr:
                _add(_extract_terms(str(item)), "requirement")
        elif wr:
            _add(_extract_terms(str(wr)), "requirement")

    return keyword_tier


def _count_occurrences(text: str, keyword: str) -> int:
    count = 0
    start = 0
    kl = len(keyword)
    while True:
        idx = text.find(keyword, start)
        if idx == -1:
            break
        count += 1
        start = idx + kl
    return count


def _score_chunk(
    chunk: dict[str, Any],
    keyword_tier: dict[str, str],
) -> tuple[float, list[str]]:
    if not keyword_tier:
        return 0.0, []

    title_path = chunk.get("title_path", [])
    if isinstance(title_path, list):
        title_text = " ".join(str(t) for t in title_path).lower()
    else:
        title_text = str(title_path).lower()

    content = str(chunk.get("content") or chunk.get("text") or "").lower()

    if not title_text and not content:
        return 0.0, []

    total_score = 0.0
    reasons: list[str] = []

    for keyword, tier in keyword_tier.items():
        kw_score = 0.0
        kw_reasons: list[str] = []

        if keyword in title_text:
            kw_score += _TITLE_HIT
            kw_reasons.append(f"命中标题：{keyword}")

        if content:
            hits = min(_count_occurrences(content, keyword), _MAX_HITS)
            if hits > 0:
                tier_weight = _TIER_SCORE.get(tier, 1)
                kw_score += tier_weight * hits
                kw_reasons.append(f"命中正文{hits}次：{keyword}（{tier}）")

        if kw_score > 0:
            total_score += kw_score
            reasons.extend(kw_reasons)

    return total_score, reasons


def rank_chunks_for_job(
    job: dict[str, Any],
    score_points: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    top_k: int = 30,
) -> list[dict[str, Any]]:
    keyword_tier = _collect_keywords(job, score_points)

    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        score, reasons = _score_chunk(chunk, keyword_tier)
        enriched = dict(chunk)
        enriched["rank_score"] = score
        enriched["rank_reasons"] = reasons
        scored.append(enriched)

    scored.sort(key=lambda c: c["rank_score"], reverse=True)
    return scored[:top_k]


def rank_for_job_separate(
    job: dict[str, Any],
    score_points: list[dict[str, Any]],
    tender_chunks: list[dict[str, Any]],
    company_chunks: list[dict[str, Any]],
    top_k: int = 30,
) -> dict[str, Any]:
    tender_top = rank_chunks_for_job(job, score_points, tender_chunks, top_k)
    company_top = rank_chunks_for_job(job, score_points, company_chunks, top_k)
    return {
        "chapter_id": str(job.get("chapter_id", "")),
        "tender_top_chunks": [
            {"id": c["id"], "rank_score": c["rank_score"], "rank_reasons": c["rank_reasons"]}
            for c in tender_top
        ],
        "company_top_chunks": [
            {"id": c["id"], "rank_score": c["rank_score"], "rank_reasons": c["rank_reasons"]}
            for c in company_top
        ],
    }
