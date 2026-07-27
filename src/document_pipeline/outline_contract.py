from __future__ import annotations

from collections import defaultdict

from control_plane import WorkspaceContext
from .contracts import ContractNode, OutlineContract, RequirementKind
from .project_model import load_promoted_project_model
from .requirement_ledger import load_promoted_requirement_ledger


class OutlineContractCompiler:
    """Produce a bid-specific outline where every generated node has tender provenance."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def compile(self) -> OutlineContract:
        ledger = load_promoted_requirement_ledger(self.context)
        project = load_promoted_project_model(self.context)
        groups: dict[RequirementKind, list[str]] = defaultdict(list)
        for item in ledger.requirements:
            groups[item.kind].append(item.requirement_id)
        nodes: list[ContractNode] = []
        for order, (kind, requirement_ids) in enumerate(groups.items()):
            first = next(item for item in ledger.requirements if item.requirement_id == requirement_ids[0])
            title = self._title_from_requirement(first.normalized_requirement, kind, len(requirement_ids))
            nodes.append(
                ContractNode(
                    node_id=f"outline-{order + 1}",
                    order=order,
                    writable_target=f"node:outline-{order + 1}",
                    title=title,
                    requirement_ids=requirement_ids,
                )
            )
        if not nodes:
            raise ValueError("OUTLINE_BLOCKED: 要求台账为空，禁止生成通用目录")
        return OutlineContract(
            revision=max(ledger.revision, project.revision),
            source_hashes={**ledger.source_hashes, **project.source_hashes},
            nodes=nodes,
        )

    @staticmethod
    def _title_from_requirement(statement: str, kind: RequirementKind, count: int) -> str:
        source_title = statement.replace("\n", " ").strip()[:36]
        suffix = "" if count == 1 else f"（含{count}项）"
        return f"{source_title}{suffix}"
