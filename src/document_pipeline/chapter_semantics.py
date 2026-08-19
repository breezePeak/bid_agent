"""Read-only semantic projection for a chapter workspace."""

from __future__ import annotations

from typing import Any

from control_plane import WorkspaceContext


def load_chapter_project_context(context: WorkspaceContext) -> dict[str, Any]:
    """Load the promoted project facts used by chapter collaboration."""
    from .global_project_context import GlobalProjectContextService

    return GlobalProjectContextService(context).load()


def project_chapter_semantic_requirements(
    context: WorkspaceContext,
    chapter: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve blueprint IDs to their tender and scoring text."""
    from .requirement_ledger import load_promoted_requirement_ledger
    from .score_model import load_promoted_score_model

    node = chapter.get("blueprint_node")
    node = node if isinstance(node, dict) else {}
    requirement_ids = {str(item) for item in node.get("requirement_ids") or []}
    score_ids = {str(item) for item in node.get("score_point_ids") or []}
    condition_ids = {str(item) for item in node.get("score_condition_ids") or []}
    ledger = load_promoted_requirement_ledger(context)
    scores = load_promoted_score_model(context)
    requirements = [
        {
            "requirement_id": item.requirement_id,
            "text": str(item.normalized_requirement or ""),
            "severity": item.severity,
        }
        for item in ledger.requirements
        if item.requirement_id in requirement_ids
    ]
    scoring: list[dict[str, Any]] = []
    for point in scores.points:
        selected_conditions = [
            condition.model_dump(mode="json")
            for condition in point.score_conditions
            if condition.condition_id in condition_ids
        ]
        if point.score_point_id in score_ids or selected_conditions:
            scoring.append(
                {
                    "score_point_id": point.score_point_id,
                    "title": point.title,
                    "response_expectation": point.response_expectation,
                    "conditions": selected_conditions,
                }
            )
    return requirements, scoring


__all__ = [
    "load_chapter_project_context",
    "project_chapter_semantic_requirements",
]
