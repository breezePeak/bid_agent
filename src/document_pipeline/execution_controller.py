from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

from control_plane import CommandEnvelope, ControlPlaneError, ControlStore, WorkspaceContext

from .chapter_editing import ChapterEditingService
from .chapter_workspace import ChapterWorkspaceService
from .stage_runner import V3StageRunner
from .research_tool import V3ResearchTool


V3_PIPELINE_STAGES = (
    "ingest_inputs",
    "normalize_sources",
    "compile_template_structure",
    "build_requirement_ledger",
    "analyze_scores",
    "plan_response",
    "compile_chapter_blueprint",
    "confirm_planning",
    "sync_material_requirements",
    "compile_document_contract",
    "plan_document",
    "execute_content_plan",
    "integrate_document",
    "verify_document",
    "render_document",
    "verify_delivery",
)

V3_GENERATION_STAGES = (
    "sync_material_requirements",
    "compile_document_contract",
    "plan_document",
    "execute_content_plan",
    "integrate_document",
    "verify_document",
    "render_document",
    "verify_delivery",
)

V3_OUTLINE_STAGES = (
    "ingest_inputs",
    "normalize_sources",
    "compile_template_structure",
    "build_requirement_ledger",
    "analyze_scores",
    "plan_response",
    "compile_chapter_blueprint",
)

_STAGE_ARTIFACT_KIND = {
    "normalize_sources": "SourceIndex",
    "compile_template_structure": "TemplateStructureContract",
    "build_requirement_ledger": "RequirementLedger",
    "analyze_scores": "ScoreModel",
    "plan_response": "ProjectModel",
    "compile_chapter_blueprint": "ChapterBlueprint",
}


class V3ExecutionController:
    """CommandGateway-owned V3 execution entry point.

    The controller is deliberately synchronous for now: a command does not report
    success until its registered V3 stage has produced a verified result.  Web and
    CLI callers must submit a command to this controller rather than invoking a
    stage runner directly.
    """

    def __init__(self, context: WorkspaceContext, *, runner: V3StageRunner | None = None) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.runner = runner or V3StageRunner(context)

    @classmethod
    def for_deterministic_tests(
        cls,
        context: WorkspaceContext,
    ) -> "V3ExecutionController":
        return cls(
            context,
            runner=V3StageRunner.for_deterministic_tests(context),
        )

    def handlers(self) -> dict[str, Any]:
        chapters = ChapterWorkspaceService(self.context)
        editing = ChapterEditingService(self.context)
        return {
            "document.run_stage": self.run_stage,
            "document.prepare_outline": self.prepare_outline,
            "document.run_pipeline": self.run_pipeline,
            "document.confirm_planning": self.confirm_planning,
            "research.resolve": self.resolve_research,
            "chapter.workspace.create": chapters.handle_create,
            "chapter.workspace.ensure_all": chapters.handle_ensure_all,
            "chapter.workspace.archive": chapters.handle_archive,
            "chapter.workspace.save_metadata": chapters.handle_save_metadata,
            "chapter.context.save": chapters.handle_save_context,
            "chapter.content.apply": editing.handle_content_apply,
            "chapter.revision.restore": editing.handle_revision_restore,
            "chapter.generate_draft": editing.handle_generate_draft,
            "chapter.approval.confirm": editing.handle_approval_confirm,
            "chapter.batch.generate": self.generate_chapter_batch,
        }

    def generate_chapter_batch(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        """Run selected leaf chapters server-side and persist queue progress."""
        from .artifact_promotion import HumanGateService
        from .chapter_chat import ChapterChatService
        from .chapter_workspace import ChapterWorkspaceService
        from .global_project_context import GlobalProjectContextService

        chapter_ids = list(dict.fromkeys(
            str(item).strip() for item in (envelope.payload.get("chapter_ids") or []) if str(item).strip()
        ))
        if not chapter_ids:
            raise ControlPlaneError("CHAPTER_BATCH_EMPTY", "请选择至少一个可编写章节。", status_code=400)
        job = {
            "job_id": operation_id,
            "operation_id": operation_id,
            "status": "queued",
            "chapter_ids": chapter_ids,
            "current_chapter_id": "",
            "completed_count": 0,
            "failed_count": 0,
            "error": None,
            "items": [{"chapter_id": item, "status": "queued", "content_revision": 0} for item in chapter_ids],
        }
        self.store.upsert_agent_activity_state({**job, "phase": "chapter_batch"}, source="chapter_batch")
        try:
            # Validate every prerequisite before the first chapter is started.
            GlobalProjectContextService(context).load_model()
            HumanGateService(context).require_current_confirmation()
            chapters = ChapterWorkspaceService(context)
            for chapter_id in chapter_ids:
                chapter = chapters.get_chapter(chapter_id)
                if chapter.get("is_leaf") is False:
                    raise ControlPlaneError("CHAPTER_BODY_REQUIRES_LEAF", "批量编写只能选择叶子章节。", status_code=409)
                if not chapter.get("materialized"):
                    # Parent-node selection is expanded to its leaf descendants
                    # in the workbench.  Those leaves must be ready to write
                    # without requiring the user to visit each one first.
                    chapters.create(chapter_id=chapter_id)
                    chapter = chapters.get_chapter(chapter_id)
                chapter_context = chapter.get("context") if isinstance(chapter.get("context"), dict) else {}
                if not chapter_context.get("content_hash"):
                    raise ControlPlaneError(
                        "CHAPTER_CONTEXT_REQUIRED",
                        f"章节上下文未就绪: {chapter.get('title') or chapter_id}",
                        status_code=409,
                        details={"chapter_id": chapter_id, "chapter_title": chapter.get("title") or chapter_id},
                    )
        except ControlPlaneError as exc:
            job.update({"status": "blocked", "error": exc.as_dict()})
            self.store.upsert_agent_activity_state({**job, "phase": "blocked"}, source="chapter_batch")
            return {"accepted": False, "operation_status": "blocked", "message": exc.message, "error": exc.as_dict()}

        chat = ChapterChatService(context)
        for index, chapter_id in enumerate(chapter_ids):
            authority = chat.load_authority(chapter_id)
            if str(authority.get("mode") or "") == "human_review":
                error = {
                    "code": "CHAPTER_OUTLINE_REVIEW_REQUIRED",
                    "message": "该章节配置为人工审核，请先确认本章提纲后再继续批量编写。",
                    "retryable": True,
                    "details": {"chapter_id": chapter_id},
                }
                job["items"][index].update({"status": "blocked_human", "error": error})
                job.update({"status": "blocked_human", "error": error})
                self.store.record_stage_run(
                    operation_id,
                    f"chapter.batch:{chapter_id}",
                    "blocked_human",
                    disposition="chapter_outline_review_required",
                    error=error,
                )
                self.store.cancel_active_stage_runs(
                    operation_id,
                    disposition="chapter_outline_review_required",
                    error=error,
                )
                self.store.upsert_agent_activity_state(
                    {**job, "phase": "blocked_human"}, source="chapter_batch"
                )
                return {
                    "accepted": True,
                    "operation_status": "blocked_human",
                    "message": error["message"],
                    "error": error,
                }
            job["status"] = "running"
            job["current_chapter_id"] = chapter_id
            job["items"][index]["status"] = "running"
            self.store.upsert_agent_activity_state({**job, "phase": "writing"}, source="chapter_batch")
            self.store.record_stage_run(operation_id, f"chapter.batch:{chapter_id}", "running", disposition="chapter_batch")
            try:
                # Respect the chapter's persisted review authority.  A batch
                # command must never silently turn a human-review chapter into
                # an autonomous one.
                scoped = CommandEnvelope.from_mapping(
                    {**envelope.as_dict(), "kind": "document.run_pipeline", "payload": {"chapter_ids": [chapter_id]}},
                    workspace_id=context.workspace_id,
                )
                self.run_pipeline(context, scoped, operation_id)
                updated = chapters.get_chapter(chapter_id)
                revision = int(updated.get("head_content_revision") or 0)
                if revision <= 0:
                    raise ControlPlaneError("CHAPTER_DRAFT_COMMIT_REJECTED", "章节正文未写入中间文档。", status_code=409)
                job["items"][index].update({"status": "succeeded", "content_revision": revision})
                job["completed_count"] += 1
                self.store.record_stage_run(operation_id, f"chapter.batch:{chapter_id}", "succeeded", disposition="chapter_batch")
            except Exception as exc:
                error = self._stage_error(exc if isinstance(exc, Exception) else Exception(str(exc)))
                job["items"][index].update({"status": "failed", "error": error})
                job.update({"status": "failed", "failed_count": 1, "error": error})
                self.store.record_stage_run(operation_id, f"chapter.batch:{chapter_id}", "failed", disposition="chapter_batch", error=error)
                self.store.upsert_agent_activity_state({**job, "phase": "failed"}, source="chapter_batch")
                return {"accepted": False, "operation_status": "failed", "message": error["message"], "error": error}
            finally:
                self.store.upsert_agent_activity_state({**job, "phase": "writing"}, source="chapter_batch")
        job.update({"status": "succeeded", "current_chapter_id": ""})
        self.store.upsert_agent_activity_state({**job, "phase": "completed"}, source="chapter_batch")
        return {"accepted": True, "operation_status": "succeeded", "message": f"已完成 {job['completed_count']} 个章节的编写。"}

    def _active_artifact_identity(self, stage: str) -> tuple[str, int, str] | None:
        kind = _STAGE_ARTIFACT_KIND.get(stage)
        if kind is None:
            return None
        active = self.store.v3_active_artifact(kind)
        if active is None:
            return None
        return (
            str(active.get("artifact_id") or ""),
            int(active.get("revision") or 0),
            str(active.get("artifact_hash") or ""),
        )

    def _runner_policy_scope(self):
        scope = getattr(self.runner, "validation_policy_scope", None)
        return scope() if callable(scope) else nullcontext()

    def _stage_warnings(
        self,
        stage: str,
        result: Any,
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        consume = getattr(self.runner, "consume_stage_warnings", None)
        if callable(consume):
            warnings.extend(
                item
                for item in consume(stage)
                if isinstance(item, dict)
            )
        if isinstance(result, dict):
            warnings.extend(
                item
                for item in (result.get("warnings") or [])
                if isinstance(item, dict)
            )
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in warnings:
            key = (
                str(item.get("code") or ""),
                str(item.get("message") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)
        return deduplicated

    def _stage_output(self, stage: str, result: Any) -> dict[str, Any]:
        warnings = self._stage_warnings(stage, result)
        metrics: dict[str, Any] = {}
        consume_metrics = getattr(self.runner, "consume_stage_metrics", None)
        if callable(consume_metrics):
            value = consume_metrics(stage)
            if isinstance(value, dict):
                metrics = value
        return {
            "summary": self._stage_summary(stage, result),
            "warnings": warnings,
            "warning_count": len(warnings),
            "gate_outcome": "warn" if warnings else "pass",
            **metrics,
        }

    def _stage_reuse_output(self, stage: str) -> dict[str, Any]:
        metrics_fn = getattr(self.runner, "stage_reuse_metrics", None)
        if not callable(metrics_fn):
            return {}
        value = metrics_fn(stage)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _stage_error(exc: Exception) -> dict[str, Any]:
        details = getattr(exc, "details", {})
        normalized_details = dict(details) if isinstance(details, dict) else {}
        diagnostics = getattr(exc, "errors", ())
        if callable(diagnostics):
            try:
                diagnostics = diagnostics()
            except Exception:
                diagnostics = ()
        if diagnostics:
            normalized_details["diagnostics"] = [
                str(item) for item in diagnostics
            ]
        attempts = getattr(exc, "attempts", None)
        if attempts is not None:
            normalized_details["attempts"] = int(attempts)
        return {
            "code": str(
                getattr(exc, "code", "")
                or type(exc).__name__
            ),
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
            "details": normalized_details,
        }

    @staticmethod
    def _stage_summary(stage: str, result: Any) -> dict[str, Any]:
        if stage == "plan_document" and isinstance(result, tuple):
            units = result[1] if len(result) > 1 and isinstance(result[1], list) else []
            return {"content_unit_count": len(units)}
        if stage == "execute_content_plan" and isinstance(result, list):
            blocks = [
                block
                for unit_blocks in result
                if isinstance(unit_blocks, list)
                for block in unit_blocks
            ]
            return {
                "content_unit_count": len(result),
                "block_count": len(blocks),
                "character_count": sum(
                    len(str(getattr(block, "content", "") or ""))
                    for block in blocks
                ),
            }
        if stage == "integrate_document":
            blocks = getattr(result, "blocks", [])
            return {"block_count": len(blocks) if isinstance(blocks, list) else 0}
        if stage == "verify_document":
            findings = getattr(result, "findings", [])
            return {
                "verdict": str(getattr(result, "verdict", "") or ""),
                "issue_count": len(findings) if isinstance(findings, list) else 0,
            }
        if stage == "render_document":
            paths = result if isinstance(result, tuple) else (result,)
            filenames = [
                item.name
                for item in paths
                if isinstance(item, Path)
            ]
            return {
                "status": "rendered" if filenames else "unknown",
                "output_files": filenames,
            }
        if isinstance(result, dict):
            safe_keys = (
                "status",
                "verdict",
                "mode",
                "output_path",
                "artifact_path",
                "block_count",
                "warning_count",
                "issue_count",
            )
            return {key: result.get(key) for key in safe_keys if key in result}
        dump = getattr(result, "model_dump", None)
        if callable(dump):
            value = dump(mode="json")
            if isinstance(value, dict):
                return {
                    key: value.get(key)
                    for key in (
                        "status",
                        "verdict",
                        "mode",
                        "revision",
                    )
                    if key in value
                }
        return {}

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
        disposition = "v3_command"
        self.store.record_stage_run(operation_id, stage, "queued", disposition=disposition)
        self.store.record_stage_run(operation_id, stage, "running", disposition=disposition)
        try:
            with self._runner_policy_scope():
                result = self.runner.run(stage, operation_id=operation_id)
        except Exception as exc:
            self.store.record_stage_run(
                operation_id,
                stage,
                "failed",
                disposition=disposition,
                error=self._stage_error(exc),
            )
            raise
        if stage == "confirm_planning" and isinstance(result, dict) and result.get("verdict") == "needs_human":
            self.store.record_stage_run(operation_id, stage, "blocked_human", disposition="planning_confirmation_required")
            return {
                "accepted": True,
                "operation_status": "blocked_human",
                "message": "规划已生成，等待已认证用户在统一规划页确认。",
                "planning_snapshot": result.get("planning_snapshot") if isinstance(result, dict) else None,
            }
        self.store.record_stage_run(
            operation_id,
            stage,
            "succeeded",
            disposition=disposition,
            output=self._stage_output(stage, result),
        )
        return {"accepted": True, "operation_status": "succeeded", "message": f"V3 阶段完成: {stage}"}

    def run_pipeline(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        # document.run_pipeline starts after the separately confirmed outline.
        # Re-running ingestion/score analysis/outline compilation here both wastes
        # time and can invalidate the exact chapter IDs the user just confirmed.
        stages = V3_GENERATION_STAGES
        requested_chapter_ids = [
            str(item).strip()
            for item in (envelope.payload.get("chapter_ids") or [])
            if str(item).strip()
        ]
        set_scope = getattr(self.runner, "set_generation_scope", None)
        if callable(set_scope):
            set_scope(requested_chapter_ids)
        if requested_chapter_ids:
            stages = V3_GENERATION_STAGES[:4]
        completed: list[str] = []
        for stage in stages:
            self.store.record_stage_run(
                operation_id,
                stage,
                "queued",
                disposition="v3_pipeline",
            )
        for stage in stages:
            self.store.record_stage_run(
                operation_id,
                stage,
                "running",
                disposition="v3_pipeline",
            )
            try:
                with self._runner_policy_scope():
                    result = self.runner.run(stage, operation_id=operation_id)
            except Exception as exc:
                error = self._stage_error(exc)
                blocked_code = getattr(exc, "code", "")
                if blocked_code in {
                    "WRITER_RESEARCH_ACTION_REQUIRED",
                    "WRITER_MODEL_ACTION_REQUIRED",
                    "TECHNICAL_DRAFT_READY",
                }:
                    disposition = (
                        "writer_model_action_required"
                        if blocked_code == "WRITER_MODEL_ACTION_REQUIRED"
                        else (
                            "technical_draft_ready"
                            if blocked_code == "TECHNICAL_DRAFT_READY"
                            else "writer_research_action_required"
                        )
                    )
                    self.store.record_stage_run(
                        operation_id,
                        stage,
                        "paused",
                        disposition=disposition,
                        error=error,
                    )
                    self.store.cancel_active_stage_runs(
                        operation_id,
                        disposition=disposition,
                        error=error,
                    )
                    return {
                        "accepted": True,
                        "operation_status": "blocked",
                        "message": (
                            str(getattr(exc, "message", "") or "")
                            or (
                                "写作模型输出无法解析；任务已在当前章节暂停，可直接重试该章节。"
                                if blocked_code == "WRITER_MODEL_ACTION_REQUIRED"
                                else (
                                    "技术章节已写完；商务部分和价格部分按当前要求暂不写入。"
                                    if blocked_code == "TECHNICAL_DRAFT_READY"
                                    else "写作 Agent 需要公开资料检索；请完成当前 Provider 的检索后重新生成以继续当前章节。"
                                )
                            )
                        ),
                        "completed_stages": completed,
                        "error": error,
                    }
                self.store.record_stage_run(
                    operation_id,
                    stage,
                    "failed",
                    disposition="v3_pipeline",
                    error=error,
                )
                self.store.cancel_active_stage_runs(
                    operation_id,
                    disposition="upstream_stage_failed",
                    error=error,
                )
                raise
            if stage == "confirm_planning" and isinstance(result, dict) and result.get("verdict") == "needs_human":
                self.store.record_stage_run(operation_id, stage, "blocked_human", disposition="planning_confirmation_required")
                self.store.cancel_active_stage_runs(
                    operation_id,
                    disposition="planning_confirmation_required",
                )
                return {
                    "accepted": True,
                    "operation_status": "blocked_human",
                    "message": "规划已生成，等待已认证用户在统一规划页确认。",
                    "completed_stages": completed,
                    "planning_snapshot": result.get("planning_snapshot") if isinstance(result, dict) else None,
                }
            self.store.record_stage_run(
                operation_id,
                stage,
                "succeeded",
                disposition="v3_pipeline",
                output=self._stage_output(stage, result),
            )
            completed.append(stage)
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": (
                f"指定章节写作完成: {len(requested_chapter_ids)} 个章节范围"
                if requested_chapter_ids
                else f"V3 Pipeline 完成: {len(completed)} 个阶段"
            ),
            "completed_stages": completed,
            "chapter_ids": requested_chapter_ids,
        }

    def prepare_outline(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        """Build the promoted score-aware ChapterBlueprint and stop before H1/writing."""

        from .artifact_promotion import HumanGateService

        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        review_feedback = str(payload.get("review_feedback") or "").strip()
        base_blueprint_hash = str(payload.get("base_blueprint_hash") or "").strip()
        project_feedback = str(payload.get("project_feedback") or "").strip()
        if project_feedback:
            request_project_revision = getattr(
                self.runner,
                "request_project_revision",
                None,
            )
            if callable(request_project_revision):
                request_project_revision(project_feedback)
        if review_feedback:
            active_blueprint = self.store.v3_active_artifact("ChapterBlueprint")
            active_hash = str((active_blueprint or {}).get("artifact_hash") or "")
            if not active_hash or active_hash != base_blueprint_hash:
                raise ControlPlaneError(
                    "PLANNING_REVIEW_STALE",
                    "目录版本已变化，请刷新后对最新目录发表意见。",
                    status_code=409,
                )
            request_revision = getattr(self.runner, "request_outline_revision", None)
            if callable(request_revision):
                request_revision(review_feedback)

        completed: list[str] = []
        reused: list[str] = []
        for stage in V3_OUTLINE_STAGES:
            can_reuse = getattr(self.runner, "can_reuse_stage", None)
            if (
                callable(can_reuse)
                and bool(can_reuse(stage))
                and not (review_feedback and stage == "compile_chapter_blueprint")
            ):
                self.store.record_stage_run(
                    operation_id,
                    stage,
                    "reused",
                    disposition="v3_outline_reused",
                    output=self._stage_reuse_output(stage),
                )
                completed.append(stage)
                reused.append(stage)
                continue
            before_identity = self._active_artifact_identity(stage)
            self.store.record_stage_run(
                operation_id,
                stage,
                "running",
                disposition="v3_outline_command",
            )
            try:
                with self._runner_policy_scope():
                    result = self.runner.run(stage, operation_id=operation_id)
                if stage == "analyze_scores" and not getattr(result, "points", []):
                    raise ControlPlaneError(
                        "V3_SCORE_POINTS_NOT_FOUND",
                        "未从招标文件或评分附件中识别到评分点，已停止生成评分目录。",
                        status_code=409,
                    )
            except Exception as exc:
                error = self._stage_error(exc)
                project_action_required = (
                    stage == "plan_response"
                    and (
                        type(exc).__name__.startswith("PlanningInference")
                        or bool(getattr(exc, "retryable", False))
                        or "缺少可保留的项目" in str(exc)
                    )
                )
                if project_action_required:
                    self.store.record_stage_run(
                        operation_id,
                        stage,
                        "paused",
                        disposition="project_understanding_action_required",
                        error=error,
                    )
                    return {
                        "accepted": True,
                        "operation_status": "blocked",
                        "message": (
                            "全局项目事实已完成自动修复尝试，但仍需要处理。"
                            "请重试、输入修改意见，或补充提示中缺少的材料。"
                        ),
                        "completed_stages": completed,
                        "error": error,
                        "action_required": {
                            "kind": "project_understanding",
                            "actions": ["retry", "feedback", "later"],
                        },
                    }
                self.store.record_stage_run(
                    operation_id,
                    stage,
                    "failed",
                    disposition="v3_outline_command",
                    error=error,
                )
                raise
            after_identity = self._active_artifact_identity(stage)
            was_reused = (
                before_identity is not None
                and after_identity == before_identity
            )
            self.store.record_stage_run(
                operation_id,
                stage,
                "reused" if was_reused else "succeeded",
                disposition=(
                    "v3_outline_reused"
                    if was_reused
                    else "v3_outline_command"
                ),
                output=self._stage_output(stage, result),
            )
            if was_reused:
                reused.append(stage)
            completed.append(stage)
        planning_snapshot = HumanGateService(self.context).planning_snapshot()
        workspaces = ChapterWorkspaceService(self.context).ensure_all(
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        self.store.record_stage_run(
            operation_id,
            "confirm_planning",
            "blocked_human",
            disposition="planning_confirmation_required",
        )
        return {
            "accepted": True,
            "operation_status": "blocked_human",
            "message": "评分点解析与章节目录草案已生成，等待审阅确认。",
            "completed_stages": completed,
            "reused_stages": reused,
            "review_feedback_applied": bool(review_feedback),
            "planning_snapshot": planning_snapshot,
            "chapter_workspaces": workspaces,
        }

    def confirm_planning(self, context: WorkspaceContext, envelope: CommandEnvelope, operation_id: str) -> dict[str, Any]:
        from .artifact_promotion import HumanGateService

        actor = envelope.actor if isinstance(envelope.actor, dict) else {}
        principal_id = str(actor.get("id") or "").strip()
        if actor.get("type") != "user" or not principal_id:
            raise ValueError("AUTH_REQUIRED: ConfirmPlanning 只接受 API 认证用户。")
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        if payload.get("decision") != "confirm":
            raise ValueError("PLANNING_CONFIRM_DECISION_REQUIRED")
        snapshot = payload.get("planning_snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("PLANNING_CONFIRM_SNAPSHOT_REQUIRED")
        receipt = HumanGateService(self.context).confirm_planning(
            principal_id=principal_id,
            submitted_snapshot=snapshot,
            nonce=envelope.command_id,
        )
        self.store.record_stage_run(operation_id, "confirm_planning", "succeeded", disposition="explicit_human_confirmation")
        workspaces = ChapterWorkspaceService(self.context).ensure_all(actor=actor)
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": "规划已由认证用户确认，H1 Receipt 已签发。",
            "planning_receipt": receipt.model_dump(mode="json"),
            "chapter_workspaces": workspaces,
        }
