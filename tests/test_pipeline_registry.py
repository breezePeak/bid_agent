from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_registry import (
    STAGE_SPECS,
    auto_run_commands,
    next_enabled_command_after,
    stage_command_map,
    workflow_stage_specs,
)
class PipelineRegistryTests(unittest.TestCase):
    def test_stage_command_map_is_derived_from_registry(self) -> None:
        self.assertEqual(
            stage_command_map(),
            {spec.id: spec.command for spec in STAGE_SPECS},
        )

    def test_standalone_review_stage_is_never_duplicated_in_pipeline(self) -> None:
        self.assertNotIn("review-fix-all", auto_run_commands())
        self.assertNotIn("review_fix_chapters", [spec.id for spec in workflow_stage_specs()])
        with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
            self.assertNotIn("review-fix-all", auto_run_commands())
            self.assertNotIn("global-review", auto_run_commands())
            self.assertNotIn("compliance-check", auto_run_commands())
            self.assertIn("build-source-trace", auto_run_commands())
            self.assertIn("build-score-coverage", auto_run_commands())
            self.assertIn("estimate-score", auto_run_commands())
            self.assertNotIn("summarize-all", auto_run_commands())
            self.assertIn("build-md", auto_run_commands())
            self.assertIn("build-docx", auto_run_commands())
            self.assertIn("check-format", auto_run_commands())
            self.assertNotIn("review_fix_chapters", [spec.id for spec in workflow_stage_specs()])

            build_md = next(spec for spec in workflow_stage_specs() if spec.command == "build-md")
            self.assertNotIn(
                "workspace/summaries/*_summary.json",
                {artifact.path for artifact in build_md.requires},
            )
            self.assertEqual(next_enabled_command_after("summarize-all"), "build-md")

if __name__ == "__main__":
    unittest.main()
