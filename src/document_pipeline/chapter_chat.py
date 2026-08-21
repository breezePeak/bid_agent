"""Chapter-scoped Agent with one continuous dialogue and writing memory.

Chat turns are workspace control-plane projections, not canonical Artifacts.
Each chapter keeps an isolated append-only history file under:

    workspace/v3/chapter_chats/{chapter_id}.jsonl

The Agent reads frozen chapter/global context projections and may invoke the
controlled chapter-writing tool.  Blueprint remains read-only; draft writes
still pass through the command gateway and content gates.
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

from .chapter_writing_kernel import compile_chapter_writing_spec
from .input_manifest import V3_ROOT

CHAPTER_CHAT_DIR = V3_ROOT / "chapter_chats"
WRITING_PLAN_PATH = CHAPTER_CHAT_DIR / "_writing_plans.json"
HISTORY_TAIL = 40
PROMPT_HISTORY_TAIL = 12
DRAFT_PREVIEW_CHARS = 1200
MAX_TURN_CHARS = 20_000
_RESEARCH_REQUEST_RE = re.compile(
    r"(?:查(?:资料|一下|一查)?|检索|搜索|联网|网上查|帮我找|查找|再搜|重搜|重新搜).{0,24}"
    r"|(?:资料|政策|规范|标准|文件).{0,12}(?:查|检索|搜索|找)",
    re.I,
)
_DIRECT_BODY_WRITE_RE = re.compile(
    r"(?:开始|继续|立即|直接)?(?:编写|撰写|写)(?:本章|这一章|正文)"
    r"|按(?:这个|此|上述)(?:计划|思路|提纲)?写"
    r"|(?:继续写|重新写|重写|改具体|写具体|写得太空|写得空|分别写清楚|修改正文)"
)
_SHOW_WRITING_PLAN_RE = re.compile(
    r"(?:先)?(?:看看|看下|列出|列一下|展示).{0,12}(?:怎么写|写作思路|计划|提纲)"
    r"|这章准备怎么写|先列一下写作思路"
)
_PLAN_REVISION_RE = re.compile(r"(?:第[一二三四五六七八九十\d]+点|第[一二三四五六七八九十\d]+项|提纲|计划).{0,24}(?:删|改|细|具体|简单|拆)")
_DOCUMENT_WRITE_NOTICE = "正文将通过统一章节写作服务写入中间文档；对话区只保留进度与结果。"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_chapter_id(chapter_id: str) -> str:
    return ControlStore._normalize_chapter_id(chapter_id)


class ChapterAgentService:
    """The single persistent Agent for one chapter's dialogue and document work."""

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

    def writing_plan_path(self) -> Path:
        return self.context.root / WRITING_PLAN_PATH

    def load_writing_plan_state(self, chapter_id: str = "") -> dict[str, Any]:
        path = self.writing_plan_path()
        store = {
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapters": {},
        }
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict):
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
        return {
            **store,
            "chapter_id": chapter_id or None,
            "writing_plan": (
                dict(chapter_row.get("writing_plan"))
                if isinstance(chapter_row.get("writing_plan"), dict)
                else None
            ),
            "writing_plan_source": str(chapter_row.get("source") or ""),
        }

    def save_writing_plan(
        self,
        chapter_id: str,
        writing_plan: dict[str, Any],
        *,
        source: str = "generated",
    ) -> dict[str, Any]:
        store = self.load_writing_plan_state()
        chapter_id = _safe_chapter_id(chapter_id)
        store["chapters"][chapter_id] = {
            "writing_plan": dict(writing_plan),
            "source": str(source or "generated"),
            "updated_at": _utc_now(),
        }
        self._write_writing_plan_store(store)
        return self.load_writing_plan_state(chapter_id)

    def reset_writing_plan(self, chapter_id: str) -> None:
        store = self.load_writing_plan_state()
        store["chapters"].pop(_safe_chapter_id(chapter_id), None)
        self._write_writing_plan_store(store)

    def resolve_write_phase(
        self,
        chapter_id: str,
        *,
        outline: dict[str, Any] | None,
        agent_action: str = "respond_only",
    ) -> dict[str, Any]:
        """Route directly between internal-plan display and body writing."""
        plan_state = self.load_writing_plan_state(chapter_id)
        action = _normalize_chapter_action(agent_action)
        if action == "write_document":
            phase = "write_body"
            reason = "已生成内部 WritingPlan，直接进入正文写作。"
        elif action in {"show_writing_plan", "revise_writing_plan"}:
            phase = "show_writing_plan"
            reason = "按用户要求展示本章 WritingPlan。"
        elif action == "approve_document":
            phase = "document_approval"
            reason = "正在处理当前正文确认。"
        else:
            phase = "respond_only"
            reason = "本轮只回答问题。"
        return {
            **plan_state,
            "write_phase": phase,
            "reason": reason,
        }

    def render_writing_plan(self, chat_context: dict[str, Any]) -> str:
        title = str(chat_context.get("title") or "当前章节")
        purpose = str(chat_context.get("purpose") or "").strip()
        outline = chat_context.get("writing_outline") if isinstance(chat_context.get("writing_outline"), dict) else {}
        blocks = [item for item in (outline.get("blocks") or []) if isinstance(item, dict)]
        objectives = [
            str(item).strip()
            for item in (outline.get("writing_objectives") or [])
            if str(item or "").strip()
        ]
        lines = ["本章 WritingPlan", "", f"章节名称：{title}"]
        if purpose:
            lines.append(f"写作目的：{purpose}")
        if objectives:
            lines.extend(("", "写作目标："))
            lines.extend(
                f"{index}. {objective}"
                for index, objective in enumerate(objectives, start=1)
            )
        block_section = "评分与内容覆盖要点" if objectives else "写作要点"
        lines.extend(("", f"共 {len(blocks)} 个{block_section}："))
        for index, item in enumerate(blocks, start=1):
            kind = _kind_label(str(item.get("kind") or ""))
            heading = str(item.get("heading") or f"要点{index}")
            must = str(item.get("must_answer") or "").strip()
            write_as = str(item.get("write_as") or "").strip()
            lines.append("")
            lines.append(f"{index}. {heading}")
            lines.append(f"   内容类型：{kind}")
            if must:
                label = "覆盖要求" if objectives else "核心问题"
                lines.append(f"   {label}：{must}")
            if write_as:
                lines.append(f"   表达要求：{write_as}")
        lines.extend(("", "可直接指出要修改的序号；之后说“按这个写”会立即生成正文。"))
        return "\n".join(lines)

    def _write_writing_plan_store(self, store: dict[str, Any]) -> None:
        path = self.writing_plan_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "v3.chapter-writing-plan.v1",
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
                    "research_steps": list(item.get("research_steps") or []),
                    "elapsed_seconds": item.get("elapsed_seconds"),
                    "operation_id": str(item.get("operation_id") or ""),
                    "status": str(item.get("status") or ""),
                }
            )
        if limit > 0:
            return turns[-limit:]
        return turns

    def load_batch_history(self, chapter_id: str) -> list[dict[str, Any]]:
        """Project durable batch-writing events into readable chapter chat turns."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in self.store.chapter_batch_events(chapter_id):
            grouped.setdefault(str(event.get("job_id") or ""), []).append(event)

        turns: list[dict[str, Any]] = []
        for job_id, events in grouped.items():
            if not job_id or not events:
                continue
            notes: list[str] = []
            draft_parts: list[str] = []
            status = ""
            for event in events:
                event_type = str(event.get("type") or "")
                status = str(event.get("status") or status)
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if event_type == "draft_delta":
                    delta = str(data.get("text") or "")
                    if delta:
                        draft_parts.append(delta)
                    continue
                message = str(event.get("message") or "").strip()
                stage = str(event.get("stage") or "").strip()
                if message:
                    notes.append(f"{stage}：{message}" if stage else message)
            title = str(events[-1].get("chapter_title") or chapter_id)
            committed = any(str(event.get("type") or "") == "chapter_committed" for event in events)
            failed = any(str(event.get("type") or "") == "chapter_failed" for event in events)
            content = "".join(draft_parts).strip()
            if committed:
                content = content or f"《{title}》正文已生成并写入中间文档。"
            elif failed:
                content = content or f"《{title}》本次编写未完成。"
            else:
                content = content or f"《{title}》的批量编写记录。"
            turns.append({
                "turn_id": f"batch:{job_id}:{chapter_id}",
                "role": "assistant",
                "content": content,
                "thinking": "\n".join(notes),
                "created_at": str(events[0].get("created_at") or ""),
                "status": "succeeded" if committed else ("failed" if failed else status),
                "operation_id": f"chapter-batch:{job_id}",
                "source": "batch_writing",
            })
        return turns

    def append_turn(
        self,
        chapter_id: str,
        *,
        role: str,
        content: str,
        thinking: str = "",
        created_at: str | None = None,
        research_steps: list[dict[str, Any]] | None = None,
        elapsed_seconds: int | float | None = None,
        operation_id: str = "",
        status: str = "",
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
            "research_steps": list(research_steps or []),
            "elapsed_seconds": elapsed_seconds,
            "operation_id": str(operation_id or ""),
            "status": str(status or ""),
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
        """Remove dialogue history without discarding the internal WritingPlan."""
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
                "research_steps": list(item.get("research_steps") or []),
                "elapsed_seconds": item.get("elapsed_seconds"),
                "operation_id": str(item.get("operation_id") or ""),
                "status": str(item.get("status") or ""),
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
            from .chapter_writing_outline import compile_chapter_writing_plan

            writing_outline = compile_chapter_writing_plan(
                chapter,
                tender_requirements=tender_requirements,
                scoring_requirements=scoring_requirements,
                writing_orientation=orientation_for_agent,
                chapter_context_items=context_items,
                project_context=gpc,
            )
        except Exception:
            writing_outline = {}

        # Compile the same immutable chapter boundary used by the document
        # writer.  Chat is allowed to discuss that boundary, never to create a
        # second one from the title or from whatever project facts happen to
        # be present.
        writing_spec = compile_chapter_writing_spec(
            chapter_id=str(chapter.get("chapter_id") or ""),
            operation="rewrite" if draft_preview else "create",
            chapter=chapter,
            tender_requirements=tuple(tender_requirements or []),
            scoring_requirements=tuple(scoring_requirements or []),
            project_context=gpc,
            chapter_context={"chapter_context_items": context_items},
            writing_orientation=orientation_for_agent,
            existing_content=draft_preview,
        )
        chapter_scope = writing_spec.scope_contract().payload()
        writing_outline = writing_spec.writing_outline
        plan_state = self.load_writing_plan_state(str(chapter.get("chapter_id") or ""))
        saved_plan = plan_state.get("writing_plan")
        saved_source = str(plan_state.get("writing_plan_source") or "")
        if (
            isinstance(saved_plan, dict)
            and saved_plan.get("blocks")
            and saved_source == "user_refined"
        ):
            # User refinements alter only the internal plan's granularity. The
            # immutable scope contract still supplies purpose and requirements.
            writing_outline = saved_plan
            chapter_scope = dict(chapter_scope)
            chapter_scope["writing_outline"] = dict(saved_plan)
        else:
            plan_state = {
                **plan_state,
                "writing_plan": dict(writing_outline),
                "writing_plan_source": "generated",
            }
        shared_facts = writing_spec.project_context

        return {
            "chapter_id": str(chapter.get("chapter_id") or ""),
            "title": str(chapter.get("title") or node.get("title") or ""),
            "is_leaf": bool(chapter.get("is_leaf")),
            "status": str(chapter.get("status") or ""),
            "approval_status": str(chapter.get("approval_status") or ""),
            "purpose": str(
                writing_spec.purpose
                or (orientation_for_agent.get("writing_purpose") or {}).get("purpose")
                or ""
            ),
            "level": node.get("level"),
            "order": chapter.get("order") or node.get("order"),
            "chapter_context_revision": int(chapter_context.get("context_revision") or 0),
            "chapter_context_items": context_items,
            "tender_requirements": list(tender_requirements or []),
            "scoring_requirements": list(scoring_requirements or []),
            "shared_project_facts": shared_facts,
            "chapter_scope": chapter_scope,
            "writing_orientation": orientation_for_agent,
            "writing_outline": writing_outline,
            "role": "bid_chapter_writer",
            "document_outline_context": outline_for_agent,
            "sibling_chapter_context": sibling_for_agent,
            "inspected_chapters": [],
            "draft_preview": draft_preview,
            "writing_plan_state": plan_state,
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
        actor: dict[str, Any] | None = None,
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
                actor=actor,
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
            "document_approval_requested": bool(
                done.get("document_approval_requested")
            ),
            "document_write_completed": bool(done.get("document_write_completed")),
            "chapter": done.get("chapter"),
            "content": done.get("content"),
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
        actor: dict[str, Any] | None = None,
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
        research_steps: list[dict[str, Any]] = []
        content_parts: list[str] = []
        document_write_completed = False
        written_chapter: dict[str, Any] | None = None
        written_content: dict[str, Any] | None = None

        plan = chat_context.get("writing_outline")
        plan_state = chat_context.get("writing_plan_state") or {}
        if (
            isinstance(plan, dict)
            and plan.get("blocks")
            and str(plan_state.get("writing_plan_source") or "") != "user_refined"
        ):
            chat_context["writing_plan_state"] = self.save_writing_plan(
                safe_id, plan, source="generated"
            )

        agent_action = _decide_chapter_action(chat_context, history, text)
        document_approval_action = agent_action["action"] == "approve_document"
        head_revision = int(chat_context.get("head_content_revision") or 0)
        formal_revision = int(chat_context.get("formal_content_revision") or 0)
        document_approval_requested = (
            document_approval_action
            and head_revision > 0
            and head_revision != formal_revision
        )
        if document_approval_action:
            phase = {
                **self.load_writing_plan_state(chapter_id),
                "write_phase": "document_approval",
                "reason": "正在处理当前正文的确认请求。",
            }
        else:
            phase = self.resolve_write_phase(
                chapter_id,
                outline=chat_context.get("writing_outline"),
                agent_action=agent_action["action"],
            )
        phase["agent_action"] = agent_action
        chat_context["writing_phase"] = phase
        document_write_requested = (
            agent_action["action"] == "write_document"
            and str(phase.get("write_phase") or "") == "write_body"
        )
        outline_analysis = str(phase.get("write_phase") or "") == "show_writing_plan"

        if outline_analysis:
            title = str(chat_context.get("title") or safe_id).strip()
            title_note = f"1/4 章节名称：《{title}》"
            reasoning_parts.append(f"{title_note}\n")
            yield {
                "type": "thinking_step",
                "chapter_id": safe_id,
                "step": "chapter_title",
                "message": title_note,
            }

            orientation = (
                chat_context.get("writing_orientation")
                if isinstance(chat_context.get("writing_orientation"), dict)
                else {}
            )
            writing_purpose = (
                orientation.get("writing_purpose")
                if isinstance(orientation.get("writing_purpose"), dict)
                else {}
            )
            purpose = str(
                writing_purpose.get("purpose") or chat_context.get("purpose") or "按已确认的章节边界组织内容"
            ).strip()
            situation_note = f"2/4 章节处境：{purpose}"
            reasoning_parts.append(f"{situation_note}\n")
            yield {
                "type": "thinking_step",
                "chapter_id": safe_id,
                "step": "chapter_situation",
                "message": situation_note,
            }

        inspection = {
            "inspect_ids": [],
            "reason": "正文写作阶段直接使用内部 WritingPlan。",
            "views": [],
        } if (document_write_requested or document_approval_action) else self._resolve_inspections(
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

        research_requested = bool(_RESEARCH_REQUEST_RE.search(text))
        if outline_analysis:
            material_counts = {
                "招标要求": len(chat_context.get("tender_requirements") or []),
                "评分要求": len(chat_context.get("scoring_requirements") or []),
                "本章资料": len(chat_context.get("chapter_context_items") or []),
                "他章只读": len(inspection.get("views") or []),
            }
            material_note = "3/4 资料检查：" + "，".join(
                f"{label} {count} 条" for label, count in material_counts.items()
            )
            reasoning_parts.append(f"{material_note}\n")
            yield {
                "type": "thinking_step",
                "chapter_id": safe_id,
                "step": "material_check",
                "message": material_note,
                "counts": material_counts,
            }

            research_plan_note = (
                "4/4 查询判断：发现明确的资料查询要求，正在检索并核验公开来源。"
                if research_requested
                else "4/4 查询判断：WritingPlan 先依据已有资料生成；正文写作前将按资料缺口判断是否需要公开查询。"
            )
            reasoning_parts.append(f"{research_plan_note}\n")
            yield {
                "type": "thinking_step",
                "chapter_id": safe_id,
                "step": "research_decision",
                "message": research_plan_note,
                "research_requested": research_requested,
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

        prior_outline_shown = any(
            item.get("role") == "assistant"
            and "本章 WritingPlan" in str(item.get("content") or "")
            for item in history[-4:]
            if isinstance(item, dict)
        )
        if (
            outline_analysis
            and prior_outline_shown
            and agent_action["action"] in {"show_writing_plan", "revise_writing_plan"}
        ):
            current_outline = chat_context.get("writing_outline") or {}
            revised_outline = _revise_outline_from_feedback(
                current_outline,
                chat_context,
                text,
            )
            if _writing_plan_fingerprint(revised_outline) != _writing_plan_fingerprint(current_outline):
                phase = self.save_writing_plan(
                    chapter_id, revised_outline, source="user_refined"
                )
                phase.update(
                    {
                        "write_phase": "show_writing_plan",
                        "reason": "已按你的意见修改 WritingPlan。",
                        "agent_action": agent_action,
                    }
                )
                chat_context["writing_outline"] = revised_outline
                scope = dict(chat_context.get("chapter_scope") or {})
                scope["writing_outline"] = revised_outline
                chat_context["chapter_scope"] = scope
                chat_context["writing_phase"] = phase
                revised_note = "已根据本轮意见重新拆分并细化 WritingPlan。"
                reasoning_parts.append(f"{revised_note}\n")
                yield {
                    "type": "thinking_step",
                    "chapter_id": safe_id,
                    "step": "outline_revision",
                    "message": revised_note,
                }

        yield {
            "type": "writing_phase",
            "chapter_id": safe_id,
            "write_phase": phase.get("write_phase"),
            "message": str(phase.get("reason") or ""),
            "agent_action": agent_action,
            "document_write_requested": document_write_requested,
            "document_approval_requested": document_approval_requested,
        }
        document_write_requested = (
            agent_action["action"] == "write_document"
            and str(phase.get("write_phase") or "") == "write_body"
        )
        if document_approval_action:
            if document_approval_requested:
                answer = "正在确认当前正文，完成后会自动更新章节状态。"
            elif head_revision <= 0:
                answer = "当前没有可确认的正文草稿，请先在对话中生成正文。"
            else:
                answer = "当前正文已经确认，无需重复确认。"
            content_parts.append(answer)
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
                "content_kind": "status",
            }
            messages = None
        elif document_write_requested:
            if actor:
                start_note = "开始撰写：章节 Agent 正在结合内部 WritingPlan、本章完整对话和项目事实生成正文。"
                reasoning_parts.append(f"{start_note}\n")
                yield {
                    "type": "thinking_step",
                    "chapter_id": safe_id,
                    "step": "drafting",
                    "message": start_note,
                }
                from .chapter_writing_service import (
                    ChapterWritingRequest as AgentWritingRequest,
                    ChapterWritingService,
                )

                current_chapter = self.store.chapter_workspace(safe_id) or chapter
                dialogue = [
                    *history,
                    {
                        "role": "user",
                        "content": text,
                        "created_at": user_record.get("created_at"),
                    },
                ]
                writing_plan = (chat_context.get("writing_plan_state") or {}).get("writing_plan")
                write_request = AgentWritingRequest(
                    unit_id=f"chapter-{safe_id}",
                    node_ids=(safe_id,),
                    operation_id=f"chapter-agent:{safe_id}:{uuid.uuid4()}",
                    operation=(
                        "rewrite"
                        if int(current_chapter.get("head_content_revision") or 0) > 0
                        else "create"
                    ),
                    # The WritingPlan travels in its typed field below.  Do not
                    # append it to the user's text: words such as “资料/核查” in
                    # plan facts must not be misread as an explicit search command.
                    user_instruction=text,
                    chapter_dialogue=tuple(dialogue),
                    chapter_writing_plan=dict(writing_plan or {}),
                    chapter_id=safe_id,
                    expected_workspace_revision=int(self.store.revision()),
                    expected_chapter_revision=int(
                        current_chapter.get("chapter_revision") or 0
                    ),
                    actor=dict(actor),
                    run_research=True,
                    commit_drafts=True,
                )
                for write_event in ChapterWritingService(self.context).iter_events(
                    write_request
                ):
                    event_type = str(write_event.get("type") or "")
                    if event_type in {
                        "thinking_step",
                        "inspect_planning",
                        "inspecting",
                        "inspect_skipped",
                        "research",
                    }:
                        note = str(
                            write_event.get("message")
                            or write_event.get("reason")
                            or ""
                        ).strip()
                        if note:
                            reasoning_parts.append(f"{note}\n")
                        if event_type == "research":
                            research_steps.append(
                                {
                                    "status": str(
                                        write_event.get("status") or "processing"
                                    ),
                                    "message": note,
                                    "queries": list(write_event.get("queries") or []),
                                    "sources": list(write_event.get("sources") or []),
                                }
                            )
                    elif event_type == "thinking_delta":
                        delta = str(write_event.get("delta") or "")
                        if delta:
                            reasoning_parts.append(delta)
                    if event_type == "content_delta":
                        yield {
                            **write_event,
                            "type": "draft_delta",
                            "delta": str(write_event.get("delta") or ""),
                        }
                    elif event_type == "done":
                        document_write_completed = True
                        written_chapter = write_event.get("chapter")
                        written_content = write_event.get("content")
                    elif event_type == "meta":
                        yield {**write_event, "type": "writing_meta"}
                    else:
                        yield write_event
                answer = (
                    "本章正文已生成并写入中间文档。"
                    if document_write_completed
                    else "本章正文生成未完成。"
                )
            else:
                # Direct service callers without an authenticated actor may
                # inspect routing, but cannot execute the write tool.
                answer = _DOCUMENT_WRITE_NOTICE
            content_parts.append(answer)
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
                "content_kind": "status",
            }
            messages = None
        elif phase.get("write_phase") == "show_writing_plan":
            answer = self.render_writing_plan(chat_context)
            content_parts.append(answer)
            yield {
                "type": "content_delta",
                "chapter_id": safe_id,
                "delta": answer,
            }
            messages = None
        else:
            messages = self._build_messages(chat_context, history, text)
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
            research_steps=research_steps,
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
            "document_write_completed": document_write_completed,
            "document_approval_requested": document_approval_requested,
            "agent_action": agent_action,
            "chapter": written_chapter,
            "content": written_content,
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
                "query_count": 0,
                "search_executed": False,
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
                "message": f"本章公开资料检索未执行成功：{str(exc)[:240]}",
                "sources": [],
                "query_count": 0,
                "search_executed": False,
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
        query_count = int(getattr(batch, "query_count", 0) or 0)
        search_executed = query_count > 0
        if batch.status == "published" and sources and search_executed:
            message_text = f"已完成本章公开资料检索，找到 {len(sources)} 条可采用来源。"
        elif batch.status == "gap" and search_executed:
            message_text = "已完成本章公开资料检索，但没有通过关联性和可核验筛选的来源。"
        elif not search_executed:
            message_text = (
                "本章检索流程未实际发出查询，不能视为已经查过资料："
                f"{str(batch.error or '检索规划未完成')[:240]}"
            )
        else:
            message_text = f"本章公开资料检索未完成：{str(batch.error or '检索服务不可用')[:240]}"
        return {
            "status": batch.status,
            "message": message_text,
            "sources": sources,
            "query_count": query_count,
            "search_executed": search_executed,
        }

    @staticmethod
    def _build_messages(
        chat_context: dict[str, Any],
        history: list[dict[str, Any]],
        user_message: str,
    ) -> list[dict[str, str]]:
        writing_phase = chat_context.get("writing_phase") if isinstance(chat_context.get("writing_phase"), dict) else {}
        phase = str(writing_phase.get("write_phase") or "write_body")
        system_prompt = (
            "你是当前章节的写作 Agent。chapter_scope 是与正文写作器共用的唯一章节边界。"
            "无论本轮是解释、评判、状态说明还是提纲讨论，都只能围绕 chapter_scope 中的 "
            "purpose、writing_objectives、writing_outline.blocks 和 bound_requirements 回答。"
            "项目事实只是这些目标的证据候选，不是必须写入的话题；不得因为上下文出现采购人、"
            "部署、成果、交付物、流程或角色等事实，就把它们扩展成本章论证路线。"
            "标题只是显示信息，不能据此推断章节职责。按每个 block 的 must_answer 回答，并遵循 write_as；"
            "除非 scope 明确要求，不得增加职责、程序、输入输出、交付物、记录、验收或承诺。"
            + (
                "当前阶段是按用户要求展示内部 WritingPlan：只整理本章要写什么，不要写完整正文。"
                if phase == "show_writing_plan"
                else "本轮动作已判定为只回复、不写文档；直接回答用户问题。"
            )
            + "draft_preview 为空时要明确说明当前没有可供检查的正文，不得假装看过正文。"
            "既往 assistant 对话不具事实权威；用户质疑此前回答时，直接纠正且不得自我强化。"
            "当前能收到本提示就表示本轮没有触发写作流，禁止声称已修改、已覆盖、已写入、已提交或已完成正文。"
        )
        research = chat_context.get("research") if isinstance(chat_context.get("research"), dict) else {}
        if research and research.get("search_executed"):
            system_prompt += (
                "用户已明确要求查资料，系统已实际调用公开资料检索工具。"
                "只能基于 research.sources 中返回的来源描述检索结果，不得声称自己没有检索工具，"
                "也不得编造未返回的政策名称、文号、年份或链接。"
            )
        elif research and research.get("status") != "skipped":
            system_prompt += (
                "检索流程没有实际发出查询。必须明确告诉用户本轮没有真正查到资料，"
                "不得使用‘已检索’‘查阅后’‘根据公开资料’等表述，也不得编造来源。"
            )
        elif research:
            system_prompt += (
                "检索规划器已判断现有资料足以回答本章；如解释该决定，只能依据 research.message，"
                "不得捏造未检索的外部来源。"
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
            "chapter_scope": chat_context.get("chapter_scope") or {},
            "writing_phase": writing_phase,
            "draft_preview": (chat_context.get("draft_preview") or "") if draft_requested else "",
            "recent_chapter_dialogue": recent,
            "inspected_chapters": list(chat_context.get("inspected_chapters") or []),
            "research": research,
            "draft_inspection_requested": draft_requested,
            "history_critique_requested": history_critique,
            "history_is_non_authoritative": True,
            "user_message": user_message,
            "instruction": (
                "忠实回答 user_message，但任何论证、解释和建议均不得超出 chapter_scope。"
                "只有 draft_inspection_requested 为 true 时才检查 draft_preview；为空则如实说明。"
                "不得声称已经修改或写入文档。"
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
        scope = chat_context.get("chapter_scope")
        scope = scope if isinstance(scope, dict) else {}
        research = (
            chat_context.get("research")
            if isinstance(chat_context.get("research"), dict)
            else {}
        )
        if research:
            note = str(research.get("message") or "").strip()
            sources = [
                item
                for item in (research.get("sources") or [])
                if isinstance(item, dict)
            ]
            if sources:
                titles = "；".join(
                    str(item.get("title") or item.get("source_url") or "")
                    for item in sources[:3]
                )
                return f"{note} 可采用来源：{titles}"
            if note:
                return note
        purpose = str(scope.get("purpose") or chat_context.get("purpose") or "").strip()
        objectives = [str(item).strip() for item in scope.get("writing_objectives") or [] if str(item).strip()]
        outline = scope.get("writing_outline")
        outline = outline if isinstance(outline, dict) else {}
        blocks = [item for item in outline.get("blocks") or [] if isinstance(item, dict)]
        if _is_draft_inspection_request(message) and not chat_context.get("draft_preview"):
            return "当前没有可供检查的本章正文。" + (f"本章编写目标是：{purpose}" if purpose else "")
        parts = [f"本章编写目标是：{purpose}" if purpose else "本章仅按已确认的章节目标编写。"]
        if objectives:
            parts.append("具体目标：" + "；".join(objectives))
        if blocks:
            must_answer = [
                str(item.get("must_answer") or "").strip()
                for item in blocks
                if str(item.get("must_answer") or "").strip()
            ]
            if must_answer:
                parts.append("本章需要回答：" + "；".join(must_answer[:6]))
        return " ".join(parts)


# Transport and older callers may keep the historical name.  It is an alias,
# not a second service or a second Agent.
ChapterChatService = ChapterAgentService


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


def _kind_label(kind: str) -> str:
    return {
        "response": "做法",
        "evidence": "证据",
        "constraint": "约束",
        "quality": "质控",
    }.get(kind, "要点")


def _writing_plan_fingerprint(outline: dict[str, Any]) -> str:
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


def _normalize_chapter_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    if action in {
        "write_document",
        "show_writing_plan",
        "revise_writing_plan",
        "approve_document",
        "respond_only",
    }:
        return action
    return "write_document"


def _decide_chapter_action(
    chat_context: dict[str, Any],
    history: list[dict[str, Any]],
    user_message: str,
) -> dict[str, str]:
    """Let the chapter Agent choose its next action from meaning, not phrases.

    Writing is the Agent's primary responsibility.  A conversational reply is
    selected only when the user is explicitly asking for explanation, status,
    assessment or outline discussion without requesting a document change.
    """
    scope = chat_context.get("chapter_scope")
    scope = scope if isinstance(scope, dict) else {}
    text = str(user_message or "").strip()
    prior_plan_shown = any(
        item.get("role") == "assistant"
        and "WritingPlan" in str(item.get("content") or "")
        for item in history[-4:]
        if isinstance(item, dict)
    )
    if re.search(r"(?:确认|通过|定稿|转为正式).{0,8}(?:正文|草稿)|(?:正文|草稿).{0,8}(?:确认|通过|定稿)", text):
        return {
            "action": "approve_document",
            "reason": "用户明确确认当前正文。",
            "writing_instruction": text,
            "source": "explicit_intent",
        }
    if _DIRECT_BODY_WRITE_RE.search(text):
        return {
            "action": "write_document",
            "reason": "用户明确要求生成或修改正文。",
            "writing_instruction": text,
            "source": "explicit_intent",
        }
    if prior_plan_shown and _PLAN_REVISION_RE.search(text):
        return {
            "action": "revise_writing_plan",
            "reason": "用户正在修改刚展示的 WritingPlan。",
            "writing_instruction": text,
            "source": "explicit_intent",
        }
    if _SHOW_WRITING_PLAN_RE.search(text):
        return {
            "action": "show_writing_plan",
            "reason": "用户明确要求先看 WritingPlan。",
            "writing_instruction": text,
            "source": "explicit_intent",
        }
    recent = [
        {"role": item.get("role"), "content": item.get("content")}
        for item in history[-6:]
        if isinstance(item, dict) and item.get("content")
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are the action controller inside a chapter-writing Agent. "
                "Judge the user's meaning in context; never match keywords or fixed phrases. "
                "The Agent's default duty is to create or improve the current chapter. "
                "Choose write_document when the user asks, implies, approves, continues, "
                "corrects, critiques with an expected fix, or otherwise wants chapter content changed. "
                "An explicit request to start or continue body writing is write_document and is itself "
                "authorization to write; it does not require a separate outline-confirmation phrase. "
                "Choose show_writing_plan only when the user asks to see how the chapter will be written. "
                "Choose revise_writing_plan when the immediately preceding WritingPlan is being edited. "
                "If the user says to write using that plan, choose write_document immediately. "
                "Choose approve_document only when the user explicitly approves, confirms, finalizes, "
                "or accepts the current body draft as the formal chapter. Distinguish this from outline approval. "
                "Choose respond_only only for an explicit question, explanation, status check, "
                "assessment, or outline discussion that requests no document change. "
                "A request to search, inspect, compare, or summarize sources without also asking "
                "to write or revise the document is respond_only; the research tool runs separately "
                "and its real result is then explained by the chapter Agent. "
                "The action may not expand chapter_scope. Return one JSON object only: "
                '{"action":"write_document|show_writing_plan|revise_writing_plan|approve_document|respond_only","reason":"brief semantic reason",'
                '"writing_instruction":"the user request preserved for the writer"}.'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "chapter_scope": scope,
                    "writing_phase": chat_context.get("writing_phase") or {},
                    "has_current_draft": bool(chat_context.get("draft_preview")),
                    "head_content_revision": int(
                        chat_context.get("head_content_revision") or 0
                    ),
                    "formal_content_revision": int(
                        chat_context.get("formal_content_revision") or 0
                    ),
                    "recent_dialogue": recent,
                    "user_message": user_message,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        from llm_client import chat_with_meta
        from utils import extract_json_text

        result = chat_with_meta(messages, temperature=0.0)
        parsed = json.loads(extract_json_text(str(result.get("content") or "")))
        if not isinstance(parsed, dict):
            raise ValueError("chapter action must be an object")
        action = _normalize_chapter_action(parsed.get("action"))
        return {
            "action": action,
            "reason": str(parsed.get("reason") or "").strip()[:500],
            "writing_instruction": str(
                parsed.get("writing_instruction") or user_message
            ).strip()[:MAX_TURN_CHARS],
            "source": "chapter_agent",
        }
    except Exception as exc:
        # Failure must not silently demote a writing Agent into generic chat.
        return {
            "action": "write_document",
            "reason": "动作判断不可用，按章节 Agent 的默认写作职责继续。",
            "writing_instruction": str(user_message or "").strip()[:MAX_TURN_CHARS],
            "source": "safe_write_default",
            "error": str(exc)[:240],
        }


def _revise_outline_from_feedback(
    outline: dict[str, Any],
    chat_context: dict[str, Any],
    feedback: str,
) -> dict[str, Any]:
    """Refine outline granularity from feedback while preserving chapter scope."""
    blocks = [item for item in (outline.get("blocks") or []) if isinstance(item, dict)]
    if not blocks:
        return outline
    payload = {
        "fixed_scope": {
            "purpose": chat_context.get("purpose"),
            "writing_objectives": outline.get("writing_objectives") or [],
            "bound_requirements": (chat_context.get("chapter_scope") or {}).get(
                "bound_requirements"
            )
            or [],
        },
        "current_outline": outline,
        "project_facts": chat_context.get("shared_project_facts") or {},
        "chapter_materials": chat_context.get("chapter_context_items") or [],
        "research_sources": (chat_context.get("research") or {}).get("sources") or [],
        "user_feedback": feedback,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是投标文件章节提纲修订器。根据用户意见真正重写提纲，不要把原提纲原样返回。"
                "purpose、writing_objectives 和 bound_requirements 是不可扩展的章节边界；"
                "可以把笼统的一块拆成 3—6 个互不重复、可直接写成正文的具体要点。"
                "每个 heading 必须点明目标维度，每个 must_answer 必须写清对象、拟达到的结果、"
                "实施边界或可检验表现，禁止使用‘明确目标’‘保障落实’等循环空话。"
                "项目事实可用于项目化；公开资料只能补政策、标准或专业方法依据，"
                "不得写成当前项目既定事实。只返回 JSON：{\"blocks\":[...]}。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        from llm_client import chat_with_meta
        from utils import extract_json_text

        result = chat_with_meta(messages, temperature=0.2)
        parsed = json.loads(extract_json_text(str(result.get("content") or "")))
        raw_blocks = parsed.get("blocks") if isinstance(parsed, dict) else None
        if not isinstance(raw_blocks, list):
            return outline
        revised: list[dict[str, Any]] = []
        targeted_point = bool(re.search(r"第[一二三四五六七八九十\d]+(?:点|项)", feedback))
        max_blocks = min(8, len(blocks) + 2) if targeted_point else 8
        for index, raw in enumerate(raw_blocks[:max_blocks], start=1):
            if not isinstance(raw, dict):
                continue
            heading = re.sub(r"\s+", " ", str(raw.get("heading") or "")).strip()[:60]
            must_answer = re.sub(
                r"\s+", " ", str(raw.get("must_answer") or "")
            ).strip()[:300]
            if not heading or not must_answer:
                continue
            kind = str(raw.get("kind") or "response")
            if kind not in {"response", "evidence", "constraint", "quality"}:
                kind = "response"
            revised.append(
                {
                    "block_id": f"WO-R{index}",
                    "kind": kind,
                    "heading": heading,
                    "must_answer": must_answer,
                    "write_as": str(
                        raw.get("write_as")
                        or "写成项目化目标，交代目标对象、结果、边界和可检查表现；不编造数值或承诺。"
                    )[:300],
                    "outcome_kind": str(raw.get("outcome_kind") or ""),
                    "score_point_id": str(raw.get("score_point_id") or ""),
                    "condition_id": str(raw.get("condition_id") or ""),
                    "requirement_ids": [
                        str(item) for item in (raw.get("requirement_ids") or []) if item
                    ][:4],
                    "ownership": str(raw.get("ownership") or "primary"),
                }
            )
        if not revised:
            return outline
        result_outline = dict(outline)
        result_outline["blocks"] = revised
        result_outline["block_count"] = len(revised)
        return result_outline
    except Exception:
        return outline
