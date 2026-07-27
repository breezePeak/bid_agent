from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.tool_registry import get_tool, reset_tool_index
from agent.tool_runtime import invoke
from agent.policy import is_readonly_tool


class ComplianceLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_tools_registered(self) -> None:
        self.assertIsNotNone(get_tool("analyze_compliance"))
        self.assertIsNotNone(get_tool("fix_compliance"))
        self.assertTrue(is_readonly_tool("analyze_compliance"))

    def test_missing_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("analyze_compliance", {}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "missing_requires")

    def test_analyze_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            (ws / "chapters").mkdir()
            (ws / "chapters" / "01.md").write_text("# 01", encoding="utf-8")
            report = {
                "blocking": True,
                "items": [
                    {
                        "check_id": "C1",
                        "check_type": "mandatory_param",
                        "check_name": "强制参数",
                        "status": "fail",
                        "severity": "major",
                        "suggestion": "补写 01 参数表",
                        "requirement": "必须响应参数",
                    },
                    {
                        "check_id": "C2",
                        "check_type": "signature",
                        "check_name": "签章",
                        "status": "fail",
                        "severity": "critical",
                        "suggestion": "人工签章",
                    },
                ],
            }
            (ws / "compliance_report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            # pre-seed hints to avoid full sync dependency on jobs
            (ws / "compliance_rewrite_hints.json").write_text(
                json.dumps({"chapters": {"01": [{"fix": "补参数"}]}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch("compliance_feedback.sync_compliance_findings", return_value=ws / "x.json"):
                result = invoke("analyze_compliance", {"sync": True}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertTrue(result.metrics.get("blocking"))
            self.assertGreaterEqual(result.metrics.get("manual_count", 0), 1)

            plan = invoke("fix_compliance", {"confirm_execute": False, "sync": False}, root=root)
            self.assertTrue(plan.ok, plan.summary_for_llm)
            self.assertFalse(plan.metrics.get("executed"))
            self.assertIn("01", plan.metrics.get("chapter_ids") or [])


if __name__ == "__main__":
    unittest.main()
