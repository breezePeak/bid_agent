"""LLM-driven workspace agent for conversation and workflow actions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
from typing import Any
import uuid

from control_plane import CommandEnvelope, CommandGateway, WorkspaceContext

from .input_manifest import V3_ROOT
from .workspace_snapshot import V3WorkspaceSnapshotBuilder


_AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="workspace-agent")
_ALLOWED_ACTIONS = {"reply", "prepare_outline", "regenerate_outline", "run_document"}


def _short(value: Any, limit: int = 500) -> Any:
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "…"


def _decision_from_text(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return {"action": "reply", "reply": text}
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return {"action": "reply", "reply": text}
    if not isinstance(value, dict):
        return {"action": "reply", "reply": text}
    action = str(value.get("action") or "reply").strip()
    if action not in _ALLOWED_ACTIONS:
        action = "reply"
    return {
        "action": action,
        "reply": str(value.get("reply") or "").strip(),
    }


def _stage_summaries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    keys = (
        "stage_id",
        "capability_id",
        "title",
        "name",
        "status",
        "message",
        "error",
    )
    return [
        {
            key: _short(item.get(key))
            for key in keys
            if item.get(key) not in (None, "", [], {})
        }
        for item in value
        if isinstance(item, dict)
    ]


def _agent_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep only workflow facts needed for intent/tool selection.

    Promoted artifact payloads and parsed document bodies can contain millions of
    characters.  The workspace agent only needs to know which reusable results
    exist and what the current workflow state is.
    """

    analysis = snapshot.get("analysis") or {}
    workflow = snapshot.get("workflow") or {}
    generation = snapshot.get("generation") or {}
    document = snapshot.get("document") or {}
    chapters = snapshot.get("chapters") or {}
    promoted = snapshot.get("promoted_artifacts") or []
    artifact_keys = (
        "artifact_kind",
        "revision",
        "artifact_id",
        "artifact_hash",
        "producer_role",
        "created_at",
    )
    return {
        "workspace_id": snapshot.get("workspace_id"),
        "workspace_revision": snapshot.get("workspace_revision", 0),
        "project_mode": str((snapshot.get("profile") or {}).get("project_mode") or "full_write"),
        "materials_ready": bool((snapshot.get("material_readiness") or {}).get("ready")),
        "reusable_artifacts": [
            {
                key: item.get(key)
                for key in artifact_keys
                if item.get(key) not in (None, "", [], {})
            }
            for item in promoted
            if isinstance(item, dict)
        ],
        "analysis": {
            "status": analysis.get("status"),
            "stale": analysis.get("stale"),
            "stale_artifact_kinds": analysis.get("stale_artifact_kinds") or [],
        },
        "planning": {
            key: _short((snapshot.get("planning") or {}).get(key))
            for key in ("status", "reason", "message")
            if (snapshot.get("planning") or {}).get(key) not in (None, "", [], {})
        },
        "workflow": {
            "phase": workflow.get("phase"),
            "status": workflow.get("status"),
            "operation_id": workflow.get("operation_id"),
            "current_stage_id": workflow.get("current_stage_id"),
            "can_resume": workflow.get("can_resume"),
            "invalidation_reason": _short(workflow.get("invalidation_reason")),
            "stages": _stage_summaries(workflow.get("stages")),
        },
        "document": {
            "mode": document.get("mode"),
            "has_plan": bool(document.get("plan")),
            "has_integrated_document": bool(document.get("integrated")),
            "has_delivery": bool(document.get("delivery")),
        },
        "generation": {
            "operation_id": generation.get("operation_id"),
            "status": generation.get("status"),
            "current_stage_id": generation.get("current_stage_id"),
            "message": _short(generation.get("message")),
            "error": _short(generation.get("error")),
            "stages": _stage_summaries(generation.get("stages")),
        },
        "chapters": {
            "total": chapters.get("total"),
            "materialized": chapters.get("materialized"),
            "active": chapters.get("active"),
            "archived": chapters.get("archived"),
        },
    }


class WorkspaceChatService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def _history(self) -> tuple[Any, list[dict[str, Any]]]:
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
        return history_path, history

    def _messages(
        self,
        message: str,
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        agent_state = _agent_state(snapshot)
        return [
            {
                "role": "system",
                "content": (
                    "你是标书编制工作区的主 Agent，不是普通客服。你必须理解用户自然语言，"
                    "结合工作区状态决定是回答还是调用工作流工具。前端不会替你识别意图。\n"
                    "你只负责流程调度；不需要也不会获得标书正文、评分细节或目录结构。\n"
                    "可用动作：\n"
                    "1. reply：只回答，不调用工具。\n"
                    "2. prepare_outline：调用首次目录生成工作流。\n"
                    "3. regenerate_outline：复用已经存在的标书解析、评分理解和项目理解结果，"
                    "只调用目录重生成工具；不得重新执行上游理解。\n"
                    "4. run_document：调用正文生成或续跑工作流。\n"
                    "用户表达不清时使用 reply 追问；不要擅自扩大执行范围。"
                    "只输出一个 JSON 对象，格式为"
                    '{"action":"reply|prepare_outline|regenerate_outline|run_document",'
                    '"reply":"给用户的简洁中文回复"}。不要输出 Markdown。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"最近对话：{json.dumps(history, ensure_ascii=False)}\n"
                    f"工作区状态：{json.dumps(agent_state, ensure_ascii=False, default=str)}\n\n"
                    f"用户：{message}"
                ),
            },
        ]

    def _command_for_action(
        self,
        action: str,
        snapshot: dict[str, Any],
        actor: dict[str, Any],
    ) -> CommandEnvelope | None:
        if action == "reply":
            return None
        command_id = str(uuid.uuid4())
        payload: dict[str, Any] = {}
        kind = "document.prepare_outline"
        if action == "regenerate_outline":
            project_mode = str((snapshot.get("profile") or {}).get("project_mode") or "full_write")
            active_blueprint = (snapshot.get("analysis") or {}).get("chapter_blueprint") or {}
            planning_model = str(active_blueprint.get("planning_model") or "").strip()
            outline_capability = (
                "planning.rewrite_outline_merge"
                if project_mode == "bid_rewrite" and planning_model == "rewrite_merge"
                else "planning.chapter_outline_split"
            )
            payload["regenerate_capabilities"] = [outline_capability]
        elif action == "run_document":
            kind = "document.run_pipeline"
        return CommandEnvelope(
            command_id=command_id,
            workspace_id=self.context.workspace_id,
            kind=kind,
            payload=payload,
            goal_id=None,
            actor=actor,
            expected_revision=int(snapshot.get("workspace_revision") or 0),
            idempotency_key=f"workspace-agent:{command_id}",
        )

    def _submit(self, envelope: CommandEnvelope) -> None:
        from .execution_controller import V3ExecutionController

        controller = V3ExecutionController(self.context)
        CommandGateway(self.context, controller.handlers()).submit(envelope)

    def answer(
        self,
        message: str,
        *,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = V3WorkspaceSnapshotBuilder(self.context).build()
        history_path, history = self._history()
        from llm_client import chat

        raw = chat(self._messages(message, snapshot, history), temperature=0.1).strip()
        decision = _decision_from_text(raw)
        action = decision["action"]
        reply = decision["reply"] or "我已理解你的要求。"
        envelope = self._command_for_action(action, snapshot, actor or {"type": "user"})
        command: dict[str, Any] | None = None
        if envelope is not None:
            _AGENT_EXECUTOR.submit(self._submit, envelope)
            command = {
                "command_id": envelope.command_id,
                "kind": envelope.kind,
                "payload": envelope.payload,
            }
        with history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"role": "user", "content": message}, ensure_ascii=False) + "\n")
            stream.write(json.dumps({"role": "assistant", "content": reply}, ensure_ascii=False) + "\n")
        return {
            "reply": reply,
            "action": action,
            "command": command,
            "workspace_revision": snapshot.get("workspace_revision", 0),
        }


__all__ = ["WorkspaceChatService"]
