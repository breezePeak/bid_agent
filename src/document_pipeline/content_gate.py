"""G4 checks for Bundle-only Writer output before it can enter a ContentUnit."""

from __future__ import annotations

from typing import Any

from .contracts import ContentBlock, ContentProposal, WriterInputBundle


class WriterBundleContentGate:
    """Small fail-closed gate: no target or semantic ID may escape the Bundle."""

    def validate(self, bundle: WriterInputBundle, blocks: list[ContentBlock]) -> ContentProposal:
        target_constraints = {
            str(item["output_target"]): item
            for item in bundle.document_target_constraints
        }
        allowed_targets = set(target_constraints)
        allowed_requirements = {
            str(item["requirement_id"])
            for item in bundle.requirement_excerpts
        }
        allowed_topics = {
            str(item.get("topic_id"))
            for item in bundle.topic_and_duty_slice
            if item.get("topic_id")
        }
        allowed_duties = {
            str(item.get("duty_id"))
            for item in bundle.topic_and_duty_slice
            if item.get("duty_id")
        }
        score_catalog = self._score_catalog(bundle)
        allowed_scores = set(score_catalog["scores"])
        allowed_conditions = set(score_catalog["conditions"])
        allowed_evidence = {
            str(evidence_id)
            for item in bundle.evidence_snapshot
            if isinstance(item, dict)
            for evidence_id in item.get("evidence_ids", [])
            if str(evidence_id).strip()
        }
        required_by_target = {
            target: {
                str(value)
                for value in item.get("primary_requirement_ids", [])
            }
            for target, item in target_constraints.items()
        }
        required_conditions_by_target = {
            target: {
                str(value)
                for value in item.get("score_condition_ids", [])
            }
            for target, item in target_constraints.items()
        }
        declared_conditions = {
            condition_id
            for condition_ids in required_conditions_by_target.values()
            for condition_id in condition_ids
        }
        if unknown := declared_conditions - allowed_conditions:
            raise ValueError(
                "G4_CONTENT_CONDITION_OUT_OF_BUNDLE: "
                f"{sorted(unknown)}"
            )
        inactive = {
            condition_id
            for condition_id in declared_conditions
            if not self._is_active_condition(
                condition_id,
                score_catalog,
            )
        }
        if inactive:
            raise ValueError(
                "G4_CONTENT_CONDITION_INACTIVE: "
                f"{sorted(inactive)}"
            )
        emitted_requirements_by_target: dict[str, set[str]] = {
            target: set() for target in allowed_targets
        }
        emitted_conditions_by_target: dict[str, set[str]] = {
            target: set() for target in allowed_targets
        }
        for block in blocks:
            if block.target_node_id not in allowed_targets:
                raise ValueError("G4_CONTENT_TARGET_OUT_OF_BUNDLE")
            if not set(block.requirement_ids).issubset(allowed_requirements):
                raise ValueError("G4_CONTENT_REQUIREMENT_OUT_OF_BUNDLE")
            if not set(block.score_point_ids).issubset(allowed_scores):
                raise ValueError("G4_CONTENT_SCORE_OUT_OF_BUNDLE")
            if not set(block.topic_ids).issubset(allowed_topics):
                raise ValueError("G4_CONTENT_TOPIC_OUT_OF_BUNDLE")
            if not set(block.duty_ids).issubset(allowed_duties):
                raise ValueError("G4_CONTENT_DUTY_OUT_OF_BUNDLE")
            if not set(block.claim_ids).issubset(allowed_conditions):
                raise ValueError("G4_CONTENT_CONDITION_OUT_OF_BUNDLE")
            if not set(block.evidence_ids).issubset(allowed_evidence):
                raise ValueError("G4_CONTENT_EVIDENCE_OUT_OF_BUNDLE")
            target_conditions = required_conditions_by_target[
                block.target_node_id
            ]
            if not set(block.claim_ids).issubset(target_conditions):
                raise ValueError("G4_CONTENT_CONDITION_WRONG_TARGET")
            for condition_id in block.claim_ids:
                owner_score_id = str(
                    score_catalog["condition_score_ids"][condition_id]
                )
                if owner_score_id not in block.score_point_ids:
                    raise ValueError(
                        "G4_CONTENT_CONDITION_SCORE_BINDING_MISSING"
                    )
            if block.source_bundle_hash != bundle.bundle_hash:
                raise ValueError("G4_CONTENT_BUNDLE_HASH_MISMATCH")
            emitted_requirements_by_target[
                block.target_node_id
            ].update(block.requirement_ids)
            emitted_conditions_by_target[
                block.target_node_id
            ].update(block.claim_ids)
        missing = {
            target: sorted(
                required
                - emitted_requirements_by_target.get(target, set())
            )
            for target, required in required_by_target.items()
            if required
            - emitted_requirements_by_target.get(target, set())
        }
        if missing:
            raise ValueError(
                f"G4_CONTENT_PRIMARY_REQUIREMENT_MISSING: {missing}"
            )
        missing_conditions = {
            target: sorted(
                required
                - emitted_conditions_by_target.get(target, set())
            )
            for target, required in required_conditions_by_target.items()
            if required
            - emitted_conditions_by_target.get(target, set())
        }
        if missing_conditions:
            raise ValueError(
                "G4_CONTENT_SCORE_CONDITION_MISSING: "
                f"{missing_conditions}"
            )
        evidence_need_proposals = self._evidence_need_proposals(
            bundle=bundle,
            target_constraints=target_constraints,
            score_catalog=score_catalog,
            emitted_conditions_by_target=emitted_conditions_by_target,
        )
        for block in blocks:
            self._validate_substantive_content(
                block.content,
                target_constraints[block.target_node_id],
                score_catalog,
            )
        return ContentProposal(
            proposal_id=f"content-{bundle.bundle_id}",
            bundle_id=bundle.bundle_id,
            bundle_hash=bundle.bundle_hash,
            blocks=blocks,
            evidence_need_proposals=evidence_need_proposals,
        )

    @staticmethod
    def _validate_substantive_content(
        content: str,
        target: dict[str, Any],
        score_catalog: dict[str, Any],
    ) -> None:
        if str(target.get("content_policy") or "full") != "full":
            return
        compact = "".join(str(content or "").split())
        forbidden = (
            "满分条件",
            "得分任务",
            "本节用于",
            "按已确认的章节边界",
            "章节边界组织响应内容",
            "展开具体响应内容",
            "评分要求",
            "评分标准",
            "得分点",
        )
        if any(token in str(content or "") for token in forbidden):
            raise ValueError(
                "G4_CONTENT_SCORE_CONDITION_VISIBLE_OR_TEMPLATE_TRACE"
            )
        target_size = int(target.get("target_size") or 0)
        # Temporarily disabled: this gate evaluates each generated block rather
        # than the whole chapter, so a short supporting block can incorrectly
        # reject an otherwise substantial chapter draft.
        # if target_size >= 500 and len(compact) < 180:
        #     raise ValueError("G4_CONTENT_TOO_SHORT_OR_HOLLOW")
        if target_size < 500:
            return
        for condition_id in target.get("score_condition_ids", []):
            condition = score_catalog["conditions"].get(str(condition_id))
            if not condition:
                continue
            source = str(
                condition.get("normalized_condition")
                or condition.get("text")
                or ""
            ).strip()
            if source and "".join(source.split()) in compact:
                raise ValueError("G4_CONTENT_SCORE_CONDITION_COPIED")

    @staticmethod
    def _score_catalog(bundle: WriterInputBundle) -> dict[str, Any]:
        scores: dict[str, dict[str, Any]] = {}
        conditions: dict[str, dict[str, Any]] = {}
        units: dict[str, dict[str, Any]] = {}
        condition_score_ids: dict[str, str] = {}
        condition_unit_ids: dict[str, str] = {}
        unit_score_ids: dict[str, str] = {}
        for score in bundle.score_obligations:
            if not isinstance(score, dict) or not score.get(
                "score_point_id"
            ):
                continue
            score_id = str(score["score_point_id"])
            if score_id in scores:
                raise ValueError(
                    "G4_CONTENT_DUPLICATE_SCORE_ID_IN_BUNDLE"
                )
            scores[score_id] = score
            for condition in score.get("score_conditions", []):
                if not isinstance(condition, dict) or not condition.get(
                    "condition_id"
                ):
                    continue
                condition_id = str(condition["condition_id"])
                if condition_id in conditions:
                    raise ValueError(
                        "G4_CONTENT_DUPLICATE_CONDITION_ID_IN_BUNDLE"
                    )
                conditions[condition_id] = condition
                condition_score_ids[condition_id] = score_id
            for unit in score.get("response_units", []):
                if not isinstance(unit, dict) or not unit.get("unit_id"):
                    continue
                unit_id = str(unit["unit_id"])
                if unit_id in units:
                    raise ValueError(
                        "G4_CONTENT_DUPLICATE_RESPONSE_UNIT_ID_IN_BUNDLE"
                    )
                units[unit_id] = unit
                unit_score_ids[unit_id] = score_id
                for condition_id_value in unit.get(
                    "condition_ids",
                    [],
                ):
                    condition_id = str(condition_id_value)
                    # Units may historically list sibling conditions owned by
                    # other chapter slices. Only map conditions frozen in this
                    # Bundle; foreign ids are ignored rather than fail closed.
                    if condition_id not in conditions:
                        continue
                    if (
                        condition_id in condition_unit_ids
                        and condition_unit_ids[condition_id] != unit_id
                    ):
                        raise ValueError(
                            "G4_CONTENT_CONDITION_MULTIPLE_RESPONSE_UNITS"
                        )
                    condition_unit_ids[condition_id] = unit_id
        return {
            "scores": scores,
            "conditions": conditions,
            "units": units,
            "condition_score_ids": condition_score_ids,
            "condition_unit_ids": condition_unit_ids,
            "unit_score_ids": unit_score_ids,
        }

    @staticmethod
    def _is_active_condition(
        condition_id: str,
        catalog: dict[str, Any],
    ) -> bool:
        condition = catalog["conditions"][condition_id]
        score_id = catalog["condition_score_ids"][condition_id]
        score = catalog["scores"][score_id]
        unit_id = catalog["condition_unit_ids"].get(condition_id)
        unit = catalog["units"].get(unit_id) if unit_id else None
        return (
            str(condition.get("review_status") or "confirmed")
            != "blocked"
            and str(score.get("review_status") or "confirmed")
            != "blocked"
            and (
                unit is None
                or str(unit.get("review_status") or "confirmed")
                != "blocked"
            )
        )

    @classmethod
    def _evidence_need_proposals(
        cls,
        *,
        bundle: WriterInputBundle,
        target_constraints: dict[str, dict[str, Any]],
        score_catalog: dict[str, Any],
        emitted_conditions_by_target: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        evidence_conditions_by_unit: dict[str, set[str]] = {}
        proposals: list[dict[str, Any]] = []
        for target, condition_ids in emitted_conditions_by_target.items():
            constraint = target_constraints[target]
            for condition_id in sorted(condition_ids):
                condition = score_catalog["conditions"][condition_id]
                if str(condition.get("condition_role") or "content") != (
                    "evidence"
                ):
                    continue
                unit_id = score_catalog["condition_unit_ids"].get(
                    condition_id
                )
                if not unit_id:
                    raise ValueError(
                        "G4_CONTENT_EVIDENCE_CONDITION_UNIT_MISSING: "
                        f"{condition_id}"
                    )
                evidence_conditions_by_unit.setdefault(
                    unit_id,
                    set(),
                ).add(condition_id)
                unit = score_catalog["units"][unit_id]
                evidence_types = [
                    str(value)
                    for value in unit.get(
                        "required_evidence_types",
                        [],
                    )
                    if str(value).strip()
                ]
                if not evidence_types:
                    raise ValueError(
                        "G4_CONTENT_EVIDENCE_TYPE_MISSING"
                    )
                for ordinal, evidence_type in enumerate(
                    evidence_types,
                    start=1,
                ):
                    proposals.append(
                        {
                            "proposal_id": (
                                f"evidence-{bundle.bundle_id}-"
                                f"{condition_id}-{ordinal}"
                            ),
                            "condition_id": condition_id,
                            "response_unit_id": unit_id,
                            "score_point_id": score_catalog[
                                "unit_score_ids"
                            ][unit_id],
                            "chapter_id": str(
                                constraint.get("node_id") or target
                            ),
                            "target_node_id": target,
                            "evidence_type": evidence_type,
                            "status": "required",
                            "source_bundle_hash": bundle.bundle_hash,
                        }
                    )

        # Historical ScoreModels could carry evidence only at response-unit
        # level.  Preserve those tasks without inventing a condition binding.
        for target, constraint in target_constraints.items():
            for unit_id_value in constraint.get(
                "primary_response_unit_ids",
                [],
            ):
                unit_id = str(unit_id_value)
                unit = score_catalog["units"].get(unit_id)
                if (
                    unit is None
                    or unit_id in evidence_conditions_by_unit
                    or str(unit.get("review_status") or "confirmed")
                    == "blocked"
                ):
                    continue
                active_evidence_condition_ids = {
                    str(condition_id)
                    for condition_id in unit.get(
                        "condition_ids",
                        [],
                    )
                    if str(condition_id)
                    in score_catalog["conditions"]
                    and cls._is_active_condition(
                        str(condition_id),
                        score_catalog,
                    )
                    and str(
                        score_catalog["conditions"][
                            str(condition_id)
                        ].get("condition_role")
                        or "content"
                    )
                    == "evidence"
                }
                if active_evidence_condition_ids:
                    raise ValueError(
                        "G4_CONTENT_EVIDENCE_CONDITION_TARGET_MISSING: "
                        f"{sorted(active_evidence_condition_ids)}"
                    )
                evidence_types = [
                    str(value)
                    for value in unit.get(
                        "required_evidence_types",
                        [],
                    )
                    if str(value).strip()
                ]
                for ordinal, evidence_type in enumerate(
                    evidence_types,
                    start=1,
                ):
                    proposals.append(
                        {
                            "proposal_id": (
                                f"evidence-{bundle.bundle_id}-"
                                f"{unit_id}-legacy-{ordinal}"
                            ),
                            "condition_id": None,
                            "response_unit_id": unit_id,
                            "score_point_id": score_catalog[
                                "unit_score_ids"
                            ][unit_id],
                            "chapter_id": str(
                                constraint.get("node_id") or target
                            ),
                            "target_node_id": target,
                            "evidence_type": evidence_type,
                            "status": "required",
                            "binding_status": "legacy_unit_only",
                            "source_bundle_hash": bundle.bundle_hash,
                        }
                    )
        return proposals
