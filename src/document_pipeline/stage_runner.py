from __future__ import annotations

from control_plane import WorkspaceContext

from .content_scheduler import ContentUnitScheduler
from .content_writer import ContentWriter
from .document_contract import DocumentContractCompiler
from .document_planner import DocumentPlanner
from .integrator import DocumentIntegrator
from .project_model import ProjectModelBuilder
from .quality import QualityGate
from .renderers.standard_renderer import StandardRenderer
from .renderers.template_renderer import StrictTemplateRenderer
from .requirement_ledger import RequirementLedgerBuilder
from .source_normalizer import SourceNormalizer


class V3StageRunner:
    """The single V3 content execution kernel; unknown stages are errors."""
    def __init__(self, context: WorkspaceContext) -> None: self.context=context
    def run(self, stage: str):
        if stage == "normalize_sources": return SourceNormalizer(self.context).normalize_active_inputs()
        if stage == "build_requirement_ledger": return RequirementLedgerBuilder(self.context).build()
        if stage == "build_project_model": return ProjectModelBuilder(self.context).build()
        if stage == "compile_document_contract": return DocumentContractCompiler(self.context).compile()
        if stage == "plan_document": return DocumentPlanner(self.context).build()
        if stage == "execute_content_plan":
            units=ContentUnitScheduler(self.context).initialize(); writer=ContentWriter(self.context)
            return [writer.write(unit.unit_id,unit.node_ids) for unit in units]
        if stage == "integrate_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .document_planner import DOCUMENT_PLAN_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, DocumentPlan
            from utils import read_json
            return DocumentIntegrator(self.context).integrate(contract_revision=DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root/DOCUMENT_CONTRACT_PATH)).revision,plan_revision=DocumentPlan.model_validate(read_json(self.context.root/DOCUMENT_PLAN_PATH)).revision)
        if stage == "verify_document": return QualityGate(self.context).verify()
        if stage == "render_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, TemplateContract
            from utils import read_json
            contract=DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root/DOCUMENT_CONTRACT_PATH))
            return StrictTemplateRenderer(self.context).render() if isinstance(contract,TemplateContract) else StandardRenderer(self.context).render()
        raise ValueError(f"V3_UNKNOWN_STAGE: {stage}")
