from __future__ import annotations

import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chapter_reviewer import rewrite_fix_signatures, should_auto_rewrite


class ChapterStuckDetectionTests(unittest.TestCase):
    def test_should_not_rewrite_need_evidence(self) -> None:
        review = {
            "need_rewrite": True,
            "need_evidence": True,
            "has_writing_fixes": False,
            "rewrite_status": "need_evidence",
        }
        self.assertFalse(should_auto_rewrite(review))

    def test_should_not_rewrite_stuck(self) -> None:
        review = {
            "need_rewrite": True,
            "rewrite_status": "stuck",
            "stuck": True,
            "has_writing_fixes": True,
        }
        self.assertFalse(should_auto_rewrite(review))

    def test_signature_stable(self) -> None:
        review = {
            "priority_fixes": [
                {
                    "severity": "blocker",
                    "source": "problem",
                    "score_point_id": "A",
                    "problem_type": "gap",
                    "target": "x",
                }
            ]
        }
        a = rewrite_fix_signatures(review)
        b = rewrite_fix_signatures(review)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
