"""Deterministic chapter content editing, lock merge, H2 approval, formal compose.

Phases 3–5 and 8. User text edits never spawn a long-running Agent; AI drafts only
append new content revisions and never set formal pointers by themselves.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from control_plane import CommandEnvelope, ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import ContentBlock


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _actor_id(actor: dict[str, Any] | None) -> str:
    return str((actor or {}).get("id") or "user")[:128]


def split_text_into_blocks(
    text: str,
    *,
    chapter_id: str,
    actor_id: str = "ai",
    source: str = "AI_GENERATED",
    confidence: float = 0.8,
    source_bundle_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Split chapter prose into paragraph / list / table-shaped blocks."""
    chunks = [part.strip() for part in re.split(r"\n\s*\n", str(text or "")) if part.strip()]
    if not chunks and str(text or "").strip():
        chunks = [str(text).strip()]
    now = _now_iso()
    blocks: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        block_type = "paragraph"
        if re.match(r"^(\s*[-*•]|\s*\d+[.)])\s+", chunk):
            block_type = "list"
        elif "|" in chunk and chunk.count("|") >= 2:
            block_type = "table"
        block_id = f"{chapter_id}-b{index + 1}-{uuid.uuid4().hex[:8]}"
        blocks.append(
            ContentBlock(
                block_id=block_id,
                target_node_id=chapter_id,
                type=block_type,  # type: ignore[arg-type]
                content=chunk,
                confidence=confidence,
                source=source,  # type: ignore[arg-type]
                created_by=actor_id,
                updated_by=actor_id,
                created_at=now,
                updated_at=now,
                order=index,
                lock_state="UNLOCKED",
                human_locked=False,
                source_bundle_hash=source_bundle_hash,
            ).model_dump(mode="json")
        )
    return blocks


def is_locked(block: dict[str, Any]) -> bool:
    return bool(block.get("human_locked")) or str(block.get("lock_state") or "") == "USER_LOCKED"


def merge_ai_blocks_with_locks(
    *,
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    overwrite_locked: bool = False,
) -> list[dict[str, Any]]:
    """Preserve USER_LOCKED blocks unless overwrite_locked is explicit."""
    locked = [dict(block) for block in existing if is_locked(block)]
    if overwrite_locked:
        result = [dict(block) for block in incoming]
    else:
        unlocked_incoming = [dict(block) for block in incoming if not is_locked(block)]
        # Locked blocks keep their relative order first, then new AI content.
        result = locked + unlocked_incoming
    for index, block in enumerate(result):
        block["order"] = index
        if is_locked(block):
            block["lock_state"] = "USER_LOCKED"
            block["human_locked"] = True
    return result


def apply_block_operations(
    *,
    base_blocks: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    chapter_id: str,
    actor: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    blocks = [dict(item) for item in base_blocks]
    actor_id = _actor_id(actor)
    now = _now_iso()

    def index_of(block_id: str) -> int:
        for idx, item in enumerate(blocks):
            if str(item.get("block_id") or "") == block_id:
                return idx
        raise ControlPlaneError(
            "CHAPTER_BLOCK_NOT_FOUND",
            f"未找到 block_id: {block_id}",
            status_code=404,
        )

    for raw in operations:
        if not isinstance(raw, dict):
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                "operation 必须是对象。",
                status_code=400,
            )
        op = str(raw.get("op") or "").strip()
        if op == "insert":
            block = dict(raw.get("block") or {})
            if not block.get("block_id"):
                block["block_id"] = f"{chapter_id}-u{uuid.uuid4().hex[:10]}"
            block.setdefault("target_node_id", chapter_id)
            block.setdefault("type", "paragraph")
            block.setdefault("confidence", 0.9)
            block["source"] = "USER_CREATED"
            block["lock_state"] = "USER_LOCKED"
            block["human_locked"] = True
            block["created_by"] = actor_id
            block["updated_by"] = actor_id
            block["created_at"] = now
            block["updated_at"] = now
            try:
                index = int(raw.get("index", len(blocks)))
            except (TypeError, ValueError) as exc:
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    "insert.index 必须是整数。",
                    status_code=400,
                ) from exc
            index = max(0, min(index, len(blocks)))
            blocks.insert(index, block)
        elif op == "update":
            block_id = str(raw.get("block_id") or "").strip()
            if not block_id:
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    "update 缺少 block_id。",
                    status_code=400,
                )
            idx = index_of(block_id)
            current = dict(blocks[idx])
            if "content" in raw:
                content = str(raw.get("content") or "").strip()
                if not content:
                    raise ControlPlaneError(
                        "CHAPTER_CONTENT_INVALID",
                        "content 不能为空。",
                        status_code=400,
                    )
                current["content"] = content
            if "type" in raw and raw.get("type"):
                current["type"] = str(raw.get("type"))
            prev_source = str(current.get("source") or "AI_GENERATED")
            if prev_source == "AI_GENERATED":
                current["source"] = "USER_EDITED"
            elif prev_source == "USER_CREATED":
                current["source"] = "USER_CREATED"
            else:
                current["source"] = "USER_EDITED"
            current["lock_state"] = "USER_LOCKED"
            current["human_locked"] = True
            current["updated_by"] = actor_id
            current["updated_at"] = now
            if not current.get("created_by"):
                current["created_by"] = actor_id
            if not current.get("created_at"):
                current["created_at"] = now
            blocks[idx] = current
        elif op == "delete":
            block_id = str(raw.get("block_id") or "").strip()
            idx = index_of(block_id)
            del blocks[idx]
        elif op == "move":
            block_id = str(raw.get("block_id") or "").strip()
            try:
                to_index = int(raw.get("to_index"))
            except (TypeError, ValueError) as exc:
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    "move.to_index 必须是整数。",
                    status_code=400,
                ) from exc
            idx = index_of(block_id)
            item = blocks.pop(idx)
            to_index = max(0, min(to_index, len(blocks)))
            blocks.insert(to_index, item)
        elif op == "replace_all":
            incoming = raw.get("blocks")
            if not isinstance(incoming, list):
                raise ControlPlaneError(
                    "CHAPTER_CONTENT_INVALID",
                    "replace_all.blocks 必须是数组。",
                    status_code=400,
                )
            blocks = [dict(item) for item in incoming if isinstance(item, dict)]
        else:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                f"不支持的 operation: {op or '<empty>'}",
                status_code=400,
            )

    for index, block in enumerate(blocks):
        block["order"] = index
        block.setdefault("target_node_id", chapter_id)
    return blocks


def block_level_diff(
    from_blocks: list[dict[str, Any]],
    to_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from_map = {str(item.get("block_id") or ""): item for item in from_blocks}
    to_map = {str(item.get("block_id") or ""): item for item in to_blocks}
    changes: list[dict[str, Any]] = []
    for block_id, block in to_map.items():
        if not block_id:
            continue
        if block_id not in from_map:
            changes.append({"op": "added", "block_id": block_id, "block": block})
        elif _json_stable(from_map[block_id]) != _json_stable(block):
            changes.append(
                {
                    "op": "changed",
                    "block_id": block_id,
                    "before": from_map[block_id],
                    "after": block,
                }
            )
    for block_id, block in from_map.items():
        if block_id and block_id not in to_map:
            changes.append({"op": "removed", "block_id": block_id, "block": block})
    return changes


def _json_stable(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ChapterEditingService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    @staticmethod
    def _expected_chapter_revision(payload: dict[str, Any]) -> int:
        raw = payload.get("expected_chapter_revision", 0)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc

    def _confirmation_required(self) -> bool:
        try:
            from api.settings_service import SettingsService
            from pathlib import Path

            root = Path(__file__).resolve().parents[2]
            settings = SettingsService(root).flow_settings()
            return bool(settings.get("confirmation_required", False))
        except Exception as exc:
            raise ControlPlaneError(
                "CHAPTER_POLICY_READ_FAILED",
                f"无法读取章节确认策略，已停止提交：{exc}",
                status_code=500,
            ) from exc

    def _make_current_effective(self, result: dict[str, Any]) -> dict[str, Any]:
        """Promote a newly written revision without requiring a second user action."""
        if result.get("unchanged"):
            return result
        content = result.get("content") or {}
        chapter_data = result.get("chapter") or {}
        chapter_id = str(chapter_data.get("chapter_id") or "").strip()
        content_revision = int(content.get("content_revision") or 0)
        content_hash = str(content.get("content_hash") or "")
        if not chapter_id or not content_revision or not content_hash:
            raise ControlPlaneError(
                "CHAPTER_EFFECTIVE_REVISION_INVALID",
                "生成结果缺少 chapter_id、content_revision 或 content_hash，已停止提交。",
                status_code=500,
            )
        receipt = self.store.record_chapter_approval_receipt(
            chapter_id=chapter_id,
            content_revision=content_revision,
            content_hash=content_hash,
            decision="auto_approved",
            principal_id="system",
            confirmation_required=False,
            actor={"type": "system", "id": "system", "role": "auto"},
        )
        chapter = self.store.set_chapter_formal_pointer(
            chapter_id=chapter_id,
            expected_chapter_revision=int(chapter_data.get("chapter_revision") or 0),
            content_revision=content_revision,
            content_hash=content_hash,
            approval_status="approved",
            actor={"type": "system", "id": "system", "role": "auto"},
        )
        return {"chapter": chapter, "content": content, "approval": receipt, "unchanged": False}

    def _require_leaf_chapter(self, chapter_id: str) -> None:
        from .chapter_workspace import ChapterWorkspaceService

        ChapterWorkspaceService(self.context).require_leaf_chapter(chapter_id)

    def _evaluate_grounding(
        self,
        *,
        chapter_id: str,
        text: str,
        expected_global_ref: tuple[str, int, str] | None = None,
        expected_chapter_ref: tuple[str, int, str] | None = None,
        evidence_batch_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        from .chapter_workspace import ChapterWorkspaceService
        from .content_grounding import ContentGroundingGate
        from .global_project_context import GlobalProjectContextService
        from .requirement_ledger import load_promoted_requirement_ledger
        from .research_service import load_published_batch
        from .score_model import load_promoted_score_model

        global_context = GlobalProjectContextService(self.context).load()
        current = (
            str(global_context.get("global_context_id") or ""),
            int(global_context.get("global_context_revision") or 0),
            str(global_context.get("global_context_hash") or ""),
        )
        if expected_global_ref is not None and expected_global_ref != current:
            raise ControlPlaneError(
                "GLOBAL_PROJECT_CONTEXT_CONFLICT",
                "全局项目事实已更新，请刷新后重新生成本章。",
                status_code=409,
                details={"requested": expected_global_ref, "current": current},
            )
        chapter = ChapterWorkspaceService(self.context).get_chapter(chapter_id)
        node = chapter.get("blueprint_node")
        node = node if isinstance(node, dict) else {}
        requirement_ids = {str(item) for item in node.get("requirement_ids") or []}
        score_ids = {str(item) for item in node.get("score_point_ids") or []}
        condition_ids = {
            str(item) for item in node.get("score_condition_ids") or []
        }
        ledger = load_promoted_requirement_ledger(self.context)
        requirement_texts = [
            str(item.normalized_requirement or "")
            for item in ledger.requirements
            if item.requirement_id in requirement_ids
        ]
        scores = load_promoted_score_model(self.context)
        for point in scores.points:
            selected_conditions = [
                condition
                for condition in point.score_conditions
                if condition.condition_id in condition_ids
            ]
            if point.score_point_id in score_ids or selected_conditions:
                requirement_texts.append(
                    str(point.response_expectation or "")
                )
                requirement_texts.extend(
                    str(condition.text or "")
                    for condition in selected_conditions
                )
        evidence_sources: list[dict[str, Any]] = []
        normalized_batch_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in (evidence_batch_ids or [])
                if str(item).strip()
            )
        )
        for batch_id in normalized_batch_ids:
            batch = load_published_batch(self.context, batch_id)
            if batch is None:
                raise ControlPlaneError(
                    "EVIDENCE_BATCH_INVALID",
                    "章节引用的公开资料批次不存在或尚未发布。",
                    status_code=409,
                    details={"batch_id": batch_id},
                )
            evidence_sources.extend(
                {
                    "batch_id": batch.batch_id,
                    "evidence_id": item.evidence_id,
                    "content": item.content,
                    "supporting_excerpt": item.supporting_excerpt,
                    "relevance_tier": item.relevance_tier.value,
                    "usage_constraints": list(item.usage_constraints),
                }
                for item in batch.items
            )
        context_record = chapter.get("context")
        context_record = (
            context_record if isinstance(context_record, dict) else {}
        )
        chapter_context = GlobalProjectContextService(
            self.context
        ).build_chapter_context(
            chapter_id,
            requirement_excerpts=[
                {
                    "requirement_id": item.requirement_id,
                    "text": str(item.normalized_requirement or ""),
                }
                for item in ledger.requirements
                if item.requirement_id in requirement_ids
            ],
            chapter_context_items=list(context_record.get("items") or []),
            chapter_context_revision=int(
                context_record.get("context_revision") or 0
            ),
            chapter_context_hash=str(
                context_record.get("context_hash") or ""
            ),
        )
        current_chapter_ref = (
            str(chapter_context.get("chapter_context_id") or ""),
            int(chapter_context.get("chapter_context_revision") or 0),
            str(chapter_context.get("chapter_context_hash") or ""),
        )
        if (
            expected_chapter_ref is not None
            and expected_chapter_ref != current_chapter_ref
        ):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_CONFLICT",
                "本章上下文已更新，请刷新后重新生成。",
                status_code=409,
                details={
                    "requested": expected_chapter_ref,
                    "current": current_chapter_ref,
                },
            )
        return ContentGroundingGate.evaluate(
            global_context=global_context,
            chapter=chapter,
            content=text,
            requirement_texts=requirement_texts,
            chapter_grounding_context=chapter_context,
            evidence_sources=evidence_sources,
            require_evidence_use=bool(evidence_sources),
        )

    def apply_operations(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        operations: list[dict[str, Any]],
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_leaf_chapter(chapter_id)
        workspace = self.store.chapter_workspace(chapter_id)
        if workspace is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND",
                f"章节 Workspace 不存在: {chapter_id}",
                status_code=404,
            )
        head = self.store.chapter_content_head(chapter_id)
        base = list((head or {}).get("blocks") or [])
        next_blocks = apply_block_operations(
            base_blocks=base,
            operations=operations,
            chapter_id=chapter_id,
            actor=actor,
        )
        previous_policy = dict((head or {}).get("approval_policy") or {})
        policy = {
            "confirmation_required": self._confirmation_required(),
            "frozen_at": _now_iso(),
            "grounding_required": bool(
                previous_policy.get("grounding")
                or previous_policy.get("grounding_required")
            ),
        }
        previous_grounding = previous_policy.get("grounding")
        if isinstance(previous_grounding, dict):
            policy["source_grounding"] = previous_grounding
        result = self.store.append_chapter_content_revision(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            blocks=next_blocks,
            source="user_edit",
            approval_policy=policy,
            actor=actor,
            approval_status="draft",
        )
        if not self._confirmation_required():
            return self._make_current_effective(result)
        return result

    def restore_revision(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        from_content_revision: int,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_leaf_chapter(chapter_id)
        source = self.store.chapter_content_revision(chapter_id, from_content_revision)
        if source is None:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_NOT_FOUND",
                f"Content revision 不存在: {chapter_id}@{from_content_revision}",
                status_code=404,
            )
        policy = {
            "confirmation_required": self._confirmation_required(),
            "frozen_at": _now_iso(),
            "restored_from": int(from_content_revision),
        }
        source_policy = dict(source.get("approval_policy") or {})
        if source_policy.get("grounding") or source_policy.get("grounding_required"):
            policy["grounding_required"] = True
            if isinstance(source_policy.get("grounding"), dict):
                policy["source_grounding"] = source_policy["grounding"]
        result = self.store.append_chapter_content_revision(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            blocks=list(source.get("blocks") or []),
            source="restore",
            approval_policy=policy,
            actor=actor,
            approval_status="draft",
        )
        if not self._confirmation_required():
            return self._make_current_effective(result)
        return result

    def generate_draft(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        text: str | None = None,
        overwrite_locked: bool = False,
        actor: dict[str, Any] | None = None,
        grounding_report: dict[str, Any] | None = None,
        expected_global_ref: tuple[str, int, str] | None = None,
        expected_chapter_ref: tuple[str, int, str] | None = None,
        evidence_batch_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an AI revision and make it current when H2 is disabled."""
        self._require_leaf_chapter(chapter_id)
        workspace = self.store.chapter_workspace(chapter_id)
        if workspace is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND",
                f"章节 Workspace 不存在: {chapter_id}",
                status_code=404,
            )
        actor_id = _actor_id(actor) if actor else "ai"
        body = str(text or "").strip()
        if not body:
            context = self.store.chapter_context_head(chapter_id) or {"items": []}
            lines = [f"# {workspace.get('title') or chapter_id}"]
            for item in context.get("items") or []:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("kind") or "").strip()
                content = str(item.get("body") or "").strip()
                if title or content:
                    lines.append(f"{title}\n{content}".strip())
            body = "\n\n".join(lines) or f"{workspace.get('title') or chapter_id} 章节草稿。"
        incoming = split_text_into_blocks(
            body,
            chapter_id=chapter_id,
            actor_id=actor_id or "ai",
            source="AI_GENERATED",
            confidence=0.75,
        )
        source_report = dict(grounding_report or {})
        if expected_global_ref is None and source_report:
            expected_global_ref = (
                str(source_report.get("global_context_id") or ""),
                int(source_report.get("global_context_revision") or 0),
                str(source_report.get("global_context_hash") or ""),
            )
        if expected_chapter_ref is None and source_report:
            expected_chapter_ref = (
                str(source_report.get("chapter_context_id") or ""),
                int(source_report.get("chapter_context_revision") or 0),
                str(source_report.get("chapter_context_hash") or ""),
            )
        if evidence_batch_ids is None and source_report:
            evidence_batch_ids = [
                str(item)
                for item in source_report.get("evidence_batch_ids") or []
                if str(item)
            ]
        head = self.store.chapter_content_head(chapter_id)
        existing = list((head or {}).get("blocks") or [])
        merged = merge_ai_blocks_with_locks(
            existing=existing,
            incoming=incoming,
            overwrite_locked=bool(overwrite_locked),
        )
        report = self._evaluate_grounding(
            chapter_id=chapter_id,
            text="\n\n".join(
                str(block.get("content") or "") for block in merged
            ),
            expected_global_ref=expected_global_ref,
            expected_chapter_ref=expected_chapter_ref,
            evidence_batch_ids=evidence_batch_ids,
        )
        fact_bindings = report.get("paragraph_fact_bindings")
        fact_bindings = fact_bindings if isinstance(fact_bindings, dict) else {}
        for index, block in enumerate(merged):
            if str(block.get("source") or "") == "AI_GENERATED":
                block["fact_ids"] = list(
                    fact_bindings.get(str(index)) or []
                )
        confirmation_required = self._confirmation_required()
        policy = {
            "confirmation_required": confirmation_required,
            "frozen_at": _now_iso(),
            "overwrite_locked": bool(overwrite_locked),
            "grounding": report,
        }
        result = self.store.append_chapter_content_revision(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            blocks=merged,
            source="merge" if existing else "ai_draft",
            approval_policy=policy,
            actor=actor,
            approval_status="draft",
        )
        if not confirmation_required:
            return self._make_current_effective(result)
        return result

    def confirm_approval(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        content_revision: int,
        content_hash: str,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_leaf_chapter(chapter_id)
        principal = _actor_id(actor)
        if not principal or principal in {"system", "auto"}:
            raise ControlPlaneError(
                "CHAPTER_APPROVAL_FORBIDDEN",
                "H2 必须由已认证用户确认。",
                status_code=403,
            )
        content = self.store.chapter_content_revision(chapter_id, content_revision)
        if content is None:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_NOT_FOUND",
                f"Content revision 不存在: {chapter_id}@{content_revision}",
                status_code=404,
            )
        if str(content.get("content_hash") or "") != str(content_hash or "").strip():
            raise ControlPlaneError(
                "CHAPTER_CONTENT_HASH_MISMATCH",
                "确认绑定的 content_hash 不匹配。",
                status_code=409,
            )
        policy = dict(content.get("approval_policy") or {})
        confirmation_required = bool(policy.get("confirmation_required", True))
        if policy.get("grounding") or policy.get("grounding_required"):
            source_grounding = policy.get("grounding") or policy.get(
                "source_grounding"
            )
            expected_ref = None
            expected_chapter_ref = None
            evidence_batch_ids: list[str] = []
            if isinstance(source_grounding, dict):
                expected_ref = (
                    str(source_grounding.get("global_context_id") or ""),
                    int(source_grounding.get("global_context_revision") or 0),
                    str(source_grounding.get("global_context_hash") or ""),
                )
                expected_chapter_ref = (
                    str(source_grounding.get("chapter_context_id") or ""),
                    int(source_grounding.get("chapter_context_revision") or 0),
                    str(source_grounding.get("chapter_context_hash") or ""),
                )
                evidence_batch_ids = [
                    str(item)
                    for item in source_grounding.get("evidence_batch_ids") or []
                    if str(item)
                ]
            self._evaluate_grounding(
                chapter_id=chapter_id,
                text="\n\n".join(
                    str(block.get("content") or "")
                    for block in content.get("blocks") or []
                    if isinstance(block, dict)
                ),
                expected_global_ref=expected_ref,
                expected_chapter_ref=expected_chapter_ref,
                evidence_batch_ids=evidence_batch_ids,
            )
        # Even if auto mode is on, explicit human confirm still issues approved receipt.
        receipt = self.store.record_chapter_approval_receipt(
            chapter_id=chapter_id,
            content_revision=int(content_revision),
            content_hash=str(content_hash),
            decision="approved",
            principal_id=principal,
            confirmation_required=confirmation_required,
            actor=actor,
        )
        chapter = self.store.set_chapter_formal_pointer(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            content_revision=int(content_revision),
            content_hash=str(content_hash),
            approval_status="approved",
            actor=actor,
        )
        return {"chapter": chapter, "content": content, "approval": receipt}

    def compare_revisions(
        self,
        chapter_id: str,
        *,
        from_revision: int,
        to_revision: int,
    ) -> dict[str, Any]:
        left = self.store.chapter_content_revision(chapter_id, from_revision)
        right = self.store.chapter_content_revision(chapter_id, to_revision)
        if left is None or right is None:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_NOT_FOUND",
                "compare 目标 revision 不存在。",
                status_code=404,
            )
        return {
            "chapter_id": chapter_id,
            "from_revision": int(from_revision),
            "to_revision": int(to_revision),
            "from_hash": left.get("content_hash"),
            "to_hash": right.get("content_hash"),
            "changes": block_level_diff(
                list(left.get("blocks") or []),
                list(right.get("blocks") or []),
            ),
        }

    def compose_formal_document(self) -> dict[str, Any]:
        """Assemble IntegratedDocument from each chapter's formal revision only."""
        from .chapter_workspace import ChapterWorkspaceService

        listing = ChapterWorkspaceService(self.context).list_chapters(include_archived=False)
        pending: list[dict[str, Any]] = []
        formal_blocks: list[dict[str, Any]] = []
        chapter_manifest: list[dict[str, Any]] = []
        for item in listing.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") == "archived":
                continue
            if item.get("is_leaf") is False:
                continue
            if not item.get("materialized"):
                continue
            chapter_id = str(item.get("chapter_id") or "")
            formal_rev = int(item.get("formal_content_revision") or 0)
            if formal_rev < 1:
                pending.append(
                    {
                        "chapter_id": chapter_id,
                        "reason": "missing_formal_revision",
                        "approval_status": item.get("approval_status"),
                        "head_content_revision": item.get("head_content_revision"),
                    }
                )
                continue
            content = self.store.chapter_content_revision(chapter_id, formal_rev)
            if content is None:
                pending.append(
                    {
                        "chapter_id": chapter_id,
                        "reason": "formal_revision_missing_payload",
                        "formal_content_revision": formal_rev,
                    }
                )
                continue
            chapter_manifest.append(
                {
                    "chapter_id": chapter_id,
                    "title": item.get("title"),
                    "order": item.get("order"),
                    "content_revision": formal_rev,
                    "content_hash": content.get("content_hash"),
                    "block_count": len(content.get("blocks") or []),
                }
            )
            for block in content.get("blocks") or []:
                if isinstance(block, dict):
                    formal_blocks.append(dict(block))
        for index, block in enumerate(formal_blocks):
            block["order"] = index
        export_allowed = len(pending) == 0 and len(chapter_manifest) > 0
        document_hash = hashlib.sha256(
            _json_stable(
                {
                    "chapters": chapter_manifest,
                    "blocks": [b.get("block_id") for b in formal_blocks],
                }
            ).encode("utf-8")
        ).hexdigest()
        return {
            "export_allowed": export_allowed,
            "pending_chapters": pending,
            "chapter_manifest": chapter_manifest,
            "blocks": formal_blocks,
            "document_hash": document_hash,
            "mode": "formal" if export_allowed else "draft_preview",
        }

    # --- Command handlers ---

    def handle_content_apply(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        operations = payload.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_INVALID",
                "operations 必须是非空数组。",
                status_code=400,
            )
        result = self.apply_operations(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            operations=operations,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节正文已更新: {chapter_id}",
            "chapter": result["chapter"],
            "content": result["content"],
            "unchanged": result.get("unchanged"),
        }

    def handle_revision_restore(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        try:
            from_rev = int(payload.get("from_content_revision"))
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_REVISION_INVALID",
                "from_content_revision 必须是整数。",
                status_code=400,
            ) from exc
        result = self.restore_revision(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            from_content_revision=from_rev,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"已恢复为新 head revision: {chapter_id}",
            "chapter": result["chapter"],
            "content": result["content"],
        }

    def handle_generate_draft(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        text = payload.get("text")
        if text is None or not str(text).strip():
            raise ControlPlaneError(
                "GROUNDING_RECEIPT_REQUIRED",
                "AI 草稿必须提供经过全局项目上下文校验的正文。",
                status_code=409,
            )
        try:
            requested_global = (
                str(payload.get("global_context_id") or ""),
                int(payload.get("global_context_revision")),
                str(payload.get("global_context_hash") or ""),
            )
            requested_chapter = (
                str(payload.get("chapter_context_id") or ""),
                int(payload.get("chapter_context_revision")),
                str(payload.get("chapter_context_hash") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "GROUNDING_RECEIPT_REQUIRED",
                "章节草稿必须携带全局项目事实和本章上下文版本。",
                status_code=409,
            ) from exc
        if not all((requested_global[0], requested_global[2])) or not all(
            (requested_chapter[0], requested_chapter[2])
        ):
            raise ControlPlaneError(
                "GROUNDING_RECEIPT_REQUIRED",
                "章节草稿必须携带全局项目事实和本章上下文版本。",
                status_code=409,
            )
        result = self.generate_draft(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            text=str(text) if text is not None else None,
            overwrite_locked=bool(payload.get("overwrite_locked")),
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
            expected_global_ref=requested_global,
            expected_chapter_ref=requested_chapter,
            evidence_batch_ids=[
                str(item)
                for item in payload.get("evidence_batch_ids") or []
                if str(item)
            ],
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节草稿 revision 已生成: {chapter_id}",
            "chapter": result["chapter"],
            "content": result.get("content"),
            "approval": result.get("approval"),
            "unchanged": result.get("unchanged"),
        }

    def handle_approval_confirm(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        try:
            content_revision = int(payload.get("content_revision"))
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_REVISION_INVALID",
                "content_revision 必须是整数。",
                status_code=400,
            ) from exc
        content_hash = str(payload.get("content_hash") or "").strip()
        if not content_hash:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_HASH_REQUIRED",
                "缺少 content_hash。",
                status_code=400,
            )
        result = self.confirm_approval(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            content_revision=content_revision,
            content_hash=content_hash,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"H2 章节正文已确认: {chapter_id}@{content_revision}",
            "chapter": result["chapter"],
            "content": result["content"],
            "approval": result["approval"],
        }
