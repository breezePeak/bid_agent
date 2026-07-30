from __future__ import annotations

from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, WorkspaceContext
from utils import write_json

from .chapter_blueprint import load_promoted_chapter_blueprint
from .contracts import InputRole, RequirementKind
from .input_manifest import InputManifestService, V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger
from .score_model import load_promoted_score_model


MATERIAL_REQUIREMENTS_PATH = V3_ROOT / "materials" / "requirements.json"


class MaterialRequirementsSynchronizer:
    """Derive the V3 evidence/material checklist without consulting legacy state."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def sync(self) -> dict[str, object]:
        ledger = load_promoted_requirement_ledger(self.context)
        scores = load_promoted_score_model(self.context)
        try:
            blueprint = load_promoted_chapter_blueprint(self.context)
        except ControlPlaneError as exc:
            if exc.code != "V3_ARTIFACT_NOT_PROMOTED":
                raise
            # Historical callers could synchronize the checklist before
            # planning.  Keep the old unit-level item, but make its missing
            # condition/chapter binding explicit instead of fabricating one.
            blueprint = None
        manifest = InputManifestService(self.context).load()
        company_supplied = any(item.active and item.role is InputRole.COMPANY for item in manifest.inputs)

        qualification_items = [
            {
                "item_type": "qualification_requirement",
                "requirement_id": item.requirement_id,
                "requirement": item.normalized_requirement,
                "severity": item.severity,
                "source_anchor": item.source_anchor.model_dump(mode="json"),
                "requested_role": InputRole.COMPANY.value,
                "status": "provided" if company_supplied else "missing",
            }
            for item in ledger.requirements
            if item.kind is RequirementKind.QUALIFICATION
            and item.status != "waived"
        ]
        score_evidence_items = self._score_evidence_items(
            scores=scores,
            blueprint=blueprint,
            company_supplied=company_supplied,
        )
        items = [*qualification_items, *score_evidence_items]
        needs_review = {
            "requirement_count": sum(
                1
                for item in ledger.requirements
                if item.status in {"open", "blocked"}
            ),
            "score_point_count": sum(
                1
                for point in scores.points
                if point.review_status in {"needs_review", "blocked"}
            ),
            "response_unit_count": sum(
                1
                for point in scores.points
                for unit in point.response_units
                if unit.review_status in {"needs_review", "blocked"}
            ),
        }
        needs_review["total"] = sum(needs_review.values())
        report: dict[str, object] = {
            "schema_version": "v3",
            "revision": max(ledger.revision, scores.revision),
            "company_material_supplied": company_supplied,
            "items": items,
            "summary": {
                "total": len(items),
                "provided": sum(1 for item in items if item["status"] == "provided"),
                "missing": sum(1 for item in items if item["status"] == "missing"),
                "needs_review": needs_review,
            },
        }
        write_json(self.root / MATERIAL_REQUIREMENTS_PATH, report)
        return report

    @staticmethod
    def _score_evidence_items(
        *,
        scores: Any,
        blueprint: Any | None,
        company_supplied: bool,
    ) -> list[dict[str, Any]]:
        nodes = list(getattr(blueprint, "nodes", []) or [])
        nodes_by_condition: dict[str, list[Any]] = {}
        nodes_by_primary_unit: dict[str, list[Any]] = {}
        for node in nodes:
            for condition_id in getattr(
                node,
                "score_condition_ids",
                [],
            ):
                nodes_by_condition.setdefault(
                    str(condition_id),
                    [],
                ).append(node)
            for unit_id in getattr(
                node,
                "primary_response_unit_ids",
                [],
            ):
                nodes_by_primary_unit.setdefault(
                    str(unit_id),
                    [],
                ).append(node)

        items: list[dict[str, Any]] = []
        for point in scores.points:
            if point.review_status == "blocked":
                continue
            conditions = {
                condition.condition_id: condition
                for condition in point.score_conditions
                if condition.review_status != "blocked"
            }
            for unit in point.response_units:
                if unit.review_status == "blocked":
                    continue
                evidence_condition_ids = [
                    condition_id
                    for condition_id in unit.condition_ids
                    if condition_id in conditions
                    and conditions[condition_id].condition_role
                    == "evidence"
                ]
                if (
                    evidence_condition_ids
                    and not unit.required_evidence_types
                ):
                    raise ValueError(
                        "MATERIAL_SCORE_EVIDENCE_TYPE_MISSING: "
                        f"{sorted(evidence_condition_ids)}"
                    )
                bindings: list[tuple[str | None, Any | None]] = [
                    (condition_id, node)
                    for condition_id in evidence_condition_ids
                    for node in nodes_by_condition.get(
                        condition_id,
                        [],
                    )
                ]
                duplicate_condition_bindings = {
                    condition_id
                    for condition_id in evidence_condition_ids
                    if len(
                        nodes_by_condition.get(condition_id, [])
                    )
                    > 1
                }
                if duplicate_condition_bindings:
                    raise ValueError(
                        "MATERIAL_SCORE_EVIDENCE_TARGET_AMBIGUOUS: "
                        f"{sorted(duplicate_condition_bindings)}"
                    )
                if (
                    evidence_condition_ids
                    and not bindings
                    and blueprint is not None
                ):
                    raise ValueError(
                        "MATERIAL_SCORE_EVIDENCE_TARGET_MISSING: "
                        f"{sorted(evidence_condition_ids)}"
                    )
                if evidence_condition_ids and blueprint is None:
                    bindings = [
                        (condition_id, None)
                        for condition_id in evidence_condition_ids
                    ]
                if not bindings:
                    # Backward compatibility for a historical ScoreModel that
                    # only declared evidence at response-unit level.
                    primary_nodes = nodes_by_primary_unit.get(
                        unit.unit_id,
                        [],
                    )
                    bindings = [
                        (None, node)
                        for node in primary_nodes
                    ] or [(None, None)]
                for evidence_type in unit.required_evidence_types:
                    for condition_id, node in bindings:
                        condition = (
                            conditions.get(condition_id)
                            if condition_id
                            else None
                        )
                        chapter_id = (
                            str(node.chapter_id)
                            if node is not None
                            else None
                        )
                        target_node_id = (
                            str(
                                getattr(node, "template_target", None)
                                or node.chapter_id
                            )
                            if node is not None
                            else None
                        )
                        condition_text = (
                            str(
                                condition.normalized_condition
                                or condition.text
                            )
                            if condition is not None
                            else ""
                        )
                        source_anchor = (
                            condition.source_anchor.model_dump(
                                mode="json"
                            )
                            if condition is not None
                            and condition.source_anchor is not None
                            else (
                                point.source_anchors[0].model_dump(
                                    mode="json"
                                )
                                if point.source_anchors
                                else None
                            )
                        )
                        items.append(
                            {
                                "item_type": "score_evidence",
                                "score_point_id": point.score_point_id,
                                "response_unit_id": unit.unit_id,
                                "condition_id": condition_id,
                                "chapter_id": chapter_id,
                                "target_node_id": target_node_id,
                                "evidence_type": evidence_type,
                                "requirement": (
                                    f"{unit.title}："
                                    f"{unit.response_expectation}；"
                                    + (
                                        f"满分条件：{condition_text}；"
                                        if condition_text
                                        else ""
                                    )
                                    + f"需提供 {evidence_type}"
                                ),
                                "severity": (
                                    "blocking"
                                    if point.disqualifying
                                    else "major"
                                ),
                                "source_anchor": source_anchor,
                                "requested_role": (
                                    InputRole.COMPANY.value
                                ),
                                "status": (
                                    "provided"
                                    if company_supplied
                                    else "missing"
                                ),
                                "binding_status": (
                                    "condition_chapter"
                                    if condition_id and node is not None
                                    else (
                                        "condition_unplanned"
                                        if condition_id
                                        else "legacy_unit_only"
                                    )
                                ),
                            }
                        )
        return items
