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
from document_pipeline.chapter_chat import (  # noqa: E402
    CHAPTER_CHAT_DIR,
    ChapterChatService,
    _requests_document_write,
)
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
    def test_rewrite_request_routes_to_document_writer(self) -> None:
        self.assertTrue(
            _requests_document_write(
                "当前草稿完全错了，需要重新理解需求，重新写",
                {"write_phase": "write_body"},
            )
        )

    def test_short_colloquial_edit_request_routes_to_document_writer(self) -> None:
        self.assertTrue(
            _requests_document_write("改正文啊", {"write_phase": "write_body"})
        )
        self.assertTrue(
            _requests_document_write(
                "把第2段改成真正的项目背景",
                {"write_phase": "write_body"},
            )
        )

    def test_question_about_existing_copy_does_not_route_to_writer(self) -> None:
        self.assertFalse(
            _requests_document_write(
                "第2段写的是项目背景吗？",
                {"write_phase": "write_body"},
            )
        )

    def test_histories_are_isolated_per_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter_service = ChapterWorkspaceService(context)
            chapter_a = chapter_service.get_chapter("ch-a")
            chapter_b = chapter_service.get_chapter("ch-b")
            chat = ChapterChatService(context)

            with mock.patch(
                "llm_client.chat_with_meta",
                side_effect=RuntimeError("offline"),
            ):
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
            self.assertTrue(history_a[0].get("turn_id"))
            self.assertTrue(history_a[1].get("turn_id"))

            path_a = context.root / CHAPTER_CHAT_DIR / "ch-a.jsonl"
            path_b = context.root / CHAPTER_CHAT_DIR / "ch-b.jsonl"
            self.assertTrue(path_a.is_file())
            self.assertTrue(path_b.is_file())
            self.assertNotEqual(path_a.read_text(encoding="utf-8"), path_b.read_text(encoding="utf-8"))

    def test_history_turns_can_be_edited_in_place(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat = ChapterChatService(context)
            with mock.patch(
                "llm_client.chat_with_meta",
                side_effect=RuntimeError("offline"),
            ):
                chat.answer("ch-a", "初稿怎么写？", chapter=chapter)
            history = chat.load_history("ch-a")
            user_id = history[0]["turn_id"]
            assistant_id = history[1]["turn_id"]
            chat.update_turn(
                "ch-a",
                turn_id=user_id,
                content="改成：实施方案怎么写？",
            )
            chat.update_turn(
                "ch-a",
                turn_id=assistant_id,
                content="先写阶段划分。",
                thinking="用户改了问题，回复也一起改。",
            )
            updated = chat.load_history("ch-a")
            self.assertEqual(updated[0]["content"], "改成：实施方案怎么写？")
            self.assertEqual(updated[1]["content"], "先写阶段划分。")
            self.assertEqual(updated[1]["thinking"], "用户改了问题，回复也一起改。")

    def test_history_turn_can_be_permanently_deleted(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chat = ChapterChatService(context)
            first = chat.append_turn("ch-a", role="user", content="保留这条")
            removed = chat.append_turn("ch-a", role="assistant", content="删除这条")
            chat.delete_turn("ch-a", turn_id=removed["turn_id"])
            history = chat.load_history("ch-a")
            self.assertEqual([item["turn_id"] for item in history], [first["turn_id"]])

    def test_history_can_be_cleared_for_one_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chat = ChapterChatService(context)
            chat.append_turn("ch-a", role="user", content="清空我")
            chat.append_turn("ch-a", role="assistant", content="已记录")
            chat.append_turn("ch-b", role="user", content="保留我")

            self.assertEqual(chat.clear_history("ch-a"), 2)
            self.assertEqual(chat.load_history("ch-a"), [])
            self.assertEqual(chat.load_history("ch-b")[0]["content"], "保留我")

    def test_human_review_lists_outline_before_writing(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat = ChapterChatService(context)
            chat.set_authority(mode="human_review", chapter_id="ch-a")
            with mock.patch("llm_client.chat_with_meta") as writer:
                first = chat.answer("ch-a", "这一章怎么写？", chapter=chapter)
                self.assertIn("准备这样写", first["reply"])
                self.assertIn("确认", first["reply"])
                writer.return_value = {
                    "content": "总体技术路线分四步实施。",
                    "reasoning": "",
                }
                second = chat.answer("ch-a", "确认", chapter=chapter)
            self.assertTrue(second["document_write_requested"])
            self.assertIn("中间文档", second["reply"])

    def test_full_authority_skips_review_wait(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat = ChapterChatService(context)
            chat.set_authority(mode="full_authority", chapter_id="ch-a")
            with mock.patch(
                "llm_client.chat_with_meta",
                return_value={"content": "总体技术路线分四步实施。", "reasoning": ""},
            ):
                result = chat.answer("ch-a", "写正文", chapter=chapter)
            self.assertTrue(result["document_write_requested"])
            self.assertIn("中间文档", result["reply"])

    def test_chat_prompt_identifies_as_chapter_writer(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat = ChapterChatService(context)
            chat_context = chat.build_chapter_chat_context(chapter)
            messages = ChapterChatService._build_messages(
                chat_context,
                [],
                "这一章怎么写？",
            )
            system = messages[0]["content"]
            payload = json.loads(messages[1]["content"])
            self.assertIn("写作 Agent", system)
            self.assertIn("禁止反问", system)
            self.assertNotIn("只能给出写作建议", system)
            self.assertEqual(payload["role"], "bid_chapter_writer")
            self.assertIn("writing_outline", payload)
            self.assertIn("忠实回答", payload["instruction"])
            self.assertIn("不得声称已经修改", payload["instruction"])

    def test_chat_context_keeps_canonical_project_facts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-a")
            chat_context = ChapterChatService(context).build_chapter_chat_context(
                chapter,
                global_project_context={
                    "global_context_revision": 3,
                    "identity": {"project_name": "全国调查项目", "buyer": "采购单位"},
                    "background": ["开展年度国土变更调查"],
                    "scope": ["全国县级调查成果核查"],
                    "confirmed_facts": [
                        {
                            "fact_id": "F-1",
                            "statement": "项目包含国家级内业核查。",
                            "source_ids": ["SRC-1"],
                        }
                    ],
                },
            )
            shared = chat_context["shared_project_facts"]
            self.assertEqual(shared["project_name"], "全国调查项目")
            self.assertEqual(shared["background"], ["开展年度国土变更调查"])
            self.assertEqual(shared["scope"], ["全国县级调查成果核查"])
            self.assertEqual(
                shared["confirmed_facts"][0]["statement"],
                "项目包含国家级内业核查。",
            )

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
                mock.patch(
                    "llm_client.chat_with_meta",
                    side_effect=RuntimeError("offline"),
                ),
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
                edited = client.put(
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history",
                    json={
                        "turn_id": turns_a[0]["turn_id"],
                        "content": "A 章已改问题",
                    },
                    headers=headers,
                )
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(edited.json()["turn"]["content"], "A 章已改问题")
                hist_a_again = client.get(
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history"
                )
                self.assertEqual(hist_a_again.json()["turns"][0]["content"], "A 章已改问题")
                deleted = client.request(
                    "DELETE",
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history",
                    json={"turn_id": turns_a[1]["turn_id"]},
                    headers=headers,
                )
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertTrue(deleted.json()["ok"])
                hist_a_deleted = client.get(
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history"
                )
                self.assertEqual(len(hist_a_deleted.json()["turns"]), 1)
                cleared = client.request(
                    "DELETE",
                    "/api/v3/workspaces/alpha/chapters/ch-a/chat/history",
                    json={"clear_all": True},
                    headers=headers,
                )
                self.assertEqual(cleared.status_code, 200, cleared.text)
                self.assertEqual(cleared.json()["deleted_count"], 1)
                self.assertEqual(
                    client.get(
                        "/api/v3/workspaces/alpha/chapters/ch-a/chat/history"
                    ).json()["turns"],
                    [],
                )
                hist_b_again = client.get(
                    "/api/v3/workspaces/alpha/chapters/ch-b/chat/history"
                )
                self.assertEqual(hist_b_again.json()["turns"][0]["content"], "B 章问题")

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
