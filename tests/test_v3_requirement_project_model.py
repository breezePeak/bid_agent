from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole, RequirementKind  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402


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
            ledger = V3StageRunner(context).run("build_requirement_ledger")
            self.assertTrue(any(item.kind is RequirementKind.SCORE for item in ledger.requirements))
            self.assertTrue(any(item.kind is RequirementKind.QUALIFICATION for item in ledger.requirements))
            self.assertTrue(any(item.kind is RequirementKind.DELIVERABLE for item in ledger.requirements))
            self.assertTrue(all(item.source_anchor.chunk_id and item.original_text for item in ledger.requirements))

    def test_project_model_can_form_tender_skeleton_without_external_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._prepare(Path(tmp), with_company=False)
            runner = V3StageRunner(context)
            runner.run("build_requirement_ledger")
            runner.run("analyze_scores")
            model = runner.run("plan_response")
            self.assertTrue(model.goals)
            self.assertTrue(model.scope)
            self.assertTrue(model.deliverables)
            self.assertTrue(model.acceptance_conditions)
            self.assertTrue(model.milestones)
            self.assertEqual(model.confirmed_facts, [])
            self.assertIn("EN-company-qualification", [need.need_id for need in model.evidence_needs])

    def test_external_reference_never_becomes_company_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._prepare(Path(tmp), with_company=True)
            runner = V3StageRunner(context)
            runner.run("build_requirement_ledger")
            runner.run("analyze_scores")
            model = runner.run("plan_response")
            facts = [fact.statement for fact in model.confirmed_facts]
            self.assertEqual(facts, ["本企业已提供有效资质证书。"])
            self.assertFalse(any("外部案例" in fact for fact in facts))


if __name__ == "__main__":
    unittest.main()
