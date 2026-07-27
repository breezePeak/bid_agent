from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import RequirementLedger, ScoreModel, SourceBlock


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
) -> dict[str, object]:
    """Deterministically ensure ScoreModel only cites frozen scoring sources and Requirement IDs."""
    scoring_anchors = {
        (block.source_anchor.source_input_id, block.source_anchor.chunk_id)
        for block in source_blocks
        if block.input_role.value == "score"
        or (block.input_role.value == "amendment" and any(token in block.content for token in ("评分", "评审", "得分", "分值", "废标", "否决")))
    }
    requirements = {item.requirement_id: item for item in requirement_ledger.requirements}
    invalid_anchor_ids: list[str] = []
    unknown_requirement_ids: list[str] = []
    mismatched_requirement_ids: list[str] = []
    unlinked_score_point_ids: list[str] = []
    bulk_linked_score_point_ids: list[str] = []
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
    return {
        "passed": not any((invalid_anchor_ids, unknown_requirement_ids, mismatched_requirement_ids, unlinked_score_point_ids, bulk_linked_score_point_ids)),
        "invalid_anchor_score_point_ids": invalid_anchor_ids,
        "unknown_requirement_ids": sorted(set(unknown_requirement_ids)),
        "mismatched_requirement_ids": sorted(set(mismatched_requirement_ids)),
        "unlinked_score_point_ids": unlinked_score_point_ids,
        "bulk_linked_score_point_ids": bulk_linked_score_point_ids,
    }
