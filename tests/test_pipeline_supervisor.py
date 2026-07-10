from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_supervisor import PipelineSupervisor


def _wait_for_status(supervisor: PipelineSupervisor, root: Path, expected: str) -> dict:
    deadline = time.time() + 3
    while time.time() < deadline:
        payload = supervisor.load(root)
        if payload.get("status") == expected:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"未等到状态 {expected}: {supervisor.load(root)}")


class PipelineSupervisorTests(unittest.TestCase):
    def test_backend_supervisor_advances_all_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            completed: set[str] = set()
            calls: list[str] = []

            def runner(command: str, run_id: str, run_root: Path) -> int:
                calls.append(command)
                completed.add(command)
                return 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a", "b", "c"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", side_effect=lambda r, stage: stage in completed),
            ):
                self.assertTrue(supervisor.start("run-1", root, runner))
                payload = _wait_for_status(supervisor, root, "complete")

            self.assertEqual(calls, ["a", "b", "c"])
            self.assertEqual(payload["message"], "完整流程已完成")

    def test_resume_starts_at_requested_stage_and_reuses_completed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            completed = {"b"}
            calls: list[str] = []

            def runner(command: str, run_id: str, run_root: Path) -> int:
                calls.append(command)
                completed.add(command)
                return 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a", "b", "c"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", side_effect=lambda r, stage: stage in completed),
            ):
                self.assertTrue(supervisor.start("run-1", root, runner, start_command="b"))
                _wait_for_status(supervisor, root, "complete")

            self.assertEqual(calls, ["c"])

    def test_failed_stage_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            calls: list[str] = []

            def runner(command: str, run_id: str, run_root: Path) -> int:
                calls.append(command)
                return 1 if command == "b" else 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a", "b", "c"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", return_value=False),
            ):
                self.assertTrue(supervisor.start("run-1", root, runner))
                payload = _wait_for_status(supervisor, root, "failed")

            self.assertEqual(calls, ["a"])
            self.assertIn("产物不完整", payload["error"])

    def test_reconcile_resumes_stale_running_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            supervisor._save(
                root,
                {"run_id": "run-1", "status": "running", "current_stage": "b", "worker_pid": 0},
            )
            completed: set[str] = set()
            calls: list[str] = []

            def runner(command: str, run_id: str, run_root: Path) -> int:
                calls.append(command)
                completed.add(command)
                return 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a", "b"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", side_effect=lambda r, stage: stage in completed),
            ):
                self.assertTrue(supervisor.reconcile("run-1", root, runner))
                _wait_for_status(supervisor, root, "complete")

            self.assertEqual(calls, ["b"])


if __name__ == "__main__":
    unittest.main()
