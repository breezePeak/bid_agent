from __future__ import annotations

import re
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_registry import (
    auto_run_commands,
    next_enabled_command_after,
    stage_command_map,
    workflow_stage_specs,
)
from web_app import STAGE_TO_COMMAND, WORKFLOW_STEPS


class PipelineRegistryTests(unittest.TestCase):
    def test_web_workflow_comes_from_registry(self) -> None:
        registry_specs = workflow_stage_specs()
        self.assertEqual([spec.id for spec in registry_specs], [step["id"] for step in WORKFLOW_STEPS])
        self.assertEqual([spec.command for spec in registry_specs], [step["command"] for step in WORKFLOW_STEPS])

    def test_stage_command_map_matches_web_mapping(self) -> None:
        self.assertEqual(stage_command_map(), STAGE_TO_COMMAND)

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

    def test_all_registry_artifacts_are_mapped_in_web_status(self) -> None:
        web_app_text = (ROOT / "src" / "web_app.py").read_text(encoding="utf-8")
        step_status_match = re.search(
            r"def _step_status\(.*?key_map = \{(.*?)\n\s*\}",
            web_app_text,
            re.DOTALL,
        )
        self.assertIsNotNone(step_status_match, "未找到 web_app._step_status 的 key_map 定义。")
        key_map_block = step_status_match.group(1)
        mapped_paths = set(re.findall(r'"([^"]+)":', key_map_block))

        registry_paths = {
            artifact.path
            for spec in workflow_stage_specs()
            for artifact in (*spec.requires, *spec.produces)
            if artifact.kind != "virtual"
        }
        self.assertFalse(
            sorted(registry_paths - mapped_paths),
            f"以下 registry 产物/依赖未接入 Web 状态映射: {sorted(registry_paths - mapped_paths)}",
        )


if __name__ == "__main__":
    unittest.main()
