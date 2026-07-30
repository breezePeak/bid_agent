from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.pipeline_policy import (  # noqa: E402
    configured_validation_failure_blocks,
    validation_failure_blocks,
)
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


class ValidationPolicyTests(unittest.TestCase):
    def test_old_configuration_defaults_to_non_blocking(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(
                "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE",
                None,
            )
            self.assertFalse(configured_validation_failure_blocks())

    def test_stage_runner_freezes_policy_for_the_current_command(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            with mock.patch.dict(
                os.environ,
                {
                    "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE": "0",
                },
                clear=False,
            ):
                runner = V3StageRunner.for_deterministic_tests(context)
                os.environ[
                    "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE"
                ] = "1"
                self.assertFalse(
                    runner.validation_failure_blocks_pipeline
                )
                with runner.validation_policy_scope():
                    self.assertFalse(validation_failure_blocks())
            with mock.patch.dict(
                os.environ,
                {
                    "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE": "1",
                },
                clear=False,
            ):
                runner = V3StageRunner.for_deterministic_tests(context)
                os.environ[
                    "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE"
                ] = "0"
                self.assertTrue(
                    runner.validation_failure_blocks_pipeline
                )
                with runner.validation_policy_scope():
                    self.assertTrue(validation_failure_blocks())


if __name__ == "__main__":
    unittest.main()
