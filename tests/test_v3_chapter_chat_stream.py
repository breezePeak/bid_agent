"""Chapter chat streams thinking + content deltas."""

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

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.chapter_chat import ChapterChatService  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    DocumentMode,
)


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_blueprint(context: WorkspaceContext) -> None:
    nodes = [
        BlueprintNode(
            chapter_id="ch-a",
            order=0,
            title="技术方案",
            purpose="说明总体技术路线",
        ),
    ]
    blueprint = ChapterBlueprint(
        schema_version="v3",
        revision=1,
        source_hashes={},
        blueprint_id="bp-chat-stream",
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
    now = "2026-08-11T00:00:00.000+00:00"
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


class ChapterChatStreamTests(unittest.TestCase):
    def test_iter_answer_events_streams_thinking_then_content(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapter = {
                "chapter_id": "ch-a",
                "title": "技术方案",
                "blueprint_node": {
                    "chapter_id": "ch-a",
                    "title": "技术方案",
                    "purpose": "说明总体技术路线",
                },
                "context": {"items": []},
                "is_leaf": True,
            }
            service = ChapterChatService(context)

            def fake_stream(messages, temperature=0.2):
                yield ("reasoning", "先看目录位置与评分要求。")
                yield ("reasoning", "再给可执行建议。")
                yield ("content", "建议强调阶段划分")
                yield ("content", "与质控节点。")

            with mock.patch("llm_client.chat_stream_chunks", side_effect=fake_stream):
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "这一章怎么写？",
                        chapter=chapter,
                    )
                )

            types = [item["type"] for item in events]
            self.assertEqual(types[0], "meta")
            self.assertIn("thinking_delta", types)
            self.assertIn("content_delta", types)
            self.assertEqual(types[-1], "done")
            thinking = "".join(
                item["delta"] for item in events if item["type"] == "thinking_delta"
            )
            content = "".join(
                item["delta"] for item in events if item["type"] == "content_delta"
            )
            self.assertIn("目录位置", thinking)
            self.assertIn("阶段划分", content)
            done = events[-1]
            self.assertEqual(done["thinking"], thinking)
            self.assertEqual(done["reply"], content)

            history = service.load_history("ch-a")
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")
            self.assertEqual(history[1]["thinking"], thinking)
            self.assertEqual(history[1]["content"], content)


if __name__ == "__main__":
    unittest.main()
