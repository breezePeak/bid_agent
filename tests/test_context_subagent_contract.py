from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from session_orchestrator import _normalize_plan  # noqa: E402
from subagent_registry import get_subagent  # noqa: E402


class ContextSubagentContractTests(unittest.TestCase):
    def test_context_selector_is_registered_as_per_chapter_subagent(self) -> None:
        spec = get_subagent("chapter_context_selector")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.instantiation, "per-chapter")
        self.assertEqual(spec.command, "select-context-all")

    def test_orchestrator_accepts_dispatch_contexts_action(self) -> None:
        planned = _normalize_plan(
            {
                "intent": "派发上下文选择",
                "action": "dispatch_contexts",
                "reply": "",
                "actions": [],
                "auto_execute": True,
            },
            "开始派发上下文选择",
        )
        self.assertEqual(planned["action"], "dispatch_contexts")


if __name__ == "__main__":
    unittest.main()
