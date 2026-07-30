from __future__ import annotations

import tempfile
import sys
from pathlib import Path
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
    provider_id = "deepseek-test"

    @staticmethod
    def runtime_status():
        return {"ready": True, "python_executable": "test-python"}

    @staticmethod
    def search(question: str, *, limit: int):
        return [
            ResearchCandidate(
                title="实施指南",
                publisher="example.gov.cn",
                content="公开实施方法",
                source_url="https://example.gov.cn/guide",
            )
        ]


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
            {"requirement_id": "REQ-1", "normalized_requirement": "系统实施、验收和质量控制"}
        ],
        document_target_constraints=[{"node_id": "CH-1", "title": "实施方案", "primary_requirement_ids": ["REQ-1"]}],
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

    def test_unready_runtime_blocks_current_chapter(self):
        class _Unavailable:
            provider_id = "deepseek-test"

            @staticmethod
            def runtime_status():
                return {"ready": False, "reason": "PLAYWRIGHT_PACKAGE_MISSING"}

        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary))
            with mock.patch("document_pipeline.writer_research.create_research_adapter", return_value=_Unavailable()):
                with self.assertRaises(ControlPlaneError) as raised:
                    WriterResearchCoordinator(
                        context,
                        deterministic_test=True,
                    ).resolve_for_bundle(_bundle())
            self.assertEqual(raised.exception.code, "WRITER_RESEARCH_ACTION_REQUIRED")

class WriterResearchEnabledTests(TestCase):
    def test_respects_provider_and_kill_switch(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "BID_AGENT_RESEARCH_PROVIDER": "doubao_web",
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
                "BID_AGENT_RESEARCH_PROVIDER": "doubao_web",
                "BID_AGENT_WRITER_RESEARCH_ENABLED": "0",
            },
            clear=False,
        ):
            self.assertFalse(writer_research_enabled())
