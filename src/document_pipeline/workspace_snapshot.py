from __future__ import annotations

from datetime import datetime
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json

from .contracts import ContentBlock, WriterInputBundle
from .document_planner import CONTENT_UNITS_PATH
from .input_manifest import V3_ROOT
from .research_service import load_published_batch
from .writer_bundle import BUNDLE_DIR
from .writer_policy import assess_content_unit, registered_content_path
from .workspace_modes import workspace_capabilities


class V3WorkspaceSnapshotBuilder:
    """Read-only projection of V3 artifacts and control-plane execution evidence."""

    _ANALYSIS_INPUT_ROLES = frozenset(
        {"tender", "score", "amendment", "template"}
    )
    # The outline and full-document runs are two independent user-visible
    # phases.  Keep the analysis projection pinned to the latest outline run;
    # generation has its own snapshot below.  Mixing both command kinds here
    # made generation stage runs appear inside the phase-2 activity stream.
    _ANALYSIS_COMMAND_KINDS = frozenset({"document.prepare_outline"})
    _ANALYSIS_CHAIN = (
        "InputManifest",
        "SourceIndex",
        "RequirementLedger",
        "ScoreModel",
        "ProjectModel",
        "ChapterBlueprint",
    )
    _PIPELINE_STAGE_LABELS = {
        "normalize_sources": "来源结构解析",
        "build_requirement_ledger": "招标需求提取",
        "score_structure": "评分结构解析",
        "score_semantic": "评分理解批次",
        "plan_response": "全局项目事实生成",
        # Historical telemetry remains readable even though these stages are
        # no longer members of the automatic pipeline.
        "project_understanding": "项目整体理解（历史）",
        "topic_duty_planning": "响应主题规划（历史）",
        "compile_chapter_blueprint": "评分目录生成与覆盖校验",
        "confirm_planning": "人工确认",
    }
    _GENERATION_STAGE_LABELS = {
        "ingest_inputs": "输入检查",
        "normalize_sources": "来源解析",
        "compile_template_structure": "模板处理",
        "build_requirement_ledger": "需求提取",
        "analyze_scores": "评分理解",
        "plan_response": "全局项目事实",
        "compile_chapter_blueprint": "目录生成",
        "confirm_planning": "目录确认",
        "sync_material_requirements": "检查材料与证据缺口",
        "compile_document_contract": "锁定确认后的文档结构",
        "plan_document": "生成逐章写作任务",
        "chapter_writing": "章节写作",
        "integrate_document": "全文整合",
        "verify_document": "质量审核",
        "render_document": "Word 渲染",
        "verify_delivery": "交付验证",
    }
    _LEGACY_GENERATION_STAGE_ALIASES = {
        "execute_content_plan": "chapter_writing",
    }

    @classmethod
    def _canonical_generation_stage(cls, stage_id: Any) -> str:
        normalized = str(stage_id or "").strip()
        return cls._LEGACY_GENERATION_STAGE_ALIASES.get(normalized, normalized)

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> dict[str, Any]:
        control = ControlStore(self.context)
        document_state = control.document_state()
        writing_mode = str(document_state["writing_mode"])
        chapter_plan_flow = str(document_state["chapter_plan_flow"])
        capabilities = workspace_capabilities(writing_mode, chapter_plan_flow)
        artifacts = {item["artifact_kind"]: item for item in control.v3_promoted_artifacts()}

        def payload(kind: str) -> dict[str, Any] | None:
            value = artifacts.get(kind, {}).get("payload")
            return value if isinstance(value, dict) else None

        source_index = payload("SourceIndex")
        requirement_ledger = payload("RequirementLedger")
        score_model = payload("ScoreModel")
        project_model = payload("ProjectModel")
        response_topic_graph = payload("ResponseTopicGraph")
        contract = payload("DocumentContract") or payload("TemplateStructureContract")
        plan = payload("ChapterBlueprint")
        content_blocks = payload("ContentBlock")
        quality = payload("AuditReport")
        delivery = payload("DeliveryReceipt")
        artifact_states = self._artifact_states(control, artifacts)
        latest_analysis_operation = self._latest_analysis_operation(control, artifacts)
        stale_artifact_kinds = [
            kind
            for kind in self._ANALYSIS_CHAIN
            if artifacts.get(kind) is not None and artifact_states.get(kind) is False
        ]
        result_outdated = bool(
            stale_artifact_kinds
            or latest_analysis_operation.get("result_outdated")
        )
        has_complete_chain = all(
            artifacts.get(kind) is not None and artifact_states.get(kind) is True
            for kind in self._ANALYSIS_CHAIN
        )
        if result_outdated:
            analysis_status = "failed" if latest_analysis_operation.get("result_outdated") else "stale"
        elif has_complete_chain:
            analysis_status = "current"
        elif artifacts.get("InputManifest") is not None:
            analysis_status = "incomplete"
        else:
            analysis_status = "not_ready"

        planning: dict[str, Any] = {"status": "not_ready"}
        if artifacts.get("ChapterBlueprint"):
            if result_outdated:
                planning = {
                    "status": "outdated",
                    "reason": (
                        "latest_analysis_failed"
                        if latest_analysis_operation.get("result_outdated")
                        else "artifact_dependencies_stale"
                    ),
                }
            elif not any(
                isinstance(item, dict) and str(item.get("chapter_id") or "").strip()
                for item in (plan or {}).get("nodes", [])
            ):
                # An empty blueprint cannot be meaningfully reviewed or
                # confirmed.  Do not expose the H1 gate as actionable until
                # the directory-generation result contains chapter nodes.
                planning = {
                    "status": "blocked",
                    "reason": "PLANNING_OUTLINE_EMPTY",
                    "message": "目录未生成任何章节，无法人工确认；请重新生成目录。",
                }
            else:
                from .artifact_promotion import HumanGateService

                service = HumanGateService(self.context)
                try:
                    receipt = service.require_current_confirmation()
                    planning = {
                        "status": "confirmed",
                        "receipt_id": receipt.receipt_id,
                        "warnings": service.runtime_change_warnings(),
                    }
                except Exception:
                    try:
                        planning = {"status": "needs_human", "snapshot": service.planning_snapshot()}
                    except ControlPlaneError as exc:
                        # A changed planning runtime means the existing outline
                        # is intentionally no longer confirmable.  Project this
                        # as an outdated result instead of a generic "blocked"
                        # state: the latter hides both the confirmation and the
                        # re-planning actions, leaving the user at a dead end.
                        planning = {
                            "status": "outdated"
                            if exc.code == "PLANNING_CONFIRM_STALE"
                            else "blocked",
                            "reason": exc.code,
                            "message": exc.message,
                        }
                    except Exception:
                        planning = {"status": "blocked"}
        # A confirmed directory without its shared ProjectModel is a legacy
        # partial run, not a writable workspace.  Keep the old directory for
        # audit, but send the user back through planning so the missing stage
        # can be generated and recorded in a new operation.
        if (
            planning.get("status") == "confirmed"
            and artifact_states.get("ProjectModel") is not True
        ):
            planning = {
                "status": "outdated",
                "reason": "PROJECT_MODEL_REQUIRED",
                "message": "当前目录缺少全局项目事实，请重新进入目录流程补齐后再编写章节。",
            }
        scheduled_needs = {
            str(item.get("need_id") or ""): item
            for item in control.evidence_needs()
            if str(item.get("need_id") or "")
        }
        projected_needs: list[dict[str, Any]] = []
        for candidate in (score_model or {}).get(
            "evidence_need_candidates",
            (project_model or {}).get("evidence_needs", []),
        ):
            if not isinstance(candidate, dict):
                continue
            need_id = str(candidate.get("need_id") or "")
            scheduled = scheduled_needs.pop(need_id, None)
            projected_needs.append(
                scheduled
                or {
                    **candidate,
                    "status": str(candidate.get("status") or "open"),
                    "topic_id": str(
                        candidate.get("topic_id")
                        or (
                            f"score:{candidate.get('score_point_id')}"
                            if candidate.get("score_point_id")
                            else "unscoped"
                        )
                    ),
                    "blocking_scope": str(
                        candidate.get("blocking_scope") or "content_unit"
                    ),
                    "deadline_stage": str(
                        candidate.get("deadline_stage")
                        or "chapter_writing"
                    ),
                    "query_budget": int(candidate.get("query_budget") or 5),
                }
            )
        projected_needs.extend(scheduled_needs.values())
        from .writer_research import WRITER_RESEARCH_REPORT_PATH

        writer_research_path = self.root / WRITER_RESEARCH_REPORT_PATH
        writer_research = (
            read_json(writer_research_path)
            if writer_research_path.is_file()
            else {}
        )
        generation = self._generation_snapshot(
            control,
            plan=plan or {},
            writer_research=(writer_research if isinstance(writer_research, dict) else {}),
            delivery=delivery or {},
        )
        analysis_pipeline = self._analysis_pipeline(
            control,
            artifacts,
            artifact_states,
            latest_analysis_operation,
            planning_confirmed=planning.get("status") == "confirmed",
            planning_status=str(planning.get("status") or "not_ready"),
        )
        chapters = self._chapters_snapshot(control, plan or {})
        phase_states = control.workflow_phase_states()
        workflow = self._workflow_projection(
            planning=planning,
            analysis_pipeline=analysis_pipeline,
            generation=generation,
            chapters=chapters,
            blueprint_artifact=artifacts.get("ChapterBlueprint") or {},
            project_model_current=artifact_states.get("ProjectModel") is True,
            phase_states=phase_states,
        )
        return {
            "schema_version": "v3",
            "workspace_id": self.context.workspace_id,
            "workspace_revision": control.revision(),
            "writing_mode": writing_mode,
            "chapter_plan_flow": chapter_plan_flow,
            "capabilities": capabilities,
            # Files in workspace/v3 may be drafts or legacy compatibility
            # outputs. They are intentionally invisible here until a Receipt
            # promotes an artifact through ArtifactPromotionService.
            "inputs": payload("InputManifest"),
            "promoted_artifacts": list(artifacts.values()),
            # Read-only compatibility projections for historical workspaces.
            # New runs never require or create either artifact.
            "project_model": project_model,
            "response_topic_graph": response_topic_graph,
            "document": {
                "mode": (contract or {}).get("mode"),
                "contract": contract,
                "plan": plan,
                "integrated": payload("IntegratedDocument"),
                "delivery": delivery,
            },
            "analysis": {
                "source_index": source_index,
                "requirement_ledger": requirement_ledger,
                "score_model": score_model,
                "chapter_blueprint": plan,
                "status": analysis_status,
                "stale": result_outdated,
                "stale_artifact_kinds": stale_artifact_kinds,
                "artifact_states": artifact_states,
                "current_input_manifest_revision": int((payload("InputManifest") or {}).get("revision") or 0),
                "source_input_manifest_revision": int((source_index or {}).get("input_manifest_revision") or 0),
                "latest_operation": latest_analysis_operation or None,
                "pipeline": analysis_pipeline,
            },
            "planning": planning,
            "workflow": workflow,
            "evidence_needs": projected_needs,
            "generation": generation,
            "chapter_write_job": (
                control.agent_activity_state()
                if (control.agent_activity_state() or {}).get("control_source") == "chapter_batch"
                else None
            ),
            "materials": payload("EvidenceRepository"),
            "content_units": (content_blocks or {}).get("units", []),
            "chapters": chapters,
            "quality": {
                "coverage": (quality or {}).get("coverage"),
                "report": quality,
                "gates": control.latest_gate_evaluations(),
            },
        }

    @staticmethod
    def _workflow_projection(
        *,
        planning: dict[str, Any],
        analysis_pipeline: dict[str, Any],
        generation: dict[str, Any],
        chapters: dict[str, Any],
        blueprint_artifact: dict[str, Any],
        project_model_current: bool,
        phase_states: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """The single UI-facing workflow truth.

        Historical artifacts remain visible through their legacy projections,
        but this value is deliberately driven by the latest operation and the
        current review receipts only.
        """
        planning_status = str(planning.get("status") or "not_ready")
        analysis_status = str(analysis_pipeline.get("status") or "not_started")
        generation_status = str(generation.get("status") or "not_started")
        operation_id = str(analysis_pipeline.get("operation_id") or "")
        stages = list(analysis_pipeline.get("stages") or [])
        phase = "materials"
        status = "not_started"
        current_stage_id = ""
        pending_reviews: list[dict[str, Any]] = []

        active_stage = next(
            (
                item for item in stages
                if isinstance(item, dict)
                and str(item.get("status") or "") in {"queued", "running"}
            ),
            None,
        )
        if active_stage:
            current_stage_id = str(active_stage.get("stage_id") or "")

        # The latest operation wins over historical artifacts and receipts.
        # In particular, an old confirmed directory must never hide a failed
        # or paused plan_response attempt from the current operation.
        if analysis_status == "failed" or any(
            str(item.get("status") or "") == "failed"
            for item in stages
            if isinstance(item, dict)
        ):
            phase = "planning"
            status = "failed"
        elif analysis_status in {"blocked", "paused"} or any(
            str(item.get("status") or "") in {"blocked", "paused"}
            for item in stages
            if isinstance(item, dict)
        ):
            phase = "planning"
            status = "needs_handling"
            paused_stage = next(
                (
                    item for item in stages
                    if isinstance(item, dict)
                    and str(item.get("status") or "") in {"blocked", "paused"}
                ),
                None,
            )
            if paused_stage:
                current_stage_id = str(paused_stage.get("stage_id") or "")
        elif analysis_status in {"queued", "running", "processing"}:
            phase = "planning"
            status = "running"
        elif planning_status == "confirmed" and project_model_current:
            phase = "writing"
            status = generation_status if generation_status != "not_started" else "ready"
            operation_id = str(generation.get("operation_id") or operation_id)
            current_stage_id = str(generation.get("current_stage_id") or "")
        elif planning_status == "needs_human":
            phase = "planning_review"
            status = "blocked_human"
            payload = blueprint_artifact.get("payload") or {}
            pending_reviews.append(
                {
                    "review_id": "planning:"
                    + str(blueprint_artifact.get("artifact_hash") or payload.get("artifact_hash") or "current"),
                    "kind": "planning",
                    "status": "pending",
                    "title": "目录已生成，等待审核",
                    "summary": "请核验评分点覆盖、章节结构和响应任务。",
                    "target_revision": int(blueprint_artifact.get("revision") or payload.get("revision") or 0),
                    "target_hash": str(blueprint_artifact.get("artifact_hash") or ""),
                    "items": [
                        {
                            "label": "章节节点",
                            "value": len(payload.get("nodes") or []),
                        }
                    ],
                }
            )
        elif planning_status == "outdated":
            phase = "planning"
            status = "failed"
            current_stage_id = "plan_response" if not project_model_current else "compile_chapter_blueprint"

        for chapter in (chapters.get("items") or []):
            if not isinstance(chapter, dict):
                continue
            if str(chapter.get("approval_status") or "") not in {
                "pending",
                "draft",
            }:
                continue
            chapter_id = str(chapter.get("chapter_id") or "")
            if not chapter_id:
                continue
            pending_reviews.append(
                {
                    "review_id": f"chapter:{chapter_id}:{int(chapter.get('head_content_revision') or 0)}",
                    "kind": "chapter_content",
                    "status": "pending",
                    "title": f"章节待审核：{chapter.get('title') or chapter_id}",
                    "summary": "确认当前正文版本，或提交修改意见。",
                    "chapter_id": chapter_id,
                    "target_revision": int(chapter.get("head_content_revision") or 0),
                    "target_hash": str(chapter.get("head_content_hash") or ""),
                    "items": [],
                }
            )

        # Phase identity and status are persisted operation fields.  Artifact
        # and chapter projections above only enrich review/detail sections.
        explicit_phase = "materials"
        explicit_state = phase_states.get("materials") or {}
        planning_state = phase_states.get("planning") or {}
        writing_state = phase_states.get("writing") or {}
        planning_phase_status = str(planning_state.get("phase_status") or "not_started")
        writing_phase_status = str(writing_state.get("phase_status") or "not_started")
        if planning_phase_status in {
            "running", "waiting_confirmation", "failed", "outdated", "blocked",
        }:
            explicit_phase, explicit_state = "planning", planning_state
        elif writing_phase_status != "not_started":
            explicit_phase, explicit_state = "writing", writing_state
        elif planning_phase_status != "not_started":
            explicit_phase, explicit_state = "planning", planning_state
        phase = explicit_phase
        status = str(explicit_state.get("phase_status") or "not_started")
        operation_id = str(explicit_state.get("operation_id") or "")

        return {
            "phase": phase,
            "status": status,
            "operation_id": operation_id,
            "phase_states": phase_states,
            "attempt": max(
                (int(item.get("attempt") or 0) for item in stages if isinstance(item, dict)),
                default=0,
            ),
            "current_stage_id": current_stage_id,
            "stages": stages,
            "pending_reviews": pending_reviews,
            "can_resume": status in {"failed", "needs_handling"},
            "current_artifact": {
                "kind": "ChapterBlueprint",
                "revision": int(blueprint_artifact.get("revision") or 0),
                "hash": str(blueprint_artifact.get("artifact_hash") or ""),
                "is_current": (
                    phase in {"planning_review", "writing"}
                    and planning_status in {"needs_human", "confirmed"}
                ),
            },
            "invalidation_reason": str(
                planning.get("reason")
                or planning.get("message")
                or ""
            ),
        }

    def _chapters_snapshot(
        self,
        control: ControlStore,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        from .chapter_workspace import ChapterWorkspaceService

        try:
            snapshot = ChapterWorkspaceService(self.context).list_chapters(
                include_archived=True
            )
        except ControlPlaneError:
            materializations = control.chapter_workspaces(include_archived=True)
            nodes = [
                item
                for item in (plan.get("nodes") or [])
                if isinstance(item, dict) and str(item.get("chapter_id") or "").strip()
            ]
            snapshot = {
                "blueprint_revision": int(plan.get("revision") or 0),
                "blueprint_hash": "",
                "total": len(nodes) or len(materializations),
                "materialized": len(materializations),
                "active": sum(
                    1 for item in materializations if item.get("status") == "active"
                ),
                "archived": sum(
                    1 for item in materializations if item.get("status") == "archived"
                ),
                "items": materializations,
            }
        status_counts: dict[str, int] = {}
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict) or not item.get("materialized", True):
                continue
            chapter_id = str(item.get("chapter_id") or "")
            try:
                writing_plan = control.chapter_writing_plan(chapter_id)
            except ControlPlaneError:
                writing_plan = None
            try:
                shadow_failure = control.latest_chapter_plan_shadow_failure(
                    chapter_id
                )
            except ControlPlaneError:
                shadow_failure = None
            if writing_plan is None:
                summary = {
                    "head_plan_revision": int(item.get("head_plan_revision") or 0),
                    "confirmed_plan_revision": int(
                        item.get("confirmed_plan_revision") or 0
                    ),
                    "status": "not_started",
                    "plan_hash": "",
                    "dependency_fingerprint": "",
                    "source": "",
                    "shadow_status": (
                        "failed" if shadow_failure is not None else ""
                    ),
                    "shadow_diff": {},
                    "shadow_error": dict(shadow_failure or {}),
                }
            else:
                metadata = (
                    writing_plan.get("metadata")
                    if isinstance(writing_plan.get("metadata"), dict)
                    else {}
                )
                summary = {
                    "head_plan_revision": int(
                        writing_plan.get("plan_revision") or 0
                    ),
                    "confirmed_plan_revision": int(
                        item.get("confirmed_plan_revision") or 0
                    ),
                    "status": str(writing_plan.get("status") or "current"),
                    "plan_hash": str(writing_plan.get("plan_hash") or ""),
                    "dependency_fingerprint": str(
                        writing_plan.get("dependency_fingerprint") or ""
                    ),
                    "source": str(writing_plan.get("source") or ""),
                    "shadow_status": str(metadata.get("shadow_status") or ""),
                    "shadow_diff": dict(metadata.get("shadow_diff") or {})
                    if isinstance(metadata.get("shadow_diff"), dict)
                    else {},
                    "shadow_error": (
                        dict(shadow_failure)
                        if shadow_failure is not None
                        and str(shadow_failure.get("created_at") or "")
                        > str(writing_plan.get("created_at") or "")
                        else {}
                    ),
                }
                if summary["shadow_error"]:
                    summary["shadow_status"] = "failed"
            item["writing_plan"] = summary
            status = str(summary["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        snapshot["writing_plans"] = {
            "count": sum(status_counts.values()),
            "status_counts": status_counts,
            "shadow_status_counts": {
                shadow_status: sum(
                    1
                    for item in (snapshot.get("items") or [])
                    if isinstance(item, dict)
                    and isinstance(item.get("writing_plan"), dict)
                    and str(item["writing_plan"].get("shadow_status") or "")
                    == shadow_status
                )
                for shadow_status in ("ready", "failed")
            },
        }
        return snapshot

    def _generation_snapshot(
        self,
        control: ControlStore,
        *,
        plan: dict[str, Any],
        writer_research: dict[str, Any],
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        latest = control.latest_command_by_kind("document.run_pipeline") or {}
        operation_id = str(latest.get("operation_id") or "")
        operation = control.operation(operation_id) if operation_id else None
        operation = operation if isinstance(operation, dict) else {}
        runs_by_stage: dict[str, dict[str, Any]] = {}
        for item in control.stage_runs(operation_id) if operation_id else []:
            stage_id = self._canonical_generation_stage(item.get("stage_command"))
            previous = runs_by_stage.get(stage_id)
            if previous is None or int(item.get("attempt") or 0) >= int(
                previous.get("attempt") or 0
            ):
                runs_by_stage[stage_id] = item
        llm_requests_by_stage: dict[str, list[dict[str, Any]]] = {}
        for request in control.llm_requests(operation_id) if operation_id else []:
            llm_requests_by_stage.setdefault(
                self._canonical_generation_stage(request.get("stage_id")),
                [],
            ).append(request)

        stages: list[dict[str, Any]] = []
        for stage_id, label in self._GENERATION_STAGE_LABELS.items():
            run = runs_by_stage.get(stage_id) or {}
            llm_requests = llm_requests_by_stage.get(stage_id, [])
            output = run.get("output")
            output_value = output if isinstance(output, dict) else {}
            summary = output_value.get("summary")
            warnings = [
                item
                for item in (output_value.get("warnings") or [])
                if isinstance(item, dict)
            ]
            stages.append(
                {
                    "stage_id": stage_id,
                    "label": label,
                    "status": str(run.get("status") or "pending"),
                    "attempt": int(run.get("attempt") or 0),
                    "started_at": str(run.get("started_at") or ""),
                    "completed_at": str(run.get("completed_at") or ""),
                    "llm_request_count": len(llm_requests),
                    "llm_requests": llm_requests,
                    "summary": summary if isinstance(summary, dict) else {},
                    "warnings": warnings,
                    "warning_count": int(
                        output_value.get("warning_count") or len(warnings)
                    ),
                    "gate_outcome": str(
                        output_value.get("gate_outcome")
                        or ("warn" if warnings else "pass")
                    ),
                    "error": (
                        run.get("error")
                        if isinstance(run.get("error"), dict)
                        else None
                    ),
                }
            )

        current = next(
            (
                item["stage_id"]
                for item in stages
                if item["status"] == "running"
            ),
            "",
        )
        if not current and str(operation.get("status") or "") in {
            "queued",
            "running",
            "processing",
        }:
            current = next(
                (
                    item["stage_id"]
                    for item in stages
                    if item["status"] == "queued"
                ),
                "",
            )
        content = self._content_progress(control, plan)
        research_calls = [
            item for item in (writer_research.get("operations") or {}).get(operation_id, [])
            if isinstance(item, dict)
        ]
        research_calls.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("decision_id") or ""),
            )
        )
        research_queries = [
            query
            for call in research_calls
            for query in (call.get("queries") or [])
            if isinstance(query, dict)
        ]
        error = operation.get("error")
        generation_warnings = [
            {
                **warning,
                "stage_id": str(
                    warning.get("stage_id") or stage.get("stage_id") or ""
                ),
                "stage_label": str(stage.get("label") or ""),
            }
            for stage in stages
            for warning in (stage.get("warnings") or [])
            if isinstance(warning, dict)
        ]
        return {
            "operation_id": operation_id,
            "status": str(
                operation.get("status")
                or latest.get("status")
                or "not_started"
            ),
            "current_stage_id": current,
            "message": str(
                operation.get("message") or latest.get("message") or ""
            ),
            "error": error if isinstance(error, dict) else None,
            "started_at": str(
                operation.get("started_at")
                or operation.get("created_at")
                or latest.get("created_at")
                or ""
            ),
            "updated_at": str(
                operation.get("updated_at") or latest.get("updated_at") or ""
            ),
            "stages": stages,
            "has_warnings": bool(generation_warnings),
            "warning_count": len(generation_warnings),
            "warnings": generation_warnings,
            "content": content,
            "has_stale_content": bool(content.get("stale_units")),
            "research": {
                "source_count": sum(
                    len(item.get("sources") or [])
                    for item in research_queries
                    if str(item.get("status") or "") == "published"
                ),
                "call_count": len(research_calls),
                "published_count": sum(
                    str(item.get("decision_status") or "") == "published"
                    for item in research_calls
                ),
                "blocked_count": sum(
                    str(item.get("decision_status") or "")
                    == "blocked_human"
                    for item in research_calls
                ),
                "calls": research_calls,
            },
            "delivery": delivery,
        }

    def _content_progress(
        self,
        control: ControlStore,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        index_path = self.root / CONTENT_UNITS_PATH
        index = read_json(index_path) if index_path.is_file() else {}
        raw_units = (
            index.get("units")
            if isinstance(index, dict) and isinstance(index.get("units"), list)
            else []
        )
        planned_units = [
            item for item in raw_units if isinstance(item, dict)
        ]
        states = {
            str(item.get("unit_id") or ""): item
            for item in control.content_unit_states()
            if str(item.get("unit_id") or "")
        }
        title_by_node = {
            str(item.get("chapter_id") or item.get("node_id") or ""): str(
                item.get("title") or ""
            )
            for item in (plan.get("nodes") or [])
            if isinstance(item, dict)
        }
        units: list[dict[str, Any]] = []
        for item in planned_units:
            unit_id = str(item.get("unit_id") or "")
            if not unit_id:
                continue
            node_ids = [
                str(node_id)
                for node_id in (item.get("node_ids") or [])
                if str(node_id)
            ]
            titles = [
                title_by_node[node_id]
                for node_id in node_ids
                if title_by_node.get(node_id)
            ]
            title = titles[0] if titles else (node_ids[0] if node_ids else unit_id)
            if len(titles) > 1:
                title = f"{title} 等 {len(titles)} 个章节"
            state = states.get(unit_id) or {}
            assessment = assess_content_unit(
                self.context,
                item,
                state,
            )
            persisted_status = str(state.get("state") or "pending")
            stale = persisted_status == "stale" or (
                persisted_status == "completed"
                and not assessment["fresh"]
            )
            status = "stale" if stale else persisted_status
            preview = (
                self._content_unit_preview(unit_id, state)
                if assessment["fresh"]
                else {
                    "character_count": 0,
                    "block_count": 0,
                    "preview": "",
                }
            )
            units.append(
                {
                    "unit_id": unit_id,
                    "title": title,
                    "node_ids": node_ids,
                    "status": status,
                    "attempt": int(state.get("attempt") or 0),
                    "updated_at": str(state.get("updated_at") or ""),
                    "error": str(state.get("invalidation_reason") or ""),
                    "current_chapter_id": str(
                        state.get("current_chapter_id") or ""
                    ),
                    "current_chapter_title": str(
                        state.get("current_chapter_title") or ""
                    ),
                    "progress_phase": str(state.get("progress_phase") or ""),
                    "draft_preview": str(state.get("draft_preview") or ""),
                    "writer_fingerprint": str(
                        state.get("writer_fingerprint") or ""
                    ),
                    "stale": stale,
                    "stale_reason": str(
                        assessment.get("stale_reason")
                        or state.get("stale_reason")
                        or ""
                    ),
                    "blocked_human": persisted_status == "blocked_human",
                    **preview,
                }
            )
        statuses = [item["status"] for item in units]
        return {
            "total_units": len(units),
            "completed_units": statuses.count("completed"),
            "running_units": statuses.count("running"),
            "failed_units": statuses.count("failed"),
            "stale_units": statuses.count("stale"),
            "blocked_units": statuses.count("blocked_human"),
            "units": units,
        }

    def _registered_content_path(
        self,
        unit_id: str,
        state: dict[str, Any],
    ):
        return registered_content_path(
            self.context,
            unit_id,
            state,
        )

    def _content_unit_preview(
        self,
        unit_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            path = self._registered_content_path(unit_id, state)
            if path is None or not path.is_file():
                return {"character_count": 0, "block_count": 0, "preview": ""}
            data = read_json(path)
            blocks = [
                ContentBlock.model_validate(item)
                for item in (data.get("blocks") or [])
                if isinstance(item, dict)
            ]
        except Exception:
            return {"character_count": 0, "block_count": 0, "preview": ""}
        text = "\n".join(block.content for block in blocks)
        compact = " ".join(text.split())
        return {
            "character_count": len(text),
            "block_count": len(blocks),
            "preview": compact[:200],
        }

    def content_unit_detail(self, unit_id: str) -> dict[str, Any]:
        normalized = str(unit_id or "").strip()
        control = ControlStore(self.context)
        state = control.content_unit_state(normalized)
        if state is None:
            raise ControlPlaneError(
                "CONTENT_UNIT_NOT_FOUND",
                "未找到该章节写作单元。",
                status_code=404,
            )
        if str(state.get("output_artifact_id") or "").strip():
            # Preserve the path-containment error as the primary failure for
            # a tampered registration, before any freshness projection.
            self._registered_content_path(normalized, state)
        index_path = self.root / CONTENT_UNITS_PATH
        index = read_json(index_path) if index_path.is_file() else {}
        unit = next(
            (
                item
                for item in (index.get("units") or [])
                if isinstance(item, dict)
                and str(item.get("unit_id") or "") == normalized
            ),
            None,
        )
        if unit is None:
            raise ControlPlaneError(
                "CONTENT_UNIT_NOT_FOUND",
                "当前写作计划中不存在该章节单元。",
                status_code=404,
            )
        assessment = assess_content_unit(
            self.context,
            unit,
            state,
        )
        if str(state.get("state") or "") == "stale" or (
            str(state.get("state") or "") == "completed"
            and not assessment["fresh"]
        ):
            raise ControlPlaneError(
                "CONTENT_UNIT_STALE",
                str(
                    assessment.get("stale_reason")
                    or "该章节正文已过期，必须重新生成。"
                ),
                status_code=409,
            )
        if str(state.get("state") or "") != "completed":
            raise ControlPlaneError(
                "CONTENT_UNIT_NOT_READY",
                "该章节尚未生成完成。",
                status_code=409,
            )
        path = self._registered_content_path(normalized, state)
        if path is None or not path.is_file():
            raise ControlPlaneError(
                "CONTENT_UNIT_OUTPUT_MISSING",
                "章节正文文件不存在或尚未登记。",
                status_code=409,
            )
        try:
            data = read_json(path)
            blocks = [
                ContentBlock.model_validate(item)
                for item in (data.get("blocks") or [])
                if isinstance(item, dict)
            ]
        except Exception as exc:
            raise ControlPlaneError(
                "CONTENT_UNIT_OUTPUT_INVALID",
                "章节正文文件结构无效，无法安全预览。",
                status_code=409,
            ) from exc
        bundle_id = str(data.get("bundle_id") or "")
        sources: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        if bundle_id:
            bundle_path = (self.root / BUNDLE_DIR / f"{bundle_id}.json").resolve()
            bundle_dir = (self.root / BUNDLE_DIR).resolve()
            try:
                relative = bundle_path.relative_to(bundle_dir)
            except ValueError:
                relative = None
            if (
                relative is not None
                and len(relative.parts) == 1
                and bundle_path.is_file()
            ):
                try:
                    bundle = WriterInputBundle.model_validate(
                        read_json(bundle_path)
                    )
                except Exception:
                    bundle = None
                if bundle is None:
                    return {
                        "unit_id": normalized,
                        "status": str(state.get("state") or ""),
                        "updated_at": str(state.get("updated_at") or ""),
                        "bundle_id": bundle_id,
                        "block_count": len(blocks),
                        "character_count": sum(
                            len(block.content) for block in blocks
                        ),
                        "blocks": [
                            block.model_dump(mode="json")
                            for block in blocks
                        ],
                        "sources": [],
                    }
                for evidence in bundle.evidence_snapshot:
                    if not isinstance(evidence, dict):
                        continue
                    for source in evidence.get("sources") or []:
                        if not isinstance(source, dict):
                            continue
                        identity = str(
                            source.get("evidence_id")
                            or source.get("source_url")
                            or ""
                        )
                        if not identity or identity in seen_sources:
                            continue
                        seen_sources.add(identity)
                        sources.append(
                            {
                                key: source.get(key)
                                for key in (
                                    "evidence_id",
                                    "title",
                                    "publisher",
                                    "source_url",
                                    "source_type",
                                    "retrieved_at",
                                )
                            }
                        )
        used_evidence_ids = {
            evidence_id
            for block in blocks
            for evidence_id in block.evidence_ids
        }
        for binding in data.get("evidence_batches") or []:
            if not isinstance(binding, dict):
                continue
            batch = load_published_batch(
                self.context,
                str(binding.get("batch_id") or ""),
            )
            if batch is None:
                continue
            for item in batch.items:
                if item.evidence_id not in used_evidence_ids:
                    continue
                identity = item.evidence_id or item.source_url or ""
                if not identity or identity in seen_sources:
                    continue
                seen_sources.add(identity)
                sources.append(
                    {
                        "evidence_id": item.evidence_id,
                        "title": item.title,
                        "publisher": item.publisher,
                        "source_url": item.source_url,
                        "source_type": item.source_type.value,
                        "retrieved_at": item.retrieved_at,
                    }
                )
        return {
            "unit_id": normalized,
            "status": str(state.get("state") or ""),
            "updated_at": str(state.get("updated_at") or ""),
            "bundle_id": bundle_id,
            "writer_fingerprint": str(data.get("writer_fingerprint") or ""),
            "research_decision_id": str(
                data.get("research_decision_id") or ""
            ),
            "block_count": len(blocks),
            "character_count": sum(len(block.content) for block in blocks),
            "blocks": [block.model_dump(mode="json") for block in blocks],
            "sources": sources,
        }

    @staticmethod
    def _trace_excerpt(value: Any, *, limit: int = 480) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _writer_research_trace(self, call: dict[str, Any]) -> dict[str, Any]:
        chapter_ids = [
            str(item)
            for item in (call.get("applicable_chapter_ids") or [])
            if str(item)
        ]
        chapter_titles = [
            str(item)
            for item in (call.get("applicable_chapter_titles") or [])
            if str(item)
        ]
        chapter_title_by_id = {
            chapter_id: (
                chapter_titles[index]
                if index < len(chapter_titles)
                else chapter_id
            )
            for index, chapter_id in enumerate(chapter_ids)
        }
        used_by_chapter_value = call.get("used_evidence_by_chapter")
        used_by_chapter = (
            used_by_chapter_value
            if isinstance(used_by_chapter_value, dict)
            else {}
        )
        used_rows: list[dict[str, Any]] = []
        evidence_chapters: dict[str, list[dict[str, str]]] = {}
        used_evidence_ids: set[str] = set()
        for chapter_id, raw_evidence_ids in sorted(used_by_chapter.items()):
            normalized_chapter_id = str(chapter_id or "")
            evidence_ids = list(
                dict.fromkeys(
                    str(item)
                    for item in (
                        raw_evidence_ids
                        if isinstance(raw_evidence_ids, list)
                        else []
                    )
                    if str(item)
                )
            )
            if not normalized_chapter_id or not evidence_ids:
                continue
            chapter = {
                "chapter_id": normalized_chapter_id,
                "chapter_title": chapter_title_by_id.get(
                    normalized_chapter_id,
                    normalized_chapter_id,
                ),
            }
            used_rows.append({**chapter, "evidence_ids": evidence_ids})
            for evidence_id in evidence_ids:
                used_evidence_ids.add(evidence_id)
                evidence_chapters.setdefault(evidence_id, []).append(chapter)

        query_rows: list[dict[str, Any]] = []
        for raw_query in call.get("queries") or []:
            if not isinstance(raw_query, dict):
                continue
            batch_id = str(raw_query.get("batch_id") or "")
            batch = load_published_batch(self.context, batch_id)
            source_rows = (
                [
                    item.model_dump(mode="json")
                    for item in batch.items
                ]
                if batch is not None
                else [
                    item
                    for item in (raw_query.get("sources") or [])
                    if isinstance(item, dict)
                ]
            )
            results: list[dict[str, Any]] = []
            seen_sources: set[str] = set()
            for source in source_rows:
                evidence_id = str(source.get("evidence_id") or "")
                identity = str(
                    evidence_id
                    or source.get("source_url")
                    or source.get("title")
                    or ""
                )
                if not identity or identity in seen_sources:
                    continue
                seen_sources.add(identity)
                used_in_chapters = evidence_chapters.get(evidence_id, [])
                results.append(
                    {
                        "evidence_id": evidence_id,
                        "title": str(source.get("title") or ""),
                        "publisher": str(source.get("publisher") or ""),
                        "source_url": str(source.get("source_url") or ""),
                        "source_type": str(source.get("source_type") or ""),
                        "retrieved_at": str(source.get("retrieved_at") or ""),
                        "answer_excerpt": self._trace_excerpt(
                            source.get("content") or source.get("excerpt")
                        ),
                        "used_in_bid": bool(used_in_chapters),
                        "usage_status": (
                            "used" if used_in_chapters else "unknown"
                        ),
                        "used_in_chapters": used_in_chapters,
                    }
                )
            query_rows.append(
                {
                    "query_id": str(raw_query.get("query_id") or ""),
                    "question": str(raw_query.get("question") or ""),
                    "applicability": str(raw_query.get("applicability") or ""),
                    "target_node_ids": [
                        str(item)
                        for item in (raw_query.get("target_node_ids") or [])
                        if str(item)
                    ],
                    "status": str(raw_query.get("status") or ""),
                    "batch_id": batch_id,
                    "evidence_count": int(
                        raw_query.get("evidence_count") or len(results)
                    ),
                    "error": str(raw_query.get("error") or ""),
                    "attempts": [
                        {
                            key: attempt.get(key)
                            for key in (
                                "attempt",
                                "status",
                                "batch_id",
                                "evidence_count",
                                "source_count",
                                "error",
                                "duration_ms",
                                "at",
                            )
                        }
                        for attempt in (raw_query.get("attempts") or [])
                        if isinstance(attempt, dict)
                    ],
                    "results": results,
                }
            )

        return {
            "decision_id": str(call.get("decision_id") or ""),
            "unit_id": str(call.get("unit_id") or ""),
            "chapter_ids": chapter_ids,
            "chapter_titles": chapter_titles,
            "needs_research": bool(call.get("needs_research")),
            "decision_status": str(call.get("decision_status") or ""),
            "decision_summary": str(call.get("reason") or ""),
            "queries": query_rows,
            "query_count": len(query_rows),
            "source_count": sum(
                len(query.get("results") or []) for query in query_rows
            ),
            "used_evidence_count": len(used_evidence_ids),
            "used_by_chapter": used_rows,
            "prohibited_research_scopes": [
                str(item)
                for item in (call.get("prohibited_research_scopes") or [])
                if str(item)
            ],
            "created_at": str(call.get("created_at") or ""),
        }

    def generation_stage_detail(self, stage_id: str) -> dict[str, Any]:
        normalized = self._canonical_generation_stage(stage_id)
        if (
            normalized not in self._GENERATION_STAGE_LABELS
            and normalized not in self._PIPELINE_STAGE_LABELS
        ):
            raise ControlPlaneError(
                "GENERATION_STAGE_UNKNOWN",
                "未知的流程阶段。",
                status_code=404,
            )
        snapshot = self.build()
        generation = snapshot.get("generation") or {}
        stage = next(
            (
                item
                for item in (generation.get("stages") or [])
                if str(item.get("stage_id") or "") == normalized
            ),
            None,
        )
        if stage is None:
            stage = next(
                (
                    item
                    for item in (
                        ((snapshot.get("analysis") or {}).get("pipeline") or {}).get(
                            "stages"
                        )
                        or []
                    )
                    if str(item.get("stage_id") or "") == normalized
                ),
                None,
            )
        if stage is None:
            raise ControlPlaneError(
                "GENERATION_STAGE_NOT_FOUND",
                "当前任务尚未产生该阶段记录。",
                status_code=404,
            )
        items: list[dict[str, Any]] = []
        details: dict[str, Any] = {}
        research_trace: list[dict[str, Any]] = []
        trace_disclosure = ""
        current_writing: dict[str, Any] | None = None
        artifact_kind = {
            "normalize_sources": "SourceIndex",
            "build_requirement_ledger": "RequirementLedger",
            "analyze_scores": "ScoreModel",
            "score_structure": "ScoreModel",
            "score_semantic": "ScoreModel",
            "plan_response": "ProjectModel",
            "compile_chapter_blueprint": "ChapterBlueprint",
        }.get(normalized)
        if artifact_kind:
            artifact = next(
                (
                    item
                    for item in (snapshot.get("promoted_artifacts") or [])
                    if str(item.get("artifact_kind") or "") == artifact_kind
                ),
                None,
            )
            if (
                artifact_kind == "ProjectModel"
                and str(stage.get("status") or "") not in {"succeeded", "reused"}
            ):
                # A failed/paused current attempt must not hydrate this drawer
                # with an older promoted ProjectModel and make it look current.
                artifact = None
            payload = (
                artifact.get("payload")
                if isinstance(artifact, dict)
                and isinstance(artifact.get("payload"), dict)
                else {}
            )
            details["artifact_kind"] = artifact_kind
            details["revision"] = int(
                (artifact or {}).get("revision") or 0
            )
            if artifact_kind == "SourceIndex":
                items = [
                    {
                        "id": str(item.get("input_id") or index),
                        "title": str(
                            item.get("filename")
                            or item.get("input_id")
                            or f"来源 {index}"
                        ),
                        "status": str(item.get("status") or ""),
                        "description": str(item.get("message") or ""),
                    }
                    for index, item in enumerate(
                        payload.get("input_status") or [],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
            elif artifact_kind == "RequirementLedger":
                items = [
                    {
                        "id": str(item.get("requirement_id") or index),
                        "title": str(
                            item.get("title")
                            or item.get("requirement_id")
                            or f"需求 {index}"
                        ),
                        "status": str(item.get("status") or ""),
                        "description": str(
                            item.get("statement")
                            or item.get("requirement")
                            or ""
                        ),
                    }
                    for index, item in enumerate(
                        payload.get("requirements") or [],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
            elif artifact_kind == "ScoreModel":
                items = [
                    {
                        "id": str(item.get("score_point_id") or index),
                        "title": str(
                            item.get("title")
                            or item.get("score_point_id")
                            or f"评分点 {index}"
                        ),
                        "status": str(item.get("review_status") or ""),
                        "description": str(item.get("criterion") or ""),
                        "meta": {
                            "max_points": item.get("max_points"),
                            "condition_count": len(
                                item.get("score_conditions") or []
                            ),
                        },
                    }
                    for index, item in enumerate(
                        payload.get("points") or [],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
            elif artifact_kind == "ProjectModel":
                identity = (
                    payload.get("identity")
                    if isinstance(payload.get("identity"), dict)
                    else {}
                )
                details.update(
                    {
                        "project_id": str(payload.get("project_id") or ""),
                        "confirmed_fact_count": len(
                            payload.get("confirmed_facts") or []
                        ),
                        "inference_count": len(payload.get("inferences") or []),
                        "unknown_count": len(payload.get("unknowns") or []),
                    }
                )
                items = [
                    {
                        "id": f"identity:{key}",
                        "title": str(key),
                        "status": "confirmed",
                        "description": str(value),
                    }
                    for key, value in identity.items()
                    if str(value).strip()
                ]
                items.extend(
                    {
                        "id": f"{field}:{index}",
                        "title": field,
                        "status": "confirmed",
                        "description": str(value),
                    }
                    for field in ("goals", "scope", "work_packages", "deliverables")
                    for index, value in enumerate(payload.get(field) or [], start=1)
                    if str(value).strip()
                )
            elif artifact_kind == "ChapterBlueprint":
                items = [
                    {
                        "id": str(item.get("chapter_id") or index),
                        "title": str(
                            item.get("title")
                            or item.get("chapter_id")
                            or f"章节 {index}"
                        ),
                        "status": str(item.get("review_status") or ""),
                        "description": str(item.get("purpose") or ""),
                        "meta": {
                            "requirement_count": len(
                                item.get("requirement_ids") or []
                            ),
                            "score_condition_count": len(
                                item.get("score_condition_ids") or []
                            ),
                        },
                    }
                    for index, item in enumerate(
                        payload.get("nodes") or [],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
        elif normalized == "chapter_writing":
            content = generation.get("content") or {}
            research = generation.get("research") or {}
            details = {
                key: content.get(key)
                for key in (
                    "total_units",
                    "completed_units",
                    "running_units",
                    "failed_units",
                )
            }
            details["research_call_count"] = int(research.get("call_count") or 0)
            details["research_published_count"] = int(research.get("published_count") or 0)
            unit_states = {
                str(item.get("unit_id") or ""): item
                for item in (content.get("units") or [])
                if isinstance(item, dict) and str(item.get("unit_id") or "")
            }
            active_unit = next(
                (
                    item
                    for item in (content.get("units") or [])
                    if isinstance(item, dict)
                    and str(item.get("status") or "") == "running"
                ),
                None,
            )
            if active_unit is None:
                # Surface the chapter that paused the pipeline so the UI does
                # not keep showing a stale "正在撰写" state.
                blocked_units = [
                    item
                    for item in (content.get("units") or [])
                    if isinstance(item, dict)
                    and str(item.get("status") or "")
                    in {"blocked_human", "failed"}
                    and (
                        str(item.get("current_chapter_title") or "").strip()
                        or str(item.get("progress_phase") or "").strip()
                    )
                ]
                if blocked_units:
                    active_unit = max(
                        blocked_units,
                        key=lambda item: str(item.get("updated_at") or ""),
                    )
            if isinstance(active_unit, dict):
                current_writing = {
                    "unit_id": str(active_unit.get("unit_id") or ""),
                    "unit_title": str(active_unit.get("title") or ""),
                    "unit_status": str(active_unit.get("status") or ""),
                    "chapter_id": str(active_unit.get("current_chapter_id") or ""),
                    "chapter_title": str(
                        active_unit.get("current_chapter_title") or ""
                    ),
                    "phase": str(active_unit.get("progress_phase") or ""),
                    "error": str(active_unit.get("error") or ""),
                    "updated_at": str(active_unit.get("updated_at") or ""),
                }
            for item in research.get("calls") or []:
                if not isinstance(item, dict):
                    continue
                trace = self._writer_research_trace(item)
                unit_state = unit_states.get(str(trace.get("unit_id") or "")) or {}
                trace.update(
                    {
                        "unit_status": str(unit_state.get("status") or ""),
                        "unit_attempt": int(unit_state.get("attempt") or 0),
                        "unit_updated_at": str(unit_state.get("updated_at") or ""),
                        "current_chapter_id": str(
                            unit_state.get("current_chapter_id") or ""
                        ),
                        "current_chapter_title": str(
                            unit_state.get("current_chapter_title") or ""
                        ),
                        "progress_phase": str(
                            unit_state.get("progress_phase") or ""
                        ),
                    }
                )
                if trace["unit_status"] == "completed":
                    for query in trace.get("queries") or []:
                        for result in query.get("results") or []:
                            if result.get("usage_status") == "unknown":
                                result["usage_status"] = "not_used"
                research_trace.append(trace)
            details["search_query_count"] = sum(
                int(item.get("query_count") or 0)
                for item in research_trace
            )
            details["research_source_count"] = sum(
                int(item.get("source_count") or 0)
                for item in research_trace
            )
            details["used_evidence_count"] = sum(
                int(item.get("used_evidence_count") or 0)
                for item in research_trace
            )
            trace_disclosure = (
                "展示可审计的决策依据摘要、工具调用与证据采用记录；"
                "不展示模型内部隐藏推理。"
            )
            items = [
                {
                    "id": str(item.get("unit_id") or index),
                    "title": str(item.get("title") or item.get("unit_id") or ""),
                    "status": str(item.get("status") or ""),
                    "description": str(item.get("preview") or item.get("error") or ""),
                    "meta": {
                        "character_count": item.get("character_count"),
                        "block_count": item.get("block_count"),
                        "attempt": item.get("attempt"),
                    },
                }
                for index, item in enumerate(content.get("units") or [], start=1)
                if isinstance(item, dict)
            ]
        elif normalized == "verify_document":
            quality = snapshot.get("quality") or {}
            report = quality.get("report") or {}
            details = {
                "verdict": report.get("verdict"),
                "finding_count": len(report.get("findings") or []),
            }
            items = [
                {
                    "id": str(item.get("code") or index),
                    "title": str(item.get("code") or f"问题 {index}"),
                    "status": str(item.get("severity") or ""),
                    "description": str(
                        item.get("message")
                        or item.get("requirement_id")
                        or item.get("block_id")
                        or ""
                    ),
                }
                for index, item in enumerate(
                    report.get("findings") or [],
                    start=1,
                )
                if isinstance(item, dict)
            ]
        elif normalized in {"render_document", "verify_delivery"}:
            delivery = generation.get("delivery") or {}
            details = dict(delivery)
        else:
            details = dict(stage.get("summary") or {})
        # Keep the stage drawer self-contained for planning failures and
        # successful/reused runs.  These fields come from the backend
        # snapshot; the client must not infer token/input/request counts.
        if normalized == "plan_response":
            details = {
                **details,
                "input_chars": int(stage.get("input_chars") or details.get("input_chars") or 0),
                "source_block_count": int(
                    stage.get("source_block_count")
                    or details.get("source_block_count")
                    or 0
                ),
                "scanned_source_block_count": int(
                    stage.get("scanned_source_block_count")
                    or details.get("scanned_source_block_count")
                    or 0
                ),
                "llm_request_count": int(stage.get("llm_request_count") or 0),
                "normalized_reference_count": int(
                    stage.get("normalized_reference_count")
                    or details.get("normalized_reference_count")
                    or 0
                ),
            }
            summary = stage.get("summary")
            if not isinstance(summary, dict) or not summary:
                summary = {
                    "project_name": str(
                        (payload.get("identity") or {}).get("project_name")
                        or (payload.get("identity") or {}).get("项目名称")
                        or ""
                    )
                    if isinstance(payload.get("identity"), dict)
                    else "",
                    "fact_count": sum(
                        len(payload.get(field) or [])
                        for field in ("confirmed_facts", "inferences", "conflicts")
                    ),
                    "evidence_need_count": len(payload.get("evidence_needs") or []),
                }
            details["project_summary"] = summary
            for index, item in enumerate(stage.get("validation_errors") or [], start=1):
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        "id": str(item.get("code") or item.get("rule") or f"validation:{index}"),
                        "title": str(item.get("code") or item.get("rule") or f"第 {index} 轮校验"),
                        "status": "failed",
                        "description": str(item.get("message") or item.get("error") or item),
                        "meta": {
                            "attempt": item.get("attempt") or index,
                        },
                    }
                )
            for index, item in enumerate(stage.get("repair_history") or [], start=1):
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        "id": str(item.get("attempt") or f"repair:{index}"),
                        "title": f"自动修复第 {item.get('attempt') or index} 轮",
                        "status": str(item.get("status") or "failed"),
                        "description": str(
                            item.get("message")
                            or item.get("error")
                            or item.get("summary")
                            or "已记录该轮校验结果"
                        ),
                        "meta": {
                            "attempt": item.get("attempt") or index,
                        },
                    }
                )
        return {
            **stage,
            "details": details,
            "summary": details.get("project_summary") if normalized == "plan_response" else stage.get("summary") or {},
            "items": items,
            "research_trace": research_trace,
            "trace_disclosure": trace_disclosure,
            "current_writing": current_writing,
        }

    def _artifact_states(
        self,
        control: ControlStore,
        artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, bool]:
        """Project whether every active analysis artifact still binds current upstream facts."""

        states: dict[str, bool] = {}
        for kind in self._ANALYSIS_CHAIN:
            artifact = artifacts.get(kind)
            if artifact is None:
                continue
            current = True
            payload = artifact.get("payload")
            value = payload if isinstance(payload, dict) else {}

            if kind == "SourceIndex":
                manifest = artifacts.get("InputManifest")
                manifest_payload = (manifest or {}).get("payload")
                manifest_value = manifest_payload if isinstance(manifest_payload, dict) else {}
                current = bool(manifest) and self._analysis_inputs_match(
                    manifest_value,
                    value,
                )

            if kind == "RequirementLedger":
                source = artifacts.get("SourceIndex")
                audit = value.get("coverage_audit")
                audit_value = audit if isinstance(audit, dict) else {}
                current = bool(source) and states.get("SourceIndex") is True
                source_revision = audit_value.get("source_index_revision")
                source_hash = str(audit_value.get("source_index_hash") or "")
                if source_revision is not None:
                    current = current and int(source_revision) == int((source or {}).get("revision") or 0)
                if source_hash:
                    current = current and source_hash == str((source or {}).get("artifact_hash") or "")

            proposal = control.v3_proposal(str(artifact.get("proposal_id") or ""))
            declared = (proposal or {}).get("declared_dependencies")
            for dependency in declared if isinstance(declared, list) else []:
                if not isinstance(dependency, dict):
                    current = False
                    continue
                dependency_kind = str(dependency.get("artifact_kind") or "")
                active = artifacts.get(dependency_kind)
                if not dependency_kind or active is None or states.get(dependency_kind) is False:
                    current = False
                    continue
                if (
                    kind == "SourceIndex"
                    and dependency_kind == "InputManifest"
                    and current
                ):
                    # Company evidence is deliberately outside the
                    # requirement/score/outline dependency slice. Adding or
                    # replacing it must not hide a still-current outline.
                    continue
                expected_revision = dependency.get("expected_revision")
                expected_hash = str(dependency.get("expected_hash") or "")
                if expected_revision is not None and int(expected_revision) != int(active.get("revision") or 0):
                    current = False
                if expected_hash and expected_hash != str(active.get("artifact_hash") or ""):
                    current = False
            states[kind] = current
        return states

    @classmethod
    def _analysis_inputs_match(
        cls,
        manifest: dict[str, Any],
        source_index: dict[str, Any],
    ) -> bool:
        """Compare only inputs that can change requirement/score planning."""

        items = manifest.get("inputs")
        manifest_inputs = items if isinstance(items, list) else []
        expected: dict[str, str] = {}
        for item in manifest_inputs:
            if (
                not isinstance(item, dict)
                or item.get("active") is not True
                or str(item.get("role") or "") not in cls._ANALYSIS_INPUT_ROLES
            ):
                continue
            input_id = str(item.get("input_id") or "")
            sha256 = str(item.get("sha256") or "")
            if not input_id or not sha256:
                return False
            expected[input_id] = sha256

        raw_hashes = source_index.get("source_hashes")
        source_hashes = raw_hashes if isinstance(raw_hashes, dict) else {}
        return bool(expected) and all(
            str(source_hashes.get(input_id) or "") == sha256
            for input_id, sha256 in expected.items()
        )

    def _latest_analysis_operation(
        self,
        control: ControlStore,
        artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        latest_command = control.latest_command_by_kind("document.prepare_outline")
        if latest_command is None:
            return {}
        operation_id = str(latest_command.get("operation_id") or "")
        operation = control.operation(operation_id) or {}
        stage_runs = control.stage_runs(operation_id)
        completed_outline = any(
            str(item.get("stage_command") or "") == "compile_chapter_blueprint"
            and str(item.get("status") or "") in {"succeeded", "reused"}
            for item in stage_runs
        )
        status = str(operation.get("status") or latest_command.get("status") or "")
        operation_time = str(operation.get("updated_at") or latest_command.get("updated_at") or "")
        result_times = [
            str(artifacts[kind].get("created_at") or "")
            for kind in ("ScoreModel", "ChapterBlueprint")
            if artifacts.get(kind)
        ]
        failed_after_current_result = (
            status == "failed"
            and not completed_outline
            and bool(result_times)
            and self._timestamp_key(operation_time)
            >= max(self._timestamp_key(value) for value in result_times)
        )
        return {
            "operation_id": operation_id,
            "kind": str(operation.get("kind") or latest_command.get("kind") or ""),
            "status": status,
            "message": str(operation.get("message") or latest_command.get("message") or ""),
            "error": operation.get("error"),
            "created_at": str(operation.get("created_at") or latest_command.get("created_at") or ""),
            "updated_at": operation_time,
            "completed_outline": completed_outline,
            "result_outdated": failed_after_current_result,
            "stages": stage_runs,
        }

    def _analysis_pipeline(
        self,
        control: ControlStore,
        artifacts: dict[str, dict[str, Any]],
        artifact_states: dict[str, bool],
        latest_operation: dict[str, Any],
        *,
        planning_confirmed: bool = False,
        planning_status: str = "not_ready",
    ) -> dict[str, Any]:
        operation_id = str(latest_operation.get("operation_id") or "")
        raw_runs = (
            control.stage_runs(operation_id)
            if operation_id
            else []
        )
        llm_requests = (
            control.llm_requests(operation_id)
            if operation_id
            else []
        )
        llm_requests_by_stage: dict[str, list[dict[str, Any]]] = {}
        for request in llm_requests:
            request_stage = str(request.get("stage_id") or "")
            # Older runs recorded the two sub-capabilities separately even
            # though the user sees one "全局项目事实" node.  Project both old
            # and new telemetry onto that node so its drawer never looks empty.
            if request_stage in {"project_understanding", "topic_duty_planning"}:
                request_stage = "plan_response"
            llm_requests_by_stage.setdefault(request_stage, []).append(request)
        runs_by_stage: dict[str, dict[str, Any]] = {}
        for item in raw_runs:
            stage = str(item.get("stage_command") or "")
            previous = runs_by_stage.get(stage)
            if previous is None or int(item.get("attempt") or 0) >= int(
                previous.get("attempt") or 0
            ):
                runs_by_stage[stage] = item

        # The outline operation deliberately stops at the human gate, so its
        # final stage remains ``blocked_human`` in that operation's history.
        # A later explicit confirmation is recorded by a separate command.
        # Project the current confirmed planning state back onto this timeline
        # so the UI accurately marks the final "人工确认" node as complete.
        if planning_confirmed:
            previous = runs_by_stage.get("confirm_planning") or {}
            runs_by_stage["confirm_planning"] = {
                **previous,
                "stage_command": "confirm_planning",
                "status": "succeeded",
                "attempt": max(1, int(previous.get("attempt") or 0)),
                "disposition": "explicit_human_confirmation",
            }
        elif planning_status != "needs_human":
            previous = runs_by_stage.get("confirm_planning") or {}
            if str(previous.get("status") or "") == "blocked_human":
                # The historical outline operation stops at H1 by design.
                # If no reviewable outline is currently available, that old
                # pause must not be presented as an actionable confirmation.
                runs_by_stage["confirm_planning"] = {
                    **previous,
                    "stage_command": "confirm_planning",
                    "status": "pending",
                }

        operation_status = str(latest_operation.get("status") or "")
        operation_error = latest_operation.get("error")
        error_value = (
            operation_error
            if isinstance(operation_error, dict)
            else {}
        )
        operation_message = str(
            error_value.get("message")
            or latest_operation.get("message")
            or ""
        )
        inferred_failed_stage = self._infer_failed_stage(
            operation_message
        )
        if (
            operation_status == "failed"
            and inferred_failed_stage
            and inferred_failed_stage not in runs_by_stage
        ):
            runs_by_stage[inferred_failed_stage] = {
                "stage_command": inferred_failed_stage,
                "status": "failed",
                "attempt": 1,
                "error": error_value
                or {
                    "code": "COMMAND_DISPATCH_FAILED",
                    "message": operation_message,
                    "details": {},
                },
                "output": None,
                "started_at": "",
                "completed_at": str(
                    latest_operation.get("updated_at") or ""
                ),
                "synthetic": True,
            }

        def status_for(stage: str) -> str:
            item = runs_by_stage.get(stage)
            return (
                str(item.get("status") or "pending")
                if item
                else "pending"
            )

        analyze_run = runs_by_stage.get("analyze_scores") or {}
        score_structure_run = runs_by_stage.get("score_structure") or {}
        score_semantic_run = runs_by_stage.get("score_semantic") or {}
        analyze_output = (
            analyze_run.get("output")
            if isinstance(analyze_run.get("output"), dict)
            else {}
        )
        analyze_products = (
            analyze_output.get("products")
            if isinstance(analyze_output.get("products"), list)
            else []
        )
        analyze_status = status_for("analyze_scores")
        analyze_message = str(
            (
                analyze_run.get("error")
                if isinstance(analyze_run.get("error"), dict)
                else {}
            ).get("message")
            or operation_message
        )
        structure_ready = any(
            isinstance(item, dict)
            and item.get("kind") == "ScoreStructureDraft"
            for item in analyze_products
        ) or (
            analyze_status == "failed"
            and "score_semantic_" in analyze_message
        )
        if score_structure_run:
            score_structure_status = status_for("score_structure")
        elif structure_ready:
            score_structure_status = "succeeded"
        elif analyze_status in {"running", "failed"}:
            score_structure_status = analyze_status
        else:
            score_structure_status = (
                "succeeded"
                if analyze_status in {"succeeded", "reused"}
                else "pending"
            )
        if score_semantic_run:
            score_semantic_status = status_for("score_semantic")
        elif analyze_status == "running" and not structure_ready:
            score_semantic_status = "pending"
        else:
            score_semantic_status = analyze_status

        stage_rows = [
            self._pipeline_stage(
                "normalize_sources",
                status_for("normalize_sources"),
                runs_by_stage.get("normalize_sources"),
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "build_requirement_ledger",
                status_for("build_requirement_ledger"),
                runs_by_stage.get("build_requirement_ledger"),
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "score_structure",
                score_structure_status,
                score_structure_run or analyze_run,
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "score_semantic",
                score_semantic_status,
                score_semantic_run or analyze_run,
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "plan_response",
                status_for("plan_response"),
                runs_by_stage.get("plan_response"),
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "compile_chapter_blueprint",
                status_for("compile_chapter_blueprint"),
                runs_by_stage.get("compile_chapter_blueprint"),
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "confirm_planning",
                status_for("confirm_planning"),
                runs_by_stage.get("confirm_planning"),
                llm_requests_by_stage,
            ),
        ]
        products = self._pipeline_products(
            artifacts,
            artifact_states,
            raw_runs,
            latest_operation,
        )
        projected_status = operation_status or "not_started"
        if planning_status == "blocked" and projected_status == "blocked_human":
            projected_status = "failed"
        return {
            "operation_id": operation_id,
            "status": projected_status,
            "stages": stage_rows,
            "products": products,
        }

    def _pipeline_stage(
        self,
        stage_id: str,
        status: str,
        run: dict[str, Any] | None,
        llm_requests_by_stage: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        value = run if isinstance(run, dict) else {}
        requests = llm_requests_by_stage.get(stage_id, [])
        output = (
            value.get("output")
            if isinstance(value.get("output"), dict)
            else {}
        )
        warnings = [
            item
            for item in (output.get("warnings") or [])
            if isinstance(item, dict)
        ]
        error_value = value.get("error") if isinstance(value.get("error"), dict) else {}
        error_details = (
            error_value.get("details")
            if isinstance(error_value.get("details"), dict)
            else {}
        )
        # Planning stages now persist the compact input/validation telemetry
        # alongside the stage run.  Keep the projection tolerant of older
        # runs (which only recorded ``output`` and ``error``) so the drawer
        # remains useful after a refresh and never invents values in the UI.
        metrics = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
        input_chars = output.get(
            "input_chars",
            metrics.get(
                "input_chars",
                error_details.get("input_chars", value.get("input_chars", 0)),
            ),
        )
        source_block_count = output.get(
            "source_block_count",
            metrics.get(
                "source_block_count",
                error_details.get(
                    "source_block_count",
                    value.get("source_block_count", 0),
                ),
            ),
        )
        scanned_source_block_count = output.get(
            "scanned_source_block_count",
            metrics.get(
                "scanned_source_block_count",
                error_details.get(
                    "scanned_source_block_count",
                    value.get("scanned_source_block_count", 0),
                ),
            ),
        )
        normalized_reference_count = output.get(
            "normalized_reference_count",
            metrics.get(
                "normalized_reference_count",
                error_details.get(
                    "normalized_reference_count",
                    value.get("normalized_reference_count", 0),
                ),
            ),
        )
        attempts = int(error_details.get("attempts") or 0)
        repair_round = output.get(
            "repair_round",
            metrics.get(
                "repair_round",
                value.get("repair_round", max(0, attempts - 1)),
            ),
        )
        max_repair_rounds = output.get(
            "max_repair_rounds",
            metrics.get(
                "max_repair_rounds",
                value.get("max_repair_rounds", 1 if attempts else 0),
            ),
        )
        repair_history = output.get("repair_history")
        if not isinstance(repair_history, list):
            repair_history = output.get("validation_attempts")
        if not isinstance(repair_history, list):
            repair_history = []
        # Error details are intentionally kept as structured rows.  This
        # allows the frontend to show all controlled repair rounds instead of
        # only the final exception string.
        validation_errors = output.get("validation_errors")
        if not isinstance(validation_errors, list):
            validation_errors = output.get("errors")
        if not isinstance(validation_errors, list):
            validation_errors = []
        if not validation_errors:
            diagnostics = error_details.get("diagnostics")
            if isinstance(diagnostics, list) and diagnostics:
                validation_errors = [
                    {
                        "attempt": index,
                        "code": "PROJECT_UNDERSTANDING_VALIDATION",
                        "message": str(item),
                    }
                    for index, item in enumerate(diagnostics, start=1)
                ]
            elif error_value:
                validation_errors = [error_value]
        return {
            "stage_id": stage_id,
            "label": self._PIPELINE_STAGE_LABELS[stage_id],
            "status": status or "pending",
            "result": (
                "produced" if status == "succeeded"
                else str(status or "pending")
            ),
            "operation_id": str(value.get("operation_id") or ""),
            "attempt": int(value.get("attempt") or 0),
            "started_at": str(value.get("started_at") or ""),
            "completed_at": str(value.get("completed_at") or ""),
            "error": value.get("error"),
            "llm_request_count": len(requests),
            "llm_requests": requests,
            "input_chars": int(input_chars or 0),
            "source_block_count": int(source_block_count or 0),
            "scanned_source_block_count": int(
                scanned_source_block_count or 0
            ),
            "normalized_reference_count": int(normalized_reference_count or 0),
            "repair_round": int(repair_round or 0),
            "max_repair_rounds": int(max_repair_rounds or 0),
            "repair_history": repair_history,
            "validation_errors": validation_errors,
            "summary": output.get("summary") if isinstance(output.get("summary"), dict) else {},
            "warnings": warnings,
            "warning_count": int(
                output.get("warning_count") or len(warnings)
            ),
            "gate_outcome": str(
                output.get("gate_outcome")
                or ("warn" if warnings else "pass")
            ),
            "phase": str(
                (
                    output
                ).get("phase")
                or ""
            ),
        }

    def _pipeline_products(
        self,
        artifacts: dict[str, dict[str, Any]],
        artifact_states: dict[str, bool],
        stage_runs: list[dict[str, Any]],
        latest_operation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        seen: set[str] = set()
        for run in stage_runs:
            output = run.get("output")
            if not isinstance(output, dict):
                continue
            raw_products = output.get("products")
            for product in (
                raw_products
                if isinstance(raw_products, list)
                else []
            ):
                if not isinstance(product, dict):
                    continue
                kind = str(product.get("kind") or "")
                if (
                    not kind
                    or kind in seen
                    or kind in {"ResponseTopicGraph"}
                ):
                    continue
                seen.add(kind)
                products.append(product)

        labels = {
            "SourceIndex": "来源索引",
            "RequirementLedger": "招标需求台账",
            "ScoreModel": "评分理解结果",
            "ProjectModel": "全局项目事实",
            "ChapterBlueprint": "评分目录与覆盖结果",
        }
        stage_by_artifact = {
            "SourceIndex": "normalize_sources",
            "RequirementLedger": "build_requirement_ledger",
            "ScoreModel": "analyze_scores",
            "ProjectModel": "plan_response",
            "ChapterBlueprint": "compile_chapter_blueprint",
        }
        latest_status_by_stage = {
            str(item.get("stage_command") or ""): str(
                item.get("status") or ""
            )
            for item in stage_runs
            if isinstance(item, dict)
        }
        has_current_operation = bool(
            latest_operation.get("operation_id")
        )
        for kind, label in labels.items():
            artifact = artifacts.get(kind)
            if artifact is None or kind in seen:
                continue
            payload = artifact.get("payload")
            value = payload if isinstance(payload, dict) else {}
            stage_status = latest_status_by_stage.get(
                stage_by_artifact[kind],
                "",
            )
            current_for_operation = stage_status in {
                "succeeded",
                "reused",
                "blocked_human",
            }
            products.append(
                {
                    "kind": kind,
                    "label": label,
                    "status": (
                        "ready"
                        if artifact_states.get(kind) is True
                        and (
                            not has_current_operation
                            or current_for_operation
                        )
                        else "outdated"
                    ),
                    "revision": int(artifact.get("revision") or 0),
                    "created_at": str(
                        artifact.get("created_at") or ""
                    ),
                    "summary": self._artifact_summary(kind, value),
                }
            )
            seen.add(kind)
        return products

    @staticmethod
    def _artifact_summary(
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if kind == "SourceIndex":
            return {
                "input_count": len(payload.get("input_status") or []),
                "block_count": len(payload.get("blocks") or []),
            }
        if kind == "RequirementLedger":
            return {
                "requirement_count": len(
                    payload.get("requirements") or []
                ),
            }
        if kind == "ScoreModel":
            points = payload.get("points") or []
            return {
                "group_count": len(payload.get("groups") or []),
                "score_point_count": len(points),
                "condition_count": sum(
                    len(item.get("score_conditions") or [])
                    for item in points
                    if isinstance(item, dict)
                ),
                "response_unit_count": sum(
                    len(item.get("response_units") or [])
                    for item in points
                    if isinstance(item, dict)
                ),
                "evidence_need_count": len(
                    payload.get("evidence_need_candidates") or []
                ),
                "total_points": payload.get("total_points"),
            }
        if kind == "ProjectModel":
            identity = (
                payload.get("identity")
                if isinstance(payload.get("identity"), dict)
                else {}
            )
            return {
                "project_name": str(
                    identity.get("project_name")
                    or identity.get("项目名称")
                    or ""
                ),
                "fact_count": sum(
                    len(payload.get(field) or [])
                    for field in ("confirmed_facts", "inferences", "conflicts")
                ),
                "evidence_need_count": len(payload.get("evidence_needs") or []),
            }
        if kind == "ChapterBlueprint":
            nodes = [
                item
                for item in (payload.get("nodes") or [])
                if isinstance(item, dict)
            ]
            return {
                "chapter_count": len(nodes),
                "primary_response_unit_count": len(
                    {
                        unit_id
                        for item in nodes
                        for unit_id in (
                            item.get("primary_response_unit_ids") or []
                        )
                    }
                ),
                "supporting_response_unit_count": len(
                    {
                        unit_id
                        for item in nodes
                        for unit_id in (
                            item.get("supporting_response_unit_ids") or []
                        )
                    }
                ),
                "score_condition_count": len(
                    {
                        condition_id
                        for item in nodes
                        for condition_id in (
                            item.get("score_condition_ids") or []
                        )
                    }
                ),
                "requirement_count": len(
                    {
                        requirement_id
                        for item in nodes
                        for requirement_id in (
                            item.get("requirement_ids") or []
                        )
                    }
                ),
                "quality_gate_count": len(
                    payload.get("document_quality_gates") or []
                ),
            }
        return {}

    @staticmethod
    def _infer_failed_stage(message: str) -> str:
        text = str(message or "")
        if "score_semantic_" in text or "ScoreModel" in text:
            return "analyze_scores"
        if (
            "ProjectUnderstanding" in text
            or "TopicDuty" in text
            or "ProjectModel" in text
            or "ResponseTopicGraph" in text
        ):
            return "plan_response"
        if (
            "ChapterBlueprint" in text
            or "章节拆分" in text
            or "G2_" in text
        ):
            return "compile_chapter_blueprint"
        if "RequirementLedger" in text:
            return "build_requirement_ledger"
        if "SourceIndex" in text or "normalize" in text:
            return "normalize_sources"
        return ""

    @staticmethod
    def _timestamp_key(value: str) -> float:
        text = str(value or "").strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return 0.0
