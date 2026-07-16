from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import project_root, read_json, stringify, write_json, write_text

# 覆盖档位 → 预估得分系数（基于章节审核 coverage_level，非官方评标）
COVERAGE_RATE = {
    "high": 0.95,
    "medium": 0.70,
    "low": 0.35,
    "none": 0.0,
    "unknown": 0.0,
}
LEVEL_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3, "unknown": 4}


def _as_float(value: Any) -> float | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = stringify(value).strip().replace("分", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _best_coverage_level(row: dict[str, Any]) -> str:
    levels = [stringify(level).lower() for level in (row.get("coverage_levels") or []) if stringify(level)]
    llm_level = "none"
    if not levels and not bool(row.get("covered")):
        llm_level = "none"
    elif not levels:
        llm_level = "unknown"
    else:
        llm_level = min(levels, key=lambda level: LEVEL_RANK.get(level, 9))

    # 硬指标更严：取 LLM 与 hard level_hint 中更差的一档
    hard = row.get("hard_metrics") if isinstance(row.get("hard_metrics"), dict) else {}
    hard_level = stringify(hard.get("level_hint")).lower() or ""
    if hard_level in LEVEL_RANK:
        if LEVEL_RANK.get(hard_level, 9) > LEVEL_RANK.get(llm_level, 9):
            return hard_level
    if hard_level == "none" and llm_level in {"high", "medium"}:
        return "low"
    return llm_level


def _rate_for_level(level: str) -> float:
    return COVERAGE_RATE.get(level.lower(), 0.0)


def _confidence_for_row(row: dict[str, Any], full_score: float | None, estimated: float) -> str:
    if full_score is None:
        return "unscored"
    if not row.get("review_coverage"):
        return "low"
    if not row.get("bound_chapters"):
        return "low"
    level = _best_coverage_level(row)
    if level == "high" and estimated >= full_score * 0.9:
        return "high"
    if level in {"high", "medium"}:
        return "medium"
    return "low"


def estimate_final_score(root: Path | None = None) -> Path:
    """根据评分点分值 × 覆盖档位，估算生成标书的技术/商务得分区间。"""
    root = root or project_root()
    matrix_path = root / "workspace" / "score_coverage_matrix.json"
    if not matrix_path.exists():
        raise FileNotFoundError(f"缺少覆盖矩阵: {matrix_path}，请先执行 build-score-coverage")

    matrix = read_json(matrix_path)
    if not isinstance(matrix, dict):
        raise ValueError("score_coverage_matrix.json 必须是 JSON 对象")

    rows = matrix.get("matrix") if isinstance(matrix.get("matrix"), list) else []
    items: list[dict[str, Any]] = []
    total_full = 0.0
    total_estimated = 0.0
    total_optimistic = 0.0
    total_conservative = 0.0
    scored_count = 0
    unscored_ids: list[str] = []
    by_category: dict[str, dict[str, float]] = {}
    by_level = {"high": 0, "medium": 0, "low": 0, "none": 0, "unknown": 0}

    for row in rows:
        if not isinstance(row, dict):
            continue
        score_point_id = stringify(row.get("score_point_id"))
        category = stringify(row.get("category")) or "unknown"
        full_score = _as_float(row.get("score"))
        best_level = _best_coverage_level(row)
        by_level[best_level] = by_level.get(best_level, 0) + 1
        rate = _rate_for_level(best_level)
        # 乐观/保守：在基准系数上下浮动
        optimistic_rate = min(1.0, rate + 0.05) if rate > 0 else 0.0
        conservative_rate = max(0.0, rate - 0.15) if rate > 0 else 0.0

        estimated = round(full_score * rate, 2) if full_score is not None else None
        optimistic = round(full_score * optimistic_rate, 2) if full_score is not None else None
        conservative = round(full_score * conservative_rate, 2) if full_score is not None else None

        if full_score is None:
            unscored_ids.append(score_point_id)
        else:
            scored_count += 1
            total_full += full_score
            total_estimated += estimated or 0.0
            total_optimistic += optimistic or 0.0
            total_conservative += conservative or 0.0
            bucket = by_category.setdefault(category, {"full_score": 0.0, "estimated_score": 0.0, "count": 0.0})
            bucket["full_score"] += full_score
            bucket["estimated_score"] += estimated or 0.0
            bucket["count"] += 1

        items.append(
            {
                "score_point_id": score_point_id,
                "score_point_title": stringify(row.get("score_point_title")),
                "category": category,
                "full_score": full_score,
                "best_coverage_level": best_level,
                "coverage_rate": rate,
                "estimated_score": estimated,
                "optimistic_score": optimistic,
                "conservative_score": conservative,
                "risk_level": stringify(row.get("risk_level")),
                "bound_chapter_ids": [
                    stringify(item.get("chapter_id"))
                    for item in (row.get("bound_chapters") or [])
                    if isinstance(item, dict) and stringify(item.get("chapter_id"))
                ],
                "confidence": _confidence_for_row(row, full_score, estimated or 0.0),
                "suggestion": _item_suggestion(row, best_level, full_score),
            }
        )

    percent = round((total_estimated / total_full) * 100, 1) if total_full > 0 else 0.0
    grade = _grade(percent)
    lost_points = round(total_full - total_estimated, 2)
    top_losses = sorted(
        [item for item in items if item.get("full_score") is not None],
        key=lambda item: (item.get("full_score") or 0) - (item.get("estimated_score") or 0),
        reverse=True,
    )[:8]

    report = {
        "method": "coverage_weighted_estimate",
        "disclaimer": (
            "本结果为系统根据「评分点分值 × 章节审核覆盖档位」估算的参考分，"
            "不是评标委员会正式打分；价格分、主观分、资格符合性未完整建模。"
        ),
        "coverage_rates": COVERAGE_RATE,
        "summary": {
            "score_point_count": len(items),
            "scored_point_count": scored_count,
            "unscored_point_count": len(unscored_ids),
            "full_score_total": round(total_full, 2),
            "estimated_score_total": round(total_estimated, 2),
            "optimistic_score_total": round(total_optimistic, 2),
            "conservative_score_total": round(total_conservative, 2),
            "estimated_percent": percent,
            "lost_points": lost_points,
            "grade": grade,
            "coverage_level_counts": by_level,
        },
        "by_category": {
            key: {
                "full_score": round(value["full_score"], 2),
                "estimated_score": round(value["estimated_score"], 2),
                "count": int(value["count"]),
                "estimated_percent": round((value["estimated_score"] / value["full_score"]) * 100, 1)
                if value["full_score"]
                else 0.0,
            }
            for key, value in sorted(by_category.items())
        },
        "unscored_score_points": unscored_ids,
        "top_score_losses": [
            {
                "score_point_id": item["score_point_id"],
                "score_point_title": item["score_point_title"],
                "full_score": item["full_score"],
                "estimated_score": item["estimated_score"],
                "lost_points": round((item["full_score"] or 0) - (item["estimated_score"] or 0), 2),
                "best_coverage_level": item["best_coverage_level"],
                "suggestion": item["suggestion"],
            }
            for item in top_losses
            if ((item.get("full_score") or 0) - (item.get("estimated_score") or 0)) > 0.01
        ],
        "items": items,
    }

    output_path = root / "workspace" / "final_score_estimate.json"
    write_json(output_path, report)

    md_path = root / "outputs" / "score_estimate.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(md_path, _to_markdown(report))

    summary = report["summary"]
    print(
        f"[完成] 终稿估分: {summary['estimated_score_total']}/{summary['full_score_total']} "
        f"({summary['estimated_percent']}%, {summary['grade']}) → {output_path}"
    )
    print(f"[完成] 估分报告: {md_path}")
    if unscored_ids:
        print(f"[提示] {len(unscored_ids)} 个评分点无分值，未计入总分: {unscored_ids[:8]}")
    return output_path


def _item_suggestion(row: dict[str, Any], level: str, full_score: float | None) -> str:
    if full_score is None:
        return "评分点未解析出分值，请检查 inputs/score.md 解析结果。"
    if level in {"none", "unknown"}:
        return "当前未有效覆盖，优先补写响应并绑定到章节。"
    if level == "low":
        return "覆盖偏弱，补充具体方案细节、指标与可核验证据。"
    if level == "medium":
        return "基本覆盖，可再强化针对性表述与证明材料引用。"
    return "覆盖较好，保持证据链与事实一致性即可。"


def _grade(percent: float) -> str:
    if percent >= 90:
        return "A"
    if percent >= 80:
        return "B"
    if percent >= 70:
        return "C"
    if percent >= 60:
        return "D"
    return "E"


def _to_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# 标书终稿估分报告",
        "",
        f"> {report.get('disclaimer', '')}",
        "",
        "## 总览",
        "",
        f"- **满分合计**: {summary.get('full_score_total')}",
        f"- **预估得分**: {summary.get('estimated_score_total')}",
        f"- **预估得分率**: {summary.get('estimated_percent')}%",
        f"- **保守 / 乐观**: {summary.get('conservative_score_total')} / {summary.get('optimistic_score_total')}",
        f"- **等级**: {summary.get('grade')}",
        f"- **预计失分**: {summary.get('lost_points')}",
        f"- **有分值评分点**: {summary.get('scored_point_count')} / {summary.get('score_point_count')}",
        "",
        "## 覆盖档位统计",
        "",
    ]
    counts = summary.get("coverage_level_counts") or {}
    for level in ("high", "medium", "low", "none", "unknown"):
        lines.append(f"- {level}: {counts.get(level, 0)}")

    lines.extend(["", "## 分类得分", ""])
    for category, payload in (report.get("by_category") or {}).items():
        lines.append(
            f"- **{category}**: {payload.get('estimated_score')}/{payload.get('full_score')} "
            f"({payload.get('estimated_percent')}%)"
        )

    lines.extend(["", "## 主要失分项", ""])
    losses = report.get("top_score_losses") or []
    if not losses:
        lines.append("- 无明显失分项")
    else:
        for item in losses:
            lines.append(
                f"- `{item.get('score_point_id')}` {item.get('score_point_title')}："
                f"失分 {item.get('lost_points')}（{item.get('best_coverage_level')}）—"
                f"{item.get('suggestion')}"
            )

    lines.extend(
        [
            "",
            "## 系数说明",
            "",
            f"- high → {COVERAGE_RATE['high']}",
            f"- medium → {COVERAGE_RATE['medium']}",
            f"- low → {COVERAGE_RATE['low']}",
            f"- none/unknown → {COVERAGE_RATE['none']}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
