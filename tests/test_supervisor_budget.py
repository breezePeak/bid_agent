from __future__ import annotations

import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.budgets import AgentBudget, tool_call_fingerprint


class BudgetTests(unittest.TestCase):
    def test_max_steps(self) -> None:
        b = AgentBudget(max_steps=2, max_llm_calls=10, max_same_tool_streak=5, max_no_progress_steps=10)
        self.assertTrue(b.allow_next_step())
        b.record_step(tool="query_status", args={}, observation="a", executed=True, ok=True)
        self.assertTrue(b.allow_next_step())
        b.record_step(tool="query_status", args={"v": 1}, observation="b", executed=True, ok=True)
        self.assertFalse(b.allow_next_step())
        self.assertEqual(b.stop_reason, "budget_exceeded")

    def test_same_tool_streak(self) -> None:
        b = AgentBudget(max_steps=20, max_llm_calls=20, max_same_tool_streak=2, max_no_progress_steps=20)
        b.record_step(tool="analyze_coverage", args={}, observation="same", executed=True, ok=True)
        b.record_step(tool="analyze_coverage", args={}, observation="same", executed=True, ok=True)
        self.assertFalse(b.allow_next_step())

    def test_fingerprint_stable(self) -> None:
        a = tool_call_fingerprint("x", {"b": 1, "a": 2})
        b = tool_call_fingerprint("x", {"a": 2, "b": 1})
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
