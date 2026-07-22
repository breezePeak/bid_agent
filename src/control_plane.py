from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class ControlPlaneError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: str
    root: Path

    @classmethod
    def resolve(cls, runs_root: Path, workspace_id: str) -> "WorkspaceContext":
        value = str(workspace_id or "").strip()
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ControlPlaneError("WORKSPACE_INVALID", "无效工作空间 ID。", status_code=400)
        resolved_runs = runs_root.resolve()
        root = (resolved_runs / value).resolve()
        if not root.is_relative_to(resolved_runs) or not root.exists() or not root.is_dir():
            raise ControlPlaneError("WORKSPACE_NOT_FOUND", f"工作空间不存在: {value}", status_code=404)
        return cls(workspace_id=value, root=root)


@dataclass(frozen=True)
class CommandEnvelope:
    command_id: str
    workspace_id: str
    kind: str
    payload: dict[str, Any]
    goal_id: str | None
    actor: dict[str, Any]
    expected_revision: int
    idempotency_key: str
    confirmation_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any], *, workspace_id: str) -> "CommandEnvelope":
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
        actor = value.get("actor") if isinstance(value.get("actor"), dict) else {"type": "user"}
        command_id = str(value.get("command_id") or uuid.uuid4())
        kind = str(value.get("kind") or "").strip()
        idempotency_key = str(value.get("idempotency_key") or command_id).strip()
        if not kind:
            raise ControlPlaneError("COMMAND_INVALID", "缺少 Command kind。", status_code=400)
        if not idempotency_key:
            raise ControlPlaneError("COMMAND_INVALID", "缺少 idempotency_key。", status_code=400)
        try:
            expected_revision = int(value.get("expected_revision", 0))
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError("COMMAND_INVALID", "expected_revision 必须是整数。", status_code=400) from exc
        return cls(
            command_id=command_id,
            workspace_id=workspace_id,
            kind=kind,
            payload=payload,
            goal_id=str(value.get("goal_id") or "").strip() or None,
            actor=actor,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            confirmation_id=str(value.get("confirmation_id") or "").strip() or None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "payload": self.payload,
            "goal_id": self.goal_id,
            "actor": self.actor,
            "expected_revision": self.expected_revision,
            "idempotency_key": self.idempotency_key,
            "confirmation_id": self.confirmation_id,
        }


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    operation_id: str | None
    status: str
    workspace_revision: int
    confirmation_id: str | None = None
    error: dict[str, Any] | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "operation_id": self.operation_id,
            "status": self.status,
            "workspace_revision": self.workspace_revision,
            "confirmation_id": self.confirmation_id,
            "error": self.error,
            "message": self.message,
        }


CommandHandler = Callable[[WorkspaceContext, CommandEnvelope, str], dict[str, Any]]
_STORE_INIT_GUARD = threading.Lock()
_STORE_INIT_LOCKS: dict[Path, threading.Lock] = {}


def _store_init_lock(path: Path) -> threading.Lock:
    with _STORE_INIT_GUARD:
        lock = _STORE_INIT_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _STORE_INIT_LOCKS[path] = lock
        return lock


class ControlStore:
    """Per-workspace authoritative V2 control state.

    Artifact content remains on disk. This store only owns command/control state and
    the append-only workspace event stream.
    """

    SCHEMA_VERSION = 16
    ACTIVE_OPERATION_STATES = ("queued", "running", "pausing", "paused", "cancelling", "blocked")
    CONFIRMATION_REQUIRED_KINDS = {
        "pipeline.cancel",
        "pipeline.skip_stage",
        "repair.start",
        "repair.issues",
        "issues.accept_risk",
        "rewrite.chapters",
        "materials.update",
        "materials.refill",
        "materials.upload",
        "materials.confirm_verification",
        "review.update",
        "document.apply_edit",
        "workspace.set_profile",
        "workspace.run_utility",
        "workspace.archive",
        "workspace.clean",
        "migration.scan",
        "migration.cutover",
        "migration.reconcile",
    }
    BLOCKED_REMEDIATION_KINDS = {
        "repair.start",
        "repair.issues",
        "issues.accept_risk",
        "rewrite.chapters",
        "materials.update",
        "materials.refill",
        "materials.upload",
        "materials.confirm_verification",
        "materials.verify",
        "materials.rebuild",
        "quality.revalidate",
        "gate.revalidate",
        "goal.resume",
        "review.update",
        "document.apply_edit",
        "workspace.set_profile",
        "workspace.run_utility",
        "workspace.archive",
        "workspace.clean",
    }

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.path = context.root / "workspace" / "control.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = _store_init_lock(self.path.resolve())
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._init_lock:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS control_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS commands (
                        command_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        goal_id TEXT,
                        actor_json TEXT NOT NULL,
                        expected_revision INTEGER NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        operation_id TEXT,
                        confirmation_id TEXT,
                        error_json TEXT,
                        message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS operations (
                        operation_id TEXT PRIMARY KEY,
                        parent_operation_id TEXT,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        start_command TEXT NOT NULL DEFAULT '',
                        fencing_token INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        message TEXT NOT NULL DEFAULT '',
                        error_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS workspace_lease (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        lease_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        fencing_token INTEGER NOT NULL,
                        owner TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS gate_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        verdict TEXT NOT NULL,
                        gate_input_fingerprint TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        artifact_sha256 TEXT NOT NULL,
                        rules_version TEXT NOT NULL,
                        findings_json TEXT NOT NULL,
                        policy_decisions_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS gate_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        command TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        input_fingerprint TEXT NOT NULL,
                        findings_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        source_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_gate_evaluations_command
                        ON gate_evaluations(command, created_at);
                    CREATE TABLE IF NOT EXISTS material_upload_tokens (
                        upload_token TEXT PRIMARY KEY,
                        staged_path TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        consumed_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS material_states (
                        item_id TEXT PRIMARY KEY,
                        response_status TEXT NOT NULL,
                        lifecycle_status TEXT NOT NULL,
                        evidence_status TEXT NOT NULL,
                        item_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS material_verifications (
                        verification_id TEXT PRIMARY KEY,
                        item_id TEXT NOT NULL,
                        verification_type TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        verification_json TEXT NOT NULL,
                        actor_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_material_verifications_item
                        ON material_verifications(item_id, created_at);
                    CREATE TABLE IF NOT EXISTS material_submissions (
                        submission_id TEXT PRIMARY KEY,
                        item_id TEXT NOT NULL,
                        upload_token TEXT NOT NULL UNIQUE,
                        filename TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        actor_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_material_submissions_item
                        ON material_submissions(item_id, created_at);
                    CREATE TABLE IF NOT EXISTS workspace_acl (
                        principal_id TEXT PRIMARY KEY,
                        role TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS issue_states (
                        issue_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        issue_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS policy_decisions (
                        decision_id TEXT PRIMARY KEY,
                        issue_id TEXT NOT NULL,
                        decision_type TEXT NOT NULL,
                        decision_json TEXT NOT NULL,
                        actor_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS artifact_states (
                        artifact_key TEXT PRIMARY KEY,
                        artifact_path TEXT NOT NULL,
                        artifact_kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        producer TEXT NOT NULL,
                        input_fingerprint TEXT NOT NULL,
                        manifest_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS goal_state (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        goal_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        goal_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS repair_job_state (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        job_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        job_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS agent_activity_state (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        status TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        activity_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS confirmations (
                        confirmation_id TEXT PRIMARY KEY,
                        command_json TEXT NOT NULL,
                        command_hash TEXT NOT NULL,
                        actor_json TEXT NOT NULL,
                        risk TEXT NOT NULL,
                        label TEXT NOT NULL,
                        status TEXT NOT NULL,
                        expected_revision INTEGER NOT NULL,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        consumed_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS workspace_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        workspace_revision INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        aggregate_type TEXT NOT NULL,
                        aggregate_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        occurred_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS migration_conflicts (
                        conflict_id TEXT PRIMARY KEY,
                        domain TEXT NOT NULL,
                        status TEXT NOT NULL,
                        legacy_json TEXT NOT NULL,
                        authoritative_json TEXT NOT NULL,
                        resolution_json TEXT,
                        actor_json TEXT,
                        reason TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        resolved_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_revision ON workspace_events(workspace_revision);
                    CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_issue_states_status ON issue_states(status, severity);
                    CREATE INDEX IF NOT EXISTS idx_policy_decisions_issue ON policy_decisions(issue_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_artifact_states_status ON artifact_states(status, producer);
                    CREATE INDEX IF NOT EXISTS idx_migration_conflicts_status
                        ON migration_conflicts(status, domain, created_at);
                    """
                )
                operation_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(operations)").fetchall()
                }
                if "parent_operation_id" not in operation_columns:
                    connection.execute("ALTER TABLE operations ADD COLUMN parent_operation_id TEXT")
                connection.execute(
                    """
                    INSERT INTO control_meta(key, value) VALUES ('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(self.SCHEMA_VERSION),),
                )
                connection.execute("INSERT OR IGNORE INTO control_meta(key, value) VALUES ('revision', '0')")

    @staticmethod
    def _migration_conflict_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["legacy"] = _decode(value.pop("legacy_json", None), {})
        value["authoritative"] = _decode(value.pop("authoritative_json", None), {})
        value["resolution"] = _decode(value.pop("resolution_json", None), None)
        value["actor"] = _decode(value.pop("actor_json", None), None)
        return value

    def migration_conflicts(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM migration_conflicts WHERE status = ? ORDER BY created_at, conflict_id",
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM migration_conflicts ORDER BY created_at, conflict_id"
                ).fetchall()
        return [self._migration_conflict_row(row) for row in rows]

    def migration_state(self) -> dict[str, Any]:
        conflicts = self.migration_conflicts()
        open_conflicts = [item for item in conflicts if item.get("status") == "open"]
        with self._connection() as connection:
            scan = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'migration_last_scan'"
            ).fetchone()
            cutover = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'migration_cutover'"
            ).fetchone()
        return {
            "status": "needs_reconciliation" if open_conflicts else "ready",
            "open_count": len(open_conflicts),
            "conflicts": conflicts,
            "last_scan": _decode(str(scan["value"]), None) if scan is not None else None,
            "cutover": _decode(str(cutover["value"]), None) if cutover is not None else None,
        }

    def record_compatibility_usage(self, route: str, actor: dict[str, Any]) -> None:
        """Track V1 adapter usage without changing control revision or domain state."""
        route_name = str(route or "").strip()[:256]
        if not route_name:
            return
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'compatibility_usage'"
                ).fetchone()
                usage = _decode(str(row["value"]), {}) if row is not None else {}
                usage = usage if isinstance(usage, dict) else {}
                routes = usage.get("routes") if isinstance(usage.get("routes"), dict) else {}
                current = routes.get(route_name) if isinstance(routes.get(route_name), dict) else {}
                routes[route_name] = {
                    "calls": int(current.get("calls") or 0) + 1,
                    "last_called_at": now,
                    "last_actor": {
                        "id": str(actor.get("id") or "")[:128],
                        "type": str(actor.get("type") or "")[:64],
                    },
                }
                usage["routes"] = routes
                usage["updated_at"] = now
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('compatibility_usage', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_json(usage),),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def compatibility_usage(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'compatibility_usage'"
            ).fetchone()
        usage = _decode(str(row["value"]), {}) if row is not None else {}
        return usage if isinstance(usage, dict) else {}

    def migration_backups(self) -> list[dict[str, Any]]:
        backup_dir = (self.path.parent / "migration_backups").resolve()
        if not backup_dir.exists():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(backup_dir.glob("control-before-*.db")):
            resolved = path.resolve()
            if not resolved.is_relative_to(backup_dir) or not resolved.is_file():
                continue
            item: dict[str, Any] = {
                "path": resolved.relative_to(self.context.root).as_posix(),
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "size_bytes": resolved.stat().st_size,
                "verified": False,
            }
            try:
                connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
                try:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()
                    tables = {
                        str(row[0])
                        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                    }
                    version = connection.execute(
                        "SELECT value FROM control_meta WHERE key = 'schema_version'"
                    ).fetchone() if "control_meta" in tables else None
                    item.update(
                        {
                            "verified": bool(integrity and str(integrity[0]).lower() == "ok")
                            and {"control_meta", "operations", "workspace_events"}.issubset(tables),
                            "integrity": str(integrity[0]) if integrity else "missing",
                            "schema_version": str(version[0]) if version else "",
                        }
                    )
                finally:
                    connection.close()
            except (OSError, sqlite3.Error) as exc:
                item["error"] = str(exc)
            result.append(item)
        return result

    def drill_migration_backup(self, relative_path: str) -> dict[str, Any]:
        backup_dir = (self.path.parent / "migration_backups").resolve()
        candidate = (self.context.root / str(relative_path or "")).resolve()
        if (
            not candidate.is_relative_to(backup_dir)
            or candidate.name != Path(relative_path).name
            or candidate.suffix.lower() != ".db"
            or not candidate.exists()
            or not candidate.is_file()
        ):
            raise ControlPlaneError("MIGRATION_BACKUP_NOT_FOUND", "迁移备份不存在或路径无效。", status_code=404)
        verified = next((item for item in self.migration_backups() if item["path"] == candidate.relative_to(self.context.root).as_posix()), None)
        if not verified or not verified.get("verified"):
            raise ControlPlaneError("MIGRATION_BACKUP_INVALID", "迁移备份未通过完整性校验。", status_code=409)
        with tempfile.TemporaryDirectory(prefix="bid-agent-migration-drill-") as tmp:
            restored_path = Path(tmp) / "restored-control.db"
            source = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
            destination = sqlite3.connect(str(restored_path))
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            restored = sqlite3.connect(f"file:{restored_path.as_posix()}?mode=ro", uri=True)
            try:
                integrity = restored.execute("PRAGMA integrity_check").fetchone()
                tables = {
                    str(row[0])
                    for row in restored.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                }
            finally:
                restored.close()
        ok = bool(integrity and str(integrity[0]).lower() == "ok") and {
            "control_meta", "operations", "workspace_events"
        }.issubset(tables)
        if not ok:
            raise ControlPlaneError("MIGRATION_RECOVERY_DRILL_FAILED", "迁移备份恢复演练失败。", status_code=503)
        return {**verified, "recovery_drill": "passed", "restored_tables": sorted(tables)}

    def record_migration_scan(self, *, fingerprint: str, manifest: list[dict[str, Any]], actor: dict[str, Any]) -> None:
        if not fingerprint:
            raise ControlPlaneError("COMMAND_INVALID", "迁移扫描缺少 source fingerprint。", status_code=400)
        value = {"fingerprint": fingerprint, "manifest": manifest, "actor": actor, "scanned_at": _now()}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'migration_last_scan'"
                ).fetchone()
                prior_value = _decode(str(prior["value"]), {}) if prior is not None else {}
                if isinstance(prior_value, dict) and prior_value.get("fingerprint") == fingerprint:
                    connection.commit()
                    return
                revision = self._bump_revision(connection)
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('migration_last_scan', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_json(value),),
                )
                self._event(
                    connection, revision, "MigrationScanned", "Migration", self.context.workspace_id,
                    {"fingerprint": fingerprint, "source_count": len(manifest), "actor": actor},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def activate_migration_cutover(self, *, fingerprint: str, actor: dict[str, Any]) -> dict[str, Any]:
        state = self.migration_state()
        if state["open_count"]:
            raise ControlPlaneError(
                "MIGRATION_RECONCILIATION_REQUIRED",
                "存在未处理迁移冲突，不能切换工作区控制面。",
                details={"open_count": state["open_count"]},
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                scan_row = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'migration_last_scan'"
                ).fetchone()
                scan = _decode(str(scan_row["value"]), {}) if scan_row is not None else {}
                if not isinstance(scan, dict) or str(scan.get("fingerprint") or "") != fingerprint:
                    raise ControlPlaneError(
                        "MIGRATION_SCAN_REQUIRED",
                        "迁移扫描不存在或源文件已变化，请重新扫描后切换。",
                        details={"scan_fingerprint": scan.get("fingerprint") if isinstance(scan, dict) else ""},
                    )
                existing = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'migration_cutover'"
                ).fetchone()
                prior = _decode(str(existing["value"]), {}) if existing is not None else {}
                if isinstance(prior, dict) and prior.get("fingerprint") == fingerprint:
                    connection.commit()
                    return prior
                value = {"status": "active", "fingerprint": fingerprint, "actor": actor, "activated_at": _now()}
                revision = self._bump_revision(connection)
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('migration_cutover', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_json(value),),
                )
                self._event(
                    connection, revision, "MigrationCutoverActivated", "Migration", self.context.workspace_id,
                    {"fingerprint": fingerprint, "actor": actor},
                )
                connection.commit()
                return value
            except Exception:
                connection.rollback()
                raise

    def v1_import_pending(self, domain: str) -> bool:
        markers = {
            "goal": "goal_v1_imported",
            "materials": "materials_v1_imported",
            "issues": "issue_v1_imported",
            "repair_job": "repair_job_v1_imported",
            "agent_activity": "agent_activity_v1_imported",
        }
        marker = markers.get(str(domain or ""))
        if not marker:
            raise ControlPlaneError("COMMAND_INVALID", "未知 V1 导入领域。", status_code=400)
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM control_meta WHERE key = ?", (marker,)
            ).fetchone() is None

    def migration_dry_run(
        self,
        legacy_domains: dict[str, Any],
        *,
        orphans: list[dict[str, Any]] | None = None,
        unrecognized: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compare V1 candidates with SQLite without importing or changing revision."""
        authoritative = {
            "goal": self.goal_state(),
            "materials": self.material_states(),
            "issues": self.issue_states(),
            "repair_job": self.repair_job_state(),
            "agent_activity": self.agent_activity_state(),
        }

        def projection(domain: str, value: Any) -> Any:
            if domain == "goal":
                item = value if isinstance(value, dict) else {}
                return (str(item.get("goal_id") or item.get("id") or ""), str(item.get("status") or "pending"))
            if domain == "repair_job":
                item = value if isinstance(value, dict) else {}
                return (
                    str(item.get("job_id") or ""), str(item.get("status") or "awaiting_confirmation"),
                    str(item.get("phase") or "awaiting_confirmation"),
                )
            if domain == "agent_activity":
                item = value if isinstance(value, dict) else {}
                return (str(item.get("status") or "idle"), str(item.get("phase") or ""))
            rows = value if isinstance(value, list) else []
            if domain == "materials":
                return sorted(
                    (
                        str(item.get("item_id") or ""),
                        str(item.get("response_status") or "deferred"),
                        str(item.get("lifecycle_status") or "missing"),
                        str(item.get("evidence_status") or "missing"),
                    )
                    for item in rows if isinstance(item, dict)
                )
            return sorted(
                (
                    str(item.get("id") or ""),
                    str(item.get("status") or "open"),
                    str(item.get("severity") or "warn"),
                )
                for item in rows if isinstance(item, dict)
            )

        resolved_evidence = {
            (str(item.get("domain") or ""), _json(item.get("legacy")))
            for item in self.migration_conflicts(status="resolved")
        }

        def is_acknowledged(domain: str, legacy: Any) -> bool:
            return (domain, _json(legacy)) in resolved_evidence

        inventory: dict[str, list[dict[str, Any]]] = {
            "importable": [],
            "aligned": [],
            "conflicts": [],
            "orphans": [],
            "unrecognized": [],
            "acknowledged": [],
        }
        for item in orphans or []:
            if is_acknowledged("orphan", item):
                inventory["acknowledged"].append({"domain": "orphan", "legacy": item})
            else:
                inventory["orphans"].append(item)
        for item in unrecognized or []:
            if is_acknowledged("unrecognized", item):
                inventory["acknowledged"].append({"domain": "unrecognized", "legacy": item})
            else:
                inventory["unrecognized"].append(item)
        for domain, candidate in legacy_domains.items():
            if domain not in authoritative:
                item = {"domain": domain, "value": candidate}
                if is_acknowledged("unrecognized", item):
                    inventory["acknowledged"].append({"domain": "unrecognized", "legacy": item})
                else:
                    inventory["unrecognized"].append(item)
                continue
            current = authoritative[domain]
            current_empty = current is None if domain in {"goal", "agent_activity"} else not current
            item = {"domain": domain, "legacy": candidate, "authoritative": current}
            if current_empty:
                inventory["importable"].append(item)
            elif projection(domain, candidate) == projection(domain, current):
                inventory["aligned"].append(item)
            elif is_acknowledged(domain, candidate):
                inventory["acknowledged"].append(item)
            else:
                inventory["conflicts"].append(item)
        return {
            "status": (
                "needs_reconciliation"
                if inventory["conflicts"] or inventory["orphans"] or inventory["unrecognized"]
                else "ready"
            ),
            "dry_run": True,
            "inventory": inventory,
            "counts": {key: len(value) for key, value in inventory.items()},
            "workspace_revision": self.revision(),
        }

    def assert_migration_ready(self) -> None:
        state = self.migration_state()
        if state["open_count"]:
            raise ControlPlaneError(
                "MIGRATION_RECONCILIATION_REQUIRED",
                "工作区存在未解决的 V1/V2 状态冲突，已拒绝变更操作。",
                status_code=409,
                details={"open_count": state["open_count"]},
            )

    def record_migration_conflict(
        self,
        *,
        domain: str,
        legacy: Any,
        authoritative: Any,
        reason: str,
        exclude_operation_id: str = "",
    ) -> dict[str, Any]:
        domain_name = str(domain or "").strip()
        if not domain_name:
            raise ControlPlaneError("COMMAND_INVALID", "迁移冲突缺少 domain。", status_code=400)
        identity = _json(
            {"workspace_id": self.context.workspace_id, "domain": domain_name,
             "legacy": legacy, "authoritative": authoritative}
        )
        conflict_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"migration-conflict:{identity}"))
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO migration_conflicts(
                        conflict_id, domain, status, legacy_json, authoritative_json,
                        reason, created_at
                    ) VALUES (?, ?, 'open', ?, ?, ?, ?)
                    """,
                    (conflict_id, domain_name, _json(legacy), _json(authoritative), str(reason), now),
                )
                if int(cursor.rowcount or 0):
                    placeholders = ",".join("?" for _ in self.ACTIVE_OPERATION_STATES)
                    blocked_sql = (
                        f"UPDATE operations SET status = 'blocked', updated_at = ?, message = ? "
                        "WHERE status IN (" + placeholders + ")"
                    )
                    blocked_args: tuple[Any, ...] = (
                        now,
                        "检测到 V1/V2 状态迁移冲突，等待管理员协调。",
                        *self.ACTIVE_OPERATION_STATES,
                    )
                    if exclude_operation_id:
                        blocked_sql += " AND operation_id <> ?"
                        blocked_args += (exclude_operation_id,)
                    blocked = connection.execute(blocked_sql, blocked_args)
                    revision = self._bump_revision(connection)
                    self._event(
                        connection, revision, "MigrationConflictDetected", "MigrationConflict",
                        conflict_id,
                        {"domain": domain_name, "reason": str(reason), "blocked_operations": int(blocked.rowcount or 0)},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return next(item for item in self.migration_conflicts() if item["conflict_id"] == conflict_id)

    def resolve_migration_conflict(
        self,
        conflict_id: str,
        *,
        resolution: str,
        actor: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        resolution_name = str(resolution or "").strip()
        if resolution_name not in {"bind_legacy", "mark_failed", "keep_orphan"}:
            raise ControlPlaneError("COMMAND_INVALID", "无效的迁移冲突处理方式。", status_code=400)
        if not str(reason or "").strip():
            raise ControlPlaneError("COMMAND_INVALID", "迁移冲突处理必须填写原因。", status_code=400)
        with self._connection() as connection:
            preview_row = connection.execute(
                "SELECT * FROM migration_conflicts WHERE conflict_id = ?", (conflict_id,)
            ).fetchone()
        if preview_row is None:
            raise ControlPlaneError("MIGRATION_CONFLICT_NOT_FOUND", "迁移冲突不存在。", status_code=404)
        backup_dir = self.path.parent / "migration_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"control-before-{conflict_id}.db"
        if not backup_path.exists():
            with self._connection() as source:
                destination = sqlite3.connect(str(backup_path))
                try:
                    source.backup(destination)
                finally:
                    destination.close()
        backup_sha256 = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM migration_conflicts WHERE conflict_id = ?", (conflict_id,)
                ).fetchone()
                if row is None:
                    raise ControlPlaneError("MIGRATION_CONFLICT_NOT_FOUND", "迁移冲突不存在。", status_code=404)
                if str(row["status"]) != "open":
                    raise ControlPlaneError("MIGRATION_CONFLICT_RESOLVED", "迁移冲突已处理。", status_code=409)
                domain = str(row["domain"])
                legacy = _decode(str(row["legacy_json"]), None)
                state_effect = "authority_preserved"
                if resolution_name == "bind_legacy":
                    if domain == "goal":
                        if not isinstance(legacy, dict):
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 Goal 证据无效。", status_code=503)
                        goal_id = str(legacy.get("goal_id") or legacy.get("id") or "").strip()
                        if not goal_id:
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 Goal 缺少 goal_id。", status_code=503)
                        legacy_status = str(legacy.get("status") or "pending")
                        status = "blocked_human" if legacy_status in {"succeeded", "completed"} else legacy_status
                        normalized = {**legacy, "goal_id": goal_id, "status": status}
                        existing_goal = connection.execute(
                            "SELECT created_at FROM goal_state WHERE singleton = 1"
                        ).fetchone()
                        connection.execute(
                            """
                            INSERT INTO goal_state(
                                singleton, goal_id, status, goal_json, source, created_at, updated_at
                            ) VALUES (1, ?, ?, ?, 'migration_reconciliation', ?, ?)
                            ON CONFLICT(singleton) DO UPDATE SET
                                goal_id = excluded.goal_id, status = excluded.status,
                                goal_json = excluded.goal_json, source = excluded.source,
                                updated_at = excluded.updated_at
                            """,
                            (
                                goal_id, status, _json(normalized),
                                str(existing_goal["created_at"]) if existing_goal else now, now,
                            ),
                        )
                        connection.execute(
                            "INSERT INTO control_meta(key, value) VALUES ('goal_v1_imported', '1') "
                            "ON CONFLICT(key) DO UPDATE SET value = '1'"
                        )
                        state_effect = "legacy_bound_goal_success_normalized" if status != legacy_status else "legacy_bound"
                    elif domain == "repair_job":
                        if not isinstance(legacy, dict) or not str(legacy.get("job_id") or "").strip():
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 RepairJob 证据无效。", status_code=503)
                        connection.execute(
                            """
                            INSERT INTO repair_job_state(
                                singleton, job_id, status, phase, job_json, source, created_at, updated_at
                            ) VALUES (1, ?, ?, ?, ?, 'migration_reconciliation', ?, ?)
                            ON CONFLICT(singleton) DO UPDATE SET
                                job_id = excluded.job_id, status = excluded.status, phase = excluded.phase,
                                job_json = excluded.job_json, source = excluded.source, updated_at = excluded.updated_at
                            """,
                            (
                                str(legacy["job_id"]), str(legacy.get("status") or "awaiting_confirmation"),
                                str(legacy.get("phase") or "awaiting_confirmation"), _json(legacy), now, now,
                            ),
                        )
                        connection.execute(
                            "INSERT INTO control_meta(key, value) VALUES ('repair_job_v1_imported', '1') "
                            "ON CONFLICT(key) DO UPDATE SET value = '1'"
                        )
                        state_effect = "legacy_bound"
                    elif domain == "agent_activity":
                        if not isinstance(legacy, dict):
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 AgentActivity 证据无效。", status_code=503)
                        connection.execute(
                            """
                            INSERT INTO agent_activity_state(
                                singleton, status, phase, activity_json, source, created_at, updated_at
                            ) VALUES (1, ?, ?, ?, 'migration_reconciliation', ?, ?)
                            ON CONFLICT(singleton) DO UPDATE SET
                                status = excluded.status, phase = excluded.phase, activity_json = excluded.activity_json,
                                source = excluded.source, updated_at = excluded.updated_at
                            """,
                            (str(legacy.get("status") or "idle"), str(legacy.get("phase") or ""), _json(legacy), now, now),
                        )
                        connection.execute(
                            "INSERT INTO control_meta(key, value) VALUES ('agent_activity_v1_imported', '1') "
                            "ON CONFLICT(key) DO UPDATE SET value = '1'"
                        )
                        state_effect = "legacy_bound"
                    elif domain == "materials":
                        if not isinstance(legacy, list):
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移材料证据无效。", status_code=503)
                        connection.execute("DELETE FROM material_states")
                        for item in legacy:
                            if not isinstance(item, dict) or not str(item.get("item_id") or "").strip():
                                raise ControlPlaneError("STATE_UNAVAILABLE", "迁移材料项缺少 item_id。", status_code=503)
                            connection.execute(
                                """
                                INSERT INTO material_states(
                                    item_id, response_status, lifecycle_status, evidence_status,
                                    item_json, source, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, 'migration_reconciliation', ?, ?)
                                """,
                                (
                                    str(item["item_id"]), str(item.get("response_status") or "deferred"),
                                    str(item.get("lifecycle_status") or "missing"),
                                    str(item.get("evidence_status") or "missing"), _json(item), now, now,
                                ),
                            )
                        connection.execute(
                            "INSERT INTO control_meta(key, value) VALUES ('materials_v1_imported', '1') "
                            "ON CONFLICT(key) DO UPDATE SET value = '1'"
                        )
                        state_effect = "legacy_bound"
                    elif domain == "issues":
                        if not isinstance(legacy, list):
                            raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 Issue 证据无效。", status_code=503)
                        connection.execute("DELETE FROM issue_states")
                        for item in legacy:
                            issue_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
                            if not issue_id:
                                raise ControlPlaneError("STATE_UNAVAILABLE", "迁移 Issue 缺少 id。", status_code=503)
                            connection.execute(
                                """
                                INSERT INTO issue_states(
                                    issue_id, status, severity, issue_json, source, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, 'migration_reconciliation', ?, ?)
                                """,
                                (
                                    issue_id, str(item.get("status") or "open"),
                                    str(item.get("severity") or "warn"), _json(item), now, now,
                                ),
                            )
                            if str(item.get("status") or "") == "accepted":
                                decision_id = str(uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    f"{self.context.workspace_id}:migration-accepted-risk:{issue_id}",
                                ))
                                connection.execute(
                                    """
                                    INSERT OR IGNORE INTO policy_decisions(
                                        decision_id, issue_id, decision_type, decision_json,
                                        actor_json, created_at
                                    ) VALUES (?, ?, 'accept_risk', ?, ?, ?)
                                    """,
                                    (
                                        decision_id, issue_id,
                                        _json({
                                            "risk_class": item.get("risk_class"),
                                            "reason": item.get("accept_reason"),
                                            "source": "migration_reconciliation",
                                        }),
                                        _json(actor), now,
                                    ),
                                )
                        connection.execute(
                            "INSERT INTO control_meta(key, value) VALUES ('issue_v1_imported', '1') "
                            "ON CONFLICT(key) DO UPDATE SET value = '1'"
                        )
                        state_effect = "legacy_bound"
                    elif domain == "orphan" and isinstance(legacy, dict) and str(legacy.get("kind") or "") == "legacy_pipeline_checkpoint":
                        checkpoint = legacy.get("state")
                        if not isinstance(checkpoint, dict):
                            raise ControlPlaneError(
                                "STATE_UNAVAILABLE",
                                "旧 Pipeline checkpoint 缺少可审计状态，不能导入。",
                                status_code=503,
                            )
                        legacy_status = str(checkpoint.get("status") or "").strip().lower()
                        terminal_status = {
                            "complete": "succeeded",
                            "completed": "succeeded",
                            "failed": "failed",
                            "cancelled": "cancelled",
                            "paused": "paused",
                        }.get(legacy_status)
                        if terminal_status is None:
                            raise ControlPlaneError(
                                "COMMAND_INVALID",
                                "仅能导入已结束的旧 Pipeline checkpoint；运行中状态必须保留 orphan 并由显式 V2 Command 恢复。",
                                status_code=409,
                            )
                        imported_operation_id = str(uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{self.context.workspace_id}:legacy-pipeline-checkpoint:{conflict_id}",
                        ))
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO operations(
                                operation_id, parent_operation_id, kind, status, start_command,
                                fencing_token, created_at, updated_at, completed_at, message, error_json
                            ) VALUES (?, NULL, 'pipeline.legacy_checkpoint', ?, ?, 0, ?, ?, ?, ?, ?)
                            """,
                            (
                                imported_operation_id,
                                terminal_status,
                                str(checkpoint.get("current_stage") or ""),
                                now,
                                now,
                                now,
                                "由已确认的 V1 Pipeline checkpoint 导入；仅供历史审计，不可恢复执行。",
                                _json({"source": "migration_reconciliation", "legacy_status": legacy_status}),
                            ),
                        )
                        state_effect = "legacy_terminal_pipeline_imported"
                    else:
                        raise ControlPlaneError("COMMAND_INVALID", f"不支持绑定迁移领域: {domain}", status_code=400)
                elif resolution_name == "mark_failed" and domain == "goal":
                    connection.execute(
                        "UPDATE goal_state SET status = 'failed', source = 'migration_reconciliation', updated_at = ? "
                        "WHERE singleton = 1",
                        (now,),
                    )
                    state_effect = "goal_marked_failed"
                resolution_value = {
                    "choice": resolution_name,
                    "state_effect": state_effect,
                    "original_evidence_preserved": True,
                    "backup_path": backup_path.relative_to(self.context.root).as_posix(),
                    "backup_sha256": backup_sha256,
                }
                connection.execute(
                    """
                    UPDATE migration_conflicts
                    SET status = 'resolved', resolution_json = ?, actor_json = ?, reason = ?, resolved_at = ?
                    WHERE conflict_id = ? AND status = 'open'
                    """,
                    (_json(resolution_value), _json(actor), str(reason).strip(), now, conflict_id),
                )
                decision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"migration-resolution:{conflict_id}"))
                connection.execute(
                    """
                    INSERT INTO policy_decisions(
                        decision_id, issue_id, decision_type, decision_json, actor_json, created_at
                    ) VALUES (?, ?, 'migration_reconciliation', ?, ?, ?)
                    """,
                    (decision_id, f"migration:{conflict_id}", _json(resolution_value), _json(actor), now),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "MigrationConflictResolved", "MigrationConflict", conflict_id,
                    {"resolution": resolution_name, "reason": str(reason).strip(), "actor": actor},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return next(item for item in self.migration_conflicts() if item["conflict_id"] == conflict_id)

    def record_gate_evaluation(
        self,
        *,
        command: str,
        verdict: str,
        input_fingerprint: str,
        findings: list[dict[str, Any]],
        source: str,
    ) -> dict[str, Any]:
        command_name = str(command or "").strip()
        verdict_name = str(verdict or "").strip().lower()
        fingerprint = str(input_fingerprint or "").strip()
        if not command_name or verdict_name not in {"pass", "block", "error"} or not fingerprint:
            raise ControlPlaneError("STATE_UNAVAILABLE", "GateEvaluation 输入无效，已拒绝记录。", status_code=503)
        normalized = [dict(item) for item in findings if isinstance(item, dict)]
        evaluation_id = str(uuid.uuid4())
        created_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                source_revision = self._revision(connection)
                connection.execute(
                    """
                    INSERT INTO gate_evaluations(
                        evaluation_id, command, verdict, input_fingerprint,
                        findings_json, source, source_revision, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evaluation_id, command_name, verdict_name, fingerprint,
                        _json(normalized), str(source or "v2_quality_revalidate"),
                        source_revision, created_at,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "GateEvaluated", "GateEvaluation", evaluation_id,
                    {"command": command_name, "verdict": verdict_name, "finding_count": len(normalized)},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "evaluation_id": evaluation_id,
            "command": command_name,
            "verdict": verdict_name,
            "input_fingerprint": fingerprint,
            "findings": normalized,
            "source": str(source or "v2_quality_revalidate"),
            "source_revision": source_revision,
            "created_at": created_at,
        }

    def gate_evaluations(self, *, command: str = "", limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = max(1, min(int(limit), 200))
        with self._connection() as connection:
            if command:
                rows = connection.execute(
                    "SELECT * FROM gate_evaluations WHERE command = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (str(command), capped_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM gate_evaluations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (capped_limit,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["findings"] = _decode(item.pop("findings_json", ""), [])
            result.append(item)
        return result

    def issue_gate_receipt(
        self,
        *,
        verdict: str,
        gate_input_fingerprint: str,
        artifact_path: str,
        artifact_sha256: str,
        rules_version: str,
        findings: list[Any] | None = None,
        policy_decisions: list[Any] | None = None,
    ) -> dict[str, Any]:
        if verdict not in {"pass", "block"} or not gate_input_fingerprint or not artifact_sha256:
            raise ControlPlaneError("STATE_UNAVAILABLE", "GateReceipt 输入无效，已拒绝签发。", status_code=503)
        receipt_id = str(uuid.uuid4())
        created_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    INSERT INTO gate_receipts(
                        receipt_id, verdict, gate_input_fingerprint, artifact_path,
                        artifact_sha256, rules_version, findings_json,
                        policy_decisions_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        verdict,
                        gate_input_fingerprint,
                        artifact_path,
                        artifact_sha256,
                        rules_version,
                        _json(findings or []),
                        _json(policy_decisions or []),
                        created_at,
                    ),
                )
                self._event(
                    connection,
                    revision,
                    "GateReceiptIssued",
                    "GateReceipt",
                    receipt_id,
                    {
                        "verdict": verdict,
                        "gate_input_fingerprint": gate_input_fingerprint,
                        "artifact_path": artifact_path,
                        "rules_version": rules_version,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.gate_receipt(receipt_id) or {}

    def gate_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM gate_receipts WHERE receipt_id = ?",
                (str(receipt_id or ""),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["findings"] = _decode(result.pop("findings_json", ""), [])
        result["policy_decisions"] = _decode(result.pop("policy_decisions_json", ""), [])
        return result

    def latest_gate_receipt(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT receipt_id FROM gate_receipts ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self.gate_receipt(str(row["receipt_id"])) if row is not None else None

    def register_material_upload(
        self,
        *,
        staged_path: str,
        filename: str,
        sha256: str,
        size_bytes: int,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        relative = str(staged_path or "").strip().replace("\\", "/")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ControlPlaneError("UPLOAD_INVALID", "材料暂存路径无效。", status_code=400)
        if not sha256 or int(size_bytes) <= 0:
            raise ControlPlaneError("UPLOAD_INVALID", "材料上传内容为空或摘要无效。", status_code=400)
        token = str(uuid.uuid4())
        created_at = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(60, ttl_seconds))).isoformat(
            timespec="milliseconds"
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    INSERT INTO material_upload_tokens(
                        upload_token, staged_path, filename, sha256, size_bytes,
                        status, created_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL)
                    """,
                    (token, relative, filename[:255], sha256, int(size_bytes), created_at, expires_at),
                )
                self._event(
                    connection,
                    revision,
                    "MaterialUploadStaged",
                    "MaterialUpload",
                    token,
                    {"filename": filename[:255], "sha256": sha256, "size_bytes": int(size_bytes)},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.material_upload(token) or {}

    def material_upload(self, upload_token: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM material_upload_tokens WHERE upload_token = ?",
                (str(upload_token or ""),),
            ).fetchone()
        return dict(row) if row is not None else None

    def consume_material_upload(self, upload_token: str) -> dict[str, Any]:
        token = str(upload_token or "").strip()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM material_upload_tokens WHERE upload_token = ?",
                    (token,),
                ).fetchone()
                if row is None:
                    raise ControlPlaneError("UPLOAD_TOKEN_INVALID", "上传 token 不存在。", status_code=404)
                if str(row["status"]) != "pending":
                    raise ControlPlaneError("UPLOAD_TOKEN_CONSUMED", "上传 token 已使用。", status_code=409)
                try:
                    expires_at = datetime.fromisoformat(str(row["expires_at"]))
                except ValueError as exc:
                    raise ControlPlaneError("STATE_UNAVAILABLE", "上传 token 到期时间无效。", status_code=503) from exc
                if expires_at <= datetime.now(timezone.utc):
                    revision = self._bump_revision(connection)
                    connection.execute(
                        "UPDATE material_upload_tokens SET status = 'expired' WHERE upload_token = ?",
                        (token,),
                    )
                    self._event(
                        connection,
                        revision,
                        "MaterialUploadExpired",
                        "MaterialUpload",
                        token,
                        {},
                    )
                    connection.commit()
                    raise ControlPlaneError("UPLOAD_TOKEN_EXPIRED", "上传 token 已过期。", status_code=409)
                now = _now()
                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    UPDATE material_upload_tokens
                    SET status = 'consumed', consumed_at = ?
                    WHERE upload_token = ? AND status = 'pending'
                    """,
                    (now, token),
                )
                self._event(
                    connection,
                    revision,
                    "MaterialUploadConsumed",
                    "MaterialUpload",
                    token,
                    {"sha256": str(row["sha256"]), "filename": str(row["filename"])},
                )
                connection.commit()
                result = dict(row)
                result.update({"status": "consumed", "consumed_at": now})
                return result
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def record_material_submission(
        self,
        *,
        item_id: str,
        upload: dict[str, Any],
        actor: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        material_id = str(item_id or "").strip()
        token = str(upload.get("upload_token") or "").strip()
        filename = str(upload.get("filename") or "").strip()[:255]
        sha256 = str(upload.get("sha256") or "").strip()
        try:
            size_bytes = int(upload.get("size_bytes") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if not material_id or not token or not filename or not sha256 or size_bytes <= 0:
            raise ControlPlaneError("COMMAND_INVALID", "材料提交记录无效。", status_code=400)
        submission_id = str(uuid.uuid4())
        created_at = _now()
        actor_value = {
            "type": str(actor.get("type") or "")[:64],
            "id": str(actor.get("id") or "")[:128],
            "role": str(actor.get("role") or "")[:32],
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO material_submissions(
                        submission_id, item_id, upload_token, filename, sha256,
                        size_bytes, actor_json, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id, material_id, token, filename, sha256,
                        size_bytes, _json(actor_value), str(source or "materials.upload"), created_at,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "MaterialSubmitted", "MaterialSubmission", submission_id,
                    {"item_id": material_id, "filename": filename, "sha256": sha256, "size_bytes": size_bytes},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "submission_id": submission_id,
            "item_id": material_id,
            "upload_token": token,
            "filename": filename,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "actor": actor_value,
            "source": str(source or "materials.upload"),
            "created_at": created_at,
        }

    def material_submissions(self, *, item_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        capped_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            if item_id:
                rows = connection.execute(
                    "SELECT * FROM material_submissions WHERE item_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(item_id), capped_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM material_submissions ORDER BY created_at DESC LIMIT ?",
                    (capped_limit,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["actor"] = _decode(item.pop("actor_json", ""), {})
            result.append(item)
        return result

    def ensure_material_states(self, items: list[dict[str, Any]]) -> int:
        rows = [dict(item) for item in items if isinstance(item, dict) and str(item.get("item_id") or "").strip()]
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                imported = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'materials_v1_imported'"
                ).fetchone()
                if imported is not None:
                    connection.commit()
                    return 0
                existing_rows = connection.execute(
                    "SELECT item_id, response_status, lifecycle_status, evidence_status FROM material_states "
                    "ORDER BY item_id"
                ).fetchall()
                legacy_projection = sorted(
                    (
                        str(item.get("item_id") or ""),
                        str(item.get("response_status") or "deferred"),
                        str(item.get("lifecycle_status") or "missing"),
                        str(item.get("evidence_status") or "missing"),
                    )
                    for item in rows
                )
                authoritative_projection = [tuple(str(value) for value in row) for row in existing_rows]
                if existing_rows and legacy_projection != authoritative_projection:
                    authoritative = [dict(row) for row in existing_rows]
                    connection.rollback()
                    conflict = self.record_migration_conflict(
                        domain="materials",
                        legacy=rows,
                        authoritative=authoritative,
                        reason="V1 材料状态与 control.db 权威状态不一致。",
                    )
                    raise ControlPlaneError(
                        "MIGRATION_RECONCILIATION_REQUIRED",
                        "检测到 V1/V2 材料状态冲突，需管理员处理。",
                        details={"conflict_id": conflict["conflict_id"]},
                    )
                inserted = 0
                for item in rows:
                    item_id = str(item.get("item_id") or "").strip()
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO material_states(
                            item_id, response_status, lifecycle_status, evidence_status,
                            item_json, source, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'v1_import', ?, ?)
                        """,
                        (
                            item_id,
                            str(item.get("response_status") or "deferred"),
                            str(item.get("lifecycle_status") or "missing"),
                            str(item.get("evidence_status") or "missing"),
                            _json(item),
                            now,
                            now,
                        ),
                    )
                    inserted += max(0, int(cursor.rowcount or 0))
                connection.execute("INSERT INTO control_meta(key, value) VALUES ('materials_v1_imported', '1')")
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "MaterialStateImported",
                    "Materials",
                    self.context.workspace_id,
                    {"count": inserted, "source": "v1_import"},
                )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def material_states(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM material_states ORDER BY item_id").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            item = _decode(value.pop("item_json", ""), {})
            if not isinstance(item, dict):
                item = {}
            item.update(
                {
                    "item_id": value["item_id"],
                    "response_status": value["response_status"],
                    "lifecycle_status": value["lifecycle_status"],
                    "evidence_status": value["evidence_status"],
                    "control_source": value["source"],
                    "control_updated_at": value["updated_at"],
                }
            )
            result.append(item)
        return result

    def record_material_verification(
        self,
        *,
        item_id: str,
        verification_type: str,
        verdict: str,
        verification: dict[str, Any],
        actor: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        material_id = str(item_id or "").strip()
        kind = str(verification_type or "").strip()
        decision = str(verdict or "").strip().lower()
        if not material_id or not kind or decision not in {"verified", "rejected", "uploaded"}:
            raise ControlPlaneError("COMMAND_INVALID", "材料核验记录无效。", status_code=400)
        verification_id = str(uuid.uuid4())
        created_at = _now()
        payload = dict(verification) if isinstance(verification, dict) else {}
        actor_value = {
            "type": str(actor.get("type") or "")[:64],
            "id": str(actor.get("id") or "")[:128],
            "role": str(actor.get("role") or "")[:32],
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO material_verifications(
                        verification_id, item_id, verification_type, verdict,
                        verification_json, actor_json, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification_id, material_id, kind, decision,
                        _json(payload), _json(actor_value), str(source or "v2_command"), created_at,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "MaterialVerified", "MaterialVerification", verification_id,
                    {"item_id": material_id, "verification_type": kind, "verdict": decision},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "verification_id": verification_id,
            "item_id": material_id,
            "verification_type": kind,
            "verdict": decision,
            "verification": payload,
            "actor": actor_value,
            "source": str(source or "v2_command"),
            "created_at": created_at,
        }

    def material_verifications(self, *, item_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        capped_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            if item_id:
                rows = connection.execute(
                    "SELECT * FROM material_verifications WHERE item_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(item_id), capped_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM material_verifications ORDER BY created_at DESC LIMIT ?",
                    (capped_limit,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["verification"] = _decode(item.pop("verification_json", ""), {})
            item["actor"] = _decode(item.pop("actor_json", ""), {})
            result.append(item)
        return result

    def material_state(self, item_id: str) -> dict[str, Any] | None:
        wanted = str(item_id or "").strip()
        return next((item for item in self.material_states() if item.get("item_id") == wanted), None)

    def upsert_material_state(self, item: dict[str, Any], *, source: str = "v2_command") -> dict[str, Any]:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            raise ControlPlaneError("COMMAND_INVALID", "材料状态缺少 item_id。", status_code=400)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT created_at FROM material_states WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                created_at = str(existing["created_at"]) if existing is not None else now
                connection.execute(
                    """
                    INSERT INTO material_states(
                        item_id, response_status, lifecycle_status, evidence_status,
                        item_json, source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        response_status = excluded.response_status,
                        lifecycle_status = excluded.lifecycle_status,
                        evidence_status = excluded.evidence_status,
                        item_json = excluded.item_json,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item_id,
                        str(item.get("response_status") or "deferred"),
                        str(item.get("lifecycle_status") or "missing"),
                        str(item.get("evidence_status") or "missing"),
                        _json(item),
                        source,
                        created_at,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('materials_v1_imported', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "MaterialStateChanged",
                    "Material",
                    item_id,
                    {
                        "response_status": str(item.get("response_status") or "deferred"),
                        "lifecycle_status": str(item.get("lifecycle_status") or "missing"),
                        "source": source,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.material_state(item_id) or {}

    def ensure_issue_states(self, issues: list[dict[str, Any]]) -> int:
        """One-time V1 import; later file projections can never overwrite V2 state implicitly."""
        rows = [dict(item) for item in issues if isinstance(item, dict) and str(item.get("id") or "").strip()]
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                imported = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'issue_v1_imported'"
                ).fetchone()
                if imported is not None:
                    connection.commit()
                    return 0
                existing_rows = connection.execute(
                    "SELECT issue_id, status, severity FROM issue_states ORDER BY issue_id"
                ).fetchall()
                legacy_projection = sorted(
                    (
                        str(item.get("id") or ""),
                        str(item.get("status") or "open"),
                        str(item.get("severity") or "warn"),
                    )
                    for item in rows
                )
                authoritative_projection = [tuple(str(value) for value in row) for row in existing_rows]
                if existing_rows and legacy_projection != authoritative_projection:
                    authoritative = [dict(row) for row in existing_rows]
                    connection.rollback()
                    conflict = self.record_migration_conflict(
                        domain="issues",
                        legacy=rows,
                        authoritative=authoritative,
                        reason="V1 Issue 状态与 control.db 权威状态不一致。",
                    )
                    raise ControlPlaneError(
                        "MIGRATION_RECONCILIATION_REQUIRED",
                        "检测到 V1/V2 Issue 状态冲突，需管理员处理。",
                        details={"conflict_id": conflict["conflict_id"]},
                    )
                for item in rows:
                    issue_id = str(item.get("id") or "").strip()
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO issue_states(
                            issue_id, status, severity, issue_json, source, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'v1_import', ?, ?)
                        """,
                        (
                            issue_id,
                            str(item.get("status") or "open"),
                            str(item.get("severity") or "warn"),
                            _json(item),
                            str(item.get("created_at") or now),
                            str(item.get("updated_at") or now),
                        ),
                    )
                    if str(item.get("status") or "") == "accepted":
                        decision_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{self.context.workspace_id}:v1-accepted-risk:{issue_id}",
                            )
                        )
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO policy_decisions(
                                decision_id, issue_id, decision_type, decision_json, actor_json, created_at
                            ) VALUES (?, ?, 'accept_risk', ?, ?, ?)
                            """,
                            (
                                decision_id,
                                issue_id,
                                _json(
                                    {
                                        "risk_class": item.get("risk_class"),
                                        "reason": item.get("accept_reason"),
                                        "accepted_at": item.get("accepted_at"),
                                        "source": "v1_import",
                                    }
                                ),
                                _json({"type": "legacy", "id": item.get("accepted_by") or "unknown"}),
                                str(item.get("accepted_at") or item.get("updated_at") or now),
                            ),
                        )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('issue_v1_imported', '1')"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "IssueStateImported",
                    "Issues",
                    self.context.workspace_id,
                    {"count": len(rows), "source": "v1_import"},
                )
                connection.commit()
                return len(rows)
            except Exception:
                connection.rollback()
                raise

    def issue_v1_import_pending(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'issue_v1_imported'"
            ).fetchone()
        return row is None

    def issue_states(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM issue_states ORDER BY created_at, issue_id").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            item = _decode(value.pop("issue_json", ""), {})
            if not isinstance(item, dict):
                item = {}
            item.update(
                {
                    "id": value["issue_id"],
                    "status": value["status"],
                    "severity": value["severity"],
                    "control_source": value["source"],
                    "control_updated_at": value["updated_at"],
                }
            )
            result.append(item)
        return result

    def replace_issue_states(self, issues: list[dict[str, Any]], *, source: str = "v2_projection") -> int:
        rows = [dict(item) for item in issues if isinstance(item, dict) and str(item.get("id") or "").strip()]
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM issue_states")
                for item in rows:
                    issue_id = str(item.get("id") or "").strip()
                    connection.execute(
                        """
                        INSERT INTO issue_states(
                            issue_id, status, severity, issue_json, source, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            issue_id,
                            str(item.get("status") or "open"),
                            str(item.get("severity") or "warn"),
                            _json(item),
                            source,
                            str(item.get("created_at") or now),
                            str(item.get("updated_at") or now),
                        ),
                    )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('issue_v1_imported', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "IssueStateProjected",
                    "Issues",
                    self.context.workspace_id,
                    {"count": len(rows), "source": source},
                )
                connection.commit()
                return len(rows)
            except Exception:
                connection.rollback()
                raise

    def update_issue_state_with_policy(
        self,
        issue: dict[str, Any],
        *,
        decision_type: str,
        decision: dict[str, Any],
        actor: dict[str, Any],
        source: str = "v2_command",
    ) -> dict[str, Any]:
        """Atomically update one authoritative Issue and append its PolicyDecision."""
        item = dict(issue) if isinstance(issue, dict) else {}
        issue_id = str(item.get("id") or "").strip()
        if not issue_id:
            raise ControlPlaneError("COMMAND_INVALID", "Issue 缺少 id。", status_code=400)
        decision_id = str(uuid.uuid4())
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT created_at FROM issue_states WHERE issue_id = ?",
                    (issue_id,),
                ).fetchone()
                if not existing:
                    raise ControlPlaneError("ISSUE_NOT_FOUND", f"未找到问题: {issue_id}", status_code=404)
                connection.execute(
                    """
                    UPDATE issue_states
                    SET status = ?, severity = ?, issue_json = ?, source = ?, updated_at = ?
                    WHERE issue_id = ?
                    """,
                    (
                        str(item.get("status") or "open"),
                        str(item.get("severity") or "warn"),
                        _json(item),
                        source,
                        str(item.get("updated_at") or now),
                        issue_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO policy_decisions(
                        decision_id, issue_id, decision_type, decision_json, actor_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (decision_id, issue_id, decision_type, _json(decision), _json(actor), now),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "IssueStateChanged",
                    "Issue",
                    issue_id,
                    {"status": str(item.get("status") or "open"), "source": source},
                )
                self._event(
                    connection,
                    revision,
                    "PolicyDecisionRecorded",
                    "PolicyDecision",
                    decision_id,
                    {"issue_id": issue_id, "decision_type": decision_type},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "decision_id": decision_id,
            "issue_id": issue_id,
            "decision_type": decision_type,
            "decision": dict(decision),
            "actor": dict(actor),
            "created_at": now,
        }

    def record_policy_decision(
        self,
        *,
        issue_id: str,
        decision_type: str,
        decision: dict[str, Any],
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        decision_id = str(uuid.uuid4())
        created_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO policy_decisions(
                        decision_id, issue_id, decision_type, decision_json, actor_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (decision_id, issue_id, decision_type, _json(decision), _json(actor), created_at),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "PolicyDecisionRecorded",
                    "PolicyDecision",
                    decision_id,
                    {"issue_id": issue_id, "decision_type": decision_type},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "decision_id": decision_id,
            "issue_id": issue_id,
            "decision_type": decision_type,
            "decision": dict(decision),
            "actor": dict(actor),
            "created_at": created_at,
        }

    def policy_decisions(self, *, issue_id: str = "") -> list[dict[str, Any]]:
        with self._connection() as connection:
            if issue_id:
                rows = connection.execute(
                    "SELECT * FROM policy_decisions WHERE issue_id = ? ORDER BY created_at, rowid",
                    (issue_id,),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM policy_decisions ORDER BY created_at, rowid").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["decision"] = _decode(value.pop("decision_json", ""), {})
            value["actor"] = _decode(value.pop("actor_json", ""), {})
            result.append(value)
        return result

    def upsert_artifact_state(self, manifest: dict[str, Any]) -> dict[str, Any]:
        stored = self.upsert_artifact_states([manifest])
        return stored[0] if stored else {}

    def upsert_artifact_states(self, manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for manifest in manifests:
            artifact_key = str(manifest.get("artifact_key") or manifest.get("path") or "").strip()
            status = str(manifest.get("status") or "").strip()
            if not artifact_key or status not in {"ready", "stale", "missing"}:
                raise ControlPlaneError("STATE_UNAVAILABLE", "Artifact manifest 无效。", status_code=503)
            payload = dict(manifest)
            payload["artifact_key"] = artifact_key
            payloads.append(payload)
        if not payloads:
            return []
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                revision = self._bump_revision(connection)
                for payload in payloads:
                    artifact_key = str(payload["artifact_key"])
                    status = str(payload["status"])
                    existing = connection.execute(
                        "SELECT created_at FROM artifact_states WHERE artifact_key = ?",
                        (artifact_key,),
                    ).fetchone()
                    created_at = str(existing["created_at"]) if existing else now
                    connection.execute(
                        """
                        INSERT INTO artifact_states(
                            artifact_key, artifact_path, artifact_kind, status, sha256,
                            producer, input_fingerprint, manifest_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(artifact_key) DO UPDATE SET
                            artifact_path = excluded.artifact_path,
                            artifact_kind = excluded.artifact_kind,
                            status = excluded.status,
                            sha256 = excluded.sha256,
                            producer = excluded.producer,
                            input_fingerprint = excluded.input_fingerprint,
                            manifest_json = excluded.manifest_json,
                            updated_at = excluded.updated_at
                        """,
                        (
                            artifact_key,
                            str(payload.get("path") or artifact_key),
                            str(payload.get("kind") or "file"),
                            status,
                            str(payload.get("sha256") or ""),
                            str(payload.get("producer") or ""),
                            str(payload.get("input_fingerprint") or ""),
                            _json(payload),
                            created_at,
                            now,
                        ),
                    )
                    self._event(
                        connection,
                        revision,
                        "ArtifactStateChanged",
                        "Artifact",
                        artifact_key,
                        {"status": status, "producer": str(payload.get("producer") or "")},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        states = {item["artifact_key"]: item for item in self.artifact_states()}
        return [states[str(payload["artifact_key"])] for payload in payloads]

    def artifact_state(self, artifact_key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_states WHERE artifact_key = ?",
                (artifact_key,),
            ).fetchone()
        return self._artifact_row(row) if row else None

    def artifact_states(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM artifact_states ORDER BY artifact_key"
            ).fetchall()
        return [self._artifact_row(row) for row in rows]

    def mark_artifact_states_stale(
        self,
        artifact_keys: list[str],
        *,
        reason: str,
        source_command: str = "",
    ) -> list[dict[str, Any]]:
        keys = sorted({str(key).strip() for key in artifact_keys if str(key).strip()})
        if not keys:
            return []
        now = _now()
        changed: list[str] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"SELECT * FROM artifact_states WHERE artifact_key IN ({','.join('?' for _ in keys)})",
                    keys,
                ).fetchall()
                candidates = [row for row in rows if str(row["status"]) != "stale"]
                if not candidates:
                    connection.commit()
                    return []
                revision = self._bump_revision(connection)
                for row in candidates:
                    payload = _decode(row["manifest_json"], {})
                    manifest = dict(payload) if isinstance(payload, dict) else {}
                    manifest.update(
                        {
                            "status": "stale",
                            "stale_reason": reason,
                            "stale_source_command": source_command,
                        }
                    )
                    artifact_key = str(row["artifact_key"])
                    connection.execute(
                        "UPDATE artifact_states SET status = 'stale', manifest_json = ?, updated_at = ? WHERE artifact_key = ?",
                        (_json(manifest), now, artifact_key),
                    )
                    self._event(
                        connection,
                        revision,
                        "ArtifactStateChanged",
                        "Artifact",
                        artifact_key,
                        {"status": "stale", "reason": reason, "source_command": source_command},
                    )
                    changed.append(artifact_key)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        states = {item["artifact_key"]: item for item in self.artifact_states()}
        return [states[key] for key in changed]

    @staticmethod
    def _artifact_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        payload = _decode(value.pop("manifest_json", ""), {})
        item = dict(payload) if isinstance(payload, dict) else {}
        item.update(
            {
                "artifact_key": value["artifact_key"],
                "path": value["artifact_path"],
                "kind": value["artifact_kind"],
                "status": value["status"],
                "sha256": value["sha256"],
                "producer": value["producer"],
                "input_fingerprint": value["input_fingerprint"],
                "created_at": value["created_at"],
                "updated_at": value["updated_at"],
            }
        )
        return item

    def ensure_goal_state(self, goal: dict[str, Any] | None) -> int:
        """Import the V1 Goal once; absence is also recorded to prevent later stale-file takeover."""
        value = dict(goal) if isinstance(goal, dict) else None
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                imported = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'goal_v1_imported'"
                ).fetchone()
                if imported is not None:
                    connection.commit()
                    return 0
                existing = connection.execute(
                    "SELECT goal_id, status, goal_json FROM goal_state WHERE singleton = 1"
                ).fetchone()
                if existing is not None and value is not None:
                    legacy_projection = (
                        str(value.get("goal_id") or value.get("id") or ""),
                        str(value.get("status") or "pending"),
                    )
                    authoritative_projection = (str(existing["goal_id"]), str(existing["status"]))
                    if legacy_projection != authoritative_projection:
                        authoritative = _decode(str(existing["goal_json"]), {})
                        connection.rollback()
                        conflict = self.record_migration_conflict(
                            domain="goal",
                            legacy=value,
                            authoritative=authoritative,
                            reason="V1 Goal 与 control.db 权威 Goal 不一致。",
                        )
                        raise ControlPlaneError(
                            "MIGRATION_RECONCILIATION_REQUIRED",
                            "检测到 V1/V2 Goal 状态冲突，需管理员处理。",
                            details={"conflict_id": conflict["conflict_id"]},
                        )
                inserted = 0
                if value is not None and existing is None:
                    goal_id = str(value.get("goal_id") or value.get("id") or "").strip()
                    if not goal_id:
                        raise ControlPlaneError("STATE_UNAVAILABLE", "V1 Goal 缺少 goal_id，拒绝导入。", status_code=503)
                    connection.execute(
                        """
                        INSERT INTO goal_state(
                            singleton, goal_id, status, goal_json, source, created_at, updated_at
                        ) VALUES (1, ?, ?, ?, 'v1_import', ?, ?)
                        """,
                        (
                            goal_id,
                            str(value.get("status") or "pending"),
                            _json(value),
                            str(value.get("created_at") or now),
                            str(value.get("updated_at") or now),
                        ),
                    )
                    inserted = 1
                connection.execute("INSERT INTO control_meta(key, value) VALUES ('goal_v1_imported', '1')")
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "GoalStateImported",
                    "Goal",
                    str((value or {}).get("goal_id") or self.context.workspace_id),
                    {"count": inserted, "source": "v1_import"},
                )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def goal_state(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM goal_state WHERE singleton = 1").fetchone()
        if row is None:
            return None
        value = dict(row)
        goal = _decode(value.pop("goal_json", ""), {})
        if not isinstance(goal, dict):
            raise ControlPlaneError("STATE_UNAVAILABLE", "Goal 控制状态损坏。", status_code=503)
        goal.update(
            {
                "goal_id": value["goal_id"],
                "status": value["status"],
                "control_source": value["source"],
                "control_updated_at": value["updated_at"],
            }
        )
        return goal

    def upsert_goal_state(self, goal: dict[str, Any], *, source: str = "v2_projection") -> dict[str, Any]:
        value = dict(goal)
        goal_id = str(value.get("goal_id") or value.get("id") or "").strip()
        if not goal_id:
            raise ControlPlaneError("COMMAND_INVALID", "Goal 状态缺少 goal_id。", status_code=400)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute("SELECT created_at FROM goal_state WHERE singleton = 1").fetchone()
                created_at = str(existing["created_at"]) if existing is not None else str(value.get("created_at") or now)
                connection.execute(
                    """
                    INSERT INTO goal_state(
                        singleton, goal_id, status, goal_json, source, created_at, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        goal_id = excluded.goal_id,
                        status = excluded.status,
                        goal_json = excluded.goal_json,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        goal_id,
                        str(value.get("status") or "pending"),
                        _json(value),
                        source,
                        created_at,
                        str(value.get("updated_at") or now),
                    ),
                )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('goal_v1_imported', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "GoalStateChanged",
                    "Goal",
                    goal_id,
                    {"status": str(value.get("status") or "pending"), "source": source},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.goal_state() or {}

    def ensure_repair_job_state(self, job: dict[str, Any] | None) -> int:
        value = dict(job) if isinstance(job, dict) and job else None
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                imported = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'repair_job_v1_imported'"
                ).fetchone()
                if imported is not None:
                    connection.commit()
                    return 0
                existing = connection.execute(
                    "SELECT job_id, status, phase, job_json FROM repair_job_state WHERE singleton = 1"
                ).fetchone()
                if existing is not None and value is not None:
                    legacy_projection = (
                        str(value.get("job_id") or ""), str(value.get("status") or "awaiting_confirmation"),
                        str(value.get("phase") or "awaiting_confirmation"),
                    )
                    authoritative_projection = (str(existing["job_id"]), str(existing["status"]), str(existing["phase"]))
                    if legacy_projection != authoritative_projection:
                        connection.rollback()
                        conflict = self.record_migration_conflict(
                            domain="repair_job", legacy=value,
                            authoritative=_decode(str(existing["job_json"]), {}),
                            reason="V1 RepairJob 与 control.db 权威状态不一致。",
                        )
                        raise ControlPlaneError(
                            "MIGRATION_RECONCILIATION_REQUIRED",
                            "检测到 V1/V2 RepairJob 状态冲突，需管理员处理。",
                            details={"conflict_id": conflict["conflict_id"]},
                        )
                inserted = 0
                if value is not None and existing is None:
                    job_id = str(value.get("job_id") or "").strip()
                    if not job_id:
                        raise ControlPlaneError("STATE_UNAVAILABLE", "V1 RepairJob 缺少 job_id。", status_code=503)
                    connection.execute(
                        """
                        INSERT INTO repair_job_state(
                            singleton, job_id, status, phase, job_json, source, created_at, updated_at
                        ) VALUES (1, ?, ?, ?, ?, 'v1_import', ?, ?)
                        """,
                        (
                            job_id,
                            str(value.get("status") or "awaiting_confirmation"),
                            str(value.get("phase") or "awaiting_confirmation"),
                            _json(value),
                            str(value.get("created_at") or now),
                            str(value.get("updated_at") or now),
                        ),
                    )
                    inserted = 1
                connection.execute("INSERT INTO control_meta(key, value) VALUES ('repair_job_v1_imported', '1')")
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "RepairJobImported",
                    "RepairJob",
                    str((value or {}).get("job_id") or self.context.workspace_id),
                    {"count": inserted, "source": "v1_import"},
                )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def repair_job_state(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM repair_job_state WHERE singleton = 1").fetchone()
        if row is None:
            return {}
        value = dict(row)
        job = _decode(value.pop("job_json", ""), {})
        if not isinstance(job, dict):
            raise ControlPlaneError("STATE_UNAVAILABLE", "RepairJob 控制状态损坏。", status_code=503)
        job.update(
            {
                "job_id": value["job_id"],
                "status": value["status"],
                "phase": value["phase"],
                "control_source": value["source"],
                "control_updated_at": value["updated_at"],
            }
        )
        return job

    def upsert_repair_job_state(self, job: dict[str, Any], *, source: str = "v2_projection") -> dict[str, Any]:
        value = dict(job)
        job_id = str(value.get("job_id") or "").strip()
        if not job_id:
            raise ControlPlaneError("COMMAND_INVALID", "RepairJob 状态缺少 job_id。", status_code=400)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute("SELECT created_at FROM repair_job_state WHERE singleton = 1").fetchone()
                created_at = str(existing["created_at"]) if existing is not None else str(value.get("created_at") or now)
                connection.execute(
                    """
                    INSERT INTO repair_job_state(
                        singleton, job_id, status, phase, job_json, source, created_at, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        job_id = excluded.job_id,
                        status = excluded.status,
                        phase = excluded.phase,
                        job_json = excluded.job_json,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        job_id,
                        str(value.get("status") or "awaiting_confirmation"),
                        str(value.get("phase") or "awaiting_confirmation"),
                        _json(value),
                        source,
                        created_at,
                        str(value.get("updated_at") or now),
                    ),
                )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('repair_job_v1_imported', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "RepairJobChanged",
                    "RepairJob",
                    job_id,
                    {
                        "status": str(value.get("status") or "awaiting_confirmation"),
                        "phase": str(value.get("phase") or "awaiting_confirmation"),
                        "source": source,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.repair_job_state()

    def ensure_agent_activity_state(self, activity: dict[str, Any] | None) -> int:
        value = dict(activity) if isinstance(activity, dict) and activity else None
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                imported = connection.execute(
                    "SELECT value FROM control_meta WHERE key = 'agent_activity_v1_imported'"
                ).fetchone()
                if imported is not None:
                    connection.commit()
                    return 0
                existing = connection.execute(
                    "SELECT status, phase, activity_json FROM agent_activity_state WHERE singleton = 1"
                ).fetchone()
                if existing is not None and value is not None:
                    legacy_projection = (str(value.get("status") or "idle"), str(value.get("phase") or ""))
                    authoritative_projection = (str(existing["status"]), str(existing["phase"]))
                    if legacy_projection != authoritative_projection:
                        connection.rollback()
                        conflict = self.record_migration_conflict(
                            domain="agent_activity", legacy=value,
                            authoritative=_decode(str(existing["activity_json"]), {}),
                            reason="V1 AgentActivity 与 control.db 权威状态不一致。",
                        )
                        raise ControlPlaneError(
                            "MIGRATION_RECONCILIATION_REQUIRED",
                            "检测到 V1/V2 AgentActivity 状态冲突，需管理员处理。",
                            details={"conflict_id": conflict["conflict_id"]},
                        )
                inserted = 0
                if value is not None and existing is None:
                    connection.execute(
                        """
                        INSERT INTO agent_activity_state(
                            singleton, status, phase, activity_json, source, created_at, updated_at
                        ) VALUES (1, ?, ?, ?, 'v1_import', ?, ?)
                        """,
                        (
                            str(value.get("status") or "idle"),
                            str(value.get("phase") or ""),
                            _json(value),
                            str(value.get("created_at") or value.get("updated_at") or now),
                            str(value.get("updated_at") or now),
                        ),
                    )
                    inserted = 1
                connection.execute("INSERT INTO control_meta(key, value) VALUES ('agent_activity_v1_imported', '1')")
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "AgentActivityImported",
                    "AgentActivity",
                    self.context.workspace_id,
                    {"count": inserted, "source": "v1_import"},
                )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def agent_activity_state(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM agent_activity_state WHERE singleton = 1").fetchone()
        if row is None:
            return None
        value = dict(row)
        activity = _decode(value.pop("activity_json", ""), {})
        if not isinstance(activity, dict):
            raise ControlPlaneError("STATE_UNAVAILABLE", "AgentActivity 控制状态损坏。", status_code=503)
        activity.update(
            {
                "status": value["status"],
                "phase": value["phase"],
                "control_source": value["source"],
                "control_updated_at": value["updated_at"],
            }
        )
        return activity

    def upsert_agent_activity_state(
        self,
        activity: dict[str, Any],
        *,
        source: str = "v2_projection",
    ) -> dict[str, Any]:
        value = dict(activity)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT created_at FROM agent_activity_state WHERE singleton = 1"
                ).fetchone()
                created_at = str(existing["created_at"]) if existing is not None else str(value.get("created_at") or now)
                connection.execute(
                    """
                    INSERT INTO agent_activity_state(
                        singleton, status, phase, activity_json, source, created_at, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        status = excluded.status,
                        phase = excluded.phase,
                        activity_json = excluded.activity_json,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(value.get("status") or "idle"),
                        str(value.get("phase") or ""),
                        _json(value),
                        source,
                        created_at,
                        str(value.get("updated_at") or now),
                    ),
                )
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('agent_activity_v1_imported', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "AgentActivityChanged",
                    "AgentActivity",
                    self.context.workspace_id,
                    {
                        "status": str(value.get("status") or "idle"),
                        "phase": str(value.get("phase") or ""),
                        "source": source,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.agent_activity_state() or {}

    def workspace_acl(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT principal_id, role, created_at FROM workspace_acl ORDER BY principal_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def grant_workspace_access(self, principal_id: str, *, role: str = "owner") -> dict[str, Any]:
        principal = str(principal_id or "").strip()
        if not principal:
            raise ControlPlaneError("AUTH_REQUIRED", "缺少服务端认证主体。", status_code=401)
        if role not in {"owner", "editor", "viewer"}:
            raise ControlPlaneError("COMMAND_INVALID", f"无效工作区角色: {role}", status_code=400)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT role FROM workspace_acl WHERE principal_id = ?",
                    (principal,),
                ).fetchone()
                if existing is None:
                    revision = self._bump_revision(connection)
                    connection.execute(
                        "INSERT INTO workspace_acl(principal_id, role, created_at) VALUES (?, ?, ?)",
                        (principal, role, _now()),
                    )
                    self._event(
                        connection,
                        revision,
                        "WorkspaceAccessGranted",
                        "Workspace",
                        self.context.workspace_id,
                        {"principal_id": principal, "role": role},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"principal_id": principal, "role": role}

    def require_workspace_access(self, principal_id: str, *, write: bool = False) -> dict[str, Any]:
        principal = str(principal_id or "").strip()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT principal_id, role, created_at FROM workspace_acl WHERE principal_id = ?",
                (principal,),
            ).fetchone()
        if row is None:
            raise ControlPlaneError("WORKSPACE_FORBIDDEN", "无权访问此工作区。", status_code=403)
        result = dict(row)
        if write and result["role"] == "viewer":
            raise ControlPlaneError("WORKSPACE_FORBIDDEN", "当前主体只有只读权限。", status_code=403)
        return result

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM control_meta WHERE key = 'revision'").fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> int:
        revision = ControlStore._revision(connection) + 1
        connection.execute("UPDATE control_meta SET value = ? WHERE key = 'revision'", (str(revision),))
        return revision

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        revision: int,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO workspace_events(
                event_id, workspace_revision, kind, aggregate_type,
                aggregate_id, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), revision, kind, aggregate_type, aggregate_id, _json(payload), _now()),
        )

    def revision(self) -> int:
        with self._connection() as connection:
            return self._revision(connection)

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row, *, duplicate: bool = False) -> CommandReceipt:
        return CommandReceipt(
            command_id=str(row["command_id"]),
            operation_id=str(row["operation_id"] or "") or None,
            status="duplicate" if duplicate else str(row["status"]),
            workspace_revision=int(row["workspace_revision"] if "workspace_revision" in row.keys() else 0),
            confirmation_id=str(row["confirmation_id"] or "") or None,
            error=_decode(row["error_json"], None),
            message=str(row["message"] or ""),
        )

    def _current_operation(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        placeholders = ",".join("?" for _ in self.ACTIVE_OPERATION_STATES)
        return connection.execute(
            f"SELECT * FROM operations WHERE status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
            self.ACTIVE_OPERATION_STATES,
        ).fetchone()

    def prepare(self, envelope: CommandEnvelope) -> tuple[CommandReceipt, bool]:
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = connection.execute(
                    "SELECT *, ? AS workspace_revision FROM commands WHERE idempotency_key = ?",
                    (self._revision(connection), envelope.idempotency_key),
                ).fetchone()
                if duplicate:
                    connection.commit()
                    return self._receipt_from_row(duplicate, duplicate=True), False

                if envelope.kind not in {"migration.scan", "migration.reconcile"}:
                    open_conflicts = int(connection.execute(
                        "SELECT COUNT(*) FROM migration_conflicts WHERE status = 'open'"
                    ).fetchone()[0])
                    if open_conflicts:
                        raise ControlPlaneError(
                            "MIGRATION_RECONCILIATION_REQUIRED",
                            "工作区存在未解决的 V1/V2 状态冲突，已拒绝变更操作。",
                            status_code=409,
                            details={"open_count": open_conflicts},
                        )

                if envelope.kind in self.CONFIRMATION_REQUIRED_KINDS:
                    if not envelope.confirmation_id:
                        raise ControlPlaneError("CONFIRMATION_REQUIRED", "该 Command 必须先完成确认。")
                    confirmation = connection.execute(
                        "SELECT status, command_json FROM confirmations WHERE confirmation_id = ?",
                        (envelope.confirmation_id,),
                    ).fetchone()
                    if not confirmation or str(confirmation["status"]) != "confirmed":
                        raise ControlPlaneError("CONFIRMATION_REQUIRED", "确认记录无效或尚未确认。")
                    confirmed_command = _decode(str(confirmation["command_json"]), {})
                    expected_identity = {
                        "command_id": str(confirmed_command.get("command_id") or ""),
                        "workspace_id": str(confirmed_command.get("workspace_id") or ""),
                        "kind": str(confirmed_command.get("kind") or ""),
                        "payload": confirmed_command.get("payload") or {},
                        "idempotency_key": str(confirmed_command.get("idempotency_key") or ""),
                    }
                    actual_identity = {
                        "command_id": envelope.command_id,
                        "workspace_id": envelope.workspace_id,
                        "kind": envelope.kind,
                        "payload": envelope.payload,
                        "idempotency_key": envelope.idempotency_key,
                    }
                    if _json(expected_identity) != _json(actual_identity):
                        raise ControlPlaneError("ACTION_REPLAYED", "确认记录与 Command 不匹配，已拒绝执行。")

                current_revision = self._revision(connection)
                if envelope.expected_revision != current_revision:
                    raise ControlPlaneError(
                        "REVISION_CONFLICT",
                        "工作区状态已变化，请刷新后重试。",
                        details={"expected_revision": envelope.expected_revision, "current_revision": current_revision},
                    )

                active = self._current_operation(connection)
                blocked_mutation_retry = (
                    envelope.kind in self.BLOCKED_REMEDIATION_KINDS
                    and active is not None
                    and str(active["status"]) == "blocked"
                )
                blocked_pipeline_parent = bool(
                    blocked_mutation_retry
                    and active is not None
                    and str(active["kind"] or "").startswith("pipeline.")
                )
                control_kind = envelope.kind in {
                    "pipeline.pause",
                    "pipeline.resume",
                    "pipeline.cancel",
                    "pipeline.skip_stage",
                } or (blocked_mutation_retry and not blocked_pipeline_parent)
                operation_id = ""
                previous_status = ""
                fencing_token = 0
                if control_kind:
                    requested_id = str(envelope.payload.get("operation_id") or "").strip()
                    if requested_id:
                        active = connection.execute(
                            "SELECT * FROM operations WHERE operation_id = ?",
                            (requested_id,),
                        ).fetchone()
                    if not active:
                        raise ControlPlaneError("OPERATION_NOT_FOUND", "当前没有可控制的 Operation。", status_code=404)
                    operation_id = str(active["operation_id"])
                    previous_status = str(active["status"])
                    fencing_token = int(active["fencing_token"])
                    allowed = {
                        "pipeline.pause": {"running"},
                        "pipeline.resume": {"paused", "blocked"},
                        "pipeline.cancel": {"queued", "running", "pausing", "paused", "blocked"},
                        "pipeline.skip_stage": {"paused", "blocked"},
                        "repair.start": {"blocked"},
                        "rewrite.chapters": {"blocked"},
                        "materials.update": {"blocked"},
                        "materials.refill": {"blocked"},
                    }
                    allowed_statuses = {"blocked"} if blocked_mutation_retry else allowed[envelope.kind]
                    if previous_status not in allowed_statuses:
                        raise ControlPlaneError(
                            "OPERATION_STATE_CONFLICT",
                            f"Operation 当前状态为 {previous_status}，不能执行 {envelope.kind}。",
                            details={"operation_id": operation_id, "status": previous_status},
                        )
                    prepared_status = "queued" if blocked_mutation_retry else {
                        "pipeline.pause": "pausing",
                        "pipeline.resume": "queued",
                        "pipeline.cancel": "cancelling",
                        "pipeline.skip_stage": previous_status,
                    }[envelope.kind]
                    if envelope.kind == "pipeline.resume" or blocked_mutation_retry:
                        fencing_token += 1
                    connection.execute(
                        "UPDATE operations SET status = ?, fencing_token = ?, updated_at = ? WHERE operation_id = ?",
                        (prepared_status, fencing_token, now, operation_id),
                    )
                else:
                    if active and not blocked_pipeline_parent:
                        raise ControlPlaneError(
                            "LEASE_CONFLICT",
                            "当前工作区已有变更 Operation。",
                            details={"operation_id": str(active["operation_id"]), "status": str(active["status"])},
                        )
                    operation_id = str(uuid.uuid4())
                    previous_status = str(active["status"]) if blocked_pipeline_parent and active else ""
                    parent_operation_id = str(active["operation_id"]) if blocked_pipeline_parent and active else ""
                    fencing_token = 1
                    connection.execute(
                        """
                        INSERT INTO operations(
                            operation_id, parent_operation_id, kind, status, start_command,
                            fencing_token, created_at, updated_at, message
                        ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, '')
                        """,
                        (
                            operation_id,
                            parent_operation_id or None,
                            envelope.kind,
                            str(envelope.payload.get("start_command") or ""),
                            fencing_token,
                            now,
                            now,
                        ),
                    )

                lease_id = str(uuid.uuid4())
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec="milliseconds")
                connection.execute(
                    """
                    INSERT INTO workspace_lease(
                        singleton, lease_id, operation_id, fencing_token, owner, heartbeat_at, expires_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        lease_id=excluded.lease_id,
                        operation_id=excluded.operation_id,
                        fencing_token=excluded.fencing_token,
                        owner=excluded.owner,
                        heartbeat_at=excluded.heartbeat_at,
                        expires_at=excluded.expires_at
                    """,
                    (lease_id, operation_id, fencing_token, str(envelope.actor.get("id") or "web"), now, expires_at),
                )

                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    INSERT INTO commands(
                        command_id, kind, payload_json, goal_id, actor_json,
                        expected_revision, idempotency_key, status, operation_id,
                        confirmation_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, ?, ?)
                    """,
                    (
                        envelope.command_id,
                        envelope.kind,
                        _json(envelope.payload),
                        envelope.goal_id,
                        _json(envelope.actor),
                        envelope.expected_revision,
                        envelope.idempotency_key,
                        operation_id,
                        envelope.confirmation_id,
                        now,
                        now,
                    ),
                )
                self._event(
                    connection,
                    revision,
                    "CommandAccepted",
                    "Command",
                    envelope.command_id,
                    {
                        "kind": envelope.kind,
                        "operation_id": operation_id,
                        "previous_operation_status": previous_status,
                        "parent_operation_id": parent_operation_id if not control_kind else "",
                        "fencing_token": fencing_token,
                    },
                )
                connection.commit()
                return CommandReceipt(
                    command_id=envelope.command_id,
                    operation_id=operation_id,
                    status="accepted",
                    workspace_revision=revision,
                ), True
            except Exception:
                connection.rollback()
                raise

    def finish_dispatch(
        self,
        envelope: CommandEnvelope,
        operation_id: str,
        *,
        success: bool,
        operation_status: str,
        message: str,
        error: dict[str, Any] | None = None,
    ) -> CommandReceipt:
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                operation = connection.execute(
                    "SELECT status FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if not operation:
                    raise ControlPlaneError("OPERATION_NOT_FOUND", "Operation 不存在。", status_code=404)
                current_operation_status = str(operation["status"])
                if current_operation_status in {"succeeded", "failed", "cancelled"}:
                    operation_status = current_operation_status
                elif operation_status == "pausing" and current_operation_status == "paused":
                    operation_status = current_operation_status
                elif operation_status == "cancelling" and current_operation_status == "cancelled":
                    operation_status = current_operation_status
                revision = self._bump_revision(connection)
                command_status = "completed" if success else "rejected"
                connection.execute(
                    """
                    UPDATE commands SET status = ?, message = ?, error_json = ?, updated_at = ?
                    WHERE command_id = ?
                    """,
                    (command_status, message, _json(error) if error else None, now, envelope.command_id),
                )
                terminal_at = now if operation_status in {"succeeded", "failed", "cancelled"} else None
                connection.execute(
                    """
                    UPDATE operations SET status = ?, message = ?, error_json = ?, updated_at = ?,
                        completed_at = COALESCE(?, completed_at)
                    WHERE operation_id = ?
                    """,
                    (operation_status, message, _json(error) if error else None, now, terminal_at, operation_id),
                )
                if operation_status in {"succeeded", "failed", "cancelled"}:
                    connection.execute("DELETE FROM workspace_lease WHERE operation_id = ?", (operation_id,))
                self._event(
                    connection,
                    revision,
                    "CommandDispatched" if success else "CommandRejected",
                    "Command",
                    envelope.command_id,
                    {
                        "kind": envelope.kind,
                        "operation_id": operation_id,
                        "operation_status": operation_status,
                        "message": message,
                        "error": error,
                    },
                )
                connection.commit()
                return CommandReceipt(
                    command_id=envelope.command_id,
                    operation_id=operation_id,
                    status="accepted" if success else "rejected",
                    workspace_revision=revision,
                    error=error,
                    message=message,
                )
            except Exception:
                connection.rollback()
                raise

    def sync_operation(
        self,
        operation_id: str,
        status: str,
        *,
        message: str = "",
        error: Any = None,
        fencing_token: int | None = None,
    ) -> int:
        if not operation_id:
            return self.revision()
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, message, error_json, fencing_token FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if not row:
                    connection.commit()
                    return self._revision(connection)
                if fencing_token is not None and int(row["fencing_token"]) != int(fencing_token):
                    raise ControlPlaneError(
                        "LEASE_FENCED",
                        "Worker fencing token 已失效，拒绝写入控制状态。",
                        details={
                            "operation_id": operation_id,
                            "expected_fencing_token": int(row["fencing_token"]),
                            "actual_fencing_token": int(fencing_token),
                        },
                    )
                error_json = _json(error) if error else None
                if str(row["status"]) == status and str(row["message"] or "") == message and row["error_json"] == error_json:
                    if status not in {"succeeded", "failed", "cancelled"}:
                        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec="milliseconds")
                        connection.execute(
                            "UPDATE workspace_lease SET heartbeat_at = ?, expires_at = ? WHERE operation_id = ?",
                            (now, expires_at, operation_id),
                        )
                    connection.commit()
                    return self._revision(connection)
                revision = self._bump_revision(connection)
                terminal_at = now if status in {"succeeded", "failed", "cancelled"} else None
                connection.execute(
                    """
                    UPDATE operations SET status = ?, message = ?, error_json = ?, updated_at = ?,
                        completed_at = COALESCE(?, completed_at)
                    WHERE operation_id = ?
                    """,
                    (status, message, error_json, now, terminal_at, operation_id),
                )
                if status in {"succeeded", "failed", "cancelled"}:
                    connection.execute("DELETE FROM workspace_lease WHERE operation_id = ?", (operation_id,))
                else:
                    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec="milliseconds")
                    connection.execute(
                        "UPDATE workspace_lease SET heartbeat_at = ?, expires_at = ? WHERE operation_id = ?",
                        (now, expires_at, operation_id),
                    )
                self._event(
                    connection,
                    revision,
                    "OperationStatusChanged",
                    "Operation",
                    operation_id,
                    {"status": status, "message": message, "error": error},
                )
                connection.commit()
                return revision
            except Exception:
                connection.rollback()
                raise

    def propose_confirmation(
        self,
        envelope: CommandEnvelope,
        *,
        label: str,
        risk: str,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        confirmation_id = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(timespec="milliseconds")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_revision = self._revision(connection)
                if envelope.expected_revision != current_revision:
                    raise ControlPlaneError(
                        "REVISION_CONFLICT",
                        "工作区状态已变化，请刷新后重试。",
                        details={"expected_revision": envelope.expected_revision, "current_revision": current_revision},
                    )
                revision = self._bump_revision(connection)
                stored = replace(envelope, expected_revision=revision, confirmation_id=confirmation_id)
                command_json = _json(stored.as_dict())
                command_hash = uuid.uuid5(uuid.NAMESPACE_URL, command_json).hex
                connection.execute(
                    """
                    INSERT INTO confirmations(
                        confirmation_id, command_json, command_hash, actor_json, risk,
                        label, status, expected_revision, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        confirmation_id,
                        command_json,
                        command_hash,
                        _json(envelope.actor),
                        risk,
                        label,
                        revision,
                        expires_at,
                        _now(),
                    ),
                )
                self._event(
                    connection,
                    revision,
                    "ConfirmationRequested",
                    "Confirmation",
                    confirmation_id,
                    {"kind": envelope.kind, "risk": risk, "label": label, "expires_at": expires_at},
                )
                connection.commit()
                return {
                    "action_id": confirmation_id,
                    "confirmation_id": confirmation_id,
                    "workspace_id": self.context.workspace_id,
                    "label": label,
                    "risk": risk,
                    "requires_confirmation": True,
                    "expected_revision": revision,
                    "expires_at": expires_at,
                    "type": "confirm_v2_command",
                }
            except Exception:
                connection.rollback()
                raise

    def consume_confirmation(
        self,
        confirmation_id: str,
        *,
        decline: bool = False,
        actor: dict[str, Any] | None = None,
    ) -> CommandEnvelope:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM confirmations WHERE confirmation_id = ?",
                    (confirmation_id,),
                ).fetchone()
                if not row:
                    raise ControlPlaneError("CONFIRMATION_NOT_FOUND", "确认请求不存在。", status_code=404)
                if str(row["status"]) != "pending":
                    raise ControlPlaneError("ACTION_REPLAYED", "确认请求已处理，不能重复使用。")
                if actor is not None:
                    proposed_actor = _decode(str(row["actor_json"] or ""), {})
                    proposed_actor_id = str(
                        proposed_actor.get("id") if isinstance(proposed_actor, dict) else ""
                    ).strip()
                    confirming_actor_id = str(actor.get("id") or "").strip()
                    if not confirming_actor_id or confirming_actor_id != proposed_actor_id:
                        raise ControlPlaneError(
                            "CONFIRMATION_FORBIDDEN",
                            "只能由创建该 Action 的认证主体确认或拒绝。",
                            status_code=403,
                        )
                expires_at = datetime.fromisoformat(str(row["expires_at"]))
                if expires_at <= datetime.now(timezone.utc):
                    connection.execute(
                        "UPDATE confirmations SET status = 'expired', consumed_at = ? WHERE confirmation_id = ?",
                        (_now(), confirmation_id),
                    )
                    connection.commit()
                    raise ControlPlaneError("ACTION_EXPIRED", "确认请求已过期，请重新发起。")
                current_revision = self._revision(connection)
                if current_revision != int(row["expected_revision"]):
                    raise ControlPlaneError(
                        "REVISION_CONFLICT",
                        "确认期间工作区状态已变化，请重新确认。",
                        details={"expected_revision": int(row["expected_revision"]), "current_revision": current_revision},
                    )
                revision = self._bump_revision(connection)
                status = "declined" if decline else "confirmed"
                connection.execute(
                    "UPDATE confirmations SET status = ?, consumed_at = ? WHERE confirmation_id = ?",
                    (status, _now(), confirmation_id),
                )
                self._event(
                    connection,
                    revision,
                    "ConfirmationDeclined" if decline else "ConfirmationConfirmed",
                    "Confirmation",
                    confirmation_id,
                    {"command_hash": str(row["command_hash"])},
                )
                connection.commit()
                data = _decode(str(row["command_json"]), {})
                envelope = CommandEnvelope.from_mapping(data, workspace_id=self.context.workspace_id)
                return replace(
                    envelope,
                    expected_revision=revision,
                    confirmation_id=confirmation_id,
                    actor=dict(actor) if actor is not None else envelope.actor,
                )
            except Exception:
                connection.rollback()
                raise

    def snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            revision = self._revision(connection)
            operations = [dict(row) for row in connection.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT 20"
            ).fetchall()]
            commands = [dict(row) for row in connection.execute(
                "SELECT command_id, kind, status, operation_id, confirmation_id, message, created_at, updated_at "
                "FROM commands ORDER BY created_at DESC LIMIT 50"
            ).fetchall()]
            confirmations = [dict(row) for row in connection.execute(
                "SELECT confirmation_id, risk, label, status, expected_revision, expires_at, created_at "
                "FROM confirmations WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()]
            lease_row = connection.execute("SELECT * FROM workspace_lease WHERE singleton = 1").fetchone()
            artifact_rows = connection.execute(
                "SELECT * FROM artifact_states ORDER BY artifact_key"
            ).fetchall()
            migration_rows = connection.execute(
                "SELECT * FROM migration_conflicts ORDER BY created_at, conflict_id"
            ).fetchall()
            migration_scan = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'migration_last_scan'"
            ).fetchone()
            migration_cutover = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'migration_cutover'"
            ).fetchone()
        for operation in operations:
            operation["error"] = _decode(operation.pop("error_json", None), None)
        return {
            "workspace_id": self.context.workspace_id,
            "revision": revision,
            "operation": next(
                (item for item in operations if str(item.get("status") or "") in self.ACTIVE_OPERATION_STATES),
                operations[0] if operations else None,
            ),
            "operations": operations,
            "commands": commands,
            "confirmations": confirmations,
            "lease": dict(lease_row) if lease_row else None,
            "artifacts": [self._artifact_row(row) for row in artifact_rows],
            "migration": {
                "status": (
                    "needs_reconciliation"
                    if any(str(row["status"]) == "open" for row in migration_rows)
                    else "ready"
                ),
                "open_count": sum(1 for row in migration_rows if str(row["status"]) == "open"),
                "conflicts": [self._migration_conflict_row(row) for row in migration_rows],
                "last_scan": _decode(str(migration_scan["value"]), None) if migration_scan else None,
                "cutover": _decode(str(migration_cutover["value"]), None) if migration_cutover else None,
            },
        }

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if not row:
            return None
        operation = dict(row)
        operation["error"] = _decode(operation.pop("error_json", None), None)
        return operation

    def events(self, after_seq: int = 0, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspace_events WHERE seq > ? ORDER BY seq LIMIT ?",
                (max(0, int(after_seq)), max(1, min(int(limit), 2000))),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["workspace_id"] = self.context.workspace_id
            item["payload"] = _decode(item.pop("payload_json", None), {})
            events.append(item)
        return events


class CommandGateway:
    def __init__(self, context: WorkspaceContext, handlers: dict[str, CommandHandler]) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.handlers = handlers

    def submit(self, envelope: CommandEnvelope) -> CommandReceipt:
        if envelope.workspace_id != self.context.workspace_id:
            raise ControlPlaneError("WORKSPACE_MISMATCH", "Command 工作区与请求路径不一致。", status_code=400)
        handler = self.handlers.get(envelope.kind)
        if handler is None:
            raise ControlPlaneError("COMMAND_UNSUPPORTED", f"暂不支持 Command: {envelope.kind}", status_code=400)
        receipt, should_dispatch = self.store.prepare(envelope)
        if not should_dispatch:
            return receipt
        operation_id = str(receipt.operation_id or "")
        after_commit: Any = None
        try:
            result = handler(self.context, envelope, operation_id) or {}
            after_commit = result.pop("_after_commit", None)
            accepted = bool(result.get("accepted", True))
            message = str(result.get("message") or "命令已接收。")
            operation_status = str(result.get("operation_status") or ("running" if accepted else "failed"))
            error = result.get("error") if isinstance(result.get("error"), dict) else None
            receipt = self.store.finish_dispatch(
                envelope,
                operation_id,
                success=accepted,
                operation_status=operation_status,
                message=message,
                error=error,
            )
        except ControlPlaneError as exc:
            blocked = exc.code == "GATE_BLOCKED" or envelope.kind in {
                "pipeline.pause",
                "pipeline.cancel",
                "pipeline.skip_stage",
            }
            return self.store.finish_dispatch(
                envelope,
                operation_id,
                success=False,
                operation_status="blocked" if blocked else "failed",
                message=exc.message,
                error=exc.as_dict(),
            )
        except Exception as exc:
            error = ControlPlaneError("COMMAND_DISPATCH_FAILED", str(exc), status_code=500)
            return self.store.finish_dispatch(
                envelope,
                operation_id,
                success=False,
                operation_status="failed",
                message=error.message,
                error=error.as_dict(),
            )
        if callable(after_commit):
            after_commit()
        return receipt

    def propose(self, envelope: CommandEnvelope, *, label: str, risk: str) -> dict[str, Any]:
        return self.store.propose_confirmation(envelope, label=label, risk=risk)

    def confirm(self, confirmation_id: str, *, actor: dict[str, Any] | None = None) -> CommandReceipt:
        envelope = self.store.consume_confirmation(confirmation_id, actor=actor)
        return self.submit(envelope)

    def decline(self, confirmation_id: str, *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
        envelope = self.store.consume_confirmation(confirmation_id, decline=True, actor=actor)
        return {
            "confirmation_id": confirmation_id,
            "status": "declined",
            "kind": envelope.kind,
            "workspace_revision": self.store.revision(),
        }
