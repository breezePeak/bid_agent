from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import EvidenceBatch, EvidenceItem, EvidenceNeed, EvidenceSourceType
from .input_manifest import V3_ROOT


EVIDENCE_BATCH_DIR = V3_ROOT / "evidence" / "batches"


@dataclass(frozen=True)
class ResearchCandidate:
    title: str
    publisher: str
    content: str
    source_url: str | None = None
    source_type: EvidenceSourceType = EvidenceSourceType.WEB
    claim_types: tuple[str, ...] = ("project_context",)


class ResearchProvider(Protocol):
    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]: ...


class ResearchService:
    """On-demand research that publishes immutable, scoped evidence batches."""

    def __init__(self, context: WorkspaceContext, provider: ResearchProvider) -> None:
        self.context = context
        self.root = context.root
        self.provider = provider
        self.store = ControlStore(context)

    def resolve(self, need: EvidenceNeed) -> EvidenceBatch:
        self.store.upsert_evidence_need(need.model_dump(mode="json"))
        existing = self._published_batch(need)
        if existing is not None:
            self.store.upsert_evidence_need(
                {**need.model_dump(mode="json"), "status": "satisfied", "active_batch_id": existing.batch_id}
            )
            return existing
        if need.query_budget <= 0:
            latest = self._latest_batch(need)
            if latest is not None and latest.status == "gap":
                return latest
            return self._publish(need, [], query_count=0, status="gap")
        self.store.upsert_evidence_need({**need.model_dump(mode="json"), "status": "researching"})
        try:
            candidates = self.provider.search(need.question, limit=need.query_budget)
        except Exception as exc:
            return self._publish(
                need,
                [],
                query_count=1,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        items = [self._validate_candidate(need, candidate, index) for index, candidate in enumerate(candidates[: need.query_budget])]
        items = [item for item in items if item is not None]
        return self._publish(need, items, query_count=min(len(candidates), need.query_budget), status="published" if items else "gap")

    def _validate_candidate(self, need: EvidenceNeed, candidate: ResearchCandidate, index: int) -> EvidenceItem | None:
        if not candidate.title.strip() or not candidate.publisher.strip() or not candidate.content.strip():
            return None
        if candidate.source_type is EvidenceSourceType.COMPANY:
            # A research provider is external by definition; company records must
            # arrive through the explicit company input role.
            return None
        if "enterprise_capability" in candidate.claim_types:
            return None
        batch_id = self._base_batch_id(need)
        evidence_id = f"E-{hashlib.sha256(f'{batch_id}:{index}:{candidate.source_url or candidate.title}'.encode('utf-8')).hexdigest()[:16]}"
        return EvidenceItem(
            evidence_id=evidence_id,
            batch_id=batch_id,
            source_type=candidate.source_type,
            title=candidate.title,
            source_url=candidate.source_url,
            publisher=candidate.publisher,
            content=candidate.content,
            claim_types=list(candidate.claim_types),
            retrieved_at=datetime.now(UTC).isoformat(),
        )

    def _publish(
        self,
        need: EvidenceNeed,
        items: list[EvidenceItem],
        *,
        query_count: int,
        status: str,
        error: str | None = None,
    ) -> EvidenceBatch:
        previous = self._batches(need)
        revision = max((batch.revision for batch in previous), default=0) + 1
        base_id = self._base_batch_id(need)
        batch_id = base_id if revision == 1 else f"{base_id}-R{revision}"
        normalized = []
        for index, item in enumerate(items):
            evidence_seed = f"{batch_id}:{index}:{item.source_url or item.title}"
            normalized.append(
                item.model_copy(
                    update={
                        "batch_id": batch_id,
                        "evidence_id": f"E-{hashlib.sha256(evidence_seed.encode('utf-8')).hexdigest()[:16]}",
                    }
                )
            )
        source_hashes = {
            need.need_id: hashlib.sha256(need.question.encode("utf-8")).hexdigest(),
        }
        if provider_fingerprint := self._provider_fingerprint():
            source_hashes["research_attachments"] = provider_fingerprint
        batch = EvidenceBatch(
            revision=revision,
            source_hashes=source_hashes,
            batch_id=batch_id,
            need_id=need.need_id,
            query_count=query_count,
            items=normalized,
            status=status,
            error=error,
        )
        path = self.root / EVIDENCE_BATCH_DIR / f"{batch_id}.json"
        if path.exists():
            existing = EvidenceBatch.model_validate(read_json(path))
            if existing.model_dump(mode="json") != batch.model_dump(mode="json"):
                raise ValueError("EvidenceBatch 不可变；相同 need 不能覆盖已有证据快照")
        else:
            write_json(path, batch.model_dump(mode="json"))
        next_status = "satisfied" if status == "published" else "gap"
        self.store.upsert_evidence_need({**need.model_dump(mode="json"), "status": next_status, "active_batch_id": batch_id})
        return batch

    def _base_batch_id(self, need: EvidenceNeed) -> str:
        seed = f"{need.need_id}:{need.question}"
        if provider_fingerprint := self._provider_fingerprint():
            seed += f":{provider_fingerprint}"
        return f"EB-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"

    def _provider_fingerprint(self) -> str:
        return str(getattr(self.provider, "cache_fingerprint", "") or "").strip()

    def _batches(self, need: EvidenceNeed) -> list[EvidenceBatch]:
        directory = self.root / EVIDENCE_BATCH_DIR
        if not directory.is_dir():
            return []
        prefix = self._base_batch_id(need)
        batches: list[EvidenceBatch] = []
        for path in directory.glob(f"{prefix}*.json"):
            try:
                batch = EvidenceBatch.model_validate(read_json(path))
            except Exception:
                continue
            if batch.need_id == need.need_id:
                batches.append(batch)
        return sorted(batches, key=lambda item: item.revision)

    def _latest_batch(self, need: EvidenceNeed) -> EvidenceBatch | None:
        batches = self._batches(need)
        return batches[-1] if batches else None

    def _published_batch(self, need: EvidenceNeed) -> EvidenceBatch | None:
        return next(
            (batch for batch in reversed(self._batches(need)) if batch.status == "published"),
            None,
        )
