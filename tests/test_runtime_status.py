from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.activity import begin_phase, mark_agent
from agent.goal import create_goal, set_goal_status
from agent.runtime_status import (
    build_runtime_status,
    detect_inconsistencies,
    soft_heal_inconsistencies,
)
from utils import write_json


class RuntimeStatusTests(unittest.TestCase):
    def test_detects_goal_succeeded_with_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
            goal = create_goal(
                root,
                raw_user_goal="导出",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.md"}],
                plan=[],
            )
            set_goal_status(root, "succeeded", goal=goal)
            begin_phase(
                root,
                phase="write",
                phase_label="写作",
                role="chapter_writer",
                chapter_ids=["01"],
            )
            mark_agent(root, role="chapter_writer", chapter_id="01", status="running")
            runtime = build_runtime_status(root)
            codes = {w["code"] for w in runtime.get("warnings") or []}
            self.assertIn("goal_succeeded_workers_active", codes)
            self.assertFalse(runtime.get("consistent"))
            self.assertEqual(runtime.get("product_mode"), "inconsistent")

    def test_soft_heal_demotes_false_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
            goal = create_goal(
                root,
                raw_user_goal="导出",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.md"}],
                plan=[],
            )
            set_goal_status(root, "succeeded", goal=goal)
            begin_phase(
                root,
                phase="write",
                phase_label="写作",
                role="chapter_writer",
                chapter_ids=["01"],
            )
            mark_agent(root, role="chapter_writer", chapter_id="01", status="running")
            final = soft_heal_inconsistencies(root)
            self.assertIn("demote_goal_succeeded_to_in_progress", final.get("heal_actions") or [])
            g = final.get("stores", {}).get("goal", {})
            self.assertNotEqual(g.get("status"), "succeeded")

    def test_idle_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = build_runtime_status(root)
            self.assertTrue(runtime.get("ok"))
            self.assertTrue(runtime.get("consistent"))
            self.assertEqual(runtime.get("product_mode"), "idle")


if __name__ == "__main__":
    unittest.main()
