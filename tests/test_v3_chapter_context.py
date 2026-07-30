"""Phase 2: Chapter Context seed, append-only revisions, isolation, CAS."""

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


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_blueprint(context: WorkspaceContext, nodes: list[BlueprintNode]) -> dict:
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
                    "ChapterBlueprint@1",
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
                ("ChapterBlueprint@1", now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    active = store.v3_active_artifact("ChapterBlueprint")
    assert active is not None
    return active


def _nodes() -> list[BlueprintNode]:
    return [
        BlueprintNode(
            chapter_id="ch-a",
            order=0,
            title="技术方案",
            purpose="说明总体技术路线",
            writing_objectives=["覆盖架构", "覆盖安全"],
            score_point_ids=["SP-1"],
            score_condition_ids=["SC-1"],
            requirement_ids=["REQ-1"],
            required_mentions=["交付周期"],
        ),
        BlueprintNode(
            chapter_id="ch-b",
            order=1,
            title="实施计划",
            purpose="说明实施与里程碑",
            score_point_ids=["SP-2"],
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


class ChapterContextPhase2Tests(unittest.TestCase):
    def test_blueprint_seed_once_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            created = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            self.assertEqual(created["head_context_revision"], 1)
            head = service.store.chapter_context_head("ch-a")
            assert head is not None
            self.assertTrue(head["seeded_from_blueprint"])
            kinds = {item["kind"] for item in head["items"]}
            self.assertIn("GOAL", kinds)
            self.assertIn("SCORING_REQUIREMENT", kinds)
            self.assertIn("KEY_FACT", kinds)
            seed_hash = head["content_hash"]
            seed_items = list(head["items"])

            # User edits context.
            user_items = [
                {
                    "item_id": "user:goal:1",
                    "kind": "GOAL",
                    "title": "用户目标",
                    "body": "强调自主可控",
                    "order": 0,
                    "source": "USER",
                },
                {
                    "item_id": "user:scoring:1",
                    "kind": "SCORING_REQUIREMENT",
                    "title": "得分要点",
                    "body": "满分条件逐条响应",
                    "order": 1,
                    "source": "USER",
                },
            ]
            saved = service.save_context(
                chapter_id="ch-a",
                expected_chapter_revision=created["chapter_revision"],
                items=user_items,
            )
            self.assertFalse(saved["unchanged"])
            self.assertEqual(saved["context"]["context_revision"], 2)
            self.assertFalse(saved["context"]["seeded_from_blueprint"])
            self.assertEqual(len(saved["context"]["items"]), 2)
            self.assertNotEqual(saved["context"]["content_hash"], seed_hash)

            # Re-materialize / create must not re-seed over user context.
            again = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            self.assertEqual(again["head_context_revision"], 2)
            head_after = service.store.chapter_context_head("ch-a")
            assert head_after is not None
            self.assertEqual(head_after["content_hash"], saved["context"]["content_hash"])
            self.assertEqual(
                [item["item_id"] for item in head_after["items"]],
                ["user:goal:1", "user:scoring:1"],
            )
            # Historical seed revision remains append-only.
            rev1 = service.get_context_revision("ch-a", 1)
            self.assertEqual(rev1["content_hash"], seed_hash)
            self.assertEqual(
                [item["item_id"] for item in rev1["items"]],
                [item["item_id"] for item in seed_items],
            )

    def test_context_change_only_stales_current_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            a = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            b = service.create(chapter_id="ch-b", expected_chapter_revision=0)
            b_hash = b["state_hash"]
            b_rev = b["chapter_revision"]
            b_context = service.store.chapter_context_head("ch-b")
            assert b_context is not None
            b_context_hash = b_context["content_hash"]

            service.save_context(
                chapter_id="ch-a",
                expected_chapter_revision=a["chapter_revision"],
                items=[
                    {
                        "item_id": "user:a",
                        "kind": "TECHNICAL_CONSTRAINT",
                        "title": "约束",
                        "body": "仅 A",
                        "order": 0,
                        "source": "USER",
                    }
                ],
            )
            b_after = service.store.chapter_workspace("ch-b")
            assert b_after is not None
            self.assertEqual(b_after["state_hash"], b_hash)
            self.assertEqual(b_after["chapter_revision"], b_rev)
            self.assertEqual(b_after["head_context_revision"], b["head_context_revision"])
            b_context_after = service.store.chapter_context_head("ch-b")
            assert b_context_after is not None
            self.assertEqual(b_context_after["content_hash"], b_context_hash)

    def test_append_only_history_and_identical_save_noop(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            created = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            items = [
                {
                    "item_id": "user:1",
                    "kind": "GOAL",
                    "title": "G",
                    "body": "body",
                    "order": 0,
                    "source": "USER",
                }
            ]
            first = service.save_context(
                chapter_id="ch-a",
                expected_chapter_revision=created["chapter_revision"],
                items=items,
            )
            second = service.save_context(
                chapter_id="ch-a",
                expected_chapter_revision=first["chapter"]["chapter_revision"],
                items=items,
            )
            self.assertTrue(second["unchanged"])
            self.assertEqual(
                second["context"]["context_revision"],
                first["context"]["context_revision"],
            )
            self.assertEqual(
                second["chapter"]["chapter_revision"],
                first["chapter"]["chapter_revision"],
            )
            listed = service.list_context_revisions("ch-a")
            # seed + first user save (identical re-save does not append)
            self.assertEqual(listed["head_context_revision"], 2)
            self.assertGreaterEqual(len(listed["revisions"]), 2)
            revisions = [item["context_revision"] for item in listed["revisions"]]
            self.assertEqual(revisions, sorted(revisions, reverse=True))

    def test_context_cas_conflict_and_invalid_kind(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            created = service.create(chapter_id="ch-a", expected_chapter_revision=0)
            with self.assertRaises(ControlPlaneError) as conflict:
                service.save_context(
                    chapter_id="ch-a",
                    expected_chapter_revision=0,
                    items=[
                        {
                            "item_id": "u1",
                            "kind": "GOAL",
                            "title": "t",
                            "body": "b",
                            "order": 0,
                            "source": "USER",
                        }
                    ],
                )
            self.assertEqual(conflict.exception.code, "CHAPTER_REVISION_CONFLICT")

            with self.assertRaises(ControlPlaneError) as invalid:
                service.save_context(
                    chapter_id="ch-a",
                    expected_chapter_revision=created["chapter_revision"],
                    items=[
                        {
                            "item_id": "u1",
                            "kind": "UNKNOWN",
                            "title": "t",
                            "body": "b",
                            "order": 0,
                            "source": "USER",
                        }
                    ],
                )
            self.assertEqual(invalid.exception.code, "CHAPTER_CONTEXT_INVALID")

    def test_command_gateway_context_save(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            store = ControlStore(context)
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            created = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.workspace.create",
                    payload={"chapter_id": "ch-a", "expected_chapter_revision": 0},
                )
            )
            self.assertEqual(created.status, "accepted")
            assert created.result is not None
            chapter_rev = int(created.result["chapter"]["chapter_revision"])
            receipt = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.context.save",
                    payload={
                        "chapter_id": "ch-a",
                        "expected_chapter_revision": chapter_rev,
                        "items": [
                            {
                                "item_id": "user:goal",
                                "kind": "GOAL",
                                "title": "目标",
                                "body": "可核验",
                                "order": 0,
                                "source": "USER",
                            }
                        ],
                    },
                )
            )
            self.assertEqual(receipt.status, "accepted")
            assert receipt.result is not None
            self.assertEqual(receipt.result["context"]["context_revision"], 2)
            self.assertEqual(receipt.result["context"]["items"][0]["kind"], "GOAL")

    def test_get_chapter_includes_head_context(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            service = ChapterWorkspaceService(context)
            service.create(chapter_id="ch-a", expected_chapter_revision=0)
            detail = service.get_chapter("ch-a")
            self.assertTrue(detail["materialized"])
            self.assertIsNotNone(detail.get("context"))
            assert detail["context"] is not None
            self.assertGreaterEqual(len(detail["context"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
