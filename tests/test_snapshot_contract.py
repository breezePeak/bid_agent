from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import create_goal
from agent.snapshot import build_snapshot


class SnapshotContractTests(unittest.TestCase):
    def test_snapshot_keys_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            (root / "outputs").mkdir(parents=True)
            create_goal(
                root,
                raw_user_goal="状态",
                objectives=[{"type": "status"}],
                success_criteria=[],
            )
            snap = build_snapshot(root, for_llm=True)
            for key in (
                "pipeline",
                "goal",
                "artifacts",
                "issues",
                "materials",
                "repair_job",
                "manual_review",
                "last_tool_result",
                "budget",
            ):
                self.assertIn(key, snap)
            raw = json.dumps(snap, ensure_ascii=False)
            self.assertLessEqual(len(raw), 20000)
            # no secrets
            self.assertNotIn("OPENAI_API_KEY", raw)
            self.assertNotIn("api_key", raw.lower())


if __name__ == "__main__":
    unittest.main()
