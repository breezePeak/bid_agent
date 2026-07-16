from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import export_preflight, make_issue, upsert_issues, can_proceed
from agent.tool_registry import get_tool, reset_tool_index
from agent.tool_runtime import invoke


class G5ToolsAndPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_tools_registered(self) -> None:
        for name in ("list_issues", "explain_issue", "repair_issue", "export_preflight"):
            self.assertIsNotNone(get_tool(name), name)

    def test_list_issues_and_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="未覆盖",
            )
            upsert_issues(root, [iss])
            r = invoke("list_issues", {"status": "block"}, root=root)
            self.assertTrue(r.ok)
            self.assertGreaterEqual(r.metrics.get("count", 0), 1)
            pre = export_preflight(root)
            self.assertFalse(pre["can_export"])
            # revalidate allow for global-review command
            gate = can_proceed(root, next_command="global-review")
            self.assertTrue(gate["can_proceed"])
            gate2 = can_proceed(root, next_command="build-docx")
            self.assertFalse(gate2["can_proceed"])

    def test_export_preflight_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
            # clean - no reports
            r = invoke("export_preflight", {}, root=root)
            self.assertTrue(r.ok)
            self.assertIn("can_export", r.metrics)


if __name__ == "__main__":
    unittest.main()
