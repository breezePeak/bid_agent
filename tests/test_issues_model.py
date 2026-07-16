from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import (
    assert_can_proceed,
    can_proceed,
    load_open_issues,
    make_issue,
    open_block_issues,
    upsert_issues,
)
from agent.root_cause import (
    issues_from_compliance_report,
    issues_from_global_review,
    sync_issues_from_global_review,
)


class IssuesModelTests(unittest.TestCase):
    def test_upsert_and_can_proceed(self) -> None:
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
                target_ids=["S001"],
            )
            upsert_issues(root, [iss], replace_stage_id="global_review")
            self.assertEqual(len(open_block_issues(root)), 1)
            result = can_proceed(root, next_command="compliance-check")
            self.assertFalse(result["can_proceed"])
            with self.assertRaises(RuntimeError):
                assert_can_proceed(root, next_command="compliance-check")
            with mock.patch.dict(os.environ, {"QUALITY_GATE_MODE": "soft"}):
                soft = can_proceed(root, next_command="compliance-check")
                self.assertTrue(soft["can_proceed"])

    def test_replace_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            a = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="A",
                title="A",
            )
            b = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="B",
                title="B",
            )
            upsert_issues(root, [a, b])
            self.assertEqual(len(load_open_issues(root)), 2)
            c = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="warn",
                code="C",
                title="C",
            )
            upsert_issues(root, [c], replace_stage_id="global_review")
            issues = load_open_issues(root)
            self.assertIn("compliance_check", {i["stage_id"] for i in issues})
            self.assertTrue(any(i["code"] == "C" for i in issues))
            self.assertFalse(any(i["code"] == "A" for i in issues))


class RootCauseAdapterTests(unittest.TestCase):
    def test_from_global_review(self) -> None:
        review = {
            "project_name_consistent": False,
            "uncovered_score_points": ["S001", "S002"],
            "chapter_conflicts": [],
            "fabrication_risks": [],
            "missing_chapters": [],
        }
        issues = issues_from_global_review(review)
        codes = {i["code"] for i in issues}
        self.assertIn("NAME_INCONSISTENT", codes)
        self.assertIn("UNCOVERED_SCORE", codes)

    def test_from_compliance(self) -> None:
        report = {
            "blocking": True,
            "items": [
                {
                    "check_id": "QUAL-001",
                    "check_name": "资格",
                    "check_type": "qualification",
                    "status": "fail",
                    "severity": "critical",
                    "requirement": "需三年经验",
                },
                {
                    "check_id": "W-1",
                    "check_name": "提示",
                    "status": "warn",
                    "severity": "minor",
                },
            ],
        }
        issues = issues_from_compliance_report(report)
        self.assertTrue(any(i["severity"] == "block" for i in issues))
        self.assertTrue(any(i["severity"] == "warn" for i in issues))

    def test_sync_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            (ws / "global_review.json").write_text(
                json.dumps(
                    {
                        "project_name_consistent": False,
                        "uncovered_score_points": ["S009"],
                        "chapter_conflicts": [],
                        "fabrication_risks": [],
                        "missing_chapters": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            synced = sync_issues_from_global_review(root)
            self.assertGreaterEqual(len(synced), 1)
            self.assertTrue((root / "workspace" / "issues" / "open.json").exists())


if __name__ == "__main__":
    unittest.main()
