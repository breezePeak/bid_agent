from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graph.state_recorder import _rotate_jsonl, record_stage_finish, record_stage_start, save_run_state, stage_resume_ready


class StateRecorderTests(unittest.TestCase):
    def test_run_state_uses_compact_metrics_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            metrics = {
                "run_id": "run-1",
                "stages": {
                    "write_chapters": {
                        "attempts": 1,
                        "duration_ms": 10,
                        "llm_calls": 1000,
                        "input_tokens_est": 2000,
                        "output_tokens_est": 3000,
                        "agent_runs": [{"artifact_path": "x" * 1000} for _ in range(100)],
                    }
                },
            }
            (workspace / "run_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

            path = save_run_state(root, {"root_dir": str(root)}, stage="write_chapters", status="running")
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertNotIn("agent_runs", payload["metrics"]["write_chapters"])
            self.assertLess(path.stat().st_size, 10_000)

    def test_jsonl_history_is_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_state_history.jsonl"
            path.write_text("x" * 20, encoding="utf-8")
            _rotate_jsonl(path, max_bytes=10, keep=2)
            self.assertFalse(path.exists())
            self.assertTrue(path.with_name(path.name + ".1").exists())

    def test_resume_requires_event_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "score_requirements.json").write_text("[]", encoding="utf-8")
            artifact = workspace / "score_points.json"
            artifact.write_text("[]", encoding="utf-8")

            state = {"root_dir": str(root)}
            record_stage_start(root, "parse_score", state=state, message="start")
            save_run_state(root, state, stage="parse_score", status="ok", message="done")
            self.assertFalse(stage_resume_ready(root, "parse_score"))

            record_stage_finish(root, "parse_score", "success", message="done", artifact_path="workspace/score_points.json", status="ok")
            self.assertTrue(stage_resume_ready(root, "parse_score"))

            artifact.unlink()
            self.assertFalse(stage_resume_ready(root, "parse_score"))


if __name__ == "__main__":
    unittest.main()
