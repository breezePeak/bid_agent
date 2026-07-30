from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.autonomous_research import (  # noqa: E402
    AUTO_RESEARCH_REPORT_PATH,
    AutonomousResearchCoordinator,
    PlannedResearchNeed,
)
from document_pipeline.contracts import EvidenceNeed, EvidenceSourceType  # noqa: E402
from document_pipeline.content_writer import ContentWriter  # noqa: E402
from document_pipeline.pipeline_policy import validation_policy_scope  # noqa: E402
from document_pipeline.research_service import ResearchCandidate  # noqa: E402
from document_pipeline.writer_bundle import WriterInputBundleAssembler  # noqa: E402


class _Provider:
    provider_id = "test-public-web"
    cache_fingerprint = ""

    def __init__(self) -> None:
        self.questions: list[str] = []

    def search(
        self,
        question: str,
        *,
        limit: int,
    ) -> list[ResearchCandidate]:
        self.questions.append(question)
        return [
            ResearchCandidate(
                title="公开实施指南",
                publisher="example.gov.cn",
                content="实施过程应建立可核验的质量控制、测试与验收记录。",
                source_url="https://example.gov.cn/guide",
                source_type=EvidenceSourceType.OFFICIAL,
                claim_types=("project_context", "method"),
            )
        ][:limit]


class _ExhaustedProvider:
    provider_id = "deepseek-test"
    cache_fingerprint = "retry-test"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        self.calls += 1
        if self.calls % 2:
            raise RuntimeError("temporary DeepSeek failure")
        return []


class AutonomousResearchTests(unittest.TestCase):
    @staticmethod
    def _models():
        requirement = SimpleNamespace(
            requirement_id="REQ-TECH",
            normalized_requirement=(
                "建设单位要求完成系统部署、接口联调、测试和验收，"
                "并提交全过程质量记录。"
            ),
        )
        qualification = SimpleNamespace(
            requirement_id="REQ-QUAL",
            normalized_requirement="投标人须提供企业资质和同类项目业绩。",
        )
        score = SimpleNamespace(
            score_point_id="SP-TECH",
            title="实施方案完整性",
            response_expectation="形成部署、联调、测试、验收的完整闭环",
        )
        blueprint = SimpleNamespace(
            revision=2,
            nodes=[
                SimpleNamespace(
                    chapter_id="CH-TECH",
                    title="系统实施与验收方案",
                    purpose="说明部署、联调、测试、质量控制及验收方法",
                    requirement_ids=["REQ-TECH"],
                    score_point_ids=["SP-TECH"],
                    target_size=1800,
                ),
                SimpleNamespace(
                    chapter_id="CH-QUAL",
                    title="企业资质与业绩",
                    purpose="响应资格证明要求",
                    requirement_ids=["REQ-QUAL"],
                    score_point_ids=[],
                    target_size=500,
                ),
            ],
        )
        return (
            blueprint,
            SimpleNamespace(requirements=[requirement, qualification]),
            SimpleNamespace(points=[score]),
        )

    def test_plans_current_bid_query_and_excludes_enterprise_only_topic(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            blueprint, ledger, scores = self._models()
            with (
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_chapter_blueprint",
                    return_value=blueprint,
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_requirement_ledger",
                    return_value=ledger,
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_score_model",
                    return_value=scores,
                ),
                mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_AUTO_RESEARCH_MAX_QUERIES": "3"},
                ),
            ):
                planned = AutonomousResearchCoordinator(
                    context,
                    enabled=True,
                ).plan()
            self.assertEqual(len(planned), 1)
            self.assertEqual(planned[0].chapter_id, "CH-TECH")
            self.assertIn("接口联调", planned[0].need.question)
            self.assertIn("实施方案完整性", planned[0].need.question)
            self.assertNotIn("企业资质和同类项目业绩", planned[0].need.question)

    def test_background_chapter_uses_public_project_context_and_deferred_sections_skip_research(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            requirement = SimpleNamespace(
                requirement_id="REQ-BG",
                normalized_requirement="围绕项目背景和建设范围形成项目理解。",
            )
            blueprint = SimpleNamespace(
                revision=3,
                nodes=[
                    SimpleNamespace(
                        chapter_id="CH-BG",
                        title="项目背景与项目理解",
                        purpose="说明公开背景、采购人需求和建设范围",
                        requirement_ids=["REQ-BG"],
                        score_point_ids=[],
                        target_size=1600,
                        content_policy="full",
                    ),
                    SimpleNamespace(
                        chapter_id="CH-PRICE",
                        title="报价一览表",
                        purpose="报价响应",
                        requirement_ids=[],
                        score_point_ids=[],
                        target_size=300,
                        content_policy="deferred_title_only",
                    ),
                ],
            )
            project = SimpleNamespace(
                identity={"项目名称": "智慧园区运维平台", "采购人": "某某管理委员会", "地区": "杭州"},
                background=["园区数字化运行管理升级。"],
                scope=["平台建设、数据接入和运维服务。"],
            )
            with (
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_chapter_blueprint",
                    return_value=blueprint,
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_requirement_ledger",
                    return_value=SimpleNamespace(requirements=[requirement]),
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_score_model",
                    return_value=SimpleNamespace(points=[]),
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_project_model",
                    return_value=project,
                ),
            ):
                coordinator = AutonomousResearchCoordinator(context, enabled=True)
                planned = coordinator.plan()
            self.assertTrue(planned)
            self.assertEqual({item.chapter_id for item in planned}, {"CH-BG"})
            self.assertIn("智慧园区运维平台", planned[0].need.question)
            decisions = coordinator._last_decisions
            self.assertEqual(len(decisions), 2)
            self.assertFalse(decisions[1]["needs_research"])
            self.assertIn("section_deferred", decisions[1]["reasons"])

    def test_resolves_and_freezes_published_evidence_for_writer_unit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            blueprint, ledger, scores = self._models()
            provider = _Provider()
            with (
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_chapter_blueprint",
                    return_value=blueprint,
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_requirement_ledger",
                    return_value=ledger,
                ),
                mock.patch(
                    "document_pipeline.autonomous_research.load_promoted_score_model",
                    return_value=scores,
                ),
                mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_AUTO_RESEARCH_MAX_QUERIES": "1"},
                ),
            ):
                report = AutonomousResearchCoordinator(
                    context,
                    provider=provider,
                    enabled=True,
                ).resolve()
            self.assertEqual(report["published_count"], 1)
            self.assertEqual(report["failed_count"], 0)
            self.assertEqual(len(provider.questions), 1)
            self.assertTrue(
                (context.root / AUTO_RESEARCH_REPORT_PATH).is_file()
            )
            stored = ControlStore(context).evidence_needs()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["status"], "satisfied")
            snapshot = WriterInputBundleAssembler(
                context
            )._evidence_snapshot(
                node_ids={"CH-TECH"},
                score_ids={"SP-TECH"},
                requirement_ids={"REQ-TECH"},
            )
            self.assertEqual(len(snapshot), 1)
            self.assertEqual(snapshot[0]["topic_id"], "chapter:CH-TECH")
            self.assertEqual(
                snapshot[0]["sources"][0]["source_url"],
                "https://example.gov.cn/guide",
            )
            self.assertTrue(snapshot[0]["evidence_ids"])
            clause = ContentWriter._research_clause(snapshot)
            self.assertIn("公开实施指南", clause)
            self.assertNotIn("https://example.gov.cn/guide", clause)

    def test_retries_failed_and_gap_three_times_then_continues_with_warning(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            provider = _ExhaustedProvider()
            coordinator = AutonomousResearchCoordinator(
                context,
                provider=provider,
                enabled=True,
            )
            planned = PlannedResearchNeed(
                need=EvidenceNeed(
                    need_id="EN-AUTO-0000000000000001",
                    question="查询公开实施标准及可核验来源",
                    topic_id="chapter:CH-1",
                    blocking_scope="none",
                    deadline_stage="execute_content_plan",
                    query_budget=2,
                ),
                chapter_id="CH-1",
                chapter_title="实施方案",
                score=10,
            )
            with (
                mock.patch.object(coordinator, "plan", return_value=[planned]),
                mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_AUTO_RESEARCH_MAX_RETRIES": "3"},
                ),
                validation_policy_scope(False),
            ):
                report = coordinator.resolve()
            self.assertEqual(provider.calls, 4)
            self.assertEqual(report["max_retries"], 3)
            self.assertEqual(report["results"][0]["attempt_count"], 4)
            self.assertTrue(report["results"][0]["exhausted"])
            self.assertEqual(
                [item["status"] for item in report["results"][0]["attempts"]],
                ["failed", "gap", "failed", "gap"],
            )
            self.assertEqual(
                report["warnings"][0]["policy_override"],
                "continue_with_warnings",
            )

    def test_research_exhaustion_blocks_when_validation_gate_is_enabled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            provider = _ExhaustedProvider()
            coordinator = AutonomousResearchCoordinator(
                context,
                provider=provider,
                enabled=True,
            )
            planned = PlannedResearchNeed(
                need=EvidenceNeed(
                    need_id="EN-AUTO-0000000000000002",
                    question="查询公开验收标准及可核验来源",
                    topic_id="chapter:CH-2",
                    blocking_scope="none",
                    deadline_stage="execute_content_plan",
                    query_budget=2,
                ),
                chapter_id="CH-2",
                chapter_title="验收方案",
                score=9,
            )
            with (
                mock.patch.object(coordinator, "plan", return_value=[planned]),
                mock.patch.dict(
                    "os.environ",
                    {"BID_AGENT_AUTO_RESEARCH_MAX_RETRIES": "3"},
                ),
                validation_policy_scope(True),
                self.assertRaises(ControlPlaneError) as raised,
            ):
                coordinator.resolve()
            self.assertEqual(provider.calls, 4)
            self.assertEqual(raised.exception.code, "AUTO_RESEARCH_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
