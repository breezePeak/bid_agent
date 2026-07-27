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
    accept_issue_risk,
    assert_can_proceed,
    can_proceed,
    load_open_issues,
    make_issue,
    open_block_issues,
    upsert_issues,
)
from control_plane import ControlStore, WorkspaceContext
from agent.root_cause import (
    issues_from_compliance_report,
    issues_from_global_review,
    sync_issues_from_global_review,
)


class IssuesModelTests(unittest.TestCase):
    def test_v1_file_import_is_one_time_and_sqlite_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue_path = root / "workspace" / "issues" / "open.json"
            issue_path.parent.mkdir(parents=True)
            issue_path.write_text(
                json.dumps({"issues": [{"id": "iss-legacy", "status": "open", "severity": "block", "title": "original"}]}),
                encoding="utf-8",
            )
            self.assertEqual(load_open_issues(root)[0]["title"], "original")
            issue_path.write_text(
                json.dumps({"issues": [{"id": "iss-legacy", "status": "accepted", "severity": "warn", "title": "stale file"}]}),
                encoding="utf-8",
            )
            current = load_open_issues(root)[0]
            self.assertEqual(current["title"], "original")
            self.assertEqual(current["status"], "open")

    def test_accept_risk_records_policy_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            issue = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="warn",
                code="REVIEW_WARNING",
                title="需要披露的风险",
            )
            upsert_issues(root, [issue])
            with mock.patch.dict(os.environ, {"ISSUE_ACCEPT_RISK_ENABLED": "1"}):
                result = accept_issue_risk(root, issue["id"], reason="已经完成充分评估并接受风险", actor="owner")
            self.assertTrue(result["ok"])
            store = ControlStore(WorkspaceContext.resolve(root.parent, root.name))
            decisions = store.policy_decisions(issue_id=issue["id"])
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["decision_type"], "accept_risk")
            self.assertEqual(decisions[0]["actor"]["id"], "owner")

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

    def test_compliance_findings_never_block_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            issue = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="COMPLIANCE_FATAL",
                title="最高限价检查失败",
            )
            upsert_issues(root, [issue], replace_stage_id="compliance_check")

            earlier = can_proceed(root, next_command="build-source-trace")
            self.assertTrue(earlier["can_proceed"])
            self.assertEqual(earlier["block_count"], 0)

            revalidate = can_proceed(root, next_command="compliance-check")
            self.assertTrue(revalidate["can_proceed"])

            downstream = can_proceed(root, next_command="build-md")
            self.assertTrue(downstream["can_proceed"])
            self.assertEqual(downstream["block_count"], 0)

    def test_revalidating_later_gate_does_not_bypass_earlier_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            global_issue = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="GLOBAL_REVIEW_BLOCK",
                title="全文审核失败",
            )
            compliance_issue = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="COMPLIANCE_FATAL",
                title="专项合规失败",
            )
            upsert_issues(root, [global_issue, compliance_issue])

            result = can_proceed(root, next_command="compliance-check")
            self.assertFalse(result["can_proceed"])
            self.assertEqual(result["block_count"], 1)

    def test_disabled_review_keeps_history_but_does_not_block_existing_first_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter = root / "workspace" / "chapters" / "2.2.md"
            chapter.parent.mkdir(parents=True)
            chapter.write_text("# 第一版草稿", encoding="utf-8")
            failed = make_issue(
                stage_id="write_chapters",
                command="write-all",
                severity="block",
                code="WRITE_CHAPTER_FAILED",
                title="章节 2.2 写作失败",
                target_type="chapter",
                target_ids=["2.2"],
            )
            review = make_issue(
                stage_id="review_fix_chapters",
                command="review-fix-all",
                severity="block",
                code="CHAPTER_REVIEW_BLOCKER",
                title="章节 2.2 审核未收敛",
                target_type="chapter",
                target_ids=["2.2"],
            )
            upsert_issues(root, [failed, review])

            with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
                self.assertEqual(len(load_open_issues(root)), 2)
                self.assertEqual(open_block_issues(root), [])
                self.assertTrue(can_proceed(root, next_command="build-docx")["can_proceed"])

    def test_disabled_review_ignores_claim_gate_write_failure_without_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            audit_failure = make_issue(
                stage_id="write_chapters",
                command="write-all",
                severity="block",
                code="WRITE_CHAPTER_FAILED",
                title="章节 3.1 写作失败",
                detail="章节 claim 防编造门禁失败",
                target_type="chapter",
                target_ids=["3.1"],
                evidence={"error": "防编造门禁发现 blocker"},
            )
            upsert_issues(root, [audit_failure])

            with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
                self.assertEqual(open_block_issues(root), [])

    def test_disabled_review_keeps_real_write_failure_blocking_without_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            runtime_failure = make_issue(
                stage_id="write_chapters",
                command="write-all",
                severity="block",
                code="WRITE_CHAPTER_FAILED",
                title="章节 3.2 写作失败",
                detail="模型请求超时",
                target_type="chapter",
                target_ids=["3.2"],
                evidence={"error": "timeout"},
            )
            upsert_issues(root, [runtime_failure])

            with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
                self.assertEqual(len(open_block_issues(root)), 1)

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
        self.assertTrue(issues)
        self.assertTrue(all(i["severity"] == "warn" for i in issues))

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
