from __future__ import annotations

from control_plane import WorkspaceContext

from .content_scheduler import ContentUnitScheduler
from .content_writer import ContentWriter
from .document_contract import DocumentContractCompiler
from .document_planner import DocumentPlanner
from .integrator import DocumentIntegrator
from .input_manifest import InputManifestService
from .material_sync import MaterialRequirementsSynchronizer
from .project_model import ProjectModelBuilder
from .quality import CONTENT_QUALITY_PATH, QualityGate
from .renderers.render_verifier import DeliveryVerifier
from .renderers.standard_renderer import StandardRenderer
from .renderers.template_renderer import StrictTemplateRenderer
from .requirement_ledger import RequirementLedgerBuilder
from .source_normalizer import SourceNormalizer


class V3StageRunner:
    """The single V3 content execution kernel; unknown stages are errors."""
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def run(self, stage: str):
        if stage == "ingest_inputs":
            manifest = InputManifestService(self.context).load()
            if not any(item.active and item.role.value == "tender" for item in manifest.inputs):
                raise ValueError("INGEST_BLOCKED: 至少需要一份活动招标文件")
            return manifest
        if stage == "normalize_sources":
            return SourceNormalizer(self.context).normalize_active_inputs()
        if stage == "build_requirement_ledger":
            return RequirementLedgerBuilder(self.context).build()
        if stage == "build_project_model":
            return ProjectModelBuilder(self.context).build()
        if stage == "sync_material_requirements":
            return MaterialRequirementsSynchronizer(self.context).sync()
        if stage == "compile_document_contract":
            return DocumentContractCompiler(self.context).compile()
        if stage == "plan_document":
            return DocumentPlanner(self.context).build()
        if stage == "execute_content_plan":
            units = ContentUnitScheduler(self.context).initialize()
            writer = ContentWriter(self.context)
            return [writer.write(unit.unit_id, unit.node_ids) for unit in units]
        if stage == "integrate_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .document_planner import DOCUMENT_PLAN_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, DocumentPlan
            from utils import read_json
            contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root / DOCUMENT_CONTRACT_PATH))
            plan = DocumentPlan.model_validate(read_json(self.context.root / DOCUMENT_PLAN_PATH))
            return DocumentIntegrator(self.context).integrate(
                contract_revision=contract.revision,
                plan_revision=plan.revision,
            )
        if stage == "verify_document":
            return QualityGate(self.context).verify()
        if stage == "render_document":
            from .document_contract import DOCUMENT_CONTRACT_PATH
            from .contracts import DOCUMENT_CONTRACT_ADAPTER, TemplateContract
            from utils import read_json
            quality_path = self.context.root / CONTENT_QUALITY_PATH
            if not quality_path.is_file():
                raise ValueError("RENDER_BLOCKED: 尚未执行内容质量门禁")
            quality = read_json(quality_path)
            if quality.get("verdict") != "pass":
                raise ValueError("RENDER_BLOCKED: 内容质量门禁未通过")
            contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.context.root / DOCUMENT_CONTRACT_PATH))
            return StrictTemplateRenderer(self.context).render() if isinstance(contract, TemplateContract) else StandardRenderer(self.context).render()
        if stage == "verify_delivery":
            return DeliveryVerifier(self.context).verify()
        raise ValueError(f"V3_UNKNOWN_STAGE: {stage}")
