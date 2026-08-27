"""Compatibility facade for bid-rewrite chapter execution.

Chapter generation itself is always delegated to ``ChapterWritingService``.
The effective mode and assigned legacy sources come from the promoted chapter
Blueprint; no chapter-level rematching or rewrite-plan decision is performed.
"""

from __future__ import annotations

from typing import Any, Iterator

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .chapter_writing_service import ChapterWritingRequest, ChapterWritingService


class BidRewriteExecutionService:
    """Backward-compatible adapter over the unified chapter writing service."""

    def __init__(self, context: WorkspaceContext, **_: Any) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build_request(
        self,
        chapter_id: str,
        *,
        operation_id: str,
        expected_workspace_revision: int | None,
        expected_chapter_revision: int,
        actor: dict[str, Any],
        overwrite_locked: bool = False,
        plan_revision: int | None = None,
        plan_hash: str = "",
    ) -> ChapterWritingRequest:
        self._require_mode()
        # Older batch payloads may still carry these fields. They no longer
        # participate in the body-generation decision.
        del plan_revision, plan_hash
        return ChapterWritingRequest(
            unit_id=f"chapter-{chapter_id}",
            node_ids=(chapter_id,),
            operation_id=operation_id,
            operation="rewrite",
            overwrite_locked=overwrite_locked,
            chapter_id=chapter_id,
            expected_workspace_revision=expected_workspace_revision,
            expected_chapter_revision=expected_chapter_revision,
            actor=dict(actor),
            run_research=True,
            commit_drafts=True,
        )

    def iter_events(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        request = self.build_request(**kwargs)
        yield from ChapterWritingService(self.context).iter_events(request)

    def execute(self, **kwargs: Any) -> Any:
        request = self.build_request(**kwargs)
        return ChapterWritingService(self.context).write(request)

    def _writing_service(self, request: ChapterWritingRequest) -> ChapterWritingService:
        del request
        return ChapterWritingService(self.context)

    def _require_mode(self) -> None:
        if self.store.workspace_profile().get("project_mode") != "bid_rewrite":
            raise ControlPlaneError(
                "REWRITE_MODE_REQUIRED",
                "当前工作空间不是标书改写模式。",
                status_code=409,
            )


__all__ = ["BidRewriteExecutionService"]
