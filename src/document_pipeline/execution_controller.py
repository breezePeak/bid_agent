from __future__ import annotations

from typing import Any

from control_plane import CommandEnvelope, ControlStore, WorkspaceContext

from .stage_runner import V3StageRunner
from .research_tool import V3ResearchTool


V3_PIPELINE_STAGES = (
    "ingest_inputs",
    "normalize_sources",
    "compile_template_structure",
    "build_requirement_ledger",
    "build_project_model",
    "sync_material_requirements",
    "compile_document_contract",
    "plan_document",
    "execute_content_plan",
    "integrate_document",
    "verify_document",
    "render_document",
    "verify_delivery",
)


class V3ExecutionController:
    """CommandGateway-owned V3 execution entry point.

    The controller is deliberately synchronous for now: a command does not report
    success until its registered V3 stage has produced a verified result.  Web and
    CLI callers must submit a command to this controller rather than invoking a
    stage runner directly.
    """

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.runner = V3StageRunner(context)

    def handlers(self) -> dict[str, Any]:
        return {
            "document.run_stage": self.run_stage,
            "document.run_pipeline": self.run_pipeline,
            "research.resolve": self.resolve_research,
        }

    def resolve_research(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        need_id = str(envelope.payload.get("need_id") or "").strip()
        if not need_id:
            raise ValueError("V3_EVIDENCE_NEED_REQUIRED")
        attachment_input_ids = envelope.payload.get("attachment_input_ids", [])
        if not isinstance(attachment_input_ids, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in attachment_input_ids
        ):
            raise ValueError("V3_RESEARCH_ATTACHMENT_INPUT_IDS_INVALID")
        result = V3ResearchTool(context).invoke(
            need_id,
            provider_id=str(envelope.payload.get("provider_id") or "").strip() or None,
            attachment_input_ids=attachment_input_ids,
        )
        batch = result["batch"]
        if batch["status"] == "failed":
            message = str(batch.get("error") or "外部研究失败。")
            self.store.record_stage_run(
                operation_id,
                f"research.resolve:{need_id}",
                "failed",
                disposition="v3_agent_tool",
            )
            return {
                "accepted": False,
                "operation_status": "failed",
                "message": message,
                "error": {"code": "V3_RESEARCH_FAILED", "message": message},
                **result,
            }
        self.store.record_stage_run(
            operation_id,
            f"research.resolve:{need_id}",
            "succeeded",
            disposition="v3_agent_tool",
        )
        message = (
            f"研究完成，写入 {len(batch['items'])} 项可核验证据。"
            if batch["status"] == "published"
            else "研究完成，但没有找到可核验的公开证据。"
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": message,
            **result,
        }

    def run_stage(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        stage = str(envelope.payload.get("stage") or "").strip()
        if stage not in V3_PIPELINE_STAGES:
            raise ValueError(f"V3_UNKNOWN_STAGE: {stage or '<empty>'}")
        self.runner.run(stage)
        self.store.record_stage_run(operation_id, stage, "succeeded", disposition="v3_command")
        return {"accepted": True, "operation_status": "succeeded", "message": f"V3 阶段完成: {stage}"}

    def run_pipeline(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        completed: list[str] = []
        for stage in V3_PIPELINE_STAGES:
            self.runner.run(stage)
            self.store.record_stage_run(operation_id, stage, "succeeded", disposition="v3_command")
            completed.append(stage)
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"V3 Pipeline 完成: {len(completed)} 个阶段",
            "completed_stages": completed,
        }
