"""Chapter-scoped chat: history isolation and chapter-bound answers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.chapter_chat import CHAPTER_CHAT_DIR, ChapterChatService  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    DocumentMode,
)
import api.v3_app as v3_app  # noqa: E402
from api.settings_service import SettingsService  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
        ),
        BlueprintNode(
            chapter_id="ch-b",
            order=1,
            title="实施计划",
            purpose="说明实施与里程碑",
        ),
    ]


class ChapterChatServiceTests(unittest.TestCase):
    def test_histories_are_isolated_per_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter_service = ChapterWorkspaceService(context)
            chapter_a = chapter_service.get_chapter("ch-a")
            chapter_b = chapter_service.get_chapter("ch-b")
            chat = ChapterChatService(context)

            with mock.patch("llm_client.chat", side_effect=RuntimeError("offline")):
                result_a = chat.answer(
                    "ch-a",
                    "这一章写什么？",
                    chapter=chapter_a,
                )
                result_b = chat.answer(
                    "ch-b",
                    "里程碑怎么排？",
                    chapter=chapter_b,
                )

            self.assertEqual(result_a["chapter_id"], "ch-a")
            self.assertEqual(result_b["chapter_id"], "ch-b")
            self.assertIn("技术方案", result_a["reply"])
            self.assertIn("实施计划", result_b["reply"])

            history_a = chat.load_history("ch-a")
            history_b = chat.load_history("ch-b")
            self.assertEqual(len(history_a), 2)
            self.assertEqual(len(history_b), 2)
            self.assertEqual(history_a[0]["content"], "这一章写什么？")
            self.assertEqual(history_b[0]["content"], "里程碑怎么排？")
            self.assertNotEqual(history_a[0]["content"], history_b[0]["content"])

            path_a = context.root / CHAPTER_CHAT_DIR / "ch-a.jsonl"
            path_b = context.root / CHAPTER_CHAT_DIR / "ch-b.jsonl"
            self.assertTrue(path_a.is_file())
            self.assertTrue(path_b.is_file())
            self.assertNotEqual(path_a.read_text(encoding="utf-8"), path_b.read_text(encoding="utf-8"))

    def test_empty_message_rejected(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat = ChapterChatService(context)
            with self.assertRaises(ControlPlaneError) as raised:
                chat.answer("ch-a", "  ", chapter=chapter)
            self.assertEqual(raised.exception.code, "CHAT_MESSAGE_REQUIRED")


class ChapterChatApiTests(unittest.TestCase):
    def test_http_routes_keep_chapter_histories_separate(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            _seed_blueprint(context, _nodes())
            ControlStore(context).grant_workspace_access("ui-test", role="owner")
            settings = SettingsService(root)
            environment = {
                "BID_AGENT_AUTH_USER": "ui-test",
                "BID_AGENT_AUTH_PASSWORD": "ui-password",
                "BID_AGENT_AUTH_SECURE_COOKIE": "0",
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(v3_app, "RUNS_DIR", runs),
                mock.patch.object(v3_app, "SETTINGS", settings),
                mock.patch("llm_client.chat", side_effect=RuntimeError("offline")),
                TestClient(v3_app.app) as client,
            ):
                login = client.post(
                    "/api/auth/login",
                    json={"username": "ui-test", "password": "ui-password"},
                )
                self.assertEqual(login.status_code, 200)
                headers = {"X-CSRF-Token": client.cookies.get("bid_agent_csrf")}
                turn_a = client.post(
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/turn",
                    json={"message": "A 章问题"},
                    headers=headers,
                )
                turn_b = client.post(
                    "/api/v3/workspaces/alpha/chapters/ch-b/chat/turn",
                    json={"message": "B 章问题"},
                    headers=headers,
                )
                hist_a = client.get(
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history"
                )
                hist_b = client.get(
                    "/api/v3/workspaces/alpha/chapters/ch-b/chat/history"
                )

            self.assertEqual(turn_a.status_code, 200, turn_a.text)
            self.assertEqual(turn_b.status_code, 200, turn_b.text)
            self.assertTrue(turn_a.json()["ok"])
            self.assertEqual(turn_a.json()["chapter_id"], "ch-a")
            self.assertEqual(turn_b.json()["chapter_id"], "ch-b")

            self.assertEqual(hist_a.status_code, 200)
            self.assertEqual(hist_b.status_code, 200)
            turns_a = hist_a.json()["turns"]
            turns_b = hist_b.json()["turns"]
            self.assertEqual(turns_a[0]["content"], "A 章问题")
            self.assertEqual(turns_b[0]["content"], "B 章问题")
            self.assertEqual(len(turns_a), 2)
            self.assertEqual(len(turns_b), 2)

    def test_unknown_chapter_returns_error(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            _seed_blueprint(context, _nodes())
            ControlStore(context).grant_workspace_access("ui-test", role="owner")
            settings = SettingsService(root)
            environment = {
                "BID_AGENT_AUTH_USER": "ui-test",
                "BID_AGENT_AUTH_PASSWORD": "ui-password",
                "BID_AGENT_AUTH_SECURE_COOKIE": "0",
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(v3_app, "RUNS_DIR", runs),
                mock.patch.object(v3_app, "SETTINGS", settings),
                TestClient(v3_app.app) as client,
            ):
                login = client.post(
                    "/api/auth/login",
                    json={"username": "ui-test", "password": "ui-password"},
                )
                self.assertEqual(login.status_code, 200)
                response = client.get(
                    "/api/v3/workspaces/alpha/chapters/missing/chat/history"
                )
            self.assertIn(response.status_code, {404, 409, 400})
            body = response.json()
            self.assertFalse(body.get("ok", True))

    def test_routes_registered_on_public_app(self) -> None:
        paths = {getattr(route, "path", "") for route in v3_app.app.routes}
        self.assertIn(
            "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/history",
            paths,
        )
        self.assertIn(
            "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/turn",
            paths,
        )


if __name__ == "__main__":
    unittest.main()
