"""Phases 3–5 and 8: content revisions, locks, H2 approval, formal compose."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


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
from document_pipeline.chapter_editing import (  # noqa: E402
    ChapterEditingService,
    merge_ai_blocks_with_locks,
    split_text_into_blocks,
)
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    ContentBlock,
    DocumentMode,
)
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_blueprint(
    context: WorkspaceContext,
    nodes: list[BlueprintNode] | None = None,
) -> None:
    nodes = nodes or [
        BlueprintNode(
            chapter_id="ch-a",
            order=0,
            title="技术方案",
            purpose="说明总体技术路线",
            writing_objectives=["覆盖架构"],
            score_point_ids=["SP-1"],
        ),
        BlueprintNode(
            chapter_id="ch-b",
            order=1,
            title="实施计划",
            purpose="说明实施与里程碑",
        ),
    ]
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
    proposal_hash = _sha(proposal_id + artifact_hash)
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


def _envelope(context, store, kind, payload):
    command_id = str(uuid.uuid4())
    return CommandEnvelope.from_mapping(
        {
            "command_id": command_id,
            "kind": kind,
            "payload": payload,
            "expected_revision": store.revision(),
            "idempotency_key": command_id,
            "actor": {"type": "user", "id": "owner"},
        },
        workspace_id=context.workspace_id,
    )


class ChapterContentPhasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._grounding_patch = mock.patch.object(
            ChapterEditingService,
            "_evaluate_grounding",
            side_effect=lambda **kwargs: {
                "verdict": "pass",
                "global_context_id": "PM-TEST",
                "global_context_revision": 1,
                "global_context_hash": "g" * 64,
                "chapter_context_id": (
                    f"chapter-context:{kwargs.get('chapter_id') or 'unknown'}"
                ),
                "chapter_context_revision": 0,
                "chapter_context_hash": "c" * 64,
                "paragraph_fact_bindings": {},
            },
        )
        self._grounding_patch.start()

    def tearDown(self) -> None:
        self._grounding_patch.stop()

    def test_only_leaf_chapter_can_write_and_parent_does_not_block_compose(self) -> None:
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
            chapters = ChapterWorkspaceService(context)
            editing = ChapterEditingService(context)
            parent = chapters.create(
                chapter_id="parent",
                expected_chapter_revision=0,
            )
            leaf = chapters.create(
                chapter_id="leaf",
                expected_chapter_revision=0,
            )

            with self.assertRaises(ControlPlaneError) as blocked:
                editing.generate_draft(
                    chapter_id="parent",
                    expected_chapter_revision=parent["chapter_revision"],
                    text="父节点不应生成正文",
                )
            self.assertEqual(blocked.exception.code, "CHAPTER_BODY_REQUIRES_LEAF")

            with mock.patch.object(
                ChapterEditingService,
                "_confirmation_required",
                return_value=False,
            ):
                editing.generate_draft(
                    chapter_id="leaf",
                    expected_chapter_revision=leaf["chapter_revision"],
                    text="叶子章节正文",
                )
            composed = editing.compose_formal_document()
            self.assertTrue(composed["export_allowed"])
            self.assertEqual(
                [item["chapter_id"] for item in composed["chapter_manifest"]],
                ["leaf"],
            )

    def test_content_block_legacy_defaults_to_ai_generated(self) -> None:
        block = ContentBlock(
            block_id="b1",
            target_node_id="ch-a",
            type="paragraph",
            content="hello",
            confidence=0.5,
        )
        self.assertEqual(block.source, "AI_GENERATED")
        self.assertEqual(block.lock_state, "UNLOCKED")

    def test_split_text_and_block_ops_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapters = ChapterWorkspaceService(context)
            editing = ChapterEditingService(context)
            a = chapters.create(chapter_id="ch-a", expected_chapter_revision=0)
            b = chapters.create(chapter_id="ch-b", expected_chapter_revision=0)
            b_hash = b["state_hash"]

            parts = split_text_into_blocks(
                "第一段。\n\n- 列表项\n\n| a | b |\n| 1 | 2 |",
                chapter_id="ch-a",
            )
            self.assertGreaterEqual(len(parts), 2)
            self.assertEqual(parts[1]["type"], "list")

            draft = editing.generate_draft(
                chapter_id="ch-a",
                expected_chapter_revision=a["chapter_revision"],
                text="AI 段落一\n\nAI 段落二",
                actor={"type": "user", "id": "owner"},
            )
            self.assertEqual(draft["content"]["source"] in {"ai_draft", "merge"}, True)
            self.assertEqual(len(draft["content"]["blocks"]), 2)

            edited = editing.apply_operations(
                chapter_id="ch-a",
                expected_chapter_revision=draft["chapter"]["chapter_revision"],
                operations=[
                    {
                        "op": "update",
                        "block_id": draft["content"]["blocks"][0]["block_id"],
                        "content": "用户改写后的段落",
                    },
                    {
                        "op": "insert",
                        "index": 1,
                        "block": {
                            "type": "paragraph",
                            "content": "用户新增段落",
                        },
                    },
                ],
                actor={"type": "user", "id": "owner"},
            )
            blocks = edited["content"]["blocks"]
            locked = [item for item in blocks if item.get("human_locked")]
            self.assertGreaterEqual(len(locked), 2)
            self.assertTrue(
                any(item.get("source") in {"USER_EDITED", "USER_CREATED"} for item in blocks)
            )

            b_after = ControlStore(context).chapter_workspace("ch-b")
            assert b_after is not None
            self.assertEqual(b_after["state_hash"], b_hash)

    def test_lock_merge_preserves_user_locked_blocks(self) -> None:
        existing = [
            {
                "block_id": "locked-1",
                "content": "用户锁定",
                "lock_state": "USER_LOCKED",
                "human_locked": True,
                "order": 0,
            },
            {
                "block_id": "ai-1",
                "content": "旧 AI",
                "lock_state": "UNLOCKED",
                "human_locked": False,
                "order": 1,
            },
        ]
        incoming = [
            {
                "block_id": "ai-new-1",
                "content": "新 AI 1",
                "lock_state": "UNLOCKED",
                "human_locked": False,
                "order": 0,
            },
            {
                "block_id": "ai-new-2",
                "content": "新 AI 2",
                "lock_state": "UNLOCKED",
                "human_locked": False,
                "order": 1,
            },
        ]
        merged = merge_ai_blocks_with_locks(
            existing=existing,
            incoming=incoming,
            overwrite_locked=False,
        )
        self.assertEqual(merged[0]["block_id"], "locked-1")
        self.assertEqual(merged[0]["content"], "用户锁定")
        self.assertEqual(len(merged), 3)

        overwritten = merge_ai_blocks_with_locks(
            existing=existing,
            incoming=incoming,
            overwrite_locked=True,
        )
        self.assertEqual([item["block_id"] for item in overwritten], ["ai-new-1", "ai-new-2"])

    def test_restore_creates_new_head(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapters = ChapterWorkspaceService(context)
            editing = ChapterEditingService(context)
            created = chapters.create(chapter_id="ch-a", expected_chapter_revision=0)
            first = editing.generate_draft(
                chapter_id="ch-a",
                expected_chapter_revision=created["chapter_revision"],
                text="版本一",
            )
            second = editing.generate_draft(
                chapter_id="ch-a",
                expected_chapter_revision=first["chapter"]["chapter_revision"],
                text="版本二",
                overwrite_locked=True,
            )
            restored = editing.restore_revision(
                chapter_id="ch-a",
                expected_chapter_revision=second["chapter"]["chapter_revision"],
                from_content_revision=first["content"]["content_revision"],
            )
            self.assertEqual(
                restored["content"]["content_hash"],
                first["content"]["content_hash"],
            )
            self.assertGreater(
                restored["content"]["content_revision"],
                second["content"]["content_revision"],
            )
            self.assertEqual(restored["content"]["source"], "restore")

    def test_h2_confirm_and_auto_approve_paths(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapters = ChapterWorkspaceService(context)
            editing = ChapterEditingService(context)
            created = chapters.create(chapter_id="ch-a", expected_chapter_revision=0)

            with mock.patch.object(
                ChapterEditingService,
                "_confirmation_required",
                return_value=True,
            ):
                draft = editing.generate_draft(
                    chapter_id="ch-a",
                    expected_chapter_revision=created["chapter_revision"],
                    text="需要人工确认",
                )
                self.assertEqual(draft["chapter"]["formal_content_revision"], 0)
                self.assertEqual(draft["chapter"]["approval_status"], "draft")
                confirmed = editing.confirm_approval(
                    chapter_id="ch-a",
                    expected_chapter_revision=draft["chapter"]["chapter_revision"],
                    content_revision=draft["content"]["content_revision"],
                    content_hash=draft["content"]["content_hash"],
                    actor={"type": "user", "id": "owner"},
                )
                self.assertEqual(confirmed["approval"]["decision"], "approved")
                self.assertEqual(
                    confirmed["chapter"]["formal_content_revision"],
                    draft["content"]["content_revision"],
                )
                self.assertEqual(confirmed["chapter"]["approval_status"], "approved")

            created_b = chapters.create(chapter_id="ch-b", expected_chapter_revision=0)
            with mock.patch.object(
                ChapterEditingService,
                "_confirmation_required",
                return_value=False,
            ):
                auto = editing.generate_draft(
                    chapter_id="ch-b",
                    expected_chapter_revision=created_b["chapter_revision"],
                    text="自动正式",
                )
                self.assertEqual(auto["approval"]["decision"], "auto_approved")
                self.assertEqual(
                    auto["chapter"]["formal_content_revision"],
                    auto["content"]["content_revision"],
                )
                # Auto mode must not look like human confirm.
                self.assertEqual(auto["approval"]["principal_id"], "system")
                self.assertEqual(auto["chapter"]["approval_status"], "approved")

    def test_compose_blocks_pending_and_export_gate(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapters = ChapterWorkspaceService(context)
            editing = ChapterEditingService(context)
            a = chapters.create(chapter_id="ch-a", expected_chapter_revision=0)
            chapters.create(chapter_id="ch-b", expected_chapter_revision=0)
            with mock.patch.object(
                ChapterEditingService,
                "_confirmation_required",
                return_value=True,
            ):
                draft = editing.generate_draft(
                    chapter_id="ch-a",
                    expected_chapter_revision=a["chapter_revision"],
                    text="仅 A 有草稿",
                )
                composed = editing.compose_formal_document()
                self.assertFalse(composed["export_allowed"])
                self.assertTrue(composed["pending_chapters"])

                editing.confirm_approval(
                    chapter_id="ch-a",
                    expected_chapter_revision=draft["chapter"]["chapter_revision"],
                    content_revision=draft["content"]["content_revision"],
                    content_hash=draft["content"]["content_hash"],
                    actor={"type": "user", "id": "owner"},
                )
                # B still pending
                composed2 = editing.compose_formal_document()
                self.assertFalse(composed2["export_allowed"])
                self.assertTrue(
                    any(item["chapter_id"] == "ch-b" for item in composed2["pending_chapters"])
                )

                b = ControlStore(context).chapter_workspace("ch-b")
                assert b is not None
                draft_b = editing.generate_draft(
                    chapter_id="ch-b",
                    expected_chapter_revision=b["chapter_revision"],
                    text="B 草稿",
                )
                editing.confirm_approval(
                    chapter_id="ch-b",
                    expected_chapter_revision=draft_b["chapter"]["chapter_revision"],
                    content_revision=draft_b["content"]["content_revision"],
                    content_hash=draft_b["content"]["content_hash"],
                    actor={"type": "user", "id": "owner"},
                )
                final = editing.compose_formal_document()
                self.assertTrue(final["export_allowed"])
                self.assertEqual(len(final["chapter_manifest"]), 2)
                self.assertGreaterEqual(len(final["blocks"]), 2)
                self.assertEqual(final["mode"], "formal")

    def test_command_gateway_content_and_approval(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            store = ControlStore(context)
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            create = gateway.submit(
                _envelope(
                    context,
                    store,
                    "chapter.workspace.create",
                    {"chapter_id": "ch-a", "expected_chapter_revision": 0},
                )
            )
            self.assertEqual(create.status, "accepted")
            chapter_rev = int(create.result["chapter"]["chapter_revision"])
            with mock.patch.object(
                ChapterEditingService,
                "_confirmation_required",
                return_value=True,
            ):
                draft = gateway.submit(
                    _envelope(
                        context,
                        store,
                        "chapter.generate_draft",
                        {
                            "chapter_id": "ch-a",
                            "expected_chapter_revision": chapter_rev,
                            "text": "网关草稿",
                            "global_context_id": "PM-TEST",
                            "global_context_revision": 1,
                            "global_context_hash": "g" * 64,
                            "chapter_context_id": "chapter-context:ch-a",
                            "chapter_context_revision": 0,
                            "chapter_context_hash": "c" * 64,
                        },
                    )
                )
                self.assertEqual(draft.status, "accepted")
                content = draft.result["content"]
                approve = gateway.submit(
                    _envelope(
                        context,
                        store,
                        "chapter.approval.confirm",
                        {
                            "chapter_id": "ch-a",
                            "expected_chapter_revision": draft.result["chapter"][
                                "chapter_revision"
                            ],
                            "content_revision": content["content_revision"],
                            "content_hash": content["content_hash"],
                        },
                    )
                )
                self.assertEqual(approve.status, "accepted")
                self.assertEqual(
                    approve.result["chapter"]["formal_content_revision"],
                    content["content_revision"],
                )

    def test_auto_approve_rejected_when_confirmation_required(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            store = ControlStore(context)
            with self.assertRaises(ControlPlaneError) as err:
                store.record_chapter_approval_receipt(
                    chapter_id="ch-x",
                    content_revision=1,
                    content_hash="abc",
                    decision="auto_approved",
                    principal_id="system",
                    confirmation_required=True,
                )
            self.assertEqual(err.exception.code, "CHAPTER_APPROVAL_INVALID")


if __name__ == "__main__":
    unittest.main()
