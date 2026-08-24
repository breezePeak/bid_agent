from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api import v3_app  # noqa: E402
from control_plane import (  # noqa: E402
    CommandEnvelope,
    CommandGateway,
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.chapter_chat import ChapterChatService  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.chapter_writing_plan import (  # noqa: E402
    ChapterWritingPlanService,
)
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    ChapterWritingPlanCandidate,
    DocumentMode,
)
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def _promote(
    store: ControlStore,
    artifact_kind: str,
    payload: dict,
    *,
    revision: int = 1,
) -> dict:
    artifact_hash = canonical_payload_hash(payload)
    now = "2026-08-24T00:00:00.000+00:00"
    proposal_id = f"proposal-{artifact_kind}-{revision}-{uuid.uuid4()}"
    proposal_hash = f"proposal-hash-{artifact_kind}-{revision}-{uuid.uuid4()}"
    with store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO v3_proposals(
                proposal_id, workspace_id, artifact_kind, producer_role,
                operation_id, base_revision, dependency_fingerprint,
                declared_dependencies_json, proposal_hash,
                canonical_payload_hash, payload_json, cited_source_ids_json,
                prompt_version, model_fingerprint, status, created_at
            ) VALUES (?, ?, ?, 'test', ?, 0, 'test-fingerprint', '[]',
                      ?, ?, ?, '[]', 'test', 'test', 'promoted', ?)
            """,
            (
                proposal_id,
                store.context.workspace_id,
                artifact_kind,
                f"operation-{uuid.uuid4()}",
                proposal_hash,
                artifact_hash,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO v3_artifact_revisions(
                artifact_kind, revision, artifact_id, artifact_hash, payload_json,
                producer_role, dependency_fingerprint, proposal_id, proposal_hash,
                created_at
            ) VALUES (?, ?, ?, ?, ?, 'test', 'test-fingerprint', ?, ?, ?)
            """,
            (
                artifact_kind,
                revision,
                f"{artifact_kind}@{revision}",
                artifact_hash,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                proposal_id,
                proposal_hash,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO v3_active_artifacts(
                artifact_kind, artifact_id, revision, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(artifact_kind) DO UPDATE SET
                artifact_id=excluded.artifact_id,
                revision=excluded.revision,
                updated_at=excluded.updated_at
            """,
            (artifact_kind, f"{artifact_kind}@{revision}", revision, now),
        )
        connection.commit()
    return {"revision": revision, "artifact_hash": artifact_hash, "payload": payload}


def _seed_blueprint(
    context: WorkspaceContext,
    nodes: list[BlueprintNode] | None = None,
    *,
    revision: int = 1,
) -> dict:
    blueprint = ChapterBlueprint(
        schema_version="v3",
        revision=revision,
        source_hashes={},
        blueprint_id=f"bp-{revision}",
        mode=DocumentMode.AUTO_OUTLINE,
        planning_model="score_direct",
        requirement_ledger_revision=1,
        score_model_revision=1,
        nodes=nodes
        or [
            BlueprintNode(
                chapter_id="ch-a",
                order=0,
                title="技术方案",
                purpose="说明技术路线",
            )
        ],
        assignments=[],
    )
    return _promote(
        ControlStore(context),
        "ChapterBlueprint",
        blueprint.model_dump(mode="json"),
        revision=revision,
    )


def _setup(base: Path) -> tuple[WorkspaceContext, ChapterWritingPlanService, dict]:
    context = _workspace(base)
    _seed_blueprint(context)
    chapter = ChapterWorkspaceService(context).create(
        chapter_id="ch-a",
        expected_chapter_revision=0,
    )
    return context, ChapterWritingPlanService(context), chapter


def _candidate(label: str = "总体技术路线") -> dict:
    return {
        "schema_version": "v3.chapter-writing-plan.v2",
        "content_units": [
            {
                "unit_id": "unit-1",
                "title": label,
                "instructions": f"写清{label}及其实施边界。",
                "order": 0,
                "source_refs": [],
            }
        ],
        "metadata": {"b": 2, "a": 1},
    }


def _append(
    service: ChapterWritingPlanService,
    chapter: dict,
    candidate: dict | None = None,
) -> dict:
    return service.append(
        chapter_id="ch-a",
        expected_chapter_revision=int(chapter["chapter_revision"]),
        plan=candidate or _candidate(),
    )


def test_contracts_forbid_extra_and_plan_hash_is_canonical() -> None:
    with pytest.raises(ValidationError):
        ChapterWritingPlanCandidate.model_validate({**_candidate(), "unknown": True})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        _context, service, chapter = _setup(Path(temporary))
        first = _append(service, chapter)
        reordered = _candidate()
        reordered["metadata"] = {"a": 1, "b": 2}
        duplicate = service.append(
            chapter_id="ch-a",
            expected_chapter_revision=int(chapter["chapter_revision"]),
            plan=reordered,
        )
        assert duplicate["unchanged"] is True
        assert duplicate["plan"]["plan_hash"] == first["plan"]["plan_hash"]


def test_pr03_sources_bindings_and_research_decisions_are_persisted() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        _context, service, chapter = _setup(Path(temporary))
        candidate = {
            "schema_version": "v3.chapter-writing-plan.v2",
            "content_units": [{
                "unit_id": "WO-1",
                "title": "现行标准",
                "instructions": "引用可核验现行标准",
                "purpose": "说明标准依据",
                "must_answer": "适用哪些标准",
                "order": 0,
                "source_refs": ["PS-1"],
            }],
            "sources": [{
                "source_id": "PS-1",
                "source_type": "WEB_EVIDENCE",
                "reference_id": "EV-1",
                "content_hash": "evidence-hash",
                "title": "标准原文",
                "preview": "发布机构",
                "snapshot_ref": "evidence:EB-1:EV-1",
            }],
            "source_bindings": [{
                "source_id": "PS-1",
                "content_unit_id": "WO-1",
                "usage_type": "evidence",
                "instruction": "仅用于可核验支持范围",
                "required": False,
            }],
            "research_decisions": [{
                "decision_id": "PRD-1",
                "content_unit_id": "WO-1",
                "needs_research": True,
                "prohibited": False,
                "reason": "缺少现行标准",
                "query": "现行国家标准",
                "status": "published",
                "evidence_ids": ["EV-1"],
            }],
            "metadata": {"projection": "shadow_builder"},
        }
        appended = service.append(
            chapter_id="ch-a",
            expected_chapter_revision=int(chapter["chapter_revision"]),
            plan=candidate,
            source="shadow_builder",
        )

        assert appended["plan"]["source"] == "shadow_builder"
        assert appended["plan"]["sources"][0]["source_id"] == "PS-1"
        assert appended["plan"]["source_bindings"][0]["content_unit_id"] == "WO-1"
        assert appended["plan"]["research_decisions"][0]["evidence_ids"] == ["EV-1"]


def test_schema_migration_adds_plan_tables_pointers_and_version() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context = _workspace(Path(temporary))
        store = ControlStore(context)
        with store._connection() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(chapter_workspaces)")
            }
            version = connection.execute(
                "SELECT value FROM control_meta WHERE key='schema_version'"
            ).fetchone()[0]
        assert {
            "chapter_writing_plan_revisions",
            "chapter_plan_approval_receipts",
            "chapter_plan_events",
        }.issubset(tables)
        assert {
            "head_plan_revision",
            "confirmed_plan_revision",
            "plan_status",
        }.issubset(columns)
        assert version == str(ControlStore.SCHEMA_VERSION)


def test_append_only_duplicate_idempotency_and_stale_cas() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        first = _append(service, chapter)
        duplicate = _append(service, chapter)
        assert duplicate["unchanged"] is True
        with pytest.raises(ControlPlaneError) as conflict:
            service.append(
                chapter_id="ch-a",
                expected_chapter_revision=int(chapter["chapter_revision"]),
                plan=_candidate("安全保障"),
            )
        assert conflict.value.code == "CHAPTER_REVISION_CONFLICT"

        current = ControlStore(context).chapter_workspace("ch-a") or {}
        second = _append(service, current, _candidate("安全保障"))
        assert second["plan"]["plan_revision"] == 2
        assert second["plan"]["parent_plan_revision"] == 1
        with ControlStore(context)._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM chapter_writing_plan_revisions "
                "WHERE chapter_id='ch-a'"
            ).fetchone()[0]
        assert count == 2
        assert first["plan"]["plan_hash"] != second["plan"]["plan_hash"]


def test_exact_receipt_binding_and_retry_are_idempotent() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        appended = _append(service, chapter)
        current = ControlStore(context).chapter_workspace("ch-a") or {}
        plan = appended["plan"]
        receipt = service.confirm(
            chapter_id="ch-a",
            expected_chapter_revision=int(current["chapter_revision"]),
            plan_revision=int(plan["plan_revision"]),
            plan_hash=str(plan["plan_hash"]),
            dependency_fingerprint=str(plan["dependency_fingerprint"]),
            principal_id="owner",
        )
        retry = service.confirm(
            chapter_id="ch-a",
            expected_chapter_revision=int(current["chapter_revision"]),
            plan_revision=int(plan["plan_revision"]),
            plan_hash=str(plan["plan_hash"]),
            dependency_fingerprint=str(plan["dependency_fingerprint"]),
            principal_id="owner",
        )
        assert retry["receipt_id"] == receipt["receipt_id"]
        assert retry["receipt_hash"] == receipt["receipt_hash"]

        latest = ControlStore(context).chapter_workspace("ch-a") or {}
        with pytest.raises(ControlPlaneError) as forged:
            service.confirm(
                chapter_id="ch-a",
                expected_chapter_revision=int(latest["chapter_revision"]),
                plan_revision=int(plan["plan_revision"]),
                plan_hash="forged",
                dependency_fingerprint=str(plan["dependency_fingerprint"]),
                principal_id="owner",
            )
        assert forged.value.code == "PLAN_BINDING_MISMATCH"


def test_dependency_change_blocks_confirmation_and_reports_stale_global() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        appended = _append(service, chapter)
        _promote(ControlStore(context), "ProjectModel", {"revision": 1})
        current = ControlStore(context).chapter_workspace("ch-a") or {}
        plan = appended["plan"]
        with pytest.raises(ControlPlaneError) as stale:
            service.confirm(
                chapter_id="ch-a",
                expected_chapter_revision=int(current["chapter_revision"]),
                plan_revision=int(plan["plan_revision"]),
                plan_hash=str(plan["plan_hash"]),
                dependency_fingerprint=str(plan["dependency_fingerprint"]),
                principal_id="owner",
            )
        assert stale.value.code == "PLAN_STALE"
        assert stale.value.details["status"] == "stale_global_context"
        assert service.read("ch-a")["status"] == "stale_global_context"


def test_source_and_evidence_changes_have_distinct_stale_statuses() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        _append(service, chapter)
        store = ControlStore(context)
        _promote(store, "SourceIndex", {"revision": 1, "blocks": []})
        assert service.read("ch-a")["status"] == "stale_source"

        current = store.chapter_workspace("ch-a") or {}
        refreshed = _append(service, current)
        assert refreshed["plan"]["status"] == "current"
        store.upsert_evidence_need(
            {
                "need_id": "need-1",
                "question": "需要核验的公开标准是什么？",
                "topic_id": "topic-1",
                "priority": "high",
                "blocking_scope": "content_unit",
                "deadline_stage": "write",
                "query_budget": 1,
                "status": "open",
            }
        )
        assert service.read("ch-a")["status"] == "stale_evidence"


def test_context_change_clears_confirmation_and_marks_plan_stale() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        appended = _append(service, chapter)
        store = ControlStore(context)
        current = store.chapter_workspace("ch-a") or {}
        plan = appended["plan"]
        service.confirm(
            chapter_id="ch-a",
            expected_chapter_revision=int(current["chapter_revision"]),
            plan_revision=int(plan["plan_revision"]),
            plan_hash=str(plan["plan_hash"]),
            dependency_fingerprint=str(plan["dependency_fingerprint"]),
            principal_id="owner",
        )
        confirmed = store.chapter_workspace("ch-a") or {}
        store.append_chapter_context_revision(
            chapter_id="ch-a",
            expected_chapter_revision=int(confirmed["chapter_revision"]),
            items=[
                {
                    "item_id": "ctx-2",
                    "kind": "KEY_FACT",
                    "title": "新事实",
                    "body": "依赖发生变化",
                    "order": 0,
                    "source": "USER",
                }
            ],
        )
        changed = store.chapter_workspace("ch-a") or {}
        assert changed["confirmed_plan_revision"] == 0
        assert changed["plan_status"] == "stale_chapter_context"
        assert service.read("ch-a")["status"] == "stale_chapter_context"


def test_parent_chapter_cannot_receive_plan() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context = _workspace(Path(temporary))
        _seed_blueprint(
            context,
            [
                BlueprintNode(
                    chapter_id="parent",
                    order=0,
                    title="总体方案",
                    purpose="结构节点",
                ),
                BlueprintNode(
                    chapter_id="leaf",
                    parent_chapter_id="parent",
                    order=1,
                    title="技术路线",
                    purpose="说明技术路线",
                ),
            ],
        )
        chapter = ChapterWorkspaceService(context).create(
            chapter_id="parent",
            expected_chapter_revision=0,
        )
        with pytest.raises(ControlPlaneError) as blocked:
            ChapterWritingPlanService(context).append(
                chapter_id="parent",
                expected_chapter_revision=int(chapter["chapter_revision"]),
                plan=_candidate(),
            )
        assert blocked.value.code == "CHAPTER_BODY_REQUIRES_LEAF"


def test_legacy_json_seed_is_idempotent_and_failure_is_non_blocking() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        path = context.root / "workspace/v3/chapter_chats/_writing_plans.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "v3.chapter-writing-plan.v1",
                    "chapters": {
                        "ch-a": {
                            "writing_plan": {
                                "schema_version": "v3.chapter-writing-plan.v1",
                                "chapter_id": "ch-a",
                                "blocks": [
                                    {
                                        "block_id": "WO-1",
                                        "heading": "技术路线",
                                        "must_answer": "写清技术路线",
                                        "write_as": "按项目边界展开",
                                    }
                                ],
                            }
                        },
                        "missing": {"writing_plan": {"blocks": []}},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            {"BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1"},
            clear=False,
        ):
            first = service.import_legacy_json(path)
            second = service.import_legacy_json(path)
        assert first["imported"] == 1
        assert first["failed"]
        assert second["unchanged"] == 1
        assert path.is_file()


def test_snapshot_restart_and_read_api_restore_plan_and_receipt() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        base = Path(temporary)
        context, service, chapter = _setup(base)
        appended = _append(service, chapter)
        store = ControlStore(context)
        current = store.chapter_workspace("ch-a") or {}
        plan = appended["plan"]
        receipt = service.confirm(
            chapter_id="ch-a",
            expected_chapter_revision=int(current["chapter_revision"]),
            plan_revision=int(plan["plan_revision"]),
            plan_hash=str(plan["plan_hash"]),
            dependency_fingerprint=str(plan["dependency_fingerprint"]),
            principal_id="owner",
        )

        restarted = ChapterWritingPlanService(
            WorkspaceContext.resolve(base / "runs", "alpha")
        )
        assert restarted.read("ch-a")["status"] == "confirmed"
        assert (
            restarted.store.chapter_plan_approval_receipt("ch-a")["receipt_id"]
            == receipt["receipt_id"]
        )
        snapshot = V3WorkspaceSnapshotBuilder(context).build()
        item = snapshot["chapters"]["items"][0]
        assert item["writing_plan"]["status"] == "confirmed"
        assert snapshot["chapters"]["writing_plans"]["status_counts"] == {
            "confirmed": 1
        }

        with mock.patch.object(v3_app, "RUNS_DIR", base / "runs"):
            response = v3_app.get_chapter_writing_plan("alpha", "ch-a", None)
        body = json.loads(response.body)
        assert body["plan"]["plan_hash"] == plan["plan_hash"]
        assert body["receipt"]["receipt_id"] == receipt["receipt_id"]


def test_sqlite_failure_rolls_back_half_plan_revision() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        with mock.patch.object(
            ControlStore,
            "_event",
            side_effect=RuntimeError("interrupt before commit"),
        ):
            with pytest.raises(RuntimeError):
                _append(service, chapter)
        store = ControlStore(context)
        restored = store.chapter_workspace("ch-a") or {}
        assert restored["head_plan_revision"] == 0
        with store._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM chapter_writing_plan_revisions"
            ).fetchone()[0]
        assert count == 0


def test_commands_are_registered_flagged_and_confirmation_requires_user() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, chapter = _setup(Path(temporary))
        handlers = V3ExecutionController(context).handlers()
        assert {
            "chapter.plan.propose",
            "chapter.plan.append",
            "chapter.plan.confirm",
            "chapter.plan.invalidate",
            "chapter.plan.shadow.generate",
        }.issubset(handlers)
        append_envelope = CommandEnvelope.from_mapping(
            {
                "command_id": str(uuid.uuid4()),
                "kind": "chapter.plan.propose",
                "payload": {
                    "chapter_id": "ch-a",
                    "expected_chapter_revision": chapter["chapter_revision"],
                    "plan": _candidate(),
                },
                "expected_revision": ControlStore(context).revision(),
                "idempotency_key": str(uuid.uuid4()),
                "actor": {"type": "agent", "id": "planner"},
            },
            workspace_id="alpha",
        )
        with mock.patch.dict(
            os.environ,
            {"BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1"},
            clear=False,
        ):
            append_receipt = CommandGateway(context, handlers).submit(append_envelope)
        assert append_receipt.status == "accepted", append_receipt
        assert append_receipt.result["plan"]["status"] == "current"
        assert append_receipt.result["chapter"]["head_plan_revision"] == 1
        envelope = CommandEnvelope.from_mapping(
            {
                "command_id": str(uuid.uuid4()),
                "kind": "chapter.plan.confirm",
                "payload": {
                    "chapter_id": "ch-a",
                    "expected_chapter_revision": 1,
                    "plan_revision": 1,
                    "plan_hash": "x",
                    "dependency_fingerprint": "y",
                },
                "expected_revision": ControlStore(context).revision(),
                "idempotency_key": str(uuid.uuid4()),
                "actor": {"type": "agent", "id": "planner"},
            },
            workspace_id="alpha",
        )
        with mock.patch.dict(
            os.environ,
            {"BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1"},
            clear=False,
        ):
            with pytest.raises(ControlPlaneError) as unauthorized:
                service.handle_confirm(context, envelope, "operation")
        assert unauthorized.value.code == "PLAN_APPROVAL_INVALID"


def test_pr03_shadow_command_appends_unconfirmed_revision() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, _service, chapter = _setup(Path(temporary))
        store = ControlStore(context)
        _promote(
            store,
            "RequirementLedger",
            {
                "revision": 1,
                "requirements": [{
                    "requirement_id": "REQ-1",
                    "normalized_requirement": "完成数据治理",
                }],
            },
        )
        _promote(
            store,
            "ScoreModel",
            {
                "revision": 1,
                "points": [{
                    "score_point_id": "S-1",
                    "title": "方案完整性",
                }],
            },
        )
        _promote(
            store,
            "ProjectModel",
            {
                "revision": 1,
                "project_id": "project-1",
                "identity": {"project_name": "数据治理项目"},
                "work_packages": ["完成数据治理"],
            },
        )
        handlers = V3ExecutionController(context).handlers()
        envelope = CommandEnvelope.from_mapping(
            {
                "command_id": str(uuid.uuid4()),
                "kind": "chapter.plan.shadow.generate",
                "payload": {
                    "chapter_id": "ch-a",
                    "writing_plan": {
                        "schema_version": "v3.chapter-writing-plan.v1",
                        "chapter_id": "ch-a",
                        "chapter_title": "工作内容",
                        "purpose": "说明工作内容",
                        "blocks": [{
                            "block_id": "WO-1",
                            "heading": "数据治理",
                            "must_answer": "说明数据治理任务",
                            "write_as": "按项目事实说明",
                            "requirement_ids": ["REQ-1"],
                            "score_point_id": "S-1",
                            "project_fact_refs": ["work_packages[0]"],
                        }],
                    },
                    "project_context": {"work_packages": ["完成数据治理"]},
                },
                "expected_revision": ControlStore(context).revision(),
                "idempotency_key": str(uuid.uuid4()),
                "actor": {"type": "agent", "id": "shadow-planner"},
            },
            workspace_id="alpha",
        )
        with mock.patch.dict(
            os.environ,
            {
                "BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1",
                "BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED": "1",
            },
            clear=False,
        ):
            receipt = CommandGateway(context, handlers).submit(envelope)

        assert receipt.status == "accepted", receipt
        assert receipt.result["plan"]["source"] == "shadow_builder"
        assert receipt.result["shadow"]["status"] == "ready"
        assert receipt.result["shadow"]["metrics"]["content_unit_count"] == 1
        assert receipt.result["shadow"]["metrics"]["failed_search_count"] == 0
        assert {
            item["source_type"] for item in receipt.result["plan"]["sources"]
        } == {
            "TENDER_REQUIREMENT",
            "SCORE_OBLIGATION",
            "GLOBAL_PROJECT_FACT",
            "CHAPTER_CONTEXT_ITEM",
        }
        stored = ControlStore(context).chapter_workspace("ch-a") or {}
        assert stored["head_plan_revision"] == 1
        assert stored["confirmed_plan_revision"] == 0
        assert stored["plan_status"] == "current"
        with ControlStore(context)._connection() as connection:
            succeeded = connection.execute(
                "SELECT COUNT(*) FROM chapter_plan_events "
                "WHERE chapter_id='ch-a' AND event_type='shadow_succeeded'"
            ).fetchone()[0]
        assert succeeded == 1
        snapshot = V3WorkspaceSnapshotBuilder(context).build()
        summary = snapshot["chapters"]["items"][0]["writing_plan"]
        assert summary["source"] == "shadow_builder"
        assert summary["shadow_status"] == "ready"
        assert snapshot["chapters"]["writing_plans"]["shadow_status_counts"] == {
            "ready": 1,
            "failed": 0,
        }


def test_pr03_legacy_plan_save_routes_to_authoritative_shadow_builder() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, _service, _chapter = _setup(Path(temporary))
        chat = ChapterChatService(context)
        writing_plan = {
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-a",
            "blocks": [{
                "block_id": "WO-1",
                "heading": "技术路线",
                "must_answer": "写清技术路线",
                "write_as": "按章节目标展开",
            }],
        }
        with mock.patch.dict(
            os.environ,
            {
                "BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1",
                "BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED": "1",
            },
            clear=False,
        ), mock.patch.object(
            ChapterWritingPlanService,
            "append_shadow_best_effort",
            autospec=True,
            return_value={"unchanged": False},
        ) as shadow:
            chat.save_writing_plan("ch-a", writing_plan)

        shadow.assert_called()
        assert shadow.call_args.kwargs["chapter_id"] == "ch-a"
        assert shadow.call_args.kwargs["writing_plan"] == writing_plan


def test_pr03_shadow_failure_is_durable_and_does_not_move_plan_head() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, service, _chapter = _setup(Path(temporary))
        with mock.patch.dict(
            os.environ,
            {
                "BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "1",
                "BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED": "1",
            },
            clear=False,
        ), mock.patch.object(
            service,
            "authoritative_shadow_inputs",
            side_effect=RuntimeError("authority unavailable"),
        ):
            result = service.append_shadow_best_effort(
                chapter_id="ch-a",
                writing_plan={"blocks": []},
            )

        assert result["shadow_status"] == "failed"
        stored = ControlStore(context).chapter_workspace("ch-a") or {}
        assert stored["head_plan_revision"] == 0
        assert stored["confirmed_plan_revision"] == 0
        failure = ControlStore(context).latest_chapter_plan_shadow_failure("ch-a")
        assert failure["error_code"] == "RuntimeError"
        snapshot = V3WorkspaceSnapshotBuilder(context).build()
        summary = snapshot["chapters"]["items"][0]["writing_plan"]
        assert summary["shadow_status"] == "failed"
        assert summary["shadow_error"]["error_code"] == "RuntimeError"


def test_flag_off_preserves_legacy_inline_json_behavior() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        context, _service, _chapter = _setup(Path(temporary))
        chat = ChapterChatService(context)
        legacy = {
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-a",
            "blocks": [
                {
                    "block_id": "WO-1",
                    "heading": "技术路线",
                    "must_answer": "写清技术路线",
                }
            ],
        }
        with mock.patch.dict(
            os.environ,
            {"BID_AGENT_CHAPTER_PLAN_V2_ENABLED": "0"},
            clear=False,
        ):
            saved = chat.save_writing_plan("ch-a", legacy)
        assert saved["writing_plan"] == legacy
        assert ControlStore(context).chapter_workspace("ch-a")["head_plan_revision"] == 0
