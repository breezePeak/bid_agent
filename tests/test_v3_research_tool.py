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
from document_pipeline.canonicalization import (  # noqa: E402
    canonical_hash,
    canonical_json,
)
from document_pipeline.contracts import EvidenceSourceType, InputRole  # noqa: E402
from document_pipeline.execution_controller import V3ExecutionController  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.planning_inference import (  # noqa: E402
    PROJECT_CAPABILITY_VERSION,
    PROJECT_SCHEMA_VERSION,
    ProjectEvidenceNeedCandidate,
    ProjectUnderstandingCandidate,
    StructuredInferenceResult,
)
from document_pipeline.research_service import ResearchCandidate  # noqa: E402
from document_pipeline.research_tool import V3ResearchTool  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


class _Provider:
    provider_id = "test"

    def search(self, question: str, *, limit: int):
        return [
            ResearchCandidate(
                title="适用标准",
                publisher="example.gov.cn",
                content="适用标准要求应形成全过程记录和可核验成果。",
                source_url="https://example.gov.cn/standard",
                source_type=EvidenceSourceType.OFFICIAL,
            )
        ]


class _FailingProvider:
    provider_id = "failing"

    def search(self, question: str, *, limit: int):
        raise RuntimeError("browser unavailable")


def _deterministic_review(_need, candidate):
    return {
        "verdict": "relevant",
        "confidence": 1.0,
        "reason": "deterministic fixture",
        "supporting_excerpts": [candidate.content],
        "extracted_points": [candidate.content],
        "usage_category": "industry_standard",
    }


class _ResearchProjectProvider:
    capability_id = "planning.project_understanding"
    capability_version = PROJECT_CAPABILITY_VERSION
    prompt_version = "test.research.project.v1"
    prompt_hash = canonical_hash({"prompt": prompt_version})
    schema_version = PROJECT_SCHEMA_VERSION
    provider_fingerprint = "test-research-project-provider"
    model_fingerprint = "test-research-project-model"
    temperature = 0.1

    def __init__(self, needs: list[dict[str, object]]) -> None:
        self.needs = needs

    def understand(self, request):
        score_model = getattr(request, "score_model", {}) or {}
        score_points = score_model.get("points", [])
        requirements = [
            item
            for item in request.requirement_ledger.get("requirements", [])
            if item.get("status") not in {"blocked", "waived"}
        ]
        semantic_refs = [
            *(
                f"RequirementLedger:{item['requirement_id']}"
                for item in requirements
            ),
            *(
                f"ScoreModel:{item['score_point_id']}"
                for item in score_points
            ),
        ]
        if not semantic_refs and request.source_context:
            semantic_refs = [
                f"SourceIndex:{request.source_context[0]['block_id']}"
            ]
        candidate = ProjectUnderstandingCandidate(
            evidence_needs=[
                ProjectEvidenceNeedCandidate(
                    local_id=str(item["need_id"]),
                    question=str(item["question"]),
                    topic_id=str(item["topic_id"]),
                    priority=str(item.get("priority") or "normal"),
                    blocking_scope=str(
                        item.get("blocking_scope") or "none"
                    ),
                    deadline_stage=str(item["deadline_stage"]),
                    query_budget=int(item.get("query_budget") or 0),
                    upstream_refs=semantic_refs,
                    confidence=1.0,
                )
                for item in self.needs
            ],
        )
        raw = canonical_json(candidate.model_dump(mode="json"))
        return StructuredInferenceResult(
            candidate=candidate,
            raw_output=raw,
            normalized_output=raw,
            reasoning="",
            input_snapshot=canonical_json(request.model_dump(mode="json")),
            attempt_count=1,
            capability_id=self.capability_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            provider_fingerprint=self.provider_fingerprint,
            model_fingerprint=self.model_fingerprint,
            temperature=self.temperature,
        )


class V3ResearchToolTests(unittest.TestCase):
    @staticmethod
    def _promote_project_model(
        context: WorkspaceContext,
        needs: list[dict[str, object]],
    ) -> dict[str, str]:
        inputs = InputManifestService(context)
        manifest = inputs.load()
        if not any(
            item.active and item.role is InputRole.TENDER
            for item in manifest.inputs
        ):
            tender = context.root / "research-fixture-tender.md"
            tender.write_text(
                "投标人须查询并核验项目适用标准。",
                encoding="utf-8",
            )
            inputs.register_local_file(tender, InputRole.TENDER)
        runner = V3StageRunner.for_deterministic_tests(
            context,
            project_understanding_provider=_ResearchProjectProvider(needs),
        )
        runner.run("normalize_sources")
        runner.run("build_requirement_ledger")
        runner.run("analyze_scores")
        model = runner.run("plan_response")
        return {
            str(source["need_id"]): next(
                need.need_id
                for need in model.evidence_needs
                if need.question == source["question"]
                and need.topic_id == source["topic_id"]
            )
            for source in needs
        }

    def test_agent_tool_resolves_only_declared_need(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            need_ids = self._promote_project_model(context, [{"need_id": "EN-1", "question": "适用标准", "topic_id": "standard", "deadline_stage": "plan_document", "query_budget": 1}])
            result = V3ResearchTool(
                context,
                _Provider(),
                semantic_reviewer=_deterministic_review,
            ).invoke(
                need_ids["EN-1"]
            )
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
            need_ids = self._promote_project_model(context, [{"need_id": "EN-FAIL", "question": "适用标准", "topic_id": "standard", "deadline_stage": "plan_document", "query_budget": 1}])
            gateway = CommandGateway(context, V3ExecutionController(context).handlers())
            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "research.resolve",
                    "payload": {"need_id": need_ids["EN-FAIL"]},
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

    def test_rejects_attachments_for_tavily(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            tender = root / "tender.md"
            tender.write_text(
                "投标人须查询并核验适用政策。",
                encoding="utf-8",
            )
            registration = InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            need_ids = self._promote_project_model(context, [{"need_id": "EN-ATTACH", "question": "查询适用政策", "topic_id": "policy", "deadline_stage": "plan_document", "query_budget": 1}])
            with self.assertRaisesRegex(
                ValueError,
                "V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED",
            ):
                V3ResearchTool(context).invoke(
                    need_ids["EN-ATTACH"],
                    attachment_input_ids=[registration.item.input_id],
                )

    def test_rejects_any_attachment_before_manifest_lookup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            need_ids = self._promote_project_model(context, [{"need_id": "EN-1", "question": "适用标准", "topic_id": "standard", "deadline_stage": "plan_document", "query_budget": 1}])
            with self.assertRaisesRegex(
                ValueError,
                "V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED",
            ):
                V3ResearchTool(context).invoke(
                    need_ids["EN-1"],
                    attachment_input_ids=["missing"],
                )
