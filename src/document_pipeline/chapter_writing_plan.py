"""Authoritative chapter writing-plan control-plane service.

PR-02 persists and confirms plans without changing Writer authorization.  The
legacy inline writer continues to use its compatibility JSON projection while
the exact plan revision, dependency snapshot and receipt live in control.db.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from control_plane import (
    CommandEnvelope,
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)

from .contracts import (
    ChapterPlanBinding,
    ChapterPlanContentUnit,
    ChapterWritingPlanCandidate,
    ChapterWritingPlanPayload,
)
from .input_manifest import V3_ROOT
from .workspace_modes import CHAPTER_PLAN_V2_ENABLED_ENV, env_flag


LEGACY_WRITING_PLAN_PATH = V3_ROOT / "chapter_chats" / "_writing_plans.json"


class ChapterWritingPlanService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    @staticmethod
    def enabled() -> bool:
        return env_flag(CHAPTER_PLAN_V2_ENABLED_ENV)

    def require_enabled(self) -> None:
        if not self.enabled():
            raise ControlPlaneError(
                "CAPABILITY_DISABLED",
                "章节编写规划 v2 能力未启用。",
                status_code=403,
            )

    @staticmethod
    def dependency_fingerprint(snapshot: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def candidate_from_legacy(
        chapter_id: str,
        writing_plan: dict[str, Any],
    ) -> ChapterWritingPlanCandidate:
        blocks = [
            item
            for item in (writing_plan.get("blocks") or [])
            if isinstance(item, dict)
        ]
        units: list[ChapterPlanContentUnit] = []
        for index, block in enumerate(blocks):
            title = str(block.get("heading") or f"写作要点 {index + 1}").strip()
            must_answer = str(block.get("must_answer") or "").strip()
            write_as = str(block.get("write_as") or "").strip()
            instructions = "\n".join(
                item for item in (must_answer, write_as) if item
            ).strip()
            if not instructions:
                continue
            refs = []
            for value in (
                block.get("project_fact_refs") or block.get("source_refs") or []
            ):
                ref = str(value or "").strip()
                if ref and ref not in refs:
                    refs.append(ref)
            units.append(
                ChapterPlanContentUnit(
                    unit_id=str(block.get("block_id") or f"legacy-{index + 1}"),
                    title=title,
                    instructions=instructions,
                    order=len(units),
                    source_refs=refs,
                )
            )
        if not units:
            raise ControlPlaneError(
                "LEGACY_PLAN_INVALID",
                "旧 WritingPlan 没有可导入的内容块。",
                status_code=400,
            )
        legacy_hash = hashlib.sha256(
            json.dumps(
                writing_plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ChapterWritingPlanCandidate(
            content_units=units,
            metadata={
                "projection": "legacy_inline",
                "legacy_schema_version": str(
                    writing_plan.get("schema_version") or ""
                ),
                "legacy_plan_hash": legacy_hash,
                "legacy_chapter_id": str(
                    writing_plan.get("chapter_id") or chapter_id
                ),
            },
        )

    def append(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        plan: dict[str, Any],
        source: str = "agent_proposal",
    ) -> dict[str, Any]:
        try:
            candidate = ChapterWritingPlanCandidate.model_validate(plan)
        except Exception as exc:
            raise ControlPlaneError(
                "PLAN_INVALID",
                "章节编写规划候选不符合严格 Schema。",
                status_code=400,
                details={"error": f"{type(exc).__name__}: {exc}"[:1200]},
            ) from exc
        snapshot = self.store.chapter_plan_dependency_snapshot(chapter_id)
        binding = ChapterPlanBinding.model_validate(snapshot)
        payload = ChapterWritingPlanPayload(
            content_units=candidate.content_units,
            metadata=candidate.metadata,
            binding=binding,
        )
        fingerprint = self.dependency_fingerprint(snapshot)
        return self.store.append_chapter_writing_plan(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            plan=payload.model_dump(mode="json"),
            dependency_fingerprint=fingerprint,
            source=source,
        )

    def append_legacy_projection(
        self,
        *,
        chapter_id: str,
        writing_plan: dict[str, Any],
        seed_only: bool = False,
    ) -> dict[str, Any]:
        chapter = self.store.chapter_workspace(chapter_id)
        if chapter is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND",
                f"章节 Workspace 不存在: {chapter_id}",
                status_code=404,
            )
        if seed_only and int(chapter.get("head_plan_revision") or 0) > 0:
            return {
                "chapter": chapter,
                "plan": self.read(chapter_id),
                "unchanged": True,
            }
        candidate = self.candidate_from_legacy(chapter_id, writing_plan)
        return self.append(
            chapter_id=chapter_id,
            expected_chapter_revision=int(chapter.get("chapter_revision") or 0),
            plan=candidate.model_dump(mode="json"),
            source="legacy_projection",
        )

    def read(self, chapter_id: str, revision: int | None = None) -> dict[str, Any] | None:
        return self.store.chapter_writing_plan(chapter_id, revision)

    def confirm(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        plan_revision: int,
        plan_hash: str,
        dependency_fingerprint: str,
        principal_id: str,
    ) -> dict[str, Any]:
        return self.store.confirm_chapter_writing_plan(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            plan_revision=plan_revision,
            plan_hash=plan_hash,
            dependency_fingerprint=dependency_fingerprint,
            principal_id=principal_id,
        )

    def invalidate(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        actor: dict[str, Any],
        reason: str = "",
    ) -> dict[str, Any]:
        return self.store.invalidate_chapter_writing_plan(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            actor=actor,
            reason=reason,
        )

    def import_legacy_json(self, path: Path | None = None) -> dict[str, Any]:
        """Best-effort, idempotent seed import; never deletes or rewrites JSON."""
        source_path = path or (self.context.root / LEGACY_WRITING_PLAN_PATH)
        summary: dict[str, Any] = {
            "path": str(source_path),
            "imported": 0,
            "unchanged": 0,
            "failed": [],
        }
        if not self.enabled():
            summary["disabled"] = True
            return summary
        if not source_path.is_file():
            return summary
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except Exception as exc:
            summary["failed"].append(
                {"chapter_id": "", "error": f"{type(exc).__name__}: {exc}"[:500]}
            )
            return summary
        chapters = raw.get("chapters") if isinstance(raw, dict) else None
        if not isinstance(chapters, dict):
            summary["failed"].append(
                {"chapter_id": "", "error": "legacy plan store chapters 非对象"}
            )
            return summary
        for chapter_id, item in sorted(chapters.items()):
            writing_plan = item.get("writing_plan") if isinstance(item, dict) else None
            if not isinstance(writing_plan, dict):
                continue
            try:
                result = self.append_legacy_projection(
                    chapter_id=str(chapter_id),
                    writing_plan=writing_plan,
                    seed_only=True,
                )
                key = "unchanged" if result.get("unchanged") else "imported"
                summary[key] += 1
            except Exception as exc:
                summary["failed"].append(
                    {
                        "chapter_id": str(chapter_id),
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
        return summary

    @staticmethod
    def legacy_projection(plan: dict[str, Any]) -> dict[str, Any]:
        units = [
            item
            for item in (plan.get("content_units") or [])
            if isinstance(item, dict)
        ]
        blocks = [
            {
                "block_id": str(item.get("unit_id") or f"WO-{index + 1}"),
                "kind": "response",
                "heading": str(item.get("title") or f"写作要点 {index + 1}"),
                "must_answer": str(item.get("instructions") or ""),
                "write_as": "",
                "outcome_kind": "",
                "score_point_id": "",
                "condition_id": "",
                "requirement_ids": [],
                "ownership": "primary",
                "project_fact_refs": list(item.get("source_refs") or []),
            }
            for index, item in enumerate(units)
        ]
        return {
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": str(plan.get("chapter_id") or ""),
            "chapter_title": "",
            "purpose": "",
            "writing_objectives": [],
            "block_count": len(blocks),
            "blocks": blocks,
            "usable_local_facts": [],
            "usable_project_facts": [],
            "writing_rule": "由 control.db 权威规划重建的兼容投影。",
        }

    @staticmethod
    def _expected_revision(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("expected_chapter_revision"))
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc

    def handle_append(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        self.require_enabled()
        chapter_id = str(payload.get("chapter_id") or "").strip()
        plan = payload.get("plan")
        if not chapter_id or not isinstance(plan, dict):
            raise ControlPlaneError(
                "PLAN_COMMAND_INVALID",
                "缺少 chapter_id 或 plan。",
                status_code=400,
            )
        result = self.append(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_revision(payload),
            plan=plan,
            source="agent_proposal",
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节规划已追加: {chapter_id}",
            **result,
        }

    def handle_confirm(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        actor = envelope.actor if isinstance(envelope.actor, dict) else {}
        self.require_enabled()
        if str(actor.get("type") or "") != "user" or not str(
            actor.get("id") or ""
        ).strip():
            raise ControlPlaneError(
                "PLAN_APPROVAL_INVALID",
                "章节规划确认只接受已认证用户。",
                status_code=403,
            )
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        try:
            plan_revision = int(payload.get("plan_revision"))
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "PLAN_REVISION_INVALID",
                "plan_revision 必须是整数。",
                status_code=400,
            ) from exc
        receipt = self.confirm(
            chapter_id=str(payload.get("chapter_id") or ""),
            expected_chapter_revision=self._expected_revision(payload),
            plan_revision=plan_revision,
            plan_hash=str(payload.get("plan_hash") or ""),
            dependency_fingerprint=str(
                payload.get("dependency_fingerprint") or ""
            ),
            principal_id=str(actor.get("id") or ""),
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": "章节规划已确认。",
            "receipt": receipt,
            "chapter": self.store.chapter_workspace(
                str(payload.get("chapter_id") or "")
            ),
        }

    def handle_invalidate(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        actor = envelope.actor if isinstance(envelope.actor, dict) else {}
        self.require_enabled()
        result = self.invalidate(
            chapter_id=str(payload.get("chapter_id") or ""),
            expected_chapter_revision=self._expected_revision(payload),
            actor=actor,
            reason=str(payload.get("reason") or ""),
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": "章节规划确认已失效。",
            **result,
        }
