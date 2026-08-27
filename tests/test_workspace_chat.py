from types import SimpleNamespace

import llm_client

import document_pipeline.workspace_chat as workspace_chat
from document_pipeline.workspace_chat import (
    WorkspaceChatService,
    _agent_state,
    _decision_from_text,
)


def test_workspace_agent_parses_structured_llm_action() -> None:
    assert _decision_from_text(
        '```json\n{"action":"regenerate_outline","reply":"只重新生成目录。"}\n```'
    ) == {
        "action": "regenerate_outline",
        "reply": "只重新生成目录。",
    }


def test_workspace_agent_state_excludes_large_artifact_payloads() -> None:
    marker = "parsed-document-body-should-not-reach-agent"
    state = _agent_state(
        {
            "workspace_id": "workspace-1",
            "workspace_revision": 5,
            "profile": {"project_mode": "bid_rewrite"},
            "promoted_artifacts": [
                {
                    "artifact_kind": "score_model",
                    "revision": 3,
                    "artifact_id": "artifact-1",
                    "payload": {"content": marker * 100_000},
                }
            ],
            "analysis": {"status": "ready", "requirement_ledger": marker * 100_000},
            "workflow": {"status": "waiting", "stages": []},
        }
    )

    encoded = str(state)
    assert marker not in encoded
    assert state["reusable_artifacts"] == [
        {"artifact_kind": "score_model", "revision": 3, "artifact_id": "artifact-1"}
    ]
    assert len(encoded) < 10_000


def test_regenerate_outline_does_not_bypass_source_confirmation() -> None:
    service = WorkspaceChatService.__new__(WorkspaceChatService)
    service.context = SimpleNamespace(workspace_id="workspace-1")

    envelope = service._command_for_action(
        "regenerate_outline",
        {
            "workspace_revision": 7,
            "profile": {"project_mode": "bid_rewrite"},
            "analysis": {"chapter_blueprint": {"planning_model": "rewrite_merge"}},
        },
        {"type": "user", "id": "user-1"},
    )

    assert envelope is not None
    assert envelope.kind == "document.prepare_outline"
    assert envelope.payload == {
        "regenerate_capabilities": ["planning.chapter_outline_split"],
    }
    assert "score.semantic_reconcile" not in envelope.payload["regenerate_capabilities"]
    assert "planning.project_understanding" not in envelope.payload["regenerate_capabilities"]


def test_regenerate_outline_only_requests_direct_outline_capability() -> None:
    service = WorkspaceChatService.__new__(WorkspaceChatService)
    service.context = SimpleNamespace(workspace_id="workspace-1")

    envelope = service._command_for_action(
        "regenerate_outline",
        {
            "workspace_revision": 8,
            "profile": {"project_mode": "full_write"},
        },
        {"type": "user", "id": "user-1"},
    )

    assert envelope is not None
    assert envelope.payload == {
        "regenerate_capabilities": ["planning.chapter_outline_split"],
    }


def test_workspace_chat_uses_llm_as_main_agent_and_dispatches_its_action(
    monkeypatch,
    tmp_path,
) -> None:
    service = WorkspaceChatService.__new__(WorkspaceChatService)
    service.context = SimpleNamespace(workspace_id="workspace-1", root=tmp_path)
    snapshot = {
        "workspace_revision": 9,
        "profile": {"project_mode": "bid_rewrite"},
        "analysis": {"chapter_blueprint": {"planning_model": "score_direct"}},
    }
    history_path = tmp_path / "chat_history.jsonl"
    submitted = []
    seen_messages = []

    monkeypatch.setattr(
        workspace_chat.V3WorkspaceSnapshotBuilder,
        "build",
        lambda _self: snapshot,
    )
    monkeypatch.setattr(service, "_history", lambda: (history_path, []))
    monkeypatch.setattr(
        llm_client,
        "chat",
        lambda messages, temperature: (
            seen_messages.extend(messages)
            or '{"action":"regenerate_outline","reply":"复用现有理解，只重新生成目录。"}'
        ),
    )
    monkeypatch.setattr(
        workspace_chat,
        "_AGENT_EXECUTOR",
        SimpleNamespace(submit=lambda fn, envelope: submitted.append((fn, envelope))),
    )

    result = service.answer("请重新根据标书生成一版新目录")

    assert result["action"] == "regenerate_outline"
    assert result["command"]["payload"] == {
        "regenerate_capabilities": ["planning.chapter_outline_split"],
    }
    assert submitted
    assert "主 Agent" in seen_messages[0]["content"]
