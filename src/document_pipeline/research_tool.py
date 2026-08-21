"""Agent-callable V3 tool for resolving one declared EvidenceNeed."""

from __future__ import annotations

from typing import Any

from control_plane import ControlStore, WorkspaceContext
from .contracts import EvidenceNeed
from .project_model import load_promoted_project_model
from .score_model import load_promoted_score_model
from .research_adapters import ResearchProviderAdapter, create_research_adapter
from .research_service import ResearchService


class V3ResearchTool:
    """Resolve exactly one V3 EvidenceNeed through a configured provider adapter."""

    def __init__(self, context: WorkspaceContext, provider: ResearchProviderAdapter | None = None) -> None:
        self.context = context
        self.provider = provider

    def invoke(
        self,
        need_id: str,
        *,
        provider_id: str | None = None,
        attachment_input_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        need = self._need(need_id)
        attachment_ids = attachment_input_ids or []
        if not isinstance(attachment_ids, list):
            raise ValueError("V3_RESEARCH_ATTACHMENT_INPUT_IDS_INVALID")
        if attachment_ids:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        provider = self.provider or create_research_adapter(
            provider_id,
        )
        batch = ResearchService(self.context, provider).resolve(need)
        return {
            "provider_id": provider.provider_id,
            "need_id": need.need_id,
            "attachment_input_ids": attachment_ids,
            "batch": batch.model_dump(mode="json"),
        }

    def _need(self, need_id: str) -> EvidenceNeed:
        try:
            model = load_promoted_score_model(self.context)
        except Exception:
            model = None
        if model is not None:
            for candidate in model.evidence_need_candidates:
                if candidate.need_id == need_id:
                    return EvidenceNeed(
                        need_id=candidate.need_id,
                        question=candidate.question,
                        topic_id=f"score:{candidate.score_point_id}",
                        priority=candidate.priority,
                        blocking_scope=(
                            "content_unit"
                            if candidate.priority in {"blocking", "high"}
                            else "none"
                        ),
                        deadline_stage="chapter_writing",
                        query_budget=5,
                    )
        # Explicit legacy planning calls may still have promoted ProjectModel
        # evidence needs. This fallback is read-only and is not part of the
        # score-direct automatic pipeline.
        try:
            project = load_promoted_project_model(self.context)
        except Exception:
            project = None
        if project is not None:
            for need in project.evidence_needs:
                if need.need_id == need_id:
                    return need
        scheduled = ControlStore(self.context).evidence_need(need_id)
        if scheduled is not None:
            return EvidenceNeed.model_validate(
                {
                    key: value
                    for key, value in scheduled.items()
                    if key in EvidenceNeed.model_fields
                }
            )
        raise ValueError(f"V3_UNKNOWN_EVIDENCE_NEED: {need_id}")
