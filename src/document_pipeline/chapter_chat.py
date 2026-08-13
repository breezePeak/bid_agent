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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .input_manifest import V3_ROOT

CHAPTER_CHAT_DIR = V3_ROOT / "chapter_chats"
HISTORY_TAIL = 40
PROMPT_HISTORY_TAIL = 12
DRAFT_PREVIEW_CHARS = 1200
MAX_TURN_CHARS = 20_000


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
            thinking = str(item.get("thinking") or item.get("reasoning") or "")
            created_at = str(item.get("created_at") or "")
            if role not in {"user", "assistant"}:
                continue
            if not content and not thinking:
                continue
            turn_id = str(item.get("turn_id") or "").strip()
            if not turn_id:
                turn_id = f"legacy:{len(turns)}:{role}:{created_at}"
            turns.append(
                {
                    "turn_id": turn_id,
                    "role": role,
                    "content": content,
                    "thinking": thinking,
                    "created_at": created_at,
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
        thinking: str = "",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        path = self.history_path(chapter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "turn_id": str(uuid.uuid4()),
            "role": str(role).strip(),
            "content": str(content),
            "thinking": str(thinking or ""),
            "created_at": created_at or _utc_now(),
            "chapter_id": _safe_chapter_id(chapter_id),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def update_turn(
        self,
        chapter_id: str,
        *,
        turn_id: str = "",
        created_at: str = "",
        role: str = "",
        content: str | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        """Edit a persisted collaboration turn. Not a canonical Artifact write."""
        wanted_id = str(turn_id or "").strip()
        wanted_created = str(created_at or "").strip()
        wanted_role = str(role or "").strip()
        if wanted_role and wanted_role not in {"user", "assistant"}:
            raise ControlPlaneError(
                "CHAT_TURN_INVALID",
                "只能编辑 user 或 assistant 消息。",
                status_code=400,
            )
        if content is None and thinking is None:
            raise ControlPlaneError(
                "CHAT_TURN_INVALID",
                "请提供要修改的正文或思考过程。",
                status_code=400,
            )
        next_content = None if content is None else str(content)
        next_thinking = None if thinking is None else str(thinking)
        if next_content is not None and len(next_content) > MAX_TURN_CHARS:
            raise ControlPlaneError(
                "CHAT_TURN_TOO_LONG",
                f"消息正文不能超过 {MAX_TURN_CHARS} 字。",
                status_code=400,
            )
        if next_thinking is not None and len(next_thinking) > MAX_TURN_CHARS:
            raise ControlPlaneError(
                "CHAT_TURN_TOO_LONG",
                f"思考过程不能超过 {MAX_TURN_CHARS} 字。",
                status_code=400,
            )

        turns = self.load_history(chapter_id, limit=0)
        match_index = -1
        for index, item in enumerate(turns):
            if wanted_id and str(item.get("turn_id") or "") == wanted_id:
                match_index = index
                break
            if (
                not wanted_id
                and wanted_created
                and wanted_role
                and str(item.get("created_at") or "") == wanted_created
                and str(item.get("role") or "") == wanted_role
            ):
                match_index = index
        if match_index < 0:
            raise ControlPlaneError(
                "CHAT_TURN_NOT_FOUND",
                "未找到要编辑的历史消息。",
                status_code=404,
            )
        updated = dict(turns[match_index])
        if next_content is not None:
            updated["content"] = next_content
        if next_thinking is not None:
            updated["thinking"] = next_thinking
        if not str(updated.get("content") or "").strip() and not str(
            updated.get("thinking") or ""
        ).strip():
            raise ControlPlaneError(
                "CHAT_TURN_INVALID",
                "正文和思考过程不能同时为空。",
                status_code=400,
            )
        turns[match_index] = updated
        self._write_history(chapter_id, turns)
        return updated

    def _write_history(self, chapter_id: str, turns: list[dict[str, Any]]) -> None:
        path = self.history_path(chapter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_id = _safe_chapter_id(chapter_id)
        lines: list[str] = []
        for item in turns:
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            record = {
                "turn_id": str(item.get("turn_id") or uuid.uuid4()),
                "role": role,
                "content": str(item.get("content") or ""),
                "thinking": str(item.get("thinking") or ""),
                "created_at": str(item.get("created_at") or _utc_now()),
                "chapter_id": safe_id,
            }
            lines.append(json.dumps(record, ensure_ascii=False))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def build_chapter_chat_context(
        self,
        chapter: dict[str, Any],
        *,
        global_project_context: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
        sibling_context: dict[str, Any] | None = None,
        outline_context: dict[str, Any] | None = None,
        writing_orientation: dict[str, Any] | None = None,
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

        sibling_payload = sibling_context if isinstance(sibling_context, dict) else {}
        if not sibling_payload:
            try:
                from .sibling_chapter_context import SiblingChapterContextService

                sibling_payload = SiblingChapterContextService(
                    self.context
                ).build_for_chapter(chapter)
            except Exception:
                sibling_payload = {}

        outline_payload = outline_context if isinstance(outline_context, dict) else {}
        if not outline_payload:
            try:
                from .document_outline_context import DocumentOutlineContextService

                outline_payload = DocumentOutlineContextService(
                    self.context
                ).build_for_chapter(chapter)
            except Exception:
                outline_payload = {}
        try:
            from .document_outline_context import (
                compact_outline_for_prompt,
                compact_sibling_for_prompt,
            )

            outline_for_agent = compact_outline_for_prompt(outline_payload)
            sibling_for_agent = compact_sibling_for_prompt(sibling_payload)
        except Exception:
            outline_for_agent = outline_payload
            sibling_for_agent = sibling_payload

        orientation_payload = writing_orientation if isinstance(writing_orientation, dict) else {}
        if not orientation_payload:
            try:
                from .writing_orientation import WritingOrientationService

                orientation_payload = WritingOrientationService(
                    self.context
                ).build_for_chapter(
                    chapter,
                    outline_context=outline_payload,
                    sibling_context=sibling_payload,
                    tender_requirements=tender_requirements,
                    scoring_requirements=scoring_requirements,
                )
            except Exception:
                orientation_payload = {}
        try:
            from .writing_orientation import compact_orientation_for_prompt

            orientation_for_agent = compact_orientation_for_prompt(orientation_payload)
        except Exception:
            orientation_for_agent = orientation_payload

        return {
            "chapter_id": str(chapter.get("chapter_id") or ""),
            "title": str(chapter.get("title") or node.get("title") or ""),
            "is_leaf": bool(chapter.get("is_leaf")),
            "status": str(chapter.get("status") or ""),
            "approval_status": str(chapter.get("approval_status") or ""),
            "purpose": str(
                (orientation_for_agent.get("writing_purpose") or {}).get("purpose")
                or node.get("purpose")
                or node.get("response_purpose")
                or ""
            ),
            "level": node.get("level"),
            "order": chapter.get("order") or node.get("order"),
            "chapter_context_revision": int(chapter_context.get("context_revision") or 0),
            "chapter_context_items": context_items,
            "tender_requirements": list(tender_requirements or []),
            "scoring_requirements": list(scoring_requirements or []),
            "shared_project_facts": shared_facts,
            "writing_orientation": orientation_for_agent,
            "document_outline_context": outline_for_agent,
            "sibling_chapter_context": sibling_for_agent,
            "inspected_chapters": [],
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
        sibling_context: dict[str, Any] | None = None,
        outline_context: dict[str, Any] | None = None,
        writing_orientation: dict[str, Any] | None = None,
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
            sibling_context=sibling_context,
            outline_context=outline_context,
            writing_orientation=writing_orientation,
        )
        inspection = self._resolve_inspections(
            chapter_id=chapter_id,
            outline_context=outline_context or chat_context.get("document_outline_context"),
            task=text,
        )
        chat_context["inspected_chapters"] = list(inspection.get("views") or [])
        messages = self._build_messages(chat_context, history, text)
        thinking = ""
        try:
            from llm_client import chat_with_meta

            meta = chat_with_meta(messages, temperature=0.2)
            answer = str(meta.get("content") or "").strip()
            thinking = str(meta.get("reasoning") or "").strip()
        except Exception:
            answer = self._fallback_answer(chat_context, text)

        if not answer:
            answer = "（无回复）"

        user_record = self.append_turn(chapter_id, role="user", content=text)
        assistant_record = self.append_turn(
            chapter_id,
            role="assistant",
            content=answer,
            thinking=thinking,
        )
        return {
            "reply": answer,
            "thinking": thinking,
            "chapter_id": _safe_chapter_id(chapter_id),
            "inspected_chapter_ids": list(inspection.get("inspect_ids") or []),
            "user_turn": user_record,
            "assistant_turn": assistant_record,
            "history_tail": self.load_history(chapter_id, limit=HISTORY_TAIL),
        }

    def iter_answer_events(
        self,
        chapter_id: str,
        message: str,
        *,
        chapter: dict[str, Any],
        global_project_context: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
        sibling_context: dict[str, Any] | None = None,
        outline_context: dict[str, Any] | None = None,
        writing_orientation: dict[str, Any] | None = None,
    ):
        """Yield NDJSON-ready events: inspect / thinking_delta / content_delta / done."""
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
            sibling_context=sibling_context,
            outline_context=outline_context,
            writing_orientation=writing_orientation,
        )
        safe_id = _safe_chapter_id(chapter_id)
        user_record = self.append_turn(chapter_id, role="user", content=text)
        yield {
            "type": "meta",
            "chapter_id": safe_id,
            "title": str(chat_context.get("title") or ""),
            "user_turn": user_record,
            "disclosure": "titles_first",
        }

        yield {
            "type": "inspect_planning",
            "chapter_id": safe_id,
            "message": "先看目录标题，判断是否需要打开他章详情…",
        }
        inspection = self._resolve_inspections(
            chapter_id=chapter_id,
            outline_context=outline_context or chat_context.get("document_outline_context"),
            task=text,
        )
        chat_context["inspected_chapters"] = list(inspection.get("views") or [])
        if inspection.get("inspect_ids"):
            yield {
                "type": "inspecting",
                "chapter_id": safe_id,
                "inspect_ids": list(inspection.get("inspect_ids") or []),
                "titles": [
                    str(item.get("title") or item.get("chapter_id") or "")
                    for item in (inspection.get("views") or [])
                ],
                "reason": str(inspection.get("reason") or ""),
                "message": (
                    "按需打开只读详情："
                    + "、".join(
                        str(item.get("title") or item.get("chapter_id") or "")
                        for item in (inspection.get("views") or [])
                    )
                ),
            }
        else:
            yield {
                "type": "inspect_skipped",
                "chapter_id": safe_id,
                "reason": str(inspection.get("reason") or "标题树已足够"),
                "message": str(inspection.get("reason") or "仅依据目录标题继续回答。"),
            }

        messages = self._build_messages(chat_context, history, text)
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        try:
            from llm_client import chat_stream_chunks

            for kind, value in chat_stream_chunks(messages, temperature=0.2):
                chunk = str(value or "")
                if not chunk:
                    continue
                if kind == "reasoning":
                    reasoning_parts.append(chunk)
                    yield {
                        "type": "thinking_delta",
                        "chapter_id": safe_id,
                        "delta": chunk,
                    }
                elif kind == "content":
                    content_parts.append(chunk)
                    yield {
                        "type": "content_delta",
                        "chapter_id": safe_id,
                        "delta": chunk,
                    }
        except Exception as exc:
            try:
                from llm_client import chat_with_meta

                meta = chat_with_meta(messages, temperature=0.2)
                answer = str(meta.get("content") or "").strip()
                thinking = str(meta.get("reasoning") or "").strip()
            except Exception:
                answer = self._fallback_answer(chat_context, text)
                thinking = ""
            if thinking:
                reasoning_parts = [thinking]
                yield {
                    "type": "thinking_delta",
                    "chapter_id": safe_id,
                    "delta": thinking,
                }
            if not answer:
                answer = f"请求失败后的降级回复：{exc}"[:500]
            content_parts = [answer]
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
            }

        answer = "".join(content_parts).strip() or "（无回复）"
        thinking = "".join(reasoning_parts).strip()
        assistant_record = self.append_turn(
            chapter_id,
            role="assistant",
            content=answer,
            thinking=thinking,
        )
        yield {
            "type": "done",
            "chapter_id": safe_id,
            "reply": answer,
            "thinking": thinking,
            "inspected_chapter_ids": list(inspection.get("inspect_ids") or []),
            "user_turn": user_record,
            "assistant_turn": assistant_record,
            "turns": self.load_history(chapter_id, limit=HISTORY_TAIL),
        }

    def _resolve_inspections(
        self,
        *,
        chapter_id: str,
        outline_context: dict[str, Any] | None,
        task: str,
    ) -> dict[str, Any]:
        try:
            from .document_outline_context import DocumentOutlineContextService

            return DocumentOutlineContextService(self.context).plan_and_load_inspections(
                viewer_chapter_id=chapter_id,
                outline_context=outline_context,
                task=task,
            )
        except Exception:
            return {
                "inspect_ids": [],
                "reason": "目录检查失败，仅使用标题树。",
                "views": [],
                "decision_source": "error",
            }

    @staticmethod
    def _build_messages(
        chat_context: dict[str, Any],
        history: list[dict[str, Any]],
        user_message: str,
    ) -> list[dict[str, str]]:
        system_prompt = (
            "你是正在编制标书的协作 Agent，当前对话只针对「这一章」。"
            "用自然、直接的中文回答，不复述问题，不说套话。"
            "先根据 writing_orientation 确认：本章写作目的、在整份标书中的位置、"
            "以及与其他章节的关系；回答必须落在本章职责内。"
            "document_outline_context 默认只有目录标题树与状态（titles_first），"
            "不是他章全文；只有 inspected_chapters 中的章节才提供只读详情。"
            "据此理解本章位置与边界；不得改写其他章节，也不得把未 inspect 的章节当成已读正文。"
            "若仍缺关键上游内容，应明确指出还需要哪些目录章节。"
            "不确定就明确缺什么证据或材料。不得把外部信息当企业资质。"
            "你不能直接修改正文或晋级 Artifact，只能给出写作建议与下一步。"
            "请先在模型思考通道中分析写作目的、目录位置、章节关系、是否还缺详情与证据缺口，"
            "再给出面向用户的最终建议。"
        )
        recent = [
            {
                "role": item.get("role"),
                "content": item.get("content"),
            }
            for item in history
            if isinstance(item, dict) and item.get("content")
        ]
        user_payload = {
            "chapter_id": chat_context.get("chapter_id"),
            "chapter_title": chat_context.get("title"),
            "recent_chapter_dialogue": recent,
            "chapter_context": chat_context,
            "inspected_chapters": list(chat_context.get("inspected_chapters") or []),
            "user_message": user_message,
        }
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ]

    @staticmethod
    def _fallback_answer(chat_context: dict[str, Any], message: str) -> str:
        title = str(chat_context.get("title") or chat_context.get("chapter_id") or "当前章节")
        req_n = len(chat_context.get("tender_requirements") or [])
        score_n = len(chat_context.get("scoring_requirements") or [])
        ctx_n = len(chat_context.get("chapter_context_items") or [])
        outline = chat_context.get("document_outline_context")
        outline = outline if isinstance(outline, dict) else {}
        outline_nodes = outline.get("outline") if isinstance(outline.get("outline"), list) else []
        inspected = (
            chat_context.get("inspected_chapters")
            if isinstance(chat_context.get("inspected_chapters"), list)
            else []
        )
        path_label = str((outline.get("position") or {}).get("path_label") or "").strip()
        sibling = chat_context.get("sibling_chapter_context")
        sibling = sibling if isinstance(sibling, dict) else {}
        missing = (
            sibling.get("missing_upstream")
            if isinstance(sibling.get("missing_upstream"), list)
            else []
        )
        parts = [
            f"这是章节「{title}」的专属对话。",
            f"本章已挂接招标要求 {req_n} 条、评分要求 {score_n} 条、专属上下文 {ctx_n} 条。",
            f"整份目录共 {len(outline_nodes)} 个标题节点（默认不预载他章正文）。",
        ]
        if path_label:
            parts.append(f"当前位置：{path_label}。")
        if inspected:
            names = "、".join(
                str(item.get("title") or item.get("chapter_id") or "")
                for item in inspected
                if isinstance(item, dict)
            )
            parts.append(f"已按需打开只读详情：{names}。")
        if missing:
            names = "、".join(
                str(item.get("title") or item.get("chapter_id") or "")
                for item in missing
                if isinstance(item, dict)
            )
            parts.append(
                f"图示/依赖章建议先完成：{names}，再按目录中相关章节骨架写本章。"
            )
        if not chat_context.get("is_leaf"):
            parts.append("当前节点是目录父节点，正文应写在下级叶子章节。")
        elif not chat_context.get("draft_preview"):
            parts.append("本章尚无草稿正文，可先生成初稿再针对段落细化。")
        else:
            parts.append("已有草稿，可继续追问应强调的评分点、交付物或证据缺口。")
        if "材料" in message or "证据" in message:
            parts.append("缺企业资质/案例/产品实绩时，请走 Evidence 补证，不要用外部网页顶替。")
        return " ".join(parts)
