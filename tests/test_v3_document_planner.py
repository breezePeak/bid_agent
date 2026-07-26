from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.document_contract import DocumentContractCompiler  # noqa: E402
from document_pipeline.document_planner import DocumentPlanner  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.project_model import ProjectModelBuilder  # noqa: E402
from document_pipeline.requirement_ledger import RequirementLedgerBuilder  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402


class V3DocumentPlannerTests(unittest.TestCase):
    def test_each_requirement_has_exactly_one_primary_owner_and_units_are_not_leaf_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runs = base / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            tender = base / "tender.md"
            score = base / "score.md"
            tender.write_text("项目目标。\n\n服务范围；交付成果；验收条件；工期30日。", encoding="utf-8")
            score.write_text("评分要求：实施方案。", encoding="utf-8")
            inputs = InputManifestService(context)
            inputs.register_local_file(tender, InputRole.TENDER)
            inputs.register_local_file(score, InputRole.SCORE)
            SourceNormalizer(context).normalize_active_inputs()
            ledger = RequirementLedgerBuilder(context).build()
            ProjectModelBuilder(context).build()
            contract = DocumentContractCompiler(context).compile()
            plan, units = DocumentPlanner(context).build()
            owned = [item for node in plan.nodes for item in node.primary_requirement_ids]
            self.assertEqual(sorted(owned), sorted(item.requirement_id for item in ledger.requirements))
            self.assertEqual(len(owned), len(set(owned)))
            self.assertLess(len(units), len(contract.nodes))


if __name__ == "__main__":
    unittest.main()
