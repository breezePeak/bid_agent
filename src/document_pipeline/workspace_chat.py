"""Workspace chat service; keeps Prompt and LLM calls out of HTTP adapters."""

from __future__ import annotations

import json
from typing import Any

from control_plane import WorkspaceContext

from .input_manifest import V3_ROOT
from .workspace_snapshot import V3WorkspaceSnapshotBuilder


class WorkspaceChatService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def answer(self, message: str) -> dict[str, Any]:
        snapshot = V3WorkspaceSnapshotBuilder(self.context).build()
        history_path = self.context.root / V3_ROOT / "chat_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        if history_path.is_file():
            for line in history_path.read_text(encoding="utf-8").splitlines()[-12:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    history.append(item)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是正在编制标书的协作 Agent。用自然、直接的中文回答，不复述问题，不说套话。"
                    "基于工作区状态给出判断和下一步；不确定就明确缺什么证据。"
                    "不得把外部信息当企业资质。"
                ),
            },
            {
                "role": "user",
                "content": f"最近对话：{history}\n工作区状态：{snapshot}\n\n用户：{message}",
            },
        ]
        try:
            from llm_client import chat

            answer = chat(messages).strip()
        except Exception:
            document = snapshot.get("document") or {}
            needs = snapshot.get("evidence_needs") or []
            answer = f"当前文档状态：{(document.get('delivery') or {}).get('status', 'new')}。"
            if needs:
                answer += f" 还缺 {len(needs)} 项证据，先补“{needs[0].get('question', '')}”。"
        with history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"role": "user", "content": message}, ensure_ascii=False) + "\n")
            stream.write(json.dumps({"role": "assistant", "content": answer}, ensure_ascii=False) + "\n")
        return {
            "reply": answer,
            "workspace_revision": snapshot.get("workspace_revision", 0),
        }


__all__ = ["WorkspaceChatService"]
