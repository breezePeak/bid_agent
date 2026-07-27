from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import EvidenceNeed, EvidenceSourceType  # noqa: E402
from document_pipeline.research_service import ResearchCandidate, ResearchService  # noqa: E402


class _Provider:
    def __init__(
        self,
        candidates: list[ResearchCandidate],
        *,
        cache_fingerprint: str = "",
    ) -> None:
        self.candidates = candidates
        self.calls = 0
        self.cache_fingerprint = cache_fingerprint

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        self.calls += 1
        return self.candidates[:limit]


class _FailsOnceProvider(_Provider):
    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary browser failure")
        return self.candidates[:limit]


class V3ResearchServiceTests(unittest.TestCase):
    def _context(self, base: Path) -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    def test_publishes_immutable_evidence_batch_and_updates_control_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _Provider([ResearchCandidate(title="国家标准", publisher="标准发布机构", content="标准正文", source_type=EvidenceSourceType.STANDARD)])
            service = ResearchService(self._context(Path(tmp)), provider)
            need = EvidenceNeed(need_id="EN-1", question="适用标准", topic_id="standard", deadline_stage="plan_document", query_budget=2)
            batch = service.resolve(need)
            self.assertEqual(batch.status, "published")
            self.assertEqual(len(batch.items), 1)
            self.assertEqual(service.store.evidence_need("EN-1")["status"], "satisfied")
            self.assertEqual(service.resolve(need).batch_id, batch.batch_id)

    def test_budget_exhaustion_creates_explicit_gap_without_searching(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _Provider([])
            service = ResearchService(self._context(Path(tmp)), provider)
            need = EvidenceNeed(need_id="EN-2", question="缺失资料", topic_id="gap", deadline_stage="execute_content_plan", query_budget=0)
            batch = service.resolve(need)
            self.assertEqual(batch.status, "gap")
            self.assertEqual(provider.calls, 0)
            self.assertEqual(service.store.evidence_need("EN-2")["status"], "gap")

    def test_external_research_cannot_publish_enterprise_capability(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _Provider([ResearchCandidate(title="案例", publisher="网站", content="案例内容", claim_types=("enterprise_capability",))])
            service = ResearchService(self._context(Path(tmp)), provider)
            need = EvidenceNeed(need_id="EN-3", question="企业能力", topic_id="company", deadline_stage="execute_content_plan", query_budget=1)
            self.assertEqual(service.resolve(need).status, "gap")

    def test_failed_research_can_retry_without_overwriting_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _FailsOnceProvider(
                [ResearchCandidate(title="公开标准", publisher="标准机构", content="标准正文")]
            )
            service = ResearchService(self._context(Path(tmp)), provider)
            need = EvidenceNeed(
                need_id="EN-4",
                question="公开标准",
                topic_id="standard",
                deadline_stage="plan_document",
                query_budget=2,
            )
            failed = service.resolve(need)
            published = service.resolve(need)
            self.assertEqual(failed.status, "failed")
            self.assertIn("temporary browser failure", failed.error or "")
            self.assertEqual(published.status, "published")
            self.assertEqual(published.revision, 2)
            self.assertNotEqual(failed.batch_id, published.batch_id)
            self.assertEqual(provider.calls, 2)

    def test_attachment_fingerprint_scopes_the_evidence_cache(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = self._context(Path(tmp))
            candidate = ResearchCandidate(
                title="公开标准",
                publisher="标准机构",
                content="标准正文",
            )
            need = EvidenceNeed(
                need_id="EN-5",
                question="公开标准",
                topic_id="standard",
                deadline_stage="plan_document",
                query_budget=1,
            )
            first = ResearchService(
                context,
                _Provider([candidate], cache_fingerprint="a" * 64),
            ).resolve(need)
            second = ResearchService(
                context,
                _Provider([candidate], cache_fingerprint="b" * 64),
            ).resolve(need)
            self.assertNotEqual(first.batch_id, second.batch_id)
            self.assertEqual(first.source_hashes["research_attachments"], "a" * 64)
            self.assertEqual(second.source_hashes["research_attachments"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
