from __future__ import annotations

from datetime import datetime
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json

from .contracts import ContentBlock, WriterInputBundle
from .document_planner import CONTENT_UNITS_PATH
from .input_manifest import V3_ROOT
from .writer_bundle import BUNDLE_DIR


class V3WorkspaceSnapshotBuilder:
    """Read-only projection of V3 artifacts and control-plane execution evidence."""

    _ANALYSIS_INPUT_ROLES = frozenset(
        {"tender", "score", "amendment", "template"}
    )
    _ANALYSIS_COMMAND_KINDS = frozenset(
        {"document.prepare_outline", "document.run_pipeline"}
    )
    _ANALYSIS_CHAIN = (
        "InputManifest",
        "SourceIndex",
        "RequirementLedger",
        "ScoreModel",
        "ChapterBlueprint",
    )
    _PIPELINE_STAGE_LABELS = {
        "normalize_sources": "来源结构解析",
        "build_requirement_ledger": "招标需求提取",
        "score_structure": "评分结构解析",
        "score_semantic": "评分理解批次",
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
        "compile_chapter_blueprint": "目录生成",
        "confirm_planning": "目录确认",
        "sync_material_requirements": "材料同步",
        "compile_document_contract": "文档结构编译",
        "plan_document": "写作计划",
        "resolve_evidence": "DeepSeek 公开研究",
        "execute_content_plan": "章节写作",
        "integrate_document": "全文整合",
        "verify_document": "质量审核",
        "render_document": "Word 渲染",
        "verify_delivery": "交付验证",
    }

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> dict[str, Any]:
        control = ControlStore(self.context)
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
            else:
                from .artifact_promotion import HumanGateService

                service = HumanGateService(self.context)
                try:
                    receipt = service.require_current_confirmation()
                    planning = {"status": "confirmed", "receipt_id": receipt.receipt_id}
                except Exception:
                    try:
                        planning = {"status": "needs_human", "snapshot": service.planning_snapshot()}
                    except Exception:
                        planning = {"status": "blocked"}
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
                        or "execute_content_plan"
                    ),
                    "query_budget": int(candidate.get("query_budget") or 5),
                }
            )
        projected_needs.extend(scheduled_needs.values())
        from .autonomous_research import AUTO_RESEARCH_REPORT_PATH

        auto_research_path = self.root / AUTO_RESEARCH_REPORT_PATH
        auto_research = (
            read_json(auto_research_path)
            if auto_research_path.is_file()
            else {}
        )
        generation = self._generation_snapshot(
            control,
            plan=plan or {},
            auto_research=(
                auto_research if isinstance(auto_research, dict) else {}
            ),
            delivery=delivery or {},
        )
        return {
            "schema_version": "v3",
            "workspace_id": self.context.workspace_id,
            "workspace_revision": control.revision(),
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
                "pipeline": self._analysis_pipeline(
                    control,
                    artifacts,
                    artifact_states,
                    latest_analysis_operation,
                ),
            },
            "planning": planning,
            "evidence_needs": projected_needs,
            "auto_research": (
                auto_research if isinstance(auto_research, dict) else {}
            ),
            "generation": generation,
            "materials": payload("EvidenceRepository"),
            "content_units": (content_blocks or {}).get("units", []),
            "quality": {
                "coverage": (quality or {}).get("coverage"),
                "report": quality,
                "gates": control.latest_gate_evaluations(),
            },
        }

    def _generation_snapshot(
        self,
        control: ControlStore,
        *,
        plan: dict[str, Any],
        auto_research: dict[str, Any],
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        commands = [
            item
            for item in (control.snapshot().get("commands") or [])
            if isinstance(item, dict)
            and str(item.get("kind") or "") == "document.run_pipeline"
        ]
        latest = max(
            commands,
            key=lambda item: self._timestamp_key(
                str(item.get("updated_at") or item.get("created_at") or "")
            ),
            default={},
        )
        operation_id = str(latest.get("operation_id") or "")
        operation = control.operation(operation_id) if operation_id else None
        operation = operation if isinstance(operation, dict) else {}
        runs_by_stage: dict[str, dict[str, Any]] = {}
        for item in control.stage_runs(operation_id) if operation_id else []:
            stage_id = str(item.get("stage_command") or "")
            previous = runs_by_stage.get(stage_id)
            if previous is None or int(item.get("attempt") or 0) >= int(
                previous.get("attempt") or 0
            ):
                runs_by_stage[stage_id] = item

        stages: list[dict[str, Any]] = []
        for stage_id, label in self._GENERATION_STAGE_LABELS.items():
            run = runs_by_stage.get(stage_id) or {}
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
        research_questions = [
            {
                "need_id": str(item.get("need_id") or ""),
                "question": str(item.get("question") or ""),
                "status": str(item.get("status") or "open"),
                "topic_id": str(item.get("topic_id") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
            for item in control.evidence_needs()
            if str(item.get("need_id") or "").startswith("EN-AUTO-")
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
            "research": {
                **auto_research,
                "source_count": sum(
                    int(item.get("item_count") or 0)
                    for item in (auto_research.get("results") or [])
                    if isinstance(item, dict)
                    and str(item.get("status") or "") == "published"
                ),
                "questions": research_questions,
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
            preview = self._content_unit_preview(unit_id, state)
            units.append(
                {
                    "unit_id": unit_id,
                    "title": title,
                    "node_ids": node_ids,
                    "status": str(state.get("state") or "pending"),
                    "attempt": int(state.get("attempt") or 0),
                    "updated_at": str(state.get("updated_at") or ""),
                    "error": str(state.get("invalidation_reason") or ""),
                    **preview,
                }
            )
        statuses = [item["status"] for item in units]
        return {
            "total_units": len(units),
            "completed_units": statuses.count("completed"),
            "running_units": statuses.count("running"),
            "failed_units": statuses.count("failed"),
            "units": units,
        }

    def _registered_content_path(
        self,
        unit_id: str,
        state: dict[str, Any],
    ):
        registered = str(state.get("output_artifact_id") or "").strip()
        if not registered:
            return None
        content_dir = (self.root / V3_ROOT / "content_units").resolve()
        candidate = (self.root / registered).resolve()
        try:
            relative = candidate.relative_to(content_dir)
        except ValueError as exc:
            raise ControlPlaneError(
                "CONTENT_UNIT_PATH_INVALID",
                "章节正文登记路径不在当前工作区内容目录内。",
                status_code=409,
            ) from exc
        if (
            len(relative.parts) != 1
            or candidate.name != f"{unit_id}.json"
        ):
            raise ControlPlaneError(
                "CONTENT_UNIT_PATH_INVALID",
                "章节正文登记路径与章节标识不一致。",
                status_code=409,
            )
        return candidate

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
        return {
            "unit_id": normalized,
            "status": str(state.get("state") or ""),
            "updated_at": str(state.get("updated_at") or ""),
            "bundle_id": bundle_id,
            "block_count": len(blocks),
            "character_count": sum(len(block.content) for block in blocks),
            "blocks": [block.model_dump(mode="json") for block in blocks],
            "sources": sources,
        }

    def generation_stage_detail(self, stage_id: str) -> dict[str, Any]:
        normalized = str(stage_id or "").strip()
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
        artifact_kind = {
            "normalize_sources": "SourceIndex",
            "build_requirement_ledger": "RequirementLedger",
            "analyze_scores": "ScoreModel",
            "score_structure": "ScoreModel",
            "score_semantic": "ScoreModel",
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
        elif normalized == "resolve_evidence":
            research = generation.get("research") or {}
            details = {
                key: research.get(key)
                for key in (
                    "provider_id",
                    "planned_count",
                    "published_count",
                    "gap_count",
                    "failed_count",
                    "max_retries",
                    "max_rounds",
                    "blocking_policy",
                )
            }
            details["decision_count"] = len(
                [
                    item
                    for item in (research.get("decisions") or [])
                    if isinstance(item, dict)
                ]
            )
            items = [
                {
                    "id": str(item.get("need_id") or index),
                    "title": str(
                        item.get("chapter_title")
                        or item.get("need_id")
                        or f"研究问题 {index}"
                    ),
                    "status": str(item.get("status") or ""),
                    "description": str(item.get("error") or ""),
                    "attempts": [
                        attempt
                        for attempt in (item.get("attempts") or [])
                        if isinstance(attempt, dict)
                    ],
                    "meta": {
                        "chapter_id": item.get("chapter_id"),
                        "evidence_count": item.get("evidence_count"),
                        "query": item.get("query"),
                    },
                }
                for index, item in enumerate(
                    research.get("results") or [],
                    start=1,
                )
                if isinstance(item, dict)
            ]
            if not items:
                items = [
                    {
                        "id": str(item.get("chapter_id") or index),
                        "title": str(
                            item.get("chapter_title")
                            or item.get("chapter_id")
                            or f"研究判断 {index}"
                        ),
                        "status": (
                            "needs_research"
                            if item.get("needs_research")
                            else "skipped"
                        ),
                        "description": "；".join(
                            str(reason)
                            for reason in (item.get("reasons") or [])
                        ),
                        "meta": item,
                    }
                    for index, item in enumerate(
                        research.get("decisions") or [],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
        elif normalized == "execute_content_plan":
            content = generation.get("content") or {}
            details = {
                key: content.get(key)
                for key in (
                    "total_units",
                    "completed_units",
                    "running_units",
                    "failed_units",
                )
            }
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
        return {
            **stage,
            "details": details,
            "items": items,
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
        snapshot = control.snapshot()
        commands = snapshot.get("commands")
        command_items = commands if isinstance(commands, list) else []
        latest_command = next(
            (
                command
                for command in command_items
                if isinstance(command, dict)
                and str(command.get("kind") or "") in self._ANALYSIS_COMMAND_KINDS
            ),
            None,
        )
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
            llm_requests_by_stage.setdefault(
                str(request.get("stage_id") or ""), []
            ).append(request)
        runs_by_stage: dict[str, dict[str, Any]] = {}
        for item in raw_runs:
            stage = str(item.get("stage_command") or "")
            previous = runs_by_stage.get(stage)
            if previous is None or int(item.get("attempt") or 0) >= int(
                previous.get("attempt") or 0
            ):
                runs_by_stage[stage] = item

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
        if structure_ready:
            score_structure_status = "succeeded"
        elif analyze_status in {"running", "failed"}:
            score_structure_status = analyze_status
        else:
            score_structure_status = (
                "succeeded"
                if analyze_status in {"succeeded", "reused"}
                else "pending"
            )
        if analyze_status == "running" and not structure_ready:
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
                analyze_run,
                llm_requests_by_stage,
            ),
            self._pipeline_stage(
                "score_semantic",
                score_semantic_status,
                analyze_run,
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
        return {
            "operation_id": operation_id,
            "status": operation_status or "not_started",
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
        return {
            "stage_id": stage_id,
            "label": self._PIPELINE_STAGE_LABELS[stage_id],
            "status": status or "pending",
            "attempt": int(value.get("attempt") or 0),
            "started_at": str(value.get("started_at") or ""),
            "completed_at": str(value.get("completed_at") or ""),
            "error": value.get("error"),
            "llm_request_count": len(requests),
            "llm_requests": requests,
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
                    or kind in {"ProjectModel", "ResponseTopicGraph"}
                ):
                    continue
                seen.add(kind)
                products.append(product)

        labels = {
            "SourceIndex": "来源索引",
            "RequirementLedger": "招标需求台账",
            "ScoreModel": "评分理解结果",
            "ChapterBlueprint": "评分目录与覆盖结果",
        }
        stage_by_artifact = {
            "SourceIndex": "normalize_sources",
            "RequirementLedger": "build_requirement_ledger",
            "ScoreModel": "analyze_scores",
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
            return ""
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
