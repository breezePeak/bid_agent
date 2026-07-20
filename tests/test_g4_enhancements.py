from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import accept_issue_risk, can_proceed, make_issue, open_block_issues, upsert_issues
from agent.repair import execute_repair_batch
from agent.root_cause import ALLOWED_CAUSE_STAGES, refine_issue_cause_with_llm


class AcceptRiskTests(unittest.TestCase):
    def test_can_disable_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="x",
            )
            upsert_issues(root, [iss])
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "0"}):
                result = accept_issue_risk(root, iss["id"], reason="测试原因足够长")
            self.assertFalse(result["ok"])

    def test_accept_unblocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="x",
            )
            upsert_issues(root, [iss])
            self.assertTrue(open_block_issues(root))
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                result = accept_issue_risk(root, iss["id"], reason="足够长的接受风险原因说明")
            self.assertTrue(result["ok"], result)
            self.assertFalse(open_block_issues(root))
            self.assertTrue(can_proceed(root)["can_proceed"])


class LlmCauseTests(unittest.TestCase):
    def test_whitelist_rejects_illegal_stage(self) -> None:
        issue = make_issue(
            stage_id="global_review",
            command="global-review",
            severity="block",
            code="UNCOVERED_SCORE",
            title="未覆盖",
            likely_cause_stage="write_chapters",
        )
        with mock.patch.dict(os.environ, {"ISSUE_LLM_CAUSE_ENABLED": "1"}):
            result = refine_issue_cause_with_llm(
                None,
                issue,
                llm_chat=lambda messages, temperature=0: '{"likely_cause_stage":"hack_stage","reason":"x","confidence":0.9}',
            )
        self.assertEqual(result["source"], "rule")
        self.assertEqual(result["likely_cause_stage"], "write_chapters")

    def test_whitelist_accepts_legal_stage(self) -> None:
        issue = make_issue(
            stage_id="global_review",
            command="global-review",
            severity="block",
            code="UNCOVERED_SCORE",
            title="未覆盖",
            likely_cause_stage="write_chapters",
        )
        with mock.patch.dict(os.environ, {"ISSUE_LLM_CAUSE_ENABLED": "1"}):
            result = refine_issue_cause_with_llm(
                None,
                issue,
                llm_chat=lambda messages, temperature=0: '{"likely_cause_stage":"generate_outline","reason":"大纲未绑定","confidence":0.8}',
            )
        self.assertEqual(result["source"], "llm+whitelist")
        self.assertEqual(result["likely_cause_stage"], "generate_outline")
        self.assertIn("generate_outline", ALLOWED_CAUSE_STAGES)


class BatchRepairTests(unittest.TestCase):
    def test_batch_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            a = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title="a",
            )
            b = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="COMPLIANCE_BLOCK",
                title="b",
            )
            # COMPLIANCE_BLOCK may not be exact code from table - use COMPLIANCE_CRITICAL style
            b["code"] = "NAME_INCONSISTENT"
            upsert_issues(root, [a, b])
            result = execute_repair_batch(root, [a["id"], b["id"]], confirm=False, dry_run=True)
            self.assertTrue(result["ok"])
            self.assertFalse(result["executed"])
            self.assertEqual(len(result["plans"]), 2)


if __name__ == "__main__":
    unittest.main()
