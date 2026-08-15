"""Phase 1: Chapter Workspace materialization, isolation, CAS, soft-delete."""

from __future__ import annotations

import json
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
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    DocumentMode,
)
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def _seed_blueprint(context: WorkspaceContext, nodes: list[BlueprintNode]) -> dict:
    """Install a promoted ChapterBlueprint for unit tests (control-plane fixture)."""
    blueprint = ChapterBlueprint(
        schema_version="v3",
        revision=1,
        source_hashes={},
        blueprint_id="bp-test",
        mode=DocumentMode.AUTO_OUTLINE,
        planning_model="score_direct",
        requirement_ledger_revision=1,
        score_model_revision=1,
        nodes=nodes,
        assignments=[],
    )
    payload = blueprint.model_dump(mode="json")
    artifact_hash = canonical_payload_hash(payload)
    proposal_id = f"prop-bp-{uuid.uuid4()}"
    proposal_hash = hashlib_sha(proposal_id + artifact_hash)
    now = "2026-07-30T00:00:00.000+00:00"
    store = ControlStore(context)
    with store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO v3_proposals(
                    proposal_id, workspace_id, artifact_kind, producer_role, operation_id,
                    base_revision, dependency_fingerprint, declared_dependencies_json,
                    proposal_hash, canonical_payload_hash, payload_json, cited_source_ids_json,
                    prompt_version, model_fingerprint, status, created_at
                ) VALUES (?, ?, 'ChapterBlueprint', 'planning_agent', ?, 0, 'fp-test', '[]',
                          ?, ?, ?, '[]', 'test', 'test', 'promoted', ?)
                """,
                (
                    proposal_id,
                    context.workspace_id,
                    f"op-bp-{uuid.uuid4()}",
                    proposal_hash,
                    artifact_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO v3_artifact_revisions(
                    artifact_kind, revision, artifact_id, artifact_hash, payload_json,
                    producer_role, dependency_fingerprint, proposal_id, proposal_hash, created_at
                ) VALUES ('ChapterBlueprint', 1, ?, ?, ?, 'planning_agent', 'fp-test', ?, ?, ?)
                """,
                (
                    f"ChapterBlueprint@1",
                    artifact_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    proposal_id,
                    proposal_hash,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO v3_active_artifacts(artifact_kind, artifact_id, revision, updated_at)
                VALUES ('ChapterBlueprint', ?, 1, ?)
                ON CONFLICT(artifact_kind) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                (f"ChapterBlueprint@1", now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    active = store.v3_active_artifact("ChapterBlueprint")
    assert active is not None
    return active


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _nodes() -> list[BlueprintNode]:
    return [
        BlueprintNode(
            chapter_id="ch-a",
            order=0,
            title="技术方案",
            purpose="说明总体技术路线",
        ),
        BlueprintNode(
            chapter_id="ch-b",
            order=1,
            title="实施计划",
            purpose="说明实施与里程碑",
        ),
    ]


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


class ChapterWorkspacePhase1Tests(unittest.TestCase):
    def test_parent_is_structural_and_only_leaf_is_writable(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(
                context,
                [
                    BlueprintNode(
                        chapter_id="parent",
                        order=0,
                        title="目标任务",
                        purpose="组织目标任务子章节",
                    ),
                    BlueprintNode(
                        chapter_id="leaf",
                        parent_chapter_id="parent",
                        order=1,
                        title="工作内容",
                        purpose="撰写具体工作内容",
                    ),
                ],
            )
            service = ChapterWorkspaceService(context)
            listed = {
                item["chapter_id"]: item
                for item in service.list_chapters()["items"]
            }

            self.assertFalse(listed["parent"]["is_leaf"])
            self.assertTrue(listed["leaf"]["is_leaf"])
            with self.assertRaises(ControlPlaneError) as blocked:
                service.require_leaf_chapter("parent")
            self.assertEqual(blocked.exception.code, "CHAPTER_BODY_REQUIRES_LEAF")
            self.assertEqual(
                service.require_leaf_chapter("leaf")["chapter_id"],
                "leaf",
            )

    def test_materialize_list_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            active = _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            listed = service.list_chapters()
            self.assertEqual(listed["total"], 2)
            self.assertEqual(listed["materialized"], 0)
            self.assertEqual(
                [item["status"] for item in listed["items"]],
                ["projected", "projected"],
            )

            created_a = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            self.assertEqual(created_a["chapter_id"], "ch-a")
            self.assertEqual(created_a["status"], "active")
            # Materialize + optional blueprint context seed both bump chapter_revision.
            self.assertGreaterEqual(created_a["chapter_revision"], 1)
            self.assertEqual(created_a["blueprint_revision"], int(active["revision"]))
            self.assertTrue(created_a["state_hash"])

            created_b = service.create(chapter_id="ch-b", expected_chapter_revision=0)
            hash_b_before = created_b["state_hash"]
            rev_b_before = created_b["chapter_revision"]

            updated_a = service.save_metadata(
                chapter_id="ch-a",
                expected_chapter_revision=created_a["chapter_revision"],
                metadata={"note": "A only"},
            )
            self.assertEqual(
                updated_a["chapter_revision"],
                created_a["chapter_revision"] + 1,
            )
            self.assertEqual(updated_a["metadata"]["note"], "A only")
            self.assertNotEqual(updated_a["state_hash"], created_a["state_hash"])

            b_after = service.store.chapter_workspace("ch-b")
            assert b_after is not None
            self.assertEqual(b_after["state_hash"], hash_b_before)
            self.assertEqual(b_after["chapter_revision"], rev_b_before)
            self.assertEqual(b_after.get("metadata") or {}, {})

    def test_ensure_all_materializes_projected_chapters(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            first = service.ensure_all(actor={"type": "test", "id": "tester"})
            self.assertEqual(first["created"], 2)
            self.assertGreaterEqual(first["seeded"], 1)
            listed = service.list_chapters()
            self.assertEqual(listed["materialized"], 2)
            self.assertTrue(all(item["materialized"] for item in listed["items"]))
            second = service.ensure_all()
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["unchanged"], 2)

    def test_ensure_all_skips_structural_parent_nodes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(
                context,
                [
                    BlueprintNode(
                        chapter_id="parent",
                        order=0,
                        title="目录节点",
                        purpose="组织下级章节",
                    ),
                    BlueprintNode(
                        chapter_id="leaf",
                        parent_chapter_id="parent",
                        order=1,
                        title="叶子章节",
                        purpose="撰写具体正文",
                    ),
                ],
            )
            service = ChapterWorkspaceService(context)

            summary = service.ensure_all()
            listed = {
                item["chapter_id"]: item
                for item in service.list_chapters()["items"]
            }

            self.assertEqual(summary["created"], 1)
            self.assertFalse(listed["parent"]["materialized"])
            self.assertTrue(listed["leaf"]["materialized"])

    def test_idempotent_create_and_cas_conflict(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            first = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            second = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            self.assertEqual(first["state_hash"], second["state_hash"])
            self.assertEqual(first["chapter_revision"], second["chapter_revision"])

            with self.assertRaises(ControlPlaneError) as conflict:
                service.save_metadata(
                    chapter_id="ch-a",
                    expected_chapter_revision=0,
                    metadata={"x": 1},
                )
            self.assertEqual(conflict.exception.code, "CHAPTER_REVISION_CONFLICT")
            self.assertEqual(conflict.exception.status_code, 409)

    def test_archive_is_soft_delete(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            created = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            archived = service.archive(
                chapter_id="ch-a",
                expected_chapter_revision=created["chapter_revision"],
            )
            self.assertEqual(archived["status"], "archived")
            self.assertEqual(archived["chapter_revision"], created["chapter_revision"] + 1)

            with self.assertRaises(ControlPlaneError) as blocked:
                service.save_metadata(
                    chapter_id="ch-a",
                    expected_chapter_revision=archived["chapter_revision"],
                    metadata={"after": True},
                )
            self.assertEqual(blocked.exception.code, "CHAPTER_ARCHIVED")

            listed = service.list_chapters(include_archived=True)
            statuses = {
                item["chapter_id"]: item["status"] for item in listed["items"]
            }
            self.assertEqual(statuses["ch-a"], "archived")
            self.assertEqual(statuses["ch-b"], "projected")

            # Blueprint nodes remain; archive does not destroy structure.
            detail = service.get_chapter("ch-b")
            self.assertEqual(detail["status"], "projected")
            self.assertIn("blueprint_node", detail)

    def test_path_safety_and_unknown_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            for bad in ("../escape", "a/b", "a\\b", "", ".", ".."):
                with self.subTest(bad=bad), self.assertRaises(ControlPlaneError) as err:
                    service.create(chapter_id=bad, expected_chapter_revision=0)
                self.assertIn(
                    err.exception.code,
                    {"CHAPTER_ID_INVALID", "CHAPTER_ID_REQUIRED", "CHAPTER_NOT_IN_BLUEPRINT"},
                )
            with self.assertRaises(ControlPlaneError) as missing:
                service.create(chapter_id="ch-missing", expected_chapter_revision=0)
            self.assertEqual(missing.exception.code, "CHAPTER_NOT_IN_BLUEPRINT")

    def test_command_gateway_handlers_and_workspace_revision_cas(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            store = ControlStore(context)
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())

            receipt = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.workspace.create",
                    payload={"chapter_id": "ch-a", "expected_chapter_revision": 0},
                )
            )
            self.assertEqual(receipt.status, "accepted")
            self.assertIsNotNone(receipt.result)
            assert receipt.result is not None
            self.assertEqual(receipt.result["chapter"]["chapter_id"], "ch-a")

            # Stale workspace expected_revision must fail closed.
            stale = CommandEnvelope.from_mapping(
                {
                    "command_id": str(uuid.uuid4()),
                    "kind": "chapter.workspace.create",
                    "payload": {
                        "chapter_id": "ch-b",
                        "expected_chapter_revision": 0,
                    },
                    "expected_revision": 0,
                    "idempotency_key": str(uuid.uuid4()),
                    "actor": {"type": "test", "id": "tester"},
                },
                workspace_id=context.workspace_id,
            )
            with self.assertRaises(ControlPlaneError) as conflict:
                gateway.submit(stale)
            self.assertEqual(conflict.exception.code, "REVISION_CONFLICT")

            chapter_rev = int(receipt.result["chapter"]["chapter_revision"])
            current = store.revision()
            meta = gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "command_id": str(uuid.uuid4()),
                        "kind": "chapter.workspace.save_metadata",
                        "payload": {
                            "chapter_id": "ch-a",
                            "expected_chapter_revision": chapter_rev,
                            "metadata": {"owner": "qa"},
                        },
                        "expected_revision": current,
                        "idempotency_key": str(uuid.uuid4()),
                        "actor": {"type": "test", "id": "tester"},
                    },
                    workspace_id=context.workspace_id,
                )
            )
            self.assertEqual(meta.status, "accepted")
            assert meta.result is not None
            self.assertEqual(meta.result["chapter"]["metadata"]["owner"], "qa")

            arch = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.workspace.archive",
                    payload={
                        "chapter_id": "ch-a",
                        "expected_chapter_revision": chapter_rev + 1,
                    },
                )
            )
            self.assertEqual(arch.status, "accepted")
            assert arch.result is not None
            self.assertEqual(arch.result["chapter"]["status"], "archived")

    def test_idempotency_key_replays_receipt(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            store = ControlStore(context)
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            key = "idem-chapter-create-a"
            first = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.workspace.create",
                    payload={"chapter_id": "ch-a", "expected_chapter_revision": 0},
                    key=key,
                )
            )
            # After first command, expected_revision is stale; idempotent replay must
            # still return the original accepted receipt without re-dispatch.
            second = gateway.submit(
                CommandEnvelope.from_mapping(
                    {
                        "command_id": str(uuid.uuid4()),
                        "kind": "chapter.workspace.create",
                        "payload": {
                            "chapter_id": "ch-a",
                            "expected_chapter_revision": 0,
                        },
                        "expected_revision": 0,
                        "idempotency_key": key,
                        "actor": {"type": "test", "id": "tester"},
                    },
                    workspace_id=context.workspace_id,
                )
            )
            self.assertEqual(first.command_id, second.command_id)
            self.assertEqual(first.status, "accepted")
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(first.operation_id, second.operation_id)

    def test_snapshot_includes_chapter_status(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            service.create(chapter_id="ch-a", expected_chapter_revision=0)
            snapshot = V3WorkspaceSnapshotBuilder(context).build()
            chapters = snapshot.get("chapters") or {}
            self.assertEqual(chapters.get("total"), 2)
            self.assertEqual(chapters.get("materialized"), 1)
            self.assertEqual(chapters.get("active"), 1)
            by_id = {
                item["chapter_id"]: item
                for item in chapters.get("items") or []
                if isinstance(item, dict)
            }
            self.assertEqual(by_id["ch-a"]["status"], "active")
            self.assertTrue(by_id["ch-a"]["state_hash"])
            self.assertEqual(by_id["ch-b"]["status"], "projected")

    def test_acl_denies_unlisted_principal_on_api_helpers(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            store = ControlStore(context)
            store.grant_workspace_access("owner", role="owner")
            store.grant_workspace_access("reader", role="viewer")
            self.assertEqual(
                store.require_workspace_access("owner", write=True)["role"],
                "owner",
            )
            self.assertEqual(
                store.require_workspace_access("reader", write=False)["role"],
                "viewer",
            )
            with self.assertRaises(ControlPlaneError) as viewer_write:
                store.require_workspace_access("reader", write=True)
            self.assertEqual(viewer_write.exception.code, "WORKSPACE_FORBIDDEN")
            with self.assertRaises(ControlPlaneError) as stranger:
                store.require_workspace_access("stranger", write=False)
            self.assertEqual(stranger.exception.code, "WORKSPACE_FORBIDDEN")

    def test_schema_creates_chapter_workspaces_table(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            store = ControlStore(context)
            with store._connection() as connection:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='chapter_workspaces'"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            # Migration path on existing db also succeeds.
            store._initialize()
            with store._connection() as connection:
                info = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(chapter_workspaces)"
                    ).fetchall()
                }
            self.assertIn("chapter_revision", info)
            self.assertIn("state_hash", info)


if __name__ == "__main__":
    unittest.main()
