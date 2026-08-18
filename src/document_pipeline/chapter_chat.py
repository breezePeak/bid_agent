"""Chapter-scoped collaborative chat (non-authoritative).

Chat turns are workspace control-plane projections, not canonical Artifacts.
Each chapter keeps an isolated append-only history file under:

    workspace/v3/chapter_chats/{chapter_id}.jsonl

The Agent may only read frozen chapter/global context projections; it cannot
write ContentBlock, Blueprint, or other promoted Artifacts.
"""

from __future__ import annotations

import hashlib
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
MAX_DELEGATE_ROUNDS = 2
_RESEARCH_REQUEST_RE = re.compile(
    r"(?:查(?:资料|一下|一查)?|检索|搜索|联网|网上查|帮我找|查找|再搜|重搜|重新搜).{0,24}"
    r"|(?:资料|政策|规范|标准|文件).{0,12}(?:查|检索|搜索|找)",
    re.I,
)
_CONFIRM_RE = re.compile(
    r"^(确认|通过|同意|可以写|按这个写|按此提纲|开始写|审核通过|写吧)([，,。.\s].*)?$"
)
_REJECT_RE = re.compile(r"(不通过|重列|改提纲|提纲不对|重新列)")
_DOCUMENT_WRITE_RE = re.compile(
    r"(写|撰写|生成|开始|填入|写入|放到|改写|重写|重新写|重做|修订).{0,16}"
    r"(正文|本章|章节内容|中间文档|屏幕中间文档|文档中|文档里)"
    r"|(中间文档|屏幕中间文档).{0,8}(写|撰写|生成|填入|写入|放到)"
    r"|(?:草稿|内容).{0,16}(?:改写|重写|重新写|重做|修订)"
    r"|(?:改写|重写|重新写|重做|修订).{0,16}(?:草稿|内容|需求)"
    r"|^(写正文|生成正文|改写|重写|重新写)$"
    r"|(?:改|修改|调整|优化|删掉|删除|替换|补充).{0,12}(?:正文|本章|章节|第\s*[#\d一二三四五六七八九十]+\s*段)"
    r"|(?:正文|本章|章节|第\s*[#\d一二三四五六七八九十]+\s*段).{0,12}(?:改|修改|调整|优化|删掉|删除|替换|补充)"
)
_DOCUMENT_WRITE_NOTICE = "提纲已确认，正文将写入中间文档；对话区只保留进度与结果。"


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
        pre_check = _delegate_review_outline(outline)
        if not pre_check["passed"]:
            # 快速预检不通过，不消耗 LLM token
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
                "delegate_review": pre_check,
                "reason": pre_check["reason"],
            }
        # LLM 深度审核放到流式路径中执行（resolve_write_phase 本身只做快速判断）
        # 如果预检通过且之前有 delegated 状态，直接走 write_body
        if review_status in {"delegated", "approved"}:
            return {
                **authority,
                "write_phase": "write_body",
                "review_status": "delegated",
                "outline_hash": outline_hash,
                "delegate_review": {"passed": True, "reason": "已通过代审。"},
                "reason": "已通过代审，按提纲写正文。",
            }
        # 首次进入 delegate_review：标记为 pending_delegate，在流式路径中触发 LLM 审核
        self._update_chapter_review(
            chapter_id,
            review_status="pending_delegate",
            outline_hash=outline_hash,
            mode=mode,
        )
        return {
            **authority,
            "write_phase": "list_for_review",
            "review_status": "pending_delegate",
            "outline_hash": outline_hash,
            "reason": "提纲预检通过，正在进行 AI 深度审核…",
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
                issues = review.get("issues") or []
                if issues:
                    lines.append("\n问题清单：")
                    for i, issue in enumerate(issues, 1):
                        heading = str(issue.get("heading") or f"第{issue.get('block_index', '?')}块")
                        problem = str(issue.get("issue") or "")
                        suggestion = str(issue.get("suggestion") or "")
                        lines.append(f"  {i}. {heading}：{problem}")
                        if suggestion:
                            lines.append(f"     建议：{suggestion}")
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

    def delete_turn(
        self,
        chapter_id: str,
        *,
        turn_id: str = "",
        created_at: str = "",
        role: str = "",
    ) -> None:
        """Permanently remove one chapter-local collaboration turn."""
        wanted_id = str(turn_id or "").strip()
        wanted_created = str(created_at or "").strip()
        wanted_role = str(role or "").strip()
        if not wanted_id and not (wanted_created and wanted_role):
            raise ControlPlaneError(
                "CHAT_TURN_INVALID",
                "删除消息需要 turn_id 或创建时间和角色。",
                status_code=400,
            )
        turns = self.load_history(chapter_id, limit=0)
        match_index = next(
            (
                index
                for index, item in enumerate(turns)
                if (wanted_id and str(item.get("turn_id") or "") == wanted_id)
                or (
                    not wanted_id
                    and str(item.get("created_at") or "") == wanted_created
                    and str(item.get("role") or "") == wanted_role
                )
            ),
            -1,
        )
        if match_index < 0:
            raise ControlPlaneError(
                "CHAT_TURN_NOT_FOUND",
                "未找到要删除的历史消息。",
                status_code=404,
            )
        del turns[match_index]
        self._write_history(chapter_id, turns)

    def clear_history(self, chapter_id: str) -> int:
        """Permanently remove all chapter-local collaboration turns."""
        turns = self.load_history(chapter_id, limit=0)
        deleted_count = len(turns)
        self._write_history(chapter_id, [])
        return deleted_count

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
        # GlobalProjectContextService exposes the canonical key as ``identity``.
        # Looking only for ``project_identity`` left the chapter agent without
        # the project name or any substantive project facts.
        identity = gpc.get("identity") if isinstance(gpc.get("identity"), dict) else {}
        if not identity and isinstance(gpc.get("project_identity"), dict):
            identity = dict(gpc["project_identity"])

        def _context_list(key: str, limit: int = 40) -> list[Any]:
            values = gpc.get(key)
            return list(values[:limit]) if isinstance(values, list) else []

        confirmed_facts = []
        for item in _context_list("confirmed_facts", 40):
            if isinstance(item, dict):
                statement = str(item.get("statement") or item.get("value") or "").strip()
                if statement:
                    confirmed_facts.append(
                        {
                            "fact_id": str(item.get("fact_id") or ""),
                            "statement": statement,
                            "source_ids": list(item.get("source_ids") or [])[:8],
                        }
                    )
        shared_facts = {
            "global_context_revision": gpc.get("global_context_revision"),
            "project_name": identity.get("project_name") or gpc.get("project_name"),
            "buyer": identity.get("buyer") or gpc.get("buyer"),
            "identity": identity,
            "background": _context_list("background"),
            "goals": _context_list("goals"),
            "scope": _context_list("scope"),
            "boundaries": _context_list("boundaries"),
            "work_packages": _context_list("work_packages"),
            "inputs": _context_list("inputs"),
            "outputs": _context_list("outputs"),
            "deliverables": _context_list("deliverables"),
            "constraints": _context_list("constraints"),
            "confirmed_facts": confirmed_facts,
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

        # Project global context through the chapter's compiled purpose and
        # blocks before exposing it to the model.  This is deliberately
        # content-driven: chapter titles never choose a fact set.
        shared_facts = _project_shared_facts(
            shared_facts,
            purpose=str(
                (orientation_for_agent.get("writing_purpose") or {}).get("purpose")
                or node.get("purpose")
                or node.get("response_purpose")
                or ""
            ),
            outline=writing_outline,
            tender_requirements=tender_requirements,
            scoring_requirements=scoring_requirements,
        )

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

    # Non-streaming callers consume the exact same event producer as the
    # streaming endpoint.  Keep this adapter next to the producer so phase,
    # research, delegation and fallback behavior cannot drift.
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
        events = list(
            self.iter_answer_events(
                chapter_id,
                message,
                chapter=chapter,
                global_project_context=global_project_context,
                tender_requirements=tender_requirements,
                scoring_requirements=scoring_requirements,
                sibling_context=sibling_context,
                outline_context=outline_context,
                writing_orientation=writing_orientation,
            )
        )
        done = next(
            (item for item in reversed(events) if item.get("type") == "done"),
            None,
        )
        if done is None:
            raise ControlPlaneError(
                "CHAT_TURN_FAILED",
                "Chapter chat did not return a done event.",
                status_code=500,
            )
        return {
            "reply": done.get("reply") or "",
            "thinking": done.get("thinking") or "",
            "chapter_id": done.get("chapter_id") or _safe_chapter_id(chapter_id),
            "inspected_chapter_ids": list(done.get("inspected_chapter_ids") or []),
            "user_turn": done.get("user_turn"),
            "assistant_turn": done.get("assistant_turn"),
            "history_tail": done.get("turns") or self.load_history(
                chapter_id, limit=HISTORY_TAIL
            ),
            "document_write_requested": bool(done.get("document_write_requested")),
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

        # Keep every progress message that is shown in the UI in the persisted
        # assistant turn as well.  The client replaces its temporary streamed
        # turn with `turns` from the done event, so omitting these notes here
        # made the just-displayed reasoning disappear at completion.
        reasoning_parts: list[str] = []
        content_parts: list[str] = []

        inspection = self._resolve_inspections(
            chapter_id=chapter_id,
            outline_context=outline_context or chat_context.get("document_outline_context"),
            task=text,
        )
        chat_context["inspected_chapters"] = list(inspection.get("views") or [])
        if inspection.get("inspect_ids"):
            inspection_note = (
                "按需打开只读详情："
                + "、".join(
                    str(item.get("title") or item.get("chapter_id") or "")
                    for item in (inspection.get("views") or [])
                )
            )
            reasoning_parts.append(f"{inspection_note}\n")
            yield {
                "type": "inspecting",
                "chapter_id": safe_id,
                "inspect_ids": list(inspection.get("inspect_ids") or []),
                "titles": [
                    str(item.get("title") or item.get("chapter_id") or "")
                    for item in (inspection.get("views") or [])
                ],
                "reason": str(inspection.get("reason") or ""),
                "message": inspection_note,
            }

        research = self._research_for_message(chapter_id, text, chat_context)
        if research:
            chat_context["research"] = research
            research_note = str(research["message"])
            reasoning_parts.append(f"{research_note}\n")
            yield {
                "type": "research",
                "chapter_id": safe_id,
                "status": research["status"],
                "message": research_note,
                "sources": research["sources"],
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
            "document_write_requested": _requests_document_write(text, phase),
        }
        # delegate review: LLM 深度审核 + 定向修改循环
        if phase.get("review_status") == "pending_delegate":
            delegate_round = 0
            current_outline = chat_context.get("writing_outline") or {}
            while delegate_round < MAX_DELEGATE_ROUNDS:
                delegate_round += 1
                review_note = f"审核 Agent 正在审核提纲（第 {delegate_round} 轮）…"
                reasoning_parts.append(f"{review_note}\n")
                yield {
                    "type": "delegate_reviewing",
                    "chapter_id": safe_id,
                    "round": delegate_round,
                    "message": review_note,
                }
                review_result = None
                for kind, data in _llm_delegate_review_outline_stream(
                    current_outline, chat_context,
                ):
                    if kind == "thinking_delta":
                        reasoning_parts.append(str(data))
                        yield {
                            "type": "thinking_delta",
                            "chapter_id": safe_id,
                            "delta": data,
                        }
                    elif kind == "result":
                        review_result = data
                if review_result is None:
                    review_result = {"passed": True, "reason": "审核完成，未发现问题。"}
                
                if review_result.get("passed"):
                    # 审核通过
                    self._update_chapter_review(
                        chapter_id,
                        review_status="delegated",
                        outline_hash=_outline_hash(current_outline),
                        mode="delegate_review",
                    )
                    phase["review_status"] = "delegated"
                    phase["write_phase"] = "write_body"
                    phase["delegate_review"] = review_result
                    chat_context["authority"] = phase
                    yield {
                        "type": "authority",
                        "chapter_id": safe_id,
                        "mode": "delegate_review",
                        "write_phase": "write_body",
                        "review_status": "delegated",
                        "message": f"代审通过：{review_result.get('reason', '')}",
                        "document_write_requested": _requests_document_write(text, phase),
                    }
                    break
                else:
                    # 审核不通过，尝试定向修改
                    issues = review_result.get("issues") or []
                    issues_note = f"发现 {len(issues)} 个问题，正在定向修改…"
                    reasoning_parts.append(f"{issues_note}\n")
                    yield {
                        "type": "delegate_reviewing",
                        "chapter_id": safe_id,
                        "round": delegate_round,
                        "status": "issues_found",
                        "issues": issues,
                        "message": issues_note,
                    }
                    if not issues or delegate_round >= MAX_DELEGATE_ROUNDS:
                        # 没有具体 issues 或已达上限，降级为人工审核
                        self._update_chapter_review(
                            chapter_id,
                            review_status="rejected",
                            outline_hash=_outline_hash(current_outline),
                            mode="delegate_review",
                        )
                        phase["review_status"] = "rejected"
                        phase["write_phase"] = "list_for_review"
                        phase["delegate_review"] = review_result
                        chat_context["authority"] = phase
                        yield {
                            "type": "authority",
                            "chapter_id": safe_id,
                            "mode": "delegate_review",
                            "write_phase": "list_for_review",
                            "review_status": "rejected",
                            "message": f"代审未通过（{delegate_round} 轮后）：{review_result.get('reason', '')}。请人工处理。",
                        }
                        break
                    # 定向修改
                    fixing_note = f"正在根据审核意见修改提纲（第 {delegate_round} 轮）…"
                    reasoning_parts.append(f"{fixing_note}\n")
                    yield {
                        "type": "delegate_fixing",
                        "chapter_id": safe_id,
                        "round": delegate_round,
                        "message": fixing_note,
                    }
                    for kind, data in self._apply_delegate_fixes_stream(
                        chapter_id, current_outline, issues, chat_context,
                    ):
                        if kind == "thinking_delta":
                            reasoning_parts.append(str(data))
                            yield {
                                "type": "thinking_delta",
                                "chapter_id": safe_id,
                                "delta": data,
                            }
                        elif kind == "result":
                            current_outline = data
                            chat_context["writing_outline"] = current_outline

        document_write_requested = _requests_document_write(text, phase)
        if document_write_requested:
            answer = _DOCUMENT_WRITE_NOTICE
            content_parts.append(answer)
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
                "content_kind": "status",
            }
            messages = None
        elif phase.get("write_phase") == "list_for_review":
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
            "document_write_requested": document_write_requested,
        }

    def _apply_delegate_fixes(
        self,
        chapter_id: str,
        outline: dict[str, Any],
        issues: list[dict[str, Any]],
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        """根据审核 agent 的建议，定向修改提纲中有问题的 block。
        返回修改后的 outline。
        """
        system_prompt = (
            "你是投标写作 Agent。当前正根据审核专家的意见修改提纲中的特定块。\n"
            "以下是目前的提纲和需要修改的问题清单（issues）。\n"
            "请仔细阅读，并**只针对有问题清单的 block** 提出修改。其余 block 原样保留。\n"
            "输出请返回一个只包含修改后的 blocks 数组的 JSON，比如：\n"
            "{\"blocks\": [ ... ]}"
        )
        user_message = json.dumps(
            {
                "outline": outline,
                "issues": issues,
                "tender_requirements": chat_context.get("tender_requirements"),
                "scoring_requirements": chat_context.get("scoring_requirements"),
            },
            ensure_ascii=False
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        try:
            from llm_client import chat_with_meta
            meta = chat_with_meta(messages, temperature=0.2)
            content = str(meta.get("content") or "").strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            parsed = json.loads(content.strip())
            new_blocks = parsed.get("blocks")
            if isinstance(new_blocks, list):
                new_outline = dict(outline)
                new_outline["blocks"] = new_blocks
                return new_outline
        except Exception:
            pass
        return outline

    def _apply_delegate_fixes_stream(
        self,
        chapter_id: str,
        outline: dict[str, Any],
        issues: list[dict[str, Any]],
        chat_context: dict[str, Any],
    ):
        """流式版本，yield (kind, data) tuples。"""
        system_prompt = (
            "你是投标写作 Agent。当前正根据审核专家的意见修改提纲中的特定块。\n"
            "以下是目前的提纲和需要修改的问题清单（issues）。\n"
            "请仔细阅读，并**只针对有问题清单的 block** 提出修改。其余 block 可以在保持原样的前提下加入返回结果中。\n"
            "必须返回包含所有 blocks (修改的及未修改的) 的 JSON 格式，如下：\n"
            "{\"blocks\": [ ... ]}\n"
            "不要输出多余解释。"
        )
        user_message = json.dumps(
            {
                "outline": outline,
                "issues": issues,
                "tender_requirements": chat_context.get("tender_requirements"),
                "scoring_requirements": chat_context.get("scoring_requirements"),
            },
            ensure_ascii=False
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        content_parts = []
        try:
            from llm_client import chat_stream_chunks
            for kind, value in chat_stream_chunks(messages, temperature=0.2):
                chunk = str(value or "")
                if not chunk:
                    continue
                if kind == "reasoning":
                    yield "thinking_delta", chunk
                elif kind == "content":
                    content_parts.append(chunk)
            
            content = "".join(content_parts).strip()
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            parsed = json.loads(content.strip())
            new_blocks = parsed.get("blocks")
            if isinstance(new_blocks, list):
                new_outline = dict(outline)
                new_outline["blocks"] = new_blocks
                yield "result", new_outline
                return
        except Exception:
            pass
        yield "result", outline

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

    def _research_for_message(
        self,
        chapter_id: str,
        message: str,
        chat_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run the bounded research tool when the user explicitly asks for it."""
        if not _RESEARCH_REQUEST_RE.search(message):
            return None
        from .chapter_research_planner import plan_chapter_research
        from .contracts import EvidenceNeed
        from .research_adapters import create_research_adapter
        from .research_service import ResearchService

        title = str(chat_context.get("title") or chapter_id).strip()
        purpose = str(chat_context.get("purpose") or "").strip()
        requirements = [
            str(item.get("normalized_requirement") or item.get("requirement") or "").strip()
            for item in list(chat_context.get("tender_requirements") or [])[:3]
            if isinstance(item, dict)
        ]
        research_chapter = {
            "chapter_id": _safe_chapter_id(chapter_id),
            "title": title,
            "blueprint_node": {"title": title, "purpose": purpose},
            "context": {"items": list(chat_context.get("chapter_context_items") or [])},
        }
        shared = chat_context.get("shared_project_facts")
        shared = shared if isinstance(shared, dict) else {}
        research_plan = plan_chapter_research(
            research_chapter,
            project_context={
                "identity": {
                    key: value
                    for key, value in {
                        "project_name": shared.get("project_name"),
                        "buyer": shared.get("buyer"),
                    }.items()
                    if str(value or "").strip()
                },
                "scope": list(shared.get("scope") or []),
            },
            sibling_context=chat_context.get("sibling_chapter_context"),
            writing_orientation=chat_context.get("writing_orientation"),
            inspected_chapters=chat_context.get("inspected_chapters"),
            tender_requirements=chat_context.get("tender_requirements"),
            scoring_requirements=chat_context.get("scoring_requirements"),
            instruction=message,
            force_research=True,
        )
        if not research_plan.get("need_research"):
            return {
                "status": "skipped",
                "message": str(research_plan.get("reason") or "章节 Agent 判断现有资料足够，无需公开检索。"),
                "sources": [],
            }
        question_parts = [
            f"请检索与投标章节“{title}”直接相关、可核验的公开资料。",
            "优先采用政府部门、标准发布机构和采购人官网来源；逐项给出标题、发布机构、摘要和 URL。",
            "仅补充项目背景、政策标准、技术方法或实施依据；不得推断投标企业资质、业绩、人员、报价或承诺。",
        ]
        if purpose:
            question_parts.append(f"章节目的：{purpose}")
        if requirements:
            question_parts.append("关联要求：" + "；".join(requirements))
        # A terse command such as “你去查资料啊” must become a usable,
        # chapter-scoped query instead of being sent verbatim to the provider.
        if len(message) > 12:
            question_parts.append(f"用户关注点：{message}")
        question = str(research_plan.get("search_query") or "").strip() or "\n".join(question_parts)
        digest = hashlib.sha256(f"{chapter_id}:{question}".encode("utf-8")).hexdigest()[:16]
        need = EvidenceNeed(
            need_id=f"EN-CHAT-{digest}",
            question=question,
            topic_id=f"chapter-chat:{_safe_chapter_id(chapter_id)}",
            priority="high",
            blocking_scope="none",
            deadline_stage="chapter_chat",
            query_budget=3,
            task_anchors=[title] if title else [],
            relevance_context={
                "chapter_title": title,
                "chapter_purpose": purpose,
                "tender_requirements": requirements,
                "project_scope": list(shared.get("scope") or []),
            },
            max_adopted_items=3,
        )
        try:
            batch = ResearchService(self.context, create_research_adapter()).resolve(
                need,
                force_refresh=True,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "message": f"已发起本章公开资料检索，但检索工具未完成：{str(exc)[:240]}",
                "sources": [],
            }
        sources = [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "publisher": item.publisher,
                "source_url": item.source_url,
                "excerpt": item.supporting_excerpt or item.content[:500],
                "extracted_points": list(item.extracted_points or []),
                "relevance_reason": item.relevance_reason,
                "relevance_confidence": item.relevance_confidence,
                "usage_category": item.usage_category,
            }
            for item in batch.items
        ]
        if batch.status == "published" and sources:
            message_text = f"已完成本章公开资料检索，找到 {len(sources)} 条可采用来源。"
        elif batch.status == "gap":
            message_text = "已完成本章公开资料检索，但没有通过关联性和可核验筛选的来源。"
        else:
            message_text = f"本章公开资料检索未完成：{str(batch.error or '检索服务不可用')[:240]}"
        return {"status": batch.status, "message": message_text, "sources": sources}

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
            "用户在和你讨论本章：提问就直接回答，要求评判就对照 draft_preview 指出具体问题；"
            "只有明确索要可粘贴文本时才在对话中给文本。"
            + (f"本章目的：{purpose}。" if purpose else "")
            + "先按 writing_orientation 确认本章职责和目录位置，再按 writing_outline.blocks 逐块写。"
            "每块写清做法或检查口径、交付物或记录方式；用标书口吻，直接给可粘贴的正文。"
            + (
                "当前阶段是列出提纲等用户审核：只整理本章要写什么，不要写完整正文。"
                if phase == "list_for_review"
                else "当前阶段是写正文：按已确认或已代审的提纲直接写。"
            )
            + "draft_preview 为空时要明确说明当前没有可供检查的正文，不得假装看过正文。"
            "禁止反问“需要我给出框架吗”“要不要我展开”；有材料就写，缺企业证据就用待补表述继续写完。"
            "不要输出满分条件、得分点、评分要求等内部术语。"
            "document_outline_context 只有目录标题；只有 inspected_chapters 才是他章只读详情。"
            "不得改写其他章节，不得把外部网页写成企业资质、业绩或人员。"
            "提纲确认或用户明确要求写正文时，正文必须交给中间文档写作流落盘；对话区只回复进度和结果，不粘贴完整正文。"
            "本对话模型本身不能修改中间文档；只有上游标记 document_write_requested 后才会触发写作流。"
            "当前能收到本提示就表示本轮没有触发写作流，因此绝对禁止声称‘已修改’、‘已覆盖’、‘已写入’、‘已提交’或‘已完成正文’。"
            "思考通道里分析本章提纲和材料缺口；面向用户按其实际问题给出具体、可核对的回答。"
        )
        research = chat_context.get("research") if isinstance(chat_context.get("research"), dict) else {}
        if research and research.get("status") != "skipped":
            system_prompt += (
                "用户已明确要求查资料，系统已实际调用公开资料检索工具。"
                "只能基于 research.sources 中返回的来源描述检索结果，不得声称自己没有检索工具，"
                "也不得编造未返回的政策名称、文号、年份或链接。"
            )
        elif research:
            system_prompt += (
                "检索规划器已判断现有资料足以回答本章；如解释该决定，只能依据 research.message，"
                "不得捏造未检索的外部来源。"
            )
        # Keep the generic outline guidance narrow.  Block-level details such
        # as procedures, deliverables, acceptance or ownership are included
        # only when the compiled outline explicitly asks for them.
        system_prompt += (
            "Answer each block's must_answer in order and follow its write_as. "
            "Do not add procedures, deliverables, records, acceptance criteria, "
            "inputs/outputs or role assignments unless the outline asks for them. "
            "A prior assistant reply is non-authoritative and may be wrong. "
            "When the user criticizes prior dialogue, address that dialogue directly. "
            "Only inspect draft_preview when the user explicitly asks about the current draft."
        )

        # Assistant history is a conversation record only.  It is never a
        # project fact or an instruction: an earlier answer may be wrong and
        # must not reinforce itself on the next turn.
        recent = [
            {
                "role": item.get("role"),
                "content": item.get("content"),
                "authority": "non_authoritative"
                if item.get("role") == "assistant"
                else "user_instruction",
            }
            for item in history
            if isinstance(item, dict) and item.get("content")
        ]
        draft_requested = _is_draft_inspection_request(user_message)
        history_critique = _is_history_critique_request(user_message)
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
            "research": research,
            "draft_inspection_requested": draft_requested,
            "history_critique_requested": history_critique,
            "history_is_non_authoritative": True,
            "user_message": user_message,
            "instruction": (
                "忠实回答 user_message。若用户是在追问、质疑或检查现有内容，必须引用 draft_preview 的具体内容回答；"
                "若 draft_preview 为空则如实说明。不得声称已经修改或写入文档。"
            ),
        }
        # Replace the legacy broad instruction above with the explicit
        # distinction used by the current routing contract.
        user_payload["instruction"] += (
            "Answer user_message faithfully. Inspect draft_preview only when "
            "draft_inspection_requested is true; if it is empty, say no current "
            "draft is available. If history_critique_requested is true, address "
            "the prior dialogue directly and treat assistant history as non-authoritative. "
            "Never claim that a document was changed unless document_write_requested is true."
        )
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


def _text_terms(value: Any) -> set[str]:
    """Return language-neutral search terms for generic fact projection."""
    text = str(value or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", text))
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    terms.update("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
    return {term for term in terms if term.strip()}


def _project_shared_facts(
    shared: dict[str, Any],
    *,
    purpose: str,
    outline: dict[str, Any],
    tender_requirements: list[dict[str, Any]] | None,
    scoring_requirements: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Keep only global facts that can support this chapter's declared goal.

    Identity metadata is retained for display.  Content lists are projected
    by lexical relevance to purpose/objectives/blocks, with no chapter-title
    or topic-specific branches.  If no target terms are available, preserve
    the source projection rather than silently dropping all evidence.
    """
    target_parts: list[str] = [purpose]
    if isinstance(outline, dict):
        for block in outline.get("blocks") or []:
            if isinstance(block, dict):
                target_parts.extend(
                    str(block.get(key) or "")
                    for key in ("heading", "must_answer", "write_as", "outcome_kind")
                )
    for requirement in list(tender_requirements or [])[:20] + list(scoring_requirements or [])[:20]:
        if isinstance(requirement, dict):
            target_parts.append(
                str(
                    requirement.get("normalized_requirement")
                    or requirement.get("requirement")
                    or requirement.get("text")
                    or ""
                )
            )
    target_terms = _text_terms(" ".join(target_parts))

    def relevant(value: Any) -> bool:
        if isinstance(value, dict):
            value_text = " ".join(str(item or "") for item in value.values())
        else:
            value_text = str(value or "")
        if not value_text.strip() or not target_terms:
            return True
        candidate_terms = _text_terms(value_text)
        # Untagged primitive context values are already the canonical compact
        # projection supplied by the context service.  Keep them for
        # backwards compatibility; apply lexical filtering to structured
        # facts where scope/role metadata is available.
        if not isinstance(value, dict):
            return True
        return bool(candidate_terms & target_terms)

    projected = dict(shared)
    for key in (
        "background",
        "goals",
        "scope",
        "boundaries",
        "work_packages",
        "inputs",
        "outputs",
        "deliverables",
        "constraints",
    ):
        values = shared.get(key)
        if isinstance(values, list):
            selected = [item for item in values if relevant(item)]
            projected[key] = selected if selected or not values else values
    facts = shared.get("confirmed_facts")
    if isinstance(facts, list):
        selected_facts = [item for item in facts if relevant(item)]
        projected["confirmed_facts"] = (
            selected_facts if selected_facts or not facts else facts
        )
        projected["confirmed_fact_count"] = len(projected["confirmed_facts"])
    projected["projection"] = {
        "basis": "purpose_objectives_outline_requirements",
        "target_term_count": len(target_terms),
    }
    return projected


def _is_draft_inspection_request(message: str) -> bool:
    text = str(message or "").lower()
    markers = (
        "draft",
        "preview",
        "正文",
        "草稿",
        "段落",
        "第1段",
        "第2段",
        "第3段",
        "paragraph",
        "current copy",
    )
    return any(marker in text for marker in markers)


def _is_history_critique_request(message: str) -> bool:
    text = str(message or "").lower()
    markers = (
        "history",
        "previous",
        "prior",
        "assistant",
        "dialogue",
        "对话",
        "回复",
        "刚才",
        "之前",
        "为什么又",
        "怎么又",
        "仍然",
    )
    return any(marker in text for marker in markers)


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


def _requests_document_write(message: str, phase: dict[str, Any]) -> bool:
    """Route explicit body-writing requests to the document writer, not chat."""
    if str(phase.get("write_phase") or "") != "write_body":
        return False
    text = str(message or "").strip()
    return _user_review_intent(text) == "confirm" or bool(_DOCUMENT_WRITE_RE.search(text))


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


def _llm_delegate_review_outline(
    outline: dict[str, Any],
    chat_context: dict[str, Any],
) -> dict[str, Any]:
    """用 LLM agent 替用户审核提纲，返回审核结果与修改建议。"""
    pre_check = _delegate_review_outline(outline)
    if not pre_check["passed"]:
        return pre_check
    
    system_prompt = (
        "你是投标文件的审核专家。你要替用户审核本章写作提纲，判断提纲是否可以直接写正文。\n"
        "审核维度：\n"
        "1. 提纲是否完整覆盖了招标要求和评分要求中与本章相关的内容\n"
        "2. 各要点块的 must_answer 是否准确、具体，不空泛\n"
        "3. 各要点块之间是否有逻辑连贯性，不重复、不遗漏\n"
        "4. write_as 写法建议是否合理\n\n"
        "只输出 JSON，不要输出其他内容。如果提纲整体没问题，passed=true。\n"
        "如果存在需要修改的问题，passed=false，并在 issues 数组中列出每个有问题的块。\n"
        "返回格式要求：\n"
        "{\"passed\": bool, \"reason\": str, \"issues\": [{\"block_index\": int, \"heading\": str, \"issue\": str, \"suggestion\": str}]}"
    )
    user_message = json.dumps(
        {
            "outline_blocks": outline.get("blocks"),
            "tender_requirements": chat_context.get("tender_requirements"),
            "scoring_requirements": chat_context.get("scoring_requirements"),
        },
        ensure_ascii=False
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    try:
        from llm_client import chat_with_meta
        meta = chat_with_meta(messages, temperature=0.2)
        content = str(meta.get("content") or "").strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        parsed = json.loads(content.strip())
        if "passed" in parsed:
            return parsed
    except Exception:
        pass
    return pre_check


def _llm_delegate_review_outline_stream(
    outline: dict[str, Any],
    chat_context: dict[str, Any],
):
    """流式版本：yield (kind, data) tuples。
    kind: 'thinking_delta' | 'result'
    """
    pre_check = _delegate_review_outline(outline)
    if not pre_check["passed"]:
        yield "result", pre_check
        return

    system_prompt = (
        "你是投标文件的审核专家。你要替用户审核本章写作提纲，判断提纲是否可以直接写正文。\n"
        "审核维度：\n"
        "1. 提纲是否完整覆盖了招标要求和评分要求中与本章相关的内容\n"
        "2. 各要点块的 must_answer 是否准确、具体，不空泛\n"
        "3. 各要点块之间是否有逻辑连贯性，不重复、不遗漏\n"
        "4. write_as 写法建议是否合理\n\n"
        "只输出 JSON，不要输出其他内容。如果提纲整体没问题，passed=true。\n"
        "如果存在需要修改的问题，passed=false，并在 issues 数组中列出每个有问题的块。\n"
        "返回格式要求：\n"
        "{\"passed\": bool, \"reason\": str, \"issues\": [{\"block_index\": int, \"heading\": str, \"issue\": str, \"suggestion\": str}]}"
    )
    user_message = json.dumps(
        {
            "outline_blocks": outline.get("blocks"),
            "tender_requirements": chat_context.get("tender_requirements"),
            "scoring_requirements": chat_context.get("scoring_requirements"),
        },
        ensure_ascii=False
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    content_parts = []
    try:
        from llm_client import chat_stream_chunks
        for kind, value in chat_stream_chunks(messages, temperature=0.2):
            chunk = str(value or "")
            if not chunk:
                continue
            if kind == "reasoning":
                yield "thinking_delta", chunk
            elif kind == "content":
                content_parts.append(chunk)
        
        content = "".join(content_parts).strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        parsed = json.loads(content.strip())
        if "passed" in parsed:
            yield "result", parsed
            return
    except Exception:
        pass
    yield "result", pre_check
