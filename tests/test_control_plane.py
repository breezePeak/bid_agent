from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import (  # noqa: E402
    CommandEnvelope,
    CommandGateway,
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)


def _envelope(
    context: WorkspaceContext,
    store: ControlStore,
    kind: str,
    *,
    payload: dict | None = None,
    key: str | None = None,
) -> CommandEnvelope:
    command_id = str(uuid.uuid4())
    return CommandEnvelope.from_mapping(
        {
            "command_id": command_id,
            "kind": kind,
            "payload": payload or {},
            "expected_revision": store.revision(),
            "idempotency_key": key or command_id,
            "actor": {"type": "test", "id": "tester"},
        },
        workspace_id=context.workspace_id,
    )


class ControlPlaneTests(unittest.TestCase):
    def _workspace(self, base: Path, workspace_id: str) -> WorkspaceContext:
        runs = base / "runs"
        (runs / workspace_id).mkdir(parents=True)
        return WorkspaceContext.resolve(runs, workspace_id)

    def test_workspace_context_rejects_traversal_and_missing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "safe").mkdir(parents=True)
            self.assertEqual(WorkspaceContext.resolve(runs, "safe").root, (runs / "safe").resolve())
            for invalid in ("", "..", "../safe", "safe/../other"):
                with self.subTest(invalid=invalid), self.assertRaises(ControlPlaneError):
                    WorkspaceContext.resolve(runs, invalid)
            with self.assertRaises(ControlPlaneError) as missing:
                WorkspaceContext.resolve(runs, "missing")
            self.assertEqual(missing.exception.code, "WORKSPACE_NOT_FOUND")

    def test_material_verification_is_immutable_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            first = store.record_material_verification(
                item_id="qualification-license",
                verification_type="automatic",
                verdict="verified",
                verification={"confidence": 0.95, "evidence_ref": "upload-1"},
                actor={"id": "reviewer", "role": "reviewer"},
                source="materials.verify",
            )
            second = store.record_material_verification(
                item_id="qualification-license",
                verification_type="human",
                verdict="verified",
                verification={"reason": "checked original"},
                actor={"id": "admin", "role": "admin"},
                source="materials.confirm_verification",
            )
            history = store.material_verifications(item_id="qualification-license")
            self.assertEqual([item["verification_id"] for item in history], [second["verification_id"], first["verification_id"]])
            self.assertEqual(history[0]["actor"]["id"], "admin")
            self.assertTrue(any(event["kind"] == "MaterialVerified" for event in store.events()))

    def test_material_verification_can_commit_current_state_in_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            receipt = store.record_material_verification(
                item_id="qualification-license",
                verification_type="human",
                verdict="verified",
                verification={"reason": "checked original"},
                actor={"id": "admin", "role": "admin"},
                source="materials.confirm_verification",
                material_state={
                    "item_id": "qualification-license",
                    "response_status": "ready",
                    "lifecycle_status": "verified",
                    "evidence_status": "verified",
                },
            )
            state = store.material_state("qualification-license")
            verification_event = next(
                event for event in store.events() if event["aggregate_id"] == receipt["verification_id"]
            )
            state_event = next(
                event for event in store.events()
                if event["kind"] == "MaterialStateChanged" and event["aggregate_id"] == "qualification-license"
            )
            self.assertEqual(state["response_status"], "ready")
            self.assertEqual(verification_event["workspace_revision"], state_event["workspace_revision"])

    def test_material_verification_rolls_back_current_state_when_audit_insert_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            with store._connection() as connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_material_verification
                    BEFORE INSERT ON material_verifications
                    BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.record_material_verification(
                    item_id="qualification-license",
                    verification_type="human",
                    verdict="verified",
                    verification={"reason": "checked original"},
                    actor={"id": "admin", "role": "admin"},
                    source="materials.confirm_verification",
                    material_state={
                        "item_id": "qualification-license",
                        "response_status": "ready",
                        "lifecycle_status": "verified",
                        "evidence_status": "verified",
                    },
                )
            self.assertIsNone(store.material_state("qualification-license"))
            self.assertEqual(store.material_verifications(item_id="qualification-license"), [])

    def test_material_submission_keeps_hash_and_actor_without_staged_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            staged = store.register_material_upload(
                staged_path="workspace/material_uploads/license.pdf",
                filename="license.pdf",
                sha256="a" * 64,
                size_bytes=123,
            )
            consumed = store.consume_material_upload(staged["upload_token"])
            submission = store.record_material_submission(
                item_id="qualification-license",
                upload={**consumed, "staged_path": "workspace/material_uploads/secret.pdf"},
                actor={"id": "owner", "role": "operator"},
                source="materials.upload",
            )
            history = store.material_submissions(item_id="qualification-license")
            self.assertEqual(history[0]["submission_id"], submission["submission_id"])
            self.assertEqual(history[0]["sha256"], "a" * 64)
            self.assertEqual(history[0]["actor"]["id"], "owner")
            self.assertNotIn("staged_path", history[0])
            self.assertEqual(
                store.material_audit_summary()["qualification-license"]["latest_submission"]["submission_id"],
                submission["submission_id"],
            )
            with self.assertRaises(ControlPlaneError) as forged:
                store.record_material_submission(
                    item_id="qualification-license",
                    upload={**consumed, "sha256": "b" * 64},
                    actor={"id": "owner"},
                    source="materials.upload",
                )
            self.assertEqual(forged.exception.code, "UPLOAD_HASH_MISMATCH")

    def test_material_submission_keeps_upload_token_pending_when_audit_insert_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            staged = store.register_material_upload(
                staged_path="workspace/material_uploads/license.pdf",
                filename="license.pdf",
                sha256="a" * 64,
                size_bytes=123,
            )
            with store._connection() as connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_material_submission
                    BEFORE INSERT ON material_submissions
                    BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.record_material_submission(
                    item_id="qualification-license",
                    upload=staged,
                    actor={"id": "owner", "role": "operator"},
                    source="materials.upload",
                    consume_upload=True,
                )
            self.assertEqual(store.material_upload(staged["upload_token"])["status"], "pending")
            self.assertEqual(store.material_submissions(item_id="qualification-license"), [])

    def test_latest_gate_evaluations_uses_persisted_insertion_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            store.record_gate_evaluation(
                command="global-review", verdict="error", input_fingerprint="first", findings=[], source="test"
            )
            latest = store.record_gate_evaluation(
                command="global-review", verdict="pass", input_fingerprint="second", findings=[], source="test"
            )
            store.record_gate_evaluation(
                command="compliance-check", verdict="block", input_fingerprint="third", findings=[], source="test"
            )
            by_command = {item["command"]: item for item in store.latest_gate_evaluations()}
            self.assertEqual(by_command["global-review"]["evaluation_id"], latest["evaluation_id"])
            self.assertEqual(by_command["compliance-check"]["verdict"], "block")

    def test_latest_gate_receipt_uses_persisted_insertion_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            first = store.issue_gate_receipt(
                verdict="pass",
                gate_input_fingerprint="first",
                artifact_path="outputs/final.docx",
                artifact_sha256="a" * 64,
                rules_version="test",
            )
            latest = store.issue_gate_receipt(
                verdict="pass",
                gate_input_fingerprint="second",
                artifact_path="outputs/final.docx",
                artifact_sha256="b" * 64,
                rules_version="test",
            )
            self.assertNotEqual(first["receipt_id"], latest["receipt_id"])
            self.assertEqual(store.latest_gate_receipt()["receipt_id"], latest["receipt_id"])

    def test_gate_receipt_rejects_unsafe_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            with self.assertRaises(ControlPlaneError) as raised:
                ControlStore(context).issue_gate_receipt(
                    verdict="pass",
                    gate_input_fingerprint="fingerprint",
                    artifact_path="../outside.docx",
                    artifact_sha256="a" * 64,
                    rules_version="test",
                )
            self.assertEqual(raised.exception.code, "STATE_UNAVAILABLE")

    def test_gate_receipt_requires_rules_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            with self.assertRaises(ControlPlaneError) as raised:
                ControlStore(context).issue_gate_receipt(
                    verdict="pass",
                    gate_input_fingerprint="fingerprint",
                    artifact_path="outputs/final.docx",
                    artifact_sha256="a" * 64,
                    rules_version=" ",
                )
            self.assertEqual(raised.exception.code, "STATE_UNAVAILABLE")

    def test_command_is_durable_idempotent_and_emits_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            calls: list[str] = []

            def start(ctx, envelope, operation_id):
                calls.append(operation_id)
                return {"accepted": True, "operation_status": "running", "message": "started"}

            gateway = CommandGateway(context, {"pipeline.start": start})
            envelope = _envelope(context, gateway.store, "pipeline.start", key="same-request")
            first = gateway.submit(envelope)
            duplicate = gateway.submit(envelope)

            self.assertEqual(first.status, "accepted")
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(first.operation_id, duplicate.operation_id)
            self.assertEqual(len(calls), 1)
            self.assertTrue((context.root / "workspace" / "control.db").exists())
            snapshot = gateway.store.snapshot()
            self.assertEqual(snapshot["operation"]["status"], "running")
            self.assertEqual(len(snapshot["operations"]), 1)
            self.assertEqual(len(gateway.store.events()), 2)
            self.assertEqual(gateway.store.recent_events(limit=10), gateway.store.events())
            self.assertEqual(
                gateway.store.recent_events(limit=1)[0]["seq"],
                gateway.store.events()[-1]["seq"],
            )

    def test_post_commit_start_failure_marks_running_operation_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")

            def start(ctx, envelope, operation_id):
                def fail_start() -> None:
                    raise RuntimeError("worker thread unavailable")

                return {
                    "accepted": True,
                    "operation_status": "running",
                    "message": "worker queued",
                    "_after_commit": fail_start,
                }

            gateway = CommandGateway(context, {"pipeline.start": start})
            receipt = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            operation = gateway.store.operation(receipt.operation_id or "") or {}

            self.assertEqual(receipt.status, "rejected")
            self.assertEqual(operation["status"], "failed")
            self.assertEqual(receipt.error["code"], "COMMAND_POST_COMMIT_FAILED")

    def test_stage_run_records_attempt_and_terminal_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            running = store.record_stage_run(
                "operation-1",
                "build-md",
                "running",
                output={"phase": "structure", "products": [{"kind": "Draft"}]},
            )
            progress = store.record_stage_run(
                "operation-1",
                "build-md",
                "running",
                output={"phase": "semantic", "products": [{"kind": "Draft"}]},
            )
            completed = store.record_stage_run(
                "operation-1", "build-md", "succeeded", disposition="produced"
            )
            runs = store.stage_runs("operation-1")

            self.assertEqual(running["stage_run_id"], completed["stage_run_id"])
            self.assertEqual(progress["stage_run_id"], completed["stage_run_id"])
            self.assertEqual(runs[0]["attempt"], 1)
            self.assertEqual(runs[0]["status"], "succeeded")
            self.assertEqual(runs[0]["disposition"], "produced")
            self.assertEqual(runs[0]["output"]["phase"], "semantic")
            self.assertEqual(store.snapshot()["stage_runs"][0]["stage_run_id"], running["stage_run_id"])
            self.assertEqual(store.snapshot()["current_stage_runs"], [])
            self.assertEqual(store.latest_stage_run("operation-1", "build-md")["status"], "succeeded")
            self.assertIsNone(store.latest_stage_run("operation-1", "missing-stage"))

    def test_reconcile_expired_operation_closes_stages_llm_requests_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            gateway = CommandGateway(
                context,
                {"pipeline.start": lambda ctx, envelope, operation_id: {"accepted": True, "operation_status": "running"}},
            )
            receipt = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            operation_id = receipt.operation_id or ""
            store = gateway.store
            store.record_stage_run(operation_id, "analyze_scores", "running", disposition="started")
            request = store.start_llm_request(
                operation_id,
                "score_semantic_inference",
                parameters={"model": "test-model"},
            )

            connection = sqlite3.connect(store.path)
            try:
                connection.execute(
                    "UPDATE workspace_lease SET expires_at = ? WHERE operation_id = ?",
                    ("2000-01-01T00:00:00+00:00", operation_id),
                )
                connection.commit()
            finally:
                connection.close()

            recovered = store.reconcile_expired_operations()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["operation_id"], operation_id)
            self.assertEqual((store.operation(operation_id) or {})["status"], "failed")
            self.assertEqual(store.latest_stage_run(operation_id, "analyze_scores")["status"], "failed")
            self.assertEqual(store.llm_requests(operation_id)[0]["status"], "failed")
            self.assertIsNone(store.snapshot()["lease"])
            self.assertEqual(store.reconcile_expired_operations(), [])

    def test_stage_run_promotes_queued_attempt_without_creating_a_second_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)

            queued = store.record_stage_run("operation-1", "build-md", "queued", disposition="queued")
            running = store.record_stage_run("operation-1", "build-md", "running", disposition="started")
            completed = store.record_stage_run("operation-1", "build-md", "succeeded", disposition="produced")

            self.assertEqual(queued["stage_run_id"], running["stage_run_id"])
            self.assertEqual(running["stage_run_id"], completed["stage_run_id"])
            self.assertEqual(completed["attempt"], 1)

    def test_stage_run_terminal_state_is_immutable_and_duplicate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            store.record_stage_run("operation-1", "build-md", "running", disposition="started")
            completed = store.record_stage_run("operation-1", "build-md", "succeeded", disposition="produced")
            revision = store.revision()

            duplicate = store.record_stage_run("operation-1", "build-md", "succeeded", disposition="ignored")
            self.assertEqual(duplicate["stage_run_id"], completed["stage_run_id"])
            self.assertEqual(store.revision(), revision)
            with self.assertRaises(ControlPlaneError) as raised:
                store.record_stage_run("operation-1", "build-md", "failed", disposition="late_failure")
            self.assertEqual(raised.exception.code, "STATE_CONFLICT")

    def test_latest_terminal_stage_run_ignores_inflight_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            store.record_stage_run("operation-1", "build-md", "succeeded", disposition="produced")
            store.record_stage_run("operation-2", "build-md", "queued", disposition="queued")

            self.assertEqual(store.latest_stage_run_for_command("build-md")["status"], "queued")
            terminal = store.latest_terminal_stage_run_for_command("build-md") or {}
            self.assertEqual(terminal.get("status"), "succeeded")

    def test_operation_terminal_state_rejects_late_worker_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            gateway = CommandGateway(
                context,
                {"pipeline.start": lambda ctx, envelope, operation_id: {"accepted": True, "operation_status": "running"}},
            )
            receipt = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            operation = gateway.store.operation(receipt.operation_id or "") or {}
            gateway.store.sync_operation(
                receipt.operation_id or "",
                "succeeded",
                message="completed",
                fencing_token=operation["fencing_token"],
            )
            revision = gateway.store.revision()

            self.assertEqual(
                gateway.store.sync_operation(
                    receipt.operation_id or "",
                    "succeeded",
                    message="late duplicate",
                    fencing_token=operation["fencing_token"],
                ),
                revision,
            )
            with self.assertRaises(ControlPlaneError) as raised:
                gateway.store.sync_operation(
                    receipt.operation_id or "",
                    "failed",
                    message="late worker failure",
                    fencing_token=operation["fencing_token"],
                )
            self.assertEqual(raised.exception.code, "STATE_CONFLICT")
            self.assertEqual((gateway.store.operation(receipt.operation_id or "") or {})["status"], "succeeded")

    def test_late_pause_or_cancel_on_terminal_pipeline_is_durable_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            gateway = CommandGateway(
                context,
                {
                    "pipeline.start": lambda ctx, envelope, operation_id: {"accepted": True, "operation_status": "running"},
                    "pipeline.pause": lambda ctx, envelope, operation_id: self.fail("late pause must not dispatch"),
                    "pipeline.cancel": lambda ctx, envelope, operation_id: self.fail("late cancel must not dispatch"),
                },
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            operation = gateway.store.operation(started.operation_id or "") or {}
            gateway.store.sync_operation(
                started.operation_id or "",
                "succeeded",
                fencing_token=operation["fencing_token"],
            )
            pause = gateway.submit(
                _envelope(
                    context,
                    gateway.store,
                    "pipeline.pause",
                    payload={"operation_id": started.operation_id},
                    key="late-pause",
                )
            )
            self.assertEqual(pause.status, "no_op")
            self.assertEqual(pause.operation_id, started.operation_id)

            cancel_action = gateway.propose(
                _envelope(
                    context,
                    gateway.store,
                    "pipeline.cancel",
                    payload={"operation_id": started.operation_id},
                    key="late-cancel",
                ),
                label="确认取消",
                risk="high",
            )
            cancel = gateway.confirm(cancel_action["confirmation_id"])
            self.assertEqual(cancel.status, "no_op")
            self.assertEqual(cancel.operation_id, started.operation_id)

    def test_revision_conflict_fails_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            called = False

            def start(ctx, envelope, operation_id):
                nonlocal called
                called = True
                return {"accepted": True, "operation_status": "running"}

            gateway = CommandGateway(context, {"pipeline.start": start})
            stale = CommandEnvelope.from_mapping(
                {
                    "kind": "pipeline.start",
                    "payload": {},
                    "expected_revision": 99,
                    "idempotency_key": "stale",
                },
                workspace_id=context.workspace_id,
            )
            with self.assertRaises(ControlPlaneError) as error:
                gateway.submit(stale)
            self.assertEqual(error.exception.code, "REVISION_CONFLICT")
            self.assertFalse(called)

    def test_pause_reuses_existing_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")

            def start(ctx, envelope, operation_id):
                return {"accepted": True, "operation_status": "running", "message": "started"}

            def pause(ctx, envelope, operation_id):
                return {"accepted": True, "operation_status": "paused", "message": "paused"}

            gateway = CommandGateway(
                context,
                {"pipeline.start": start, "pipeline.pause": pause},
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            paused = gateway.submit(_envelope(context, gateway.store, "pipeline.pause"))
            snapshot = gateway.store.snapshot()

            self.assertEqual(started.operation_id, paused.operation_id)
            self.assertEqual(snapshot["operation"]["status"], "paused")
            self.assertEqual(len(snapshot["operations"]), 1)
            self.assertEqual(len(snapshot["commands"]), 2)

    def test_cancel_confirmation_is_single_use_and_controls_same_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")

            def start(ctx, envelope, operation_id):
                return {"accepted": True, "operation_status": "running"}

            def cancel(ctx, envelope, operation_id):
                return {"accepted": True, "operation_status": "cancelled", "message": "cancelled"}

            gateway = CommandGateway(
                context,
                {"pipeline.start": start, "pipeline.cancel": cancel},
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            cancel_envelope = _envelope(context, gateway.store, "pipeline.cancel")
            action = gateway.propose(cancel_envelope, label="确认取消", risk="high")
            cancelled = gateway.confirm(action["confirmation_id"])

            self.assertEqual(cancelled.operation_id, started.operation_id)
            self.assertEqual(gateway.store.snapshot()["operation"]["status"], "cancelled")
            with self.assertRaises(ControlPlaneError) as replay:
                gateway.confirm(action["confirmation_id"])
            self.assertEqual(replay.exception.code, "ACTION_REPLAYED")

    def test_confirmation_is_bound_to_proposer_and_refreshes_actor_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            observed: dict = {}

            def handler(ctx, envelope, operation_id):
                observed.update(envelope.actor)
                return {"accepted": True, "operation_status": "succeeded"}

            gateway = CommandGateway(context, {"repair.start": handler})
            envelope = _envelope(context, gateway.store, "repair.start")
            action = gateway.propose(envelope, label="确认修复", risk="high")
            revision = gateway.store.revision()

            with self.assertRaises(ControlPlaneError) as forbidden:
                gateway.confirm(
                    action["confirmation_id"],
                    actor={"type": "user", "id": "other-user", "role": "admin"},
                )
            self.assertEqual(forbidden.exception.code, "CONFIRMATION_FORBIDDEN")
            self.assertEqual(gateway.store.revision(), revision)
            self.assertEqual(gateway.store.snapshot()["confirmations"][0]["status"], "pending")

            receipt = gateway.confirm(
                action["confirmation_id"],
                actor={"type": "user", "id": "tester", "role": "editor"},
            )
            self.assertEqual(receipt.status, "accepted")
            self.assertEqual(observed, {"type": "user", "id": "tester", "role": "editor"})

    def test_repair_command_requires_persisted_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "running",
            }
            gateway = CommandGateway(context, {"repair.start": handler})
            envelope = _envelope(context, gateway.store, "repair.start")

            with self.assertRaises(ControlPlaneError) as unconfirmed:
                gateway.submit(envelope)
            self.assertEqual(unconfirmed.exception.code, "CONFIRMATION_REQUIRED")

            action = gateway.propose(envelope, label="确认最小修复", risk="high")
            receipt = gateway.confirm(action["confirmation_id"])
            self.assertEqual(receipt.status, "accepted")
            self.assertEqual(gateway.store.snapshot()["operation"]["kind"], "repair.start")

    def test_repair_creates_child_operation_and_preserves_blocked_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            start_handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "blocked",
            }
            repair_handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "running",
            }
            gateway = CommandGateway(
                context,
                {"pipeline.start": start_handler, "repair.start": repair_handler},
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            before = gateway.store.operation(started.operation_id or "") or {}
            repair = _envelope(context, gateway.store, "repair.start")
            action = gateway.propose(repair, label="confirm repair", risk="high")
            repaired = gateway.confirm(action["confirmation_id"])
            after = gateway.store.operation(repaired.operation_id or "") or {}

            self.assertNotEqual(repaired.operation_id, started.operation_id)
            self.assertEqual(after["parent_operation_id"], started.operation_id)
            self.assertEqual(after["status"], "running")
            self.assertEqual(after["fencing_token"], 1)
            self.assertEqual(gateway.store.operation(started.operation_id or "")["status"], "blocked")

    def test_confirmed_remediation_uses_child_and_pipeline_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            gateway = CommandGateway(
                context,
                {
                    "pipeline.start": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "blocked",
                    },
                    "issues.accept_risk": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "succeeded",
                    },
                    "pipeline.resume": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "running",
                    },
                },
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            before = gateway.store.operation(started.operation_id or "") or {}
            remediation = _envelope(
                context,
                gateway.store,
                "issues.accept_risk",
                payload={"issue_id": "issue-1", "reason": "documented acceptance"},
            )
            action = gateway.propose(remediation, label="accept risk", risk="high")
            accepted = gateway.confirm(action["confirmation_id"])
            after = gateway.store.operation(accepted.operation_id or "") or {}

            self.assertNotEqual(accepted.operation_id, started.operation_id)
            self.assertEqual(after["parent_operation_id"], started.operation_id)
            self.assertEqual(after["status"], "succeeded")
            self.assertEqual(gateway.store.operation(started.operation_id or "")["status"], "blocked")
            resumed = gateway.submit(_envelope(context, gateway.store, "pipeline.resume"))
            self.assertEqual(resumed.operation_id, started.operation_id)
            self.assertEqual(gateway.store.operation(started.operation_id or "")["status"], "running")
            self.assertEqual(
                gateway.store.operation(started.operation_id or "")["fencing_token"],
                before["fencing_token"] + 1,
            )

    def test_blocked_remediation_retry_reuses_child_not_pipeline_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            attempts = 0

            def repair(ctx, envelope, operation_id):
                nonlocal attempts
                attempts += 1
                return {
                    "accepted": True,
                    "operation_status": "blocked" if attempts == 1 else "succeeded",
                }

            gateway = CommandGateway(
                context,
                {
                    "pipeline.start": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "blocked",
                    },
                    "repair.start": repair,
                },
            )
            pipeline = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            first_envelope = _envelope(context, gateway.store, "repair.start")
            first_action = gateway.propose(first_envelope, label="repair", risk="high")
            first = gateway.confirm(first_action["confirmation_id"])
            first_state = gateway.store.operation(first.operation_id or "") or {}
            second_envelope = _envelope(context, gateway.store, "repair.start")
            second_action = gateway.propose(second_envelope, label="repair again", risk="high")
            second = gateway.confirm(second_action["confirmation_id"])

            self.assertEqual(second.operation_id, first.operation_id)
            self.assertNotEqual(second.operation_id, pipeline.operation_id)
            self.assertEqual(first_state["parent_operation_id"], pipeline.operation_id)
            self.assertEqual(gateway.store.operation(second.operation_id or "")["status"], "succeeded")
            self.assertEqual(gateway.store.operation(pipeline.operation_id or "")["status"], "blocked")

    def test_prepare_outline_supersedes_blocked_pipeline_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            gateway = CommandGateway(
                context,
                {
                    "document.run_pipeline": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "blocked",
                    },
                    "document.prepare_outline": lambda ctx, envelope, operation_id: {
                        "accepted": True,
                        "operation_status": "succeeded",
                    },
                },
            )
            pipeline = gateway.submit(
                _envelope(context, gateway.store, "document.run_pipeline")
            )
            outline = gateway.submit(
                _envelope(context, gateway.store, "document.prepare_outline")
            )

            self.assertNotEqual(outline.operation_id, pipeline.operation_id)
            old_operation = gateway.store.operation(pipeline.operation_id or "") or {}
            self.assertEqual(old_operation["status"], "cancelled")
            self.assertIsNotNone(old_operation["completed_at"])
            self.assertEqual(
                gateway.store.operation(outline.operation_id or "")["status"],
                "succeeded",
            )

    def test_rewrite_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "running",
            }
            gateway = CommandGateway(context, {"rewrite.chapters": handler})
            envelope = _envelope(
                context,
                gateway.store,
                "rewrite.chapters",
                payload={"chapter_ids": ["1.1"]},
            )

            with self.assertRaises(ControlPlaneError) as unconfirmed:
                gateway.submit(envelope)
            self.assertEqual(unconfirmed.exception.code, "CONFIRMATION_REQUIRED")
            action = gateway.propose(envelope, label="confirm rewrite", risk="high")
            receipt = gateway.confirm(action["confirmation_id"])
            self.assertEqual(receipt.status, "accepted")

    def test_material_mutations_require_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "succeeded",
            }
            gateway = CommandGateway(
                context,
                {
                    "materials.update": handler,
                    "materials.refill": handler,
                    "materials.upload": handler,
                    "materials.confirm_verification": handler,
                    "repair.issues": handler,
                    "issues.accept_risk": handler,
                },
            )
            for kind, payload in (
                ("materials.update", {"item_id": "m1", "response_status": "deferred"}),
                ("materials.refill", {}),
                ("materials.upload", {"item_id": "m1", "uploaded_path": "workspace/m1.pdf"}),
                ("materials.confirm_verification", {"item_id": "m1", "accept": True}),
                ("repair.issues", {"issue_ids": ["iss-1"]}),
                ("issues.accept_risk", {"issue_id": "iss-1", "reason": "documented reason"}),
            ):
                envelope = _envelope(context, gateway.store, kind, payload=payload)
                with self.assertRaises(ControlPlaneError) as unconfirmed:
                    gateway.submit(envelope)
                self.assertEqual(unconfirmed.exception.code, "CONFIRMATION_REQUIRED")
                action = gateway.propose(envelope, label=f"confirm {kind}", risk="high")
                receipt = gateway.confirm(action["confirmation_id"])
                self.assertEqual(receipt.status, "accepted")

    def test_workspaces_have_independent_databases_and_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            alpha = self._workspace(base, "alpha")
            beta = self._workspace(base, "beta")
            handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "running",
            }
            alpha_gateway = CommandGateway(alpha, {"pipeline.start": handler})
            beta_store = ControlStore(beta)
            alpha_gateway.submit(_envelope(alpha, alpha_gateway.store, "pipeline.start"))

            self.assertGreater(alpha_gateway.store.revision(), 0)
            self.assertEqual(beta_store.revision(), 0)
            self.assertIsNone(beta_store.snapshot()["operation"])
            self.assertNotEqual(alpha_gateway.store.path, beta_store.path)

    def test_gate_receipt_is_persisted_with_event_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            receipt = store.issue_gate_receipt(
                verdict="pass",
                gate_input_fingerprint="fingerprint-1",
                artifact_path="outputs/final.docx",
                artifact_sha256="artifact-sha",
                rules_version="rules-v1",
            )
            self.assertEqual(receipt["verdict"], "pass")
            self.assertEqual(store.latest_gate_receipt()["receipt_id"], receipt["receipt_id"])
            self.assertGreater(store.revision(), 0)
            self.assertEqual(store.events(0)[-1]["kind"], "GateReceiptIssued")

    def test_material_upload_token_is_one_time_control_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            staged = store.register_material_upload(
                staged_path="workspace/material_uploads/staging/cert.pdf",
                filename="cert.pdf",
                sha256="abc123",
                size_bytes=42,
            )
            self.assertEqual(staged["status"], "pending")
            consumed = store.consume_material_upload(staged["upload_token"])
            self.assertEqual(consumed["status"], "consumed")
            with self.assertRaises(ControlPlaneError) as replay:
                store.consume_material_upload(staged["upload_token"])
            self.assertEqual(replay.exception.code, "UPLOAD_TOKEN_CONSUMED")

    def test_policy_decisions_are_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            first = store.record_policy_decision(
                issue_id="iss-1",
                decision_type="accept_risk",
                decision={"reason": "documented exception"},
                actor={"id": "reviewer"},
            )
            second = store.record_policy_decision(
                issue_id="iss-1",
                decision_type="accept_risk",
                decision={"reason": "second review"},
                actor={"id": "owner"},
            )
            decisions = store.policy_decisions(issue_id="iss-1")
            self.assertEqual([item["decision_id"] for item in decisions], [first["decision_id"], second["decision_id"]])
            self.assertEqual(decisions[0]["actor"]["id"], "reviewer")

    def test_issue_update_and_policy_decision_commit_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            issue = {
                "id": "iss-atomic",
                "status": "open",
                "severity": "block",
                "code": "CRITICAL_CONFLICT",
            }
            store.replace_issue_states([issue], source="test")
            accepted = {**issue, "status": "accepted", "accepted_by": "admin"}
            result = store.update_issue_state_with_policy(
                accepted,
                decision_type="accept_risk",
                decision={"reason": "approved after review"},
                actor={"id": "admin", "role": "admin"},
                source="test_command",
            )
            self.assertEqual(result["issue_id"], "iss-atomic")
            self.assertEqual(store.issue_states()[0]["status"], "accepted")
            self.assertEqual(store.issue_states()[0]["control_source"], "test_command")
            decisions = store.policy_decisions(issue_id="iss-atomic")
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["actor"]["role"], "admin")

            with self.assertRaises(ControlPlaneError):
                store.update_issue_state_with_policy(
                    {**accepted, "id": "missing"},
                    decision_type="accept_risk",
                    decision={"reason": "must not persist"},
                    actor={"id": "admin"},
                )
            self.assertEqual(store.policy_decisions(issue_id="missing"), [])

    def test_workspace_acl_denies_unassigned_and_read_only_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            with self.assertRaises(ControlPlaneError) as missing:
                store.require_workspace_access("other-user")
            self.assertEqual(missing.exception.code, "WORKSPACE_FORBIDDEN")

            store.grant_workspace_access("owner", role="owner")
            store.grant_workspace_access("reader", role="viewer")
            self.assertEqual(store.require_workspace_access("owner", write=True)["role"], "owner")
            self.assertEqual(store.require_workspace_access("reader", write=False)["role"], "viewer")
            with self.assertRaises(ControlPlaneError) as read_only:
                store.require_workspace_access("reader", write=True)
            self.assertEqual(read_only.exception.code, "WORKSPACE_FORBIDDEN")

    def test_gate_rejection_keeps_operation_blocked_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")

            def blocked(ctx, envelope, operation_id):
                raise ControlPlaneError("GATE_BLOCKED", "quality gate blocked")

            gateway = CommandGateway(context, {"pipeline.start": blocked})
            receipt = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))

            self.assertEqual(receipt.status, "rejected")
            self.assertEqual(receipt.error["code"], "GATE_BLOCKED")
            self.assertEqual(gateway.store.snapshot()["operation"]["status"], "blocked")

    def test_document_regenerate_retries_a_blocked_operation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = self._workspace(Path(tmp), "workspace")
            attempts = 0

            def run_pipeline(ctx, envelope, operation_id):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ControlPlaneError("RESEARCH_BLOCKED", "research needs attention")
                return {"operation_status": "succeeded", "message": "regenerated"}

            gateway = CommandGateway(context, {"document.run_pipeline": run_pipeline})
            first = gateway.submit(_envelope(context, gateway.store, "document.run_pipeline"))
            self.assertEqual(first.status, "rejected")
            retry = gateway.submit(
                _envelope(context, gateway.store, "document.run_pipeline")
            )
            self.assertEqual(retry.status, "accepted")
            self.assertNotEqual(retry.operation_id, first.operation_id)

    def test_resume_increments_fencing_token_and_rejects_stale_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            handler = lambda ctx, envelope, operation_id: {
                "accepted": True,
                "operation_status": "running",
            }
            gateway = CommandGateway(
                context,
                {"pipeline.start": handler, "pipeline.resume": handler},
            )
            started = gateway.submit(_envelope(context, gateway.store, "pipeline.start"))
            gateway.store.sync_operation(started.operation_id or "", "paused", fencing_token=1)
            resumed = gateway.submit(
                _envelope(
                    context,
                    gateway.store,
                    "pipeline.resume",
                    payload={"operation_id": started.operation_id},
                )
            )
            operation = gateway.store.operation(resumed.operation_id or "") or {}
            self.assertEqual(operation["fencing_token"], 2)
            with self.assertRaises(ControlPlaneError) as fenced:
                gateway.store.sync_operation(
                    resumed.operation_id or "",
                    "running",
                    message="stale worker",
                    fencing_token=1,
                )
            self.assertEqual(fenced.exception.code, "LEASE_FENCED")


if __name__ == "__main__":
    unittest.main()
