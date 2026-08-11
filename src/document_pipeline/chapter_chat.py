"""Chapter-scoped collaborative chat (non-authoritative).

Chat turns are workspace control-plane projections, not canonical Artifacts.
Each chapter keeps an isolated append-only history file under:

    workspace/v3/chapter_chats/{chapter_id}.jsonl

The Agent may only read frozen chapter/global context projections; it cannot
write ContentBlock, Blueprint, or other promoted Artifacts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .input_manifest import V3_ROOT

CHAPTER_CHAT_DIR = V3_ROOT / "chapter_chats"
HISTORY_TAIL = 40
PROMPT_HISTORY_TAIL = 12
DRAFT_PREVIEW_CHARS = 1200


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_chapter_id(chapter_id: str) -> str:
    return ControlStore._normalize_chapter_id(chapter_id)


class ChapterChatService:
    """Persist and answer chapter-local dialogues."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def history_path(self, chapter_id: str) -> Path:
        safe_id = _safe_chapter_id(chapter_id)
        # Reject anything that could escape the chat directory after normalize.
        if not re.fullmatch(r"[A-Za-z0-9._\-]+", safe_id):
            raise ControlPlaneError(
                "CHAPTER_ID_INVALID",
                "无效 chapter_id。",
                status_code=400,
            )
        return self.context.root / CHAPTER_CHAT_DIR / f"{safe_id}.jsonl"

    def load_history(
        self,
        chapter_id: str,
        *,
        limit: int = HISTORY_TAIL,
    ) -> list[dict[str, Any]]:
        path = self.history_path(chapter_id)
        if not path.is_file():
            return []
        turns: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            turns.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": str(item.get("created_at") or ""),
                }
            )
        if limit > 0:
            return turns[-limit:]
        return turns

    def append_turn(
        self,
        chapter_id: str,
        *,
        role: str,
        content: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        path = self.history_path(chapter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "role": str(role).strip(),
            "content": str(content),
            "created_at": created_at or _utc_now(),
            "chapter_id": _safe_chapter_id(chapter_id),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def build_chapter_chat_context(
        self,
        chapter: dict[str, Any],
        *,
        global_project_context: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        node = chapter.get("blueprint_node")
        node = node if isinstance(node, dict) else {}
        chapter_context = chapter.get("context")
        chapter_context = chapter_context if isinstance(chapter_context, dict) else {}
        content = chapter.get("content")
        content = content if isinstance(content, dict) else {}
        blocks = content.get("blocks") if isinstance(content.get("blocks"), list) else []
        draft_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = str(
                block.get("content")
                or block.get("text")
                or block.get("body")
                or ""
            ).strip()
            if text:
                draft_parts.append(text)
        draft_preview = "\n".join(draft_parts).strip()
        if len(draft_preview) > DRAFT_PREVIEW_CHARS:
            draft_preview = draft_preview[:DRAFT_PREVIEW_CHARS] + "…"

        context_items = [
            {
                "kind": str(item.get("kind") or ""),
                "title": str(item.get("title") or ""),
                "body": str(item.get("body") or ""),
                "source": str(item.get("source") or ""),
            }
            for item in (chapter_context.get("items") or [])
            if isinstance(item, dict)
        ]

        gpc = global_project_context if isinstance(global_project_context, dict) else {}
        identity = gpc.get("project_identity") if isinstance(gpc.get("project_identity"), dict) else {}
        shared_facts = {
            "global_context_revision": gpc.get("global_context_revision"),
            "project_name": identity.get("project_name") or gpc.get("project_name"),
            "buyer": identity.get("buyer") or gpc.get("buyer"),
            "scope_summary": gpc.get("scope_summary") or identity.get("scope_summary"),
            "confirmed_fact_count": len(gpc.get("confirmed_facts") or [])
            if isinstance(gpc.get("confirmed_facts"), list)
            else 0,
        }

        return {
            "chapter_id": str(chapter.get("chapter_id") or ""),
            "title": str(chapter.get("title") or node.get("title") or ""),
            "is_leaf": bool(chapter.get("is_leaf")),
            "status": str(chapter.get("status") or ""),
            "approval_status": str(chapter.get("approval_status") or ""),
            "purpose": str(node.get("purpose") or node.get("response_purpose") or ""),
            "level": node.get("level"),
            "order": chapter.get("order") or node.get("order"),
            "chapter_context_revision": int(chapter_context.get("context_revision") or 0),
            "chapter_context_items": context_items,
            "tender_requirements": list(tender_requirements or []),
            "scoring_requirements": list(scoring_requirements or []),
            "shared_project_facts": shared_facts,
            "draft_preview": draft_preview,
            "head_content_revision": int(chapter.get("head_content_revision") or 0),
            "formal_content_revision": int(chapter.get("formal_content_revision") or 0),
        }

    def answer(
        self,
        chapter_id: str,
        message: str,
        *,
        chapter: dict[str, Any],
        global_project_context: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            raise ControlPlaneError(
                "CHAT_MESSAGE_REQUIRED",
                "请输入要处理的问题。",
                status_code=400,
            )

        history = self.load_history(chapter_id, limit=PROMPT_HISTORY_TAIL)
        chat_context = self.build_chapter_chat_context(
            chapter,
            global_project_context=global_project_context,
            tender_requirements=tender_requirements,
            scoring_requirements=scoring_requirements,
        )
        system_prompt = (
            "你是正在编制标书的协作 Agent，当前对话只针对「这一章」。"
            "用自然、直接的中文回答，不复述问题，不说套话。"
            "只基于本章上下文、公共项目事实与本章草稿回答；不要越权改写其他章节。"
            "不确定就明确缺什么证据或材料。不得把外部信息当企业资质。"
            "你不能直接修改正文或晋级 Artifact，只能给出写作建议与下一步。"
        )
        user_payload = {
            "chapter_id": chat_context["chapter_id"],
            "chapter_title": chat_context["title"],
            "recent_chapter_dialogue": history,
            "chapter_context": chat_context,
            "user_message": text,
        }
        try:
            from llm_client import chat

            answer = chat(
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False),
                    },
                ]
            ).strip()
        except Exception:
            answer = self._fallback_answer(chat_context, text)

        if not answer:
            answer = "（无回复）"

        user_record = self.append_turn(chapter_id, role="user", content=text)
        assistant_record = self.append_turn(chapter_id, role="assistant", content=answer)
        return {
            "reply": answer,
            "chapter_id": _safe_chapter_id(chapter_id),
            "user_turn": user_record,
            "assistant_turn": assistant_record,
            "history_tail": self.load_history(chapter_id, limit=HISTORY_TAIL),
        }

    @staticmethod
    def _fallback_answer(chat_context: dict[str, Any], message: str) -> str:
        title = str(chat_context.get("title") or chat_context.get("chapter_id") or "当前章节")
        req_n = len(chat_context.get("tender_requirements") or [])
        score_n = len(chat_context.get("scoring_requirements") or [])
        ctx_n = len(chat_context.get("chapter_context_items") or [])
        parts = [
            f"这是章节「{title}」的专属对话。",
            f"本章已挂接招标要求 {req_n} 条、评分要求 {score_n} 条、专属上下文 {ctx_n} 条。",
        ]
        if not chat_context.get("is_leaf"):
            parts.append("当前节点是目录父节点，正文应写在下级叶子章节。")
        elif not chat_context.get("draft_preview"):
            parts.append("本章尚无草稿正文，可先生成初稿再针对段落细化。")
        else:
            parts.append("已有草稿，可继续追问应强调的评分点、交付物或证据缺口。")
        if "材料" in message or "证据" in message:
            parts.append("缺企业资质/案例/产品实绩时，请走 Evidence 补证，不要用外部网页顶替。")
        return " ".join(parts)
