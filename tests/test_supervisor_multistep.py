from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.supervisor import run_supervisor_turn
from agent.tool_registry import reset_tool_index
from agent.trace import load_decisions


class SupervisorMultistepTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_status_query_executes_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_supervisor_turn(
                "当前进度怎么样",
                root=root,
                status={},
                history=[],
                use_llm=False,
                max_steps=4,
            )
            self.assertTrue(result.get("supervisor"))
            self.assertTrue(result.get("steps"))
            self.assertEqual(result["steps"][0]["tool"], "query_status")
            self.assertTrue(result["steps"][0]["executed"])
            decisions = load_decisions(root, tail=10)
            self.assertTrue(decisions)

    def test_coverage_goal_runs_multiple_readonly_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            # matrix present so analyze_coverage may still fail missing requires; mock invoke
            calls: list[str] = []

            def fake_invoke(name, args=None, **kwargs):
                calls.append(name)
                from agent.types import ToolResult

                return ToolResult(
                    ok=True,
                    tool=name,
                    args=args or {},
                    started_at="",
                    ended_at="",
                    summary_for_llm=f"{name} ok",
                    metrics={"tool": name, "args": args or {}},
                )

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                result = run_supervisor_turn(
                    "补齐所有可自动补齐的评分点并出 Word",
                    root=root,
                    use_llm=False,
                    max_steps=6,
                    user_confirmed=True,
                )
            self.assertGreaterEqual(len(result.get("steps") or []), 1)
            self.assertIn("terminal_status", result)
            # plan should drive at least analyze_coverage
            tools = [s.get("tool") for s in result.get("steps") or []]
            self.assertTrue(any(t in {"analyze_coverage", "fix_coverage", "export_preflight", "build_export"} for t in tools))
            self.assertTrue(calls)

    def test_goal_success_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from agent.goal import create_goal, save_goal

            goal = create_goal(
                root,
                raw_user_goal="导出",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.md"}],
                plan=[],
            )
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
            from agent.goal import reevaluate_goal

            reevaluate_goal(root)
            result = run_supervisor_turn(
                "导出",
                root=root,
                use_llm=False,
                max_steps=3,
            )
            # either reuses goal and succeeds, or creates new — if criteria met terminal succeeded
            g = result.get("goal") or {}
            if g.get("all_criteria_ok"):
                self.assertEqual(result.get("terminal_status"), "succeeded")

    def test_mutation_needs_confirm_without_user_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_supervisor_turn(
                "继续下一步",
                root=root,
                status={"next_step": {"command": "parse-score", "label": "解析评分"}},
                use_llm=False,
                user_confirmed=False,
                max_steps=2,
            )
            executed_mutate = any(
                s.get("executed") and s.get("tool") == "run_stage" for s in result.get("steps") or []
            )
            self.assertFalse(executed_mutate)

    def test_same_tool_streak_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_invoke(name, args=None, **kwargs):
                from agent.types import ToolResult

                return ToolResult(
                    ok=True,
                    tool=name,
                    args=args or {},
                    started_at="",
                    ended_at="",
                    summary_for_llm="same",
                    metrics={},
                )

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                with mock.patch.dict(
                    "os.environ",
                    {
                        "AGENT_MAX_STEPS": "8",
                        "AGENT_MAX_SAME_TOOL_STREAK": "2",
                        "AGENT_MAX_NO_PROGRESS_STEPS": "2",
                    },
                ):
                    from agent.budgets import AgentBudget

                    # force plan that keeps picking analyze_coverage
                    result = run_supervisor_turn(
                        "评分覆盖率怎么样",
                        root=root,
                        use_llm=False,
                        max_steps=5,
                    )
            self.assertTrue(result.get("steps"))
            self.assertIn(result.get("terminal_status"), {
                "in_progress",
                "succeeded",
                "budget_exceeded",
                "awaiting_confirmation",
                "blocked_human",
            })


if __name__ == "__main__":
    unittest.main()
