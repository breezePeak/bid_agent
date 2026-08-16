from __future__ import annotations

import tempfile
import sys
import os
from pathlib import Path
from unittest import TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import CommandEnvelope, ControlPlaneError, ControlStore, WorkspaceContext
from api import v3_app
from api.settings_service import SettingsService
from document_pipeline.contracts import (
    ContentBlock,
    EvidenceBatch,
    EvidenceItem,
    EvidenceSourceType,
)
from document_pipeline.document_planner import CONTENT_UNITS_PATH
from document_pipeline.execution_controller import (
    V3_GENERATION_STAGES,
    V3ExecutionController,
)
from document_pipeline.input_manifest import V3_ROOT
from document_pipeline.research_service import EVIDENCE_BATCH_DIR
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder
from document_pipeline.writer_policy import (
    writer_base_fingerprint,
    writer_fingerprint,
)
from utils import write_json
from fastapi.testclient import TestClient


class _Runner:
    def __init__(self, fail_stage: str = "") -> None:
        self.fail_stage = fail_stage
        self.calls: list[str] = []
        self.chapter_ids: list[str] = []

    def set_generation_scope(self, chapter_ids: list[str] | None) -> None:
        self.chapter_ids = list(chapter_ids or [])

    def run(self, stage: str, *, operation_id: str | None = None):
        self.calls.append(stage)
        if stage == self.fail_stage:
            raise RuntimeError(f"{stage} failed")
        if stage == "confirm_planning":
            return {"verdict": "pass"}
        if stage == "plan_document":
            return object(), []
        return {}


class _ResearchBlockedRunner(_Runner):
    def run(self, stage: str, *, operation_id: str | None = None):
        self.calls.append(stage)
        if stage == "execute_content_plan":
            raise ControlPlaneError(
                "WRITER_RESEARCH_ACTION_REQUIRED",
                "DeepSeek needs attention",
            )
        if stage == "confirm_planning":
            return {"verdict": "pass"}
        if stage == "plan_document":
            return object(), []
        return {}


class V3GenerationProgressTests(TestCase):
    def _context(self, root: Path) -> WorkspaceContext:
        runs = root / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    @staticmethod
    def _envelope() -> CommandEnvelope:
        return CommandEnvelope.from_mapping(
            {
                "kind": "document.run_pipeline",
                "expected_revision": 0,
                "idempotency_key": "generation-progress-test",
            },
            workspace_id="alpha",
        )

    def test_analysis_snapshot_ignores_newer_generation_operation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))

            class _Control:
                @staticmethod
                def snapshot():
                    return {
                        "commands": [
                            {
                                "kind": "document.run_pipeline",
                                "operation_id": "generation-operation",
                                "status": "running",
                            },
                            {
                                "kind": "document.prepare_outline",
                                "operation_id": "outline-operation",
                                "status": "succeeded",
                            },
                        ]
                    }

                @staticmethod
                def operation(operation_id: str):
                    return {
                        "operation_id": operation_id,
                        "kind": (
                            "document.prepare_outline"
                            if operation_id == "outline-operation"
                            else "document.run_pipeline"
                        ),
                        "status": "succeeded",
                    }

                @staticmethod
                def stage_runs(operation_id: str):
                    if operation_id == "outline-operation":
                        return [
                            {
                                "stage_command": "compile_chapter_blueprint",
                                "status": "succeeded",
                            }
                        ]
                    return [
                        {
                            "stage_command": "execute_content_plan",
                            "status": "running",
                        }
                    ]

            latest = V3WorkspaceSnapshotBuilder(context)._latest_analysis_operation(
                _Control(),
                {},
            )

            self.assertEqual(latest["kind"], "document.prepare_outline")
            self.assertEqual(latest["operation_id"], "outline-operation")
            self.assertTrue(latest["completed_outline"])

    def test_generation_snapshot_keeps_its_llm_requests_on_generation_stages(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))

            class _Control:
                @staticmethod
                def snapshot():
                    return {
                        "commands": [
                            {
                                "kind": "document.run_pipeline",
                                "operation_id": "generation-operation",
                                "status": "running",
                            }
                        ]
                    }

                @staticmethod
                def operation(_operation_id: str):
                    return {"status": "running"}

                @staticmethod
                def stage_runs(_operation_id: str):
                    return [
                        {
                            "stage_command": "execute_content_plan",
                            "status": "running",
                            "attempt": 1,
                        }
                    ]

                @staticmethod
                def llm_requests(_operation_id: str):
                    return [
                        {
                            "request_id": "generation-request",
                            "stage_id": "execute_content_plan",
                            "status": "running",
                            "parameters": {"candidate_attempt": 1},
                        }
                    ]

            builder = V3WorkspaceSnapshotBuilder(context)
            with mock.patch.object(builder, "_content_progress", return_value={}):
                generation = builder._generation_snapshot(
                    _Control(),
                    plan={},
                    writer_research={},
                    delivery={},
                )

            writing = next(
                stage
                for stage in generation["stages"]
                if stage["stage_id"] == "execute_content_plan"
            )
            self.assertEqual(writing["llm_request_count"], 1)
            self.assertEqual(
                writing["llm_requests"][0]["request_id"],
                "generation-request",
            )

    def test_confirmed_planning_marks_the_outline_human_gate_complete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))

            class _Control:
                @staticmethod
                def stage_runs(_operation_id: str):
                    return [
                        {
                            "stage_command": "confirm_planning",
                            "status": "blocked_human",
                            "attempt": 1,
                        }
                    ]

                @staticmethod
                def llm_requests(_operation_id: str):
                    return []

            pipeline = V3WorkspaceSnapshotBuilder(context)._analysis_pipeline(
                _Control(),
                {},
                {},
                {"operation_id": "outline-operation", "status": "blocked_human"},
                planning_confirmed=True,
            )
            confirmation = next(
                stage for stage in pipeline["stages"]
                if stage["stage_id"] == "confirm_planning"
            )
            self.assertEqual(confirmation["status"], "succeeded")

    def test_empty_outline_does_not_expose_a_stale_human_confirmation_gate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))

            class _Control:
                @staticmethod
                def stage_runs(_operation_id: str):
                    return [{"stage_command": "confirm_planning", "status": "blocked_human"}]

                @staticmethod
                def llm_requests(_operation_id: str):
                    return []

            pipeline = V3WorkspaceSnapshotBuilder(context)._analysis_pipeline(
                _Control(),
                {},
                {},
                {"operation_id": "outline-operation", "status": "blocked_human"},
                planning_status="blocked",
            )
            confirmation = next(
                stage for stage in pipeline["stages"]
                if stage["stage_id"] == "confirm_planning"
            )
            self.assertEqual(confirmation["status"], "pending")
            self.assertEqual(pipeline["status"], "failed")

    def test_pipeline_records_queued_running_and_terminal_for_every_stage(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            runner = _Runner()
            controller = V3ExecutionController(context, runner=runner)
            with mock.patch.object(
                controller.store,
                "record_stage_run",
                wraps=controller.store.record_stage_run,
            ) as record:
                result = controller.run_pipeline(
                    context,
                    self._envelope(),
                    "operation-progress",
                )

            self.assertEqual(result["operation_status"], "succeeded")
            self.assertNotIn("resolve_evidence", runner.calls)
            transitions = [
                (call.args[1], call.args[2])
                for call in record.call_args_list
            ]
            for stage in V3_GENERATION_STAGES:
                self.assertIn((stage, "queued"), transitions)
                self.assertIn((stage, "running"), transitions)
                self.assertIn((stage, "succeeded"), transitions)

    def test_pipeline_failure_marks_exact_stage_and_cancels_future_stages(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            controller = V3ExecutionController(
                context,
                runner=_Runner("execute_content_plan"),
            )
            with self.assertRaisesRegex(RuntimeError, "execute_content_plan failed"):
                controller.run_pipeline(
                    context,
                    self._envelope(),
                    "operation-failure",
                )

            states = {
                item["stage_command"]: item
                for item in ControlStore(context).stage_runs("operation-failure")
            }
            self.assertEqual(states["execute_content_plan"]["status"], "failed")
            self.assertEqual(
                states["execute_content_plan"]["error"]["message"],
                "execute_content_plan failed",
            )
            self.assertEqual(states["integrate_document"]["status"], "cancelled")
            self.assertEqual(states["verify_delivery"]["status"], "cancelled")

    def test_selected_chapter_stops_after_writing_without_full_document_delivery(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            runner = _Runner()
            controller = V3ExecutionController(context, runner=runner)
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "document.run_pipeline",
                    "payload": {"chapter_ids": ["chapter-3"]},
                    "expected_revision": 0,
                    "idempotency_key": "generation-chapter-test",
                },
                workspace_id="alpha",
            )

            result = controller.run_pipeline(
                context,
                envelope,
                "operation-chapter",
            )

            self.assertEqual(result["operation_status"], "succeeded")
            self.assertEqual(runner.chapter_ids, ["chapter-3"])
            self.assertEqual(
                runner.calls,
                list(V3_GENERATION_STAGES[:4]),
            )

    def test_writer_research_requirement_pauses_current_generation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            controller = V3ExecutionController(context, runner=_ResearchBlockedRunner())
            result = controller.run_pipeline(context, self._envelope(), "operation-research-blocked")
            self.assertEqual(result["operation_status"], "blocked")
            states = {
                item["stage_command"]: item
                for item in ControlStore(context).stage_runs("operation-research-blocked")
            }
            self.assertEqual(states["execute_content_plan"]["status"], "paused")
            self.assertEqual(states["integrate_document"]["status"], "cancelled")

    def test_snapshot_returns_excerpt_and_detail_returns_full_registered_content(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            unit_id = "unit-section-1"
            write_json(
                context.root / CONTENT_UNITS_PATH,
                {
                    "schema_version": "v3",
                    "units": [
                        {
                            "unit_id": unit_id,
                            "contract_revision": 1,
                            "node_ids": ["chapter-1"],
                            "upstream_unit_ids": [],
                        }
                    ],
                },
            )
            long_text = "实施方案正文" * 80
            block = ContentBlock(
                block_id="block-1",
                target_node_id="chapter-1",
                type="paragraph",
                content=long_text,
                confidence=0.9,
            )
            output = context.root / V3_ROOT / "content_units" / f"{unit_id}.json"
            base_fingerprint = writer_base_fingerprint(
                context,
                unit_id=unit_id,
                contract_revision=1,
                node_ids=["chapter-1"],
            )
            fingerprint = writer_fingerprint(base_fingerprint, [])
            write_json(
                output,
                {
                    "schema_version": "v3",
                    "unit_id": unit_id,
                    "bundle_id": "",
                    "writer_base_fingerprint": base_fingerprint,
                    "writer_fingerprint": fingerprint,
                    "evidence_batches": [],
                    "blocks": [block.model_dump(mode="json")],
                },
            )
            ControlStore(context).upsert_content_unit_state(
                {
                    "unit_id": unit_id,
                    "contract_revision": 1,
                    "state": "completed",
                    "attempt": 1,
                    "writer_fingerprint": fingerprint,
                    "output_artifact_id": output.relative_to(
                        context.root
                    ).as_posix(),
                }
            )

            builder = V3WorkspaceSnapshotBuilder(context)
            projection = builder._content_progress(
                ControlStore(context),
                {"nodes": [{"chapter_id": "chapter-1", "title": "第一章 实施方案"}]},
            )
            unit = projection["units"][0]
            self.assertEqual(unit["title"], "第一章 实施方案")
            self.assertEqual(unit["block_count"], 1)
            self.assertEqual(len(unit["preview"]), 200)
            self.assertNotEqual(unit["preview"], long_text)

            detail = builder.content_unit_detail(unit_id)
            self.assertEqual(detail["blocks"][0]["content"], long_text)
            self.assertEqual(detail["block_count"], 1)

    def test_generation_stage_detail_projects_research_decision_and_evidence_use(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            batch_id = "EB-0123456789abcdef"
            evidence_used = EvidenceItem(
                evidence_id="E-used",
                batch_id=batch_id,
                source_type=EvidenceSourceType.OFFICIAL,
                title="信息安全技术标准",
                source_url="https://example.gov.cn/security-standard",
                publisher="国家标准公开平台",
                content="该标准规定了安全设计、实施控制与验收检查的公开要求。",
                claim_types=["standard", "method"],
                retrieved_at="2026-07-30T08:00:00+00:00",
            )
            evidence_unused = EvidenceItem(
                evidence_id="E-unused",
                batch_id=batch_id,
                source_type=EvidenceSourceType.WEB,
                title="行业实施观察",
                source_url="https://example.com/industry-note",
                publisher="行业研究机构",
                content="该资料提供了行业背景，但最终正文未采用。",
                claim_types=["project_context"],
                retrieved_at="2026-07-30T08:01:00+00:00",
            )
            batch = EvidenceBatch(
                revision=1,
                source_hashes={"query": "hash"},
                batch_id=batch_id,
                need_id="EN-WR-trace",
                query_count=1,
                items=[evidence_used, evidence_unused],
                status="published",
            )
            write_json(
                context.root / EVIDENCE_BATCH_DIR / f"{batch_id}.json",
                batch.model_dump(mode="json"),
            )
            research_call = {
                "decision_id": "WRD-trace",
                "operation_id": "operation-trace",
                "unit_id": "unit-security",
                "applicable_chapter_ids": ["chapter-security"],
                "applicable_chapter_titles": ["第三章 安全设计"],
                "needs_research": True,
                "reason": "本章涉及现行标准和验收方法，需要公开来源支撑。",
                "decision_status": "published",
                "queries": [
                    {
                        "query_id": "WRQ-trace",
                        "question": "检索现行信息安全标准与验收要求",
                        "target_node_ids": ["chapter-security"],
                        "applicability": "第三章 安全设计",
                        "status": "published",
                        "attempts": [
                            {
                                "attempt": 1,
                                "status": "published",
                                "batch_id": batch_id,
                                "evidence_count": 2,
                                "source_count": 2,
                                "duration_ms": 321,
                            }
                        ],
                        "batch_id": batch_id,
                        "evidence_count": 2,
                        "sources": [],
                        "error": "",
                    }
                ],
                "prohibited_research_scopes": ["企业资质与资格"],
                "used_evidence_by_chapter": {
                    "chapter-security": ["E-used"],
                },
                "created_at": "2026-07-30T07:59:00+00:00",
            }
            snapshot = {
                "generation": {
                    "stages": [
                        {
                            "stage_id": "execute_content_plan",
                            "label": "章节写作",
                            "status": "running",
                            "attempt": 1,
                            "summary": {},
                            "warnings": [],
                            "warning_count": 0,
                        }
                    ],
                    "content": {
                        "total_units": 1,
                        "completed_units": 1,
                        "running_units": 0,
                        "failed_units": 0,
                        "units": [
                            {
                                "unit_id": "unit-security",
                                "title": "第三章 安全设计",
                                "status": "completed",
                                "attempt": 1,
                                "updated_at": "2026-07-30T08:02:00+00:00",
                            }
                        ],
                    },
                    "research": {
                        "call_count": 1,
                        "published_count": 1,
                        "calls": [research_call],
                    },
                },
                "analysis": {"pipeline": {"stages": []}},
            }
            builder = V3WorkspaceSnapshotBuilder(context)
            with mock.patch.object(builder, "build", return_value=snapshot):
                detail = builder.generation_stage_detail("execute_content_plan")

            self.assertIn("不展示模型内部隐藏推理", detail["trace_disclosure"])
            self.assertEqual(detail["details"]["research_call_count"], 1)
            self.assertEqual(detail["details"]["search_query_count"], 1)
            self.assertEqual(detail["details"]["research_source_count"], 2)
            self.assertEqual(detail["details"]["used_evidence_count"], 1)
            trace = detail["research_trace"][0]
            self.assertEqual(trace["unit_status"], "completed")
            self.assertEqual(trace["chapter_titles"], ["第三章 安全设计"])
            self.assertEqual(
                trace["decision_summary"],
                "本章涉及现行标准和验收方法，需要公开来源支撑。",
            )
            query = trace["queries"][0]
            self.assertEqual(
                query["question"],
                "检索现行信息安全标准与验收要求",
            )
            self.assertEqual(query["attempts"][0]["status"], "published")
            self.assertEqual(query["attempts"][0]["duration_ms"], 321)
            used, unused = query["results"]
            self.assertEqual(
                used["source_url"],
                "https://example.gov.cn/security-standard",
            )
            self.assertIn("安全设计", used["answer_excerpt"])
            self.assertTrue(used["used_in_bid"])
            self.assertEqual(used["usage_status"], "used")
            self.assertEqual(
                used["used_in_chapters"][0]["chapter_title"],
                "第三章 安全设计",
            )
            self.assertFalse(unused["used_in_bid"])
            self.assertEqual(unused["usage_status"], "not_used")

    def test_generation_stage_detail_projects_current_writing_chapter(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            snapshot = {
                "generation": {
                    "stages": [
                        {
                            "stage_id": "execute_content_plan",
                            "label": "章节写作",
                            "status": "running",
                            "attempt": 1,
                            "summary": {},
                            "warnings": [],
                            "warning_count": 0,
                        }
                    ],
                    "content": {
                        "total_units": 2,
                        "completed_units": 0,
                        "running_units": 1,
                        "failed_units": 0,
                        "units": [
                            {
                                "unit_id": "unit-security",
                                "title": "第三章 安全设计",
                                "status": "running",
                                "attempt": 2,
                                "current_chapter_id": "chapter-security-design",
                                "current_chapter_title": "3.2 安全体系设计",
                                "progress_phase": "drafting",
                                "updated_at": "2026-07-30T08:03:00+00:00",
                            },
                            {
                                "unit_id": "unit-service",
                                "title": "第四章 服务方案",
                                "status": "queued",
                            },
                        ],
                    },
                    "research": {"call_count": 0, "published_count": 0, "calls": []},
                },
                "analysis": {"pipeline": {"stages": []}},
            }
            builder = V3WorkspaceSnapshotBuilder(context)
            with mock.patch.object(builder, "build", return_value=snapshot):
                detail = builder.generation_stage_detail("execute_content_plan")

            self.assertEqual(
                detail["current_writing"],
                {
                    "unit_id": "unit-security",
                    "unit_title": "第三章 安全设计",
                    "unit_status": "running",
                    "chapter_id": "chapter-security-design",
                    "chapter_title": "3.2 安全体系设计",
                    "phase": "drafting",
                    "error": "",
                    "updated_at": "2026-07-30T08:03:00+00:00",
                },
            )

    def test_running_content_unit_persists_current_chapter(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            store = ControlStore(context)
            store.upsert_content_unit_state(
                {
                    "unit_id": "unit-security",
                    "contract_revision": 1,
                    "state": "running",
                    "attempt": 1,
                }
            )
            progress = store.update_content_unit_progress(
                "unit-security",
                chapter_id="chapter-security-design",
                chapter_title="3.2 安全体系设计",
                phase="drafting",
            )

            self.assertEqual(progress["current_chapter_id"], "chapter-security-design")
            self.assertEqual(progress["current_chapter_title"], "3.2 安全体系设计")
            self.assertEqual(progress["progress_phase"], "drafting")

    def test_legacy_completed_content_is_stale_and_cannot_be_previewed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            unit_id = "unit-legacy"
            write_json(
                context.root / CONTENT_UNITS_PATH,
                {
                    "schema_version": "v3",
                    "units": [
                        {
                            "unit_id": unit_id,
                            "contract_revision": 1,
                            "node_ids": ["chapter-legacy"],
                            "upstream_unit_ids": [],
                        }
                    ],
                },
            )
            output = context.root / V3_ROOT / "content_units" / f"{unit_id}.json"
            write_json(
                output,
                {
                    "schema_version": "v3",
                    "unit_id": unit_id,
                    "blocks": [],
                },
            )
            # A legacy row deliberately has no writer_fingerprint.
            ControlStore(context).upsert_content_unit_state(
                {
                    "unit_id": unit_id,
                    "contract_revision": 1,
                    "state": "completed",
                    "output_artifact_id": output.relative_to(
                        context.root
                    ).as_posix(),
                }
            )
            builder = V3WorkspaceSnapshotBuilder(context)
            projection = builder._content_progress(
                ControlStore(context),
                {
                    "nodes": [
                        {
                            "chapter_id": "chapter-legacy",
                            "title": "旧章节",
                        }
                    ]
                },
            )
            self.assertEqual(projection["stale_units"], 1)
            self.assertEqual(projection["units"][0]["status"], "stale")
            self.assertEqual(projection["units"][0]["preview"], "")
            with self.assertRaises(ControlPlaneError) as raised:
                builder.content_unit_detail(unit_id)
            self.assertEqual(raised.exception.code, "CONTENT_UNIT_STALE")

    def test_content_detail_rejects_unknown_and_unregistered_paths(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            builder = V3WorkspaceSnapshotBuilder(context)
            with self.assertRaises(ControlPlaneError) as missing:
                builder.content_unit_detail("unknown")
            self.assertEqual(missing.exception.code, "CONTENT_UNIT_NOT_FOUND")

            ControlStore(context).upsert_content_unit_state(
                {
                    "unit_id": "unit-unsafe",
                    "contract_revision": 1,
                    "state": "completed",
                    "output_artifact_id": "workspace/outside.json",
                }
            )
            with self.assertRaises(ControlPlaneError) as unsafe:
                builder.content_unit_detail("unit-unsafe")
            self.assertEqual(unsafe.exception.code, "CONTENT_UNIT_PATH_INVALID")

    def test_content_detail_route_rejects_principal_without_workspace_acl(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            context = self._context(root)
            ControlStore(context).grant_workspace_access("workspace-owner")
            settings = SettingsService(root)
            environment = {
                "BID_AGENT_AUTH_USER": "intruder",
                "BID_AGENT_AUTH_PASSWORD": "test-password",
                "BID_AGENT_AUTH_SECURE_COOKIE": "0",
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(v3_app, "RUNS_DIR", root / "runs"),
                mock.patch.object(v3_app, "SETTINGS", settings),
                TestClient(v3_app.app) as client,
            ):
                login = client.post(
                    "/api/auth/login",
                    json={
                        "username": "intruder",
                        "password": "test-password",
                    },
                )
                self.assertEqual(login.status_code, 200)
                response = client.get(
                    "/api/v3/workspaces/alpha/content-units/unknown"
                )
            self.assertEqual(response.status_code, 403)
