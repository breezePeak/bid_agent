from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import create_goal, load_goal, resume_goal_after_materials, set_goal_status


class GoalResumeTests(unittest.TestCase):
    def test_resume_from_blocked_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="补齐评分点并出 Word",
                objectives=[{"type": "fix_coverage"}, {"type": "export"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
            )
            set_goal_status(root, "blocked_human", blocked_reason="缺证书", goal=goal)
            g2 = load_goal(root)
            self.assertEqual(g2["status"], "blocked_human")
            g3 = resume_goal_after_materials(root, note="uploaded")
            self.assertEqual(g3["status"], "in_progress")
            self.assertEqual(g3.get("blocked_reason"), "")


if __name__ == "__main__":
    unittest.main()
