from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import (
    accept_issue_risk,
    classify_issue_risk,
    export_preflight,
    list_accepted_risks,
    make_issue,
    upsert_issues,
)


class AcceptRiskPolicyTests(unittest.TestCase):
    def test_default_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="SCORE_GAP",
                title="缺口",
            )
            upsert_issues(root, [iss])
            env = {k: v for k, v in os.environ.items() if k != "ISSUE_ACCEPT_RISK_ENABLED"}
            with mock.patch.dict(os.environ, env, clear=True):
                result = accept_issue_risk(root, iss["id"], reason="足够长的原因说明文字")
            self.assertFalse(result["ok"])

    def test_reason_min_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="SCORE_GAP",
                title="缺口",
            )
            upsert_issues(root, [iss])
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                result = accept_issue_risk(root, iss["id"], reason="短")
            self.assertFalse(result["ok"])
            self.assertEqual(result.get("code"), "reason_too_short")

    def test_fatal_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            iss = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="DISQUALIFY",
                title="废标项",
            )
            upsert_issues(root, [iss])
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                result = accept_issue_risk(root, iss["id"], reason="足够长的原因说明文字")
            self.assertFalse(result["ok"])
            self.assertEqual(result.get("code"), "fatal_forbidden")

    def test_accept_major_keeps_evidence_and_not_all_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# x", encoding="utf-8")
            # minimal reports so preflight can pass blocks
            (root / "workspace" / "global_review.json").write_text(
                '{"blocking": false}', encoding="utf-8"
            )
            (root / "workspace" / "compliance_report.json").write_text(
                '{"blocking": false, "summary": {"blocking": false}}', encoding="utf-8"
            )
            iss = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="SCORE_GAP",
                title="评分风险",
                evidence={"note": "keep-me"},
            )
            upsert_issues(root, [iss])
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                result = accept_issue_risk(root, iss["id"], reason="足够长的原因说明文字")
            self.assertTrue(result["ok"], result)
            self.assertFalse(result.get("all_passed", True))
            accepted = list_accepted_risks(root)
            self.assertEqual(len(accepted), 1)
            self.assertIn("keep-me", str(accepted[0].get("evidence")))
            pf = export_preflight(root)
            self.assertTrue(pf.get("has_accepted_risks"))
            self.assertFalse(pf.get("all_passed"))
            self.assertTrue(pf.get("accepted_risks"))

    def test_classify(self) -> None:
        self.assertEqual(classify_issue_risk({"code": "DISQUALIFY", "severity": "block"}), "fatal")
        self.assertEqual(classify_issue_risk({"code": "QUALIFICATION_MISSING", "severity": "block"}), "qualification")


if __name__ == "__main__":
    unittest.main()
