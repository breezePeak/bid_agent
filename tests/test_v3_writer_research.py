from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, WorkspaceContext
from document_pipeline.contracts import WriterInputBundle
from document_pipeline.research_service import ResearchCandidate
from document_pipeline.writer_research import (
    WRITER_RESEARCH_REPORT_PATH,
    WriterResearchCoordinator,
    writer_research_enabled,
)
from utils import read_json


class _Provider:
    provider_id = "tavily-test"

    @staticmethod
    def runtime_status():
        return {"ready": True, "python_executable": "test-python"}

    @staticmethod
    def search(question: str, *, limit: int):
        return [
            ResearchCandidate(
                title="实施指南",
                publisher="example.gov.cn",
                content="公开实施方法应建立全过程记录并形成可核验结果",
                source_url="https://example.gov.cn/guide",
            )
        ]


class _ThirdAttemptProvider:
    provider_id = "retry-test"

    def __init__(self) -> None:
        self.calls = 0
        self.questions: list[str] = []

    @staticmethod
    def runtime_status():
        return {"ready": True, "python_executable": "test-python"}

    def search(self, question: str, *, limit: int):
        self.calls += 1
        self.questions.append(question)
        if self.calls < 3:
            return []
        return _Provider.search(question, limit=limit)


def _bundle() -> WriterInputBundle:
    return WriterInputBundle(
        revision=1,
        source_hashes={},
        bundle_id="bundle-1",
        bundle_hash="hash",
        unit_id="unit-1",
        source_blueprint_artifact_id="artifact-1",
        source_blueprint_revision=1,
        source_blueprint_hash="blueprint-hash",
        h1_receipt_id="receipt-1",
        blueprint_slice=[{"chapter_id": "CH-1", "title": "实施方案"}],
        requirement_excerpts=[
            {"requirement_id": "REQ-1", "text": "系统实施、验收和质量控制", "normalized_requirement": "系统实施、验收和质量控制"}
        ],
        score_obligations=[{"score_point_id": "SP-1", "title": "实施方案完整性"}],
        document_target_constraints=[{"node_id": "CH-1", "title": "实施方案", "purpose": "说明项目实施路径", "writing_objectives": ["形成可验收的实施计划"], "primary_requirement_ids": ["REQ-1"]}],
        global_project_context={"project_scope": "建设、部署和验收范围"},
        chapter_grounding_context={"writing_purpose": {"title": "实施方案", "purpose": "说明项目实施路径", "writing_objectives": ["形成可验收的实施计划"]}},
        prompt_version="test",
        model_config_hash="test",
    )


class WriterResearchTests(TestCase):
    def _context(self, root: Path) -> WorkspaceContext:
        runs = root / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    def test_writer_time_decision_publishes_evidence_and_audit_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            coordinator = WriterResearchCoordinator(
                context,
                operation_id="op-1",
                deterministic_test=True,
            )
            with mock.patch("document_pipeline.writer_research.create_research_adapter", return_value=_Provider()):
                decision, snapshots = coordinator.resolve_for_bundle(_bundle())
            self.assertEqual(decision["decision_status"], "published")
            self.assertTrue(snapshots and snapshots[0]["evidence_ids"])
            report = read_json(context.root / WRITER_RESEARCH_REPORT_PATH)
            self.assertEqual(
                report["operations"]["op-1"][0]["decision_status"],
                "published",
            )

    def test_evidence_need_relevance_context_contains_frozen_chapter_inputs(self) -> None:
        context = WriterResearchCoordinator._relevance_context(_bundle())
        self.assertEqual(context["chapter_title"], "实施方案")
        self.assertEqual(context["chapter_purpose"], "说明项目实施路径")
        self.assertEqual(context["writing_objectives"], ["形成可验收的实施计划"])
        self.assertEqual(context["tender_requirements"], ["系统实施、验收和质量控制"])
        self.assertEqual(context["scoring_requirements"], ["实施方案完整性"])
        self.assertEqual(context["project_scope"], "建设、部署和验收范围")

    def test_enterprise_only_chapter_skips_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            decision, snapshots = WriterResearchCoordinator(
                context,
                deterministic_test=True,
            ).resolve_for_bundle(
                _bundle().model_copy(
                    update={
                        "requirement_excerpts": [
                            {
                                "requirement_id": "REQ-1",
                                "text": "企业资质和业绩",
                                "normalized_requirement": "企业资质和业绩",
                            }
                        ],
                        "document_target_constraints": [
                            {
                                "node_id": "CH-1",
                                "title": "企业资质",
                                "primary_requirement_ids": ["REQ-1"],
                            }
                        ],
                    }
                )
            )
            self.assertFalse(decision["needs_research"])
            self.assertEqual(snapshots, [])

    def test_missing_non_enterprise_material_uses_model_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            bundle = _bundle().model_copy(
                update={
                    "requirement_excerpts": [
                        {
                            "requirement_id": "REQ-1",
                            "normalized_requirement": "本章应结合项目需求形成响应说明",
                        }
                    ],
                    "document_target_constraints": [
                        {
                            "node_id": "CH-1",
                            "title": "项目响应说明",
                            "primary_requirement_ids": ["REQ-1"],
                        }
                    ],
                }
            )
            coordinator = WriterResearchCoordinator(
                context,
                decision_provider=lambda _request: {
                    "needs_research": False,
                    "reason": "现有招标资料足以编写本章。",
                    "queries": [],
                },
            )
            with mock.patch(
                "document_pipeline.writer_research.create_research_adapter",
                return_value=_Provider(),
            ):
                decision, snapshots = coordinator.resolve_for_bundle(bundle)

            self.assertFalse(decision["needs_research"])
            self.assertEqual(decision["decision_status"], "skipped")
            self.assertEqual(snapshots, [])

    def test_missing_tavily_key_blocks_with_actionable_reason(self):
        class _Unavailable:
            provider_id = "tavily-test"

            @staticmethod
            def runtime_status():
                return {"ready": False, "reason": "TAVILY_API_KEY_MISSING"}

        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            with mock.patch("document_pipeline.writer_research.create_research_adapter", return_value=_Unavailable()):
                with self.assertRaises(ControlPlaneError) as raised:
                    WriterResearchCoordinator(
                        context,
                        deterministic_test=True,
                    ).resolve_for_bundle(_bundle())
            self.assertEqual(raised.exception.code, "WRITER_RESEARCH_ACTION_REQUIRED")
            research = raised.exception.details["research"]
            self.assertEqual(
                research["queries"][0]["error"],
                "TAVILY_API_KEY_MISSING",
            )

    def test_failed_retrieval_is_retried_before_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            provider = _ThirdAttemptProvider()
            coordinator = WriterResearchCoordinator(
                context,
                deterministic_test=True,
                decision_provider=lambda _request: {
                    "need_research": True,
                    "reason": "需要公开实施指南。",
                    "search_query": "公开实施指南",
                    "existing_materials_sufficient": False,
                },
            )
            with (
                mock.patch(
                    "document_pipeline.writer_research.create_research_adapter",
                    return_value=provider,
                ),
                mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_WRITER_RESEARCH_MAX_ATTEMPTS": "3"},
                    clear=False,
                ),
            ):
                decision, snapshots = coordinator.resolve_for_bundle(_bundle())

            self.assertEqual(provider.calls, 3)
            self.assertEqual(len(set(provider.questions)), 3)
            self.assertIn("本年度", provider.questions[0])
            self.assertIn("相邻年度", provider.questions[1])
            self.assertIn("拆分检索", provider.questions[2])
            self.assertEqual(decision["decision_status"], "published")
            attempts = decision["queries"][0]["attempts"]
            self.assertEqual(len(attempts), 3)
            self.assertEqual(
                [item["query_strategy"] for item in attempts],
                [
                    "current_official_exact",
                    "latest_effective_official",
                    "workflow_components_official",
                ],
            )
            self.assertTrue(snapshots)

    def test_verified_authoritative_partial_batch_fills_writer_public_gap(self):
        batch = SimpleNamespace(
            status="gap",
            error="budget_exhausted",
            research_run={
                "satisfied_claim_ids": ["C1"],
                "missing_claim_ids": ["C2"],
            },
        )
        self.assertTrue(
            WriterResearchCoordinator._accept_verified_partial_batch(
                batch,
                [
                    {
                        "source_type": "official",
                        "source_url": "https://example.gov.cn/policy",
                    }
                ],
            )
        )
        self.assertFalse(
            WriterResearchCoordinator._accept_verified_partial_batch(
                batch,
                [{"source_type": "web", "source_url": "https://example.com/a"}],
            )
        )

class WriterResearchEnabledTests(TestCase):
    def test_respects_provider_and_kill_switch(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "BID_AGENT_RESEARCH_PROVIDER": "tavily",
                "BID_AGENT_WRITER_RESEARCH_ENABLED": "1",
            },
            clear=False,
        ):
            self.assertTrue(writer_research_enabled())
        with mock.patch.dict(
            "os.environ",
            {
                "BID_AGENT_RESEARCH_PROVIDER": "disabled",
                "BID_AGENT_WRITER_RESEARCH_ENABLED": "1",
            },
            clear=False,
        ):
            self.assertFalse(writer_research_enabled())
        with mock.patch.dict(
            "os.environ",
            {
                "BID_AGENT_RESEARCH_PROVIDER": "tavily",
                "BID_AGENT_WRITER_RESEARCH_ENABLED": "0",
            },
            clear=False,
        ):
            self.assertFalse(writer_research_enabled())
