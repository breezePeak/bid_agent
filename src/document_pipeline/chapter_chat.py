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
AUTHORITY_PATH = CHAPTER_CHAT_DIR / "_authority.json"
HISTORY_TAIL = 40
PROMPT_HISTORY_TAIL = 12
DRAFT_PREVIEW_CHARS = 1200
MAX_TURN_CHARS = 20_000
AUTHORITY_MODES = ("human_review", "delegate_review", "full_authority")
DEFAULT_AUTHORITY_MODE = "human_review"
_CONFIRM_RE = re.compile(
    r"^(确认|通过|同意|可以写|按这个写|按此提纲|开始写|审核通过|写吧)([，,。.\s].*)?$"
)
_REJECT_RE = re.compile(r"(不通过|重列|改提纲|提纲不对|重新列)")


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

    def authority_path(self) -> Path:
        return self.context.root / AUTHORITY_PATH

    def load_authority(self, chapter_id: str = "") -> dict[str, Any]:
        path = self.authority_path()
        store = {
            "schema_version": "v3.chapter-chat-authority.v1",
            "default_mode": DEFAULT_AUTHORITY_MODE,
            "chapters": {},
        }
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict):
                store["default_mode"] = _normalize_mode(raw.get("default_mode"))
                chapters = raw.get("chapters")
                if isinstance(chapters, dict):
                    store["chapters"] = chapters
        chapter_id = str(chapter_id or "").strip()
        chapter_row = {}
        if chapter_id:
            try:
                chapter_id = _safe_chapter_id(chapter_id)
            except ControlPlaneError:
                chapter_id = ""
        if chapter_id:
            raw_row = store["chapters"].get(chapter_id)
            chapter_row = dict(raw_row) if isinstance(raw_row, dict) else {}
        mode = _normalize_mode(chapter_row.get("mode") or store["default_mode"])
        return {
            **store,
            "chapter_id": chapter_id or None,
            "mode": mode,
            "review_status": str(chapter_row.get("review_status") or "idle"),
            "outline_hash": str(chapter_row.get("outline_hash") or ""),
            "mode_label": _mode_label(mode),
        }

    def set_authority(
        self,
        *,
        mode: str,
        chapter_id: str = "",
        scope: str = "chapter",
    ) -> dict[str, Any]:
        normalized = _normalize_mode(mode)
        store = self.load_authority()
        if scope == "workspace":
            store["default_mode"] = normalized
        chapter_id = str(chapter_id or "").strip()
        if chapter_id:
            chapter_id = _safe_chapter_id(chapter_id)
            row = store["chapters"].get(chapter_id)
            row = dict(row) if isinstance(row, dict) else {}
            row["mode"] = normalized
            row["review_status"] = "idle"
            row["outline_hash"] = ""
            row["updated_at"] = _utc_now()
            store["chapters"][chapter_id] = row
        self._write_authority_store(store)
        return self.load_authority(chapter_id)

    def decide_outline_review(
        self,
        chapter_id: str,
        *,
        decision: str,
        outline_hash: str = "",
    ) -> dict[str, Any]:
        verdict = str(decision or "").strip().lower()
        if verdict in {"confirm", "approve", "pass"}:
            status = "approved"
        elif verdict in {"reject", "revise"}:
            status = "rejected"
        else:
            raise ControlPlaneError(
                "CHAT_AUTHORITY_INVALID",
                "审核决定只能是 confirm 或 reject。",
                status_code=400,
            )
        return self._update_chapter_review(
            chapter_id,
            review_status=status,
            outline_hash=outline_hash,
        )

    def resolve_write_phase(
        self,
        chapter_id: str,
        *,
        outline: dict[str, Any] | None,
        user_message: str = "",
    ) -> dict[str, Any]:
        """Decide whether to list the outline, wait, or write body."""
        authority = self.load_authority(chapter_id)
        outline = outline if isinstance(outline, dict) else {}
        outline_hash = _outline_hash(outline)
        stored_hash = str(authority.get("outline_hash") or "")
        review_status = str(authority.get("review_status") or "idle")
        if outline_hash and stored_hash and stored_hash != outline_hash:
            review_status = "idle"
        intent = _user_review_intent(user_message)
        mode = str(authority.get("mode") or DEFAULT_AUTHORITY_MODE)
        blocks = [
            item for item in (outline.get("blocks") or []) if isinstance(item, dict)
        ]
        if not blocks:
            return {
                **authority,
                "write_phase": "write_body",
                "review_status": "approved",
                "outline_hash": outline_hash,
                "reason": "没有可审提纲，直接写本章。",
            }

        if mode == "full_authority":
            self._update_chapter_review(
                chapter_id,
                review_status="approved",
                outline_hash=outline_hash,
                mode=mode,
            )
            return {
                **authority,
                "write_phase": "write_body",
                "review_status": "approved",
                "outline_hash": outline_hash,
                "reason": "完全权限：按提纲直接写正文。",
            }

        if intent == "reject":
            self._update_chapter_review(
                chapter_id,
                review_status="rejected",
                outline_hash=outline_hash,
                mode=mode,
            )
            return {
                **authority,
                "write_phase": "list_for_review",
                "review_status": "rejected",
                "outline_hash": outline_hash,
                "reason": "已退回提纲，请按你的意见重列后再审。",
            }

        if mode == "human_review":
            if intent == "confirm" or review_status == "approved":
                self._update_chapter_review(
                    chapter_id,
                    review_status="approved",
                    outline_hash=outline_hash,
                    mode=mode,
                )
                return {
                    **authority,
                    "write_phase": "write_body",
                    "review_status": "approved",
                    "outline_hash": outline_hash,
                    "reason": "你已确认提纲，开始写本章正文。",
                }
            self._update_chapter_review(
                chapter_id,
                review_status="pending",
                outline_hash=outline_hash,
                mode=mode,
            )
            return {
                **authority,
                "write_phase": "list_for_review",
                "review_status": "pending",
                "outline_hash": outline_hash,
                "reason": "先列出本章要写的内容，等你确认后再写正文。",
            }

        # delegate_review
        review = _delegate_review_outline(outline)
        status = "delegated" if review["passed"] else "rejected"
        self._update_chapter_review(
            chapter_id,
            review_status=status,
            outline_hash=outline_hash,
            mode=mode,
        )
        return {
            **authority,
            "write_phase": "write_body" if review["passed"] else "list_for_review",
            "review_status": status,
            "outline_hash": outline_hash,
            "delegate_review": review,
            "reason": review["reason"],
        }

    def render_outline_review(self, chat_context: dict[str, Any]) -> str:
        title = str(chat_context.get("title") or "当前章节")
        purpose = str(chat_context.get("purpose") or "").strip()
        authority = chat_context.get("authority") if isinstance(chat_context.get("authority"), dict) else {}
        outline = chat_context.get("writing_outline") if isinstance(chat_context.get("writing_outline"), dict) else {}
        blocks = [item for item in (outline.get("blocks") or []) if isinstance(item, dict)]
        lines = [f"本章《{title}》准备这样写："]
        if purpose:
            lines.append(f"章节目的：{purpose}")
        for index, item in enumerate(blocks, start=1):
            kind = _kind_label(str(item.get("kind") or ""))
            heading = str(item.get("heading") or f"要点{index}")
            must = str(item.get("must_answer") or "").strip()
            write_as = str(item.get("write_as") or "").strip()
            lines.append(f"{index}. 【{kind}】{heading}")
            if must:
                lines.append(f"   要写清：{must}")
            if write_as:
                lines.append(f"   写法：{write_as}")
        mode = str(authority.get("mode") or DEFAULT_AUTHORITY_MODE)
        if mode == "human_review":
            lines.append("请审核这份提纲。回复「确认」后我按此写正文；要改就直接说改哪一块。")
        elif mode == "delegate_review":
            review = authority.get("delegate_review") if isinstance(authority.get("delegate_review"), dict) else {}
            if review.get("passed"):
                lines.append(f"代审结果：通过。{review.get('reason') or ''}下面按提纲写正文。")
            else:
                lines.append(f"代审结果：未通过。{review.get('reason') or '请先补提纲。'}")
        return "\n".join(lines)

    def require_write_ready(
        self,
        chapter_id: str,
        *,
        outline: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Whether Word draft generation may proceed under the chat authority mode."""
        phase = self.resolve_write_phase(
            chapter_id,
            outline=outline,
            user_message="",
        )
        ready = str(phase.get("write_phase") or "") == "write_body"
        return {
            **phase,
            "ready": ready,
            "code": "" if ready else "CHAPTER_OUTLINE_REVIEW_REQUIRED",
        }

    def _update_chapter_review(
        self,
        chapter_id: str,
        *,
        review_status: str,
        outline_hash: str = "",
        mode: str | None = None,
    ) -> dict[str, Any]:
        store = self.load_authority()
        chapter_id = _safe_chapter_id(chapter_id)
        row = store["chapters"].get(chapter_id)
        row = dict(row) if isinstance(row, dict) else {}
        if mode:
            row["mode"] = _normalize_mode(mode)
        row["review_status"] = str(review_status)
        if outline_hash:
            row["outline_hash"] = outline_hash
        row["updated_at"] = _utc_now()
        store["chapters"][chapter_id] = row
        self._write_authority_store(store)
        return self.load_authority(chapter_id)

    def _write_authority_store(self, store: dict[str, Any]) -> None:
        path = self.authority_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "v3.chapter-chat-authority.v1",
            "default_mode": _normalize_mode(store.get("default_mode")),
            "chapters": store.get("chapters") if isinstance(store.get("chapters"), dict) else {},
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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

        try:
            from .chapter_writing_outline import compile_chapter_writing_outline

            writing_outline = compile_chapter_writing_outline(
                chapter,
                tender_requirements=tender_requirements,
                scoring_requirements=scoring_requirements,
                writing_orientation=orientation_for_agent,
                chapter_context_items=context_items,
            )
        except Exception:
            writing_outline = {}

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
            "writing_outline": writing_outline,
            "role": "bid_chapter_writer",
            "document_outline_context": outline_for_agent,
            "sibling_chapter_context": sibling_for_agent,
            "inspected_chapters": [],
            "draft_preview": draft_preview,
            "authority": self.load_authority(str(chapter.get("chapter_id") or "")),
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
        phase = self.resolve_write_phase(
            chapter_id,
            outline=chat_context.get("writing_outline"),
            user_message=text,
        )
        chat_context["authority"] = phase
        thinking = ""
        if phase.get("write_phase") == "list_for_review":
            answer = self.render_outline_review(chat_context)
        else:
            messages = self._build_messages(chat_context, history, text)
            try:
                from llm_client import chat_with_meta

                meta = chat_with_meta(messages, temperature=0.2)
                answer = str(meta.get("content") or "").strip()
                thinking = str(meta.get("reasoning") or "").strip()
            except Exception:
                answer = self._fallback_answer(chat_context, text)
            if phase.get("mode") == "delegate_review" and phase.get("delegate_review"):
                answer = self.render_outline_review(chat_context) + "\n\n" + answer

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

        phase = self.resolve_write_phase(
            chapter_id,
            outline=chat_context.get("writing_outline"),
            user_message=text,
        )
        chat_context["authority"] = phase
        yield {
            "type": "authority",
            "chapter_id": safe_id,
            "mode": phase.get("mode"),
            "write_phase": phase.get("write_phase"),
            "review_status": phase.get("review_status"),
            "message": str(phase.get("reason") or ""),
        }
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        if phase.get("write_phase") == "list_for_review":
            answer = self.render_outline_review(chat_context)
            content_parts.append(answer)
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
            }
            messages = None
        else:
            messages = self._build_messages(chat_context, history, text)
            prefix = ""
            if phase.get("mode") == "delegate_review" and phase.get("delegate_review", {}).get("passed"):
                prefix = self.render_outline_review(chat_context) + "\n\n"
                content_parts.append(prefix)
                yield {
                    "type": "content_delta",
                    "chapter_id": safe_id,
                    "delta": prefix,
                }
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
                content_parts.append(answer)
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
        title = str(chat_context.get("title") or "当前章节")
        purpose = str(chat_context.get("purpose") or "").strip()
        authority = chat_context.get("authority") if isinstance(chat_context.get("authority"), dict) else {}
        phase = str(authority.get("write_phase") or "write_body")
        mode_label = str(authority.get("mode_label") or _mode_label(authority.get("mode")))
        system_prompt = (
            f"你就是这份投标文件里「{title}」这一章的写作 Agent，不是顾问、不是检查员、不是产品经理。"
            f"当前权限：{mode_label}。"
            "用户在和你讨论本章，你的默认动作是写标书正文，或改本章正文。"
            + (f"本章目的：{purpose}。" if purpose else "")
            + "先按 writing_orientation 确认本章职责和目录位置，再按 writing_outline.blocks 逐块写。"
            "每块写清做法或检查口径、交付物或记录方式；用标书口吻，直接给可粘贴的正文。"
            + (
                "当前阶段是列出提纲等用户审核：只整理本章要写什么，不要写完整正文。"
                if phase == "list_for_review"
                else "当前阶段是写正文：按已确认或已代审的提纲直接写。"
            )
            + "draft_preview 为空时，不要说“建议重新撰写”。"
            "禁止反问“需要我给出框架吗”“要不要我展开”；有材料就写，缺企业证据就用待补表述继续写完。"
            "不要输出满分条件、得分点、评分要求等内部术语。"
            "document_outline_context 只有目录标题；只有 inspected_chapters 才是他章只读详情。"
            "不得改写其他章节，不得把外部网页写成企业资质、业绩或人员。"
            "你在对话里输出的是本章文稿，不会自动写入中间 Word；用户要点「生成草稿」才会落盘。"
            "思考通道里分析本章提纲和材料缺口，面向用户只出标书正文或针对性改稿。"
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
            "role": "bid_chapter_writer",
            "chapter_id": chat_context.get("chapter_id"),
            "chapter_title": chat_context.get("title"),
            "chapter_purpose": chat_context.get("purpose"),
            "writing_outline": chat_context.get("writing_outline") or {},
            "authority": authority,
            "draft_preview": chat_context.get("draft_preview") or "",
            "recent_chapter_dialogue": recent,
            "chapter_context": chat_context,
            "inspected_chapters": list(chat_context.get("inspected_chapters") or []),
            "user_message": user_message,
            "instruction": (
                "若用户在要正文、改写、展开或本章尚无草稿，直接输出本章标书正文；"
                "不要给检查清单，不要征求是否继续写。"
            ),
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
            outline = chat_context.get("writing_outline")
            blocks = (
                outline.get("blocks")
                if isinstance(outline, dict) and isinstance(outline.get("blocks"), list)
                else []
            )
            if blocks:
                parts.append("本章尚无落盘草稿。按提纲可先写：")
                for item in blocks[:6]:
                    if not isinstance(item, dict):
                        continue
                    heading = str(item.get("heading") or "").strip()
                    must = str(item.get("must_answer") or "").strip()
                    if heading:
                        parts.append(f"{heading}：{must}" if must else heading)
            else:
                parts.append("本章尚无落盘草稿，我按本章目的直接起草正文。")
        else:
            parts.append("已有草稿，我按你的要求改本章正文。")
        if "材料" in message or "证据" in message:
            parts.append("缺企业资质/案例/产品实绩时，请走 Evidence 补证，不要用外部网页顶替。")
        return " ".join(parts)


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if mode in AUTHORITY_MODES:
        return mode
    return DEFAULT_AUTHORITY_MODE


def _mode_label(mode: Any) -> str:
    return {
        "human_review": "用户审核",
        "delegate_review": "替我审核",
        "full_authority": "完全权限",
    }.get(_normalize_mode(mode), "用户审核")


def _kind_label(kind: str) -> str:
    return {
        "response": "做法",
        "evidence": "证据",
        "constraint": "约束",
        "quality": "质控",
    }.get(kind, "要点")


def _outline_hash(outline: dict[str, Any]) -> str:
    from .canonicalization import canonical_hash

    blocks = []
    for item in outline.get("blocks") or []:
        if not isinstance(item, dict):
            continue
        blocks.append(
            {
                "kind": item.get("kind"),
                "heading": item.get("heading"),
                "must_answer": item.get("must_answer"),
            }
        )
    return canonical_hash({"blocks": blocks}) if blocks else ""


def _user_review_intent(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if _REJECT_RE.search(text):
        return "reject"
    if _CONFIRM_RE.match(text):
        return "confirm"
    return ""


def _delegate_review_outline(outline: dict[str, Any]) -> dict[str, Any]:
    blocks = [item for item in (outline.get("blocks") or []) if isinstance(item, dict)]
    if not blocks:
        return {"passed": False, "reason": "提纲为空，不能代审通过。"}
    missing = [
        str(item.get("heading") or item.get("block_id") or "未命名")
        for item in blocks
        if not str(item.get("must_answer") or "").strip()
    ]
    if missing:
        return {
            "passed": False,
            "reason": "这些块没有写清要回答什么：" + "、".join(missing[:4]),
        }
    return {
        "passed": True,
        "reason": f"提纲完整，共 {len(blocks)} 块，职责落在本章。",
    }
