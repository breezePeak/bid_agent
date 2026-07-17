from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from global_reviewer import GLOBAL_REVIEW_CONTEXT_MAX_CHARS, _build_global_review_user_content


def test_global_review_context_is_bounded_and_keeps_risk_summary() -> None:
    summaries = [
        {
            "chapter_id": str(index),
            "chapter_title": f"章节 {index}",
            "project_names": ["测试项目"],
            "bidder_names": ["测试公司"],
            "service_periods": ["一年"],
            "warranty_periods": ["两年"],
            "main_claims": ["不应原样进入全文审核上下文" * 200],
            "possible_conflicts": ["期限表述待核对"] if index == 3 else [],
            "fabrication_risks": [],
            "risks": [],
            "need_manual_review": index == 3,
        }
        for index in range(1, 201)
    ]
    content = _build_global_review_user_content(
        global_facts={"project_name": "测试项目"},
        outline={
            "chapters": [
                {"id": str(index), "title": f"章节 {index}", "parent_id": "", "score_point_ids": []}
                for index in range(1, 201)
            ]
        },
        score_points=[
            {"id": "S001", "title": "风险项", "category": "technical", "requirement": "必须响应"}
        ],
        reviews=[
            {
                "chapter_id": "3",
                "chapter_title": "章节 3",
                "need_rewrite": True,
                "problems": ["期限不一致"],
            }
        ],
        score_coverage_matrix={
            "summary": {"score_point_count": 1, "uncovered_score_point_count": 1},
            "uncovered_score_points": ["S001"],
            "weak_score_points": [],
        },
        source_trace_index={"summary": {"chapter_count": 200}, "missing_chapters": []},
        review_summary={"total_pending": 1},
        summaries=summaries,
        chapters_data="",
        chapters_section_label="## 章节摘要\n\n",
    )

    assert len(content) <= GLOBAL_REVIEW_CONTEXT_MAX_CHARS
    assert "期限表述待核对" in content
    assert "S001" in content
    assert "不应原样进入全文审核上下文" not in content
