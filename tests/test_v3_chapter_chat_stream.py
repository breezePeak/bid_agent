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
    def test_agent_execution_record_survives_history_reload_and_edit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            service = ChapterChatService(context)
            saved = service.append_turn(
                "ch-a",
                role="assistant",
                content="草稿未完成：模型连接中断。",
                thinking="已完成资料检查，开始生成正文。",
                research_steps=[
                    {
                        "status": "published",
                        "message": "已获得 2 条公开来源。",
                        "queries": ["项目建设依据"],
                        "sources": [{"title": "公开来源"}],
                    }
                ],
                elapsed_seconds=18,
                operation_id="draft-ch-a-1",
                status="failed",
            )

            history = service.load_history("ch-a", limit=0)
            self.assertEqual(history[0]["research_steps"][0]["status"], "published")
            self.assertEqual(history[0]["elapsed_seconds"], 18)
            self.assertEqual(history[0]["operation_id"], "draft-ch-a-1")
            self.assertEqual(history[0]["status"], "failed")

            service.update_turn(
                "ch-a",
                turn_id=saved["turn_id"],
                role="assistant",
                content="草稿失败现场已保留。",
            )
            reloaded = service.load_history("ch-a", limit=0)[0]
            self.assertEqual(reloaded["research_steps"][0]["status"], "published")
            self.assertEqual(reloaded["operation_id"], "draft-ch-a-1")

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
                payload = json.loads(messages[1]["content"])
                self.assertEqual(payload["role"], "bid_chapter_writer")
                self.assertEqual(payload["inspected_chapters"], [])
                yield ("reasoning", "先看目录位置与评分要求。")
                yield ("reasoning", "再给可执行建议。")
                yield ("content", "建议强调阶段划分")
                yield ("content", "与质控节点。")

            with (
                mock.patch(
                    "document_pipeline.chapter_chat._decide_chapter_action",
                    return_value={
                        "action": "respond_only",
                        "reason": "用户询问写法",
                        "writing_instruction": "这一章怎么写？",
                        "source": "chapter_agent",
                    },
                ),
                mock.patch(
                    "document_pipeline.document_outline_context.DocumentOutlineContextService.plan_and_load_inspections",
                    return_value={
                        "inspect_ids": [],
                        "reason": "标题树足够",
                        "views": [],
                        "decision_source": "chapter_agent",
                    },
                ),
                mock.patch("llm_client.chat_stream_chunks", side_effect=fake_stream),
            ):
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "这一章怎么写？",
                        chapter=chapter,
                    )
                )

            types = [item["type"] for item in events]
            self.assertEqual(types[0], "meta")
            self.assertNotIn("inspect_planning", types)
            self.assertNotIn("inspect_skipped", types)
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
            # Do not render a generic directory-inspection preamble on every
            # turn; only actual cross-chapter reads are surfaced to the user.
            self.assertNotIn("先看目录标题", done["thinking"])
            self.assertNotIn("标题树足够", done["thinking"])
            self.assertIn(thinking, done["thinking"])
            self.assertEqual(done["reply"], content)

            history = service.load_history("ch-a")
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")
            self.assertEqual(history[1]["thinking"], done["thinking"])
            self.assertEqual(history[1]["content"], content)

    def test_confirmed_outline_routes_body_to_document_instead_of_chat(self) -> None:
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
            list(service.iter_answer_events("ch-a", "先列提纲", chapter=chapter))

            with mock.patch("llm_client.chat_stream_chunks") as writer:
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "按这个写",
                        chapter=chapter,
                    )
                )

            writer.assert_not_called()
            phase = next(item for item in events if item["type"] == "writing_phase")
            self.assertTrue(phase["document_write_requested"])
            content = "".join(
                item["delta"] for item in events if item["type"] == "content_delta"
            )
            self.assertIn("中间文档", content)
            self.assertNotIn("总体技术路线分四步", content)
            self.assertTrue(events[-1]["document_write_requested"])
            self.assertNotIn("先看目录标题", events[-1]["thinking"])
            self.assertNotIn("1/4 章节名称", events[-1]["thinking"])
            self.assertEqual(
                service.load_history("ch-a")[-1]["thinking"],
                events[-1]["thinking"],
            )

    def test_explicit_research_request_invokes_research_before_answering(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapter = {
                "chapter_id": "ch-a",
                "title": "技术方案",
                "blueprint_node": {"chapter_id": "ch-a", "title": "技术方案"},
                "context": {"items": []},
                "is_leaf": True,
            }
            service = ChapterChatService(context)

            class FakeBatch:
                status = "published"
                error = None
                items = []
                query_count = 1

            with (
                mock.patch(
                    "document_pipeline.document_outline_context.DocumentOutlineContextService.plan_and_load_inspections",
                    return_value={"inspect_ids": [], "reason": "标题树足够", "views": []},
                ),
                mock.patch(
                    "document_pipeline.chapter_research_planner.plan_chapter_research",
                    return_value={"need_research": True, "search_query": "技术方案 公开规范"},
                ),
                mock.patch("document_pipeline.research_adapters.create_research_adapter"),
                mock.patch(
                    "document_pipeline.research_service.ResearchService.resolve",
                    return_value=FakeBatch(),
                ) as research,
                mock.patch(
                    "llm_client.chat_stream_chunks",
                    return_value=iter([("content", "已按检索结果整理。")]),
                ),
            ):
                events = list(service.iter_answer_events("ch-a", "你去查资料啊", chapter=chapter))

            research.assert_called_once()
            self.assertTrue(research.call_args.kwargs["force_refresh"])
            research_event = next(item for item in events if item["type"] == "research")
            self.assertEqual(research_event["status"], "published")
            self.assertIn("公开资料检索", research_event["message"])

    def test_document_approval_request_is_routed_out_of_chat(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapter = {
                "chapter_id": "ch-a",
                "title": "技术方案",
                "blueprint_node": {"chapter_id": "ch-a", "title": "技术方案"},
                "context": {"items": []},
                "content": {
                    "content_revision": 2,
                    "blocks": [{"content": "当前正文草稿"}],
                },
                "head_content_revision": 2,
                "formal_content_revision": 1,
                "is_leaf": True,
            }
            service = ChapterChatService(context)

            action = {
                "action": "approve_document",
                "reason": "用户确认当前正文",
                "writing_instruction": "确认当前正文",
                "source": "chapter_agent",
            }
            with (
                mock.patch(
                    "document_pipeline.chapter_chat._decide_chapter_action",
                    return_value=action,
                ),
                mock.patch("llm_client.chat_stream_chunks") as writer,
            ):
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "确认当前正文并定稿",
                        chapter=chapter,
                    )
                )

            writer.assert_not_called()
            phase = next(item for item in events if item["type"] == "writing_phase")
            self.assertTrue(phase["document_approval_requested"])
            self.assertTrue(events[-1]["document_approval_requested"])
            self.assertFalse(events[-1]["document_write_requested"])
            self.assertIn("正在确认当前正文", events[-1]["reply"])

    def test_document_approval_without_pending_draft_returns_chinese_status(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            service = ChapterChatService(context)
            action = {
                "action": "approve_document",
                "reason": "用户确认当前正文",
                "writing_instruction": "正文定稿",
                "source": "chapter_agent",
            }
            chapter = {
                "chapter_id": "ch-a",
                "title": "技术方案",
                "blueprint_node": {"chapter_id": "ch-a", "title": "技术方案"},
                "context": {"items": []},
                "head_content_revision": 0,
                "formal_content_revision": 0,
                "is_leaf": True,
            }
            with mock.patch(
                "document_pipeline.chapter_chat._decide_chapter_action",
                return_value=action,
            ):
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "正文定稿",
                        chapter=chapter,
                    )
                )

            self.assertFalse(events[-1]["document_approval_requested"])
            self.assertIn("没有可确认的正文草稿", events[-1]["reply"])

            chapter["content"] = {
                "content_revision": 2,
                "blocks": [{"content": "已确认正文"}],
            }
            chapter["head_content_revision"] = 2
            chapter["formal_content_revision"] = 2
            with mock.patch(
                "document_pipeline.chapter_chat._decide_chapter_action",
                return_value=action,
            ):
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "再次确认正文",
                        chapter=chapter,
                    )
                )

            self.assertFalse(events[-1]["document_approval_requested"])
            self.assertIn("已经确认", events[-1]["reply"])

    def test_request_to_write_to_middle_document_routes_body_out_of_chat(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context)
            chapter = {
                "chapter_id": "ch-a",
                "title": "技术方案",
                "blueprint_node": {"chapter_id": "ch-a", "title": "技术方案"},
                "context": {"items": []},
                "is_leaf": True,
            }
            service = ChapterChatService(context)

            with mock.patch("llm_client.chat_stream_chunks") as writer:
                events = list(
                    service.iter_answer_events(
                        "ch-a",
                        "把内容写到屏幕中间文档中",
                        chapter=chapter,
                    )
                )

            writer.assert_not_called()
            self.assertTrue(events[-1]["document_write_requested"])
            self.assertIn("中间文档", events[-1]["reply"])


if __name__ == "__main__":
    unittest.main()
