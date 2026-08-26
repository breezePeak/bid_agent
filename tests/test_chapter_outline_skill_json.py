from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.chapter_outline_skill import (  # noqa: E402
    build_chapter_outline_from_payload,
)


def _ledger() -> dict[str, object]:
    return {
        "requirements": [
            {"requirement_id": "REQ-A", "status": "confirmed"},
            {"requirement_id": "REQ-B", "status": "confirmed"},
        ]
    }


def _condition(
    condition_id: str,
    subject: str,
    objective: str,
    *,
    role: str = "content",
) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "subject": subject,
        "normalized_condition": objective,
        "response_intent": objective,
        "condition_role": role,
        "review_status": "confirmed",
    }


def _point(
    point_id: str,
    unit_id: str,
    requirement_id: str,
    conditions: list[dict[str, object]],
) -> dict[str, object]:
    path = ["履约体系（18分）", "资源编排（9分）"]
    return {
        "score_point_id": point_id,
        "group_id": "GROUP-A",
        "title": path[-1],
        "outline_path": path,
        "review_status": "confirmed",
        "score_conditions": conditions,
        "response_units": [
            {
                "unit_id": unit_id,
                "title": path[-1],
                "outline_path": path,
                "condition_ids": [item["condition_id"] for item in conditions],
                "linked_requirement_ids": [requirement_id],
                "response_scope": "section",
                "response_expectation": "覆盖本评分单元的全部要求",
                "review_status": "confirmed",
                "confidence": 1.0,
            }
        ],
    }


def _score_model() -> dict[str, object]:
    return {
        "groups": [
            {
                "group_id": "GROUP-A",
                "title": "综合响应（30分）",
            }
        ],
        "points": [
            _point(
                "POINT-A",
                "UNIT-A",
                "REQ-A",
                [
                    _condition("COND-A1", "资源调度机制", "资源调度机制完整"),
                    _condition("COND-A2", "资源调度机制", "资源调度机制可执行"),
                    _condition("COND-A3", "异常恢复流程", "异常恢复流程清晰"),
                    _condition("COND-A4", "资源编排", "资源编排说明充分"),
                ],
            ),
            _point(
                "POINT-B",
                "UNIT-B",
                "REQ-B",
                [
                    _condition("COND-B1", "资源调度机制", "资源调度机制可验证"),
                ],
            ),
        ],
    }


def test_structured_subjects_define_and_merge_business_nodes() -> None:
    candidate = build_chapter_outline_from_payload(_ledger(), _score_model())
    by_title = {node.title: node for node in candidate.nodes}

    assert [node.title for node in candidate.nodes] == [
        "综合响应（30分）",
        "履约体系（18分）",
        "资源编排（9分）",
        "资源调度机制",
        "异常恢复流程",
    ]
    assert by_title["资源调度机制"].score_condition_ids == [
        "COND-A1",
        "COND-A2",
        "COND-B1",
    ]
    assert by_title["资源调度机制"].supporting_response_unit_ids == [
        "UNIT-A",
        "UNIT-B",
    ]
    assert by_title["资源编排（9分）"].score_condition_ids == ["COND-A4"]
    assert by_title["资源编排（9分）"].primary_response_unit_ids == [
        "UNIT-A",
        "UNIT-B",
    ]
    assert not any("（2）" in node.title for node in candidate.nodes)


def test_stable_ids_depend_on_path_and_business_object() -> None:
    first = build_chapter_outline_from_payload(_ledger(), _score_model())
    regenerated = build_chapter_outline_from_payload(
        deepcopy(_ledger()),
        deepcopy(_score_model()),
    )

    assert [node.local_id for node in regenerated.nodes] == [
        node.local_id for node in first.nodes
    ]
    assert regenerated == first


def test_missing_subject_stays_on_factor_and_requires_review() -> None:
    score_model = _score_model()
    point = score_model["points"][0]
    point["score_conditions"] = [
        _condition("COND-EMPTY", "", "说明完整且可核验")
    ]
    point["response_units"][0]["condition_ids"] = ["COND-EMPTY"]
    score_model["points"] = [point]

    candidate = build_chapter_outline_from_payload(_ledger(), score_model)

    assert candidate.review_status == "needs_review"
    assert candidate.nodes[-1].title == "资源编排（9分）"
    assert candidate.nodes[-1].score_condition_ids == ["COND-EMPTY"]
    assert candidate.nodes[-1].needs_human is True
    assert all("满分条件" not in node.title for node in candidate.nodes)
