from __future__ import annotations

from control_plane import WorkspaceContext
from utils import write_json

from .chapter_blueprint import load_promoted_chapter_blueprint
from .contracts import (
    DOCUMENT_CONTRACT_ADAPTER,
    BlueprintNode,
    ContractNode,
    DocumentContract,
    InputRole,
    OutlineContract,
    TemplateContract,
    TemplateSlot,
    TemplateStructureContract,
)
from .input_manifest import InputManifestService, V3_ROOT
from .outline_contract import OutlineContractCompiler
from .source_artifacts import load_promoted_template_structure
from .template_contract import TemplateContractCompiler


DOCUMENT_CONTRACT_PATH = V3_ROOT / "contracts" / "document_contract.json"


class DocumentContractCompiler:
    """Select the only legal document mode from the frozen active inputs."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def compile(self) -> DocumentContract:
        from control_plane import ControlStore
        from .artifact_promotion import HumanGateService

        store = ControlStore(self.context)
        blueprint_artifact = store.v3_active_artifact("ChapterBlueprint")
        if blueprint_artifact is not None:
            HumanGateService(self.context).require_current_confirmation()
            blueprint = load_promoted_chapter_blueprint(self.context)
            if blueprint.planning_model != "score_direct":
                raise ValueError(
                    "DOCUMENT_CONTRACT_LEGACY_READ_ONLY: legacy TopicGraph "
                    "Blueprint 仅支持历史查看，请重新生成评分直连目录"
                )
            requirement_ids_by_chapter: dict[str, list[str]] = {
                item.chapter_id: list(item.requirement_ids)
                for item in blueprint.nodes
            }
            response_obligation_ids_by_chapter: dict[str, list[str]] = {
                item.chapter_id: [
                    *item.primary_response_unit_ids,
                    *item.supporting_response_unit_ids,
                    *item.score_condition_ids,
                    *item.requirement_ids,
                ]
                if item.content_policy == "full"
                else []
                for item in blueprint.nodes
            }
            common = {
                "revision": blueprint.revision,
                "source_hashes": dict(blueprint.source_hashes),
                "source_blueprint_artifact_id": str(
                    blueprint_artifact["artifact_id"]
                ),
                "source_blueprint_revision": int(
                    blueprint_artifact["revision"]
                ),
                "source_blueprint_hash": str(
                    blueprint_artifact["artifact_hash"]
                ),
            }
            if blueprint.mode.value == "template_strict":
                structure = load_promoted_template_structure(self.context)
                if structure is None:
                    raise ValueError(
                        "DOCUMENT_CONTRACT_BLOCKED: 严格模板 Blueprint "
                        "缺少已晋级 TemplateStructureContract"
                    )
                contract = self._compile_template_blueprint(
                    blueprint.nodes,
                    structure,
                    requirement_ids_by_chapter=requirement_ids_by_chapter,
                    response_obligation_ids_by_chapter=(
                        response_obligation_ids_by_chapter
                    ),
                    common=common,
                )
            else:
                contract = OutlineContract(
                    **common,
                    nodes=[
                        ContractNode(
                            node_id=node.chapter_id,
                            parent_node_id=node.parent_chapter_id,
                            order=node.order,
                            writable_target=f"node:{node.chapter_id}",
                            title=node.title,
                            requirement_ids=sorted(
                                set(
                                    requirement_ids_by_chapter[
                                        node.chapter_id
                                    ]
                                )
                            ),
                            section_domain=node.section_domain,
                            content_policy=node.content_policy,
                            deferred_reason=node.deferred_reason,
                        )
                        for node in blueprint.nodes
                    ],
                )
            write_json(
                self.root / DOCUMENT_CONTRACT_PATH,
                DOCUMENT_CONTRACT_ADAPTER.dump_python(contract, mode="json"),
            )
            return contract
        manifest = InputManifestService(self.context).load()
        active_templates = [
            item
            for item in manifest.inputs
            if item.active and item.role is InputRole.TEMPLATE
        ]
        if active_templates:
            contract = TemplateContractCompiler(self.context).compile(
                active_templates[0]
            )
        else:
            contract = OutlineContractCompiler(self.context).compile()
        write_json(
            self.root / DOCUMENT_CONTRACT_PATH,
            DOCUMENT_CONTRACT_ADAPTER.dump_python(contract, mode="json"),
        )
        return contract

    @staticmethod
    def _compile_template_blueprint(
        blueprint_nodes: list[BlueprintNode],
        structure: TemplateStructureContract,
        *,
        requirement_ids_by_chapter: dict[str, list[str]],
        response_obligation_ids_by_chapter: dict[str, list[str]],
        common: dict[str, object],
    ) -> TemplateContract:
        template_nodes = {node.node_id: node for node in structure.nodes}
        mapped_nodes = {
            node.template_node_id: node
            for node in blueprint_nodes
            if node.template_node_id is not None
        }
        if set(mapped_nodes) != set(template_nodes):
            raise ValueError(
                "DOCUMENT_CONTRACT_BLOCKED: Blueprint 与当前严格模板节点映射不一致"
            )

        structure_slots = {slot.slot_id: slot for slot in structure.slots}
        remapped_slots = [
            TemplateSlot(
                slot_id=slot.slot_id,
                node_id=mapped_nodes[slot.node_id].chapter_id,
                kind=slot.kind,
                anchor=slot.anchor,
            )
            for slot in structure.slots
        ]
        contract_nodes: list[ContractNode] = []
        blocking_gaps: list[str] = []
        warnings: list[str] = []
        for node in sorted(blueprint_nodes, key=lambda item: item.order):
            template_node = template_nodes[str(node.template_node_id)]
            mapped_slot_ids = list(node.template_slot_ids)
            for slot_id in mapped_slot_ids:
                slot = structure_slots.get(slot_id)
                if slot is None or slot.node_id != template_node.node_id:
                    raise ValueError(
                        "DOCUMENT_CONTRACT_BLOCKED: Blueprint 的模板 Slot "
                        f"映射无效: {node.chapter_id}/{slot_id}"
                    )
            if mapped_slot_ids:
                writable_target = mapped_slot_ids[0]
            else:
                writable_target = str(
                    node.template_target or template_node.writable_target
                )
                warnings.append(
                    f"TEMPLATE_NODE_NOT_WRITABLE:{node.chapter_id}"
                )
                if response_obligation_ids_by_chapter[node.chapter_id]:
                    blocking_gaps.append(
                        f"TEMPLATE_MAPPING_GAP:{node.chapter_id}"
                    )
            contract_nodes.append(
                ContractNode(
                    node_id=node.chapter_id,
                    parent_node_id=node.parent_chapter_id,
                    order=node.order,
                    level=int(node.template_level or template_node.level),
                    numbering=node.template_numbering,
                    writable_target=writable_target,
                    title=node.title,
                    requirement_ids=sorted(
                        set(requirement_ids_by_chapter[node.chapter_id])
                    ),
                    section_domain=node.section_domain,
                    content_policy=node.content_policy,
                    deferred_reason=node.deferred_reason,
                )
            )

        contract_values = {
            **common,
            "source_hashes": {
                **common["source_hashes"],
                **structure.source_hashes,
            },
        }
        return TemplateContract(
            **contract_values,
            template_hash=structure.template_hash,
            structural_fingerprint=structure.structural_fingerprint,
            nodes=contract_nodes,
            slots=remapped_slots,
            warnings=warnings,
            blocking_gaps=blocking_gaps,
        )
