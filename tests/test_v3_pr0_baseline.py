from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import main  # noqa: E402
from pipeline_registry import STAGE_SPECS, workflow_stage_specs  # noqa: E402


class V3Pr0BaselineTests(unittest.TestCase):
    def test_experimental_research_stages_are_not_in_automatic_pipeline(self) -> None:
        experimental = {
            spec.id
            for spec in STAGE_SPECS
            if not spec.auto_run
        }
        self.assertEqual(experimental, {"analyze_project_understanding", "research_project_materials"})
        self.assertFalse(experimental & {spec.id for spec in workflow_stage_specs()})

    def test_every_automatic_stage_has_exactly_one_main_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runners = main._pipeline_stage_runners(Path(tmp), workers=1, max_retries=0)
        auto_stage_ids = [spec.id for spec in workflow_stage_specs()]
        self.assertEqual(len(auto_stage_ids), len(set(auto_stage_ids)))
        self.assertTrue(set(auto_stage_ids).issubset(runners))

    def test_pipeline_rejects_unimplemented_registered_stage(self) -> None:
        unknown = SimpleNamespace(id="unknown_stage", label="未知阶段")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(main, "workflow_stage_specs", return_value=[unknown]):
                with self.assertRaisesRegex(RuntimeError, "缺少 stage runner: unknown_stage"):
                    main.run_pipeline(Path(tmp), workers=1)


if __name__ == "__main__":
    unittest.main()
