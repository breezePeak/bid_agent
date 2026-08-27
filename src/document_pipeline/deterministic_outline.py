"""Compatibility entry point for the authoritative chapter-outline Skill."""

from __future__ import annotations

from .chapter_outline_skill import build_chapter_outline
from .contracts import RequirementLedger, ScoreModel, TemplateStructureContract
from .planning_inference import ChapterOutlineCandidate
from .scoring_outline_policy import active_planning_requirement_ids


def build_deterministic_outline_candidate(
    ledger: RequirementLedger,
    scores: ScoreModel,
    template_structure: TemplateStructureContract | None,
) -> ChapterOutlineCandidate:
    """Delegate historical callers to ``planning.chapter_outline_split``."""

    planning_requirement_ids = active_planning_requirement_ids(
        ledger,
        scores,
    )
    planning_ledger = ledger.model_copy(
        update={
            "requirements": [
                item
                for item in ledger.requirements
                if item.requirement_id in planning_requirement_ids
            ]
        }
    )
    return build_chapter_outline(
        planning_ledger,
        scores,
        template_structure,
    )
