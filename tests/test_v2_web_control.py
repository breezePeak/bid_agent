from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app  # noqa: E402
import httpx  # noqa: E402
from fastapi import UploadFile  # noqa: E402
from agent.repair_jobs import create_confirmation  # noqa: E402
from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402


class _Request:
    def __init__(self, body: dict, *, principal: dict | None = None) -> None:
        self.body = body
        self.state = SimpleNamespace(principal=principal) if principal is not None else SimpleNamespace()

    async def json(self) -> dict:
        return self.body


class _EventRequest:
    def __init__(self, last_event_id: str = "") -> None:
        self.headers = {"last-event-id": last_event_id} if last_event_id else {}

    async def is_disconnected(self) -> bool:
        return False


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class V2WebControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_active_id = web_app.ACTIVE_RUN_ID
        self.previous_active_root = web_app.ACTIVE_RUN_ROOT
        self.previous_running = web_app.RUNNING
        self.previous_current_root = web_app.CURRENT_RUN_ROOT

    def tearDown(self) -> None:
        web_app.ACTIVE_RUN_ID = self.previous_active_id
        web_app.ACTIVE_RUN_ROOT = self.previous_active_root
        web_app.RUNNING = self.previous_running
        web_app.CURRENT_RUN_ROOT = self.previous_current_root
        web_app.SUPERVISOR.set_status_listener(None)

    def test_v2_chat_turn_preserves_authenticated_principal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            principal = {"type": "user", "id": "chat-owner", "role": "admin"}

            async def inspect_request(forwarded_request):
                forwarded_body = await forwarded_request.json()
                self.assertEqual(forwarded_body["run_id"], "alpha")
                self.assertEqual(forwarded_request.state.principal, principal)
                self.assertEqual(web_app._request_actor(forwarded_request, source="chat"), principal)
                return web_app.JSONResponse({"ok": True})

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "api_chat_orchestrate", side_effect=inspect_request) as orchestrate:
                    response = _body(
                        asyncio.run(
                            web_app.api_v2_chat_turn(
                                "alpha",
                                _Request({"message": "继续", "run_id": "spoofed"}, principal=principal),
                            )
                        )
                    )
            self.assertTrue(response["ok"])
            orchestrate.assert_called_once()

    def test_material_readiness_does_not_trust_legacy_ready_projection(self) -> None:
        self.assertFalse(
            web_app._material_fulfillment_verified(
                {
                    "response_status": "ready",
                    "evidence_status": "missing",
                    "lifecycle_status": "resolved",
                }
            )
        )
        self.assertTrue(
            web_app._material_fulfillment_verified(
                {
                    "response_status": "ready",
                    "evidence_status": "missing",
                    "lifecycle_status": "resolved",
                    "verified_at": "2026-07-21T00:00:00Z",
                }
            )
        )

    def test_v2_gate_reads_sqlite_without_syncing_legacy_issue_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).replace_issue_states(
                [{"id": "block-1", "status": "open", "severity": "block", "command": "global-review"}],
                source="test",
            )

            with mock.patch("agent.issues.quality_gate_mode", return_value="hard"):
                with mock.patch("agent.root_cause.sync_issues_from_compliance") as sync_compliance:
                    with mock.patch("agent.root_cause.sync_issues_from_global_review") as sync_review:
                        blocked = web_app._v2_gate_can_proceed(context, "build-md")
                        revalidate = web_app._v2_gate_can_proceed(context, "global-review")

            sync_compliance.assert_not_called()
            sync_review.assert_not_called()
            self.assertFalse(blocked["can_proceed"])
            self.assertEqual(blocked["source"], "control.db")
            self.assertTrue(revalidate["can_proceed"])
            self.assertTrue(revalidate["revalidate_allowed"])

    def test_v2_gate_never_soft_allows_fatal_or_qualification_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).replace_issue_states(
                [
                    {"id": "fatal-1", "status": "open", "severity": "fatal"},
                    {"id": "qualification-1", "status": "open", "severity": "block", "category": "qualification"},
                ],
                source="test",
            )

            with mock.patch("agent.issues.quality_gate_mode", return_value="soft"):
                gate = web_app._v2_gate_can_proceed(context, "build-md")

            self.assertFalse(gate["can_proceed"])
            self.assertEqual(gate["block_count"], 2)

    def test_v2_gate_fails_closed_when_sqlite_state_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            with mock.patch.object(ControlStore, "issue_states", side_effect=sqlite3.OperationalError("locked")):
                with self.assertRaisesRegex(Exception, "已拒绝执行"):
                    web_app._v2_gate_can_proceed(context, "build-md")

    def test_v2_gate_imports_legacy_issues_once_then_keeps_sqlite_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            issue_dir = root / "workspace" / "issues"
            issue_dir.mkdir(parents=True)
            path = issue_dir / "open.json"
            path.write_text(
                json.dumps({"issues": [{"id": "legacy-block", "status": "open", "severity": "block"}]}),
                encoding="utf-8",
            )
            context = WorkspaceContext.resolve(runs, "alpha")

            with mock.patch("agent.issues.quality_gate_mode", return_value="hard"):
                first = web_app._v2_gate_can_proceed(context, "build-md")
                path.write_text(json.dumps({"issues": []}), encoding="utf-8")
                second = web_app._v2_gate_can_proceed(context, "build-md")

            self.assertFalse(first["can_proceed"])
            self.assertFalse(second["can_proceed"])
            imported = ControlStore(context).issue_states()
            self.assertEqual(imported[0]["id"], "legacy-block")
            self.assertEqual(imported[0]["control_source"], "v1_import")

    def test_v2_issue_import_fails_closed_for_invalid_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            issue_dir = root / "workspace" / "issues"
            issue_dir.mkdir(parents=True)
            (issue_dir / "open.json").write_text("{invalid", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")

            with self.assertRaisesRegex(Exception, "无法导入"):
                web_app._v2_gate_can_proceed(context, "build-md")
            self.assertTrue(ControlStore(context).issue_v1_import_pending())

    def test_v2_start_snapshot_pause_and_cancel_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_v2_gate_can_proceed", return_value={"can_proceed": True}):
                    with mock.patch.object(web_app.SUPERVISOR, "is_running", return_value=False):
                        with mock.patch.object(web_app.SUPERVISOR, "start", return_value=True) as start:
                            start_response = asyncio.run(
                                web_app.api_v2_submit_command(
                                    "alpha",
                                    _Request(
                                        {
                                            "kind": "pipeline.start",
                                            "payload": {"start_command": ""},
                                            "expected_revision": 0,
                                            "idempotency_key": "start-once",
                                        }
                                    ),
                                )
                            )
            started = _body(start_response)
            self.assertTrue(started["ok"])
            operation_id = started["receipt"]["operation_id"]
            self.assertEqual(start.call_args.kwargs["operation_id"], operation_id)
            self.assertTrue((root / "workspace" / "control.db").exists())

            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            self.assertEqual(store.snapshot()["operation"]["status"], "running")

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app.SUPERVISOR, "is_running", return_value=True):
                    with mock.patch.object(web_app.SUPERVISOR, "pause") as pause:
                        with mock.patch.object(web_app, "_terminate_workspace_process"):
                            pause_response = asyncio.run(
                                web_app.api_v2_submit_command(
                                    "alpha",
                                    _Request(
                                        {
                                            "kind": "pipeline.pause",
                                            "payload": {"operation_id": operation_id},
                                            "expected_revision": store.revision(),
                                            "idempotency_key": "pause-once",
                                        }
                                    ),
                                )
                            )
            self.assertTrue(_body(pause_response)["ok"])
            pause.assert_called_once()

            store.sync_operation(operation_id, "paused", message="paused")
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                cancel_response = asyncio.run(
                    web_app.api_v2_submit_command(
                        "alpha",
                        _Request(
                            {
                                "kind": "pipeline.cancel",
                                "payload": {"operation_id": operation_id},
                                "expected_revision": store.revision(),
                                "idempotency_key": "cancel-once",
                            }
                        ),
                    )
                )
            cancel = _body(cancel_response)
            self.assertEqual(cancel["receipt"]["status"], "requires_confirmation")
            action_id = cancel["action"]["action_id"]

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app.SUPERVISOR, "is_running", return_value=False):
                    confirmed = web_app.api_v2_confirm_action("alpha", action_id, _Request({}))
            confirmed_body = _body(confirmed)
            self.assertTrue(confirmed_body["ok"])
            self.assertEqual(ControlStore(context).snapshot()["operation"]["status"], "cancelled")

    def test_chat_uses_explicit_workspace_and_pause_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)

            def start_handler(ctx, envelope, operation_id):
                return {"accepted": True, "operation_status": "running"}

            from control_plane import CommandEnvelope, CommandGateway

            gateway = CommandGateway(context, {"pipeline.start": start_handler})
            gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "pipeline.start",
                        "payload": {},
                        "expected_revision": store.revision(),
                        "idempotency_key": str(uuid.uuid4()),
                    },
                    workspace_id="alpha",
                )
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app.SUPERVISOR, "is_running", return_value=False):
                    response = asyncio.run(
                        web_app.api_chat_orchestrate(
                            _Request({"run_id": "alpha", "message": "暂停", "idempotency_key": "chat-pause"})
                        )
                    )
            payload = _body(response)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["intent"], "pipeline_control")
            self.assertEqual(ControlStore(context).snapshot()["operation"]["status"], "paused")
            self.assertFalse((beta / "workspace" / "control.db").exists())
            web_app.close_chat_store(context.root)

    def test_v2_repair_requires_confirmation_and_uses_same_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposal_response = asyncio.run(
                    web_app.api_v2_submit_command(
                        "alpha",
                        _Request(
                            {
                                "kind": "repair.start",
                                "payload": {},
                                "expected_revision": 0,
                                "idempotency_key": "repair-once",
                            }
                        ),
                    )
                )
            proposal = _body(proposal_response)
            self.assertEqual(proposal["receipt"]["status"], "requires_confirmation")

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(
                    web_app,
                    "_ensure_minimal_repair_confirmation",
                    return_value={"confirmation_id": "v1-confirmation"},
                ):
                    with mock.patch.object(
                        web_app,
                        "_trigger_repair_job",
                        return_value={"ok": True, "message": "repair started"},
                    ) as trigger:
                        confirmed = web_app.api_v2_confirm_action(
                            "alpha",
                            proposal["action"]["action_id"],
                            _Request({}),
                        )

            confirmed_body = _body(confirmed)
            self.assertTrue(confirmed_body["ok"])
            operation_id = confirmed_body["receipt"]["operation_id"]
            operation = ControlStore(WorkspaceContext.resolve(runs, "alpha")).operation(operation_id)
            self.assertEqual(operation["kind"], "repair.start")
            self.assertEqual(operation["status"], "running")
            self.assertEqual(trigger.call_args.kwargs["control_operation_id"], operation_id)
            self.assertFalse(trigger.call_args.kwargs["resume_pipeline"])

    def test_repair_worker_closes_v2_operation_without_implicit_pipeline_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {
                    "repair.start": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "running",
                    }
                },
            )
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "repair.start",
                    "payload": {},
                    "expected_revision": 0,
                    "idempotency_key": "repair-worker",
                },
                workspace_id="alpha",
            )
            action = gateway.propose(envelope, label="confirm repair", risk="high")
            receipt = gateway.confirm(action["confirmation_id"])
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            job = create_confirmation(
                root,
                issue_fingerprints=["issue-fingerprint"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            repair_result = {
                "ok": True,
                "resolved": ["issue-1"],
                "still_open": [],
                "manual": [],
                "failed": [],
                "message": "repaired",
            }
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_minimal_repair_candidates", return_value=[{"id": "issue-1"}]):
                    with mock.patch("agent.repair.execute_repair_batch", return_value=repair_result):
                        with mock.patch.object(web_app, "save_message"):
                            with mock.patch.object(web_app.SUPERVISOR, "start") as resume:
                                started = web_app._trigger_repair_job(
                                    root,
                                    job["confirmation_id"],
                                    control_operation_id=receipt.operation_id or "",
                                    control_fencing_token=int(operation.get("fencing_token") or 0),
                                    resume_pipeline=False,
                                )
                                self.assertTrue(started["ok"])
                                deadline = time.monotonic() + 2
                                while time.monotonic() < deadline:
                                    current = gateway.store.operation(receipt.operation_id or "") or {}
                                    if current.get("status") == "succeeded":
                                        break
                                    time.sleep(0.01)
                                started["_worker_thread"].join(timeout=2)
            self.assertEqual(current.get("status"), "succeeded")
            resume.assert_not_called()

    def test_v2_rewrite_requires_confirmation_and_passes_workspace_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposal_response = asyncio.run(
                    web_app.api_v2_submit_command(
                        "alpha",
                        _Request(
                            {
                                "kind": "rewrite.chapters",
                                "payload": {"chapter_ids": ["1.1", "2.3"]},
                                "expected_revision": 0,
                                "idempotency_key": "rewrite-once",
                            }
                        ),
                    )
                )
            proposal = _body(proposal_response)
            self.assertEqual(proposal["receipt"]["status"], "requires_confirmation")

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(
                    web_app,
                    "_trigger_rewrite_targets_inline",
                    return_value={"ok": True, "message": "rewrite started"},
                ) as trigger:
                    confirmed = web_app.api_v2_confirm_action(
                        "alpha",
                        proposal["action"]["action_id"],
                        _Request({}),
                    )
            confirmed_body = _body(confirmed)
            self.assertTrue(confirmed_body["ok"])
            operation_id = confirmed_body["receipt"]["operation_id"]
            self.assertTrue(web_app._same_path(trigger.call_args.kwargs["root"], root))
            self.assertEqual(trigger.call_args.kwargs["run_id"], "alpha")
            self.assertEqual(trigger.call_args.kwargs["control_operation_id"], operation_id)

    def test_chat_rewrite_proposes_v2_action_without_starting_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            plan = {
                "reply": "ready to rewrite",
                "actions": [],
                "trigger_rewrite_targets": [{"chapter_id": "1.1"}],
            }

            try:
                with mock.patch.object(web_app, "RUNS_DIR", runs):
                    with mock.patch.object(web_app, "_minimal_repair_candidates", return_value=[]):
                        with mock.patch.object(web_app, "orchestrator_plan", return_value=plan):
                            with mock.patch.object(web_app, "orchestrator_resolve", return_value=plan):
                                with mock.patch.object(web_app, "_trigger_rewrite_targets_inline") as worker:
                                    response = asyncio.run(
                                        web_app.api_chat_orchestrate(
                                            _Request({"run_id": "alpha", "message": "重写 1.1"})
                                        )
                                    )
                payload = _body(response)
                self.assertTrue(payload["rewrite_proposed"])
                self.assertFalse(payload["triggered_rewrite"])
                self.assertEqual(payload["actions"][0]["type"], "confirm_v2_command")
                worker.assert_not_called()
            finally:
                web_app.close_chat_store(WorkspaceContext.resolve(runs, "alpha").root)

    def test_rewrite_worker_closes_v2_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {
                    "rewrite.chapters": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "running",
                    }
                },
            )
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "rewrite.chapters",
                    "payload": {"chapter_ids": ["1.1"]},
                    "expected_revision": 0,
                    "idempotency_key": "rewrite-worker",
                },
                workspace_id="alpha",
            )
            action = gateway.propose(envelope, label="confirm rewrite", risk="high")
            receipt = gateway.confirm(action["confirmation_id"])
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("subagent_runner.run_rewrite_all", return_value={"completed": ["1.1"], "failed": []}):
                    started = web_app._trigger_rewrite_targets_inline(
                        [{"chapter_id": "1.1"}],
                        root=context.root,
                        run_id="alpha",
                        control_operation_id=receipt.operation_id or "",
                        control_fencing_token=int(operation.get("fencing_token") or 0),
                    )
                    self.assertTrue(started["ok"])
                    deadline = time.monotonic() + 2
                    current = gateway.store.operation(receipt.operation_id or "") or {}
                    while time.monotonic() < deadline and current.get("status") != "succeeded":
                        time.sleep(0.01)
                        current = gateway.store.operation(receipt.operation_id or "") or {}
            self.assertEqual(current.get("status"), "succeeded")
            self.assertFalse(web_app.RUNNING)

    def test_rewrite_worker_fails_closed_when_control_state_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {"rewrite.chapters": lambda ctx, envelope, operation_id: {
                    "accepted": True,
                    "operation_status": "running",
                }},
            )
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "rewrite.chapters",
                    "payload": {"chapter_ids": ["1.1"]},
                    "expected_revision": 0,
                    "idempotency_key": "rewrite-state-unavailable",
                },
                workspace_id="alpha",
            )
            action = gateway.propose(envelope, label="confirm rewrite", risk="high")
            receipt = gateway.confirm(action["confirmation_id"])
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            sync_attempted = threading.Event()
            worker_done = threading.Event()

            def unavailable(*args, **kwargs):
                sync_attempted.set()
                raise RuntimeError("control db unavailable")

            def capture_log(message: str) -> None:
                if "终态回写失败" in message:
                    worker_done.set()

            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(ControlStore, "sync_operation", side_effect=unavailable):
                    with mock.patch.object(web_app, "_append_log", side_effect=capture_log):
                        with mock.patch("subagent_runner.run_rewrite_all") as rewrite:
                            started = web_app._trigger_rewrite_targets_inline(
                                [{"chapter_id": "1.1"}],
                                root=context.root,
                                run_id="alpha",
                                control_operation_id=receipt.operation_id or "",
                                control_fencing_token=int(operation.get("fencing_token") or 0),
                            )
                            self.assertTrue(started["ok"])
                            self.assertTrue(sync_attempted.wait(2))
                            self.assertTrue(worker_done.wait(2))
                            self.assertFalse(web_app.RUNNING)
                            rewrite.assert_not_called()

    def test_rewrite_worker_rejects_execution_without_control_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs" / "alpha"
            root.mkdir(parents=True)
            web_app.RUNNING = False
            with mock.patch("subagent_runner.run_rewrite_all") as rewrite:
                result = web_app._trigger_rewrite_targets_inline(
                    [{"chapter_id": "1.1"}],
                    root=root,
                    run_id="alpha",
                )
            self.assertFalse(result["ok"])
            self.assertIn("Operation/fencing token", result["message"])
            rewrite.assert_not_called()

    def test_material_ready_requires_verification_before_confirmed_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            checklist = {
                "summary": {"total": 1, "deferred": 1, "ready": 0},
                "items": [
                    {
                        "item_id": "mat-cert",
                        "category": "qualification",
                        "severity": "block",
                        "requirement": "qualification certificate",
                        "response_status": "deferred",
                        "evidence_status": "missing",
                        "lifecycle_status": "uploaded",
                    }
                ],
            }
            (workspace / "materials_checklist.json").write_text(
                json.dumps(checklist),
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            async def propose(key: str):
                context = WorkspaceContext.resolve(runs, "alpha")
                return await web_app.api_v2_submit_command(
                    "alpha",
                    _Request(
                        {
                            "kind": "materials.update",
                            "payload": {"item_id": "mat-cert", "response_status": "ready"},
                            "expected_revision": ControlStore(context).revision(),
                            "idempotency_key": key,
                        }
                    ),
                )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                first = _body(asyncio.run(propose("material-ready-unverified")))
                blocked = _body(web_app.api_v2_confirm_action("alpha", first["action"]["action_id"], _Request({})))
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["receipt"]["error"]["code"], "GATE_BLOCKED")

            checklist["items"][0]["evidence_status"] = "verified"
            checklist["items"][0]["lifecycle_status"] = "verified"
            (workspace / "materials_checklist.json").write_text(
                json.dumps(checklist),
                encoding="utf-8",
            )
            ControlStore(WorkspaceContext.resolve(runs, "alpha")).upsert_material_state(
                checklist["items"][0],
                source="test_verified",
            )
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                second = _body(asyncio.run(propose("material-ready-verified")))
                with mock.patch(
                    "materials_checklist.update_item_response",
                    return_value={"ok": True, "message": "updated"},
                ) as update:
                    with mock.patch(
                        "materials_checklist.build_materials_checklist",
                        return_value=workspace / "materials_checklist.json",
                    ):
                        accepted = _body(
                            web_app.api_v2_confirm_action("alpha", second["action"]["action_id"], _Request({}))
                        )
            self.assertTrue(accepted["ok"], accepted)
            update.assert_called_once()
            operation_id = accepted["receipt"]["operation_id"]
            operation = ControlStore(WorkspaceContext.resolve(runs, "alpha")).operation(operation_id)
            self.assertEqual(operation["status"], "succeeded")

    def test_material_refill_rejects_unverified_ready_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "materials_checklist.json").write_text(
                json.dumps(
                    {
                        "summary": {"total": 1, "ready": 1},
                        "items": [
                            {
                                "item_id": "unsafe",
                                "response_status": "ready",
                                "evidence_status": "missing",
                                "lifecycle_status": "uploaded",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            context = WorkspaceContext.resolve(runs, "alpha")
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "materials.refill",
                                    "payload": {},
                                    "expected_revision": 0,
                                    "idempotency_key": "unsafe-refill",
                                }
                            ),
                        )
                    )
                )
                confirmed = _body(
                    web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"], _Request({}))
                )
            self.assertFalse(confirmed["ok"])
            self.assertEqual(confirmed["receipt"]["error"]["code"], "GATE_BLOCKED")
            self.assertEqual(ControlStore(context).snapshot()["operation"]["status"], "blocked")

    def test_legacy_material_update_only_creates_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("materials_checklist.update_item_response") as update:
                    response = asyncio.run(
                        web_app.api_materials_checklist_update(
                            _Request(
                                {
                                    "item_id": "legacy-item",
                                    "response_status": "ready",
                                }
                            )
                        )
                    )
            payload = _body(response)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers.get("deprecation"), "true")
            self.assertEqual(payload["status"], "requires_confirmation")
            self.assertEqual(payload["action"]["type"], "confirm_v2_command")
            update.assert_not_called()

    def test_material_upload_requires_confirmation_and_workspace_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "materials_checklist.json").write_text(
                json.dumps({"items": [{"item_id": "mat-upload", "response_status": "deferred"}]}),
                encoding="utf-8",
            )
            outside = Path(tmp) / "outside.txt"
            outside.write_text("not workspace owned", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "materials.upload",
                                    "payload": {"item_id": "mat-upload", "uploaded_path": str(outside)},
                                    "expected_revision": 0,
                                    "idempotency_key": "upload-outside",
                                }
                            ),
                        )
                    )
                )
                with mock.patch("materials_checklist.mark_material_uploaded") as upload:
                    rejected = _body(
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"], _Request({}))
                    )
            self.assertEqual(proposed["receipt"]["status"], "requires_confirmation")
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["receipt"]["error"]["code"], "UPLOAD_TOKEN_REQUIRED")
            upload.assert_not_called()

    def test_material_human_verification_uses_server_actor_not_client_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "materials_checklist.json").write_text(
                json.dumps({"items": [{"item_id": "mat-review", "response_status": "deferred"}]}),
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "materials.confirm_verification",
                                    "payload": {
                                        "item_id": "mat-review",
                                        "accept": True,
                                        "operator": "spoofed-operator",
                                    },
                                    "actor": {"type": "user", "id": "reviewer-7"},
                                    "expected_revision": 0,
                                    "idempotency_key": "confirm-material",
                                }
                            ),
                        )
                    )
                )
                verifier_result = {
                    "ok": True,
                    "item_id": "mat-review",
                    "lifecycle_status": "verified",
                }
                with mock.patch(
                    "agent.material_verifier.human_confirm_verification",
                    return_value=verifier_result,
                ) as verify:
                    with mock.patch(
                        "materials_checklist.update_item_response",
                        return_value={"ok": True, "message": "updated"},
                    ):
                        accepted = _body(
                            web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"], _Request({}))
                        )
            self.assertTrue(accepted["ok"])
            self.assertEqual(verify.call_args.kwargs["operator"], "anonymous")

    def test_legacy_material_upload_only_creates_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("materials_checklist.mark_material_uploaded") as upload:
                    context = WorkspaceContext.resolve(runs, "alpha")
                    staged_path = root / "workspace" / "material_uploads" / "staging" / "item.pdf"
                    staged_path.parent.mkdir(parents=True)
                    staged_path.write_bytes(b"pdf")
                    staged = ControlStore(context).register_material_upload(
                        staged_path=staged_path.relative_to(root).as_posix(),
                        filename="item.pdf",
                        sha256="hash",
                        size_bytes=3,
                    )
                    response = asyncio.run(
                        web_app.api_materials_checklist_upload(
                            _Request({"item_id": "legacy-item", "upload_token": staged["upload_token"]})
                        )
                    )
            payload = _body(response)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers.get("deprecation"), "true")
            self.assertEqual(payload["status"], "requires_confirmation")
            upload.assert_not_called()

    def test_material_upload_token_is_workspace_scoped_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "materials_checklist.json").write_text(
                json.dumps({"items": [{"item_id": "mat-token", "response_status": "deferred"}]}),
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            upload = UploadFile(filename="certificate.txt", file=io.BytesIO(b"certificate evidence"))
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                staged_response = asyncio.run(web_app.api_v2_stage_material_upload("alpha", upload))
                staged = _body(staged_response)
                self.assertEqual(staged_response.status_code, 201)
                self.assertNotIn("path", staged)
                context = WorkspaceContext.resolve(runs, "alpha")
                proposed = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "materials.upload",
                                    "payload": {
                                        "item_id": "mat-token",
                                        "upload_token": staged["upload_token"],
                                    },
                                    "expected_revision": ControlStore(context).revision(),
                                    "idempotency_key": "token-upload",
                                }
                            ),
                        )
                    )
                )
                with mock.patch(
                    "materials_checklist.mark_material_uploaded",
                    return_value={"ok": True, "lifecycle_status": "uploaded", "message": "registered"},
                ) as register:
                    accepted = _body(
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"], _Request({}))
                    )
                self.assertTrue(accepted["ok"])
                token_row = ControlStore(context).material_upload(staged["upload_token"])
                self.assertEqual(token_row["status"], "consumed")
                material_state = ControlStore(context).material_state("mat-token")
                self.assertEqual(material_state["lifecycle_status"], "uploaded")
                self.assertEqual(material_state["response_status"], "deferred")
                registered_path = Path(register.call_args.kwargs["uploaded_path"])
                self.assertTrue(web_app._same_path(registered_path.parent, workspace / "material_uploads" / "staging"))

                replay = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "materials.upload",
                                    "payload": {
                                        "item_id": "mat-token",
                                        "upload_token": staged["upload_token"],
                                    },
                                    "expected_revision": ControlStore(context).revision(),
                                    "idempotency_key": "token-replay",
                                }
                            ),
                        )
                    )
                )
                rejected = _body(
                    web_app.api_v2_confirm_action("alpha", replay["action"]["action_id"], _Request({}))
                )
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["receipt"]["error"]["code"], "UPLOAD_TOKEN_INVALID")

    def test_legacy_material_rebuild_routes_through_v2_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch(
                    "materials_checklist.build_materials_checklist",
                    return_value=root / "workspace" / "materials_checklist.json",
                ) as rebuild:
                    with mock.patch(
                        "materials_checklist.load_materials_checklist",
                        return_value={"summary": {"total": 0}, "items": []},
                    ):
                        (root / "workspace").mkdir(parents=True)
                        (root / "workspace" / "materials_checklist.json").write_text("{}", encoding="utf-8")
                        response = web_app.api_materials_checklist_rebuild(
                            _Request({}, principal={"type": "user", "id": "legacy-owner"})
                        )
            payload = _body(response)
            self.assertEqual(response.status_code, 202)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["receipt"]["status"], "accepted")
            rebuild.assert_called_once()
            self.assertTrue(web_app._same_path(rebuild.call_args.args[0], root))
            connection = sqlite3.connect(root / "workspace" / "control.db")
            try:
                row = connection.execute(
                    "SELECT actor_json FROM commands WHERE command_id = ?",
                    (payload["receipt"]["command_id"],),
                ).fetchone()
            finally:
                connection.close()
            actor = json.loads(str(row[0] if row else "{}"))
            self.assertEqual(actor, {"type": "user", "id": "legacy-owner"})

    def test_formal_export_requires_current_gate_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            outputs = root / "outputs"
            workspace.mkdir(parents=True)
            outputs.mkdir(parents=True)
            (outputs / "final.md").write_text("formal markdown", encoding="utf-8")
            (outputs / "final.docx").write_bytes(b"formal-docx-v1")
            (workspace / "materials_checklist.json").write_text(
                json.dumps({"items": []}),
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            preflight = {
                "ok": True,
                "can_export": True,
                "checks": [],
                "block_issues": [],
                "accepted_risks": [],
            }
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(
                    web_app,
                    "_v2_gate_can_proceed",
                    return_value={"can_proceed": True, "block_count": 0, "blocks": []},
                ):
                    with mock.patch.object(web_app, "_v2_export_preflight", return_value=preflight):
                        issued = _body(
                            asyncio.run(
                                web_app.api_v2_submit_command(
                                    "alpha",
                                    _Request(
                                        {
                                            "kind": "gate.revalidate",
                                            "payload": {},
                                            "expected_revision": 0,
                                            "idempotency_key": "formal-gate",
                                        }
                                    ),
                                )
                            )
                        )
                self.assertTrue(issued["ok"])
                latest = _body(web_app.api_v2_latest_gate_receipt("alpha"))["gate_receipt"]
                allowed = web_app.api_v2_download_final("alpha", latest["receipt_id"])
                self.assertEqual(Path(allowed.path).read_bytes(), b"formal-docx-v1")
                legacy_missing = _body(web_app.download_final_docx(""))
                self.assertEqual(legacy_missing["error"]["code"], "GATE_RECEIPT_REQUIRED")
                legacy_allowed = web_app.download_final_docx(latest["receipt_id"])
                self.assertEqual(Path(legacy_allowed.path).read_bytes(), b"formal-docx-v1")

                (outputs / "final.docx").write_bytes(b"formal-docx-v2")
                stale = _body(web_app.api_v2_download_final("alpha", latest["receipt_id"]))
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["error"]["code"], "GATE_RECEIPT_STALE")

    def test_v2_draft_download_is_scoped_to_path_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha" / "outputs"
            beta = runs / "beta" / "outputs"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "final.md").write_text("alpha draft", encoding="utf-8")
            (beta / "final.md").write_text("beta draft", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta.parent

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                response = web_app.api_v2_download_draft("alpha")

            self.assertEqual(Path(response.path).read_text(encoding="utf-8"), "alpha draft")

    def test_formal_gate_fingerprint_uses_sqlite_control_domains_not_v1_projections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            issue_dir = root / "workspace" / "issues"
            issue_dir.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.replace_issue_states(
                [{"id": "issue-1", "status": "open", "severity": "warn"}],
                source="test",
            )
            store.ensure_material_states([])

            first, _ = web_app._formal_gate_fingerprint(context)
            (issue_dir / "open.json").write_text(
                json.dumps({"issues": [{"id": "v1-only", "status": "open", "severity": "block"}]}),
                encoding="utf-8",
            )
            (root / "workspace" / "materials_checklist.json").write_text(
                json.dumps({"items": [{"item_id": "v1-only"}]}),
                encoding="utf-8",
            )
            projected, _ = web_app._formal_gate_fingerprint(context)
            store.replace_issue_states(
                [{"id": "issue-1", "status": "fixed", "severity": "warn"}],
                source="test",
            )
            changed_issue, _ = web_app._formal_gate_fingerprint(context)
            store.record_policy_decision(
                issue_id="issue-1",
                decision_type="accept_risk",
                decision={"reason": "test"},
                actor={"type": "user", "id": "owner"},
            )
            changed_policy, _ = web_app._formal_gate_fingerprint(context)
            store.upsert_artifact_state(
                {
                    "artifact_key": "outputs/final.docx",
                    "path": "outputs/final.docx",
                    "kind": "file",
                    "status": "stale",
                    "producer": "build-docx",
                    "sha256": "old",
                    "input_fingerprint": "old-input",
                }
            )
            changed_artifact, _ = web_app._formal_gate_fingerprint(context)

            self.assertEqual(first, projected)
            self.assertNotEqual(projected, changed_issue)
            self.assertNotEqual(changed_issue, changed_policy)
            self.assertNotEqual(changed_policy, changed_artifact)

    def test_formal_gate_blocks_stale_sqlite_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.docx").write_bytes(b"docx")
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).upsert_artifact_state(
                {
                    "artifact_key": "outputs/final.docx",
                    "path": "outputs/final.docx",
                    "kind": "file",
                    "status": "stale",
                    "producer": "build-docx",
                    "sha256": "old",
                    "input_fingerprint": "old-input",
                }
            )

            with self.assertRaises(ControlPlaneError) as raised:
                web_app._assert_formal_artifacts_ready(context)

            self.assertEqual(raised.exception.code, "GATE_BLOCKED")
            self.assertEqual(raised.exception.details["artifacts"][0]["reason"], "stale")

    def test_formal_gate_requires_artifact_manifest_after_v2_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.docx").write_bytes(b"docx")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.record_migration_scan(
                fingerprint="verified-v1-source",
                manifest=[],
                actor={"type": "user", "id": "admin", "role": "admin"},
            )
            store.activate_migration_cutover(
                fingerprint="verified-v1-source",
                actor={"type": "user", "id": "admin", "role": "admin"},
            )

            with self.assertRaises(ControlPlaneError) as raised:
                web_app._assert_formal_artifacts_ready(context)

            self.assertEqual(raised.exception.code, "GATE_BLOCKED")
            self.assertEqual(
                raised.exception.details["artifacts"][0]["reason"],
                "manifest_missing_after_cutover",
            )

    def test_formal_gate_fails_closed_without_docx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                response = asyncio.run(
                    web_app.api_v2_submit_command(
                        "alpha",
                        _Request(
                            {
                                "kind": "gate.revalidate",
                                "payload": {},
                                "expected_revision": 0,
                                "idempotency_key": "missing-final",
                            }
                        ),
                    )
                )
            payload = _body(response)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["receipt"]["error"]["code"], "GATE_BLOCKED")

    def test_formal_gate_blocks_unverified_qualification_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            outputs = root / "outputs"
            workspace.mkdir(parents=True)
            outputs.mkdir(parents=True)
            (outputs / "final.md").write_text("draft", encoding="utf-8")
            (outputs / "final.docx").write_bytes(b"docx")
            (workspace / "materials_checklist.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "item_id": "qualification-gap",
                                "category": "qualification",
                                "severity": "block",
                                "response_status": "deferred",
                                "lifecycle_status": "missing",
                                "evidence_status": "missing",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(
                    web_app,
                    "_v2_gate_can_proceed",
                    return_value={"can_proceed": True, "block_count": 0, "blocks": []},
                ):
                    response = asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "gate.revalidate",
                                    "payload": {},
                                    "expected_revision": 0,
                                    "idempotency_key": "qualification-gate",
                                }
                            ),
                        )
                    )
            payload = _body(response)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["receipt"]["error"]["code"], "GATE_BLOCKED")

    def test_issue_repair_and_risk_acceptance_require_persisted_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("agent.repair.execute_repair_batch") as execute:
                    repair = _body(
                        asyncio.run(
                            web_app.api_execute_repair(
                                "iss-1",
                                _Request({"confirm": True, "dry_run": False}),
                            )
                        )
                    )
                with mock.patch("agent.issues.accept_issue_risk") as accept:
                    risk = _body(
                        asyncio.run(
                            web_app.api_accept_issue_risk(
                                "iss-1",
                                _Request(
                                    {
                                        "reason": "这是一个充分记录的风险原因",
                                        "actor": "spoofed",
                                        "is_admin": True,
                                        "confirm_critical": True,
                                    }
                                ),
                            )
                        )
                    )
            self.assertEqual(repair["status"], "requires_confirmation")
            self.assertEqual(risk["status"], "requires_confirmation")
            execute.assert_not_called()
            accept.assert_not_called()

    def test_critical_risk_cannot_use_client_admin_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            issues = root / "workspace" / "issues"
            issues.mkdir(parents=True)
            (issues / "open.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "critical-1",
                            "code": "CRITICAL_CONFLICT",
                            "title": "critical conflict",
                            "severity": "block",
                            "status": "open",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.dict("os.environ", {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                    proposed = _body(
                        asyncio.run(
                            web_app.api_v2_submit_command(
                                "alpha",
                                _Request(
                                    {
                                        "kind": "issues.accept_risk",
                                        "payload": {
                                            "issue_id": "critical-1",
                                            "reason": "这是一个充分记录并经过讨论的风险原因",
                                            "is_admin": True,
                                            "confirm_critical": True,
                                        },
                                        "expected_revision": 0,
                                        "idempotency_key": "critical-risk",
                                    }
                                ),
                            )
                        )
                    )
                    rejected = _body(
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"], _Request({}))
                    )
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["receipt"]["error"]["code"], "POLICY_DENIED")

    def test_v2_action_cannot_be_confirmed_by_another_principal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            owner = {"type": "user", "id": "owner-1", "role": "user"}
            intruder = {"type": "user", "id": "editor-2", "role": "admin"}
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = _body(
                    asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "pipeline.cancel",
                                    "payload": {},
                                    "expected_revision": 0,
                                    "idempotency_key": "owner-cancel",
                                },
                                principal=owner,
                            ),
                        )
                    )
                )
                rejected = _body(
                    web_app.api_v2_confirm_action(
                        "alpha",
                        proposed["action"]["action_id"],
                        _Request({}, principal=intruder),
                    )
                )
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["error"]["code"], "CONFIRMATION_FORBIDDEN")
            pending = ControlStore(WorkspaceContext.resolve(runs, "alpha")).snapshot()["confirmations"]
            self.assertEqual(pending[0]["status"], "pending")

    def test_authenticated_admin_accepts_authoritative_critical_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            issues_dir = root / "workspace" / "issues"
            issues_dir.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            authoritative = {
                "id": "critical-admin",
                "code": "CRITICAL_CONFLICT",
                "title": "critical conflict",
                "detail": "authoritative sqlite detail",
                "severity": "block",
                "status": "open",
                "evidence": {"source": "quality-gate"},
            }
            ControlStore(context).replace_issue_states([authoritative], source="test")
            # A conflicting V1 projection must not lower the authoritative risk class.
            (issues_dir / "open.json").write_text(
                json.dumps(
                    [
                        {
                            **authoritative,
                            "code": "LOW_RISK",
                            "severity": "warn",
                            "risk_class": "minor",
                            "detail": "tampered projection",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            principal = {"type": "user", "id": "admin-1", "role": "admin"}
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.dict("os.environ", {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                    proposed = _body(
                        asyncio.run(
                            web_app.api_v2_submit_command(
                                "alpha",
                                _Request(
                                    {
                                        "kind": "issues.accept_risk",
                                        "payload": {
                                            "issue_id": "critical-admin",
                                            "reason": "管理员完成专项复核并记录充分接受理由",
                                            "is_admin": False,
                                            "confirm_critical": False,
                                        },
                                        "expected_revision": ControlStore(context).revision(),
                                        "idempotency_key": "critical-admin-risk",
                                    },
                                    principal=principal,
                                ),
                            )
                        )
                    )
                    accepted = _body(
                        web_app.api_v2_confirm_action(
                            "alpha",
                            proposed["action"]["action_id"],
                            _Request({}, principal=principal),
                        )
                    )
            self.assertTrue(accepted["ok"])
            state = ControlStore(context).issue_states()[0]
            self.assertEqual(state["status"], "accepted")
            self.assertEqual(state["risk_class"], "critical")
            self.assertEqual(state["detail"], "authoritative sqlite detail")
            decisions = ControlStore(context).policy_decisions(issue_id="critical-admin")
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["actor"]["id"], "admin-1")
            self.assertEqual(decisions[0]["actor"]["role"], "admin")
            self.assertTrue(decisions[0]["decision"]["confirmation_id"])

    def test_admin_still_cannot_accept_fatal_or_qualification_risk(self) -> None:
        for issue_id, code in (("fatal-1", "FATAL"), ("qualification-1", "QUALIFICATION_MISSING")):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                runs = Path(tmp) / "runs"
                root = runs / "alpha"
                root.mkdir(parents=True)
                context = WorkspaceContext.resolve(runs, "alpha")
                ControlStore(context).replace_issue_states(
                    [{"id": issue_id, "code": code, "severity": "block", "status": "open"}],
                    source="test",
                )
                with mock.patch.object(web_app, "RUNS_DIR", runs):
                    with mock.patch.dict("os.environ", {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                        proposed = _body(
                            asyncio.run(
                                web_app.api_v2_submit_command(
                                    "alpha",
                                    _Request(
                                        {
                                            "kind": "issues.accept_risk",
                                            "payload": {
                                                "issue_id": issue_id,
                                                "reason": "管理员复核后仍尝试接受该项风险",
                                            },
                                            "expected_revision": ControlStore(context).revision(),
                                            "idempotency_key": f"deny-{issue_id}",
                                        },
                                        principal={"type": "user", "id": "admin", "role": "admin"},
                                    ),
                                )
                            )
                        )
                        rejected = _body(
                            web_app.api_v2_confirm_action(
                                "alpha",
                                proposed["action"]["action_id"],
                                _Request({}, principal={"type": "user", "id": "admin", "role": "admin"}),
                            )
                        )
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["receipt"]["error"]["code"], "POLICY_DENIED")
                self.assertEqual(ControlStore(context).issue_states()[0]["status"], "open")
                self.assertEqual(ControlStore(context).policy_decisions(issue_id=issue_id), [])

    def test_quality_revalidation_runs_through_explicit_workspace_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch(
                    "agent.repair.revalidate_gate",
                    return_value={"ok": True, "message": "revalidated"},
                ) as revalidate:
                    response = asyncio.run(
                        web_app.api_v2_submit_command(
                            "alpha",
                            _Request(
                                {
                                    "kind": "quality.revalidate",
                                    "payload": {"command": "global-review"},
                                    "expected_revision": 0,
                                    "idempotency_key": "quality-revalidate",
                                }
                            ),
                        )
                    )
            payload = _body(response)
            self.assertTrue(payload["ok"], payload)
            self.assertTrue(web_app._same_path(revalidate.call_args.args[0], alpha))
            self.assertFalse((beta / "workspace" / "control.db").exists())

    def test_quality_revalidation_records_immutable_gate_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.replace_issue_states(
                [{"id": "issue-1", "code": "RULE_1", "stage_id": "global_review", "severity": "block", "status": "open"}],
                source="test",
            )
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "quality.revalidate",
                    "payload": {"command": "global-review"},
                    "expected_revision": store.revision(),
                    "actor": {"id": "admin", "role": "admin"},
                },
                workspace_id="alpha",
            )
            with mock.patch("agent.repair.revalidate_gate", return_value={"ok": True, "message": "revalidated"}):
                result = web_app._handle_quality_revalidate(context, envelope, "quality-op")

            evaluation = store.gate_evaluations(command="global-review")[0]
            self.assertEqual(result["gate_evaluation"]["evaluation_id"], evaluation["evaluation_id"])
            self.assertEqual(evaluation["verdict"], "block")
            self.assertEqual(evaluation["findings"][0]["issue_id"], "issue-1")

    def test_failed_quality_revalidation_records_fail_closed_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "quality.revalidate",
                    "payload": {"command": "global-review"},
                    "expected_revision": store.revision(),
                    "actor": {"id": "admin", "role": "admin"},
                },
                workspace_id="alpha",
            )
            with mock.patch("agent.repair.revalidate_gate", return_value={"ok": False, "message": "blocked"}):
                with self.assertRaises(ControlPlaneError) as blocked:
                    web_app._handle_quality_revalidate(context, envelope, "quality-op")

            self.assertEqual(blocked.exception.code, "GATE_BLOCKED")
            evaluation = store.gate_evaluations(command="global-review")[0]
            self.assertEqual(evaluation["verdict"], "block")
            self.assertEqual(evaluation["findings"], [])

    def test_debug_tool_api_rejects_mutation_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            response = asyncio.run(
                web_app.api_agent_tools_invoke(
                    _Request({"name": "build_export", "args": {}, "dry_run": False})
                )
            )
            payload = _body(response)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "POLICY_DENIED")

    def test_goal_resume_routes_through_gateway_and_bulk_confirmation_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("agent.goal.load_goal", return_value={"status": "blocked_human"}):
                    with mock.patch(
                        "agent.goal.resume_goal_after_materials",
                        return_value={"status": "in_progress"},
                    ) as resume:
                        response = asyncio.run(web_app.api_agent_goal_resume(_Request({"note": "continue"})))
                denied = asyncio.run(web_app.api_agent_goal_confirm(_Request({"all_mutations": True})))
            payload = _body(response)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["receipt"]["status"], "accepted")
            self.assertTrue(web_app._same_path(resume.call_args.args[0], root))
            denied_payload = _body(denied)
            self.assertEqual(denied.status_code, 410)
            self.assertEqual(denied_payload["error"]["code"], "POLICY_DENIED")

    def test_server_login_session_and_workspace_acl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app._AUTH_SESSIONS.clear()
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.dict("os.environ", {"BID_AGENT_AUTH_PASSWORD": ""}, clear=False):
                    unconfigured = asyncio.run(
                        web_app.api_auth_login(_Request({"username": "admin", "password": ""}))
                    )
                self.assertEqual(unconfigured.status_code, 503)
                with mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_AUTH_USER": "tester", "BID_AGENT_AUTH_PASSWORD": "secret-pass"},
                ):
                    denied = asyncio.run(
                        web_app.api_auth_login(_Request({"username": "tester", "password": "wrong"}))
                    )
                    accepted = asyncio.run(
                        web_app.api_auth_login(_Request({"username": "tester", "password": "secret-pass"}))
                    )
                self.assertEqual(denied.status_code, 401)
                cookie = SimpleCookie()
                cookie.load(accepted.headers["set-cookie"])
                token = cookie[web_app._AUTH_COOKIE].value
                principal = web_app._session_principal(token)
                self.assertEqual(principal["id"], "tester")
                self.assertTrue(_body(accepted)["csrf_token"])
                self.assertEqual(
                    web_app._session_record(token)["csrf_token"],
                    _body(accepted)["csrf_token"],
                )
                context = WorkspaceContext.resolve(runs, "alpha")
                web_app._ensure_workspace_acl(context, principal, write=True)
                self.assertEqual(ControlStore(context).workspace_acl()[0]["principal_id"], "tester")
                with self.assertRaises(Exception) as forbidden:
                    web_app._ensure_workspace_acl(
                        context,
                        {"id": "intruder", "role": "admin"},
                        write=False,
                    )
                self.assertEqual(getattr(forbidden.exception, "code", ""), "WORKSPACE_FORBIDDEN")

    def test_auth_middleware_enforces_session_and_workspace_acl(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                runs = Path(tmp) / "runs"
                root = runs / "alpha"
                root.mkdir(parents=True)
                context = WorkspaceContext.resolve(runs, "alpha")
                ControlStore(context).grant_workspace_access("owner-only", role="owner")
                beta_root = runs / "beta"
                beta_root.mkdir(parents=True)
                beta_context = WorkspaceContext.resolve(runs, "beta")
                ControlStore(beta_context).grant_workspace_access("beta-owner", role="owner")
                web_app.ACTIVE_RUN_ID = "alpha"
                web_app.ACTIVE_RUN_ROOT = root
                web_app._AUTH_SESSIONS.clear()
                transport = httpx.ASGITransport(app=web_app.app)
                with mock.patch.object(web_app, "RUNS_DIR", runs):
                    with mock.patch.dict(
                        "os.environ",
                        {"BID_AGENT_AUTH_USER": "tester", "BID_AGENT_AUTH_PASSWORD": "secret-pass"},
                    ):
                        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                            anonymous = await client.get("/api/runs")
                            self.assertEqual(anonymous.status_code, 401)
                            login = await client.post(
                                "/api/auth/login",
                                json={"username": "tester", "password": "secret-pass"},
                            )
                            self.assertEqual(login.status_code, 200)
                            csrf_token = login.json()["csrf_token"]
                            csrf_denied = await client.post("/api/select-run", json={"run_id": "alpha"})
                            self.assertEqual(csrf_denied.status_code, 403)
                            self.assertEqual(csrf_denied.json()["error"]["code"], "CSRF_REQUIRED")
                            csrf_headers = {"X-CSRF-Token": csrf_token}
                            runs_response = await client.get("/api/runs")
                            self.assertEqual(runs_response.status_code, 200)
                            self.assertEqual(runs_response.headers["deprecation"], "true")
                            self.assertIn("successor-version", runs_response.headers["link"])
                            forbidden = await client.get("/api/v2/workspaces/alpha/snapshot")
                            self.assertEqual(forbidden.status_code, 403)
                            self.assertNotIn("deprecation", forbidden.headers)
                            self.assertEqual(forbidden.json()["error"]["code"], "WORKSPACE_FORBIDDEN")
                            select_forbidden = await client.post(
                                "/api/select-run", json={"run_id": "alpha"}, headers=csrf_headers
                            )
                            self.assertEqual(select_forbidden.status_code, 403)
                            delete_forbidden = await client.post(
                                "/api/delete-run", json={"run_id": "alpha"}, headers=csrf_headers
                            )
                            self.assertEqual(delete_forbidden.status_code, 403)
                            ControlStore(context).grant_workspace_access("tester", role="editor")
                            query_allowed = await client.get(
                                "/api/materials-checklist",
                                params={"workspace_id": "alpha"},
                            )
                            self.assertEqual(query_allowed.status_code, 200)
                            self.assertEqual(
                                ControlStore(context).compatibility_usage()["routes"]["/api/materials-checklist"]["calls"],
                                1,
                            )
                            query_forbidden = await client.get(
                                "/api/materials-checklist",
                                params={"workspace_id": "beta"},
                            )
                            self.assertEqual(query_forbidden.status_code, 403)
                            self.assertEqual(query_forbidden.json()["error"]["code"], "WORKSPACE_FORBIDDEN")
                            self.assertTrue(root.exists())

        asyncio.run(scenario())

    def test_manual_review_update_requires_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            request = _Request(
                {
                    "category": "chapter_review",
                    "payload": {
                        "item_id": "chapter-1",
                        "status": "accepted",
                        "operator_instruction": "采用人工结论",
                    },
                }
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "apply_manual_review_update", wraps=web_app.apply_manual_review_update) as apply:
                    proposed = asyncio.run(web_app.api_manual_review_update(request))
                    proposal = _body(proposed)
                    self.assertEqual(proposed.status_code, 202)
                    self.assertEqual(proposal["action"]["type"], "confirm_v2_command")
                    apply.assert_not_called()

                    receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                        proposal["action"]["confirmation_id"]
                    )

                self.assertEqual(receipt.status, "accepted")
                self.assertEqual(
                    web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).store.operation(
                        receipt.operation_id or ""
                    )["status"],
                    "succeeded",
                )
                override = root / "workspace" / "manual_review" / "chapter_actions.json"
                self.assertTrue(override.exists())
                self.assertEqual(json.loads(override.read_text(encoding="utf-8"))["items"]["chapter-1"]["status"], "accepted")
                decisions = ControlStore(WorkspaceContext.resolve(runs, "alpha")).policy_decisions(
                    issue_id="manual-review:chapter_review:chapter-1"
                )
                self.assertEqual(len(decisions), 1)
                self.assertEqual(decisions[0]["decision_type"], "manual_review")
                self.assertEqual(decisions[0]["decision"]["status"], "accepted")
                self.assertEqual(decisions[0]["actor"]["id"], "anonymous")

    def test_v2_manual_review_read_uses_latest_sqlite_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.record_policy_decision(
                issue_id="manual-review:score_coverage:score-1",
                decision_type="manual_review",
                decision={"payload": {"item_id": "score-1", "status": "accepted"}},
                actor={"type": "user", "id": "reviewer"},
            )
            compatibility = [
                {
                    "item_id": "score-1",
                    "override": {"status": "pending", "operator_instruction": "legacy"},
                }
            ]
            with mock.patch.object(web_app, "manual_review_items", return_value=compatibility):
                closed = web_app._v2_manual_review_items(context, "score_coverage")
                all_items = web_app._v2_manual_review_items(context, "score_coverage", include_closed=True)

            self.assertEqual(closed, [])
            self.assertEqual(all_items[0]["override"]["status"], "accepted")
            self.assertEqual(all_items[0]["control_source"], "control.db")

    def test_final_md_edit_requires_confirmation_and_rebuilds_in_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "outputs").mkdir(parents=True)
            final_md = root / "outputs" / "final.md"
            final_md.write_text("# 标题\n原内容\n", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = asyncio.run(
                    web_app.api_final_md_line_edit(
                        _Request({"line_number": 2, "new_text": "新内容", "instruction": "人工修订"})
                    )
                )
                proposal = _body(proposed)
                self.assertEqual(proposed.status_code, 202)
                self.assertEqual(final_md.read_text(encoding="utf-8"), "# 标题\n原内容\n")

                def rebuild_docx(command, run_id, run_root):
                    (run_root / "outputs" / "final.docx").write_bytes(b"rebuilt-docx")
                    return 0

                with mock.patch.object(web_app, "_run_sync", side_effect=rebuild_docx) as rebuild:
                    receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                        proposal["action"]["confirmation_id"]
                    )

                self.assertEqual(receipt.status, "accepted")
                self.assertEqual(final_md.read_text(encoding="utf-8"), "# 标题\n新内容\n")
                rebuild.assert_called_once_with("build-docx", "alpha", root.resolve())

    def test_final_md_edit_rejects_stale_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "outputs").mkdir(parents=True)
            final_md = root / "outputs" / "final.md"
            final_md.write_text("旧内容\n", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = asyncio.run(
                    web_app.api_final_md_line_edit(_Request({"line_number": 1, "new_text": "提议内容"}))
                )
                proposal = _body(proposed)
                final_md.write_text("并发修改\n", encoding="utf-8")
                with mock.patch.object(web_app, "_run_sync") as rebuild:
                    receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                        proposal["action"]["confirmation_id"]
                    )

                self.assertEqual(receipt.status, "rejected")
                self.assertEqual(receipt.error["code"], "REVISION_CONFLICT")
                self.assertEqual(final_md.read_text(encoding="utf-8"), "并发修改\n")
                rebuild.assert_not_called()

    def test_final_md_edit_rolls_back_when_docx_rebuild_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "outputs").mkdir(parents=True)
            final_md = root / "outputs" / "final.md"
            final_md.write_text("原内容\n", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = asyncio.run(
                    web_app.api_final_md_line_edit(_Request({"line_number": 1, "new_text": "未完成内容"}))
                )
                proposal = _body(proposed)
                with mock.patch.object(web_app, "_run_sync", return_value=1):
                    receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                        proposal["action"]["confirmation_id"]
                    )

                self.assertEqual(receipt.status, "rejected")
                self.assertEqual(receipt.error["code"], "COMMAND_DISPATCH_FAILED")
                self.assertEqual(final_md.read_text(encoding="utf-8"), "原内容\n")

    def test_project_profile_change_requires_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).upsert_artifact_state(
                {
                    "artifact_key": "workspace/outline.json",
                    "path": "workspace/outline.json",
                    "kind": "file",
                    "status": "ready",
                    "producer": "generate-outline",
                    "sha256": "old",
                    "input_fingerprint": "old",
                }
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = asyncio.run(
                    web_app.api_set_project_profile(_Request({"project_type": "software_project"}))
                )
                proposal = _body(proposed)
                self.assertEqual(proposed.status_code, 202)
                self.assertFalse((root / "workspace" / "project_profile.json").exists())

                receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                    proposal["action"]["confirmation_id"]
                )

                self.assertEqual(receipt.status, "accepted")
                profile = json.loads((root / "workspace" / "project_profile.json").read_text(encoding="utf-8"))
                self.assertEqual(profile["project_type"], "software_project")
                self.assertEqual(
                    ControlStore(context).artifact_state("workspace/outline.json")["status"],
                    "stale",
                )

    def test_workspace_utility_command_requires_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_run_sync", return_value=0) as runner:
                    proposed = asyncio.run(
                        web_app.api_run_command(_Request({"run_id": "alpha", "command": "validate"}))
                    )
                    proposal = _body(proposed)
                    self.assertEqual(proposed.status_code, 202)
                    self.assertEqual(proposal["status"], "requires_confirmation")
                    runner.assert_not_called()

                    receipt = web_app._command_gateway(WorkspaceContext.resolve(runs, "alpha")).confirm(
                        proposal["action"]["confirmation_id"]
                    )

                self.assertEqual(receipt.status, "accepted")
                runner.assert_called_once_with("validate", "alpha", root.resolve())

    def test_workspace_delete_archives_only_after_v2_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).grant_workspace_access("owner", role="owner")
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root
            request = _Request({"run_id": "alpha"})
            request.state = type("State", (), {"principal": {"id": "owner", "role": "user"}})()

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "ACTIVE_RUN_FILE", runs / ".active_run"):
                    proposed = asyncio.run(web_app.api_delete_run(request))
                    proposal = _body(proposed)
                    self.assertEqual(proposed.status_code, 202)
                    self.assertTrue(root.exists())

                    receipt = web_app._command_gateway(context).confirm(proposal["action"]["confirmation_id"])

                self.assertEqual(receipt.status, "accepted")
                self.assertFalse(root.exists())
                archived = list((runs / ".trash").glob("alpha_*"))
                self.assertEqual(len(archived), 1)
                self.assertTrue((archived[0] / "workspace" / "control.db").exists())

    def test_workspace_clean_preserves_control_db_and_archives_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "workspace").mkdir(parents=True)
            (root / "outputs").mkdir(parents=True)
            (root / "workspace" / "legacy.json").write_text("{}", encoding="utf-8")
            (root / "outputs" / "final.md").write_text("draft", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context)
            web_app.ACTIVE_RUN_ID = "alpha"
            web_app.ACTIVE_RUN_ROOT = root

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                proposed = web_app.api_clean_workspace(_Request({}))
                proposal = _body(proposed)
                self.assertEqual(proposed.status_code, 202)
                self.assertTrue((root / "outputs" / "final.md").exists())

                receipt = web_app._command_gateway(context).confirm(proposal["action"]["confirmation_id"])

                self.assertEqual(receipt.status, "accepted")
                self.assertTrue((root / "workspace" / "control.db").exists())
                self.assertFalse((root / "workspace" / "legacy.json").exists())
                self.assertFalse((root / "outputs" / "final.md").exists())
                archived = list((root / ".trash").glob("clean_*"))
                self.assertEqual(len(archived), 1)
                self.assertTrue((archived[0] / "workspace" / "legacy.json").exists())
                self.assertTrue((archived[0] / "outputs" / "final.md").exists())

    def test_restart_reconcile_blocks_mismatched_pipeline_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {"pipeline.start": lambda ctx, envelope, operation_id: {"accepted": True, "operation_status": "running"}},
            )
            receipt = gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "pipeline.start",
                        "payload": {},
                        "expected_revision": gateway.store.revision(),
                        "idempotency_key": "restart-mismatch",
                    },
                    workspace_id="alpha",
                )
            )
            checkpoint = context.root / "workspace" / "pipeline_control.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "operation_id": "wrong-operation",
                        "fencing_token": 99,
                        "current_stage": "build-md",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app.SUPERVISOR, "reconcile") as reconcile:
                    resumed = web_app._reconcile_pipeline_from_control(context)

            self.assertFalse(resumed)
            reconcile.assert_not_called()
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            self.assertEqual(operation["status"], "blocked")
            self.assertEqual(operation["error"]["code"], "STATE_CONFLICT")

    def test_restart_reconcile_uses_matching_v2_operation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {"pipeline.start": lambda ctx, envelope, operation_id: {"accepted": True, "operation_status": "running"}},
            )
            receipt = gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "pipeline.start",
                        "payload": {},
                        "expected_revision": gateway.store.revision(),
                        "idempotency_key": "restart-match",
                    },
                    workspace_id="alpha",
                )
            )
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            checkpoint = context.root / "workspace" / "pipeline_control.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "operation_id": receipt.operation_id,
                        "fencing_token": operation["fencing_token"],
                        "current_stage": "build-md",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app.SUPERVISOR, "reconcile", return_value=True) as reconcile:
                    resumed = web_app._reconcile_pipeline_from_control(context)

            self.assertTrue(resumed)
            self.assertEqual(reconcile.call_args.args, ("alpha", context.root, web_app._run_sync))
            evaluator = reconcile.call_args.kwargs.get("gate_evaluator")
            self.assertTrue(callable(evaluator))

    def test_v2_snapshot_uses_sqlite_authority_for_control_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.upsert_goal_state({"goal_id": "goal-v2", "status": "running", "plan": [{"id": "p1"}]})
            store.upsert_agent_activity_state({"status": "running", "phase": "writing", "agents": [{"id": "a1"}]})
            store.upsert_repair_job_state({"job_id": "repair-v2", "status": "partial", "phase": "complete"})
            store.upsert_material_state(
                {
                    "item_id": "material-v2",
                    "response_status": "ready",
                    "lifecycle_status": "verified",
                    "evidence_status": "verified",
                }
            )
            store.replace_issue_states(
                [{"id": "issue-v2", "status": "open", "severity": "block"}],
                source="test",
            )
            store.upsert_artifact_state(
                {
                    "artifact_key": "outputs/final.docx",
                    "path": "outputs/final.docx",
                    "kind": "file",
                    "status": "ready",
                    "producer": "build-docx",
                    "sha256": "abc",
                    "input_fingerprint": "inputs",
                }
            )
            compatibility = {
                "goal": {"goal_id": "goal-v1", "status": "failed", "summary": "compat summary"},
                "goal_full": {"goal_id": "goal-v1", "status": "failed"},
                "agent_activity": {"status": "idle"},
                "repair_job": {"job_id": "repair-v1", "status": "completed"},
                "materials_summary": {"total": 99, "ready": 0},
                "issues_summary": {"total": 99, "open": 0},
                "pipeline": {},
                "workflow": [{"command": "build-docx", "done": False, "state": "blocked"}],
                "outputs": {"final_docx": True},
            }

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_status_payload", return_value=compatibility) as status_payload:
                    payload = _body(web_app.api_v2_workspace_snapshot("alpha"))

            snapshot = payload["snapshot"]
            self.assertEqual(snapshot["goal"]["goal_id"], "goal-v2")
            self.assertEqual(snapshot["goal"]["summary"], "compat summary")
            self.assertEqual(snapshot["activity"]["phase"], "writing")
            self.assertEqual(snapshot["repair_job"]["job_id"], "repair-v2")
            self.assertEqual(snapshot["materials"]["ready"], 1)
            self.assertEqual(snapshot["materials"]["source"], "control.db")
            self.assertEqual(snapshot["findings"]["issues_summary"]["block_count"], 1)
            self.assertFalse(snapshot["findings"]["issues_summary"]["can_proceed"])
            self.assertEqual(snapshot["findings"]["issues_summary"]["source"], "control.db")
            self.assertEqual(snapshot["artifacts"][0]["artifact_key"], "outputs/final.docx")
            self.assertEqual(snapshot["artifact_files"]["outputs"]["final_docx"], True)
            self.assertTrue(snapshot["presentation"]["workflow"][0]["done"])
            self.assertEqual(snapshot["presentation"]["workflow"][0]["artifact_source"], "control.db")
            status_payload.assert_called_once_with(
                context.root,
                "alpha",
                persist_manual_review_summary=False,
            )

            store.mark_artifact_states_stale(
                ["outputs/final.docx"],
                reason="upstream changed",
                source_command="build-md",
            )
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_status_payload", return_value=compatibility):
                    stale_payload = _body(web_app.api_v2_workspace_snapshot("alpha"))
            stale_step = stale_payload["snapshot"]["presentation"]["workflow"][0]
            self.assertFalse(stale_step["done"])
            self.assertEqual(stale_step["state"], "ready")
            self.assertIn("已过期", stale_step["message"])

    def test_v2_snapshot_does_not_write_manual_review_summary_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            root.mkdir(parents=True)
            summary_path = root / "workspace" / "manual_review" / "summary.json"
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(web_app.api_v2_workspace_snapshot("alpha"))
            self.assertTrue(payload["ok"])
            self.assertFalse(summary_path.exists())
            self.assertEqual(payload["snapshot"]["manual_review_summary"]["source"], "control.db")

    def test_pipeline_snapshot_rejects_stale_checkpoint_status(self) -> None:
        operation = {
            "operation_id": "op-current",
            "kind": "pipeline.start",
            "status": "failed",
            "start_command": "build-docx",
            "fencing_token": 4,
            "message": "gate failed",
            "error": {"code": "GATE_BLOCKED"},
        }
        checkpoint = {
            "operation_id": "op-old",
            "status": "running",
            "current_stage": "write-all",
            "worker_pid": 999,
            "fencing_token": 3,
        }

        pipeline = web_app._pipeline_snapshot_from_control([operation], checkpoint)

        self.assertEqual(pipeline["status"], "failed")
        self.assertEqual(pipeline["operation_id"], "op-current")
        self.assertEqual(pipeline["current_stage"], "build-docx")
        self.assertEqual(pipeline["worker_pid"], 0)
        self.assertFalse(pipeline["consistent"])
        self.assertEqual(pipeline["checkpoint_source"], "ignored_mismatch")

    def test_inactive_workspace_reconcile_blocks_orphaned_pipeline_and_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            gateway = CommandGateway(
                context,
                {
                    "pipeline.start": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "running",
                    }
                },
            )
            receipt = gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "kind": "pipeline.start",
                        "payload": {"start_command": "build-md"},
                        "expected_revision": 0,
                        "idempotency_key": "orphaned-pipeline",
                    },
                    workspace_id="alpha",
                )
            )
            gateway.store.upsert_goal_state({"goal_id": "goal-1", "status": "in_progress"})

            result = web_app._reconcile_inactive_workspace(context)

            operation = gateway.store.operation(receipt.operation_id or "") or {}
            goal = gateway.store.goal_state() or {}
            self.assertTrue(result["changed"])
            self.assertEqual(operation["status"], "blocked")
            self.assertEqual(operation["error"]["code"], "ORPHANED_AFTER_RESTART")
            self.assertEqual(goal["status"], "blocked_human")
            self.assertEqual(goal["orphaned_operation_id"], receipt.operation_id)

    def test_workspace_event_stream_uses_stable_type_and_last_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.ensure_material_states([])
            store.upsert_goal_state({"goal_id": "goal-events", "status": "running"})

            async def first_chunk() -> str:
                with mock.patch.object(web_app, "RUNS_DIR", runs):
                    response = await web_app.api_v2_workspace_events(
                        "alpha",
                        _EventRequest("1"),
                        after_seq=0,
                        limit=20,
                    )
                    chunk = await anext(response.body_iterator)
                    await response.body_iterator.aclose()
                    return chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)

            chunk = asyncio.run(first_chunk())
            self.assertIn("id: 2\n", chunk)
            self.assertIn("event: WorkspaceEvent\n", chunk)
            self.assertIn('"kind": "GoalStateChanged"', chunk)

    def test_v2_document_render_uses_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            (alpha / "outputs").mkdir(parents=True)
            (beta / "outputs").mkdir(parents=True)
            (alpha / "outputs" / "final.md").write_text("# Alpha document", encoding="utf-8")
            (beta / "outputs" / "final.md").write_text("# Beta document", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(web_app.api_final_doc_render("alpha"))

            self.assertTrue(payload["final_md_exists"])
            self.assertEqual(payload["blocks"][0]["text"], "Alpha document")
            self.assertEqual(
                payload["base_sha256"],
                hashlib.sha256((alpha / "outputs" / "final.md").read_bytes()).hexdigest(),
            )

    def test_v2_document_proposal_busy_state_is_workspace_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            (alpha / "outputs").mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "outputs" / "final.md").write_text("# Alpha", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            web_app.RUNNING = True

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("llm_client.chat", return_value="# Alpha revised") as chat:
                    allowed = _body(
                        asyncio.run(
                            web_app.api_final_doc_chat_edit(
                                _Request({"instruction": "revise"}),
                                "alpha",
                            )
                        )
                    )

                context = WorkspaceContext.resolve(runs, "alpha")
                gateway = CommandGateway(
                    context,
                    {
                        "pipeline.start": lambda ctx, envelope, operation_id: {
                            "accepted": True,
                            "operation_status": "running",
                        }
                    },
                )
                gateway.submit(
                    CommandEnvelope.from_mapping(
                        {
                            "kind": "pipeline.start",
                            "expected_revision": gateway.store.revision(),
                            "idempotency_key": "document-busy",
                        },
                        workspace_id="alpha",
                    )
                )
                web_app.RUNNING = False
                with mock.patch("llm_client.chat") as blocked_chat:
                    blocked_response = asyncio.run(
                        web_app.api_final_doc_chat_edit(_Request({"instruction": "revise again"}), "alpha")
                    )
                    blocked = _body(blocked_response)

            web_app._PENDING_DOC_EDIT.pop(alpha.resolve(), None)
            self.assertTrue(allowed["ok"])
            chat.assert_called_once()
            self.assertEqual(blocked_response.status_code, 409)
            self.assertFalse(blocked["ok"])
            blocked_chat.assert_not_called()

    def test_v2_file_preview_uses_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            (alpha / "outputs").mkdir(parents=True)
            (beta / "outputs").mkdir(parents=True)
            (alpha / "outputs" / "report.txt").write_text("alpha report", encoding="utf-8")
            (beta / "outputs" / "report.txt").write_text("beta report", encoding="utf-8")
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(web_app.api_file_preview("outputs/report.txt", "alpha"))

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["content"], "alpha report")

    def test_v2_quality_reads_use_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            (alpha / "workspace").mkdir(parents=True)
            (beta / "workspace").mkdir(parents=True)
            (alpha / "workspace" / "compliance_report.json").write_text(
                json.dumps({"blocking": True, "items": [{"check_id": "alpha-check", "status": "fail"}]}),
                encoding="utf-8",
            )
            (beta / "workspace" / "compliance_report.json").write_text(
                json.dumps({"blocking": False, "items": [{"check_id": "beta-check", "status": "ok"}]}),
                encoding="utf-8",
            )
            ControlStore(WorkspaceContext.resolve(runs, "alpha")).replace_issue_states(
                [{"id": "alpha-issue", "status": "open", "severity": "block"}],
                source="test",
            )
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                compliance = _body(web_app.api_compliance_report("alpha"))
                with mock.patch.object(web_app, "_v2_export_preflight", return_value={"ok": True}) as preflight:
                    _body(web_app.api_export_preflight("alpha"))
                with mock.patch.object(web_app, "_material_items", return_value=[]):
                    materials = _body(web_app.api_materials_checklist("alpha"))
                with mock.patch("agent.issues.load_open_issues") as load_issues:
                    with mock.patch("agent.root_cause.sync_issues_from_compliance") as sync_compliance:
                        with mock.patch("agent.root_cause.sync_issues_from_global_review") as sync_review:
                            issues = _body(web_app.api_list_issues("open", "alpha"))

            self.assertTrue(compliance["blocking"])
            self.assertEqual(compliance["items"][0]["check_id"], "alpha-check")
            preflight.assert_called_once()
            self.assertEqual(preflight.call_args.args[0].root, alpha.resolve())
            self.assertTrue(materials["ok"])
            self.assertEqual(materials["items"], [])
            load_issues.assert_not_called()
            sync_compliance.assert_not_called()
            sync_review.assert_not_called()
            self.assertEqual(issues["issues"][0]["id"], "alpha-issue")
            self.assertEqual(issues["summary"]["source"], "control.db")

    def test_v2_export_preflight_is_read_only_and_sqlite_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            outputs = root / "outputs"
            issues_dir = workspace / "issues"
            issues_dir.mkdir(parents=True)
            outputs.mkdir(parents=True)
            (workspace / "global_review.json").write_text(
                json.dumps({"blocking": False}), encoding="utf-8"
            )
            (workspace / "compliance_report.json").write_text(
                json.dumps({"blocking": False}), encoding="utf-8"
            )
            (outputs / "final.md").write_text("formal draft", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).replace_issue_states(
                [
                    {
                        "id": "accepted-1",
                        "code": "CRITICAL_CONFLICT",
                        "severity": "block",
                        "status": "accepted",
                        "accept_reason": "approved by policy",
                        "accepted_by": "admin",
                    }
                ],
                source="test",
            )
            tampered_projection = [
                {
                    "id": "fatal-from-file",
                    "code": "FATAL",
                    "severity": "fatal",
                    "status": "open",
                }
            ]
            (issues_dir / "open.json").write_text(
                json.dumps(tampered_projection), encoding="utf-8"
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("agent.root_cause.sync_issues_from_compliance") as sync_compliance:
                    with mock.patch("agent.root_cause.sync_issues_from_global_review") as sync_review:
                        with mock.patch("agent.issues.write_risk_register") as write_register:
                            preflight = _body(web_app.api_export_preflight("alpha"))

            self.assertTrue(preflight["can_export"])
            self.assertFalse(preflight["all_passed"])
            self.assertEqual(preflight["source"], "control.db")
            self.assertEqual(preflight["accepted_risks"][0]["id"], "accepted-1")
            self.assertEqual(preflight["accepted_risks"][0]["risk_class"], "critical")
            self.assertEqual(ControlStore(context).issue_states()[0]["status"], "accepted")
            self.assertEqual(
                json.loads((issues_dir / "open.json").read_text(encoding="utf-8")),
                tampered_projection,
            )
            sync_compliance.assert_not_called()
            sync_review.assert_not_called()
            write_register.assert_not_called()

    def test_v2_materials_checklist_exposes_submission_and_verification_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "materials_checklist.json").write_text(
                json.dumps({"items": [{"item_id": "license", "requirement": "营业执照", "response_status": "submitted"}]}),
                encoding="utf-8",
            )
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.ensure_material_states([{"item_id": "license", "requirement": "营业执照", "response_status": "submitted"}])
            staged = store.register_material_upload(
                staged_path="workspace/material_uploads/license.pdf",
                filename="license.pdf",
                sha256="b" * 64,
                size_bytes=99,
            )
            store.record_material_submission(
                item_id="license",
                upload=store.consume_material_upload(staged["upload_token"]),
                actor={"id": "owner"},
                source="test",
            )
            store.record_material_verification(
                item_id="license",
                verification_type="human",
                verdict="verified",
                verification={"reason": "checked"},
                actor={"id": "reviewer"},
                source="test",
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(web_app.api_materials_checklist("alpha"))

            item = payload["items"][0]
            self.assertEqual(item["submission_count"], 1)
            self.assertEqual(item["latest_submission"]["filename"], "license.pdf")
            self.assertEqual(item["latest_verification"]["verdict"], "verified")

    def test_v2_export_preflight_fails_closed_on_malformed_quality_reports(self) -> None:
        for filename, content in (
            ("global_review.json", "{broken"),
            ("compliance_report.json", json.dumps({"summary": {}})),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                runs = Path(tmp) / "runs"
                root = runs / "alpha"
                workspace = root / "workspace"
                outputs = root / "outputs"
                workspace.mkdir(parents=True)
                outputs.mkdir(parents=True)
                (workspace / "global_review.json").write_text(
                    json.dumps({"blocking": False}), encoding="utf-8"
                )
                (workspace / "compliance_report.json").write_text(
                    json.dumps({"blocking": False}), encoding="utf-8"
                )
                (workspace / filename).write_text(content, encoding="utf-8")
                (outputs / "final.md").write_text("formal draft", encoding="utf-8")
                with mock.patch.object(web_app, "RUNS_DIR", runs):
                    response = web_app.api_export_preflight("alpha")
                payload = _body(response)
                self.assertEqual(response.status_code, 503)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "STATE_UNAVAILABLE")

    def test_v2_export_preflight_rejects_latest_failed_gate_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.record_gate_evaluation(
                command="global-review",
                verdict="error",
                input_fingerprint="fingerprint",
                findings=[],
                source="test",
            )

            with mock.patch.object(web_app, "_ensure_v2_issue_import", return_value=store):
                with self.assertRaises(ControlPlaneError) as blocked:
                    web_app._v2_export_preflight(context)

            self.assertEqual(blocked.exception.code, "STATE_UNAVAILABLE")
            store.record_gate_evaluation(
                command="global-review",
                verdict="pass",
                input_fingerprint="new-fingerprint",
                findings=[],
                source="test",
            )
            workspace = context.root / "workspace"
            outputs = context.root / "outputs"
            workspace.mkdir(parents=True, exist_ok=True)
            outputs.mkdir(parents=True)
            (workspace / "global_review.json").write_text(json.dumps({"blocking": False}), encoding="utf-8")
            (workspace / "compliance_report.json").write_text(json.dumps({"blocking": False}), encoding="utf-8")
            (outputs / "final.md").write_text("draft", encoding="utf-8")
            with mock.patch.object(web_app, "_ensure_v2_issue_import", return_value=store):
                preflight = web_app._v2_export_preflight(context)
            self.assertTrue(preflight["can_export"])

    def test_v2_export_preflight_rejects_stale_migration_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            goal_path = context.root / "workspace" / "agent" / "goal_state.json"
            goal_path.parent.mkdir(parents=True)
            legacy_goal = {
                "goal_id": "legacy-goal",
                "status": "running",
                "objective": "legacy objective",
            }
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")
            store = ControlStore(context)
            store.ensure_goal_state(legacy_goal)
            preview = web_app._v1_migration_dry_run(context)
            self.assertEqual(preview["status"], "ready")
            store.record_migration_scan(
                fingerprint=preview["source_fingerprint"],
                manifest=preview["source_manifest"],
                actor={"id": "admin", "role": "admin"},
            )
            store.activate_migration_cutover(
                fingerprint=preview["source_fingerprint"],
                actor={"id": "admin", "role": "admin"},
            )
            legacy_goal["objective"] = "legacy source changed after cutover"
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")

            with mock.patch.object(web_app, "_ensure_v2_issue_import", return_value=store):
                with self.assertRaises(ControlPlaneError) as blocked:
                    web_app._v2_export_preflight(context)

            self.assertEqual(blocked.exception.code, "MIGRATION_CUTOVER_STALE")

    def test_formal_gate_fingerprint_tracks_active_cutover_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            goal_path = context.root / "workspace" / "agent" / "goal_state.json"
            goal_path.parent.mkdir(parents=True)
            legacy_goal = {"goal_id": "legacy-goal", "status": "running"}
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")
            store = ControlStore(context)
            store.ensure_goal_state(legacy_goal)
            preview = web_app._v1_migration_dry_run(context)
            store.record_migration_scan(
                fingerprint=preview["source_fingerprint"],
                manifest=preview["source_manifest"],
                actor={"id": "admin", "role": "admin"},
            )
            store.activate_migration_cutover(
                fingerprint=preview["source_fingerprint"],
                actor={"id": "admin", "role": "admin"},
            )
            before, _ = web_app._formal_gate_fingerprint(context)
            legacy_goal["objective"] = "changed after receipt issuance"
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")
            after, _ = web_app._formal_gate_fingerprint(context)

            self.assertNotEqual(before, after)

    def test_formal_gate_fingerprint_tracks_latest_gate_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            before, _ = web_app._formal_gate_fingerprint(context)
            store.record_gate_evaluation(
                command="global-review",
                verdict="pass",
                input_fingerprint="quality-v1",
                findings=[],
                source="test",
            )
            after, _ = web_app._formal_gate_fingerprint(context)

            self.assertNotEqual(before, after)

    def test_migration_snapshot_marks_active_cutover_stale_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            goal_path = context.root / "workspace" / "agent" / "goal_state.json"
            goal_path.parent.mkdir(parents=True)
            legacy_goal = {"goal_id": "legacy-goal", "status": "running"}
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")
            store = ControlStore(context)
            store.ensure_goal_state(legacy_goal)
            preview = web_app._v1_migration_dry_run(context)
            store.record_migration_scan(
                fingerprint=preview["source_fingerprint"],
                manifest=preview["source_manifest"],
                actor={"id": "admin", "role": "admin"},
            )
            store.activate_migration_cutover(
                fingerprint=preview["source_fingerprint"],
                actor={"id": "admin", "role": "admin"},
            )
            legacy_goal["objective"] = "changed after cutover"
            goal_path.write_text(json.dumps(legacy_goal), encoding="utf-8")

            snapshot = web_app._migration_snapshot_with_source_state(context, store.snapshot())

            self.assertEqual(snapshot["migration"]["status"], "cutover_stale")
            self.assertEqual(snapshot["migration"]["cutover"]["status"], "stale")
            self.assertTrue(snapshot["migration"]["cutover"]["source_stale"])

    def test_v2_step_detail_proposals_use_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            ControlStore(WorkspaceContext.resolve(runs, "alpha")).replace_issue_states(
                [{"id": "issue-1", "status": "open", "severity": "block", "code": "TEST_ISSUE"}],
                source="test",
            )
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            command = str(web_app.WORKFLOW_STEPS[0]["command"])

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_status_payload", return_value={"workflow": [], "timings": {}}) as status:
                    with mock.patch.object(web_app, "_v2_manual_review_summary", return_value={"alpha": True}) as detail_summary:
                        detail = _body(web_app.api_workflow_step_detail(command, "alpha"))
                with mock.patch.object(web_app, "_v2_manual_review_summary", return_value={"alpha": True}) as review_summary:
                    summary = _body(web_app.api_manual_review_summary("alpha"))
                with mock.patch.object(web_app, "_v2_manual_review_items", return_value=[{"id": "alpha-review"}]) as review_items:
                    items = _body(web_app.api_manual_review_items("score_coverage", "alpha"))
                with mock.patch("agent.repair.build_repair_plan", return_value={"ok": True}) as build_plan:
                    preview = _body(web_app.api_preview_repair("issue-1", "alpha"))
                with mock.patch("agent.issues.load_open_issues") as legacy_issues:
                    with mock.patch("agent.root_cause.refine_issue_cause_with_llm", return_value={"ok": True}) as explain:
                        explained = _body(asyncio.run(web_app.api_explain_issue_cause("issue-1", _Request({}), "alpha")))
                with mock.patch("agent.repair.execute_repair_batch", return_value={"ok": True}) as batch:
                    batch_result = _body(asyncio.run(web_app.api_batch_preview_repair(_Request({"issue_ids": ["issue-1"]}), "alpha")))

            resolved = alpha.resolve()
            status.assert_called_once_with(resolved, "alpha", persist_manual_review_summary=False)
            detail_summary.assert_called_once()
            self.assertEqual(detail["manual_review_summary"], {"alpha": True})
            self.assertEqual(Path(detail["run_root"]), resolved)
            review_summary.assert_called_once()
            self.assertEqual(review_summary.call_args.args[0].root, resolved)
            review_items.assert_called_once()
            self.assertEqual(review_items.call_args.args[0].root, resolved)
            self.assertEqual(review_items.call_args.args[1], "score_coverage")
            plan_issue = build_plan.call_args.kwargs["issue"]
            self.assertEqual(plan_issue["id"], "issue-1")
            build_plan.assert_called_once_with(resolved, "issue-1", issue=plan_issue)
            explain_issue = explain.call_args.args[1]
            self.assertEqual(explain_issue["id"], "issue-1")
            explain.assert_called_once_with(resolved, explain_issue)
            legacy_issues.assert_not_called()
            self.assertEqual(batch.call_args.args, (resolved, ["issue-1"]))
            self.assertFalse(batch.call_args.kwargs["confirm"])
            self.assertTrue(batch.call_args.kwargs["dry_run"])
            self.assertEqual(batch.call_args.kwargs["issue_snapshot"][0]["id"], "issue-1")
            self.assertEqual(summary["summary"], {"alpha": True})
            self.assertEqual(items["items"][0]["id"], "alpha-review")
            self.assertTrue(preview["ok"])
            self.assertTrue(explained["ok"])
            self.assertTrue(batch_result["ok"])

    def test_v2_chat_history_uses_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "load_messages", return_value=[{"content": "alpha"}]) as load:
                    loaded = _body(web_app.api_chat_messages_get("alpha"))
                with mock.patch.object(web_app, "save_message", return_value={"content": "saved"}) as save:
                    saved = _body(asyncio.run(web_app.api_chat_messages_post(_Request({"role": "user", "content": "hello"}), "alpha")))
                with mock.patch.object(web_app, "clear_messages", return_value=2) as clear:
                    cleared = _body(web_app.api_chat_messages_delete("alpha"))

            resolved = alpha.resolve()
            load.assert_called_once_with(resolved, "alpha")
            save.assert_called_once_with(resolved, "alpha", "user", "hello", "", [], "message")
            clear.assert_called_once_with(resolved, "alpha")
            self.assertEqual(loaded["run_id"], "alpha")
            self.assertEqual(saved["message"]["content"], "saved")
            self.assertEqual(cleared["removed"], 2)

    def test_v2_source_upload_uses_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.upsert_artifact_states(
                [
                    {
                        "artifact_key": "inputs/company.md",
                        "path": "inputs/company.md",
                        "kind": "file",
                        "status": "ready",
                        "producer": "prepare-inputs",
                        "sha256": "old-company",
                        "input_fingerprint": "old-sources",
                    },
                    {
                        "artifact_key": "workspace/chunks/company_chunks.json",
                        "path": "workspace/chunks/company_chunks.json",
                        "kind": "file",
                        "status": "ready",
                        "producer": "split-docs",
                        "sha256": "old-chunks",
                        "input_fingerprint": "old-inputs",
                    },
                ]
            )
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            upload = UploadFile(filename="company.txt", file=io.BytesIO(b"alpha company evidence"))

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(asyncio.run(web_app.api_upload("company", [upload], "alpha")))

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["saved"], ["company.txt"])
            self.assertEqual(
                (alpha / "sources" / "company" / "company.txt").read_bytes(),
                b"alpha company evidence",
            )
            self.assertFalse((beta / "sources" / "company" / "company.txt").exists())
            states = {item["artifact_key"]: item for item in store.artifact_states()}
            self.assertEqual(states["inputs/company.md"]["status"], "stale")
            self.assertEqual(states["workspace/chunks/company_chunks.json"]["status"], "stale")
            self.assertEqual(states["inputs/company.md"]["stale_source_command"], "sources.upload")

    def test_v2_agent_decisions_use_path_workspace_not_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch("agent.trace.load_decisions", return_value=[{"id": "alpha-decision"}]) as load:
                    payload = _body(web_app.api_agent_decisions(8, "alpha"))

            load.assert_called_once_with(alpha.resolve(), tail=8)
            self.assertEqual(payload["decisions"][0]["id"], "alpha-decision")

    def test_v2_logs_use_path_workspace_not_process_global_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            (alpha / "workspace").mkdir(parents=True)
            (beta / "workspace").mkdir(parents=True)
            (alpha / "workspace" / "runtime_logs.jsonl").write_text(
                json.dumps({"line": "alpha-log"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (beta / "workspace" / "runtime_logs.jsonl").write_text(
                json.dumps({"line": "beta-log"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            previous_logs = list(web_app.LOG_LINES)
            web_app._LOG_CONTEXT.run_root = alpha
            try:
                web_app._append_log("alpha-appended")
            finally:
                del web_app._LOG_CONTEXT.run_root
                web_app.LOG_LINES[:] = previous_logs

            async def first_chunk() -> str:
                response = await web_app.api_logs_stream(_EventRequest(), "alpha")
                chunk = await anext(response.body_iterator)
                await response.body_iterator.aclose()
                return chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                payload = _body(web_app.api_logs(200, "alpha"))
                chunk = asyncio.run(first_chunk())

            self.assertEqual(payload["lines"], ["alpha-log", "alpha-appended"])
            self.assertIn("alpha-log", chunk)
            self.assertNotIn("beta-log", chunk)

    def test_workspace_log_context_is_cleared_when_pipeline_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "alpha"
            root.mkdir()
            with mock.patch.object(web_app, "_run_sync_impl", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    web_app._run_sync("parse-tender", "alpha", root)
            self.assertFalse(hasattr(web_app._LOG_CONTEXT, "run_root"))

    def test_v2_workspace_catalog_has_no_process_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            (runs / "beta").mkdir(parents=True)
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = runs / "beta"
            request = _Request({}, principal={"id": "admin", "role": "admin"})

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_load_active_run_from_disk") as load_active:
                    payload = _body(web_app.api_v2_workspaces(request))

            load_active.assert_not_called()
            self.assertNotIn("active_run_id", payload)
            self.assertEqual({item["id"] for item in payload["runs"]}, {"alpha", "beta"})
            self.assertTrue(all("active" not in item for item in payload["runs"]))

    def test_v2_workspace_creation_does_not_change_process_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            alpha = runs / "alpha"
            beta = runs / "beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            active_file = runs / ".active_run"
            web_app.ACTIVE_RUN_ID = "beta"
            web_app.ACTIVE_RUN_ROOT = beta
            web_app.RUNNING = True
            request = _Request(
                {"name": "Alpha", "project_type": "goods", "expected_pages": 20},
                principal={"id": "owner-1", "role": "user"},
            )

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "ACTIVE_RUN_FILE", active_file):
                    with mock.patch.object(web_app, "_create_run_workspace", return_value=("alpha", alpha)):
                        payload = _body(asyncio.run(web_app.api_v2_create_workspace(request)))

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["run"]["id"], "alpha")
            self.assertEqual(web_app.ACTIVE_RUN_ID, "beta")
            self.assertEqual(web_app.ACTIVE_RUN_ROOT, beta)
            self.assertFalse(active_file.exists())
            acl = ControlStore(WorkspaceContext.resolve(runs, "alpha")).workspace_acl()
            self.assertEqual(acl[0]["principal_id"], "owner-1")

    def test_v2_project_profile_catalog_does_not_read_active_workspace(self) -> None:
        with mock.patch.object(web_app, "project_profile_choices", return_value=[{"project_type": "goods"}]) as choices:
            with mock.patch.object(web_app, "load_project_profile", side_effect=AssertionError("active workspace read")):
                payload = _body(web_app.api_v2_project_profiles())

        choices.assert_called_once_with()
        self.assertEqual(payload["choices"], [{"project_type": "goods"}])


    def test_migration_reconciliation_requires_admin_and_formal_export_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            conflict = store.record_migration_conflict(
                domain="materials",
                legacy=[{"item_id": "m1", "status": "submitted"}],
                authoritative=[{"item_id": "m1", "status": "verified"}],
                reason="material status disagreement",
            )
            with mock.patch.object(web_app, "_ensure_v2_issue_import", return_value=store):
                with self.assertRaises(ControlPlaneError) as blocked:
                    web_app._v2_export_preflight(context)
            self.assertEqual(blocked.exception.code, "MIGRATION_RECONCILIATION_REQUIRED")

            user_envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "migration.reconcile",
                    "payload": {
                        "conflict_id": conflict["conflict_id"],
                        "resolution": "keep_orphan",
                        "reason": "retain authority",
                    },
                    "expected_revision": store.revision(),
                    "actor": {"type": "user", "id": "owner", "role": "user"},
                },
                workspace_id="alpha",
            )
            with self.assertRaises(ControlPlaneError) as forbidden:
                web_app._handle_migration_reconcile(context, user_envelope, "op-user")
            self.assertEqual(forbidden.exception.code, "AUTH_FORBIDDEN")

            admin_envelope = CommandEnvelope.from_mapping(
                {
                    **user_envelope.as_dict(),
                    "actor": {"type": "user", "id": "admin", "role": "admin"},
                },
                workspace_id="alpha",
            )
            result = web_app._handle_migration_reconcile(context, admin_envelope, "op-admin")
            self.assertTrue(result["accepted"])
            self.assertEqual(store.migration_state()["status"], "ready")

    def test_migration_dry_run_inventories_legacy_files_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "workspace" / "agent").mkdir(parents=True)
            (root / "workspace" / "agent" / "goal_state.json").write_text(
                json.dumps({"goal_id": "legacy", "status": "in_progress"}),
                encoding="utf-8",
            )
            (root / "goal_state.json").write_text("{}", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            revision = store.revision()
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                response = _body(web_app.api_v2_migration_dry_run("alpha"))
            self.assertTrue(response["ok"])
            self.assertTrue(response["dry_run"])
            self.assertEqual(response["counts"]["importable"], 1)
            self.assertEqual(response["counts"]["orphans"], 1)
            self.assertEqual(store.revision(), revision)
            self.assertIsNone(store.goal_state())

    def test_migration_dry_run_quarantines_legacy_pipeline_and_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "pipeline_control.json").write_text('{"status":"running"}', encoding="utf-8")
            (workspace / "stale_artifacts.json").write_text('{"stale":["final.docx"]}', encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            result = web_app._v1_migration_dry_run(context)
            self.assertEqual(result["status"], "needs_reconciliation")
            self.assertEqual({item["kind"] for item in result["inventory"]["orphans"]}, {
                "legacy_pipeline_checkpoint", "legacy_stale_state",
            })
            checkpoint = next(
                item for item in result["inventory"]["orphans"]
                if item["kind"] == "legacy_pipeline_checkpoint"
            )
            self.assertEqual(checkpoint["state"]["status"], "running")

    def test_migration_scan_imports_candidates_and_persists_root_orphan_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "workspace" / "agent").mkdir(parents=True)
            (root / "workspace" / "agent" / "goal_state.json").write_text(
                json.dumps({"goal_id": "legacy", "status": "in_progress"}), encoding="utf-8"
            )
            (root / "goal_state.json").write_text("{}", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "migration.scan",
                    "payload": {},
                    "expected_revision": store.revision(),
                    "actor": {"id": "admin", "role": "admin"},
                },
                workspace_id="alpha",
            )
            result = web_app._handle_migration_scan(context, envelope, "scan-op")
            self.assertTrue(result["accepted"])
            self.assertEqual(store.goal_state()["goal_id"], "legacy")
            self.assertEqual(store.migration_state()["status"], "needs_reconciliation")
            self.assertEqual(store.migration_conflicts()[0]["domain"], "orphan")
            scan = store.snapshot()["migration"]["last_scan"]
            self.assertTrue(scan["fingerprint"])
            self.assertEqual(len(scan["manifest"]), 2)
            report = json.loads((root / "workspace" / "migration_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["source_fingerprint"], scan["fingerprint"])
            self.assertEqual(report["migration"]["status"], "needs_reconciliation")
            conflict = store.migration_conflicts(status="open")[0]
            reconcile = CommandEnvelope.from_mapping(
                {
                    "kind": "migration.reconcile",
                    "payload": {
                        "conflict_id": conflict["conflict_id"],
                        "resolution": "keep_orphan",
                        "reason": "retain legacy root evidence",
                    },
                    "expected_revision": store.revision(),
                    "actor": {"id": "admin", "role": "admin"},
                },
                workspace_id="alpha",
            )
            reconciled = web_app._handle_migration_reconcile(context, reconcile, "reconcile-op")
            self.assertTrue(reconciled["accepted"])
            refreshed_report = json.loads((root / "workspace" / "migration_report.json").read_text(encoding="utf-8"))
            self.assertEqual(refreshed_report["migration"]["status"], "ready")
            self.assertEqual(refreshed_report["last_action"]["kind"], "migration.reconcile")
            with mock.patch.object(web_app, "RUNS_DIR", runs):
                response = _body(web_app.api_v2_migration_report("alpha"))
            self.assertTrue(response["ok"])
            self.assertEqual(response["report"]["workspace_id"], "alpha")

    def test_migration_scan_hashes_legacy_artifacts_as_stale_until_v2_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            outputs = root / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "final.md").write_text("legacy formal draft", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)

            imported = web_app._register_legacy_artifact_inventory(context, store)

            artifact = store.artifact_state("outputs/final.md")
            self.assertGreater(imported, 0)
            self.assertEqual(artifact["status"], "stale")
            self.assertTrue(artifact["sha256"])
            self.assertEqual(artifact["legacy_readiness"], "unverified")
            self.assertEqual(web_app._register_legacy_artifact_inventory(context, store), 0)


if __name__ == "__main__":
    unittest.main()
