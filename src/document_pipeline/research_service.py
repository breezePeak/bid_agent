from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import (
    EvidenceBatch,
    EvidenceItem,
    EvidenceNeed,
    EvidenceRelevanceTier,
    EvidenceSourceType,
)
from .input_manifest import V3_ROOT


EVIDENCE_BATCH_DIR = V3_ROOT / "evidence" / "batches"
RESEARCH_RELEVANCE_POLICY_VERSION = "v3.project-relevance.v1"


def load_published_batch(context: WorkspaceContext, batch_id: str) -> EvidenceBatch | None:
    """Read one path-safe published batch without depending on a scheduler."""
    normalized = str(batch_id or "").strip()
    if not re.fullmatch(r"EB-[0-9a-f]{16}(?:-R[1-9][0-9]*)?", normalized):
        return None
    path = context.root / EVIDENCE_BATCH_DIR / f"{normalized}.json"
    if not path.is_file():
        return None
    try:
        batch = EvidenceBatch.model_validate(read_json(path))
    except Exception:
        return None
    return batch if batch.status == "published" else None


@dataclass(frozen=True)
class ResearchCandidate:
    title: str
    publisher: str
    content: str
    source_url: str | None = None
    source_type: EvidenceSourceType = EvidenceSourceType.WEB
    claim_types: tuple[str, ...] = ("project_context",)
    relevance_tier: EvidenceRelevanceTier | None = None
    supporting_excerpt: str = ""


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
            # Retrieve a broader candidate set, then adopt only the best three
            # relevant sources.  A real URL is not evidence of relevance.
            candidates = self.provider.search(
                need.question,
                limit=max(12, need.query_budget),
            )
        except Exception as exc:
            return self._publish(
                need,
                [],
                query_count=1,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        items = [
            self._validate_candidate(need, candidate, index)
            for index, candidate in enumerate(candidates)
        ]
        items = [item for item in items if item is not None]
        rank = {
            EvidenceRelevanceTier.PROJECT_DIRECT: 0,
            EvidenceRelevanceTier.SIMILAR_PROJECT: 1,
            EvidenceRelevanceTier.INDUSTRY_STANDARD: 2,
            EvidenceRelevanceTier.GENERAL_REFERENCE: 3,
        }
        items.sort(
            key=lambda item: (
                rank[item.relevance_tier],
                -len(item.matched_project_anchors),
                -len(item.matched_task_anchors),
                item.evidence_id,
            )
        )
        adopted_limit = min(need.query_budget, need.max_adopted_items)
        items = items[:adopted_limit]
        return self._publish(
            need,
            items,
            query_count=1,
            status="published" if items else "gap",
        )

    def _validate_candidate(self, need: EvidenceNeed, candidate: ResearchCandidate, index: int) -> EvidenceItem | None:
        if not candidate.title.strip() or not candidate.publisher.strip() or not candidate.content.strip():
            return None
        if candidate.source_type is EvidenceSourceType.COMPANY:
            # A research provider is external by definition; company records must
            # arrive through the explicit company input role.
            return None
        if "enterprise_capability" in candidate.claim_types:
            return None
        tier, matched_project, matched_task = self._classify_relevance(
            need, candidate
        )
        if tier is None:
            return None
        allowed = set(need.allowed_relevance_tiers)
        if tier not in allowed and not (
            not need.project_anchors
            and not need.task_anchors
            and tier is EvidenceRelevanceTier.GENERAL_REFERENCE
        ):
            return None
        claim_types = [
            str(item)
            for item in candidate.claim_types
            if str(item) != "enterprise_capability"
        ]
        if tier is not EvidenceRelevanceTier.PROJECT_DIRECT:
            claim_types = [
                item for item in claim_types if item != "project_context"
            ]
            if tier is EvidenceRelevanceTier.INDUSTRY_STANDARD:
                claim_types = list(dict.fromkeys([*claim_types, "standard", "method"]))
            else:
                claim_types = list(dict.fromkeys([*claim_types, "method"]))
        usage_constraints = []
        if tier is EvidenceRelevanceTier.SIMILAR_PROJECT:
            usage_constraints.append(
                "仅可支持实施方法、质量控制、风险或验收思路，不得写成当前项目事实。"
            )
        elif tier is EvidenceRelevanceTier.INDUSTRY_STANDARD:
            usage_constraints.append(
                "仅可支持现行标准、专业方法和检查验收依据，不得替代招标文件中的项目事实。"
            )
        elif tier is EvidenceRelevanceTier.GENERAL_REFERENCE:
            usage_constraints.append("仅供线索核对，不得写入当前项目正文。")
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
            claim_types=claim_types,
            retrieved_at=datetime.now(UTC).isoformat(),
            relevance_tier=tier,
            matched_project_anchors=matched_project,
            matched_task_anchors=matched_task,
            supporting_excerpt=(
                str(candidate.supporting_excerpt or "").strip()
                or self._supporting_excerpt(
                    candidate.content,
                    [*matched_project, *matched_task],
                )
            ),
            usage_constraints=usage_constraints,
        )

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).casefold()

    @classmethod
    def _matched_anchors(
        cls,
        anchors: list[str],
        haystack: str,
    ) -> list[str]:
        compact_haystack = cls._normalized(haystack)
        matches: list[str] = []
        for anchor in anchors:
            value = str(anchor or "").strip()
            compact = cls._normalized(value)
            if len(compact) >= 3 and compact in compact_haystack:
                matches.append(value)
        return list(dict.fromkeys(matches))

    @classmethod
    def _classify_relevance(
        cls,
        need: EvidenceNeed,
        candidate: ResearchCandidate,
    ) -> tuple[EvidenceRelevanceTier | None, list[str], list[str]]:
        text = "\n".join(
            (
                candidate.title,
                candidate.publisher,
                candidate.source_url or "",
                candidate.content,
            )
        )
        matched_project = cls._matched_anchors(need.project_anchors, text)
        matched_task = cls._matched_anchors(need.task_anchors, text)
        project_aware = bool(need.project_anchors or need.task_anchors)
        explicit = candidate.relevance_tier
        if matched_project:
            tier = EvidenceRelevanceTier.PROJECT_DIRECT
        elif not project_aware:
            if explicit is not None:
                tier = explicit
            elif candidate.source_type is EvidenceSourceType.STANDARD:
                tier = EvidenceRelevanceTier.INDUSTRY_STANDARD
            else:
                tier = EvidenceRelevanceTier.GENERAL_REFERENCE
        elif matched_task:
            standard_cues = re.search(
                r"标准|规范|规程|指南|办法|技术要求|质量要求|验收",
                text,
            )
            if (
                candidate.source_type is EvidenceSourceType.STANDARD
                or standard_cues
                or explicit is EvidenceRelevanceTier.INDUSTRY_STANDARD
            ):
                tier = EvidenceRelevanceTier.INDUSTRY_STANDARD
            else:
                tier = EvidenceRelevanceTier.SIMILAR_PROJECT
        else:
            # Even genuine government pages are rejected when they cannot show
            # which current-project or current-task conclusion they support.
            return None, [], []
        if explicit is EvidenceRelevanceTier.PROJECT_DIRECT and not matched_project:
            return None, [], []
        return tier, matched_project, matched_task

    @staticmethod
    def _supporting_excerpt(content: str, anchors: list[str]) -> str:
        paragraphs = [
            item.strip()
            for item in re.split(r"[\r\n]+", str(content or ""))
            if item.strip()
        ]
        for paragraph in paragraphs:
            if any(str(anchor) in paragraph for anchor in anchors):
                return paragraph[:800]
        return str(content or "").strip()[:800]

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
            "relevance_policy": hashlib.sha256(
                RESEARCH_RELEVANCE_POLICY_VERSION.encode("utf-8")
            ).hexdigest(),
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
        next_status = {
            "published": "satisfied",
            "gap": "gap",
            "failed": "open",
        }[status]
        self.store.upsert_evidence_need({**need.model_dump(mode="json"), "status": next_status, "active_batch_id": batch_id})
        return batch

    def _base_batch_id(self, need: EvidenceNeed) -> str:
        seed = (
            f"{RESEARCH_RELEVANCE_POLICY_VERSION}:"
            f"{need.need_id}:{need.question}:"
            f"{'|'.join(need.project_anchors)}:"
            f"{'|'.join(need.task_anchors)}:"
            f"{'|'.join(item.value for item in need.allowed_relevance_tiers)}"
        )
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
