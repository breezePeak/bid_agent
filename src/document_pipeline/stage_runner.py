from __future__ import annotations

from control_plane import WorkspaceContext

from .content_scheduler import ContentUnitScheduler
from .content_writer import ContentWriter
from .contracts import InputRole, SourceBlock
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
from .requirement_agent import RequirementAgent
from .requirement_ledger import audit_reverse_coverage, load_promoted_requirement_ledger
from .score_agent import ScoreAgent
from .score_model import audit_score_model, load_promoted_score_model
from .source_normalizer import SOURCE_INDEX_PATH, SourceNormalizer
from .template_contract import TemplateContractCompiler
from utils import read_json


class V3StageRunner:
    """The single V3 content execution kernel; unknown stages are errors."""
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def run(self, stage: str, *, operation_id: str | None = None):
        if stage == "ingest_inputs":
            manifest = InputManifestService(self.context).load()
            if not any(item.active and item.role.value == "tender" for item in manifest.inputs):
                raise ValueError("INGEST_BLOCKED: 至少需要一份活动招标文件")
            return manifest
        if stage == "normalize_sources":
            return SourceNormalizer(self.context).normalize_active_inputs()
        if stage == "compile_template_structure":
            manifest = InputManifestService(self.context).load()
            template = next((item for item in manifest.inputs if item.active and item.role.value == "template"), None)
            return TemplateContractCompiler(self.context).compile_structure(template) if template else None
        if stage in ("build_requirement_ledger", "analyze_requirements"):
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record
            from .contracts import RequirementLedger

            manifest = InputManifestService(self.context).load()
            idx = read_json(self.context.root / SOURCE_INDEX_PATH)
            blocks_raw = idx.get("blocks", []) if isinstance(idx, dict) else []
            source_blocks = [SourceBlock.model_validate(b) for b in blocks_raw if isinstance(b, dict)]

            agent = RequirementAgent(self.context)
            items = agent.extract_requirements(source_blocks, manifest)

            store = ControlStore(self.context)
            active_art = store.v3_active_artifact("RequirementLedger")
            base_rev = int(active_art["revision"]) if active_art is not None else 0
            source_hashes = idx.get("source_hashes") if isinstance(idx.get("source_hashes"), dict) else {}
            draft_ledger = RequirementLedger(revision=base_rev + 1, source_hashes=source_hashes, requirements=items)
            coverage_audit = audit_reverse_coverage(draft_ledger, idx)
            if not coverage_audit["passed"]:
                raise ControlPlaneError(
                    "V3_REQUIREMENT_COVERAGE_BLOCKED",
                    f"RequirementLedger 未覆盖 SourceBlock: {coverage_audit['missing_chunk_ids']}",
                    status_code=409,
                )
            op_id = operation_id or f"requirement:{manifest.revision}"
            proposal = agent.create_extraction_proposal(
                items,
                base_revision=base_rev,
                operation_id=op_id,
                source_hashes=source_hashes,
                coverage_audit=coverage_audit,
            )
            if active_art is not None and active_art["dependency_fingerprint"] == proposal.dependency_fingerprint:
                return load_promoted_requirement_ledger(self.context)

            sandbox = AgentProposalSandbox(self.context, role="requirement_agent")
            stored_proposal = sandbox.submit(proposal)
            proposal = proposal.model_copy(update={"proposal_id": stored_proposal["proposal_id"]})

            report = validate_and_record(
                self.context,
                proposal,
                expected_dependency_fingerprint=proposal.dependency_fingerprint,
            )
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"RequirementLedger Proposal 验证未通过: {report.findings}")

            gate_service = GateService(self.context)
            receipt = gate_service.evaluate(proposal.proposal_id, gate_id="G1_REQUIREMENT_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"RequirementLedger 门禁阻断: {receipt.findings}")

            promotion_service = ArtifactPromotionService(self.context)
            promotion_service.promote(proposal.proposal_id, gate_receipt_ids=[receipt.receipt_id])

            return load_promoted_requirement_ledger(self.context)

        if stage == "analyze_scores":
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record

            requirement_ledger = load_promoted_requirement_ledger(self.context)
            idx = read_json(self.context.root / SOURCE_INDEX_PATH)
            blocks_raw = idx.get("blocks", []) if isinstance(idx, dict) else []
            source_blocks = [SourceBlock.model_validate(block) for block in blocks_raw if isinstance(block, dict)]
            source_hashes = idx.get("source_hashes") if isinstance(idx.get("source_hashes"), dict) else {}

            store = ControlStore(self.context)
            active_art = store.v3_active_artifact("ScoreModel")
            base_rev = int(active_art["revision"]) if active_art is not None else 0
            agent = ScoreAgent(self.context)
            score_model = agent.build_score_model(
                source_blocks,
                requirement_ledger,
                revision=base_rev + 1,
                source_hashes=source_hashes,
            )
            score_audit = audit_score_model(score_model, requirement_ledger, source_blocks)
            if not score_audit["passed"]:
                raise ControlPlaneError("V3_SCORE_INTEGRITY_BLOCKED", f"ScoreModel 引用审计失败: {score_audit}", status_code=409)
            op_id = operation_id or f"score:{requirement_ledger.revision}:{idx.get('revision', 0)}"
            proposal = agent.create_score_model_proposal(
                score_model,
                base_revision=base_rev,
                operation_id=op_id,
                requirement_revision=requirement_ledger.revision,
            )
            if active_art is not None and active_art["dependency_fingerprint"] == proposal.dependency_fingerprint:
                return load_promoted_score_model(self.context)

            stored_proposal = AgentProposalSandbox(self.context, role="score_agent").submit(proposal)
            proposal = proposal.model_copy(update={"proposal_id": stored_proposal["proposal_id"]})
            report = validate_and_record(
                self.context,
                proposal,
                expected_dependency_fingerprint=proposal.dependency_fingerprint,
            )
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"ScoreModel Proposal 验证未通过: {report.findings}")
            receipt = GateService(self.context).evaluate(proposal.proposal_id, gate_id="G1_SCORE_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"ScoreModel 门禁阻断: {receipt.findings}")
            ArtifactPromotionService(self.context).promote(proposal.proposal_id, gate_receipt_ids=[receipt.receipt_id])
            return load_promoted_score_model(self.context)

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
