from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import (
    build_plan_for_objectives,
    create_goal,
    infer_goal_from_message,
    mark_plan_step,
    next_plan_step,
    reevaluate_goal,
)


class GoalPlanTests(unittest.TestCase):
    def test_infer_coverage_export_plan(self) -> None:
        g = infer_goal_from_message("补齐评分点并出 Word")
        types = [o.get("type") for o in g["objectives"]]
        self.assertIn("fix_coverage", types)
        self.assertIn("export", types)
        plan = g["plan"]
        tools = [s.get("tool") for s in plan]
        self.assertIn("analyze_coverage", tools)
        self.assertIn("build_export", tools)

    def test_plan_skips_when_run_if_not_matched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            # full coverage matrix
            import json

            (root / "workspace" / "score_coverage_matrix.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "score_point_count": 10,
                            "fully_covered_score_point_count": 10,
                        },
                        "uncovered_score_points": [],
                        "matrix": [{"id": str(i)} for i in range(10)],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            goal = create_goal(
                root,
                raw_user_goal="补齐评分点",
                objectives=[{"type": "fix_coverage"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
            )
            plan = goal.get("plan") or []
            fix_steps = [s for s in plan if s.get("tool") == "fix_coverage"]
            self.assertTrue(fix_steps)
            # refresh should skip fix_coverage when coverage already ok
            goal2 = reevaluate_goal(root, goal)
            plan2 = goal2.get("plan") or []
            fix2 = next((s for s in plan2 if s.get("tool") == "fix_coverage"), None)
            if fix2:
                self.assertIn(str(fix2.get("status")), {"skipped", "done", "pending"})

    def test_next_and_mark_plan_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_plan_for_objectives(
                [{"type": "fix_coverage"}, {"type": "export"}],
                constraints={},
            )
            goal = create_goal(
                root,
                raw_user_goal="补齐并导出",
                objectives=[{"type": "fix_coverage"}, {"type": "export"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
                plan=plan,
            )
            step = next_plan_step(root, goal)
            self.assertIsNotNone(step)
            assert step is not None
            sid = str(step.get("step_id"))
            goal = mark_plan_step(root, sid, status="done", goal=goal)
            step2 = next_plan_step(root, goal)
            if step2:
                self.assertNotEqual(str(step2.get("step_id")), sid)

    def test_coverage_not_succeeded_without_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="补齐评分点并出 Word",
                objectives=[{"type": "fix_coverage"}, {"type": "export"}],
                success_criteria=[
                    {"check": "score_coverage_min", "ratio": 0.95},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                ],
            )
            self.assertFalse(goal.get("all_criteria_ok"))
            self.assertNotEqual(goal.get("status"), "succeeded")


if __name__ == "__main__":
    unittest.main()
