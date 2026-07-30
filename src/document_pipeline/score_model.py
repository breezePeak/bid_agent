from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import RequirementLedger, ScoreModel, SourceBlock
from .score_semantic import (
    full_level_ids_for_unit,
    highest_band_fallback_text,
    semantic_coverage_text,
    uncovered_semantic_source_text,
)
from .scoring_sources import scoring_source_anchor_keys


SCORE_MODEL_REVIEW_ONLY_AUDIT_FIELDS = frozenset(
    {
        # These are semantic completeness/quality findings.  A schema-valid,
        # source-grounded model can still drive a conservative outline while a
        # human reviews the interpretation.
        "unlinked_score_point_ids",
        "bulk_linked_score_point_ids",
        "semantic_incomplete_score_point_ids",
        "invalid_response_unit_level_score_point_ids",
        "incomplete_response_unit_requirement_score_point_ids",
        "incomplete_response_unit_evidence_score_point_ids",
        "incomplete_condition_coverage_score_point_ids",
    }
)


def partition_score_model_audit(
    audit: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Split deterministic audit findings into blocking integrity and review hints.

    Source/ID/schema integrity remains fail-closed.  Interpretation coverage and
    response-planning quality are explicitly review-only so the pipeline can
    continue with a visible warning.
    """

    blocking: dict[str, object] = {}
    review_only: dict[str, object] = {}
    for key, value in audit.items():
        if key == "passed" or not value:
            continue
        target = (
            review_only
            if key in SCORE_MODEL_REVIEW_ONLY_AUDIT_FIELDS
            else blocking
        )
        target[key] = value
    return blocking, review_only


def load_promoted_score_model(context: WorkspaceContext) -> ScoreModel:
    """Return the only runtime ScoreModel: the active promoted revision."""
    artifact = ControlStore(context).v3_active_artifact("ScoreModel")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ScoreModel 尚未晋级。", status_code=409)
    model = ScoreModel.model_validate(artifact["payload"])
    if model.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ScoreModel revision 与晋级记录不一致。", status_code=409)
    return model


def audit_score_model(
    model: ScoreModel,
    requirement_ledger: RequirementLedger,
    source_blocks: list[SourceBlock],
    *,
    require_semantic: bool = False,
) -> dict[str, object]:
    """Deterministically ensure ScoreModel only cites frozen scoring sources and Requirement IDs."""
    scoring_anchors = scoring_source_anchor_keys(source_blocks)
    source_blocks_by_anchor = {
        (
            block.source_anchor.source_input_id,
            block.source_anchor.chunk_id,
        ): block
        for block in source_blocks
    }
    requirements = {item.requirement_id: item for item in requirement_ledger.requirements}
    invalid_anchor_ids: list[str] = []
    unknown_requirement_ids: list[str] = []
    unknown_context_requirement_ids: list[str] = []
    inactive_context_requirement_ids: list[str] = []
    mismatched_requirement_ids: list[str] = []
    unlinked_score_point_ids: list[str] = []
    bulk_linked_score_point_ids: list[str] = []
    semantic_incomplete_score_point_ids: list[str] = []
    invalid_condition_source_ids: list[str] = []
    invalid_condition_identity_ids: list[str] = []
    duplicate_condition_ids: list[str] = []
    invalid_response_unit_level_ids: list[str] = []
    incomplete_response_unit_requirement_score_point_ids: list[str] = []
    incomplete_response_unit_evidence_score_point_ids: list[str] = []
    incomplete_condition_coverage_score_point_ids: list[str] = []
    seen_condition_ids: set[str] = set()
    for point in model.points:
        point_anchors = {(anchor.source_input_id, anchor.chunk_id) for anchor in point.source_anchors}
        if not point_anchors <= scoring_anchors:
            invalid_anchor_ids.append(point.score_point_id)
        if not point.linked_requirement_ids:
            unlinked_score_point_ids.append(point.score_point_id)
        if len(point.linked_requirement_ids) > 1:
            bulk_linked_score_point_ids.append(point.score_point_id)
        for requirement_id in point.linked_requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None:
                unknown_requirement_ids.append(requirement_id)
            elif (requirement.source_anchor.source_input_id, requirement.source_anchor.chunk_id) not in point_anchors:
                mismatched_requirement_ids.append(requirement_id)
        for requirement_id in point.context_requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None:
                unknown_context_requirement_ids.append(requirement_id)
            elif requirement.status in {"blocked", "waived"}:
                inactive_context_requirement_ids.append(requirement_id)
        normalized_sources = {
            "".join(text.split())
            for text in (
                point.criterion,
                *(level.criterion for level in point.scoring_levels),
            )
        }
        condition_ids = {
            condition.condition_id for condition in point.score_conditions
        }
        referenced_condition_ids = [
            condition_id
            for unit in point.response_units
            for condition_id in unit.condition_ids
        ]
        if require_semantic and not point.disqualifying:
            if (
                not point.score_conditions
                or not point.response_units
                or set(referenced_condition_ids) != condition_ids
                or len(referenced_condition_ids)
                != len(set(referenced_condition_ids))
            ):
                semantic_incomplete_score_point_ids.append(
                    point.score_point_id
                )
        if require_semantic:
            expected_requirement_ids = {
                *point.context_requirement_ids,
                *(
                    requirement_id
                    for requirement_id in point.linked_requirement_ids
                    for requirement in [requirements.get(requirement_id)]
                    if (
                        requirement is not None
                        and getattr(
                            requirement.kind,
                            "value",
                            requirement.kind,
                        )
                        != "score"
                    )
                ),
            }
            assigned_requirement_ids = {
                requirement_id
                for unit in point.response_units
                for requirement_id in unit.linked_requirement_ids
            }
            if assigned_requirement_ids != expected_requirement_ids:
                incomplete_response_unit_requirement_score_point_ids.append(
                    point.score_point_id
                )
            conditions_by_id_for_evidence = {
                condition.condition_id: condition
                for condition in point.score_conditions
            }
            for unit in point.response_units:
                explicit_condition_evidence_types = {
                    evidence_type
                    for condition_id in unit.condition_ids
                    for condition in [
                        conditions_by_id_for_evidence.get(condition_id)
                    ]
                    if condition is not None
                    for evidence_type in (
                        getattr(condition, "required_evidence_types", None)
                        or []
                    )
                }
                if not explicit_condition_evidence_types <= set(
                    unit.required_evidence_types
                ):
                    incomplete_response_unit_evidence_score_point_ids.append(
                        point.score_point_id
                    )
                    break
        known_level_ids = {
            f"{point.score_point_id}-L{index:02d}"
            for index, _ in enumerate(point.scoring_levels, start=1)
        }
        levels_by_id = {
            f"{point.score_point_id}-L{index:02d}": level
            for index, level in enumerate(point.scoring_levels, start=1)
        }
        level_points = {
            level_id: level.points
            for level_id, level in levels_by_id.items()
        }
        level_orders = {
            f"{point.score_point_id}-L{index:02d}": index - 1
            for index, _ in enumerate(point.scoring_levels, start=1)
        }
        for condition in point.score_conditions:
            if condition.condition_id in seen_condition_ids:
                duplicate_condition_ids.append(condition.condition_id)
            seen_condition_ids.add(condition.condition_id)
            condition_prefix = f"{point.score_point_id}-C-"
            condition_suffix = (
                condition.condition_id.removeprefix(condition_prefix)
                if condition.condition_id.startswith(condition_prefix)
                else ""
            )
            if require_semantic and (
                len(condition_suffix) != 12
                or any(
                    token not in "0123456789abcdef"
                    for token in condition_suffix.lower()
                )
                or condition.source_anchor is None
            ):
                invalid_condition_identity_ids.append(
                    condition.condition_id
                )
            normalized_excerpt = "".join(
                condition.source_excerpt.split()
            )
            condition_source_block = (
                source_blocks_by_anchor.get(
                    (
                        condition.source_anchor.source_input_id,
                        condition.source_anchor.chunk_id,
                    )
                )
                if condition.source_anchor is not None
                else None
            )
            exact_span_is_valid = True
            level_source_is_valid = True
            if require_semantic:
                exact_span_is_valid = (
                    condition_source_block is not None
                    and condition.source_anchor
                    == condition_source_block.source_anchor
                    and condition.source_span_start is not None
                    and condition.source_span_end is not None
                    and condition.source_span_end
                    <= len(condition_source_block.content)
                    and condition_source_block.content[
                        condition.source_span_start : condition.source_span_end
                    ]
                    == condition.source_excerpt
                )
                if condition.source_level_id is not None:
                    source_level = levels_by_id.get(
                        condition.source_level_id
                    )
                    level_source_is_valid = (
                        source_level is not None
                        and normalized_excerpt
                        in "".join(source_level.criterion.split())
                    )
                else:
                    level_source_is_valid = (
                        normalized_excerpt
                        in "".join(point.criterion.split())
                    )
            if (
                not normalized_excerpt
                or not any(
                    normalized_excerpt in source
                    for source in normalized_sources
                )
                or (
                    condition.source_anchor is not None
                    and (
                        condition.source_anchor.source_input_id,
                        condition.source_anchor.chunk_id,
                    )
                    not in point_anchors
                )
                or (
                    condition.source_level_id is not None
                    and condition.source_level_id not in known_level_ids
                )
                or not exact_span_is_valid
                or not level_source_is_valid
            ):
                invalid_condition_source_ids.append(
                    condition.condition_id
                )
        if require_semantic and not point.disqualifying:
            assigned_level_ids = [
                level_id
                for unit in point.response_units
                for level_id in unit.source_level_ids
            ]
            levels_are_valid = (
                (
                    bool(known_level_ids)
                    and set(assigned_level_ids) == known_level_ids
                    and len(assigned_level_ids)
                    == len(set(assigned_level_ids))
                    and all(
                        unit.source_level_ids
                        for unit in point.response_units
                    )
                )
                or (
                    not known_level_ids
                    and not assigned_level_ids
                )
            )
            if not levels_are_valid:
                invalid_response_unit_level_ids.append(
                    point.score_point_id
                )
            coverage_is_complete = True
            conditions_by_id = {
                condition.condition_id: condition
                for condition in point.score_conditions
            }
            if known_level_ids and levels_are_valid:
                for unit in point.response_units:
                    expected_full_level_ids = full_level_ids_for_unit(
                        level_ids=unit.source_level_ids,
                        level_points=level_points,
                        level_orders=level_orders,
                    )
                    unit_conditions = [
                        conditions_by_id[condition_id]
                        for condition_id in unit.condition_ids
                        if condition_id in conditions_by_id
                    ]
                    meaningful_full_level_ids = {
                        level_id
                        for level_id in expected_full_level_ids
                        if semantic_coverage_text(
                            levels_by_id[level_id].criterion
                        )
                    }
                    uses_raw_highest_band_fallback = (
                        bool(expected_full_level_ids)
                        and not meaningful_full_level_ids
                    )
                    if uses_raw_highest_band_fallback:
                        if any(
                            condition.source_level_id is not None
                            for condition in unit_conditions
                        ):
                            coverage_is_complete = False
                        if uncovered_semantic_source_text(
                            highest_band_fallback_text(point.criterion),
                            [
                                condition.source_excerpt
                                for condition in unit_conditions
                            ],
                        ):
                            coverage_is_complete = False
                    elif any(
                        condition.source_level_id
                        not in meaningful_full_level_ids
                        for condition in unit_conditions
                    ):
                        coverage_is_complete = False
                    for level_id in meaningful_full_level_ids:
                        excerpts = [
                            condition.source_excerpt
                            for condition in unit_conditions
                            if condition.source_level_id == level_id
                        ]
                        if uncovered_semantic_source_text(
                            levels_by_id[level_id].criterion,
                            excerpts,
                        ):
                            coverage_is_complete = False
            elif not known_level_ids:
                if any(
                    condition.source_level_id is not None
                    for condition in point.score_conditions
                ):
                    coverage_is_complete = False
                if uncovered_semantic_source_text(
                    point.criterion,
                    [
                        condition.source_excerpt
                        for condition in point.score_conditions
                    ],
                ):
                    coverage_is_complete = False
            else:
                coverage_is_complete = False
            if not coverage_is_complete:
                incomplete_condition_coverage_score_point_ids.append(
                    point.score_point_id
                )
    return {
        "passed": not any(
            (
                invalid_anchor_ids,
                unknown_requirement_ids,
                unknown_context_requirement_ids,
                inactive_context_requirement_ids,
                mismatched_requirement_ids,
                unlinked_score_point_ids,
                bulk_linked_score_point_ids,
                semantic_incomplete_score_point_ids,
                invalid_condition_source_ids,
                invalid_condition_identity_ids,
                duplicate_condition_ids,
                invalid_response_unit_level_ids,
                incomplete_response_unit_requirement_score_point_ids,
                incomplete_response_unit_evidence_score_point_ids,
                incomplete_condition_coverage_score_point_ids,
            )
        ),
        "invalid_anchor_score_point_ids": invalid_anchor_ids,
        "unknown_requirement_ids": sorted(set(unknown_requirement_ids)),
        "unknown_context_requirement_ids": sorted(
            set(unknown_context_requirement_ids)
        ),
        "inactive_context_requirement_ids": sorted(
            set(inactive_context_requirement_ids)
        ),
        "mismatched_requirement_ids": sorted(set(mismatched_requirement_ids)),
        "unlinked_score_point_ids": unlinked_score_point_ids,
        "bulk_linked_score_point_ids": bulk_linked_score_point_ids,
        "semantic_incomplete_score_point_ids": semantic_incomplete_score_point_ids,
        "invalid_condition_source_ids": invalid_condition_source_ids,
        "invalid_condition_identity_ids": invalid_condition_identity_ids,
        "duplicate_condition_ids": sorted(
            set(duplicate_condition_ids)
        ),
        "invalid_response_unit_level_score_point_ids": sorted(
            set(invalid_response_unit_level_ids)
        ),
        "incomplete_response_unit_requirement_score_point_ids": sorted(
            set(incomplete_response_unit_requirement_score_point_ids)
        ),
        "incomplete_response_unit_evidence_score_point_ids": sorted(
            set(incomplete_response_unit_evidence_score_point_ids)
        ),
        "incomplete_condition_coverage_score_point_ids": sorted(
            set(incomplete_condition_coverage_score_point_ids)
        ),
    }
