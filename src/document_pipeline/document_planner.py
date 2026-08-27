from __future__ import annotations

from collections import Counter
from pathlib import Path

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import ContentUnit, DOCUMENT_CONTRACT_ADAPTER, DocumentNodePlan, DocumentPlan, RequirementLedger, TemplateContract
from .document_contract import DOCUMENT_CONTRACT_PATH
from .input_manifest import V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger
from .score_model import load_promoted_score_model
from .scoring_outline_policy import active_planning_requirement_ids


DOCUMENT_PLAN_PATH = V3_ROOT / "document_plan.json"
PLANNING_COVERAGE_PATH = V3_ROOT / "reports" / "planning_coverage.json"
CONTENT_UNITS_PATH = V3_ROOT / "content_units" / "index.json"


class DocumentPlanner:
    """Assign one primary owner before any writer is allowed to run."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> tuple[DocumentPlan, list[ContentUnit]]:
        contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.root / DOCUMENT_CONTRACT_PATH))
        ledger = load_promoted_requirement_ledger(self.context)
        scores = None
        if isinstance(contract, TemplateContract) and contract.blocking_gaps:
            raise ValueError(f"DOCUMENT_PLAN_BLOCKED: {', '.join(contract.blocking_gaps)}")
        owners = self._owners(contract.nodes, ledger)
        uses_confirmed_blueprint = bool(contract.source_blueprint_hash)
        required_requirement_ids: set[str] | None = None
        deferred_response_unit_ids: set[str] = set()
        score_owners: dict[str, list[str]] = {node.node_id: [] for node in contract.nodes}
        topic_owners: dict[str, list[str]] = {node.node_id: [] for node in contract.nodes}
        if contract.source_blueprint_hash:
            from control_plane import ControlStore
            from .artifact_promotion import HumanGateService
            from .chapter_blueprint import load_promoted_chapter_blueprint

            HumanGateService(self.context).require_current_confirmation()
            active = ControlStore(self.context).v3_active_artifact("ChapterBlueprint")
            if active is None or str(active["artifact_hash"]) != contract.source_blueprint_hash:
                raise ValueError("DOCUMENT_PLAN_BLOCKED: DocumentContract 未绑定当前 confirmed Blueprint")
            blueprint = load_promoted_chapter_blueprint(self.context)
            blueprint_node_ids = {node.chapter_id for node in blueprint.nodes}
            contract_node_ids = {node.node_id for node in contract.nodes}
            if contract_node_ids != blueprint_node_ids:
                # A stale contract must never leak a raw chapter-id KeyError into
                # the writing-plan stage. Recompile it from the confirmed
                # Blueprint, then validate the sets once more.
                from .document_contract import DocumentContractCompiler

                contract = DocumentContractCompiler(self.context).compile()
                blueprint_node_ids = {node.chapter_id for node in blueprint.nodes}
                contract_node_ids = {node.node_id for node in contract.nodes}
                if contract_node_ids != blueprint_node_ids:
                    missing = sorted(contract_node_ids - blueprint_node_ids)
                    extra = sorted(blueprint_node_ids - contract_node_ids)
                    raise ValueError(
                        "DOCUMENT_PLAN_BLOCKED: 当前文档结构与已确认目录不一致；"
                        f"仅文档结构存在={missing}，仅确认目录存在={extra}"
                    )
            if blueprint.planning_model != "score_direct":
                raise ValueError(
                    "DOCUMENT_PLAN_LEGACY_READ_ONLY: legacy TopicGraph "
                    "Blueprint 仅支持历史查看，请重新生成评分直连目录"
            )
            scores = load_promoted_score_model(self.context)
            blueprint_by_chapter = {
                node.chapter_id: node for node in blueprint.nodes
            }
            deferred_requirement_ids = {
                requirement_id
                for node in blueprint.nodes
                if node.content_policy != "full"
                for requirement_id in node.requirement_ids
            }
            deferred_response_unit_ids = {
                unit_id
                for node in blueprint.nodes
                if node.content_policy != "full"
                for unit_id in [
                    *node.primary_response_unit_ids,
                    *node.supporting_response_unit_ids,
                ]
            }
            score_point_ids = {point.score_point_id for point in scores.points}
            score_owner_by_unit = {
                unit.unit_id: point.score_point_id
                for point in scores.points
                for unit in point.response_units
            }
            blueprint_requirement_bindings = {
                node.node_id: (
                    list(blueprint_by_chapter[node.node_id].requirement_ids)
                    if blueprint_by_chapter[node.node_id].content_policy == "full"
                    else []
                )
                for node in contract.nodes
            }
            assigned_requirements = {
                requirement_id
                for values in blueprint_requirement_bindings.values()
                for requirement_id in values
            }
            # A requirement may legitimately support several score-response
            # subtrees.  ChapterBlueprint keeps every such traceability binding,
            # while DocumentPlan still needs one deterministic primary owner for
            # execution accounting.
            owners = self._unique_primary_requirement_owners(
                [node.node_id for node in contract.nodes],
                blueprint_requirement_bindings,
            )
            claimed_score_ids: set[str] = set()
            for node in blueprint.nodes:
                if node.chapter_id not in score_owners:
                    continue
                direct_score_ids = list(
                    dict.fromkeys(
                        [
                            *node.score_point_ids,
                            *[
                                score_owner_by_unit[unit_id]
                                for unit_id in node.primary_response_unit_ids
                                if unit_id in score_owner_by_unit
                            ],
                        ]
                    )
                )
                unknown_score_ids = set(direct_score_ids) - score_point_ids
                if unknown_score_ids:
                    raise ValueError(
                        "DOCUMENT_PLAN_BLOCKED: Blueprint 引用未知 "
                        f"score_point_id {sorted(unknown_score_ids)}"
                    )
                score_owners[node.chapter_id] = [
                    score_id
                    for score_id in direct_score_ids
                    if score_id not in claimed_score_ids
                ]
                claimed_score_ids.update(score_owners[node.chapter_id])
                topic_owners[node.chapter_id] = [
                    f"response-unit:{unit_id}"
                    for unit_id in node.primary_response_unit_ids
                ]
            score_linked_requirement_ids = {
                requirement_id
                for point in scores.points
                if point.review_status != "blocked"
                for unit in point.response_units
                if unit.review_status != "blocked"
                and unit.response_scope == "section"
                and unit.unit_id not in deferred_response_unit_ids
                for requirement_id in unit.linked_requirement_ids
            }
            active_requirement_ids = active_planning_requirement_ids(
                ledger,
                scores,
            )
            if isinstance(contract, TemplateContract):
                required_requirement_ids = (
                    active_requirement_ids & score_linked_requirement_ids
                )
            elif score_linked_requirement_ids:
                required_requirement_ids = (
                    active_requirement_ids & score_linked_requirement_ids
                )
            else:
                required_requirement_ids = (
                    active_requirement_ids
                    if not scores.points
                    else set()
                )
            required_requirement_ids = (
                required_requirement_ids - deferred_requirement_ids
            )
            if required_requirement_ids != assigned_requirements:
                raise ValueError(
                    "DOCUMENT_PLAN_BLOCKED: Blueprint 节点未覆盖全部"
                    "必需 Requirement"
                )
        plans = [
            DocumentNodePlan(
                node_id=node.node_id,
                primary_requirement_ids=owners[node.node_id],
                primary_score_ids=(
                    score_owners[node.node_id]
                    if uses_confirmed_blueprint
                    else [
                        item.requirement_id
                        for item in ledger.requirements
                        if item.status not in {"blocked", "waived"}
                        and item.requirement_id in owners[node.node_id]
                        and item.kind.value == "score"
                    ]
                ),
                owned_topic_ids=(
                    topic_owners[node.node_id]
                    if uses_confirmed_blueprint
                    else [
                        f"requirement:{item_id}"
                        for item_id in owners[node.node_id]
                    ]
                ),
                section_domain=node.section_domain,
                content_policy=node.content_policy,
                deferred_reason=node.deferred_reason,
            )
            for node in contract.nodes
        ]
        plan = DocumentPlan(
            revision=contract.revision,
            source_hashes={
                **contract.source_hashes,
                **ledger.source_hashes,
                **(scores.source_hashes if scores is not None else {}),
            },
            contract_revision=contract.revision,
            source_blueprint_artifact_id=contract.source_blueprint_artifact_id,
            source_blueprint_revision=contract.source_blueprint_revision,
            source_blueprint_hash=contract.source_blueprint_hash,
            nodes=plans,
        )
        writable_nodes = [
            node for node in contract.nodes if node.content_policy == "full"
        ]
        units = self._content_units(writable_nodes, plan)
        coverage = self._coverage(
            ledger,
            plan,
            required_requirement_ids=required_requirement_ids,
            allow_shared_requirements=uses_confirmed_blueprint,
        )
        if scores is not None:
            primary_response_unit_ids = {
                item.removeprefix("response-unit:")
                for node in plan.nodes
                for item in node.owned_topic_ids
                if item.startswith("response-unit:")
            }
            expected_response_unit_ids = {
                unit.unit_id
                for point in scores.points
                if point.review_status != "blocked"
                for unit in point.response_units
                if unit.review_status != "blocked"
                and unit.response_scope == "section"
                and unit.unit_id not in deferred_response_unit_ids
            }
            coverage["uncovered_response_unit_ids"] = sorted(
                expected_response_unit_ids - primary_response_unit_ids
            )
        if (
            coverage["uncovered_requirement_ids"]
            or coverage["duplicate_primary_owner_ids"]
            or coverage.get("uncovered_response_unit_ids")
        ):
            raise ValueError(f"DOCUMENT_PLAN_BLOCKED: {coverage}")
        write_json(self.root / DOCUMENT_PLAN_PATH, plan.model_dump(mode="json"))
        write_json(self.root / CONTENT_UNITS_PATH, {"schema_version": "v3", "revision": plan.revision, "units": [unit.model_dump(mode="json") for unit in units]})
        write_json(self.root / PLANNING_COVERAGE_PATH, coverage)
        return plan, units

    @staticmethod
    def _owners(nodes, ledger: RequirementLedger) -> dict[str, list[str]]:
        owners = {node.node_id: list(node.requirement_ids) for node in nodes}
        owned = {requirement_id for values in owners.values() for requirement_id in values}
        for requirement in ledger.requirements:
            if requirement.status in {"blocked", "waived"}:
                continue
            if requirement.requirement_id in owned:
                continue
            candidates = [node for node in nodes if node.title and (node.title in requirement.normalized_requirement or requirement.normalized_requirement[:8] in node.title)]
            target = (candidates or nodes)[0]
            owners[target.node_id].append(requirement.requirement_id)
            owned.add(requirement.requirement_id)
        return owners

    @staticmethod
    def _unique_primary_requirement_owners(
        node_ids: list[str],
        bindings: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Choose one execution owner without erasing Blueprint trace links."""

        claimed: set[str] = set()
        owners: dict[str, list[str]] = {}
        for node_id in node_ids:
            owners[node_id] = [
                requirement_id
                for requirement_id in bindings.get(node_id, [])
                if requirement_id not in claimed
            ]
            claimed.update(owners[node_id])
        return owners

    @staticmethod
    def _content_units(nodes, plan: DocumentPlan) -> list[ContentUnit]:
        writable_node_ids = {node.node_id for node in nodes}
        # A non-writable structural parent is deliberately absent from `nodes`.
        # Its first writable child therefore becomes a writing-unit root.
        roots = [
            node
            for node in nodes
            if node.parent_node_id is None
            or node.parent_node_id not in writable_node_ids
        ]
        if len(roots) == len(nodes) and len(nodes) > 1:
            return [
                ContentUnit(
                    revision=plan.revision,
                    source_hashes=plan.source_hashes,
                    unit_id="unit-document-1",
                    contract_revision=plan.contract_revision,
                    node_ids=[node.node_id for node in nodes],
                )
            ]
        units: list[ContentUnit] = []
        for root in roots:
            descendant_ids = [node.node_id for node in nodes if node.node_id == root.node_id or DocumentPlanner._is_descendant(node.node_id, root.node_id, nodes)]
            units.append(ContentUnit(revision=plan.revision, source_hashes=plan.source_hashes, unit_id=f"unit-{root.node_id}", contract_revision=plan.contract_revision, node_ids=descendant_ids))
        return units

    @staticmethod
    def _is_descendant(node_id: str, root_id: str, nodes) -> bool:
        by_id = {node.node_id: node for node in nodes}
        current = by_id[node_id]
        while current.parent_node_id:
            if current.parent_node_id == root_id:
                return True
            parent = by_id.get(current.parent_node_id)
            if parent is None:
                return False
            current = parent
        return False

    @staticmethod
    def _coverage(
        ledger: RequirementLedger,
        plan: DocumentPlan,
        *,
        required_requirement_ids: set[str] | None = None,
        allow_shared_requirements: bool = False,
    ) -> dict[str, object]:
        owners = [item for node in plan.nodes for item in node.primary_requirement_ids]
        counts = Counter(owners)
        expected_requirement_ids = (
            required_requirement_ids
            if required_requirement_ids is not None
            else {
                item.requirement_id
                for item in ledger.requirements
                if item.status not in {"blocked", "waived"}
            }
        )
        return {
            "schema_version": "v3",
            "revision": plan.revision,
            "uncovered_requirement_ids": sorted(
                expected_requirement_ids - set(owners)
            ),
            "duplicate_primary_owner_ids": (
                []
                if allow_shared_requirements
                else sorted(
                    item
                    for item, count in counts.items()
                    if count > 1
                )
            ),
            "primary_owners": {node.node_id: node.primary_requirement_ids for node in plan.nodes},
        }
