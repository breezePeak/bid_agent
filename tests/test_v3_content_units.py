from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.content_scheduler import ContentUnitScheduler  # noqa: E402
from document_pipeline.content_writer import ContentWriter  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.document_contract import DocumentContractCompiler  # noqa: E402
from document_pipeline.document_planner import DocumentPlanner  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402


class V3ContentUnitTests(unittest.TestCase):
    def test_writer_outputs_only_planned_targets_and_records_completion(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
            runner = V3StageRunner(context)
            runner.run("build_requirement_ledger")
            runner.run("analyze_scores")
            runner.run("plan_response")
            contract = DocumentContractCompiler(context).compile()
            _, units = DocumentPlanner(context).build()
            scheduler = ContentUnitScheduler(context)
            self.assertEqual(scheduler.initialize(), units)
            blocks = ContentWriter(context).write(units[0].unit_id, units[0].node_ids)
            self.assertTrue(blocks)
            self.assertTrue({block.target_node_id for block in blocks}.issubset({node.node_id for node in contract.nodes}))
            self.assertEqual(scheduler.store.upsert_content_unit_state({"unit_id": units[0].unit_id, "contract_revision": 1, "state": "completed"})["state"], "completed")


if __name__ == "__main__":
    unittest.main()
