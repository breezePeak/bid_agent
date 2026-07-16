from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import make_issue, upsert_issues
from agent.repair import build_repair_plan, execute_repair_plan


class RepairPlanTests(unittest.TestCase):
    def test_build_plan_uncovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="未覆盖评分点",
                target_type="score_point",
                target_ids=["S001", "S002"],
                suggested_actions=[
                    {"type": "fix_coverage", "label": "按覆盖缺口改稿", "params": {}},
                    {"type": "revalidate_gate", "label": "重验", "params": {"command": "global-review"}},
                ],
            )
            upsert_issues(root, [iss], replace_stage_id="global_review")
            plan = build_repair_plan(root, iss["id"])
            self.assertTrue(plan["ok"], plan)
            self.assertTrue(plan["steps"])
            self.assertIn("global-review", plan["revalidate"])

    def test_execute_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="NAME_INCONSISTENT",
                title="项目名不一致",
            )
            upsert_issues(root, [iss], replace_stage_id="global_review")
            result = execute_repair_plan(root, iss["id"], confirm=False, dry_run=True)
            self.assertTrue(result.get("ok"))
            self.assertFalse(result.get("executed"))

    def test_execute_confirm_calls_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="未覆盖",
                target_type="chapter",
                target_ids=["01"],
                suggested_actions=[
                    {"type": "rewrite_chapters", "label": "改章", "params": {"chapter_ids": ["01"]}},
                ],
            )
            upsert_issues(root, [iss], replace_stage_id="global_review")

            class FakeResult:
                def __init__(self, ok=True):
                    self.ok = ok
                    self.summary_for_llm = "ok"
                    self.error = None

            real_load = __import__("agent.issues", fromlist=["load_open_issues"]).load_open_issues
            calls = {"n": 0}

            def load_side_effect(r=None):
                calls["n"] += 1
                # first calls need real issues for plan/build; after tools, return empty so issue closes
                if calls["n"] <= 2:
                    return real_load(root)
                return []

            with mock.patch("agent.tool_runtime.invoke", return_value=FakeResult(True)) as inv:
                with mock.patch("agent.root_cause.sync_issues_from_global_review", return_value=[]):
                    with mock.patch("agent.root_cause.sync_issues_from_compliance", return_value=[]):
                        with mock.patch("agent.repair.load_open_issues", side_effect=load_side_effect):
                            result = execute_repair_plan(root, iss["id"], confirm=True)
            self.assertTrue(result.get("executed"), result)
            self.assertTrue(inv.called)


if __name__ == "__main__":
    unittest.main()
