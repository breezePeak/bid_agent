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
from control_plane import CommandEnvelope, CommandGateway, ControlStore, WorkspaceContext  # noqa: E402


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
                    confirmed = web_app.api_v2_confirm_action("alpha", action_id)
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
                blocked = _body(web_app.api_v2_confirm_action("alpha", first["action"]["action_id"]))
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
                            web_app.api_v2_confirm_action("alpha", second["action"]["action_id"])
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
                    web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"])
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
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"])
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
                            web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"])
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
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"])
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
                    web_app.api_v2_confirm_action("alpha", replay["action"]["action_id"])
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
                    with mock.patch("agent.issues.export_preflight", return_value=preflight):
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
                        web_app.api_v2_confirm_action("alpha", proposed["action"]["action_id"])
                    )
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["receipt"]["error"]["code"], "POLICY_DENIED")

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
                            runs_response = await client.get("/api/runs")
                            self.assertEqual(runs_response.status_code, 200)
                            forbidden = await client.get("/api/v2/workspaces/alpha/snapshot")
                            self.assertEqual(forbidden.status_code, 403)
                            self.assertEqual(forbidden.json()["error"]["code"], "WORKSPACE_FORBIDDEN")
                            select_forbidden = await client.post("/api/select-run", json={"run_id": "alpha"})
                            self.assertEqual(select_forbidden.status_code, 403)
                            delete_forbidden = await client.post("/api/delete-run", json={"run_id": "alpha"})
                            self.assertEqual(delete_forbidden.status_code, 403)
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

                with mock.patch.object(web_app, "_run_sync", return_value=0) as rebuild:
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
            reconcile.assert_called_once_with("alpha", context.root, web_app._run_sync)

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
            compatibility = {
                "goal": {"goal_id": "goal-v1", "status": "failed", "summary": "compat summary"},
                "goal_full": {"goal_id": "goal-v1", "status": "failed"},
                "agent_activity": {"status": "idle"},
                "repair_job": {"job_id": "repair-v1", "status": "completed"},
                "materials_summary": {"total": 99, "ready": 0},
                "issues_summary": {"total": 99, "open": 0},
                "pipeline": {},
                "workflow": [],
            }

            with mock.patch.object(web_app, "RUNS_DIR", runs):
                with mock.patch.object(web_app, "_status_payload", return_value=compatibility):
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
                with mock.patch("agent.issues.export_preflight", return_value={"ok": True}) as preflight:
                    _body(web_app.api_export_preflight("alpha"))
                with mock.patch.object(web_app, "_material_items", return_value=[]):
                    materials = _body(web_app.api_materials_checklist("alpha"))
                with mock.patch("agent.issues.load_open_issues") as load_issues:
                    with mock.patch("agent.root_cause.sync_issues_from_compliance") as sync_compliance:
                        with mock.patch("agent.root_cause.sync_issues_from_global_review") as sync_review:
                            issues = _body(web_app.api_list_issues("open", "alpha"))

            self.assertTrue(compliance["blocking"])
            self.assertEqual(compliance["items"][0]["check_id"], "alpha-check")
            preflight.assert_called_once_with(alpha.resolve())
            self.assertTrue(materials["ok"])
            self.assertEqual(materials["items"], [])
            load_issues.assert_not_called()
            sync_compliance.assert_not_called()
            sync_review.assert_not_called()
            self.assertEqual(issues["issues"][0]["id"], "alpha-issue")
            self.assertEqual(issues["summary"]["source"], "control.db")

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
                    detail = _body(web_app.api_workflow_step_detail(command, "alpha"))
                with mock.patch.object(web_app, "manual_review_summary", return_value={"alpha": True}) as review_summary:
                    summary = _body(web_app.api_manual_review_summary("alpha"))
                with mock.patch.object(web_app, "manual_review_items", return_value=[{"id": "alpha-review"}]) as review_items:
                    items = _body(web_app.api_manual_review_items("score_coverage", "alpha"))
                with mock.patch("agent.repair.build_repair_plan", return_value={"ok": True}) as build_plan:
                    preview = _body(web_app.api_preview_repair("issue-1", "alpha"))
                with mock.patch("agent.issues.load_open_issues") as legacy_issues:
                    with mock.patch("agent.root_cause.refine_issue_cause_with_llm", return_value={"ok": True}) as explain:
                        explained = _body(asyncio.run(web_app.api_explain_issue_cause("issue-1", _Request({}), "alpha")))
                with mock.patch("agent.repair.execute_repair_batch", return_value={"ok": True}) as batch:
                    batch_result = _body(asyncio.run(web_app.api_batch_preview_repair(_Request({"issue_ids": ["issue-1"]}), "alpha")))

            resolved = alpha.resolve()
            status.assert_called_once_with(resolved, "alpha")
            self.assertEqual(Path(detail["run_root"]), resolved)
            review_summary.assert_called_once_with(resolved)
            review_items.assert_called_once_with(resolved, "score_coverage")
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


if __name__ == "__main__":
    unittest.main()
