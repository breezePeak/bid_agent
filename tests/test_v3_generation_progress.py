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
from document_pipeline.contracts import ContentBlock
from document_pipeline.document_planner import CONTENT_UNITS_PATH
from document_pipeline.execution_controller import (
    V3ExecutionController,
    V3_PIPELINE_STAGES,
)
from document_pipeline.input_manifest import V3_ROOT
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder
from utils import write_json
from fastapi.testclient import TestClient


class _Runner:
    def __init__(self, fail_stage: str = "") -> None:
        self.fail_stage = fail_stage
        self.calls: list[str] = []

    def run(self, stage: str, *, operation_id: str | None = None):
        self.calls.append(stage)
        if stage == self.fail_stage:
            raise RuntimeError(f"{stage} failed")
        if stage == "confirm_planning":
            return {"verdict": "pass"}
        if stage == "plan_document":
            return object(), []
        if stage == "resolve_evidence":
            return {
                "provider_id": "deepseek_web",
                "planned_count": 2,
                "published_count": 1,
                "gap_count": 1,
                "failed_count": 0,
            }
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
            self.assertLess(
                runner.calls.index("resolve_evidence"),
                runner.calls.index("execute_content_plan"),
            )
            transitions = [
                (call.args[1], call.args[2])
                for call in record.call_args_list
            ]
            for stage in V3_PIPELINE_STAGES:
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
            write_json(
                output,
                {
                    "schema_version": "v3",
                    "unit_id": unit_id,
                    "bundle_id": "",
                    "blocks": [block.model_dump(mode="json")],
                },
            )
            ControlStore(context).upsert_content_unit_state(
                {
                    "unit_id": unit_id,
                    "contract_revision": 1,
                    "state": "completed",
                    "attempt": 1,
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
