from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.activity import (
    begin_phase,
    has_active_workers,
    load_activity,
    mark_agent,
    reconcile_interrupted_activity,
)
from agent.goal import create_goal, reevaluate_goal, runtime_blocks_success
from utils import write_json


class StatusConsistencyTests(unittest.TestCase):
    def test_activity_reconcile_clears_ghost_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            begin_phase(
                root,
                phase="write",
                phase_label="写作 SubAgent",
                role="chapter_writer",
                chapter_ids=["01", "02", "03"],
            )
            mark_agent(root, role="chapter_writer", chapter_id="01", status="running", message="写中")
            self.assertTrue(has_active_workers(root))
            data = reconcile_interrupted_activity(root)
            self.assertEqual(data.get("status"), "interrupted")
            self.assertFalse(has_active_workers(root))
            agents = load_activity(root).get("agents") or []
            statuses = {a.get("chapter_id"): a.get("status") for a in agents if isinstance(a, dict)}
            self.assertEqual(statuses.get("01"), "failed")
            self.assertEqual(statuses.get("02"), "skipped")

    def test_criteria_ok_not_succeeded_while_workers_running(self) -> None:
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
            begin_phase(
                root,
                phase="write",
                phase_label="写作",
                role="chapter_writer",
                chapter_ids=["4.2.1.1"],
            )
            mark_agent(root, role="chapter_writer", chapter_id="4.2.1.1", status="running")
            block = runtime_blocks_success(root, goal)
            self.assertTrue(block)
            goal2 = reevaluate_goal(root, goal)
            self.assertNotEqual(goal2.get("status"), "succeeded")
            self.assertIn(goal2.get("status"), {"in_progress", "blocked_human"})

    def test_succeeded_when_idle_and_criteria_met(self) -> None:
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
            goal2 = reevaluate_goal(root, goal)
            self.assertEqual(goal2.get("status"), "succeeded")
            self.assertTrue(goal2.get("all_criteria_ok"))

    def test_goal_success_fails_closed_when_control_domains_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = {"normalized_objectives": [], "constraints": {}}
            with mock.patch("agent.issues.open_block_issues", side_effect=RuntimeError("issue db offline")):
                issue_block = runtime_blocks_success(root, goal)
            self.assertIn("Issue 状态读取失败", issue_block)

            with mock.patch("agent.issues.open_block_issues", return_value=[]):
                with mock.patch("agent.activity.has_active_workers", side_effect=RuntimeError("activity db offline")):
                    activity_block = runtime_blocks_success(root, goal)
            self.assertIn("AgentActivity 状态读取失败", activity_block)

            with mock.patch("agent.issues.open_block_issues", return_value=[]):
                with mock.patch("agent.activity.has_active_workers", return_value=False):
                    with mock.patch("agent.repair_jobs.load_repair_job", side_effect=RuntimeError("repair db offline")):
                        repair_block = runtime_blocks_success(root, goal)
            self.assertIn("RepairJob 状态读取失败", repair_block)


if __name__ == "__main__":
    unittest.main()
