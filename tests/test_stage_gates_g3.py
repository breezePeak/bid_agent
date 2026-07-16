from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.root_cause import (
    issues_from_empty_score_points,
    issues_from_outline_error,
    issues_from_review_fix,
    issues_from_write_failures,
)
from agent.issues import upsert_issues, open_block_issues, assert_can_proceed


class StageGatesG3Tests(unittest.TestCase):
    def test_review_fix_issues(self) -> None:
        issues = issues_from_review_fix(
            need_rewrite_ids=["01"],
            need_evidence_ids=["02"],
            stuck_ids=["03"],
        )
        self.assertEqual(len(issues), 3)
        self.assertTrue(all(i["severity"] == "block" for i in issues))

    def test_write_failures(self) -> None:
        issues = issues_from_write_failures([{"chapter_id": "05", "error": "timeout"}])
        self.assertEqual(issues[0]["code"], "WRITE_CHAPTER_FAILED")

    def test_outline_and_score(self) -> None:
        self.assertEqual(issues_from_empty_score_points()[0]["code"], "EMPTY_SCORE_POINTS")
        self.assertEqual(issues_from_outline_error("missing", ["S001"])[0]["code"], "OUTLINE_UNBOUND_SCORE")

    def test_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            upsert_issues(root, issues_from_write_failures([{"chapter_id": "01", "error": "x"}]))
            self.assertTrue(open_block_issues(root))
            with self.assertRaises(RuntimeError):
                assert_can_proceed(root, next_command="build-docx")


if __name__ == "__main__":
    unittest.main()
