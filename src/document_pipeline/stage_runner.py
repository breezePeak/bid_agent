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
from .planning_agent import PlanningAgent
from .project_model import load_promoted_project_model
from .quality import CONTENT_QUALITY_PATH, QualityGate
from .renderers.render_verifier import DeliveryVerifier
from .renderers.standard_renderer import StandardRenderer
from .renderers.template_renderer import StrictTemplateRenderer
from .requirement_agent import RequirementAgent
from .requirement_ledger import audit_reverse_coverage, load_promoted_requirement_ledger
from .score_agent import ScoreAgent
from .score_model import audit_score_model, load_promoted_score_model
from .topic_graph import load_promoted_topic_graph
from .chapter_blueprint import load_promoted_chapter_blueprint
from .source_artifacts import require_promoted_source_index
from .source_normalizer import SourceNormalizer
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
            source_index = require_promoted_source_index(self.context)
            idx = source_index.model_dump(mode="json")
            source_blocks = list(source_index.blocks)

            agent = RequirementAgent(self.context)
            items = agent.extract_requirements(source_blocks, manifest)

            store = ControlStore(self.context)
            active_art = store.v3_active_artifact("RequirementLedger")
            base_rev = int(active_art["revision"]) if active_art is not None else 0
            source_hashes = dict(source_index.source_hashes)
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
            proposal_id = str(stored_proposal["proposal_id"])

            report = validate_and_record(self.context, proposal_id)
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"RequirementLedger Proposal 验证未通过: {report.findings}")

            gate_service = GateService(self.context)
            receipt = gate_service.evaluate(proposal_id, gate_id="G1_REQUIREMENT_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"RequirementLedger 门禁阻断: {receipt.findings}")

            promotion_service = ArtifactPromotionService(self.context)
            promotion_service.promote(proposal_id, gate_receipt_ids=[receipt.receipt_id])

            return load_promoted_requirement_ledger(self.context)

        if stage == "analyze_scores":
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record

            requirement_ledger = load_promoted_requirement_ledger(self.context)
            source_index = require_promoted_source_index(self.context)
            source_blocks = list(source_index.blocks)
            source_hashes = dict(source_index.source_hashes)
            idx = source_index.model_dump(mode="json")

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
            proposal_id = str(stored_proposal["proposal_id"])
            report = validate_and_record(self.context, proposal_id)
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"ScoreModel Proposal 验证未通过: {report.findings}")
            receipt = GateService(self.context).evaluate(proposal_id, gate_id="G1_SCORE_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"ScoreModel 门禁阻断: {receipt.findings}")
            ArtifactPromotionService(self.context).promote(proposal_id, gate_receipt_ids=[receipt.receipt_id])
            return load_promoted_score_model(self.context)

        if stage in ("plan_response", "build_project_model"):
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record

            ledger = load_promoted_requirement_ledger(self.context)
            scores = load_promoted_score_model(self.context)
            source_index = require_promoted_source_index(self.context)
            source_blocks = list(source_index.blocks)
            store = ControlStore(self.context)
            agent = PlanningAgent(self.context)
            active_project = store.v3_active_artifact("ProjectModel")
            project_base = int(active_project["revision"]) if active_project is not None else 0
            project = agent.project_model(ledger, scores, source_blocks, revision=project_base + 1)
            project_proposal = agent.proposal("ProjectModel", project, base_revision=project_base, operation_id=operation_id or f"planning-project:{ledger.revision}:{scores.revision}", upstream_revisions=(ledger.revision, scores.revision))
            if active_project is None or active_project["dependency_fingerprint"] != project_proposal.dependency_fingerprint:
                stored = AgentProposalSandbox(self.context, role="planning_agent").submit(project_proposal)
                project_id = str(stored["proposal_id"])
                report = validate_and_record(self.context, project_id)
                if not report.passed:
                    raise ControlPlaneError("V3_PROPOSAL_INVALID", f"ProjectModel Proposal 验证未通过: {report.findings}")
                receipt = GateService(self.context).evaluate(project_id, gate_id="G1_PROJECT_MODEL_INTEGRITY")
                if receipt.verdict != "pass":
                    raise ControlPlaneError("V3_GATE_BLOCKED", f"ProjectModel 门禁阻断: {receipt.findings}")
                ArtifactPromotionService(self.context).promote(project_id, gate_receipt_ids=[receipt.receipt_id])
            project = load_promoted_project_model(self.context)

            active_graph = store.v3_active_artifact("ResponseTopicGraph")
            graph_base = int(active_graph["revision"]) if active_graph is not None else 0
            graph = agent.topic_graph(ledger, scores, project, revision=graph_base + 1)
            graph_proposal = agent.proposal("ResponseTopicGraph", graph, base_revision=graph_base, operation_id=operation_id or f"planning-graph:{ledger.revision}:{scores.revision}:{project.revision}", upstream_revisions=(ledger.revision, scores.revision, project.revision))
            if active_graph is None or active_graph["dependency_fingerprint"] != graph_proposal.dependency_fingerprint:
                stored = AgentProposalSandbox(self.context, role="planning_agent").submit(graph_proposal)
                graph_id = str(stored["proposal_id"])
                report = validate_and_record(self.context, graph_id)
                if not report.passed:
                    raise ControlPlaneError("V3_PROPOSAL_INVALID", f"ResponseTopicGraph Proposal 验证未通过: {report.findings}")
                receipt = GateService(self.context).evaluate(graph_id, gate_id="G1_TOPIC_GRAPH_INTEGRITY")
                if receipt.verdict != "pass":
                    raise ControlPlaneError("V3_GATE_BLOCKED", f"ResponseTopicGraph 门禁阻断: {receipt.findings}")
                ArtifactPromotionService(self.context).promote(graph_id, gate_receipt_ids=[receipt.receipt_id])
            load_promoted_topic_graph(self.context)
            return project

        if stage == "compile_chapter_blueprint":
            from control_plane import ControlPlaneError, ControlStore
            from .artifact_promotion import AgentProposalSandbox, ArtifactPromotionService, GateService, validate_and_record

            graph = load_promoted_topic_graph(self.context)
            store = ControlStore(self.context)
            active = store.v3_active_artifact("ChapterBlueprint")
            base_revision = int(active["revision"]) if active is not None else 0
            blueprint = PlanningAgent(self.context).chapter_blueprint(graph, revision=base_revision + 1)
            proposal = PlanningAgent(self.context).proposal("ChapterBlueprint", blueprint, base_revision=base_revision, operation_id=operation_id or f"blueprint:{graph.revision}", upstream_revisions=(graph.revision,))
            if active is not None and active["dependency_fingerprint"] == proposal.dependency_fingerprint:
                return load_promoted_chapter_blueprint(self.context)
            stored = AgentProposalSandbox(self.context, role="planning_agent").submit(proposal)
            proposal_id = str(stored["proposal_id"])
            report = validate_and_record(self.context, proposal_id)
            if not report.passed:
                raise ControlPlaneError("V3_PROPOSAL_INVALID", f"ChapterBlueprint Proposal 验证未通过: {report.findings}")
            receipt = GateService(self.context).evaluate(proposal_id, gate_id="G2_BLUEPRINT_INTEGRITY")
            if receipt.verdict != "pass":
                raise ControlPlaneError("V3_GATE_BLOCKED", f"ChapterBlueprint 门禁阻断: {receipt.findings}")
            ArtifactPromotionService(self.context).promote(proposal_id, gate_receipt_ids=[receipt.receipt_id])
            return load_promoted_chapter_blueprint(self.context)

        if stage == "confirm_planning":
            from .artifact_promotion import GateService
            from control_plane import ControlStore

            active = ControlStore(self.context).v3_active_artifact("ChapterBlueprint")
            if active is None:
                raise ValueError("PLANNING_CONFIRM_BLOCKED: ChapterBlueprint 尚未晋级")
            return GateService(self.context).evaluate(active["proposal_id"], gate_id="H1_PLANNING_CONFIRM", reviewer="user")
        if stage == "sync_material_requirements":
            return MaterialRequirementsSynchronizer(self.context).sync()
        if stage == "compile_document_contract":
            return DocumentContractCompiler(self.context).compile()
        if stage == "plan_document":
            return DocumentPlanner(self.context).build()
        if stage == "execute_content_plan":
            from control_plane import ControlStore
            blueprint = ControlStore(self.context).v3_active_artifact("ChapterBlueprint")
            if blueprint is None or not ControlStore(self.context).has_v3_gate_receipt(blueprint["proposal_id"], "H1_PLANNING_CONFIRM"):
                raise ValueError("PLANNING_CONFIRM_REQUIRED")
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
