from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from control_plane import CommandEnvelope, CommandGateway, ControlStore  # noqa: E402
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.contracts import EvidenceSourceType, InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService, V3_ROOT  # noqa: E402
from document_pipeline.project_model import PROJECT_MODEL_PATH  # noqa: E402
from document_pipeline.research_service import ResearchCandidate  # noqa: E402
from document_pipeline.research_tool import V3ResearchTool  # noqa: E402
from utils import write_json  # noqa: E402


class _Provider:
    provider_id = "test"

    def search(self, question: str, *, limit: int):
        return [ResearchCandidate(title="公开资料", publisher="测试来源", content="可核验项目背景", source_type=EvidenceSourceType.WEB)]


class _FailingProvider:
    provider_id = "failing"

    def search(self, question: str, *, limit: int):
        raise RuntimeError("browser unavailable")


class V3ResearchToolTests(unittest.TestCase):
    def test_agent_tool_resolves_only_declared_need(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            write_json(context.root / PROJECT_MODEL_PATH, {"evidence_needs": [{"need_id": "EN-1", "question": "适用标准", "topic_id": "standard", "deadline_stage": "plan_document", "query_budget": 1}]})
            result = V3ResearchTool(context, _Provider()).invoke("EN-1")
            self.assertEqual(result["provider_id"], "test")
            self.assertEqual(result["batch"]["status"], "published")
            with self.assertRaisesRegex(ValueError, "V3_UNKNOWN_EVIDENCE_NEED"):
                V3ResearchTool(context, _Provider()).invoke("EN-missing")

    def test_gateway_registers_research_as_an_agent_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            self.assertIn("research.resolve", V3ExecutionController(context).handlers())
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            receipt = gateway.submit(CommandEnvelope.from_mapping({"kind": "research.resolve", "payload": {}, "expected_revision": ControlStore(context).revision(), "idempotency_key": "missing-need"}, workspace_id="alpha"))
            self.assertEqual(receipt.status, "rejected")

    def test_gateway_reports_failed_provider_as_failed_command(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            write_json(
                context.root / PROJECT_MODEL_PATH,
                {
                    "evidence_needs": [
                        {
                            "need_id": "EN-FAIL",
                            "question": "适用标准",
                            "topic_id": "standard",
                            "deadline_stage": "plan_document",
                            "query_budget": 1,
                        }
                    ]
                },
            )
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "research.resolve",
                    "payload": {"need_id": "EN-FAIL"},
                    "expected_revision": ControlStore(context).revision(),
                    "idempotency_key": "failed-provider",
                },
                workspace_id="alpha",
            )
            with mock.patch(
                "document_pipeline.research_tool.create_research_adapter",
                return_value=_FailingProvider(),
            ):
                receipt = gateway.submit(envelope)
            self.assertEqual(receipt.status, "rejected")
            self.assertEqual(receipt.error["code"], "V3_RESEARCH_FAILED")
            self.assertIn("browser unavailable", receipt.message)

    def test_resolves_explicit_active_manifest_inputs_as_deepseek_attachments(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            tender = root / "tender.md"
            tender.write_text("无敏感信息的招标测试内容", encoding="utf-8")
            registration = InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            write_json(
                context.root / PROJECT_MODEL_PATH,
                {
                    "evidence_needs": [
                        {
                            "need_id": "EN-ATTACH",
                            "question": "查询适用政策",
                            "topic_id": "policy",
                            "deadline_stage": "plan_document",
                            "query_budget": 1,
                        }
                    ]
                },
            )
            expected_path = (
                context.root
                / V3_ROOT
                / "sources"
                / registration.item.input_id
                / registration.item.filename
            ).resolve()
            provider = _Provider()
            with mock.patch(
                "document_pipeline.research_tool.create_research_adapter",
                return_value=provider,
            ) as factory:
                result = V3ResearchTool(context).invoke(
                    "EN-ATTACH",
                    attachment_input_ids=[registration.item.input_id],
                )
            factory.assert_called_once_with(None, attachment_paths=[expected_path])
            self.assertEqual(result["attachment_input_ids"], [registration.item.input_id])

    def test_rejects_unknown_or_inactive_attachment_input(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            write_json(
                context.root / PROJECT_MODEL_PATH,
                {
                    "evidence_needs": [
                        {
                            "need_id": "EN-1",
                            "question": "适用标准",
                            "topic_id": "standard",
                            "deadline_stage": "plan_document",
                            "query_budget": 1,
                        }
                    ]
                },
            )
            with self.assertRaisesRegex(ValueError, "V3_RESEARCH_ATTACHMENT_NOT_ACTIVE"):
                V3ResearchTool(context).invoke(
                    "EN-1",
                    attachment_input_ids=["missing"],
                )
