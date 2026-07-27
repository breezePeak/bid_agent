from __future__ import annotations

from collections import Counter
from pathlib import Path

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import ContentUnit, DOCUMENT_CONTRACT_ADAPTER, DocumentNodePlan, DocumentPlan, RequirementLedger, TemplateContract
from .document_contract import DOCUMENT_CONTRACT_PATH
from .input_manifest import V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger


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
        if isinstance(contract, TemplateContract) and contract.blocking_gaps:
            raise ValueError(f"DOCUMENT_PLAN_BLOCKED: {', '.join(contract.blocking_gaps)}")
        owners = self._owners(contract.nodes, ledger)
        plans = [
            DocumentNodePlan(
                node_id=node.node_id,
                primary_requirement_ids=owners[node.node_id],
                primary_score_ids=[item.requirement_id for item in ledger.requirements if item.requirement_id in owners[node.node_id] and item.kind.value == "score"],
                owned_topic_ids=[f"requirement:{item_id}" for item_id in owners[node.node_id]],
            )
            for node in contract.nodes
        ]
        plan = DocumentPlan(
            revision=contract.revision,
            source_hashes={**contract.source_hashes, **ledger.source_hashes},
            contract_revision=contract.revision,
            nodes=plans,
        )
        units = self._content_units(contract.nodes, plan)
        coverage = self._coverage(ledger, plan)
        if coverage["uncovered_requirement_ids"] or coverage["duplicate_primary_owner_ids"]:
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
            if requirement.requirement_id in owned:
                continue
            candidates = [node for node in nodes if node.title and (node.title in requirement.normalized_requirement or requirement.normalized_requirement[:8] in node.title)]
            target = (candidates or nodes)[0]
            owners[target.node_id].append(requirement.requirement_id)
            owned.add(requirement.requirement_id)
        return owners

    @staticmethod
    def _content_units(nodes, plan: DocumentPlan) -> list[ContentUnit]:
        roots = [node for node in nodes if node.parent_node_id is None]
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
            current = by_id[current.parent_node_id]
        return False

    @staticmethod
    def _coverage(ledger: RequirementLedger, plan: DocumentPlan) -> dict[str, object]:
        owners = [item for node in plan.nodes for item in node.primary_requirement_ids]
        counts = Counter(owners)
        return {
            "schema_version": "v3",
            "revision": plan.revision,
            "uncovered_requirement_ids": sorted({item.requirement_id for item in ledger.requirements} - set(owners)),
            "duplicate_primary_owner_ids": sorted(item for item, count in counts.items() if count > 1),
            "primary_owners": {node.node_id: node.primary_requirement_ids for node in plan.nodes},
        }
