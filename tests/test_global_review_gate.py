from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quality_gates import (
    final_review_status,
    global_review_blocking_reasons,
    validate_global_review_blocking,
)


class GlobalReviewGateTests(unittest.TestCase):
    def test_clean_review_ok(self) -> None:
        review = {
            "project_name_consistent": True,
            "bidder_name_consistent": True,
            "service_period_consistent": True,
            "warranty_period_consistent": True,
            "chapter_conflicts": [],
            "uncovered_score_points": [],
            "missing_chapters": [],
            "fabrication_risks": [],
            "need_manual_review": False,
        }
        self.assertEqual(global_review_blocking_reasons(review), [])
        self.assertEqual(final_review_status(review), "ok")

    def test_problems_block(self) -> None:
        review = {
            "project_name_consistent": False,
            "bidder_name_consistent": True,
            "uncovered_score_points": ["S003", "S004"],
            "chapter_conflicts": [],
            "fabrication_risks": [{"risk": "x"}],
            "missing_chapters": [],
            "need_manual_review": True,
        }
        reasons = global_review_blocking_reasons(review)
        self.assertTrue(any("项目名称" in r for r in reasons))
        self.assertTrue(any("未覆盖评分点" in r for r in reasons))
        self.assertTrue(any("编造风险" in r for r in reasons))
        self.assertEqual(final_review_status(review), "error")

    def test_validate_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            (ws / "global_review.json").write_text(
                json.dumps(
                    {
                        "project_name_consistent": False,
                        "uncovered_score_points": ["S001"],
                        "chapter_conflicts": [],
                        "fabrication_risks": [],
                        "missing_chapters": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                validate_global_review_blocking(root, required=True)


if __name__ == "__main__":
    unittest.main()
