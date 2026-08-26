"""Compatibility entry point for the authoritative chapter-outline Skill."""

from __future__ import annotations

from .chapter_outline_skill import build_chapter_outline
from .contracts import RequirementLedger, ScoreModel, TemplateStructureContract
from .planning_inference import ChapterOutlineCandidate


def build_deterministic_outline_candidate(
    ledger: RequirementLedger,
    scores: ScoreModel,
    template_structure: TemplateStructureContract | None,
) -> ChapterOutlineCandidate:
    """Delegate historical callers to ``planning.chapter_outline_split``."""

    return build_chapter_outline(ledger, scores, template_structure)
