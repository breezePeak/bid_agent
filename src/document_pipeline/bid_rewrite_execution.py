from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterator

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .chapter_rewrite_plan import ChapterRewritePlanService
from .chapter_writing_service import ChapterWritingRequest, ChapterWritingService
from .content_gate import WriterBundleContentGate
from .contracts import ContentBlock


class BidRewriteExecutionService:
    """Build a confirmed rewrite plan into the existing chapter-writing request.

    This service owns only the rewrite control-plane checks.  It deliberately
    delegates generation, gates, streaming and draft persistence to
    ``ChapterWritingService`` so rewrite mode never gains a second writer.
    """

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        plan_service: ChapterRewritePlanService | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.plan_service = plan_service or ChapterRewritePlanService(context)

    def build_request(
        self,
        chapter_id: str,
        *,
        operation_id: str,
        expected_workspace_revision: int | None,
        expected_chapter_revision: int,
        actor: dict[str, Any],
        overwrite_locked: bool = False,
    ) -> ChapterWritingRequest:
        self._require_mode()
        plan = self.plan_service.get(chapter_id)
        if plan.get("stale") or str(plan.get("status") or "") != "confirmed":
            raise ControlPlaneError(
                "REWRITE_PLAN_INCOMPLETE",
                "当前章节的改写方案尚未确认或已过期。",
                status_code=409,
            )
        confirmation = plan.get("confirmation") or {}
        if (
            int(confirmation.get("plan_revision") or 0) != int(plan.get("plan_revision") or 0)
            or str(confirmation.get("plan_hash") or "") != str(plan.get("plan_hash") or "")
        ):
            raise ControlPlaneError(
                "REWRITE_PLAN_INCOMPLETE",
                "改写方案确认回执与当前版本不一致。",
                status_code=409,
            )
        dependencies = plan.get("dependencies") or {}
        if int(dependencies.get("chapter_revision") or 0) != int(expected_chapter_revision):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_CHAPTER_CONFLICT",
                "章节版本已变化，请刷新后重新确认改写方案。",
                status_code=409,
            )
        unresolved = [
            item for item in plan.get("pollution_findings") or []
            if isinstance(item, dict) and item.get("status") != "resolved"
        ]
        if unresolved:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_POLLUTION_UNRESOLVED",
                "仍有旧项目污染风险未解决，不能开始改写。",
                status_code=409,
                details={"finding_ids": [item.get("finding_id") for item in unresolved]},
            )
        rewrite_context = self._rewrite_context(plan)
        return ChapterWritingRequest(
            unit_id=f"rewrite-{chapter_id}",
            node_ids=(chapter_id,),
            operation_id=operation_id,
            operation="rewrite",
            user_instruction=str(plan.get("instruction") or ""),
            chapter_writing_plan={
                **deepcopy(plan.get("writing_plan") or {}),
                "rewrite_context": rewrite_context,
            },
            overwrite_locked=overwrite_locked,
            chapter_id=chapter_id,
            expected_workspace_revision=expected_workspace_revision,
            expected_chapter_revision=expected_chapter_revision,
            actor=dict(actor),
            run_research=False,
            commit_drafts=True,
        )

    def iter_events(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        request = self.build_request(**kwargs)
        yield from self._writing_service(request).iter_events(request)

    def execute(self, **kwargs: Any) -> Any:
        """Non-streaming command entry; returns the ordinary WriterResult."""
        request = self.build_request(**kwargs)
        return self._writing_service(request).write(request)

    def _writing_service(self, request: ChapterWritingRequest) -> ChapterWritingService:
        rewrite_context = dict(request.chapter_writing_plan.get("rewrite_context") or {})
        writer = (
            _CopyWriter(rewrite_context)
            if rewrite_context.get("rewrite_strategy") == "copy"
            else None
        )
        return ChapterWritingService(
            self.context,
            writer=writer,
            quality_gate=_RewriteQualityGate(rewrite_context),
        )

    def _rewrite_context(self, plan: dict[str, Any]) -> dict[str, Any]:
        artifact = self.store.v3_active_artifact("LegacyBidIndex") or {}
        payload = artifact.get("payload") or {}
        blocks = {
            str(item.get("block_id") or ""): item
            for item in payload.get("blocks") or []
            if isinstance(item, dict)
        }
        selected: list[dict[str, Any]] = []
        for reference in plan.get("selected_legacy_blocks") or []:
            if not isinstance(reference, dict):
                continue
            block_id = str(reference.get("block_id") or "")
            block = blocks.get(block_id)
            if not block or str(block.get("content_hash") or "") != str(reference.get("content_hash") or ""):
                raise ControlPlaneError(
                    "CHAPTER_REWRITE_LEGACY_REFERENCE_INVALID",
                    "已确认方案引用的旧文块已变化，请重新确认方案。",
                    status_code=409,
                    details={"block_id": block_id},
                )
            selected.append(
                {
                    "block_id": block_id,
                    "section_id": str(reference.get("section_id") or ""),
                    "content_hash": str(reference.get("content_hash") or ""),
                    "usage": str(reference.get("usage") or "light_edit"),
                    "instruction": str(reference.get("instruction") or ""),
                    "content": str(block.get("content") or ""),
                }
            )
        return {
            "rewrite_schema": "v1",
            "rewrite_strategy": str(plan.get("strategy") or "new_write"),
            "selected_legacy_sources": selected,
            "new_content_items": deepcopy(plan.get("new_content_items") or []),
            "selected_evidence_refs": [
                evidence_id
                for item in plan.get("new_content_items") or []
                if isinstance(item, dict)
                for evidence_id in item.get("evidence_ids") or []
            ],
            "replacement_map": [
                {
                    "finding_id": item.get("finding_id"),
                    "source_text": item.get("source_text"),
                    "replacement_text": item.get("replacement_text"),
                }
                for item in plan.get("pollution_findings") or []
                if isinstance(item, dict) and item.get("status") == "resolved"
            ],
            "pollution_receipt": {
                "plan_revision": plan.get("plan_revision"),
                "plan_hash": plan.get("plan_hash"),
                "finding_count": len(plan.get("pollution_findings") or []),
            },
        }

    def _require_mode(self) -> None:
        if self.store.workspace_profile().get("project_mode") != "bid_rewrite":
            raise ControlPlaneError("REWRITE_MODE_REQUIRED", "当前工作空间不是标书改写模式。", status_code=409)


__all__ = ["BidRewriteExecutionService"]


class _CopyWriter:
    """A deterministic adapter for exact approved legacy reuse.

    It is passed to the existing writing service, therefore the same bundle
    quality gate and draft commit path still apply while no model call occurs.
    """

    def __init__(self, rewrite_context: dict[str, Any]) -> None:
        self.rewrite_context = rewrite_context

    def stream_bundle(self, bundle: Any, *, operation_id: str = "") -> list[ContentBlock]:
        del operation_id
        replacements = {
            str(item.get("source_text") or ""): str(item.get("replacement_text") or "")
            for item in self.rewrite_context.get("replacement_map") or []
            if isinstance(item, dict)
        }
        paragraphs: list[str] = []
        for item in self.rewrite_context.get("selected_legacy_sources") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "")
            for source, replacement in replacements.items():
                if source:
                    content = content.replace(source, replacement)
            if content.strip():
                paragraphs.append(content.strip())
        if not paragraphs:
            raise ControlPlaneError(
                "REWRITE_PLAN_INCOMPLETE",
                "直接复用方案没有可写入的已确认旧文块。",
                status_code=409,
            )
        remaining = [
            source
            for source in replacements
            if source and any(source in paragraph for paragraph in paragraphs)
        ]
        if remaining:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_POLLUTION_UNRESOLVED",
                "旧项目污染替换未完全生效，已阻止草稿写入。",
                status_code=409,
                details={"source_texts": remaining},
            )
        target = str((bundle.document_target_constraints or [{}])[0].get("output_target") or bundle.chapter_id or "")
        return [
            ContentBlock(
                block_id=f"{bundle.bundle_id}-{target}-copy",
                target_node_id=target,
                type="paragraph",
                content="\n\n".join(paragraphs),
                confidence=1.0,
                source_bundle_hash=bundle.bundle_hash,
                source="AI_GENERATED",
                created_by="rewrite-copy",
                updated_by="rewrite-copy",
            )
        ]


class _RewriteQualityGate:
    """Keep the ordinary content gate, then reject surviving old-project text."""

    def __init__(self, rewrite_context: dict[str, Any]) -> None:
        self.base = WriterBundleContentGate()
        self.forbidden = {
            str(item.get("source_text") or "").strip()
            for item in rewrite_context.get("replacement_map") or []
            if isinstance(item, dict)
        }
        self.forbidden.discard("")

    def validate(self, bundle: Any, blocks: list[ContentBlock]) -> Any:
        proposal = self.base.validate(bundle, blocks)
        surviving = sorted(
            text
            for text in self.forbidden
            if any(text in str(block.content or "") for block in blocks)
        )
        if surviving:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_POLLUTION_UNRESOLVED",
                "生成正文仍包含旧项目污染内容，已阻止草稿写入。",
                status_code=409,
                details={"source_texts": surviving},
            )
        return proposal
