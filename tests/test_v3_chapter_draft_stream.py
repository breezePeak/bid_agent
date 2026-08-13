from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api import v3_app
from control_plane import WorkspaceContext
from starlette.requests import Request


def _request(payload: dict) -> Request:
    encoded = json.dumps({
        "global_context_id": "PM-1",
        "global_context_revision": 2,
        "global_context_hash": "g" * 64,
        "chapter_context_id": "chapter-context:chapter-1",
        "chapter_context_revision": 0,
        "chapter_context_hash": "c" * 64,
        **payload,
    }).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/draft/stream",
            "raw_path": b"/draft/stream",
            "query_string": b"",
            "headers": [],
            "client": ("test", 123),
            "server": ("testserver", 80),
        },
        receive,
    )
    request.state.principal = {"id": "writer-user", "role": "owner"}
    return request


async def _events(response) -> list[dict]:
    raw = bytearray()
    async for chunk in response.body_iterator:
        raw.extend(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]


class V3ChapterDraftStreamTests(TestCase):
    def _context(self, root: Path) -> WorkspaceContext:
        runs = root / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    @staticmethod
    def _chapter() -> dict:
        return {
            "chapter_id": "chapter-1",
            "title": "项目实施方案",
            "chapter_revision": 7,
            "materialized": True,
            "blueprint_node": {},
            "context": {"context_revision": 0, "items": []},
        }

    @staticmethod
    def _project_context(**overrides) -> dict:
        value = {
            "global_context_id": "PM-1",
            "global_context_revision": 2,
            "global_context_hash": "g" * 64,
            "project_id": "project-1",
            "identity": {"project_name": "城市地下管网普查项目"},
            "background": ["既有地下管网底数不清"],
            "goals": ["形成完整管网底图"],
            "scope": ["开展地下管网普查"],
            "work_packages": ["管网数据采集与成果复核"],
            "confirmed_facts": [],
        }
        value.update(overrides)
        return value

    @staticmethod
    def _chapter_context() -> dict:
        return {
            "chapter_id": "chapter-1",
            "global_context_id": "PM-1",
            "global_context_revision": 2,
            "global_context_hash": "g" * 64,
            "chapter_context_id": "chapter-context:chapter-1",
            "chapter_context_revision": 0,
            "chapter_context_hash": "c" * 64,
            "requirement_excerpts": [],
            "score_obligations": [],
            "chapter_context_items": [],
            "highlighted_fact_ids": [],
        }

    @staticmethod
    def _grounding_report() -> dict:
        return {
            "verdict": "pass",
            "global_context_id": "PM-1",
            "global_context_revision": 2,
            "global_context_hash": "g" * 64,
            "chapter_context_id": "chapter-context:chapter-1",
            "chapter_context_revision": 0,
            "chapter_context_hash": "c" * 64,
            "paragraph_fact_bindings": {},
        }

    def test_draft_prompt_assigns_chapter_specific_opening_policy(self):
        background_messages = v3_app._chapter_draft_messages(
            {**self._chapter(), "title": "项目任务背景"},
            project_context=self._project_context(),
        )
        goal_messages = v3_app._chapter_draft_messages(
            {**self._chapter(), "title": "工作目标"},
            project_context=self._project_context(),
        )
        diagram_messages = v3_app._chapter_draft_messages(
            {
                **self._chapter(),
                "title": "技术路线图",
                "blueprint_node": {"purpose": "以图呈现总体技术路线"},
            },
            project_context=self._project_context(),
            sibling_context={"chapter_role": "visual", "siblings": []},
        )

        background_payload = json.loads(background_messages[1]["content"])
        goal_payload = json.loads(goal_messages[1]["content"])
        diagram_payload = json.loads(diagram_messages[1]["content"])
        self.assertEqual(
            background_payload["opening_policy"]["mode"],
            "project_overview",
        )
        self.assertEqual(goal_payload["opening_policy"]["mode"], "chapter_focus")
        self.assertIn("禁止在多个子章节套用同一段", goal_messages[0]["content"])
        self.assertEqual(diagram_payload["content_format"], "technical_roadmap_diagram")
        self.assertIn("技术路线图/流程图", diagram_messages[0]["content"])
        self.assertIn("Mermaid", diagram_messages[0]["content"])

    def test_think_tags_go_to_thinking_channel_not_saved_body(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            submitted = []
            receipt = SimpleNamespace(
                status="accepted",
                error=None,
                message="saved",
                result={"chapter": {"chapter_revision": 8}, "content": {"content_revision": 1}},
                as_dict=lambda: {"status": "accepted"},
            )
            gateway = SimpleNamespace(submit=lambda envelope: submitted.append(envelope) or receipt)

            def chunks(*_args, **_kwargs):
                yield "content", "<think>先确认章节目的"
                yield "content", "再写正文</think>"
                yield "content", "项目实施方案"

            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=self._chapter()),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch("document_pipeline.content_grounding.ContentGroundingGate.evaluate", return_value=self._grounding_report()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._skip_research_plan),
                mock.patch("llm_client.chat_stream_chunks", side_effect=chunks),
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            thinking = "".join(
                event.get("delta") or ""
                for event in events
                if event["type"] == "thinking_delta"
            )
            body = "".join(event.get("delta") or "" for event in events if event["type"] == "delta")
            self.assertIn("先确认章节目的", thinking)
            self.assertIn("再写正文", thinking)
            self.assertEqual(body, "项目实施方案")
            self.assertNotIn("<think>", body)
            self.assertEqual(submitted[0].payload["text"], "项目实施方案")

    def test_parent_chapter_is_rejected_before_model_streaming(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            parent = {**self._chapter(), "is_leaf": False, "title": "目标任务"}
            gateway = SimpleNamespace(submit=mock.Mock())
            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch(
                    "document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter",
                    return_value=parent,
                ),
                mock.patch("llm_client.chat_stream_chunks") as stream,
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha",
                    "chapter-1",
                    _request({"expected_revision": 3, "expected_chapter_revision": 7}),
                ))
                events = asyncio.run(_events(response))

            self.assertEqual(events[-1]["code"], "CHAPTER_BODY_REQUIRES_LEAF")
            stream.assert_not_called()
            gateway.submit.assert_not_called()

    @staticmethod
    def _skip_research_plan(*_args, **_kwargs):
        return {
            "need_research": False,
            "reason": "测试：章节 Agent 判定无需检索",
            "search_query": "",
            "brief": {"project_name": "城市地下管网普查项目", "related_tasks": [], "chapter_title": "x"},
            "decision_source": "chapter_agent",
        }

    @staticmethod
    def _need_research_plan(*_args, **_kwargs):
        return {
            "need_research": True,
            "reason": "测试：章节 Agent 判定需要公开资料",
            "search_query": (
                "城市地下管网普查项目 开展地下管网普查 项目任务背景 行业现状 政策要求"
            ),
            "brief": {
                "project_name": "城市地下管网普查项目",
                "related_tasks": ["开展地下管网普查"],
                "chapter_title": "项目任务背景",
                "focus_keywords": ["项目任务背景", "行业现状"],
            },
            "decision_source": "chapter_agent",
        }

    def test_stream_emits_visible_chinese_deltas_then_commits_exact_text(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            submitted = []
            receipt = SimpleNamespace(
                status="accepted",
                error=None,
                message="saved",
                result={"chapter": {"chapter_revision": 8}, "content": {"content_revision": 1}},
                as_dict=lambda: {"status": "accepted"},
            )
            gateway = SimpleNamespace(submit=lambda envelope: submitted.append(envelope) or receipt)

            def chunks(*_args, **_kwargs):
                yield "reasoning", "不得发送给浏览器"
                yield "content", "项目实"
                yield "content", "施方案"

            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=self._chapter()),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch("document_pipeline.content_grounding.ContentGroundingGate.evaluate", return_value=self._grounding_report()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._skip_research_plan),
                mock.patch("llm_client.chat_stream_chunks", side_effect=chunks),
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            self.assertEqual(response.media_type, "application/x-ndjson")
            types = [event["type"] for event in events]
            self.assertEqual(types[0], "meta")
            self.assertIn("delta", types)
            self.assertEqual(types[-1], "done")
            research_events = [event for event in events if event["type"] == "research"]
            statuses = [event.get("status") for event in research_events]
            self.assertIn("orienting", statuses)
            self.assertIn("oriented", statuses)
            self.assertIn("planning", statuses)
            self.assertLess(statuses.index("oriented"), statuses.index("planning"))
            self.assertEqual("".join(event["delta"] for event in events if event["type"] == "delta"), "项目实施方案")
            self.assertEqual(events[-1]["text"], "项目实施方案")
            thinking = "".join(
                event.get("delta") or ""
                for event in events
                if event["type"] == "thinking_delta"
            )
            self.assertEqual(thinking, "不得发送给浏览器")
            self.assertNotIn(
                "不得发送给浏览器",
                "".join(event.get("delta") or "" for event in events if event["type"] == "delta"),
            )
            self.assertEqual(len(submitted), 1)
            self.assertEqual(submitted[0].payload["text"], "项目实施方案")

    def test_provider_failure_emits_error_and_never_commits_partial_text(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            gateway = SimpleNamespace(submit=mock.Mock())

            def chunks(*_args, **_kwargs):
                yield "content", "未完成正文"
                raise RuntimeError("provider disconnected")

            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=self._chapter()),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._skip_research_plan),
                mock.patch("llm_client.chat_stream_chunks", side_effect=chunks),
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            types = [event["type"] for event in events]
            self.assertEqual(types[0], "meta")
            self.assertIn("delta", types)
            self.assertEqual(types[-1], "error")
            self.assertEqual(events[-1]["code"], "CHAPTER_DRAFT_STREAM_FAILED")
            gateway.submit.assert_not_called()

    def test_invalid_revision_is_a_terminal_error_without_model_or_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            gateway = SimpleNamespace(submit=mock.Mock())
            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("llm_client.chat_stream_chunks") as stream,
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": "bad"})
                ))
                events = asyncio.run(_events(response))

            self.assertEqual(events, [{
                "type": "error",
                "chapter_id": "chapter-1",
                "code": "CHAPTER_REVISION_INVALID",
                "message": "expected_chapter_revision 必须是整数。",
            }])
            stream.assert_not_called()
            gateway.submit.assert_not_called()

    def test_background_chapter_researches_verified_sources_before_streaming(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            chapter = {**self._chapter(), "title": "项目任务背景"}
            receipt = SimpleNamespace(
                status="accepted", error=None, message="saved",
                result={}, as_dict=lambda: {"status": "accepted"},
            )
            gateway = SimpleNamespace(submit=lambda _envelope: receipt)
            batch = SimpleNamespace(
                status="published", error=None, batch_id="EB-123",
                items=[SimpleNamespace(
                    evidence_id="E-1", title="国家标准", publisher="std.samr.gov.cn",
                    source_url="https://std.samr.gov.cn/example", content="标准原文节选：应建立全过程质量控制机制。",
                )],
            )

            def chunks(messages, **_kwargs):
                payload = json.loads(messages[1]["content"])
                self.assertEqual(payload["verified_public_sources"][0]["source_url"], "https://std.samr.gov.cn/example")
                self.assertEqual(payload["global_project_context"]["identity"]["project_name"], "城市地下管网普查项目")
                self.assertIn("项目背景、任务范围", messages[0]["content"])
                yield "content", "依据现行标准建立全过程质量控制机制。"

            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=chapter),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch("document_pipeline.content_grounding.ContentGroundingGate.evaluate", return_value=self._grounding_report()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._need_research_plan),
                mock.patch("document_pipeline.research_service.ResearchService.resolve", return_value=batch) as resolve,
                mock.patch("llm_client.chat_stream_chunks", side_effect=chunks),
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            types = [item["type"] for item in events]
            self.assertEqual(types[0], "meta")
            self.assertIn("research", types)
            self.assertIn("delta", types)
            self.assertEqual(types[-1], "done")
            research_events = [item for item in events if item["type"] == "research"]
            statuses = [item.get("status") for item in research_events]
            self.assertIn("planning", statuses)
            self.assertIn("searching", statuses)
            self.assertIn("ready", statuses)
            ready = next(item for item in research_events if item.get("status") == "ready")
            self.assertEqual(ready["sources"][0]["source_url"], "https://std.samr.gov.cn/example")
            question = resolve.call_args.args[0].question
            self.assertIn("城市地下管网普查项目", question)
            self.assertIn("开展地下管网普查", question)
            self.assertNotIn("confirmed_facts", question)
            self.assertNotIn('"identity"', question)
            self.assertLess(len(question), 800)

    def test_research_failure_blocks_chapter_writing_instead_of_becoming_gap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            chapter = {**self._chapter(), "title": "项目任务背景"}
            gateway = SimpleNamespace(submit=mock.Mock())
            batch = SimpleNamespace(
                status="failed",
                error="RuntimeError: doubao_web 正在等待网页登录。",
                batch_id="EB-auth-failed",
                items=[],
            )

            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=chapter),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch("document_pipeline.content_grounding.ContentGroundingGate.evaluate", return_value=self._grounding_report()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._need_research_plan),
                mock.patch("document_pipeline.research_service.ResearchService.resolve", return_value=batch),
                mock.patch("llm_client.chat_stream_chunks") as stream,
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            types = [item["type"] for item in events]
            self.assertEqual(types[0], "meta")
            self.assertEqual(types[-1], "error")
            self.assertIn("research", types)
            self.assertEqual(events[-1]["code"], "CHAPTER_RESEARCH_UNAVAILABLE")
            self.assertEqual(events[-1]["details"]["batch_id"], "EB-auth-failed")
            self.assertIn("等待网页登录", events[-1]["details"]["error"])
            stream.assert_not_called()
            gateway.submit.assert_not_called()

    def test_research_gap_continues_with_tender_project_facts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            context = self._context(Path(temporary))
            chapter = {**self._chapter(), "title": "项目任务背景"}
            receipt = SimpleNamespace(
                status="accepted", error=None, message="saved", result={},
                as_dict=lambda: {"status": "accepted"},
            )
            gateway = SimpleNamespace(submit=mock.Mock(return_value=receipt))
            batch = SimpleNamespace(status="gap", error="no sources", batch_id="EB-void", items=[])
            def chunks(*_args, **_kwargs):
                yield "content", "城市地下管网普查项目将开展地下管网普查。"
            with (
                mock.patch.object(v3_app, "_context", return_value=context),
                mock.patch("document_pipeline.chapter_workspace.ChapterWorkspaceService.get_chapter", return_value=chapter),
                mock.patch.object(v3_app, "_chapter_project_context", return_value=self._project_context()),
                mock.patch.object(v3_app, "_chapter_semantic_requirements", return_value=([], [])),
                mock.patch("document_pipeline.global_project_context.GlobalProjectContextService.build_chapter_context", return_value=self._chapter_context()),
                mock.patch("document_pipeline.content_grounding.ContentGroundingGate.evaluate", return_value=self._grounding_report()),
                mock.patch.object(v3_app, "_chapter_research_plan", side_effect=self._need_research_plan),
                mock.patch("document_pipeline.research_service.ResearchService.resolve", return_value=batch),
                mock.patch("llm_client.chat_stream_chunks", side_effect=chunks) as stream,
                mock.patch.object(v3_app, "_gateway", return_value=gateway),
            ):
                response = asyncio.run(v3_app.stream_chapter_draft(
                    "alpha", "chapter-1", _request({"expected_revision": 3, "expected_chapter_revision": 7})
                ))
                events = asyncio.run(_events(response))

            types = [item["type"] for item in events]
            self.assertEqual(types[0], "meta")
            self.assertIn("delta", types)
            self.assertEqual(types[-1], "done")
            research_events = [item for item in events if item["type"] == "research"]
            statuses = [item.get("status") for item in research_events]
            self.assertIn("planning", statuses)
            self.assertIn("gap", statuses)
            stream.assert_called_once()
            gateway.submit.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
