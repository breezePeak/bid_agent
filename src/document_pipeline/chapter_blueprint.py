from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import (
    ChapterBlueprint,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreModel,
    TemplateStructureContract,
)
from .scoring_outline_policy import audit_chapter_blueprint as _audit_chapter_blueprint


CHAPTER_BLUEPRINT_REVIEW_ONLY_AUDIT_CODES = frozenset(
    {
        "HOLLOW_QUALITY_HEADING",
        "EVALUATIVE_SENTENCE_HEADING",
        "MISSING_SUBJECT_HEADING",
        "RESPONSE_UNIT_PRIMARY_CARDINALITY",
        "SCORE_CONDITION_MULTIPLE_BINDINGS",
        "SCORE_CONDITION_COVERAGE_MISSING",
        "REQUIREMENT_COVERAGE_MISSING",
        "UNIT_REQUIREMENT_OUTSIDE_PRIMARY_SUBTREE",
        "SCORE_CONDITION_OUTSIDE_PRIMARY_SUBTREE",
        "QUALITY_CONDITION_STANDALONE_CHAPTER",
        "QUALITY_CONDITION_OBJECTIVE_MISSING",
        "DOCUMENT_QUALITY_GATE_CARDINALITY",
        "DOCUMENT_QUALITY_GATE_UNIT_MISMATCH",
        "DOCUMENT_QUALITY_SCORE_MISMATCH",
        "DOCUMENT_QUALITY_CONDITION_MISMATCH",
        "DOCUMENT_QUALITY_REQUIREMENT_MISMATCH",
        "DOCUMENT_QUALITY_CRITERIA_MISMATCH",
        "DOCUMENT_QUALITY_CHECK_ITEMS_MISMATCH",
        "SCORE_GROUP_POINTS_SUMMARY_MISMATCH",
        "SCORE_GROUP_ROOT_CARDINALITY",
        "SCORE_GROUP_ROOT_ORDER_MISMATCH",
        "SCORE_GROUP_ROOT_MISSING_OR_MIXED",
        "SCORE_GROUP_ROOT_TITLE_MISMATCH",
        "OUTLINE_PATH_HIERARCHY_MISSING",
    }
)


def partition_chapter_blueprint_audit(
    audit: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split G2 findings into hard integrity failures and review-only semantics."""

    findings = audit.get("findings")
    if not isinstance(findings, list):
        return (
            [
                {
                    "code": "BLUEPRINT_AUDIT_RESULT_INVALID",
                    "message": "ChapterBlueprint 审核未返回 findings 数组",
                }
            ],
            [],
        )
    blocking: list[dict[str, object]] = []
    review_only: list[dict[str, object]] = []
    for raw_finding in findings:
        if not isinstance(raw_finding, dict):
            blocking.append(
                {
                    "code": "BLUEPRINT_AUDIT_FINDING_INVALID",
                    "message": str(raw_finding),
                }
            )
            continue
        finding = dict(raw_finding)
        target = (
            review_only
            if str(finding.get("code") or "")
            in CHAPTER_BLUEPRINT_REVIEW_ONLY_AUDIT_CODES
            else blocking
        )
        target.append(finding)
    if not bool(audit.get("passed")) and not blocking and not review_only:
        blocking.append(
            {
                "code": "BLUEPRINT_AUDIT_FAILED_WITHOUT_FINDINGS",
                "message": "ChapterBlueprint 审核失败但未给出可分类 finding",
            }
        )
    return blocking, review_only


def audit_chapter_blueprint(
    blueprint: ChapterBlueprint,
    planning_input: ResponseTopicGraph | RequirementLedger,
    score_model: ScoreModel | None = None,
    template_structure: TemplateStructureContract | None = None,
) -> dict[str, object]:
    """Run BidAgent's deterministic G2 scoring/outline integrity policy."""

    return _audit_chapter_blueprint(
        blueprint,
        planning_input,
        score_model=score_model,
        template_structure=template_structure,
    )


def load_promoted_chapter_blueprint(context: WorkspaceContext) -> ChapterBlueprint:
    artifact = ControlStore(context).v3_active_artifact("ChapterBlueprint")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ChapterBlueprint 尚未晋级。", status_code=409)
    blueprint = ChapterBlueprint.model_validate(artifact["payload"])
    if blueprint.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ChapterBlueprint revision 与晋级记录不一致。", status_code=409)
    return blueprint
