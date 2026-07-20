from __future__ import annotations

import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.budgets import AgentBudget, criteria_fingerprint


class NoProgressTests(unittest.TestCase):
    def test_criteria_unchanged_counts_no_progress(self) -> None:
        b = AgentBudget(max_steps=20, max_llm_calls=20, max_same_tool_streak=10, max_no_progress_steps=2)
        fp = criteria_fingerprint([{"check": "x", "ok": False, "detail": "a"}])
        b.record_step(tool="t1", args={}, observation="1", criteria_fp=fp, executed=True, ok=True)
        # same criteria, different tool — still no criteria progress
        b.record_step(tool="t2", args={}, observation="2", criteria_fp=fp, executed=True, ok=True)
        self.assertGreaterEqual(b.no_progress_steps, 1)
        b.record_step(tool="t3", args={}, observation="3", criteria_fp=fp, executed=True, ok=True)
        self.assertFalse(b.allow_next_step())

    def test_criteria_change_resets(self) -> None:
        b = AgentBudget(max_steps=20, max_llm_calls=20, max_same_tool_streak=10, max_no_progress_steps=2)
        fp1 = criteria_fingerprint([{"check": "x", "ok": False, "detail": "a"}])
        fp2 = criteria_fingerprint([{"check": "x", "ok": True, "detail": "b"}])
        b.record_step(tool="t1", args={}, observation="1", criteria_fp=fp1, executed=True, ok=True)
        b.record_step(tool="t2", args={}, observation="2", criteria_fp=fp2, executed=True, ok=True)
        self.assertEqual(b.no_progress_steps, 0)


if __name__ == "__main__":
    unittest.main()
