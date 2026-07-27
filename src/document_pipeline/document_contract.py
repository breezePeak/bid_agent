from __future__ import annotations

from pathlib import Path

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import DocumentContract, DOCUMENT_CONTRACT_ADAPTER, InputRole
from .input_manifest import InputManifestService, V3_ROOT
from .outline_contract import OutlineContractCompiler
from .project_model import PROJECT_MODEL_PATH
from .template_contract import TemplateContractCompiler


DOCUMENT_CONTRACT_PATH = V3_ROOT / "contracts" / "document_contract.json"


class DocumentContractCompiler:
    """Select the only legal document mode from the frozen active inputs."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def compile(self) -> DocumentContract:
        manifest = InputManifestService(self.context).load()
        active_templates = [item for item in manifest.inputs if item.active and item.role is InputRole.TEMPLATE]
        if active_templates:
            contract = TemplateContractCompiler(self.context).compile(active_templates[0])
        else:
            contract = OutlineContractCompiler(self.context).compile()
        write_json(self.root / DOCUMENT_CONTRACT_PATH, DOCUMENT_CONTRACT_ADAPTER.dump_python(contract, mode="json"))
        return contract
