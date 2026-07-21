from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app  # noqa: E402
from agent.repair_jobs import create_confirmation  # noqa: E402
from control_plane import CommandEnvelope, CommandGateway, ControlStore, WorkspaceContext  # noqa: E402


class _Request:
    def __init__(self, body: dict) -> None:
        self.body = body

    async def json(self) -> dict:
        return self.body


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


if __name__ == "__main__":
    unittest.main()
