from __future__ import annotations

import json
import sqlite3
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

    SCHEMA_VERSION = 10
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
                    CREATE INDEX IF NOT EXISTS idx_events_revision ON workspace_events(workspace_revision);
                    CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_issue_states_status ON issue_states(status, severity);
                    CREATE INDEX IF NOT EXISTS idx_policy_decisions_issue ON policy_decisions(issue_id, created_at);
                    """
                )
                connection.execute(
                    """
                    INSERT INTO control_meta(key, value) VALUES ('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(self.SCHEMA_VERSION),),
                )
                connection.execute("INSERT OR IGNORE INTO control_meta(key, value) VALUES ('revision', '0')")

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
                for item in rows:
                    issue_id = str(item.get("id") or "").strip()
                    connection.execute(
                        """
                        INSERT INTO issue_states(
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
                    "SELECT * FROM policy_decisions WHERE issue_id = ? ORDER BY created_at",
                    (issue_id,),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM policy_decisions ORDER BY created_at").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["decision"] = _decode(value.pop("decision_json", ""), {})
            value["actor"] = _decode(value.pop("actor_json", ""), {})
            result.append(value)
        return result

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
                inserted = 0
                if value is not None:
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
                inserted = 0
                if value is not None:
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
                inserted = 0
                if value is not None:
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
                control_kind = envelope.kind in {
                    "pipeline.pause",
                    "pipeline.resume",
                    "pipeline.cancel",
                    "pipeline.skip_stage",
                } or blocked_mutation_retry
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
                    if active:
                        raise ControlPlaneError(
                            "LEASE_CONFLICT",
                            "当前工作区已有变更 Operation。",
                            details={"operation_id": str(active["operation_id"]), "status": str(active["status"])},
                        )
                    operation_id = str(uuid.uuid4())
                    previous_status = ""
                    fencing_token = 1
                    connection.execute(
                        """
                        INSERT INTO operations(
                            operation_id, kind, status, start_command, fencing_token,
                            created_at, updated_at, message
                        ) VALUES (?, ?, 'queued', ?, ?, ?, ?, '')
                        """,
                        (
                            operation_id,
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

    def consume_confirmation(self, confirmation_id: str, *, decline: bool = False) -> CommandEnvelope:
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
                return replace(envelope, expected_revision=revision, confirmation_id=confirmation_id)
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
        for operation in operations:
            operation["error"] = _decode(operation.pop("error_json", None), None)
        return {
            "workspace_id": self.context.workspace_id,
            "revision": revision,
            "operation": operations[0] if operations else None,
            "operations": operations,
            "commands": commands,
            "confirmations": confirmations,
            "lease": dict(lease_row) if lease_row else None,
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

    def confirm(self, confirmation_id: str) -> CommandReceipt:
        envelope = self.store.consume_confirmation(confirmation_id)
        return self.submit(envelope)

    def decline(self, confirmation_id: str) -> dict[str, Any]:
        envelope = self.store.consume_confirmation(confirmation_id, decline=True)
        return {
            "confirmation_id": confirmation_id,
            "status": "declined",
            "kind": envelope.kind,
            "workspace_revision": self.store.revision(),
        }
