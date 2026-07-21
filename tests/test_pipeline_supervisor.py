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

    def test_single_command_stops_after_requested_stage(self) -> None:
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
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        runner,
                        start_command="b",
                        operation_id="op-1",
                        single_command=True,
                    )
                )
                payload = _wait_for_status(supervisor, root, "complete")

            self.assertEqual(calls, ["b"])
            self.assertEqual(payload["operation_id"], "op-1")
            self.assertEqual(payload["requested_action"], "run_stage")

    def test_cancel_and_status_listener_preserve_operation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            events: list[dict] = []
            supervisor.set_status_listener(lambda event_root, payload: events.append(dict(payload)))

            def runner(command: str, run_id: str, run_root: Path) -> int:
                time.sleep(0.08)
                return 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", return_value=False),
            ):
                self.assertTrue(supervisor.start("run-1", root, runner, operation_id="op-cancel"))
                time.sleep(0.02)
                supervisor.cancel()
                payload = _wait_for_status(supervisor, root, "cancelled")

            self.assertEqual(payload["operation_id"], "op-cancel")
            self.assertTrue(any(item.get("status") == "cancelling" for item in events))
            self.assertTrue(any(item.get("status") == "cancelled" for item in events))

    def test_injected_v2_gate_blocks_stage_before_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            calls: list[str] = []

            with patch("pipeline_supervisor.auto_run_commands", return_value=["a"]):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        lambda command, run_id, run_root: calls.append(command) or 0,
                        gate_evaluator=lambda run_root, command: {
                            "can_proceed": False,
                            "message": "sqlite gate blocked",
                        },
                    )
                )
                payload = _wait_for_status(supervisor, root, "failed")

            self.assertEqual(calls, [])
            self.assertEqual(payload["error"], "sqlite gate blocked")

    def test_injected_v2_gate_fails_closed_when_evaluator_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            calls: list[str] = []

            def unavailable(run_root: Path, command: str) -> dict:
                raise RuntimeError("control.db locked")

            with patch("pipeline_supervisor.auto_run_commands", return_value=["a"]):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        lambda command, run_id, run_root: calls.append(command) or 0,
                        gate_evaluator=unavailable,
                    )
                )
                payload = _wait_for_status(supervisor, root, "failed")

            self.assertEqual(calls, [])
            self.assertIn("control.db locked", payload["error"])

    def test_v2_artifact_recorder_marks_reused_and_produced_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            completed = {"a"}
            recorded: list[tuple[str, str]] = []

            def runner(command: str, run_id: str, run_root: Path) -> int:
                completed.add(command)
                return 0

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a", "b"]),
                patch("pipeline_supervisor.stage_spec_by_command", side_effect=lambda c: SimpleNamespace(id=c, validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", side_effect=lambda r, stage: stage in completed),
            ):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        runner,
                        artifact_recorder=lambda run_root, command, disposition: recorded.append(
                            (command, disposition)
                        ),
                    )
                )
                _wait_for_status(supervisor, root, "complete")

            self.assertEqual(recorded, [("a", "reused"), ("b", "produced")])

    def test_v2_artifact_recorder_failure_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a"]),
                patch("pipeline_supervisor.stage_spec_by_command", return_value=SimpleNamespace(id="a", validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", return_value=True),
            ):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        lambda command, run_id, run_root: 0,
                        artifact_recorder=lambda run_root, command, disposition: (_ for _ in ()).throw(
                            RuntimeError("manifest locked")
                        ),
                    )
                )
                payload = _wait_for_status(supervisor, root, "failed")

            self.assertIn("manifest locked", payload["error"])

    def test_v2_stale_artifact_is_executed_instead_of_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()
            calls: list[str] = []

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a"]),
                patch("pipeline_supervisor.stage_spec_by_command", return_value=SimpleNamespace(id="a", validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", return_value=True),
            ):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        lambda command, run_id, run_root: calls.append(command) or 0,
                        artifact_readiness_evaluator=lambda run_root, command: False,
                    )
                )
                _wait_for_status(supervisor, root, "complete")

            self.assertEqual(calls, ["a"])

    def test_v2_artifact_readiness_failure_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = PipelineSupervisor()

            with (
                patch("pipeline_supervisor.auto_run_commands", return_value=["a"]),
                patch("pipeline_supervisor.stage_spec_by_command", return_value=SimpleNamespace(id="a", validator="")),
                patch("pipeline_supervisor.stage_outputs_ready", return_value=True),
            ):
                self.assertTrue(
                    supervisor.start(
                        "run-1",
                        root,
                        lambda command, run_id, run_root: 0,
                        artifact_readiness_evaluator=lambda run_root, command: (_ for _ in ()).throw(
                            RuntimeError("manifest unavailable")
                        ),
                    )
                )
                payload = _wait_for_status(supervisor, root, "failed")

            self.assertIn("manifest unavailable", payload["error"])


if __name__ == "__main__":
    unittest.main()
