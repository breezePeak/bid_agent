from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.issues import make_issue, upsert_issues  # noqa: E402
from agent.repair import (  # noqa: E402
    build_repair_batch_plan,
    execute_repair_batch,
    issue_fingerprint,
)
from agent.root_cause import sync_issues_from_compliance  # noqa: E402


class FakeResult:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.summary_for_llm = "ok" if ok else "failed"
        self.error = None


class RepairBatchTests(unittest.TestCase):
    def _root(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "workspace").mkdir(parents=True)
        return root

    def test_fingerprint_is_stable_and_target_sensitive(self) -> None:
        first = {
            "id": "old",
            "stage_id": "global_review",
            "code": "UNCOVERED_SCORE",
            "target": {"type": "score_point", "ids": ["S2", "S1", "S1"]},
        }
        recreated = {
            "id": "new",
            "stage_id": "global_review",
            "code": "UNCOVERED_SCORE",
            "target": {"type": "score_point", "ids": ["S1", "S2"]},
        }
        changed = {**recreated, "target": {"type": "score_point", "ids": ["S1"]}}

        self.assertEqual(issue_fingerprint(first), issue_fingerprint(recreated))
        self.assertNotEqual(issue_fingerprint(first), issue_fingerprint(changed))

    def test_batch_plan_groups_and_deduplicates_global_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            issues = [
                make_issue(
                    stage_id="global_review",
                    command="global-review",
                    severity="block",
                    code="UNCOVERED_SCORE",
                    title=f"coverage {sid}",
                    target_type="score_point",
                    target_ids=[sid],
                    likely_cause_stage="write_chapters",
                    suggested_actions=[
                        {"type": "fix_coverage", "params": {"max_chapters": 3}},
                        {"type": "rewrite_chapters", "params": {}},
                    ],
                )
                for sid in ("S1", "S2")
            ]
            issues += [
                make_issue(
                    stage_id="compliance_check",
                    command="compliance-check",
                    severity="block",
                    code="COMPLIANCE_MAJOR",
                    title=f"compliance {check_id}",
                    target_type="compliance_item",
                    target_ids=[check_id],
                    likely_cause_stage="write_chapters",
                    suggested_actions=[
                        {"type": "fix_compliance", "params": {"max_chapters": 4}}
                    ],
                )
                for check_id in ("C1", "C2")
            ]
            issues += [
                make_issue(
                    stage_id="review_fix_chapters",
                    command="review-fix-all",
                    severity="block",
                    code="CHAPTER_REVIEW_BLOCKER",
                    title=f"rewrite {chapter_id}",
                    target_type="chapter",
                    target_ids=[chapter_id],
                    likely_cause_stage="write_chapters",
                    suggested_actions=[{"type": "rewrite_chapters", "params": {}}],
                )
                for chapter_id in ("01", "02")
            ]
            upsert_issues(root, issues)

            plan = build_repair_batch_plan(root, [str(issue["id"]) for issue in issues])

            self.assertTrue(plan["ok"], plan)
            self.assertEqual(len(plan["groups"]), 1)
            by_type = {action["type"]: action for action in plan["actions"]}
            self.assertEqual(set(by_type), {"fix_coverage", "fix_compliance", "rewrite_chapters"})
            self.assertEqual(by_type["rewrite_chapters"]["target_ids"], ["01", "02"])
            self.assertEqual(len([a for a in plan["actions"] if a["type"] == "fix_coverage"]), 1)
            self.assertEqual(len([a for a in plan["actions"] if a["type"] == "fix_compliance"]), 1)

    def test_confirmed_batch_has_no_default_five_issue_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            issues = [
                make_issue(
                    stage_id="global_review",
                    command="global-review",
                    severity="block",
                    code="CHAPTER_CONFLICT",
                    title=f"chapter {index}",
                    target_type="chapter",
                    target_ids=[f"{index:02d}"],
                    suggested_actions=[{"type": "rewrite_chapters", "params": {}}],
                )
                for index in range(1, 7)
            ]
            upsert_issues(root, issues)
            ids = [str(issue["id"]) for issue in issues]

            with mock.patch("agent.tool_runtime.invoke", return_value=FakeResult(True)) as invoke:
                with mock.patch("agent.repair._sync_gate_issues", return_value=[]):
                    with mock.patch("agent.repair._open_issue_map", return_value={}):
                        result = execute_repair_batch(root, ids, confirm=True)

            self.assertFalse(result["truncated"], result)
            self.assertIsNone(result["limit"])
            self.assertEqual(result["total"], 6)
            self.assertEqual(result["resolved_count"], 6)
            self.assertEqual(result["success_count"], 6)
            # One merged rewrite plus one de-duplicated global-review gate.
            self.assertEqual(invoke.call_count, 2)

    def test_real_compliance_report_with_42_items_runs_one_grouped_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            report = {
                "blocking": True,
                "summary": {"blocking": True, "counts": {"fail": 42}},
                "items": [
                    {
                        "check_id": f"QUAL-{index:03d}",
                        "check_name": "资格条件检查",
                        "check_type": "qualification",
                        "status": "fail",
                        "severity": "critical",
                        "requirement": f"资格要求 {index}",
                        "auto_fixable": True,
                        "need_manual_review": False,
                    }
                    for index in range(1, 43)
                ],
            }
            report_path = root / "workspace" / "compliance_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            issues = sync_issues_from_compliance(root)
            ids = [str(issue["id"]) for issue in issues]
            self.assertEqual(len(ids), 42)

            with mock.patch("agent.tool_runtime.invoke", return_value=FakeResult(True)) as invoke:
                with mock.patch("agent.repair._sync_gate_issues", return_value=[]):
                    with mock.patch("agent.repair._open_issue_map", return_value={}):
                        result = execute_repair_batch(root, ids, confirm=True)

            self.assertEqual(result["total"], 42)
            self.assertFalse(result["truncated"])
            self.assertEqual(result["resolved_count"], 42)
            self.assertEqual(
                [action["type"] for action in result["actions"]],
                ["fix_compliance"],
            )
            self.assertEqual(result["revalidated_commands"], ["compliance-check"])
            self.assertEqual(invoke.call_count, 2)
            self.assertEqual(invoke.call_args_list[0].args[0], "fix_compliance")
            self.assertEqual(invoke.call_args_list[0].args[1]["max_chapters"], 10_000)
            self.assertEqual(invoke.call_args_list[-1].args[0], "run_stage")
            self.assertEqual(
                invoke.call_args_list[-1].args[1],
                {"command": "compliance-check", "force": True},
            )

    def test_open_post_fingerprints_are_not_counted_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            issues = [
                make_issue(
                    stage_id="global_review",
                    command="global-review",
                    severity="block",
                    code="CHAPTER_CONFLICT",
                    title=f"chapter {index}",
                    target_type="chapter",
                    target_ids=[f"{index:02d}"],
                    suggested_actions=[{"type": "rewrite_chapters", "params": {}}],
                )
                for index in range(1, 3)
            ]
            upsert_issues(root, issues)
            progress: list[tuple[str, dict]] = []

            with mock.patch("agent.tool_runtime.invoke", return_value=FakeResult(True)) as invoke:
                with mock.patch("agent.repair._sync_gate_issues", return_value=[]):
                    result = execute_repair_batch(
                        root,
                        [str(issue["id"]) for issue in issues],
                        confirm=True,
                        progress_callback=lambda phase, payload: progress.append((phase, payload)),
                    )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["success_count"], 0)
            self.assertEqual(result["still_open_count"], 2)
            self.assertEqual(result["revalidated_commands"], ["global-review"])
            self.assertTrue(result["no_progress"])
            self.assertEqual(invoke.call_count, 2)
            phases = [phase for phase, _payload in progress]
            self.assertIn("analysis", phases)
            self.assertIn("edit", phases)
            self.assertIn("revalidate", phases)
            self.assertEqual(phases[-1], "complete")

    def test_remaining_manual_and_failed_issues_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            manual = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="NEEDS_EVIDENCE",
                title="manual",
                suggested_actions=[{"type": "upload_evidence", "params": {}}],
            )
            failed = make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="CHAPTER_CONFLICT",
                title="failed",
                target_type="chapter",
                target_ids=["01"],
                suggested_actions=[{"type": "rewrite_chapters", "params": {}}],
            )
            upsert_issues(root, [manual, failed])

            def invoke_side_effect(tool: str, *_args, **_kwargs):
                return FakeResult(tool != "rewrite_chapters")

            with mock.patch("agent.tool_runtime.invoke", side_effect=invoke_side_effect):
                with mock.patch("agent.repair._sync_gate_issues", return_value=[]):
                    result = execute_repair_batch(
                        root,
                        [str(manual["id"]), str(failed["id"])],
                        confirm=True,
                    )

            self.assertEqual(result["manual"], [manual["id"]])
            self.assertEqual(result["failed"], [failed["id"]])
            self.assertEqual(result["success_count"], 0)
            self.assertFalse(result["ok"])

    def test_manual_issue_stays_manual_when_its_gate_cannot_revalidate(self) -> None:
        """A missing human-upload must not be mislabeled as a tool failure."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            manual = make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity="block",
                code="NEEDS_EVIDENCE",
                title="manual evidence",
                suggested_actions=[{"type": "upload_evidence", "params": {}}],
            )
            upsert_issues(root, [manual])

            with mock.patch("agent.tool_runtime.invoke", return_value=FakeResult(False)):
                with mock.patch("agent.repair._sync_gate_issues", return_value=[]):
                    result = execute_repair_batch(root, [str(manual["id"])], confirm=True)

            self.assertEqual(result["manual"], [manual["id"]])
            self.assertEqual(result["failed"], [])
            self.assertEqual(result["manual_count"], 1)
            self.assertEqual(result["failed_count"], 0)


if __name__ == "__main__":
    unittest.main()
