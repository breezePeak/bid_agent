from __future__ import annotations

import hashlib
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


def _parse_utc_timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ControlPlaneError("STATE_UNAVAILABLE", f"{label}无效。", status_code=503) from exc
    if parsed.tzinfo is None:
        raise ControlPlaneError("STATE_UNAVAILABLE", f"{label}缺少时区。", status_code=503)
    return parsed


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
    result: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "operation_id": self.operation_id,
            "status": self.status,
            "workspace_revision": self.workspace_revision,
            "confirmation_id": self.confirmation_id,
            "error": self.error,
            "message": self.message,
            "result": self.result,
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
    """Per-workspace authoritative V3 control state.

    Artifact content remains on disk. This store only owns command/control state and
    the append-only workspace event stream.
    """

    SCHEMA_VERSION = 28
    # Includes resumable blocked/paused records for command routing. They do
    # not own the workspace lease; see LOCK_OPERATION_STATES.
    ACTIVE_OPERATION_STATES = ("queued", "running", "pausing", "paused", "cancelling", "blocked")
    LOCK_OPERATION_STATES = ("queued", "running", "pausing", "cancelling")
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
    BLOCKED_SUPERSEDING_KINDS = {
        "document.prepare_outline",
        "document.run_pipeline",
        "chapter.generate_draft",
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
                    CREATE TABLE IF NOT EXISTS stage_runs (
                        stage_run_id TEXT PRIMARY KEY,
                        operation_id TEXT NOT NULL,
                        stage_command TEXT NOT NULL,
                        attempt INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        disposition TEXT NOT NULL DEFAULT '',
                        error_json TEXT,
                        output_json TEXT,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        UNIQUE(operation_id, stage_command, attempt)
                    );
                    CREATE INDEX IF NOT EXISTS idx_stage_runs_operation
                        ON stage_runs(operation_id, stage_command, attempt DESC);
                    CREATE TABLE IF NOT EXISTS llm_requests (
                        request_id TEXT PRIMARY KEY,
                        operation_id TEXT NOT NULL,
                        stage_id TEXT NOT NULL,
                        request_index INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        UNIQUE(operation_id, stage_id, request_index)
                    );
                    CREATE INDEX IF NOT EXISTS idx_llm_requests_operation
                        ON llm_requests(operation_id, stage_id, request_index);
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
                    CREATE TABLE IF NOT EXISTS chapter_batch_jobs (
                        job_id TEXT PRIMARY KEY,
                        operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
                        status TEXT NOT NULL,
                        chapter_ids_json TEXT NOT NULL,
                        current_chapter_id TEXT NOT NULL DEFAULT '',
                        completed_count INTEGER NOT NULL DEFAULT 0,
                        failed_count INTEGER NOT NULL DEFAULT 0,
                        error_json TEXT,
                        retry_policy_json TEXT NOT NULL DEFAULT '{}',
                        fencing_token INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS chapter_batch_items (
                        item_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES chapter_batch_jobs(job_id) ON DELETE CASCADE,
                        chapter_id TEXT NOT NULL,
                        chapter_title TEXT NOT NULL DEFAULT '',
                        position INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL DEFAULT 'queued',
                        attempt INTEGER NOT NULL DEFAULT 0,
                        context_ref_json TEXT NOT NULL DEFAULT '{}',
                        content_revision INTEGER NOT NULL DEFAULT 0,
                        error_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(job_id, chapter_id), UNIQUE(job_id, position)
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_batch_items_job
                        ON chapter_batch_items(job_id, position);
                    CREATE TABLE IF NOT EXISTS chapter_batch_checkpoints (
                        checkpoint_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES chapter_batch_jobs(job_id) ON DELETE CASCADE,
                        item_id TEXT NOT NULL REFERENCES chapter_batch_items(item_id) ON DELETE CASCADE,
                        stage TEXT NOT NULL,
                        input_hash TEXT NOT NULL DEFAULT '',
                        artifact_refs_json TEXT NOT NULL DEFAULT '{}',
                        event_sequence INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        UNIQUE(item_id, stage, input_hash)
                    );
                    CREATE TABLE IF NOT EXISTS chapter_batch_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        job_id TEXT NOT NULL REFERENCES chapter_batch_jobs(job_id) ON DELETE CASCADE,
                        item_id TEXT,
                        chapter_id TEXT,
                        chapter_title TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL DEFAULT '',
                        type TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT '',
                        message TEXT NOT NULL DEFAULT '',
                        data_json TEXT NOT NULL DEFAULT '{}',
                        error_json TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_batch_events_job
                        ON chapter_batch_events(job_id, sequence);
                    CREATE TABLE IF NOT EXISTS document_state (
                        workspace_id TEXT PRIMARY KEY,
                        document_mode TEXT NOT NULL DEFAULT '',
                        project_model_revision INTEGER,
                        document_contract_revision INTEGER,
                        document_plan_revision INTEGER,
                        integration_revision INTEGER,
                        delivery_status TEXT NOT NULL DEFAULT 'draft_with_gaps',
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS evidence_needs (
                        need_id TEXT PRIMARY KEY,
                        question TEXT NOT NULL,
                        topic_id TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        blocking_scope TEXT NOT NULL,
                        deadline_stage TEXT NOT NULL,
                        query_budget INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        active_batch_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS content_unit_states (
                        unit_id TEXT PRIMARY KEY,
                        contract_revision INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        evidence_snapshot_hash TEXT NOT NULL DEFAULT '',
                        writer_fingerprint TEXT NOT NULL DEFAULT '',
                        output_artifact_id TEXT,
                        invalidation_reason TEXT NOT NULL DEFAULT '',
                        stale_reason TEXT NOT NULL DEFAULT '',
                        current_chapter_id TEXT NOT NULL DEFAULT '',
                        current_chapter_title TEXT NOT NULL DEFAULT '',
                        progress_phase TEXT NOT NULL DEFAULT '',
                        draft_preview TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dependency_edges (
                        upstream_type TEXT NOT NULL,
                        upstream_id TEXT NOT NULL,
                        downstream_type TEXT NOT NULL,
                        downstream_id TEXT NOT NULL,
                        edge_kind TEXT NOT NULL,
                        PRIMARY KEY (upstream_type, upstream_id, downstream_type, downstream_id, edge_kind)
                    );
                    CREATE TABLE IF NOT EXISTS change_sets (
                        change_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        impact_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        applied_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS content_locks (
                        block_id TEXT PRIMARY KEY,
                        lock_owner TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        artifact_kind TEXT NOT NULL,
                        producer_role TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        base_revision INTEGER NOT NULL,
                        dependency_fingerprint TEXT NOT NULL,
                        declared_dependencies_json TEXT NOT NULL DEFAULT '[]',
                        proposal_hash TEXT NOT NULL UNIQUE,
                        canonical_payload_hash TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        cited_source_ids_json TEXT NOT NULL,
                        prompt_version TEXT NOT NULL,
                        model_fingerprint TEXT NOT NULL,
                        inference_receipt_refs_json TEXT NOT NULL DEFAULT '[]',
                        payload_schema_version TEXT NOT NULL DEFAULT 'v3',
                        canonicalization_version TEXT NOT NULL DEFAULT 'v3-canon-2',
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_inference_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_hash TEXT NOT NULL UNIQUE,
                        workspace_id TEXT NOT NULL,
                        invocation_id TEXT NOT NULL,
                        capability_id TEXT NOT NULL,
                        capability_version TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_validation_reports (
                        proposal_id TEXT PRIMARY KEY REFERENCES v3_proposals(proposal_id),
                        proposal_hash TEXT NOT NULL DEFAULT '',
                        report_hash TEXT NOT NULL DEFAULT '',
                        report_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_gate_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_hash TEXT NOT NULL DEFAULT '',
                        workspace_id TEXT NOT NULL DEFAULT '',
                        proposal_id TEXT NOT NULL REFERENCES v3_proposals(proposal_id),
                        proposal_hash TEXT NOT NULL,
                        validation_report_id TEXT NOT NULL DEFAULT '',
                        validation_report_hash TEXT NOT NULL DEFAULT '',
                        artifact_kind TEXT NOT NULL DEFAULT '',
                        gate_id TEXT NOT NULL,
                        gate_policy_version TEXT NOT NULL DEFAULT '',
                        verdict TEXT NOT NULL,
                        findings_json TEXT NOT NULL,
                        issuer TEXT NOT NULL DEFAULT '',
                        reviewer TEXT NOT NULL,
                        reviewed_revision INTEGER NOT NULL,
                        dependency_fingerprint TEXT NOT NULL DEFAULT '',
                        dependency_snapshot_json TEXT NOT NULL DEFAULT '{}',
                        issued_at TEXT NOT NULL DEFAULT '',
                        expires_at TEXT,
                        receipt_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_artifact_revisions (
                        artifact_kind TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        artifact_id TEXT NOT NULL,
                        artifact_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        producer_role TEXT NOT NULL,
                        dependency_fingerprint TEXT NOT NULL,
                        proposal_id TEXT NOT NULL UNIQUE REFERENCES v3_proposals(proposal_id),
                        proposal_hash TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (artifact_kind, revision)
                    );
                    CREATE TABLE IF NOT EXISTS v3_active_artifacts (
                        artifact_kind TEXT PRIMARY KEY,
                        artifact_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS v3_promotion_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_hash TEXT NOT NULL DEFAULT '',
                        workspace_id TEXT NOT NULL DEFAULT '',
                        proposal_id TEXT NOT NULL UNIQUE REFERENCES v3_proposals(proposal_id),
                        proposal_hash TEXT NOT NULL DEFAULT '',
                        artifact_kind TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        base_revision INTEGER NOT NULL DEFAULT 0,
                        promoted_revision INTEGER NOT NULL,
                        artifact_hash TEXT NOT NULL,
                        dependency_fingerprint TEXT NOT NULL,
                        dependency_snapshot_json TEXT NOT NULL DEFAULT '{}',
                        gate_receipt_ids_json TEXT NOT NULL,
                        gate_receipts_json TEXT NOT NULL DEFAULT '[]',
                        policy_version TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        UNIQUE (artifact_kind, operation_id, proposal_hash)
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_revision ON workspace_events(workspace_revision);
                    CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_issue_states_status ON issue_states(status, severity);
                    CREATE INDEX IF NOT EXISTS idx_policy_decisions_issue ON policy_decisions(issue_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_artifact_states_status ON artifact_states(status, producer);
                    CREATE INDEX IF NOT EXISTS idx_evidence_needs_status
                        ON evidence_needs(status, deadline_stage, priority);
                    CREATE INDEX IF NOT EXISTS idx_content_unit_states_state
                        ON content_unit_states(state, contract_revision);
                    CREATE INDEX IF NOT EXISTS idx_dependency_edges_downstream
                        ON dependency_edges(downstream_type, downstream_id);
                    CREATE INDEX IF NOT EXISTS idx_v3_proposals_kind_status
                        ON v3_proposals(artifact_kind, status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_v3_gate_receipts_proposal
                        ON v3_gate_receipts(proposal_id, created_at);
                    CREATE TABLE IF NOT EXISTS chapter_workspaces (
                        chapter_id TEXT PRIMARY KEY,
                        blueprint_revision INTEGER NOT NULL,
                        blueprint_hash TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL,
                        parent_chapter_id TEXT,
                        order_index INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        approval_status TEXT NOT NULL DEFAULT 'not_started',
                        chapter_revision INTEGER NOT NULL DEFAULT 0,
                        head_content_revision INTEGER NOT NULL DEFAULT 0,
                        formal_content_revision INTEGER NOT NULL DEFAULT 0,
                        head_context_revision INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        state_hash TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_workspaces_status
                        ON chapter_workspaces(status, order_index, chapter_id);
                    CREATE TABLE IF NOT EXISTS chapter_context_revisions (
                        chapter_id TEXT NOT NULL,
                        context_revision INTEGER NOT NULL,
                        items_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        parent_context_revision INTEGER,
                        seeded_from_blueprint INTEGER NOT NULL DEFAULT 0,
                        actor_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (chapter_id, context_revision)
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_context_revisions_head
                        ON chapter_context_revisions(chapter_id, context_revision DESC);
                    CREATE TABLE IF NOT EXISTS chapter_content_revisions (
                        chapter_id TEXT NOT NULL,
                        content_revision INTEGER NOT NULL,
                        blocks_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        parent_content_revision INTEGER,
                        source TEXT NOT NULL,
                        approval_policy_json TEXT NOT NULL DEFAULT '{}',
                        actor_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (chapter_id, content_revision)
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_content_revisions_head
                        ON chapter_content_revisions(chapter_id, content_revision DESC);
                    CREATE TABLE IF NOT EXISTS chapter_approval_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_hash TEXT NOT NULL UNIQUE,
                        chapter_id TEXT NOT NULL,
                        content_revision INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        principal_id TEXT NOT NULL,
                        confirmation_required INTEGER NOT NULL,
                        actor_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        UNIQUE (chapter_id, content_revision, content_hash, decision)
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapter_approval_receipts_chapter
                        ON chapter_approval_receipts(chapter_id, content_revision DESC);
                    """
                )
                operation_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(operations)").fetchall()
                }
                if "parent_operation_id" not in operation_columns:
                    connection.execute("ALTER TABLE operations ADD COLUMN parent_operation_id TEXT")
                self._migrate_v3_kernel_columns(connection)
                self._migrate_chapter_workspace_tables(connection)
                connection.execute("DROP TABLE IF EXISTS migration_conflicts")
                connection.execute(
                    """
                    DELETE FROM control_meta
                    WHERE key IN (
                        'compatibility_usage', 'migration_last_scan', 'migration_cutover',
                        'goal_v1_imported', 'materials_v1_imported', 'issue_v1_imported',
                        'repair_job_v1_imported', 'agent_activity_v1_imported'
                    )
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
                # Blocked/paused operations are resumable records, never active
                # workspace writers. Remove stale leases left by older builds.
                connection.execute(
                    "DELETE FROM workspace_lease WHERE operation_id IN "
                    "(SELECT operation_id FROM operations WHERE status IN ('blocked', 'paused'))"
                )

    def upsert_evidence_need(self, item: dict[str, Any]) -> dict[str, Any]:
        """Persist V3 research scheduling state; evidence itself is immutable."""
        required = ("need_id", "question", "topic_id", "priority", "blocking_scope", "deadline_stage", "query_budget", "status")
        missing = [key for key in required if item.get(key) is None or (isinstance(item.get(key), str) and not item[key].strip())]
        if missing:
            raise ControlPlaneError("INVALID_EVIDENCE_NEED", f"EvidenceNeed 缺少字段: {', '.join(missing)}", status_code=400)
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evidence_needs(
                    need_id, question, topic_id, priority, blocking_scope, deadline_stage,
                    query_budget, status, active_batch_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(need_id) DO UPDATE SET
                    question=excluded.question, topic_id=excluded.topic_id, priority=excluded.priority,
                    blocking_scope=excluded.blocking_scope, deadline_stage=excluded.deadline_stage,
                    query_budget=excluded.query_budget, status=excluded.status,
                    active_batch_id=excluded.active_batch_id, updated_at=excluded.updated_at
                """,
                (
                    item["need_id"], item["question"], item["topic_id"], item["priority"],
                    item["blocking_scope"], item["deadline_stage"], int(item["query_budget"]),
                    item["status"], item.get("active_batch_id"), now, now,
                ),
            )
        return self.evidence_need(str(item["need_id"])) or {}

    def evidence_need(self, need_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM evidence_needs WHERE need_id = ?", (need_id,)).fetchone()
        return dict(row) if row else None

    def evidence_needs(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_needs
                ORDER BY CASE priority WHEN 'blocking' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                    created_at, need_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_content_unit_state(self, item: dict[str, Any]) -> dict[str, Any]:
        required = ("unit_id", "contract_revision", "state")
        missing = [key for key in required if item.get(key) is None or (isinstance(item.get(key), str) and not item[key].strip())]
        if missing:
            raise ControlPlaneError("INVALID_CONTENT_UNIT", f"ContentUnit 缺少字段: {', '.join(missing)}", status_code=400)
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO content_unit_states(
                    unit_id, contract_revision, state, attempt, evidence_snapshot_hash,
                    writer_fingerprint, output_artifact_id, invalidation_reason, stale_reason,
                    current_chapter_id, current_chapter_title, progress_phase, draft_preview, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id) DO UPDATE SET
                    contract_revision=excluded.contract_revision, state=excluded.state,
                    attempt=excluded.attempt, evidence_snapshot_hash=excluded.evidence_snapshot_hash,
                    writer_fingerprint=excluded.writer_fingerprint,
                    output_artifact_id=excluded.output_artifact_id,
                    invalidation_reason=excluded.invalidation_reason,
                    stale_reason=excluded.stale_reason,
                    current_chapter_id=excluded.current_chapter_id,
                    current_chapter_title=excluded.current_chapter_title,
                    progress_phase=excluded.progress_phase,
                    draft_preview=excluded.draft_preview,
                    updated_at=excluded.updated_at
                """,
                (
                    item["unit_id"], int(item["contract_revision"]), item["state"], int(item.get("attempt", 0)),
                    str(item.get("evidence_snapshot_hash") or ""), str(item.get("writer_fingerprint") or ""),
                    item.get("output_artifact_id"), str(item.get("invalidation_reason") or ""),
                    str(item.get("stale_reason") or ""),
                    str(item.get("current_chapter_id") or ""),
                    str(item.get("current_chapter_title") or ""),
                    str(item.get("progress_phase") or ""),
                    str(item.get("draft_preview") or ""), now,
                ),
            )
        return self.content_unit_state(str(item["unit_id"])) or {}

    def update_content_unit_progress(
        self,
        unit_id: str,
        *,
        chapter_id: str,
        chapter_title: str,
        phase: str,
        draft_preview: str | None = None,
    ) -> dict[str, Any]:
        """Record the current target of a running writer without changing its plan."""
        normalized_unit_id = str(unit_id or "").strip()
        if not normalized_unit_id:
            raise ControlPlaneError(
                "INVALID_CONTENT_UNIT",
                "ContentUnit 缺少 unit_id。",
                status_code=400,
            )
        now = _now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE content_unit_states
                SET current_chapter_id = ?,
                    current_chapter_title = ?,
                    progress_phase = ?,
                    draft_preview = CASE WHEN ? IS NULL THEN draft_preview ELSE ? END,
                    updated_at = ?
                WHERE unit_id = ? AND state = 'running'
                """,
                (
                    str(chapter_id or ""),
                    str(chapter_title or ""),
                    str(phase or ""),
                    draft_preview,
                    str(draft_preview or "")[:24000],
                    now,
                    normalized_unit_id,
                ),
            )
        if cursor.rowcount == 0:
            return self.content_unit_state(normalized_unit_id) or {}
        return self.content_unit_state(normalized_unit_id) or {}

    def content_unit_state(self, unit_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM content_unit_states WHERE unit_id = ?", (str(unit_id or ""),)).fetchone()
        return dict(row) if row else None

    def content_unit_states(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM content_unit_states ORDER BY updated_at, unit_id").fetchall()
        return [dict(row) for row in rows]

    def content_locks(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM content_locks ORDER BY created_at, block_id").fetchall()
        return [dict(row) for row in rows]

    def _migrate_chapter_workspace_tables(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chapter_workspaces (
                chapter_id TEXT PRIMARY KEY,
                blueprint_revision INTEGER NOT NULL,
                blueprint_hash TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                parent_chapter_id TEXT,
                order_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                approval_status TEXT NOT NULL DEFAULT 'not_started',
                chapter_revision INTEGER NOT NULL DEFAULT 0,
                head_content_revision INTEGER NOT NULL DEFAULT 0,
                formal_content_revision INTEGER NOT NULL DEFAULT 0,
                head_context_revision INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chapter_workspaces_status
                ON chapter_workspaces(status, order_index, chapter_id);
            CREATE TABLE IF NOT EXISTS chapter_context_revisions (
                chapter_id TEXT NOT NULL,
                context_revision INTEGER NOT NULL,
                items_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                parent_context_revision INTEGER,
                seeded_from_blueprint INTEGER NOT NULL DEFAULT 0,
                actor_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, context_revision)
            );
            CREATE INDEX IF NOT EXISTS idx_chapter_context_revisions_head
                ON chapter_context_revisions(chapter_id, context_revision DESC);
            CREATE TABLE IF NOT EXISTS chapter_content_revisions (
                chapter_id TEXT NOT NULL,
                content_revision INTEGER NOT NULL,
                blocks_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                parent_content_revision INTEGER,
                source TEXT NOT NULL,
                approval_policy_json TEXT NOT NULL DEFAULT '{}',
                actor_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, content_revision)
            );
            CREATE INDEX IF NOT EXISTS idx_chapter_content_revisions_head
                ON chapter_content_revisions(chapter_id, content_revision DESC);
            CREATE TABLE IF NOT EXISTS chapter_approval_receipts (
                receipt_id TEXT PRIMARY KEY,
                receipt_hash TEXT NOT NULL UNIQUE,
                chapter_id TEXT NOT NULL,
                content_revision INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                decision TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                confirmation_required INTEGER NOT NULL,
                actor_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE (chapter_id, content_revision, content_hash, decision)
            );
            CREATE INDEX IF NOT EXISTS idx_chapter_approval_receipts_chapter
                ON chapter_approval_receipts(chapter_id, content_revision DESC);
            """
        )

    @staticmethod
    def _normalize_chapter_id(chapter_id: str) -> str:
        value = str(chapter_id or "").strip()
        if (
            not value
            or value in {".", ".."}
            or Path(value).name != value
            or "/" in value
            or "\\" in value
        ):
            raise ControlPlaneError(
                "CHAPTER_ID_INVALID",
                "无效 chapter_id。",
                status_code=400,
            )
        return value

    @staticmethod
    def _chapter_workspace_state_hash(
        *,
        chapter_id: str,
        blueprint_revision: int,
        blueprint_hash: str,
        title: str,
        parent_chapter_id: str | None,
        order_index: int,
        status: str,
        approval_status: str,
        chapter_revision: int,
        head_content_revision: int,
        formal_content_revision: int,
        head_context_revision: int,
        metadata: dict[str, Any],
    ) -> str:
        payload = {
            "chapter_id": chapter_id,
            "blueprint_revision": int(blueprint_revision),
            "blueprint_hash": str(blueprint_hash or ""),
            "title": title,
            "parent_chapter_id": parent_chapter_id,
            "order": int(order_index),
            "status": status,
            "approval_status": approval_status,
            "chapter_revision": int(chapter_revision),
            "head_content_revision": int(head_content_revision),
            "formal_content_revision": int(formal_content_revision),
            "head_context_revision": int(head_context_revision),
            "metadata": metadata,
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _chapter_workspace_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        metadata = _decode(item.pop("metadata_json", "{}"), {})
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "chapter_id": str(item.get("chapter_id") or ""),
            "blueprint_revision": int(item.get("blueprint_revision") or 0),
            "blueprint_hash": str(item.get("blueprint_hash") or ""),
            "title": str(item.get("title") or ""),
            "parent_chapter_id": (
                str(item["parent_chapter_id"])
                if item.get("parent_chapter_id") is not None
                else None
            ),
            "order": int(item.get("order_index") if item.get("order_index") is not None else item.get("order") or 0),
            "status": str(item.get("status") or "active"),
            "approval_status": str(item.get("approval_status") or "not_started"),
            "chapter_revision": int(item.get("chapter_revision") or 0),
            "head_content_revision": int(item.get("head_content_revision") or 0),
            "formal_content_revision": int(item.get("formal_content_revision") or 0),
            "head_context_revision": int(item.get("head_context_revision") or 0),
            "metadata": metadata,
            "state_hash": str(item.get("state_hash") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }

    def chapter_workspaces(self, *, include_archived: bool = True) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if include_archived:
                rows = connection.execute(
                    """
                    SELECT * FROM chapter_workspaces
                    ORDER BY order_index, chapter_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM chapter_workspaces
                    WHERE status != 'archived'
                    ORDER BY order_index, chapter_id
                    """
                ).fetchall()
        return [self._chapter_workspace_row(row) for row in rows]

    def chapter_workspace(self, chapter_id: str) -> dict[str, Any] | None:
        normalized = self._normalize_chapter_id(chapter_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                (normalized,),
            ).fetchone()
        return self._chapter_workspace_row(row) if row else None

    def materialize_chapter_workspace(
        self,
        *,
        chapter_id: str,
        blueprint_revision: int,
        blueprint_hash: str,
        title: str,
        parent_chapter_id: str | None,
        order_index: int,
        expected_chapter_revision: int,
        metadata: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Idempotently materialize or refresh a chapter workspace from Blueprint."""
        normalized = self._normalize_chapter_id(chapter_id)
        title_value = str(title or "").strip()
        if not title_value:
            raise ControlPlaneError("CHAPTER_TITLE_REQUIRED", "章节标题不能为空。", status_code=400)
        try:
            expected = int(expected_chapter_revision)
            bp_revision = int(blueprint_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision / blueprint_revision 必须是整数。",
                status_code=400,
            ) from exc
        if bp_revision < 1:
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED",
                "缺少有效 Blueprint revision。",
                status_code=409,
            )
        bp_hash = str(blueprint_hash or "").strip()
        if not bp_hash:
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED",
                "缺少有效 Blueprint hash。",
                status_code=409,
            )
        parent = (
            str(parent_chapter_id).strip()
            if parent_chapter_id is not None and str(parent_chapter_id).strip()
            else None
        )
        if parent is not None:
            parent = self._normalize_chapter_id(parent)
        meta = dict(metadata or {})
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    if expected != 0:
                        raise ControlPlaneError(
                            "CHAPTER_REVISION_CONFLICT",
                            "章节尚未物化，expected_chapter_revision 必须为 0。",
                            status_code=409,
                            details={
                                "chapter_id": normalized,
                                "expected_chapter_revision": expected,
                                "current_chapter_revision": 0,
                            },
                        )
                    chapter_revision = 1
                    approval_status = "not_started"
                    status = "active"
                    head_content_revision = 0
                    formal_content_revision = 0
                    head_context_revision = 0
                    created_at = now
                    state_hash = self._chapter_workspace_state_hash(
                        chapter_id=normalized,
                        blueprint_revision=bp_revision,
                        blueprint_hash=bp_hash,
                        title=title_value,
                        parent_chapter_id=parent,
                        order_index=int(order_index),
                        status=status,
                        approval_status=approval_status,
                        chapter_revision=chapter_revision,
                        head_content_revision=head_content_revision,
                        formal_content_revision=formal_content_revision,
                        head_context_revision=head_context_revision,
                        metadata=meta,
                    )
                    connection.execute(
                        """
                        INSERT INTO chapter_workspaces(
                            chapter_id, blueprint_revision, blueprint_hash, title,
                            parent_chapter_id, order_index, status, approval_status,
                            chapter_revision, head_content_revision, formal_content_revision,
                            head_context_revision, metadata_json, state_hash,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized,
                            bp_revision,
                            bp_hash,
                            title_value,
                            parent,
                            int(order_index),
                            status,
                            approval_status,
                            chapter_revision,
                            head_content_revision,
                            formal_content_revision,
                            head_context_revision,
                            _json(meta),
                            state_hash,
                            created_at,
                            now,
                        ),
                    )
                    revision = self._bump_revision(connection)
                    self._event(
                        connection,
                        revision,
                        "ChapterWorkspaceMaterialized",
                        "ChapterWorkspace",
                        normalized,
                        {
                            "chapter_id": normalized,
                            "chapter_revision": chapter_revision,
                            "blueprint_revision": bp_revision,
                            "state_hash": state_hash,
                            "actor": actor or {},
                        },
                    )
                    connection.commit()
                    return self.chapter_workspace(normalized) or {}

                current = self._chapter_workspace_row(existing)
                current_revision = int(current["chapter_revision"])
                # Pure re-materialize: same structural seed, no metadata change → idempotent.
                same_structure = (
                    int(current["blueprint_revision"]) == bp_revision
                    and str(current["blueprint_hash"]) == bp_hash
                    and str(current["title"]) == title_value
                    and current.get("parent_chapter_id") == parent
                    and int(current["order"]) == int(order_index)
                    and str(current["status"]) == "active"
                )
                if same_structure and not meta and expected in {0, current_revision}:
                    connection.commit()
                    return current
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                merged_meta = dict(current.get("metadata") or {})
                if meta:
                    merged_meta.update(meta)
                chapter_revision = current_revision + 1
                status = "active"
                approval_status = str(current.get("approval_status") or "not_started")
                head_content_revision = int(current.get("head_content_revision") or 0)
                formal_content_revision = int(current.get("formal_content_revision") or 0)
                head_context_revision = int(current.get("head_context_revision") or 0)
                created_at = str(current.get("created_at") or now)
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=bp_revision,
                    blueprint_hash=bp_hash,
                    title=title_value,
                    parent_chapter_id=parent,
                    order_index=int(order_index),
                    status=status,
                    approval_status=approval_status,
                    chapter_revision=chapter_revision,
                    head_content_revision=head_content_revision,
                    formal_content_revision=formal_content_revision,
                    head_context_revision=head_context_revision,
                    metadata=merged_meta,
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        blueprint_revision = ?,
                        blueprint_hash = ?,
                        title = ?,
                        parent_chapter_id = ?,
                        order_index = ?,
                        status = ?,
                        approval_status = ?,
                        chapter_revision = ?,
                        metadata_json = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (
                        bp_revision,
                        bp_hash,
                        title_value,
                        parent,
                        int(order_index),
                        status,
                        approval_status,
                        chapter_revision,
                        _json(merged_meta),
                        state_hash,
                        now,
                        normalized,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterWorkspaceUpdated",
                    "ChapterWorkspace",
                    normalized,
                    {
                        "chapter_id": normalized,
                        "chapter_revision": chapter_revision,
                        "state_hash": state_hash,
                        "actor": actor or {},
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.chapter_workspace(normalized) or {}

    def archive_chapter_workspace(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Soft-delete a chapter workspace (tombstone). Does not alter Blueprint."""
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            expected = int(expected_chapter_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    raise ControlPlaneError(
                        "CHAPTER_NOT_FOUND",
                        f"章节 Workspace 不存在: {normalized}",
                        status_code=404,
                    )
                current = self._chapter_workspace_row(existing)
                current_revision = int(current["chapter_revision"])
                if str(current.get("status") or "") == "archived":
                    if expected in {0, current_revision}:
                        connection.commit()
                        return current
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                chapter_revision = current_revision + 1
                meta = dict(current.get("metadata") or {})
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=int(current["blueprint_revision"]),
                    blueprint_hash=str(current["blueprint_hash"]),
                    title=str(current["title"]),
                    parent_chapter_id=current.get("parent_chapter_id"),
                    order_index=int(current["order"]),
                    status="archived",
                    approval_status=str(current.get("approval_status") or "not_started"),
                    chapter_revision=chapter_revision,
                    head_content_revision=int(current.get("head_content_revision") or 0),
                    formal_content_revision=int(current.get("formal_content_revision") or 0),
                    head_context_revision=int(current.get("head_context_revision") or 0),
                    metadata=meta,
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        status = 'archived',
                        chapter_revision = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (chapter_revision, state_hash, now, normalized),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterWorkspaceArchived",
                    "ChapterWorkspace",
                    normalized,
                    {
                        "chapter_id": normalized,
                        "chapter_revision": chapter_revision,
                        "state_hash": state_hash,
                        "actor": actor or {},
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.chapter_workspace(normalized) or {}

    def update_chapter_workspace_metadata(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        metadata: dict[str, Any],
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update user metadata only; does not rewrite Blueprint-derived seed fields."""
        normalized = self._normalize_chapter_id(chapter_id)
        if not isinstance(metadata, dict):
            raise ControlPlaneError(
                "CHAPTER_METADATA_INVALID",
                "metadata 必须是对象。",
                status_code=400,
            )
        try:
            expected = int(expected_chapter_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    raise ControlPlaneError(
                        "CHAPTER_NOT_FOUND",
                        f"章节 Workspace 不存在: {normalized}",
                        status_code=404,
                    )
                current = self._chapter_workspace_row(existing)
                if str(current.get("status") or "") == "archived":
                    raise ControlPlaneError(
                        "CHAPTER_ARCHIVED",
                        "已归档章节不能修改元数据。",
                        status_code=409,
                    )
                current_revision = int(current["chapter_revision"])
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                merged = dict(current.get("metadata") or {})
                merged.update(metadata)
                chapter_revision = current_revision + 1
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=int(current["blueprint_revision"]),
                    blueprint_hash=str(current["blueprint_hash"]),
                    title=str(current["title"]),
                    parent_chapter_id=current.get("parent_chapter_id"),
                    order_index=int(current["order"]),
                    status=str(current.get("status") or "active"),
                    approval_status=str(current.get("approval_status") or "not_started"),
                    chapter_revision=chapter_revision,
                    head_content_revision=int(current.get("head_content_revision") or 0),
                    formal_content_revision=int(current.get("formal_content_revision") or 0),
                    head_context_revision=int(current.get("head_context_revision") or 0),
                    metadata=merged,
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        chapter_revision = ?,
                        metadata_json = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (chapter_revision, _json(merged), state_hash, now, normalized),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterWorkspaceMetadataSaved",
                    "ChapterWorkspace",
                    normalized,
                    {
                        "chapter_id": normalized,
                        "chapter_revision": chapter_revision,
                        "state_hash": state_hash,
                        "actor": actor or {},
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.chapter_workspace(normalized) or {}

    @staticmethod
    def _normalize_context_items(items: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
        from document_pipeline.contracts import ChapterContextItem

        if not isinstance(items, list):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID",
                "context items 必须是数组。",
                status_code=400,
            )
        if len(items) > 200:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID",
                "单章 Context 最多 200 项。",
                status_code=400,
            )
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ControlPlaneError(
                    "CHAPTER_CONTEXT_INVALID",
                    "每个 Context Item 必须是对象。",
                    status_code=400,
                )
            try:
                model = ChapterContextItem.model_validate(raw)
            except Exception as exc:
                raise ControlPlaneError(
                    "CHAPTER_CONTEXT_INVALID",
                    f"Context Item 校验失败: {exc}",
                    status_code=400,
                ) from exc
            normalized.append(model.model_dump(mode="json"))
        ids = [item["item_id"] for item in normalized]
        if len(ids) != len(set(ids)):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID",
                "Context Item item_id 不允许重复。",
                status_code=400,
            )
        orders = [int(item["order"]) for item in normalized]
        if len(orders) != len(set(orders)):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID",
                "Context Item order 不允许重复。",
                status_code=400,
            )
        normalized.sort(key=lambda item: (int(item["order"]), str(item["item_id"])))
        return normalized

    @staticmethod
    def _context_items_hash(items: list[dict[str, Any]]) -> str:
        return hashlib.sha256(_json(items).encode("utf-8")).hexdigest()

    @staticmethod
    def _chapter_context_revision_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        items = _decode(item.pop("items_json", "[]"), [])
        if not isinstance(items, list):
            items = []
        actor = _decode(item.pop("actor_json", "{}"), {})
        if not isinstance(actor, dict):
            actor = {}
        parent = item.get("parent_context_revision")
        return {
            "chapter_id": str(item.get("chapter_id") or ""),
            "context_revision": int(item.get("context_revision") or 0),
            "parent_context_revision": int(parent) if parent is not None else None,
            "items": items,
            "content_hash": str(item.get("content_hash") or ""),
            "seeded_from_blueprint": bool(int(item.get("seeded_from_blueprint") or 0)),
            "actor": actor,
            "created_at": str(item.get("created_at") or ""),
        }

    def chapter_context_revisions(
        self,
        chapter_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_chapter_id(chapter_id)
        capped = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM chapter_context_revisions
                WHERE chapter_id = ?
                ORDER BY context_revision DESC
                LIMIT ?
                """,
                (normalized, capped),
            ).fetchall()
        return [self._chapter_context_revision_row(row) for row in rows]

    def chapter_context_revision(
        self,
        chapter_id: str,
        context_revision: int,
    ) -> dict[str, Any] | None:
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            revision = int(context_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_REVISION_INVALID",
                "context_revision 必须是整数。",
                status_code=400,
            ) from exc
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM chapter_context_revisions
                WHERE chapter_id = ? AND context_revision = ?
                """,
                (normalized, revision),
            ).fetchone()
        return self._chapter_context_revision_row(row) if row else None

    def chapter_context_head(self, chapter_id: str) -> dict[str, Any] | None:
        workspace = self.chapter_workspace(chapter_id)
        if workspace is None:
            return None
        head = int(workspace.get("head_context_revision") or 0)
        if head < 1:
            return None
        return self.chapter_context_revision(chapter_id, head)

    def append_chapter_context_revision(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        items: list[dict[str, Any]],
        seeded_from_blueprint: bool = False,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append-only context revision; bumps chapter_revision and head pointer."""
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            expected = int(expected_chapter_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc
        normalized_items = self._normalize_context_items(items)
        content_hash = self._context_items_hash(normalized_items)
        actor_value = {
            "type": str((actor or {}).get("type") or "")[:64],
            "id": str((actor or {}).get("id") or "")[:128],
            "role": str((actor or {}).get("role") or "")[:32],
        }
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    raise ControlPlaneError(
                        "CHAPTER_NOT_FOUND",
                        f"章节 Workspace 不存在: {normalized}",
                        status_code=404,
                    )
                current = self._chapter_workspace_row(existing)
                if str(current.get("status") or "") == "archived":
                    raise ControlPlaneError(
                        "CHAPTER_ARCHIVED",
                        "已归档章节不能修改 Context。",
                        status_code=409,
                    )
                current_revision = int(current["chapter_revision"])
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                head_context = int(current.get("head_context_revision") or 0)
                # Idempotent no-op when payload matches current head.
                if head_context >= 1:
                    head_row = connection.execute(
                        """
                        SELECT * FROM chapter_context_revisions
                        WHERE chapter_id = ? AND context_revision = ?
                        """,
                        (normalized, head_context),
                    ).fetchone()
                    if head_row is not None:
                        head = self._chapter_context_revision_row(head_row)
                        if str(head.get("content_hash") or "") == content_hash:
                            connection.commit()
                            return {
                                "chapter": current,
                                "context": head,
                                "unchanged": True,
                            }

                next_context_revision = head_context + 1
                parent = head_context if head_context >= 1 else None
                chapter_revision = current_revision + 1
                meta = dict(current.get("metadata") or {})
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=int(current["blueprint_revision"]),
                    blueprint_hash=str(current["blueprint_hash"]),
                    title=str(current["title"]),
                    parent_chapter_id=current.get("parent_chapter_id"),
                    order_index=int(current["order"]),
                    status=str(current.get("status") or "active"),
                    approval_status=str(current.get("approval_status") or "not_started"),
                    chapter_revision=chapter_revision,
                    head_content_revision=int(current.get("head_content_revision") or 0),
                    formal_content_revision=int(current.get("formal_content_revision") or 0),
                    head_context_revision=next_context_revision,
                    metadata=meta,
                )
                connection.execute(
                    """
                    INSERT INTO chapter_context_revisions(
                        chapter_id, context_revision, items_json, content_hash,
                        parent_context_revision, seeded_from_blueprint, actor_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized,
                        next_context_revision,
                        _json(normalized_items),
                        content_hash,
                        parent,
                        1 if seeded_from_blueprint else 0,
                        _json(actor_value),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        chapter_revision = ?,
                        head_context_revision = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (
                        chapter_revision,
                        next_context_revision,
                        state_hash,
                        now,
                        normalized,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterContextRevisionAppended",
                    "ChapterContext",
                    f"{normalized}@{next_context_revision}",
                    {
                        "chapter_id": normalized,
                        "context_revision": next_context_revision,
                        "parent_context_revision": parent,
                        "chapter_revision": chapter_revision,
                        "content_hash": content_hash,
                        "seeded_from_blueprint": bool(seeded_from_blueprint),
                        "item_count": len(normalized_items),
                        "actor": actor_value,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        chapter = self.chapter_workspace(normalized) or {}
        context = self.chapter_context_revision(normalized, int(chapter.get("head_context_revision") or 0)) or {}
        return {"chapter": chapter, "context": context, "unchanged": False}

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
                        _json(normalized), str(source or "v3_quality_revalidate"),
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
            "source": str(source or "v3_quality_revalidate"),
            "source_revision": source_revision,
            "created_at": created_at,
        }

    @staticmethod
    def _normalize_content_blocks(blocks: list[Any], *, chapter_id: str) -> list[dict[str, Any]]:
        from document_pipeline.contracts import ContentBlock

        if not isinstance(blocks, list):
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                "blocks 必须是数组。",
                status_code=400,
            )
        if len(blocks) > 500:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                "单章正文最多 500 个 Block。",
                status_code=400,
            )
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(blocks):
            if not isinstance(raw, dict):
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    "每个 ContentBlock 必须是对象。",
                    status_code=400,
                )
            payload = dict(raw)
            payload.setdefault("target_node_id", chapter_id)
            payload.setdefault("order", index)
            payload.setdefault("confidence", float(payload.get("confidence") or 0.8))
            if not payload.get("source"):
                payload["source"] = "AI_GENERATED"
            try:
                model = ContentBlock.model_validate(payload)
            except Exception as exc:
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    f"ContentBlock 校验失败: {exc}",
                    status_code=400,
                ) from exc
            normalized.append(model.model_dump(mode="json"))
        ids = [item["block_id"] for item in normalized]
        if len(ids) != len(set(ids)):
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                "ContentBlock block_id 不允许重复。",
                status_code=400,
            )
        normalized.sort(key=lambda item: (int(item.get("order") or 0), str(item["block_id"])))
        for index, item in enumerate(normalized):
            item["order"] = index
        return normalized

    @staticmethod
    def _content_blocks_hash(blocks: list[dict[str, Any]]) -> str:
        return hashlib.sha256(_json(blocks).encode("utf-8")).hexdigest()

    @staticmethod
    def _chapter_content_revision_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        blocks = _decode(item.pop("blocks_json", "[]"), [])
        if not isinstance(blocks, list):
            blocks = []
        actor = _decode(item.pop("actor_json", "{}"), {})
        if not isinstance(actor, dict):
            actor = {}
        policy = _decode(item.pop("approval_policy_json", "{}"), {})
        if not isinstance(policy, dict):
            policy = {}
        parent = item.get("parent_content_revision")
        return {
            "chapter_id": str(item.get("chapter_id") or ""),
            "content_revision": int(item.get("content_revision") or 0),
            "parent_content_revision": int(parent) if parent is not None else None,
            "blocks": blocks,
            "content_hash": str(item.get("content_hash") or ""),
            "source": str(item.get("source") or "user_edit"),
            "approval_policy": policy,
            "actor": actor,
            "created_at": str(item.get("created_at") or ""),
        }

    def chapter_content_revisions(
        self,
        chapter_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_chapter_id(chapter_id)
        capped = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM chapter_content_revisions
                WHERE chapter_id = ?
                ORDER BY content_revision DESC
                LIMIT ?
                """,
                (normalized, capped),
            ).fetchall()
        return [self._chapter_content_revision_row(row) for row in rows]

    def chapter_content_revision(
        self,
        chapter_id: str,
        content_revision: int,
    ) -> dict[str, Any] | None:
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            revision = int(content_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_REVISION_INVALID",
                "content_revision 必须是整数。",
                status_code=400,
            ) from exc
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM chapter_content_revisions
                WHERE chapter_id = ? AND content_revision = ?
                """,
                (normalized, revision),
            ).fetchone()
        return self._chapter_content_revision_row(row) if row else None

    def chapter_content_head(self, chapter_id: str) -> dict[str, Any] | None:
        workspace = self.chapter_workspace(chapter_id)
        if workspace is None:
            return None
        head = int(workspace.get("head_content_revision") or 0)
        if head < 1:
            return None
        return self.chapter_content_revision(chapter_id, head)

    def chapter_formal_content(self, chapter_id: str) -> dict[str, Any] | None:
        workspace = self.chapter_workspace(chapter_id)
        if workspace is None:
            return None
        formal = int(workspace.get("formal_content_revision") or 0)
        if formal < 1:
            return None
        return self.chapter_content_revision(chapter_id, formal)

    def append_chapter_content_revision(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        blocks: list[dict[str, Any]],
        source: str,
        approval_policy: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        set_formal: bool = False,
        approval_status: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            expected = int(expected_chapter_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc
        source_value = str(source or "").strip()
        if source_value not in {"user_edit", "ai_draft", "restore", "merge", "auto_approve"}:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                f"不支持的 content source: {source_value}",
                status_code=400,
            )
        normalized_blocks = self._normalize_content_blocks(blocks, chapter_id=normalized)
        content_hash = self._content_blocks_hash(normalized_blocks)
        policy = dict(approval_policy or {})
        actor_value = {
            "type": str((actor or {}).get("type") or "")[:64],
            "id": str((actor or {}).get("id") or "")[:128],
            "role": str((actor or {}).get("role") or "")[:32],
        }
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    raise ControlPlaneError(
                        "CHAPTER_NOT_FOUND",
                        f"章节 Workspace 不存在: {normalized}",
                        status_code=404,
                    )
                current = self._chapter_workspace_row(existing)
                if str(current.get("status") or "") == "archived":
                    raise ControlPlaneError(
                        "CHAPTER_ARCHIVED",
                        "已归档章节不能修改正文。",
                        status_code=409,
                    )
                current_revision = int(current["chapter_revision"])
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                head_content = int(current.get("head_content_revision") or 0)
                if head_content >= 1:
                    head_row = connection.execute(
                        """
                        SELECT * FROM chapter_content_revisions
                        WHERE chapter_id = ? AND content_revision = ?
                        """,
                        (normalized, head_content),
                    ).fetchone()
                    if head_row is not None:
                        head = self._chapter_content_revision_row(head_row)
                        if str(head.get("content_hash") or "") == content_hash and not set_formal:
                            connection.commit()
                            return {
                                "chapter": current,
                                "content": head,
                                "unchanged": True,
                            }
                next_content_revision = head_content + 1
                parent = head_content if head_content >= 1 else None
                chapter_revision = current_revision + 1
                formal = int(current.get("formal_content_revision") or 0)
                if set_formal:
                    formal = next_content_revision
                status = str(approval_status or current.get("approval_status") or "draft")
                meta = dict(current.get("metadata") or {})
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=int(current["blueprint_revision"]),
                    blueprint_hash=str(current["blueprint_hash"]),
                    title=str(current["title"]),
                    parent_chapter_id=current.get("parent_chapter_id"),
                    order_index=int(current["order"]),
                    status=str(current.get("status") or "active"),
                    approval_status=status,
                    chapter_revision=chapter_revision,
                    head_content_revision=next_content_revision,
                    formal_content_revision=formal,
                    head_context_revision=int(current.get("head_context_revision") or 0),
                    metadata=meta,
                )
                connection.execute(
                    """
                    INSERT INTO chapter_content_revisions(
                        chapter_id, content_revision, blocks_json, content_hash,
                        parent_content_revision, source, approval_policy_json, actor_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized,
                        next_content_revision,
                        _json(normalized_blocks),
                        content_hash,
                        parent,
                        source_value,
                        _json(policy),
                        _json(actor_value),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        chapter_revision = ?,
                        head_content_revision = ?,
                        formal_content_revision = ?,
                        approval_status = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (
                        chapter_revision,
                        next_content_revision,
                        formal,
                        status,
                        state_hash,
                        now,
                        normalized,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterContentRevisionAppended",
                    "ChapterContent",
                    f"{normalized}@{next_content_revision}",
                    {
                        "chapter_id": normalized,
                        "content_revision": next_content_revision,
                        "parent_content_revision": parent,
                        "chapter_revision": chapter_revision,
                        "content_hash": content_hash,
                        "source": source_value,
                        "set_formal": bool(set_formal),
                        "block_count": len(normalized_blocks),
                        "actor": actor_value,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        chapter = self.chapter_workspace(normalized) or {}
        content = self.chapter_content_revision(
            normalized, int(chapter.get("head_content_revision") or 0)
        ) or {}
        return {"chapter": chapter, "content": content, "unchanged": False}

    def set_chapter_formal_pointer(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        content_revision: int,
        content_hash: str,
        approval_status: str = "approved",
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Point formal_revision at an existing content revision without rewriting blocks."""
        normalized = self._normalize_chapter_id(chapter_id)
        try:
            expected = int(expected_chapter_revision)
            target_revision = int(content_revision)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "revision 参数必须是整数。",
                status_code=400,
            ) from exc
        wanted_hash = str(content_hash or "").strip()
        if not wanted_hash:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_HASH_REQUIRED",
                "缺少 content_hash。",
                status_code=400,
            )
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_workspaces WHERE chapter_id = ?",
                    (normalized,),
                ).fetchone()
                if existing is None:
                    raise ControlPlaneError(
                        "CHAPTER_NOT_FOUND",
                        f"章节 Workspace 不存在: {normalized}",
                        status_code=404,
                    )
                current = self._chapter_workspace_row(existing)
                if str(current.get("status") or "") == "archived":
                    raise ControlPlaneError(
                        "CHAPTER_ARCHIVED",
                        "已归档章节不能确认正文。",
                        status_code=409,
                    )
                current_revision = int(current["chapter_revision"])
                if expected != current_revision:
                    raise ControlPlaneError(
                        "CHAPTER_REVISION_CONFLICT",
                        "章节状态已变化，请刷新后重试。",
                        status_code=409,
                        details={
                            "chapter_id": normalized,
                            "expected_chapter_revision": expected,
                            "current_chapter_revision": current_revision,
                        },
                    )
                content_row = connection.execute(
                    """
                    SELECT * FROM chapter_content_revisions
                    WHERE chapter_id = ? AND content_revision = ?
                    """,
                    (normalized, target_revision),
                ).fetchone()
                if content_row is None:
                    raise ControlPlaneError(
                        "CHAPTER_CONTENT_NOT_FOUND",
                        f"Content revision 不存在: {normalized}@{target_revision}",
                        status_code=404,
                    )
                content = self._chapter_content_revision_row(content_row)
                if str(content.get("content_hash") or "") != wanted_hash:
                    raise ControlPlaneError(
                        "CHAPTER_CONTENT_HASH_MISMATCH",
                        "content_hash 与目标 revision 不一致。",
                        status_code=409,
                    )
                chapter_revision = current_revision + 1
                meta = dict(current.get("metadata") or {})
                state_hash = self._chapter_workspace_state_hash(
                    chapter_id=normalized,
                    blueprint_revision=int(current["blueprint_revision"]),
                    blueprint_hash=str(current["blueprint_hash"]),
                    title=str(current["title"]),
                    parent_chapter_id=current.get("parent_chapter_id"),
                    order_index=int(current["order"]),
                    status=str(current.get("status") or "active"),
                    approval_status=str(approval_status),
                    chapter_revision=chapter_revision,
                    head_content_revision=int(current.get("head_content_revision") or 0),
                    formal_content_revision=target_revision,
                    head_context_revision=int(current.get("head_context_revision") or 0),
                    metadata=meta,
                )
                connection.execute(
                    """
                    UPDATE chapter_workspaces SET
                        chapter_revision = ?,
                        formal_content_revision = ?,
                        approval_status = ?,
                        state_hash = ?,
                        updated_at = ?
                    WHERE chapter_id = ?
                    """,
                    (
                        chapter_revision,
                        target_revision,
                        str(approval_status),
                        state_hash,
                        now,
                        normalized,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterFormalPointerUpdated",
                    "ChapterWorkspace",
                    normalized,
                    {
                        "chapter_id": normalized,
                        "formal_content_revision": target_revision,
                        "content_hash": wanted_hash,
                        "chapter_revision": chapter_revision,
                        "approval_status": str(approval_status),
                        "actor": actor or {},
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.chapter_workspace(normalized) or {}

    def record_chapter_approval_receipt(
        self,
        *,
        chapter_id: str,
        content_revision: int,
        content_hash: str,
        decision: str,
        principal_id: str,
        confirmation_required: bool,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_chapter_id(chapter_id)
        decision_value = str(decision or "").strip()
        if decision_value not in {"approved", "auto_approved"}:
            raise ControlPlaneError(
                "CHAPTER_APPROVAL_INVALID",
                "decision 必须是 approved 或 auto_approved。",
                status_code=400,
            )
        principal = str(principal_id or "").strip()
        if not principal:
            raise ControlPlaneError(
                "CHAPTER_APPROVAL_INVALID",
                "缺少 principal_id。",
                status_code=400,
            )
        if decision_value == "approved" and principal in {"system", "auto"}:
            raise ControlPlaneError(
                "CHAPTER_APPROVAL_INVALID",
                "人工确认不得使用 system/auto principal。",
                status_code=403,
            )
        if decision_value == "auto_approved" and confirmation_required:
            raise ControlPlaneError(
                "CHAPTER_APPROVAL_INVALID",
                "confirmation_required=true 时不得签发 auto_approved。",
                status_code=409,
            )
        actor_value = {
            "type": str((actor or {}).get("type") or "")[:64],
            "id": str((actor or {}).get("id") or principal)[:128],
            "role": str((actor or {}).get("role") or "")[:32],
        }
        body = {
            "gate_id": "H2_CHAPTER_APPROVAL",
            "chapter_id": normalized,
            "content_revision": int(content_revision),
            "content_hash": str(content_hash),
            "decision": decision_value,
            "principal_id": principal,
            "confirmation_required": bool(confirmation_required),
            "actor": actor_value,
        }
        receipt_hash = hashlib.sha256(_json(body).encode("utf-8")).hexdigest()
        receipt_id = f"h2-{receipt_hash[:24]}"
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM chapter_approval_receipts WHERE receipt_id = ? OR receipt_hash = ?",
                    (receipt_id, receipt_hash),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    row = dict(existing)
                    row["actor"] = _decode(row.pop("actor_json", "{}"), {})
                    row["confirmation_required"] = bool(int(row.get("confirmation_required") or 0))
                    return row
                connection.execute(
                    """
                    INSERT INTO chapter_approval_receipts(
                        receipt_id, receipt_hash, chapter_id, content_revision, content_hash,
                        decision, principal_id, confirmation_required, actor_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        receipt_hash,
                        normalized,
                        int(content_revision),
                        str(content_hash),
                        decision_value,
                        principal,
                        1 if confirmation_required else 0,
                        _json(actor_value),
                        now,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "ChapterApprovalReceiptIssued",
                    "ChapterApproval",
                    receipt_id,
                    {
                        "chapter_id": normalized,
                        "content_revision": int(content_revision),
                        "content_hash": str(content_hash),
                        "decision": decision_value,
                        "principal_id": principal,
                        "confirmation_required": bool(confirmation_required),
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "receipt_id": receipt_id,
            "receipt_hash": receipt_hash,
            "chapter_id": normalized,
            "content_revision": int(content_revision),
            "content_hash": str(content_hash),
            "decision": decision_value,
            "principal_id": principal,
            "confirmation_required": bool(confirmation_required),
            "actor": actor_value,
            "created_at": now,
            "gate_id": "H2_CHAPTER_APPROVAL",
        }

    def chapter_approval_receipts(
        self,
        chapter_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_chapter_id(chapter_id)
        capped = max(1, min(int(limit), 200))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM chapter_approval_receipts
                WHERE chapter_id = ?
                ORDER BY created_at DESC, content_revision DESC
                LIMIT ?
                """,
                (normalized, capped),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["actor"] = _decode(item.pop("actor_json", "{}"), {})
            item["confirmation_required"] = bool(int(item.get("confirmation_required") or 0))
            item["gate_id"] = "H2_CHAPTER_APPROVAL"
            result.append(item)
        return result

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

    def latest_gate_evaluations(self) -> list[dict[str, Any]]:
        """Return the most recently persisted evaluation for every gate command."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM gate_evaluations
                WHERE rowid IN (
                    SELECT MAX(rowid) FROM gate_evaluations GROUP BY command
                )
                ORDER BY command
                """
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
        artifact_value = str(artifact_path or "").strip().replace("\\", "/")
        artifact_parts = Path(artifact_value).parts
        if (
            verdict not in {"pass", "block"}
            or not gate_input_fingerprint
            or not artifact_sha256
            or not str(rules_version or "").strip()
            or not artifact_value
            or Path(artifact_value).is_absolute()
            or ".." in artifact_parts
        ):
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
                        artifact_value,
                        artifact_sha256,
                        str(rules_version).strip(),
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
                        "artifact_path": artifact_value,
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
                "SELECT receipt_id FROM gate_receipts ORDER BY rowid DESC LIMIT 1"
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
                expires_at = _parse_utc_timestamp(row["expires_at"], label="上传 token 到期时间")
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
        consume_upload: bool = False,
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
                upload_row = connection.execute(
                    "SELECT filename, sha256, size_bytes, status, expires_at FROM material_upload_tokens WHERE upload_token = ?",
                    (token,),
                ).fetchone()
                expected_status = "pending" if consume_upload else "consumed"
                if upload_row is None or str(upload_row["status"] or "") != expected_status:
                    raise ControlPlaneError(
                        "UPLOAD_TOKEN_INVALID",
                        "材料提交必须关联当前工作区可用的 upload_token。",
                        status_code=409,
                    )
                if consume_upload:
                    expires_at = _parse_utc_timestamp(upload_row["expires_at"], label="上传 token 到期时间")
                    if expires_at <= datetime.now(timezone.utc):
                        raise ControlPlaneError("UPLOAD_TOKEN_EXPIRED", "上传 token 已过期。", status_code=409)
                if (
                    str(upload_row["filename"] or "") != filename
                    or str(upload_row["sha256"] or "") != sha256
                    or int(upload_row["size_bytes"] or 0) != size_bytes
                ):
                    raise ControlPlaneError("UPLOAD_HASH_MISMATCH", "材料提交与 upload_token 证据不一致。", status_code=409)
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
                if consume_upload:
                    connection.execute(
                        """
                        UPDATE material_upload_tokens
                        SET status = 'consumed', consumed_at = ?
                        WHERE upload_token = ? AND status = 'pending'
                        """,
                        (created_at, token),
                    )
                    self._event(
                        connection, revision, "MaterialUploadConsumed", "MaterialUpload", token,
                        {"sha256": sha256, "filename": filename},
                    )
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
        material_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        material_id = str(item_id or "").strip()
        kind = str(verification_type or "").strip()
        decision = str(verdict or "").strip().lower()
        if not material_id or not kind or decision not in {"verified", "rejected", "uploaded"}:
            raise ControlPlaneError("COMMAND_INVALID", "材料核验记录无效。", status_code=400)
        verification_id = str(uuid.uuid4())
        created_at = _now()
        payload = dict(verification) if isinstance(verification, dict) else {}
        state_payload = dict(material_state) if isinstance(material_state, dict) else None
        if state_payload is not None and str(state_payload.get("item_id") or "").strip() != material_id:
            raise ControlPlaneError("COMMAND_INVALID", "材料核验状态与材料 ID 不一致。", status_code=400)
        actor_value = {
            "type": str(actor.get("type") or "")[:64],
            "id": str(actor.get("id") or "")[:128],
            "role": str(actor.get("role") or "")[:32],
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if state_payload is not None:
                    existing = connection.execute(
                        "SELECT created_at FROM material_states WHERE item_id = ?",
                        (material_id,),
                    ).fetchone()
                    state_created_at = str(existing["created_at"]) if existing is not None else created_at
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
                            material_id,
                            str(state_payload.get("response_status") or "deferred"),
                            str(state_payload.get("lifecycle_status") or "missing"),
                            str(state_payload.get("evidence_status") or "missing"),
                            _json(state_payload), str(source or "v3_command"), state_created_at, created_at,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO material_verifications(
                        verification_id, item_id, verification_type, verdict,
                        verification_json, actor_json, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification_id, material_id, kind, decision,
                        _json(payload), _json(actor_value), str(source or "v3_command"), created_at,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "MaterialVerified", "MaterialVerification", verification_id,
                    {"item_id": material_id, "verification_type": kind, "verdict": decision},
                )
                if state_payload is not None:
                    self._event(
                        connection, revision, "MaterialStateChanged", "Material", material_id,
                        {"response_status": str(state_payload.get("response_status") or "deferred"), "source": source},
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
            "source": str(source or "v3_command"),
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

    def material_audit_summary(self) -> dict[str, dict[str, Any]]:
        """Return count/latest submission and latest verification for each material."""
        with self._connection() as connection:
            submission_counts = connection.execute(
                "SELECT item_id, COUNT(*) AS count FROM material_submissions GROUP BY item_id"
            ).fetchall()
            latest_submissions = connection.execute(
                """
                SELECT * FROM material_submissions
                WHERE rowid IN (SELECT MAX(rowid) FROM material_submissions GROUP BY item_id)
                """
            ).fetchall()
            latest_verifications = connection.execute(
                """
                SELECT * FROM material_verifications
                WHERE rowid IN (SELECT MAX(rowid) FROM material_verifications GROUP BY item_id)
                """
            ).fetchall()
        summary: dict[str, dict[str, Any]] = {
            str(row["item_id"]): {"submission_count": int(row["count"] or 0)}
            for row in submission_counts
        }
        for row in latest_submissions:
            item = dict(row)
            item["actor"] = _decode(item.pop("actor_json", ""), {})
            summary.setdefault(str(item["item_id"]), {})["latest_submission"] = item
        for row in latest_verifications:
            item = dict(row)
            item["verification"] = _decode(item.pop("verification_json", ""), {})
            item["actor"] = _decode(item.pop("actor_json", ""), {})
            summary.setdefault(str(item["item_id"]), {})["latest_verification"] = item
        return summary

    def material_state(self, item_id: str) -> dict[str, Any] | None:
        wanted = str(item_id or "").strip()
        return next((item for item in self.material_states() if item.get("item_id") == wanted), None)

    def upsert_material_state(self, item: dict[str, Any], *, source: str = "v3_command") -> dict[str, Any]:
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

    def replace_issue_states(self, issues: list[dict[str, Any]], *, source: str = "v3_control") -> int:
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
        source: str = "v3_command",
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

    # V3 semantic artifacts deliberately live in their own append-only ledger.
    # These methods are only called by the Proposal/Gate/Promotion services; V3
    # agents receive a proposal sandbox and never a ControlStore instance.

    @staticmethod
    def _migrate_v3_kernel_columns(connection: sqlite3.Connection) -> None:
        """Additive migration for PR-15.1 exact-binding columns (fail-open on exists)."""

        def columns(table: str) -> set[str]:
            return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}

        def add(table: str, column: str, ddl: str) -> None:
            if column not in columns(table):
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "stage_runs" in tables:
            add("stage_runs", "output_json", "output_json TEXT")
        if "content_unit_states" in tables:
            add(
                "content_unit_states",
                "writer_fingerprint",
                "writer_fingerprint TEXT NOT NULL DEFAULT ''",
            )
            add(
                "content_unit_states",
                "stale_reason",
                "stale_reason TEXT NOT NULL DEFAULT ''",
            )
            add(
                "content_unit_states",
                "current_chapter_id",
                "current_chapter_id TEXT NOT NULL DEFAULT ''",
            )
            add(
                "content_unit_states",
                "current_chapter_title",
                "current_chapter_title TEXT NOT NULL DEFAULT ''",
            )
            add(
                "content_unit_states",
                "progress_phase",
                "progress_phase TEXT NOT NULL DEFAULT ''",
            )
            add(
                "content_unit_states",
                "draft_preview",
                "draft_preview TEXT NOT NULL DEFAULT ''",
            )
            connection.execute(
                """
                UPDATE content_unit_states
                SET state = 'stale',
                    stale_reason = CASE
                        WHEN stale_reason = '' THEN
                            '旧正文缺少当前写作器指纹，必须重新生成。'
                        ELSE stale_reason
                    END,
                    invalidation_reason = CASE
                        WHEN invalidation_reason = '' THEN
                            '旧正文由过期写作器生成，禁止继续预览、整合或下载。'
                        ELSE invalidation_reason
                    END
                WHERE state = 'completed' AND writer_fingerprint = ''
                """
            )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS llm_requests (
                request_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                stage_id TEXT NOT NULL,
                request_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(operation_id, stage_id, request_index)
            );
            CREATE INDEX IF NOT EXISTS idx_llm_requests_operation
                ON llm_requests(operation_id, stage_id, request_index);
            """
        )

        if "v3_proposals" in tables:
            add("v3_proposals", "workspace_id", "workspace_id TEXT NOT NULL DEFAULT ''")
            add("v3_proposals", "declared_dependencies_json", "declared_dependencies_json TEXT NOT NULL DEFAULT '[]'")
            add("v3_proposals", "canonical_payload_hash", "canonical_payload_hash TEXT NOT NULL DEFAULT ''")
            add("v3_proposals", "inference_receipt_refs_json", "inference_receipt_refs_json TEXT NOT NULL DEFAULT '[]'")
            add("v3_proposals", "payload_schema_version", "payload_schema_version TEXT NOT NULL DEFAULT 'v3'")
            add("v3_proposals", "canonicalization_version", "canonicalization_version TEXT NOT NULL DEFAULT 'v3-canon-2'")
            add("v3_validation_reports", "proposal_hash", "proposal_hash TEXT NOT NULL DEFAULT ''")
            add("v3_validation_reports", "report_hash", "report_hash TEXT NOT NULL DEFAULT ''")
            for col, ddl in (
                ("receipt_hash", "receipt_hash TEXT NOT NULL DEFAULT ''"),
                ("workspace_id", "workspace_id TEXT NOT NULL DEFAULT ''"),
                ("validation_report_id", "validation_report_id TEXT NOT NULL DEFAULT ''"),
                ("validation_report_hash", "validation_report_hash TEXT NOT NULL DEFAULT ''"),
                ("artifact_kind", "artifact_kind TEXT NOT NULL DEFAULT ''"),
                ("gate_policy_version", "gate_policy_version TEXT NOT NULL DEFAULT ''"),
                ("issuer", "issuer TEXT NOT NULL DEFAULT ''"),
                ("dependency_fingerprint", "dependency_fingerprint TEXT NOT NULL DEFAULT ''"),
                ("dependency_snapshot_json", "dependency_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
                ("issued_at", "issued_at TEXT NOT NULL DEFAULT ''"),
                ("expires_at", "expires_at TEXT"),
                ("receipt_json", "receipt_json TEXT NOT NULL DEFAULT '{}'"),
            ):
                add("v3_gate_receipts", col, ddl)
            add("v3_artifact_revisions", "proposal_hash", "proposal_hash TEXT NOT NULL DEFAULT ''")
            for col, ddl in (
                ("receipt_hash", "receipt_hash TEXT NOT NULL DEFAULT ''"),
                ("workspace_id", "workspace_id TEXT NOT NULL DEFAULT ''"),
                ("proposal_hash", "proposal_hash TEXT NOT NULL DEFAULT ''"),
                ("base_revision", "base_revision INTEGER NOT NULL DEFAULT 0"),
                ("dependency_snapshot_json", "dependency_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
                ("gate_receipts_json", "gate_receipts_json TEXT NOT NULL DEFAULT '[]'"),
                ("policy_version", "policy_version TEXT NOT NULL DEFAULT ''"),
            ):
                add("v3_promotion_receipts", col, ddl)

    def append_v3_inference_receipt(
        self,
        receipt: dict[str, Any],
        *,
        kernel_seal: Any = None,
    ) -> dict[str, Any]:
        """Append immutable inference provenance through the trusted service path."""

        from document_pipeline.kernel_seal import KERNEL_SEAL
        from document_pipeline.proposals import InferenceReceipt

        if kernel_seal is not KERNEL_SEAL:
            raise ControlPlaneError(
                "V3_INFERENCE_RECEIPT_SEALED",
                "InferenceReceipt 只能由可信推理凭证服务写入。",
                status_code=403,
            )
        model = InferenceReceipt.model_validate(receipt)
        stored = model.storage_record()
        if model.workspace_id != self.context.workspace_id:
            raise ControlPlaneError(
                "V3_INFERENCE_WORKSPACE_MISMATCH",
                "InferenceReceipt workspace 与 Store 不一致。",
                status_code=409,
            )
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = connection.execute(
                    "SELECT * FROM v3_inference_receipts WHERE receipt_id = ? OR receipt_hash = ?",
                    (model.receipt_id, stored["receipt_hash"]),
                ).fetchone()
                if duplicate is not None:
                    existing = self._v3_inference_receipt_row(duplicate)
                    if existing["receipt_hash"] != stored["receipt_hash"]:
                        raise ControlPlaneError(
                            "V3_INFERENCE_RECEIPT_CONFLICT",
                            "InferenceReceipt ID 已绑定其他内容。",
                            status_code=409,
                        )
                    connection.commit()
                    return existing
                connection.execute(
                    """
                    INSERT INTO v3_inference_receipts(
                        receipt_id, receipt_hash, workspace_id, invocation_id,
                        capability_id, capability_version, receipt_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        model.receipt_id,
                        stored["receipt_hash"],
                        model.workspace_id,
                        model.invocation_id,
                        model.capability_id,
                        model.capability_version,
                        _json(stored),
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.v3_inference_receipt(model.receipt_id) or {}

    def v3_inference_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM v3_inference_receipts WHERE receipt_id = ?",
                (str(receipt_id),),
            ).fetchone()
        return self._v3_inference_receipt_row(row) if row else None

    def append_v3_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        required = (
            "proposal_id", "artifact_kind", "producer_role", "operation_id",
            "dependency_fingerprint", "proposal_hash", "prompt_version", "model_fingerprint",
        )
        if any(not str(proposal.get(key) or "").strip() for key in required):
            raise ControlPlaneError("V3_PROPOSAL_INVALID", "Proposal 缺少必填字段。", status_code=400)
        if int(proposal.get("base_revision", -1)) < 0 or not isinstance(proposal.get("payload"), dict):
            raise ControlPlaneError("V3_PROPOSAL_INVALID", "Proposal 的 revision 或 payload 无效。", status_code=400)
        proposal_id = str(proposal["proposal_id"])
        proposal_hash = str(proposal["proposal_hash"])
        workspace_id = str(proposal.get("workspace_id") or self.context.workspace_id)
        if workspace_id != self.context.workspace_id:
            raise ControlPlaneError("V3_PROPOSAL_WORKSPACE_MISMATCH", "Proposal workspace 与 Store 不一致。", status_code=409)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = connection.execute(
                    "SELECT * FROM v3_proposals WHERE proposal_id = ? OR proposal_hash = ?",
                    (proposal_id, proposal_hash),
                ).fetchone()
                if duplicate is not None:
                    if str(duplicate["proposal_hash"]) != proposal_hash:
                        raise ControlPlaneError("V3_PROPOSAL_CONFLICT", "Proposal ID 已被其他内容使用。", status_code=409)
                    connection.commit()
                    return self._v3_proposal_row(duplicate)
                # Same operation_id with a different decision hash is a hard conflict.
                op_conflict = connection.execute(
                    "SELECT proposal_hash FROM v3_proposals WHERE artifact_kind = ? AND operation_id = ?",
                    (str(proposal["artifact_kind"]), str(proposal["operation_id"])),
                ).fetchone()
                if op_conflict is not None and str(op_conflict["proposal_hash"]) != proposal_hash:
                    raise ControlPlaneError(
                        "V3_OPERATION_CONFLICT",
                        "相同 operation_id 不能对应不同 proposal_hash。",
                        status_code=409,
                    )
                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    INSERT INTO v3_proposals(
                        proposal_id, workspace_id, artifact_kind, producer_role, operation_id, base_revision,
                        dependency_fingerprint, declared_dependencies_json, proposal_hash, canonical_payload_hash,
                        payload_json, cited_source_ids_json, prompt_version, model_fingerprint,
                        inference_receipt_refs_json, payload_schema_version, canonicalization_version,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                    """,
                    (
                        proposal_id, workspace_id, str(proposal["artifact_kind"]), str(proposal["producer_role"]),
                        str(proposal["operation_id"]), int(proposal["base_revision"]),
                        str(proposal["dependency_fingerprint"]), _json(proposal.get("declared_dependencies") or []),
                        proposal_hash, str(proposal.get("canonical_payload_hash") or ""),
                        _json(proposal["payload"]), _json(proposal.get("cited_source_ids") or []),
                        str(proposal["prompt_version"]), str(proposal["model_fingerprint"]),
                        _json(proposal.get("inference_receipt_refs") or []),
                        str(proposal.get("payload_schema_version") or "v3"),
                        str(proposal.get("canonicalization_version") or "v3-canon-2"), now,
                    ),
                )
                self._event(connection, revision, "V3ProposalAppended", "V3Proposal", proposal_id, {
                    "artifact_kind": str(proposal["artifact_kind"]),
                    "producer_role": str(proposal["producer_role"]),
                    "proposal_hash": proposal_hash,
                    "workspace_id": workspace_id,
                })
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.v3_proposal(proposal_id) or {}

    def v3_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM v3_proposals WHERE proposal_id = ?", (str(proposal_id),)).fetchone()
        return self._v3_proposal_row(row) if row else None

    def record_v3_validation_report(
        self,
        proposal_id: str,
        report: dict[str, Any],
        *,
        proposal_hash: str | None = None,
        report_hash: str | None = None,
        kernel_seal: Any = None,
    ) -> dict[str, Any]:
        from document_pipeline.kernel_seal import KERNEL_SEAL

        if kernel_seal is not KERNEL_SEAL:
            raise ControlPlaneError(
                "V3_VALIDATION_SEALED",
                "ValidationReport 只能由持有 KERNEL_SEAL 的可信 Validator 写入。",
                status_code=403,
            )
        proposal = self.v3_proposal(proposal_id)
        if proposal is None:
            raise ControlPlaneError("V3_PROPOSAL_NOT_FOUND", "Proposal 不存在。", status_code=404)
        if str(report.get("proposal_id") or "") != str(proposal_id):
            raise ControlPlaneError("V3_VALIDATION_INVALID", "ValidationReport 未绑定对应 Proposal。", status_code=400)
        bound_hash = str(proposal_hash or report.get("proposal_hash") or "")
        if not bound_hash or bound_hash != str(proposal["proposal_hash"]):
            raise ControlPlaneError(
                "V3_VALIDATION_HASH_MISMATCH",
                "ValidationReport 必须绑定 Store 中的 exact proposal_hash。",
                status_code=409,
            )
        if str(report.get("workspace_id") or self.context.workspace_id) != self.context.workspace_id:
            raise ControlPlaneError("V3_VALIDATION_WORKSPACE_MISMATCH", "ValidationReport 跨工作空间。", status_code=409)

        # Never trust caller-supplied schema_valid for sealed write: re-check payload.
        from document_pipeline.artifact_registry import ARTIFACT_REGISTRY
        from document_pipeline.canonicalization import canonical_payload_hash
        from document_pipeline.proposals import ValidationReport

        payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        try:
            ARTIFACT_REGISTRY.validate_payload(str(proposal["artifact_kind"]), payload)
            recomputed_schema_valid = True
        except Exception:
            recomputed_schema_valid = False
        if bool(report.get("schema_valid")) and not recomputed_schema_valid:
            raise ControlPlaneError(
                "V3_VALIDATION_FORGED",
                "ValidationReport 宣称 schema_valid，但 Store payload 未通过 Schema。",
                status_code=409,
            )
        expected_payload_hash = canonical_payload_hash(payload)
        if str(report.get("canonical_payload_hash") or "") != expected_payload_hash:
            raise ControlPlaneError(
                "V3_VALIDATION_HASH_MISMATCH",
                "ValidationReport canonical_payload_hash 与 Store payload 不一致。",
                status_code=409,
            )

        report_model = ValidationReport.model_validate(report)
        recomputed_report_hash = report_model.compute_report_hash()
        if report_hash and str(report_hash) != recomputed_report_hash:
            raise ControlPlaneError(
                "V3_VALIDATION_HASH_MISMATCH",
                "ValidationReport report_hash 与内容不一致。",
                status_code=409,
            )
        stored_report_hash = recomputed_report_hash
        valid = all(bool(report.get(field)) for field in (
            "schema_valid", "references_valid", "authority_policy_valid", "dependency_current",
        )) and recomputed_schema_valid
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT status, proposal_hash FROM v3_proposals WHERE proposal_id = ?",
                    (str(proposal_id),),
                ).fetchone()
                if current is None or str(current["status"]) == "promoted":
                    raise ControlPlaneError("V3_VALIDATION_FORBIDDEN", "已晋级 Proposal 不能重新校验。", status_code=409)
                if str(current["proposal_hash"]) != bound_hash:
                    raise ControlPlaneError("V3_VALIDATION_HASH_MISMATCH", "Proposal hash 在写入前已变化。", status_code=409)
                revision = self._bump_revision(connection)
                connection.execute(
                    "INSERT INTO v3_validation_reports(proposal_id, proposal_hash, report_hash, report_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(proposal_id) DO UPDATE SET "
                    "proposal_hash = excluded.proposal_hash, report_hash = excluded.report_hash, "
                    "report_json = excluded.report_json, created_at = excluded.created_at",
                    (str(proposal_id), bound_hash, stored_report_hash, _json(report), now),
                )
                connection.execute(
                    "UPDATE v3_proposals SET status = ? WHERE proposal_id = ?",
                    ("validated" if valid else "rejected", str(proposal_id)),
                )
                self._event(
                    connection,
                    revision,
                    "V3ProposalValidated",
                    "V3Proposal",
                    str(proposal_id),
                    {"valid": valid, "proposal_hash": bound_hash, "report_hash": stored_report_hash},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(report)

    def v3_validation_report(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT report_json FROM v3_validation_reports WHERE proposal_id = ?", (str(proposal_id),)
            ).fetchone()
        value = _decode(str(row["report_json"]), {}) if row is not None else None
        return value if isinstance(value, dict) else None

    def issue_v3_gate_receipt(self, receipt: dict[str, Any], *, kernel_seal: Any = None) -> dict[str, Any]:
        from document_pipeline.kernel_seal import KERNEL_SEAL

        if kernel_seal is not KERNEL_SEAL:
            raise ControlPlaneError(
                "V3_GATE_SEALED",
                "GateReceipt 只能由持有 KERNEL_SEAL 的 GateService 签发。",
                status_code=403,
            )
        required = ("receipt_id", "proposal_id", "proposal_hash", "gate_id", "verdict", "reviewer", "issuer")
        if any(not str(receipt.get(key) or "").strip() for key in required) or str(receipt.get("verdict")) not in {
            "pass", "warn", "block", "needs_human",
        }:
            raise ControlPlaneError("V3_GATE_INVALID", "GateReceipt 输入无效。", status_code=400)
        proposal_id = str(receipt["proposal_id"])
        workspace_id = str(receipt.get("workspace_id") or self.context.workspace_id)
        if workspace_id != self.context.workspace_id:
            raise ControlPlaneError("V3_GATE_WORKSPACE_MISMATCH", "GateReceipt 跨工作空间。", status_code=409)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                proposal = connection.execute(
                    "SELECT * FROM v3_proposals WHERE proposal_id = ?", (proposal_id,)
                ).fetchone()
                validation = connection.execute(
                    "SELECT * FROM v3_validation_reports WHERE proposal_id = ?", (proposal_id,)
                ).fetchone()
                if proposal is None or validation is None:
                    raise ControlPlaneError("V3_GATE_FORBIDDEN", "Gate 只能评审已验证的 Proposal。", status_code=409)
                if str(proposal["workspace_id"] or workspace_id) not in {"", workspace_id}:
                    raise ControlPlaneError("V3_GATE_WORKSPACE_MISMATCH", "Proposal 与 Gate workspace 不一致。", status_code=409)
                report = _decode(str(validation["report_json"]), {})
                if not isinstance(report, dict):
                    raise ControlPlaneError("V3_GATE_FORBIDDEN", "ValidationReport 损坏。", status_code=409)
                if str(receipt["proposal_hash"]) != str(proposal["proposal_hash"]):
                    raise ControlPlaneError("V3_GATE_STALE", "GateReceipt 未绑定 exact proposal_hash。", status_code=409)
                if str(validation["proposal_hash"] or proposal["proposal_hash"]) != str(proposal["proposal_hash"]):
                    raise ControlPlaneError("V3_GATE_STALE", "ValidationReport 未绑定 exact proposal_hash。", status_code=409)

                # Recompute ValidationReport hash; never trust a caller-supplied binding alone.
                from document_pipeline.artifact_registry import ARTIFACT_REGISTRY
                from document_pipeline.proposals import GateReceipt, PlanningGateReceipt, ValidationReport

                report_model = ValidationReport.model_validate(report)
                expected_report_hash = report_model.compute_report_hash()
                if str(validation["report_hash"] or "") != expected_report_hash:
                    raise ControlPlaneError(
                        "V3_GATE_STALE",
                        "Store 中 ValidationReport hash 与内容不一致。",
                        status_code=409,
                    )
                if str(receipt.get("validation_report_hash") or "") != expected_report_hash:
                    raise ControlPlaneError("V3_GATE_STALE", "GateReceipt 未绑定 ValidationReport hash。", status_code=409)

                # Re-check payload schema independently of stored booleans.
                proposal_payload = _decode(str(proposal["payload_json"]), {})
                try:
                    ARTIFACT_REGISTRY.validate_payload(str(proposal["artifact_kind"]), proposal_payload)
                    payload_schema_ok = True
                except Exception:
                    payload_schema_ok = False

                reviewed_revision = int(receipt.get("reviewed_revision", receipt.get("base_revision", -1)))
                if reviewed_revision != int(proposal["base_revision"]):
                    raise ControlPlaneError("V3_GATE_STALE", "GateReceipt 未绑定当前 base_revision。", status_code=409)
                report_passed = all(
                    bool(report.get(field))
                    for field in ("schema_valid", "references_valid", "authority_policy_valid", "dependency_current")
                )
                if str(receipt["verdict"]) == "pass" and (not report_passed or not payload_schema_ok):
                    raise ControlPlaneError(
                        "V3_GATE_FORBIDDEN",
                        "验证未通过或 payload Schema 非法，不能签发 pass GateReceipt。",
                        status_code=409,
                    )

                # Recompute receipt content hash; reject forged hash claims.
                receipt_for_hash = dict(receipt)
                receipt_for_hash["workspace_id"] = workspace_id
                receipt_for_hash["validation_report_hash"] = expected_report_hash
                receipt_for_hash.pop("receipt_hash", None)
                receipt_model = PlanningGateReceipt if receipt_for_hash.get("receipt_subtype") == "planning" else GateReceipt
                gate_model = receipt_model.model_validate(
                    {**receipt_for_hash, "receipt_hash": "", "reviewed_revision": reviewed_revision}
                )
                recomputed_receipt_hash = gate_model.compute_receipt_content_hash()
                claimed_hash = str(receipt.get("receipt_hash") or "")
                if claimed_hash and claimed_hash != recomputed_receipt_hash:
                    raise ControlPlaneError(
                        "V3_GATE_HASH_MISMATCH",
                        "GateReceipt receipt_hash 与内容不一致。",
                        status_code=409,
                    )

                revision = self._bump_revision(connection)
                now = _now()
                issued_at = str(receipt.get("issued_at") or now)
                sealed_receipt = {
                    **receipt,
                    "workspace_id": workspace_id,
                    "validation_report_hash": expected_report_hash,
                    "receipt_hash": recomputed_receipt_hash,
                    "issued_at": issued_at,
                    "reviewed_revision": reviewed_revision,
                }
                connection.execute(
                    """
                    INSERT INTO v3_gate_receipts(
                        receipt_id, receipt_hash, workspace_id, proposal_id, proposal_hash,
                        validation_report_id, validation_report_hash, artifact_kind, gate_id,
                        gate_policy_version, verdict, findings_json, issuer, reviewer,
                        reviewed_revision, dependency_fingerprint, dependency_snapshot_json,
                        issued_at, expires_at, receipt_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(receipt["receipt_id"]),
                        recomputed_receipt_hash,
                        workspace_id,
                        proposal_id,
                        str(receipt["proposal_hash"]),
                        str(receipt.get("validation_report_id") or report.get("report_id") or ""),
                        expected_report_hash,
                        str(receipt.get("artifact_kind") or proposal["artifact_kind"]),
                        str(receipt["gate_id"]),
                        str(receipt.get("gate_policy_version") or ""),
                        str(receipt["verdict"]),
                        _json(receipt.get("findings") or []),
                        str(receipt["issuer"]),
                        str(receipt["reviewer"]),
                        reviewed_revision,
                        str(receipt.get("dependency_fingerprint") or ""),
                        _json(receipt.get("resolved_dependency_snapshot") or {}),
                        issued_at,
                        receipt.get("expires_at"),
                        _json(sealed_receipt),
                        now,
                    ),
                )
                self._event(
                    connection,
                    revision,
                    "V3GateReceiptIssued",
                    "V3Proposal",
                    proposal_id,
                    {
                        "gate_id": str(receipt["gate_id"]),
                        "verdict": str(receipt["verdict"]),
                        "issuer": str(receipt["issuer"]),
                        "proposal_hash": str(receipt["proposal_hash"]),
                        "receipt_hash": recomputed_receipt_hash,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {**sealed_receipt, "created_at": now}

    def has_v3_gate_receipt(self, proposal_id: str, gate_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM v3_gate_receipts WHERE proposal_id = ? AND gate_id = ? AND verdict = 'pass' LIMIT 1",
                (str(proposal_id), str(gate_id)),
            ).fetchone()
        return row is not None

    def latest_v3_gate_receipt(self, proposal_id: str, gate_id: str) -> dict[str, Any] | None:
        """Return the latest immutable receipt for one exact Proposal/Gate pair."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT receipt_json FROM v3_gate_receipts WHERE proposal_id = ? AND gate_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (str(proposal_id), str(gate_id)),
            ).fetchone()
        value = _decode(str(row["receipt_json"]), {}) if row is not None else None
        return value if isinstance(value, dict) else None

    def promote_v3_proposal(
        self,
        *,
        proposal_id: str,
        gate_receipt_ids: list[str],
        workspace_id: str | None = None,
        gate_policy_registry: Any = None,
        artifact_registry: Any = None,
    ) -> dict[str, Any]:
        ids = sorted({str(value).strip() for value in gate_receipt_ids if str(value).strip()})
        if not ids:
            raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", "缺少通过的 GateReceipt，不能晋级。", status_code=409)
        expected_workspace = str(workspace_id or self.context.workspace_id)
        if expected_workspace != self.context.workspace_id:
            raise ControlPlaneError("V3_PROMOTION_WORKSPACE_MISMATCH", "Promotion 跨工作空间。", status_code=409)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                proposal = connection.execute(
                    "SELECT * FROM v3_proposals WHERE proposal_id = ?", (str(proposal_id),)
                ).fetchone()
                if proposal is None:
                    raise ControlPlaneError("V3_PROPOSAL_NOT_FOUND", "Proposal 不存在。", status_code=404)
                if str(proposal["workspace_id"] or expected_workspace) not in {"", expected_workspace}:
                    raise ControlPlaneError("V3_PROMOTION_WORKSPACE_MISMATCH", "Proposal 不属于当前工作空间。", status_code=409)

                # Recompute proposal hash from stored decision fields (never trust caller body).
                from document_pipeline.canonicalization import compute_proposal_hash
                from document_pipeline.proposals import ProposalEnvelope

                proposal_row = self._v3_proposal_row(proposal)
                proposal_row["workspace_id"] = expected_workspace
                envelope = ProposalEnvelope.from_storage(proposal_row)
                recomputed_hash = compute_proposal_hash(envelope.decision_record())
                if recomputed_hash != str(proposal["proposal_hash"]):
                    raise ControlPlaneError(
                        "V3_PROMOTION_HASH_MISMATCH",
                        "Store Proposal hash 与决策内容不一致，拒绝晋级。",
                        status_code=409,
                    )

                existing = connection.execute(
                    "SELECT * FROM v3_promotion_receipts WHERE proposal_id = ?", (str(proposal_id),)
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return self._v3_promotion_row(existing)

                # Operation idempotency is bound to proposal_hash.
                duplicate = connection.execute(
                    "SELECT * FROM v3_promotion_receipts WHERE artifact_kind = ? AND operation_id = ?",
                    (str(proposal["artifact_kind"]), str(proposal["operation_id"])),
                ).fetchone()
                if duplicate is not None:
                    if str(duplicate["proposal_hash"] or "") not in {"", recomputed_hash}:
                        raise ControlPlaneError(
                            "V3_OPERATION_CONFLICT",
                            "相同 operation_id 已晋级不同 proposal_hash。",
                            status_code=409,
                        )
                    if str(duplicate["proposal_id"]) == str(proposal_id):
                        connection.commit()
                        return self._v3_promotion_row(duplicate)
                    # Same hash different id is treated as content-addressed replay only when hashes match.
                    if str(duplicate["proposal_hash"] or "") == recomputed_hash:
                        connection.commit()
                        return self._v3_promotion_row(duplicate)
                    raise ControlPlaneError(
                        "V3_OPERATION_CONFLICT",
                        "相同 operation 不能静默返回另一 Proposal 的 Receipt。",
                        status_code=409,
                    )

                validation = connection.execute(
                    "SELECT * FROM v3_validation_reports WHERE proposal_id = ?", (str(proposal_id),)
                ).fetchone()
                if validation is None:
                    raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", "缺少 ValidationReport。", status_code=409)
                report = _decode(str(validation["report_json"]), {})
                if not isinstance(report, dict) or not all(
                    bool(report.get(field))
                    for field in ("schema_valid", "references_valid", "authority_policy_valid", "dependency_current")
                ):
                    raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", "ValidationReport 未通过。", status_code=409)
                if str(validation["proposal_hash"] or recomputed_hash) != recomputed_hash:
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        "ValidationReport 未绑定 exact proposal_hash。",
                        status_code=409,
                    )
                if str(report.get("proposal_hash") or recomputed_hash) != recomputed_hash:
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        "ValidationReport payload 未绑定 exact proposal_hash。",
                        status_code=409,
                    )

                # Independent re-validation of Store payload Schema (never trust report flags alone).
                from document_pipeline.artifact_registry import ARTIFACT_REGISTRY as _DEFAULT_ARTIFACT_REGISTRY
                from document_pipeline.proposals import GateReceipt as _GateReceipt
                from document_pipeline.proposals import ValidationReport as _ValidationReport

                registry = artifact_registry or _DEFAULT_ARTIFACT_REGISTRY
                try:
                    registry.validate_payload(str(proposal["artifact_kind"]), envelope.payload)
                except Exception as exc:
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        f"Store payload Schema 非法，拒绝晋级: {exc}",
                        status_code=409,
                    ) from exc

                report_model = _ValidationReport.model_validate(report)
                recomputed_report_hash = report_model.compute_report_hash()
                if str(validation["report_hash"] or "") != recomputed_report_hash:
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        "ValidationReport hash 与内容不一致。",
                        status_code=409,
                    )
                if str(report.get("canonical_payload_hash") or "") != envelope.canonical_payload_hash():
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        "ValidationReport canonical_payload_hash 与 Store payload 不一致。",
                        status_code=409,
                    )

                placeholders = ",".join("?" for _ in ids)
                gates = connection.execute(
                    f"SELECT * FROM v3_gate_receipts WHERE receipt_id IN ({placeholders}) AND proposal_id = ?",
                    [*ids, str(proposal_id)],
                ).fetchall()
                if len(gates) != len(ids):
                    raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", "GateReceipt 不完整或不属于该 Proposal。", status_code=409)

                # Recompute every GateReceipt content hash inside the promotion transaction.
                for gate in gates:
                    body = _decode(str(gate["receipt_json"]), {})
                    if not isinstance(body, dict):
                        raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", "GateReceipt JSON 损坏。", status_code=409)
                    body_for_hash = {**body, "receipt_hash": ""}
                    try:
                        gate_model = _GateReceipt.model_validate(body_for_hash)
                    except Exception as exc:
                        raise ControlPlaneError(
                            "V3_PROMOTION_FORBIDDEN",
                            f"GateReceipt 内容无法解析: {exc}",
                            status_code=409,
                        ) from exc
                    expected_gate_hash = gate_model.compute_receipt_content_hash()
                    if str(gate["receipt_hash"] or "") != expected_gate_hash:
                        raise ControlPlaneError(
                            "V3_PROMOTION_FORBIDDEN",
                            f"GateReceipt {gate['gate_id']} receipt_hash 与内容不一致。",
                            status_code=409,
                        )
                    if str(gate["validation_report_hash"] or "") != recomputed_report_hash:
                        raise ControlPlaneError(
                            "V3_PROMOTION_FORBIDDEN",
                            f"GateReceipt {gate['gate_id']} 未绑定 exact ValidationReport hash。",
                            status_code=409,
                        )

                # Re-resolve active dependencies inside the promotion transaction.
                resolved_snapshot: dict[str, Any] = {}
                if artifact_registry is not None:
                    try:
                        registration = artifact_registry.require_promotable(str(proposal["artifact_kind"]))
                    except Exception as exc:
                        raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", str(exc), status_code=409) from exc
                    for kind in registration.dependency_kinds:
                        dep = connection.execute(
                            "SELECT revision.* FROM v3_active_artifacts active "
                            "JOIN v3_artifact_revisions revision "
                            "ON revision.artifact_kind = active.artifact_kind AND revision.revision = active.revision "
                            "WHERE active.artifact_kind = ?",
                            (kind,),
                        ).fetchone()
                        if dep is None:
                            raise ControlPlaneError(
                                "V3_PROMOTION_STALE",
                                f"晋级时依赖 {kind} 已缺失。",
                                status_code=409,
                            )
                        resolved_snapshot[kind] = {
                            "artifact_kind": kind,
                            "artifact_id": str(dep["artifact_id"]),
                            "revision": int(dep["revision"]),
                            "artifact_hash": str(dep["artifact_hash"]),
                        }
                    declared_dependencies = {
                        item.artifact_kind: item
                        for item in envelope.declared_dependencies
                    }
                    for kind in registration.optional_dependency_kinds:
                        declared_ref = declared_dependencies.get(kind)
                        if declared_ref is None:
                            continue
                        dep = connection.execute(
                            "SELECT revision.* FROM v3_active_artifacts active "
                            "JOIN v3_artifact_revisions revision "
                            "ON revision.artifact_kind = active.artifact_kind AND revision.revision = active.revision "
                            "WHERE active.artifact_kind = ?",
                            (kind,),
                        ).fetchone()
                        if dep is None:
                            raise ControlPlaneError(
                                "V3_PROMOTION_STALE",
                                f"晋级时可选依赖 {kind} 已缺失。",
                                status_code=409,
                            )
                        entry = {
                            "artifact_kind": kind,
                            "artifact_id": str(dep["artifact_id"]),
                            "revision": int(dep["revision"]),
                            "artifact_hash": str(dep["artifact_hash"]),
                        }
                        if (
                            declared_ref.expected_revision is not None
                            and int(declared_ref.expected_revision)
                            != entry["revision"]
                        ):
                            raise ControlPlaneError(
                                "V3_PROMOTION_STALE",
                                f"晋级时可选依赖 {kind} revision 已变化。",
                                status_code=409,
                            )
                        if (
                            declared_ref.expected_hash
                            and declared_ref.expected_hash
                            != entry["artifact_hash"]
                        ):
                            raise ControlPlaneError(
                                "V3_PROMOTION_STALE",
                                f"晋级时可选依赖 {kind} hash 已变化。",
                                status_code=409,
                            )
                        resolved_snapshot[kind] = entry

                from document_pipeline.proposals import trusted_dependency_fingerprint

                policy_version = ""
                required_gate_ids: list[str] = []
                if gate_policy_registry is not None:
                    try:
                        policy = gate_policy_registry.require_policy(str(proposal["artifact_kind"]))
                    except Exception as exc:
                        raise ControlPlaneError("V3_PROMOTION_FORBIDDEN", str(exc), status_code=409) from exc
                    policy_version = policy.policy_version
                    required_gate_ids = list(policy.gate_ids())
                    trusted_fp = trusted_dependency_fingerprint(
                        resolved_dependency_snapshot=resolved_snapshot,
                        schema_version=policy.schema_version,
                        policy_version=policy.policy_version,
                        prompt_version=str(proposal["prompt_version"]),
                        model_fingerprint=str(proposal["model_fingerprint"]),
                        artifact_kind=str(proposal["artifact_kind"]),
                    )
                    if trusted_fp != str(proposal["dependency_fingerprint"]):
                        raise ControlPlaneError(
                            "V3_PROMOTION_STALE",
                            "晋级时 dependency_fingerprint 与可信重算不一致。",
                            status_code=409,
                        )
                    # Full required Gate set.
                    present = {str(gate["gate_id"]): gate for gate in gates}
                    for required_id in required_gate_ids:
                        gate = present.get(required_id)
                        if gate is None:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"缺少必需 Gate {required_id}。",
                                status_code=409,
                            )
                        requirement = policy.requirement_for(required_id)
                        assert requirement is not None
                        if str(gate["verdict"]) not in requirement.promotion_verdicts:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"Gate {required_id} verdict 不允许晋级。",
                                status_code=409,
                            )
                        issuer = str(gate["issuer"] or "")
                        if issuer not in requirement.allowed_issuers:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"Gate {required_id} issuer 非法: {issuer}",
                                status_code=409,
                            )
                        if str(gate["proposal_hash"]) != recomputed_hash:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"Gate {required_id} 未绑定 exact proposal_hash。",
                                status_code=409,
                            )
                        if str(gate["workspace_id"] or expected_workspace) not in {"", expected_workspace}:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"Gate {required_id} 跨工作空间。",
                                status_code=409,
                            )
                        if gate["expires_at"]:
                            expires = _parse_utc_timestamp(gate["expires_at"], label="GateReceipt.expires_at")
                            if expires <= datetime.now(timezone.utc):
                                raise ControlPlaneError(
                                    "V3_PROMOTION_FORBIDDEN",
                                    f"Gate {required_id} 已过期。",
                                    status_code=409,
                                )
                        if str(gate["gate_policy_version"] or policy_version) not in {"", policy_version}:
                            raise ControlPlaneError(
                                "V3_PROMOTION_FORBIDDEN",
                                f"Gate {required_id} policy version 不匹配。",
                                status_code=409,
                            )
                else:
                    # Fail closed without policy registry — never accept bare pass receipts.
                    raise ControlPlaneError(
                        "V3_PROMOTION_FORBIDDEN",
                        "缺少 GatePolicyRegistry，不能晋级。",
                        status_code=409,
                    )

                active = connection.execute(
                    "SELECT revision FROM v3_active_artifacts WHERE artifact_kind = ?",
                    (str(proposal["artifact_kind"]),),
                ).fetchone()
                current_revision = int(active["revision"]) if active is not None else 0
                if current_revision != int(proposal["base_revision"]):
                    connection.execute(
                        "UPDATE v3_proposals SET status = 'stale' WHERE proposal_id = ?",
                        (str(proposal_id),),
                    )
                    self._event(
                        connection,
                        self._bump_revision(connection),
                        "V3PromotionRejectedStale",
                        "V3Proposal",
                        str(proposal_id),
                        {"base_revision": int(proposal["base_revision"]), "active_revision": current_revision},
                    )
                    connection.commit()
                    raise ControlPlaneError("V3_PROMOTION_STALE", "Proposal 的 base_revision 已过期，不能晋级。", status_code=409)

                promoted_revision = current_revision + 1
                artifact_id = f"{proposal['artifact_kind']}@{promoted_revision}"
                from document_pipeline.canonicalization import canonical_payload_hash as _payload_hash

                artifact_hash = _payload_hash(_decode(str(proposal["payload_json"]), {}))
                receipt_id = str(uuid.uuid4())
                now = _now()
                gate_bindings = [
                    {
                        "receipt_id": str(gate["receipt_id"]),
                        "receipt_hash": str(gate["receipt_hash"] or ""),
                        "gate_id": str(gate["gate_id"]),
                    }
                    for gate in gates
                ]
                promotion_body = {
                    "workspace_id": expected_workspace,
                    "proposal_id": str(proposal_id),
                    "proposal_hash": recomputed_hash,
                    "artifact_kind": str(proposal["artifact_kind"]),
                    "operation_id": str(proposal["operation_id"]),
                    "artifact_id": artifact_id,
                    "base_revision": int(proposal["base_revision"]),
                    "promoted_revision": promoted_revision,
                    "artifact_hash": artifact_hash,
                    "dependency_fingerprint": str(proposal["dependency_fingerprint"]),
                    "resolved_dependency_snapshot": resolved_snapshot,
                    "gate_receipts": gate_bindings,
                    "gate_receipt_ids": [item["receipt_id"] for item in gate_bindings],
                    "policy_version": policy_version,
                }
                from document_pipeline.proposals import PromotionReceipt as _PromotionReceipt

                # receipt_id is assigned below; content hash excludes it and the hash field itself.
                promotion_for_hash = {
                    **promotion_body,
                    "receipt_id": "pending",
                    "receipt_hash": "",
                    "created_at": now,
                }
                receipt_hash = _PromotionReceipt.model_validate(promotion_for_hash).compute_receipt_content_hash()
                revision = self._bump_revision(connection)
                connection.execute(
                    """
                    INSERT INTO v3_artifact_revisions(
                        artifact_kind, revision, artifact_id, artifact_hash, payload_json,
                        producer_role, dependency_fingerprint, proposal_id, proposal_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(proposal["artifact_kind"]), promoted_revision, artifact_id, artifact_hash,
                        str(proposal["payload_json"]), str(proposal["producer_role"]),
                        str(proposal["dependency_fingerprint"]), str(proposal_id), recomputed_hash, now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO v3_active_artifacts(artifact_kind, artifact_id, revision, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(artifact_kind) DO UPDATE SET
                        artifact_id = excluded.artifact_id,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at
                    """,
                    (str(proposal["artifact_kind"]), artifact_id, promoted_revision, now),
                )
                connection.execute(
                    """
                    INSERT INTO v3_promotion_receipts(
                        receipt_id, receipt_hash, workspace_id, proposal_id, proposal_hash,
                        artifact_kind, operation_id, artifact_id, base_revision, promoted_revision,
                        artifact_hash, dependency_fingerprint, dependency_snapshot_json,
                        gate_receipt_ids_json, gate_receipts_json, policy_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id, receipt_hash, expected_workspace, str(proposal_id), recomputed_hash,
                        str(proposal["artifact_kind"]), str(proposal["operation_id"]), artifact_id,
                        int(proposal["base_revision"]), promoted_revision, artifact_hash,
                        str(proposal["dependency_fingerprint"]), _json(resolved_snapshot),
                        _json([item["receipt_id"] for item in gate_bindings]), _json(gate_bindings),
                        policy_version, now,
                    ),
                )
                connection.execute(
                    "UPDATE v3_proposals SET status = 'promoted' WHERE proposal_id = ?",
                    (str(proposal_id),),
                )
                self._event(
                    connection,
                    revision,
                    "V3ArtifactPromoted",
                    "V3Artifact",
                    artifact_id,
                    {
                        "artifact_kind": str(proposal["artifact_kind"]),
                        "revision": promoted_revision,
                        "proposal_id": str(proposal_id),
                        "proposal_hash": recomputed_hash,
                        "receipt_hash": receipt_hash,
                        "dependency_snapshot": resolved_snapshot,
                    },
                )
                connection.commit()
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
        return self.v3_promotion_receipt(receipt_id) or {}

    def v3_promotion_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM v3_promotion_receipts WHERE receipt_id = ?", (str(receipt_id),)
            ).fetchone()
        return self._v3_promotion_row(row) if row else None

    def v3_active_artifact(self, artifact_kind: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT revision.* FROM v3_active_artifacts active "
                "JOIN v3_artifact_revisions revision "
                "ON revision.artifact_kind = active.artifact_kind AND revision.revision = active.revision "
                "WHERE active.artifact_kind = ?",
                (str(artifact_kind),),
            ).fetchone()
        return self._v3_artifact_row(row) if row else None

    def v3_promoted_artifacts(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT revision.* FROM v3_active_artifacts active "
                "JOIN v3_artifact_revisions revision "
                "ON revision.artifact_kind = active.artifact_kind AND revision.revision = active.revision "
                "ORDER BY revision.artifact_kind"
            ).fetchall()
        return [self._v3_artifact_row(row) for row in rows]

    @staticmethod
    def _v3_proposal_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _decode(value.pop("payload_json"), {})
        value["cited_source_ids"] = _decode(value.pop("cited_source_ids_json"), [])
        value["declared_dependencies"] = _decode(value.pop("declared_dependencies_json", None), [])
        value["inference_receipt_refs"] = _decode(
            value.pop("inference_receipt_refs_json", None),
            [],
        )
        return value

    @staticmethod
    def _v3_inference_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        receipt = _decode(value.pop("receipt_json"), {})
        receipt["receipt_hash"] = value["receipt_hash"]
        receipt["created_at"] = value["created_at"]
        return receipt

    @staticmethod
    def _v3_artifact_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _decode(value.pop("payload_json"), {})
        return value

    @staticmethod
    def _v3_promotion_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["gate_receipt_ids"] = _decode(value.pop("gate_receipt_ids_json"), [])
        value["gate_receipts"] = _decode(value.pop("gate_receipts_json", None), [])
        value["resolved_dependency_snapshot"] = _decode(value.pop("dependency_snapshot_json", None), {})
        return value


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

    def upsert_goal_state(self, goal: dict[str, Any], *, source: str = "v3_control") -> dict[str, Any]:
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

    def upsert_repair_job_state(self, job: dict[str, Any], *, source: str = "v3_control") -> dict[str, Any]:
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
        source: str = "v3_control",
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

    def record_stage_run(
        self,
        operation_id: str,
        stage_command: str,
        status: str,
        *,
        disposition: str = "",
        error: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = str(operation_id or "").strip()
        command = str(stage_command or "").strip()
        state = str(status or "").strip().lower()
        if not operation or not command or state not in {"queued", "running", "succeeded", "failed", "reused", "cancelled", "paused", "blocked_human"}:
            raise ControlPlaneError("STATE_UNAVAILABLE", "StageRun 状态无效。", status_code=503)
        now = _now()
        terminal = state in {"succeeded", "failed", "reused", "cancelled", "paused", "blocked_human"}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                latest = connection.execute(
                    "SELECT * FROM stage_runs WHERE operation_id = ? AND stage_command = ? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (operation, command),
                ).fetchone()
                latest_state = str(latest["status"]) if latest else ""
                latest_terminal = latest_state in {"succeeded", "failed", "reused", "cancelled", "paused", "blocked_human"}
                if latest is not None and terminal and latest_terminal:
                    if latest_state == state:
                        connection.commit()
                        return {
                            "stage_run_id": str(latest["stage_run_id"]),
                            "operation_id": operation,
                            "stage_command": command,
                            "attempt": int(latest["attempt"]),
                            "status": latest_state,
                            "disposition": str(latest["disposition"] or ""),
                        }
                    raise ControlPlaneError(
                        "STATE_CONFLICT",
                        "StageRun 已处于终态，拒绝覆盖审计记录。",
                        status_code=409,
                        details={
                            "operation_id": operation,
                            "stage_command": command,
                            "current_status": latest_state,
                            "requested_status": state,
                        },
                    )
                if (
                    latest is None
                    or state == "queued"
                    or (
                        state == "running"
                        and str(latest["status"]) not in {"queued", "running"}
                    )
                ):
                    attempt = int(latest["attempt"] if latest else 0) + 1
                    run_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO stage_runs(stage_run_id, operation_id, stage_command, attempt, status, disposition, error_json, output_json, started_at, completed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            run_id,
                            operation,
                            command,
                            attempt,
                            state,
                            disposition,
                            _json(error) if error else None,
                            _json(output) if output is not None else None,
                            now,
                            now if terminal else None,
                        ),
                    )
                else:
                    run_id = str(latest["stage_run_id"])
                    attempt = int(latest["attempt"])
                    output_json = (
                        _json(output)
                        if output is not None
                        else latest["output_json"]
                    )
                    connection.execute(
                        "UPDATE stage_runs SET status = ?, disposition = ?, error_json = ?, output_json = ?, completed_at = ? WHERE stage_run_id = ?",
                        (
                            state,
                            disposition,
                            _json(error) if error else None,
                            output_json,
                            now if terminal else None,
                            run_id,
                        ),
                    )
                revision = self._bump_revision(connection)
                self._event(
                    connection, revision, "StageRunRecorded", "StageRun", run_id,
                    {"operation_id": operation, "command": command, "attempt": attempt, "status": state, "disposition": disposition},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"stage_run_id": run_id, "operation_id": operation, "stage_command": command, "attempt": attempt, "status": state, "disposition": disposition}

    def stage_runs(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM stage_runs WHERE operation_id = ? ORDER BY stage_command, attempt",
                (str(operation_id or ""),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["error"] = _decode(item.pop("error_json", None), None)
            item["output"] = _decode(item.pop("output_json", None), None)
            result.append(item)
        return result

    def start_llm_request(
        self,
        operation_id: str,
        stage_id: str,
        *,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        operation = str(operation_id or "").strip()
        stage = str(stage_id or "").strip()
        if not operation or not stage:
            raise ControlPlaneError(
                "STATE_UNAVAILABLE",
                "大模型请求必须绑定 Operation 和阶段。",
                status_code=503,
            )
        request_id = str(uuid.uuid4())
        started_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(request_index), 0) AS latest "
                    "FROM llm_requests WHERE operation_id = ? AND stage_id = ?",
                    (operation, stage),
                ).fetchone()
                request_index = int(row["latest"] or 0) + 1
                connection.execute(
                    "INSERT INTO llm_requests(request_id, operation_id, stage_id, "
                    "request_index, status, parameters_json, error, started_at, completed_at) "
                    "VALUES (?, ?, ?, ?, 'running', ?, '', ?, NULL)",
                    (
                        request_id,
                        operation,
                        stage,
                        request_index,
                        _json(parameters),
                        started_at,
                    ),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "LLMRequestStarted",
                    "LLMRequest",
                    request_id,
                    {
                        "operation_id": operation,
                        "stage_id": stage,
                        "request_index": request_index,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "request_id": request_id,
            "operation_id": operation,
            "stage_id": stage,
            "request_index": request_index,
            "status": "running",
        }

    def finish_llm_request(
        self,
        request_id: str,
        *,
        status: str,
        error: str = "",
    ) -> None:
        state = str(status or "").strip().lower()
        if state not in {"succeeded", "failed"}:
            raise ControlPlaneError(
                "STATE_UNAVAILABLE",
                "大模型请求终态无效。",
                status_code=503,
            )
        completed_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updated = connection.execute(
                    "UPDATE llm_requests SET status = ?, error = ?, completed_at = ? "
                    "WHERE request_id = ? AND status = 'running'",
                    (state, str(error or "")[:4000], completed_at, str(request_id)),
                )
                if updated.rowcount:
                    revision = self._bump_revision(connection)
                    self._event(
                        connection,
                        revision,
                        "LLMRequestFinished",
                        "LLMRequest",
                        str(request_id),
                        {"status": state},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def llm_requests(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM llm_requests WHERE operation_id = ? "
                "ORDER BY stage_id, request_index",
                (str(operation_id or ""),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _decode(item.pop("parameters_json", None), {})
            result.append(item)
        return result

    def cancel_active_stage_runs(
        self,
        operation_id: str,
        *,
        disposition: str,
        error: dict[str, Any] | None = None,
    ) -> int:
        """Close every queued/running attempt left behind by a dead Worker."""
        operation = str(operation_id or "").strip()
        if not operation:
            return 0
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT stage_run_id, stage_command, attempt FROM stage_runs "
                    "WHERE operation_id = ? AND status IN ('queued', 'running')",
                    (operation,),
                ).fetchall()
                if not rows:
                    connection.commit()
                    return 0
                revision = self._bump_revision(connection)
                for row in rows:
                    run_id = str(row["stage_run_id"])
                    connection.execute(
                        "UPDATE stage_runs SET status = 'cancelled', disposition = ?, "
                        "error_json = ?, completed_at = ? WHERE stage_run_id = ?",
                        (disposition, _json(error) if error else None, now, run_id),
                    )
                    self._event(
                        connection,
                        revision,
                        "StageRunRecorded",
                        "StageRun",
                        run_id,
                        {
                            "operation_id": operation,
                            "command": str(row["stage_command"]),
                            "attempt": int(row["attempt"]),
                            "status": "cancelled",
                            "disposition": disposition,
                        },
                    )
                connection.commit()
                return len(rows)
            except Exception:
                connection.rollback()
                raise

    def reconcile_expired_operations(self) -> list[dict[str, Any]]:
        """Fail operations orphaned by a backend restart.

        The workspace lease is the durable liveness signal for the process that
        owns an operation.  Once it is missing or expired, an operation in a
        transient state cannot make progress in this process and must not keep
        being presented as running to the UI.  Reconcile the operation, its
        in-flight stages, and any open LLM receipts in one transaction so a
        restart cannot leave a partially active control record behind.
        """
        now = _now()
        now_dt = datetime.now(timezone.utc)
        interrupted: list[dict[str, Any]] = []
        transient_states = ("queued", "running", "pausing", "cancelling")
        placeholders = ",".join("?" for _ in transient_states)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT o.operation_id, o.status,
                           l.operation_id AS lease_operation_id, l.expires_at
                    FROM operations AS o
                    LEFT JOIN workspace_lease AS l ON l.operation_id = o.operation_id
                    WHERE o.status IN ({placeholders})
                    ORDER BY o.created_at
                    """,
                    transient_states,
                ).fetchall()
                for row in rows:
                    expires_at = str(row["expires_at"] or "")
                    lease_missing = not str(row["lease_operation_id"] or "")
                    lease_expired = False
                    if expires_at:
                        try:
                            lease_expired = _parse_utc_timestamp(
                                expires_at,
                                label="workspace lease 到期时间",
                            ) <= now_dt
                        except ControlPlaneError:
                            # A malformed lease cannot be used as a liveness
                            # signal, so treat it as orphaned and fail closed.
                            lease_expired = True
                    if not lease_missing and not lease_expired:
                        continue

                    operation_id = str(row["operation_id"])
                    reason = {
                        "code": "BACKEND_RESTART_INTERRUPTED",
                        "message": "后端重启导致任务中断，未完成阶段已关闭；请重新执行。",
                        "details": {
                            "operation_id": operation_id,
                            "previous_status": str(row["status"]),
                            "lease_missing": lease_missing,
                            "lease_expired_at": expires_at,
                        },
                    }
                    revision = self._bump_revision(connection)
                    stage_rows = connection.execute(
                        "SELECT stage_run_id, stage_command, attempt FROM stage_runs "
                        "WHERE operation_id = ? AND status IN ('queued', 'running')",
                        (operation_id,),
                    ).fetchall()
                    for stage in stage_rows:
                        stage_id = str(stage["stage_run_id"])
                        connection.execute(
                            "UPDATE stage_runs SET status = 'failed', disposition = ?, "
                            "error_json = ?, completed_at = ? WHERE stage_run_id = ?",
                            ("backend_restart", _json(reason), now, stage_id),
                        )
                        self._event(
                            connection,
                            revision,
                            "StageRunRecorded",
                            "StageRun",
                            stage_id,
                            {
                                "operation_id": operation_id,
                                "command": str(stage["stage_command"]),
                                "attempt": int(stage["attempt"]),
                                "status": "failed",
                                "disposition": "backend_restart",
                            },
                        )

                    request_rows = connection.execute(
                        "SELECT request_id FROM llm_requests WHERE operation_id = ? AND status = 'running'",
                        (operation_id,),
                    ).fetchall()
                    for request in request_rows:
                        request_id = str(request["request_id"])
                        connection.execute(
                            "UPDATE llm_requests SET status = 'failed', error = ?, completed_at = ? "
                            "WHERE request_id = ? AND status = 'running'",
                            (reason["message"], now, request_id),
                        )
                        self._event(
                            connection,
                            revision,
                            "LLMRequestFinished",
                            "LLMRequest",
                            request_id,
                            {"status": "failed", "error": reason["message"]},
                        )

                    connection.execute(
                        "UPDATE operations SET status = 'failed', message = ?, error_json = ?, "
                        "updated_at = ?, completed_at = ? WHERE operation_id = ?",
                        (reason["message"], _json(reason), now, now, operation_id),
                    )
                    connection.execute(
                        "DELETE FROM workspace_lease WHERE operation_id = ?",
                        (operation_id,),
                    )
                    self._event(
                        connection,
                        revision,
                        "OperationStatusChanged",
                        "Operation",
                        operation_id,
                        {"status": "failed", "message": reason["message"], "error": reason},
                    )
                    interrupted.append(
                        {
                            "operation_id": operation_id,
                            "previous_status": str(row["status"]),
                            "stage_runs": len(stage_rows),
                            "llm_requests": len(request_rows),
                        }
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return interrupted

    def latest_stage_run(self, operation_id: str, stage_command: str) -> dict[str, Any] | None:
        """Return the latest attempt for one stage without inferring its state."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM stage_runs WHERE operation_id = ? AND stage_command = ? "
                "ORDER BY attempt DESC LIMIT 1",
                (str(operation_id or ""), str(stage_command or "")),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["error"] = _decode(item.pop("error_json", None), None)
        item["output"] = _decode(item.pop("output_json", None), None)
        return item

    def latest_stage_run_for_command(self, stage_command: str) -> dict[str, Any] | None:
        """Return the most recent persisted attempt for a stage across Operations."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM stage_runs WHERE stage_command = ? ORDER BY rowid DESC LIMIT 1",
                (str(stage_command or ""),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["error"] = _decode(item.pop("error_json", None), None)
        item["output"] = _decode(item.pop("output_json", None), None)
        return item

    def latest_terminal_stage_run_for_command(self, stage_command: str) -> dict[str, Any] | None:
        """Return the latest completed attempt, ignoring an in-flight retry."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM stage_runs WHERE stage_command = ? "
                "AND status IN ('succeeded', 'failed', 'reused', 'cancelled', 'paused') "
                "ORDER BY rowid DESC LIMIT 1",
                (str(stage_command or ""),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["error"] = _decode(item.pop("error_json", None), None)
        item["output"] = _decode(item.pop("output_json", None), None)
        return item

    def document_undo(self) -> dict[str, Any] | None:
        """Return the durable one-step document undo pointer for this workspace."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'document_undo_state'"
            ).fetchone()
        value = _decode(str(row[0]), None) if row else None
        return dict(value) if isinstance(value, dict) else None

    def set_document_undo(self, backup_path: str, *, operation_id: str = "") -> dict[str, Any]:
        relative_path = str(backup_path or "").strip()
        if not relative_path:
            raise ControlPlaneError("STATE_UNAVAILABLE", "文档撤销备份路径不能为空。", status_code=503)
        state = {
            "backup_path": relative_path,
            "operation_id": str(operation_id or "").strip(),
            "updated_at": _now(),
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO control_meta(key, value) VALUES ('document_undo_state', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_json(state),),
                )
                revision = self._bump_revision(connection)
                self._event(
                    connection,
                    revision,
                    "DocumentUndoAvailable",
                    "Document",
                    "final.md",
                    {"backup_path": relative_path, "operation_id": state["operation_id"]},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return state

    def clear_document_undo(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                deleted = connection.execute(
                    "DELETE FROM control_meta WHERE key = 'document_undo_state'"
                ).rowcount
                if deleted:
                    revision = self._bump_revision(connection)
                    self._event(
                        connection,
                        revision,
                        "DocumentUndoCleared",
                        "Document",
                        "final.md",
                        {},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
                # A fresh planning/generation request explicitly supersedes a
                # blocked run. Keeping the blocked operation active makes the
                # primary UI actions fail forever with LEASE_CONFLICT.
                if (
                    active is not None
                    and str(active["status"] or "") == "blocked"
                    and envelope.kind in self.BLOCKED_SUPERSEDING_KINDS
                ):
                    connection.execute(
                        "UPDATE operations SET status = 'cancelled', message = ?, "
                        "updated_at = ?, completed_at = ? WHERE operation_id = ?",
                        (
                            "已由用户重新规划或生成替代。",
                            now,
                            now,
                            str(active["operation_id"]),
                        ),
                    )
                    connection.execute(
                        "DELETE FROM workspace_lease WHERE operation_id = ?",
                        (str(active["operation_id"]),),
                    )
                    active = None
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
                    if (
                        envelope.kind in {"pipeline.pause", "pipeline.cancel"}
                        and previous_status in {"succeeded", "failed", "cancelled"}
                    ):
                        revision = self._bump_revision(connection)
                        message = f"Operation 已处于终态 {previous_status}，无需执行 {envelope.kind}。"
                        connection.execute(
                            """
                            INSERT INTO commands(
                                command_id, kind, payload_json, goal_id, actor_json,
                                expected_revision, idempotency_key, status, operation_id,
                                confirmation_id, message, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'no_op', ?, ?, ?, ?, ?)
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
                                message,
                                now,
                                now,
                            ),
                        )
                        self._event(
                            connection,
                            revision,
                            "CommandNoOp",
                            "Command",
                            envelope.command_id,
                            {"kind": envelope.kind, "operation_id": operation_id, "operation_status": previous_status},
                        )
                        connection.commit()
                        return CommandReceipt(
                            command_id=envelope.command_id,
                            operation_id=operation_id,
                            status="no_op",
                            workspace_revision=revision,
                            message=message,
                        ), False
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
                    if active and str(active["status"] or "") in self.LOCK_OPERATION_STATES and not blocked_pipeline_parent:
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
                terminal_at = now if operation_status in {"succeeded", "failed", "cancelled", "blocked_human"} else None
                connection.execute(
                    """
                    UPDATE operations SET status = ?, message = ?, error_json = ?, updated_at = ?,
                        completed_at = COALESCE(?, completed_at)
                    WHERE operation_id = ?
                    """,
                    (operation_status, message, _json(error) if error else None, now, terminal_at, operation_id),
                )
                if operation_status not in {"queued", "running", "pausing", "cancelling"}:
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
                current_status = str(row["status"])
                terminal_states = {"succeeded", "failed", "cancelled"}
                if current_status in terminal_states:
                    if current_status != status:
                        raise ControlPlaneError(
                            "STATE_CONFLICT",
                            "Operation 已处于终态，拒绝覆盖控制状态。",
                            status_code=409,
                            details={
                                "operation_id": operation_id,
                                "current_status": current_status,
                                "requested_status": status,
                            },
                        )
                    connection.commit()
                    return self._revision(connection)
                error_json = _json(error) if error else None
                if str(row["status"]) == status and str(row["message"] or "") == message and row["error_json"] == error_json:
                    if status in {"queued", "running", "pausing", "cancelling"}:
                        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec="milliseconds")
                        connection.execute(
                            "UPDATE workspace_lease SET heartbeat_at = ?, expires_at = ? WHERE operation_id = ?",
                            (now, expires_at, operation_id),
                        )
                    else:
                        connection.execute("DELETE FROM workspace_lease WHERE operation_id = ?", (operation_id,))
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
                if status not in {"queued", "running", "pausing", "cancelling"}:
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
                    "type": "confirm_command",
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
                expires_at = _parse_utc_timestamp(row["expires_at"], label="Action 到期时间")
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

    def create_batch_job(
        self,
        chapters: list[dict[str, Any]],
        *,
        job_id: str | None = None,
        operation_id: str | None = None,
        retry_policy: dict[str, Any] | None = None,
        actor: str = "batch-worker",
    ) -> dict[str, Any]:
        """Create a durable chapter batch and its operation in one transaction."""
        job_id = str(job_id or uuid.uuid4())
        operation_id = str(operation_id or job_id)
        existing = self.batch_job(job_id)
        if existing is not None:
            return existing
        now = _now()
        normalized = []
        for position, chapter in enumerate(chapters):
            chapter_id = str(chapter.get("chapter_id") or "").strip()
            if not chapter_id:
                raise ControlPlaneError("CHAPTER_ID_INVALID", "批量任务缺少 chapter_id。", status_code=400)
            normalized.append({
                "item_id": str(chapter.get("item_id") or uuid.uuid4()),
                "chapter_id": chapter_id,
                "chapter_title": str(chapter.get("chapter_title") or chapter.get("title") or ""),
                "position": position,
                "context_ref": chapter.get("context_ref") if isinstance(chapter.get("context_ref"), dict) else {},
            })
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO operations(operation_id, kind, status, fencing_token, created_at, updated_at) "
                    "VALUES (?, 'chapter.generate_batch', 'background', 1, ?, ?)",
                    (operation_id, now, now),
                )
                connection.execute(
                    "INSERT INTO chapter_batch_jobs(job_id, operation_id, status, chapter_ids_json, retry_policy_json, fencing_token, created_at, updated_at) "
                    "VALUES (?, ?, 'queued', ?, ?, 1, ?, ?)",
                    (job_id, operation_id, _json([item["chapter_id"] for item in normalized]), _json(retry_policy or {}), now, now),
                )
                for item in normalized:
                    connection.execute(
                        "INSERT INTO chapter_batch_items(item_id, job_id, chapter_id, chapter_title, position, status, stage, context_ref_json, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)",
                        (item["item_id"], job_id, item["chapter_id"], item["chapter_title"], item["position"], _json(item["context_ref"]), now, now),
                    )
                self._bump_revision(connection)
                self._event(connection, self._revision(connection), "ChapterBatchCreated", "ChapterBatch", job_id, {"job_id": job_id, "operation_id": operation_id, "chapter_count": len(normalized), "actor": actor})
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.batch_job(job_id) or {}

    @staticmethod
    def _batch_decode(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for key in ("chapter_ids_json", "retry_policy_json", "context_ref_json", "artifact_refs_json", "data_json", "error_json"):
            if key in item:
                target = key.removesuffix("_json")
                item[target] = _decode(item.pop(key), {} if key != "error_json" else None)
        return item

    def batch_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM chapter_batch_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
            if not row:
                return None
            result = self._batch_decode(row)
            result["items"] = [self._batch_decode(item) for item in connection.execute("SELECT * FROM chapter_batch_items WHERE job_id = ? ORDER BY position", (str(job_id),)).fetchall()]
        return result

    def latest_batch_job(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT job_id FROM chapter_batch_jobs "
                "ORDER BY CASE WHEN status IN ('queued', 'running', 'paused') THEN 0 ELSE 1 END, "
                "updated_at DESC LIMIT 1"
            ).fetchone()
        return self.batch_job(str(row["job_id"])) if row else None

    def update_batch_job(self, job_id: str, *, status: str | None = None, current_chapter_id: str | None = None, completed_count: int | None = None, failed_count: int | None = None, error: dict[str, Any] | None = None, fencing_token: int | None = None) -> dict[str, Any]:
        fields, values = [], []
        for name, value in (("status", status), ("current_chapter_id", current_chapter_id), ("completed_count", completed_count), ("failed_count", failed_count), ("error_json", _json(error) if error is not None else None), ("fencing_token", fencing_token)):
            if value is not None:
                fields.append(f"{name} = ?"); values.append(value)
        if not fields:
            return self.batch_job(job_id) or {}
        now = _now(); fields.append("updated_at = ?"); values.append(now); values.append(str(job_id))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(f"UPDATE chapter_batch_jobs SET {', '.join(fields)} WHERE job_id = ?", values)
                operation_status = (
                    status if status in {"succeeded", "failed", "cancelled"} else "background"
                )
                if status is not None:
                    connection.execute(
                        "UPDATE operations SET status = ?, updated_at = ?, "
                        "completed_at = CASE WHEN ? IN ('succeeded', 'failed', 'cancelled') "
                        "THEN COALESCE(completed_at, ?) ELSE completed_at END "
                        "WHERE operation_id = (SELECT operation_id FROM chapter_batch_jobs WHERE job_id = ?)",
                        (operation_status, now, operation_status, now, str(job_id)),
                    )
                if status in {"succeeded", "failed", "cancelled"}:
                    connection.execute("UPDATE chapter_batch_jobs SET completed_at = COALESCE(completed_at, ?) WHERE job_id = ?", (now, str(job_id)))
                connection.commit()
            except Exception:
                connection.rollback(); raise
        return self.batch_job(job_id) or {}

    def update_batch_item(self, item_id: str, *, status: str | None = None, stage: str | None = None, attempt: int | None = None, context_ref: dict[str, Any] | None = None, content_revision: int | None = None, error: dict[str, Any] | None = None) -> dict[str, Any] | None:
        fields, values = [], []
        for name, value in (("status", status), ("stage", stage), ("attempt", attempt), ("context_ref_json", _json(context_ref) if context_ref is not None else None), ("content_revision", content_revision), ("error_json", _json(error) if error is not None else None)):
            if value is not None:
                fields.append(f"{name} = ?"); values.append(value)
        if not fields: return self.batch_item(item_id)
        values.extend([_now(), str(item_id)])
        with self._connection() as connection:
            connection.execute(f"UPDATE chapter_batch_items SET {', '.join(fields)}, updated_at = ? WHERE item_id = ?", values)
        return self.batch_item(item_id)

    def batch_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM chapter_batch_items WHERE item_id = ?", (str(item_id),)).fetchone()
        return self._batch_decode(row) if row else None

    def append_batch_event(self, job_id: str, *, event_type: str, status: str = "", stage: str = "", item_id: str | None = None, chapter_id: str | None = None, chapter_title: str = "", message: str = "", data: dict[str, Any] | None = None, error: dict[str, Any] | None = None, event_id: str | None = None) -> dict[str, Any]:
        event_id = str(event_id or uuid.uuid4()); now = _now()
        with self._connection() as connection:
            connection.execute("INSERT INTO chapter_batch_events(event_id, job_id, item_id, chapter_id, chapter_title, stage, type, status, message, data_json, error_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (event_id, str(job_id), item_id, chapter_id, chapter_title, stage, event_type, status, message, _json(data or {}), _json(error) if error else None, now))
            row = connection.execute("SELECT * FROM chapter_batch_events WHERE event_id = ?", (event_id,)).fetchone()
        return self._batch_decode(row)

    def batch_events(self, job_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM chapter_batch_events WHERE job_id = ? AND sequence > ? ORDER BY sequence LIMIT ?", (str(job_id), max(0, int(after_sequence)), max(1, min(int(limit), 2000)))).fetchall()
        return [self._batch_decode(row) for row in rows]

    def save_batch_checkpoint(self, job_id: str, item_id: str, *, stage: str, input_hash: str = "", artifact_refs: dict[str, Any] | None = None, event_sequence: int = 0) -> dict[str, Any]:
        checkpoint_id = str(uuid.uuid4())
        with self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO chapter_batch_checkpoints(checkpoint_id, job_id, item_id, stage, input_hash, artifact_refs_json, event_sequence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (checkpoint_id, str(job_id), str(item_id), stage, input_hash, _json(artifact_refs or {}), int(event_sequence), _now()))
            row = connection.execute("SELECT * FROM chapter_batch_checkpoints WHERE job_id = ? AND item_id = ? AND stage = ? AND input_hash = ?", (str(job_id), str(item_id), stage, input_hash)).fetchone()
        return self._batch_decode(row)

    def batch_checkpoint(self, item_id: str, *, stage: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM chapter_batch_checkpoints WHERE item_id = ?"
        values: list[Any] = [str(item_id)]
        if stage is not None:
            query += " AND stage = ?"
            values.append(str(stage))
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, values).fetchone()
        return self._batch_decode(row) if row else None

    def recover_batch_jobs(self) -> list[dict[str, Any]]:
        """Return jobs that can be resumed after a process restart."""
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM chapter_batch_jobs WHERE status IN ('queued', 'running', 'paused') ORDER BY created_at").fetchall()
        return [self._batch_decode(row) for row in rows]

    def claim_batch_job(self, job_id: str) -> dict[str, Any] | None:
        """Claim a resumable job and invalidate writers holding an older token."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, fencing_token FROM chapter_batch_jobs WHERE job_id = ?",
                    (str(job_id),),
                ).fetchone()
                if not row or str(row["status"] or "") in {"succeeded", "failed", "cancelled"}:
                    connection.rollback()
                    return None
                token = int(row["fencing_token"] or 0) + 1
                connection.execute(
                    "UPDATE chapter_batch_jobs SET status = 'running', fencing_token = ?, updated_at = ? WHERE job_id = ?",
                    (token, _now(), str(job_id)),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.batch_job(job_id)

    def assert_batch_fence(self, job_id: str, fencing_token: int) -> None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT fencing_token, status FROM chapter_batch_jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
        if not row or int(row["fencing_token"] or 0) != int(fencing_token):
            raise ControlPlaneError(
                "CHAPTER_BATCH_LEASE_LOST",
                "批量任务已由新的 Worker 接管，旧 Worker 已停止写入。",
                status_code=409,
            )
        if str(row["status"] or "") == "cancelled":
            raise ControlPlaneError("CHAPTER_BATCH_CANCELLED", "批量任务已取消。", status_code=409)

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
            stage_runs = [dict(row) for row in connection.execute(
                "SELECT * FROM stage_runs ORDER BY started_at DESC LIMIT 100"
            ).fetchall()]
            confirmations = [dict(row) for row in connection.execute(
                "SELECT confirmation_id, risk, label, status, expected_revision, expires_at, created_at "
                "FROM confirmations WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()]
            lease_row = connection.execute("SELECT * FROM workspace_lease WHERE singleton = 1").fetchone()
            artifact_rows = connection.execute(
                "SELECT * FROM artifact_states ORDER BY artifact_key"
            ).fetchall()
        for operation in operations:
            operation["error"] = _decode(operation.pop("error_json", None), None)
        for stage_run in stage_runs:
            stage_run["error"] = _decode(stage_run.pop("error_json", None), None)
            stage_run["output"] = _decode(stage_run.pop("output_json", None), None)
        current_operation = next(
            (item for item in operations if str(item.get("status") or "") in self.LOCK_OPERATION_STATES),
            operations[0] if operations else None,
        )
        current_operation_id = str((current_operation or {}).get("operation_id") or "")
        return {
            "workspace_id": self.context.workspace_id,
            "revision": revision,
            "operation": current_operation,
            "operations": operations,
            "commands": commands,
            "stage_runs": stage_runs,
            "current_stage_runs": [
                item for item in stage_runs if current_operation_id and str(item.get("operation_id") or "") == current_operation_id
            ],
            "confirmations": confirmations,
            "lease": dict(lease_row) if lease_row else None,
            "artifacts": [self._artifact_row(row) for row in artifact_rows],
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

    def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest events in ascending sequence order."""
        capped_limit = max(1, min(int(limit), 2000))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspace_events ORDER BY seq DESC LIMIT ?",
                (capped_limit,),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in reversed(rows):
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
            public_result = {
                key: result[key]
                for key in (
                    "operation_status",
                    "completed_stages",
                    "planning_snapshot",
                    "planning_receipt",
                    "chapter",
                    "context",
                    "content",
                    "approval",
                    "unchanged",
                    "batch_job",
                )
                if key in result
            }
            if public_result:
                receipt = replace(receipt, result=public_result)
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
            try:
                after_commit()
            except Exception as exc:
                error = ControlPlaneError(
                    "COMMAND_POST_COMMIT_FAILED",
                    f"Command 已提交但后续执行未能启动: {exc}",
                    status_code=500,
                )
                return self.store.finish_dispatch(
                    envelope,
                    operation_id,
                    success=False,
                    operation_status="failed",
                    message=error.message,
                    error=error.as_dict(),
                )
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
