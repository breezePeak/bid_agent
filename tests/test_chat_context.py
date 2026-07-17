from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chat_store import close_chat_store, load_messages, save_message
from session_orchestrator import _build_user_prompt, _compact_status_snapshot


def test_load_messages_returns_latest_limit_in_chronological_order(tmp_path: Path) -> None:
    for index in range(25):
        save_message(
            tmp_path,
            "run-1",
            "assistant" if index % 2 else "user",
            f"message-{index}",
            actions=[{"type": "show_step", "command": f"step-{index}"}],
        )

    messages = load_messages(tmp_path, "run-1", limit=20)

    assert [message["content"] for message in messages] == [
        f"message-{index}" for index in range(5, 25)
    ]
    assert [message["id"] for message in messages] == sorted(
        message["id"] for message in messages
    )
    assert messages[-1]["actions"] == [
        {"type": "show_step", "command": "step-24"}
    ]
    close_chat_store(tmp_path)


def test_prompt_includes_recent_actions_and_repair_context() -> None:
    status = {
        "run_state": {"status": "running", "message": "repairing"},
        "sources": {},
        "inputs": {},
        "outputs": {},
        "workflow": [],
        "issues_summary": {
            "open_count": 3,
            "block_count": 2,
            "can_proceed": False,
        },
        "pending_confirmation": {
            "confirmation_id": "confirm-7",
            "type": "repair_issues",
            "count": 2,
            "unused": "not copied",
        },
        "current_repair_job": {
            "job_id": "repair-9",
            "status": "running",
            "phase": "revalidate",
            "counts": {"completed": 1, "total": 2},
            "message": "正在重验",
            "resume_command": "compliance-check",
            "internal_detail": "not copied",
        },
    }
    history = [
        {"role": "user", "content": "有哪些阻断问题？"},
        {
            "role": "assistant",
            "content": "可以修复两项。",
            "actions": [
                {
                    "type": "confirm_minimal_repair",
                    "label": "确认修复",
                    "confirmation_id": "confirm-7",
                    "internal_payload": {"large": "not copied"},
                }
            ],
        },
    ]

    snapshot = _compact_status_snapshot(status)
    prompt = _build_user_prompt("继续", history, snapshot)

    assert snapshot["issues_summary"]["block_count"] == 2
    assert snapshot["pending_confirmation"] == {
        "confirmation_id": "confirm-7",
        "type": "repair_issues",
        "count": 2,
    }
    assert snapshot["repair_job"]["job_id"] == "repair-9"
    assert snapshot["repair_job"]["resume_command"] == "compliance-check"
    assert "current_repair_job" not in snapshot

    assert "最近助手动作" in prompt
    assert '"type": "confirm_minimal_repair"' in prompt
    assert '"confirmation_id": "confirm-7"' in prompt
    assert "internal_payload" not in prompt
    assert "internal_detail" not in prompt
    assert json.dumps(snapshot, ensure_ascii=False) in prompt
