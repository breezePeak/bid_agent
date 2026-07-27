from __future__ import annotations

from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import ContentBlock, DOCUMENT_CONTRACT_ADAPTER, DocumentPlan, TemplateContract
from .document_contract import DOCUMENT_CONTRACT_PATH
from .document_planner import DOCUMENT_PLAN_PATH
from .requirement_ledger import LEDGER_PATH
from .contracts import RequirementLedger
from .input_manifest import V3_ROOT


CONTENT_OUTPUT_DIR = V3_ROOT / "content_units"


class ContentWriter:
    """A constrained V3 writer that can only populate existing contract targets."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def write(self, unit_id: str, node_ids: list[str]) -> list[ContentBlock]:
        contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.root / DOCUMENT_CONTRACT_PATH))
        plan = DocumentPlan.model_validate(read_json(self.root / DOCUMENT_PLAN_PATH))
        ledger = RequirementLedger.model_validate(read_json(self.root / LEDGER_PATH))
        requirement_by_id = {item.requirement_id: item for item in ledger.requirements}
        targets = {node.node_id for node in contract.nodes}
        slot_by_node: dict[str, str] = {}
        if isinstance(contract, TemplateContract):
            for slot in contract.slots:
                slot_by_node.setdefault(slot.node_id, slot.slot_id)
        blocks: list[ContentBlock] = []
        for node_plan in plan.nodes:
            if node_plan.node_id not in node_ids:
                continue
            if node_plan.node_id not in targets:
                raise ValueError(f"CONTENT_BLOCKED: 未登记的目标节点 {node_plan.node_id}")
            if isinstance(contract, TemplateContract) and node_plan.primary_requirement_ids and node_plan.node_id not in slot_by_node:
                raise ValueError(f"CONTENT_BLOCKED: 严格模板节点缺少可写 slot: {node_plan.node_id}")
            target = slot_by_node.get(node_plan.node_id, node_plan.node_id)
            for ordinal, requirement_id in enumerate(node_plan.primary_requirement_ids):
                requirement = requirement_by_id[requirement_id]
                blocks.append(
                    ContentBlock(
                        block_id=f"{unit_id}-{node_plan.node_id}-{ordinal + 1}",
                        target_node_id=target,
                        type="paragraph",
                        content=f"针对“{requirement.normalized_requirement}”，本节将按招标文件要求组织实施并形成可核验响应。",
                        requirement_ids=[requirement_id],
                        score_point_ids=[requirement_id] if requirement.kind.value == "score" else [],
                        topic_ids=[f"requirement:{requirement_id}"],
                        confidence=0.8,
                    )
                )
        output = self.root / CONTENT_OUTPUT_DIR / f"{unit_id}.json"
        write_json(output, {"schema_version": "v3", "unit_id": unit_id, "blocks": [block.model_dump(mode="json") for block in blocks]})
        self.store.upsert_content_unit_state(
            {
                "unit_id": unit_id,
                "contract_revision": plan.contract_revision,
                "state": "completed",
                "attempt": 1,
                "output_artifact_id": output.relative_to(self.root).as_posix(),
            }
        )
        return blocks
