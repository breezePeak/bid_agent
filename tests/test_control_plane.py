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

    def test_schema_v13_adds_parent_operation_and_migration_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            database = context.root / "workspace" / "control.db"
            database.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE operations (
                        operation_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        start_command TEXT NOT NULL DEFAULT '',
                        fencing_token INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        message TEXT NOT NULL DEFAULT '',
                        error_json TEXT
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = ControlStore(context)
            migrated = sqlite3.connect(store.path)
            try:
                columns = {str(row[1]) for row in migrated.execute("PRAGMA table_info(operations)")}
                schema_version = migrated.execute(
                    "SELECT value FROM control_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                migration_table = migrated.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'migration_conflicts'"
                ).fetchone()
            finally:
                migrated.close()

            self.assertIn("parent_operation_id", columns)
            self.assertEqual(schema_version, "13")
            self.assertIsNotNone(migration_table)

    def test_migration_conflict_is_idempotent_blocks_mutations_and_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            conflict = store.record_migration_conflict(
                domain="goal",
                legacy={"goal_id": "legacy", "status": "succeeded"},
                authoritative={"goal_id": "current", "status": "in_progress"},
                reason="goal ids disagree",
            )
            duplicate = store.record_migration_conflict(
                domain="goal",
                legacy={"goal_id": "legacy", "status": "succeeded"},
                authoritative={"goal_id": "current", "status": "in_progress"},
                reason="goal ids disagree",
            )
            self.assertEqual(conflict["conflict_id"], duplicate["conflict_id"])
            self.assertEqual(store.migration_state()["status"], "needs_reconciliation")
            self.assertEqual(store.snapshot()["migration"]["open_count"], 1)

            gateway = CommandGateway(
                context,
                {"pipeline.start": lambda *_: {"accepted": True, "operation_status": "running"}},
            )
            with self.assertRaises(ControlPlaneError) as blocked:
                gateway.submit(_envelope(context, store, "pipeline.start"))
            self.assertEqual(blocked.exception.code, "MIGRATION_RECONCILIATION_REQUIRED")

            resolved = store.resolve_migration_conflict(
                conflict["conflict_id"],
                resolution="keep_orphan",
                actor={"type": "user", "id": "admin", "role": "admin"},
                reason="retain SQLite authority",
            )
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(store.migration_state()["status"], "ready")
            decisions = store.policy_decisions(issue_id=f"migration:{conflict['conflict_id']}")
            self.assertEqual(decisions[0]["decision_type"], "migration_reconciliation")
            backup_path = context.root / resolved["resolution"]["backup_path"]
            self.assertTrue(backup_path.exists())
            self.assertEqual(hashlib.sha256(backup_path.read_bytes()).hexdigest(), resolved["resolution"]["backup_sha256"])
            self.assertTrue(any(event["kind"] == "MigrationConflictResolved" for event in store.events()))

    def test_binding_legacy_goal_never_promotes_legacy_success_without_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            store.upsert_goal_state({"goal_id": "current", "status": "in_progress"}, source="test")
            conflict = store.record_migration_conflict(
                domain="goal",
                legacy={"goal_id": "legacy", "status": "succeeded"},
                authoritative=store.goal_state(),
                reason="different goals",
            )
            resolved = store.resolve_migration_conflict(
                conflict["conflict_id"],
                resolution="bind_legacy",
                actor={"id": "admin", "role": "admin"},
                reason="bind after evidence review",
            )
            self.assertEqual(store.goal_state()["goal_id"], "legacy")
            self.assertEqual(store.goal_state()["status"], "blocked_human")
            self.assertEqual(resolved["resolution"]["state_effect"], "legacy_bound_goal_success_normalized")

    def test_lazy_v1_import_detects_existing_authoritative_conflicts_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            connection = sqlite3.connect(store.path)
            try:
                connection.execute(
                    """
                    INSERT INTO goal_state(
                        singleton, goal_id, status, goal_json, source, created_at, updated_at
                    ) VALUES (1, 'goal-v2', 'in_progress', ?, 'v2_command', 'now', 'now')
                    """,
                    ('{"goal_id":"goal-v2","raw_user_goal":"sqlite","status":"in_progress"}',),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(ControlPlaneError) as conflict:
                store.ensure_goal_state(
                    {"goal_id": "goal-v1", "status": "succeeded", "raw_user_goal": "legacy"}
                )
            self.assertEqual(conflict.exception.code, "MIGRATION_RECONCILIATION_REQUIRED")
            self.assertEqual(store.goal_state()["goal_id"], "goal-v2")
            migration = store.migration_state()
            self.assertEqual(migration["open_count"], 1)
            self.assertEqual(migration["conflicts"][0]["domain"], "goal")
            self.assertEqual(migration["conflicts"][0]["legacy"]["goal_id"], "goal-v1")

    def test_migration_dry_run_classifies_inventory_without_changing_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            store.upsert_goal_state({"goal_id": "g2", "status": "in_progress"}, source="test")
            revision = store.revision()
            result = store.migration_dry_run(
                {
                    "goal": {"goal_id": "g1", "status": "succeeded"},
                    "materials": [{"item_id": "m1", "response_status": "deferred"}],
                    "unknown": {"value": 1},
                },
                orphans=[{"path": "goal_state.json"}],
            )
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["status"], "needs_reconciliation")
            self.assertEqual(result["counts"]["conflicts"], 1)
            self.assertEqual(result["counts"]["importable"], 1)
            self.assertEqual(result["counts"]["orphans"], 1)
            self.assertEqual(result["counts"]["unrecognized"], 1)
            self.assertEqual(store.revision(), revision)
            self.assertEqual(store.migration_conflicts(), [])

    def test_migration_dry_run_does_not_reopen_resolved_orphan_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            orphan = {"path": "goal_state.json", "kind": "root_legacy_control_state"}
            conflict = store.record_migration_conflict(
                domain="orphan", legacy=orphan, authoritative={}, reason="root legacy state"
            )
            store.resolve_migration_conflict(
                conflict["conflict_id"],
                resolution="keep_orphan",
                actor={"id": "admin", "role": "admin"},
                reason="keep evidence outside workspace",
            )
            dry_run = store.migration_dry_run({}, orphans=[orphan])
            self.assertEqual(dry_run["status"], "ready")
            self.assertEqual(dry_run["counts"]["orphans"], 0)
            self.assertEqual(dry_run["counts"]["acknowledged"], 1)

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

    def test_material_state_import_is_one_time_and_v2_update_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            legacy = {
                "item_id": "mat-1",
                "response_status": "deferred",
                "lifecycle_status": "missing",
                "evidence_status": "missing",
                "requirement": "certificate",
            }
            self.assertEqual(store.ensure_material_states([legacy]), 1)
            changed_legacy = {**legacy, "response_status": "ready", "lifecycle_status": "uploaded"}
            self.assertEqual(store.ensure_material_states([changed_legacy]), 0)
            self.assertEqual(store.material_state("mat-1")["response_status"], "deferred")

            authoritative = {
                **legacy,
                "response_status": "ready",
                "lifecycle_status": "verified",
                "evidence_status": "verified",
            }
            store.upsert_material_state(authoritative)
            current = store.material_state("mat-1")
            self.assertEqual(current["lifecycle_status"], "verified")
            self.assertEqual(current["control_source"], "v2_command")

    def test_empty_material_import_is_also_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            self.assertEqual(store.ensure_material_states([]), 0)
            late_legacy = {
                "item_id": "late-v1-item",
                "response_status": "ready",
                "lifecycle_status": "uploaded",
                "evidence_status": "missing",
            }
            self.assertEqual(store.ensure_material_states([late_legacy]), 0)
            self.assertEqual(store.material_states(), [])

    def test_issue_v1_import_does_not_overwrite_authoritative_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            legacy = {
                "id": "iss-1",
                "status": "open",
                "severity": "block",
                "code": "LEGACY",
                "title": "legacy issue",
            }
            accepted = {
                "id": "iss-2",
                "status": "accepted",
                "severity": "warn",
                "code": "LEGACY_ACCEPTED",
                "title": "accepted legacy issue",
                "accept_reason": "legacy decision",
                "accepted_by": "reviewer",
            }
            self.assertEqual(store.ensure_issue_states([legacy, accepted]), 2)
            changed = {**legacy, "status": "accepted", "title": "file overwrite"}
            self.assertEqual(store.ensure_issue_states([changed]), 0)
            current = next(item for item in store.issue_states() if item["id"] == "iss-1")
            self.assertEqual(current["status"], "open")
            self.assertEqual(current["title"], "legacy issue")
            imported_decisions = store.policy_decisions(issue_id="iss-2")
            self.assertEqual(len(imported_decisions), 1)
            self.assertEqual(imported_decisions[0]["actor"]["id"], "reviewer")

            authoritative = {**legacy, "status": "fixed", "title": "v2 state"}
            self.assertEqual(store.replace_issue_states([authoritative], source="test"), 1)
            current = store.issue_states()[0]
            self.assertEqual(current["status"], "fixed")
            self.assertEqual(current["control_source"], "test")

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

    def test_goal_v1_import_is_one_time_and_v2_update_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._workspace(Path(tmp), "alpha")
            store = ControlStore(context)
            legacy = {"goal_id": "goal-1", "status": "in_progress", "raw_user_goal": "legacy"}
            self.assertEqual(store.ensure_goal_state(legacy), 1)
            self.assertEqual(store.ensure_goal_state({**legacy, "status": "succeeded"}), 0)
            self.assertEqual(store.goal_state()["status"], "in_progress")

            updated = {**legacy, "status": "blocked_human", "raw_user_goal": "v2"}
            current = store.upsert_goal_state(updated, source="test")
            self.assertEqual(current["status"], "blocked_human")
            self.assertEqual(current["raw_user_goal"], "v2")
            self.assertEqual(current["control_source"], "test")

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
