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
        self.last_limit = 0
        self.cache_fingerprint = cache_fingerprint

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        self.calls += 1
        self.last_limit = limit
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
            self.assertEqual(failed.status, "failed")
            self.assertIn("temporary browser failure", failed.error or "")
            self.assertEqual(service.store.evidence_need("EN-4")["status"], "open")

            published = service.resolve(need)
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

    def test_project_aware_research_filters_generic_government_pages_and_labels_usage(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _Provider([
                ResearchCandidate(
                    title="关于完善招标投标市场规范健康发展的意见",
                    publisher="中国政府网",
                    content="发挥市场竞争作用，完善招标投标交易制度。",
                    source_url="https://www.gov.cn/policy/bidding",
                    source_type=EvidenceSourceType.OFFICIAL,
                ),
                ResearchCandidate(
                    title="2026年度全国国土变更调查监测数据核实处理项目采购公告",
                    publisher="中国国土勘测规划院",
                    content="本项目开展国家级内外业核查和质量控制。",
                    source_url="https://example.gov.cn/current-project",
                    source_type=EvidenceSourceType.OFFICIAL,
                ),
                ResearchCandidate(
                    title="某年度国土变更调查核查项目实施方案",
                    publisher="某省自然资源厅",
                    content="同类项目采用国家级内外业核查，并形成成果复核记录。",
                    source_url="https://example.gov.cn/similar-project",
                    source_type=EvidenceSourceType.OFFICIAL,
                ),
                ResearchCandidate(
                    title="国土调查成果质量检查规范",
                    publisher="国家标准全文公开系统",
                    content="规范提出质量控制和成果复核要求。",
                    source_url="https://std.samr.gov.cn/standard",
                    source_type=EvidenceSourceType.STANDARD,
                ),
            ])
            need = EvidenceNeed(
                need_id="EN-PROJECT",
                question="检索本项目、同类项目和行业标准",
                topic_id="chapter:background",
                deadline_stage="chapter_draft_stream",
                query_budget=3,
                project_anchors=["2026年度全国国土变更调查监测数据核实处理项目"],
                task_anchors=["国家级内外业核查", "质量控制", "成果复核"],
            )
            batch = ResearchService(self._context(Path(tmp)), provider).resolve(need)
            self.assertEqual(batch.status, "published")
            self.assertEqual(provider.last_limit, 12)
            self.assertEqual(
                {item.relevance_tier.value for item in batch.items},
                {"project_direct", "similar_project", "industry_standard"},
            )
            self.assertNotIn(
                "关于完善招标投标市场规范健康发展的意见",
                [item.title for item in batch.items],
            )
            non_direct = [
                item
                for item in batch.items
                if item.relevance_tier.value != "project_direct"
            ]
            self.assertTrue(all("project_context" not in item.claim_types for item in non_direct))
            self.assertTrue(all(item.usage_constraints for item in non_direct))

    def test_true_but_unrelated_sources_publish_an_explicit_gap(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            provider = _Provider([
                ResearchCandidate(
                    title="政府采购需求管理办法",
                    publisher="财政部",
                    content="采购人应依法开展采购需求管理。",
                    source_url="https://www.gov.cn/policy/procurement",
                    source_type=EvidenceSourceType.OFFICIAL,
                )
            ])
            need = EvidenceNeed(
                need_id="EN-IRRELEVANT",
                question="核实当前国土调查项目背景",
                topic_id="chapter:background",
                deadline_stage="chapter_draft_stream",
                query_budget=3,
                project_anchors=["全国国土变更调查监测数据核实处理项目"],
                task_anchors=["国家级内外业核查", "成果复核"],
            )
            batch = ResearchService(self._context(Path(tmp)), provider).resolve(need)
            self.assertEqual(batch.status, "gap")
            self.assertEqual(batch.items, [])


if __name__ == "__main__":
    unittest.main()
