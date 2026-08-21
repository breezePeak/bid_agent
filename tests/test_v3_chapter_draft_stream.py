from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import api.v3_app as v3_app  # noqa: E402
from control_plane import ControlPlaneError  # noqa: E402


class _Request:
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return dict(self._body)


async def _events(response) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return [json.loads(line) for line in "".join(chunks).splitlines() if line]


def test_draft_stream_delegates_to_the_single_chapter_writing_service() -> None:
    captured = []
    emitted = [
        {"type": "meta", "operation_id": "op-1"},
        {
            "type": "done",
            "chapter": {"chapter_id": "chapter-1", "chapter_revision": 8},
            "content": {"content_revision": 2},
        },
    ]
    writing_service = mock.Mock()
    writing_service.iter_events.side_effect = (
        lambda request: captured.append(request) or iter(emitted)
    )
    workspace_service = mock.Mock()
    workspace_service.get_chapter.return_value = {
        "chapter_id": "chapter-1",
        "head_content_revision": 1,
    }
    with (
        mock.patch.object(v3_app, "_principal", return_value={"id": "tester", "type": "user"}),
        mock.patch.object(v3_app, "_context", return_value=object()),
        mock.patch(
            "document_pipeline.chapter_workspace.ChapterWorkspaceService",
            return_value=workspace_service,
        ),
        mock.patch(
            "document_pipeline.chapter_writing_service.ChapterWritingService",
            return_value=writing_service,
        ),
    ):
        response = asyncio.run(
            v3_app.stream_chapter_draft(
                "alpha",
                "chapter-1",
                _Request(
                    {
                        "expected_revision": 3,
                        "expected_chapter_revision": 7,
                        "instruction": "重新写",
                    }
                ),
            )
        )
        events = asyncio.run(_events(response))

    assert [item["type"] for item in events] == ["meta", "done"]
    assert len(captured) == 1
    request = captured[0]
    assert request.operation == "rewrite"
    assert request.user_instruction == "重新写"
    assert request.commit_drafts is True
    assert request.run_research is True


def test_draft_stream_preserves_research_failure_details() -> None:
    failure = ControlPlaneError(
        "WRITER_RESEARCH_ACTION_REQUIRED",
        "公开资料未形成可核验来源",
        details={
            "research": {
                "decision_status": "blocked_human",
                "queries": [
                    {
                        "question": "公开标准",
                        "evidence_count": 0,
                        "error": "no_verified_source",
                    }
                ],
            }
        },
    )
    writing_service = mock.Mock()
    writing_service.iter_events.side_effect = failure
    workspace_service = mock.Mock()
    workspace_service.get_chapter.return_value = {
        "chapter_id": "chapter-1",
        "head_content_revision": 0,
    }
    with (
        mock.patch.object(v3_app, "_principal", return_value={"id": "tester", "type": "user"}),
        mock.patch.object(v3_app, "_context", return_value=object()),
        mock.patch(
            "document_pipeline.chapter_workspace.ChapterWorkspaceService",
            return_value=workspace_service,
        ),
        mock.patch(
            "document_pipeline.chapter_writing_service.ChapterWritingService",
            return_value=writing_service,
        ),
    ):
        response = asyncio.run(
            v3_app.stream_chapter_draft(
                "alpha",
                "chapter-1",
                _Request({"expected_revision": 3, "expected_chapter_revision": 7}),
            )
        )
        events = asyncio.run(_events(response))

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "CHAPTER_RESEARCH_UNAVAILABLE"
    assert events[0]["details"]["original_code"] == "WRITER_RESEARCH_ACTION_REQUIRED"
    assert events[0]["details"]["error"] == "no_verified_source"
    assert "仍未取得合格来源" in events[0]["message"]
    assert events[0]["details"]["attempt_count"] == 1


def test_http_layer_contains_no_parallel_writer_kernel() -> None:
    source = (ROOT / "src/api/v3_app.py").read_text(encoding="utf-8")
    assert "ChapterWritingService(context).iter_events" in source
    for removed in (
        "_chapter_draft_messages",
        "def _chapter_project_context",
        "_chapter_research_plan",
        "_research_candidate_rows",
    ):
        assert removed not in source
