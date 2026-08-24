from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import (  # noqa: E402
    canonical_hash,
    canonical_json,
)
from document_pipeline.contracts import InputRole, RequirementKind  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.planning_inference import (  # noqa: E402
    PROJECT_CAPABILITY_VERSION,
    PROJECT_SCHEMA_VERSION,
    CitedStatementCandidate,
    ProjectEvidenceNeedCandidate,
    ProjectFactCandidate,
    ProjectUnderstandingCandidate,
    StructuredInferenceResult,
)
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402


class _FakeProjectUnderstandingProvider:
    capability_id = "planning.project_understanding"
    capability_version = PROJECT_CAPABILITY_VERSION
    prompt_version = "test.project.semantic.v1"
    prompt_hash = canonical_hash({"prompt": prompt_version})
    schema_version = PROJECT_SCHEMA_VERSION
    provider_fingerprint = "test-project-understanding-provider"
    model_fingerprint = "test-project-understanding-model"
    temperature = 0.1

    @staticmethod
    def _statement(item: dict[str, object]) -> CitedStatementCandidate:
        requirement_id = str(item["requirement_id"])
        return CitedStatementCandidate(
            text=str(item["normalized_requirement"]),
            upstream_refs=[f"RequirementLedger:{requirement_id}"],
            confidence=1.0,
        )

    def understand(self, request):
        requirements = [
            item
            for item in request.requirement_ledger["requirements"]
            if item["status"] not in {"blocked", "waived"}
        ]

        def select(token: str) -> list[CitedStatementCandidate]:
            return [
                self._statement(item)
                for item in requirements
                if token in str(item["normalized_requirement"])
            ][:1]

        qualifications = [
            item
            for item in requirements
            if item["kind"] == RequirementKind.QUALIFICATION.value
        ]
        company_blocks = [
            item
            for item in request.source_context
            if item["input_role"] == InputRole.COMPANY.value
        ]
        evidence_needs = []
        if qualifications and not company_blocks:
            requirement_id = str(qualifications[0]["requirement_id"])
            evidence_needs.append(
                ProjectEvidenceNeedCandidate(
                    local_id="company-qualification",
                    question="请补充与资格要求对应的企业资质材料。",
                    topic_id="company_qualification",
                    priority="blocking",
                    blocking_scope="content_unit",
                    deadline_stage="write_content",
                    query_budget=0,
                    upstream_refs=[
                        f"RequirementLedger:{requirement_id}"
                    ],
                    confidence=1.0,
                )
            )
        facts = [
            ProjectFactCandidate(
                local_id=f"company-{index}",
                statement=str(block["content"]),
                classification="confirmed",
                upstream_refs=[f"SourceIndex:{block['block_id']}"],
                confidence=1.0,
            )
            for index, block in enumerate(company_blocks, start=1)
        ]
        formal_refs = [
            f"RequirementLedger:{item['requirement_id']}"
            for item in requirements
        ]
        facts.append(
            ProjectFactCandidate(
                local_id="semantic-coverage",
                statement="基于全部已晋级需求与评分点形成项目整体理解。",
                classification="inference",
                upstream_refs=formal_refs,
                requirement_ids=[
                    str(item["requirement_id"]) for item in requirements
                ],
                confidence=1.0,
            )
        )
        candidate = ProjectUnderstandingCandidate(
            goals=select("项目目标"),
            scope=select("服务范围"),
            deliverables=select("交付成果"),
            acceptance_conditions=select("验收条件"),
            milestones=select("工期"),
            facts=facts,
            evidence_needs=evidence_needs,
            review_status="confirmed",
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


class V3RequirementProjectModelTests(unittest.TestCase):
    def _context(self, base: Path) -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    def _prepare(self, base: Path, *, with_company: bool) -> WorkspaceContext:
        files = {
            "tender.md": "城市治理项目服务要求\n\n项目目标是建设统一治理服务。\n\n服务范围包括数据治理；交付成果为实施报告；验收条件为通过采购人验收；工期为 30 个工作日。\n\n供应商须具备相关资质证书。",
            "score.md": "评分项：项目实施方案完整性。",
            "reference.md": "外部案例声称具有资质，但不能作为企业事实。",
            "company.md": "本企业已提供有效资质证书。",
        }
        for name, content in files.items():
            (base / name).write_text(content, encoding="utf-8")
        context = self._context(base)
        inputs = InputManifestService(context)
        inputs.register_local_file(base / "tender.md", InputRole.TENDER)
        inputs.register_local_file(base / "score.md", InputRole.SCORE)
        inputs.register_local_file(base / "reference.md", InputRole.REFERENCE)
        if with_company:
            inputs.register_local_file(base / "company.md", InputRole.COMPANY)
        SourceNormalizer(context).normalize_active_inputs()
        return context

    def test_requirement_ledger_unifies_tender_and_score_with_source_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._prepare(Path(tmp), with_company=False)
            ledger = V3StageRunner.for_deterministic_tests(context).run(
                "build_requirement_ledger"
            )
            self.assertTrue(any(item.kind is RequirementKind.SCORE for item in ledger.requirements))
            self.assertTrue(any(item.kind is RequirementKind.QUALIFICATION for item in ledger.requirements))
            self.assertTrue(any(item.kind is RequirementKind.DELIVERABLE for item in ledger.requirements))
            self.assertTrue(all(item.source_anchor.chunk_id and item.original_text for item in ledger.requirements))

    def test_project_model_can_form_tender_skeleton_without_external_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._prepare(Path(tmp), with_company=False)
            runner = V3StageRunner.for_deterministic_tests(
                context,
                project_understanding_provider=(
                    _FakeProjectUnderstandingProvider()
                ),
            )
            runner.run("build_requirement_ledger")
            runner.run("analyze_scores")
            model = runner.run("plan_response")
            self.assertTrue(model.goals)
            self.assertTrue(model.scope)
            self.assertTrue(model.deliverables)
            self.assertTrue(model.acceptance_conditions)
            self.assertTrue(model.milestones)
            self.assertFalse(
                any(
                    "本企业" in fact.statement
                    or "外部案例" in fact.statement
                    for fact in model.confirmed_facts
                )
            )
            self.assertTrue(
                any(
                    need.topic_id == "company_qualification"
                    and need.priority == "blocking"
                    for need in model.evidence_needs
                )
            )

    def test_external_reference_never_becomes_company_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._prepare(Path(tmp), with_company=True)
            runner = V3StageRunner.for_deterministic_tests(
                context,
                project_understanding_provider=(
                    _FakeProjectUnderstandingProvider()
                ),
            )
            runner.run("build_requirement_ledger")
            runner.run("analyze_scores")
            model = runner.run("plan_response")
            facts = [fact.statement for fact in model.confirmed_facts]
            self.assertIn("本企业已提供有效资质证书。", facts)
            self.assertFalse(any("外部案例" in fact for fact in facts))


if __name__ == "__main__":
    unittest.main()
