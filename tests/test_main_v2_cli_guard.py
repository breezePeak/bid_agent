from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import main  # noqa: E402


class MainV2CliGuardTests(unittest.TestCase):
    def test_managed_workspace_rejects_direct_stage_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            workspace = runs / "alpha"
            workspace.mkdir(parents=True)
            output = io.StringIO()
            with mock.patch.object(main, "project_root", return_value=workspace):
                with mock.patch.object(sys, "argv", ["main.py", "prepare-inputs"]):
                    with mock.patch.dict(
                        "os.environ",
                        {"BID_AGENT_RUNS_ROOT": str(runs), "BID_AGENT_EXECUTION_WORKER": "0"},
                        clear=False,
                    ):
                        with mock.patch.object(main, "_configure_console_encoding"):
                            with mock.patch.object(main, "_run_prepare_inputs") as runner:
                                with mock.patch("sys.stdout", output):
                                    exit_code = main.main()
            self.assertEqual(exit_code, 2)
            self.assertIn("V2 CommandGateway", output.getvalue())
            runner.assert_not_called()

    def test_pipeline_execution_worker_can_run_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            workspace = runs / "alpha"
            workspace.mkdir(parents=True)
            with mock.patch.object(main, "project_root", return_value=workspace):
                with mock.patch.object(sys, "argv", ["main.py", "prepare-inputs"]):
                    with mock.patch.dict(
                        "os.environ",
                        {"BID_AGENT_RUNS_ROOT": str(runs), "BID_AGENT_EXECUTION_WORKER": "1"},
                        clear=False,
                    ):
                        with mock.patch.object(main, "_configure_console_encoding"):
                            with mock.patch.object(main, "_run_prepare_inputs") as runner:
                                exit_code = main.main()
            self.assertEqual(exit_code, 0)
            runner.assert_called_once_with(workspace)

    def test_validate_is_rejected_outside_execution_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            workspace = runs / "alpha"
            workspace.mkdir(parents=True)
            with mock.patch.object(main, "project_root", return_value=workspace):
                with mock.patch.object(sys, "argv", ["main.py", "validate"]):
                    with mock.patch.dict(
                        "os.environ",
                        {"BID_AGENT_RUNS_ROOT": str(runs), "BID_AGENT_EXECUTION_WORKER": "0"},
                        clear=False,
                    ):
                        with mock.patch.object(main, "_configure_console_encoding"):
                            with mock.patch.object(main, "validate_project") as validate:
                                with mock.patch("sys.stdout", io.StringIO()):
                                    exit_code = main.main()
            self.assertEqual(exit_code, 2)
            validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
