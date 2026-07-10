from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class AutoRecoveryTests(unittest.TestCase):
    def test_stalled_process_is_terminated_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process = MagicMock()
            process.pid = 123
            process.stdout.readline.return_value = b""
            process.poll.return_value = None
            process.wait.return_value = 0
            process.returncode = None
            with (
                patch("web_app.subprocess.Popen", return_value=process),
                patch("web_app.time.monotonic", side_effect=[0.0, 61.0]),
                patch("web_app.SUPERVISOR.heartbeat"),
                patch("web_app._terminate_process_tree") as terminate,
                patch.dict("web_app.os.environ", {"BID_AGENT_STAGE_STALL_TIMEOUT": "60"}),
            ):
                exit_code = web_app._run_process_once("parse-score", root)

            self.assertEqual(exit_code, 124)
            terminate.assert_called_once_with(process)

    def test_partial_collection_recovers_its_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for chapter_id in ("01", "02"):
                path = root / "workspace" / "jobs" / f"{chapter_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"chapter_id": chapter_id}), encoding="utf-8")
            context = root / "workspace" / "contexts" / "01_context.json"
            context.parent.mkdir(parents=True, exist_ok=True)
            context.write_text(json.dumps({"chapter_id": "01"}), encoding="utf-8")

            commands = web_app._dependency_recovery_commands("write-all", root)

            self.assertIn("select-context-all", commands)

    def test_non_recoverable_auth_error_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("web_app._run_process_once") as run_once:
                result = web_app._attempt_auto_recovery("parse-score", root, ["HTTP 401 API Key 无效或未授权"])
            self.assertIsNone(result)
            run_once.assert_not_called()

    def test_missing_dependency_backfills_then_retries_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[str] = []

            def fake_run(command: str, run_root: Path) -> int:
                calls.append(command)
                if command == "prepare-inputs":
                    (run_root / "inputs").mkdir(parents=True, exist_ok=True)
                    (run_root / "inputs" / "score.md").write_text("评分", encoding="utf-8")
                    return 0
                return 0

            with patch("web_app._run_process_once", side_effect=fake_run):
                result = web_app._attempt_auto_recovery("parse-score", root, ["FileNotFoundError: inputs/score.md 不存在"])

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["prepare-inputs", "parse-score"])
            recovery = _read_json(root / "workspace" / "recovery_state.json")
            self.assertEqual(recovery["command"], "parse-score")
            self.assertEqual(recovery["attempt"], 1)

    def test_retry_limit_is_enforced_for_transient_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs").mkdir(parents=True, exist_ok=True)
            (root / "inputs" / "score.md").write_text("评分", encoding="utf-8")
            with patch("web_app.time.sleep"), patch("web_app._run_process_once", return_value=1) as run_once:
                result = web_app._attempt_auto_recovery("parse-score", root, ["LLM 请求失败 timeout"])

            self.assertIsNone(result)
            self.assertEqual(run_once.call_count, web_app.AUTO_RECOVERY_MAX_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
