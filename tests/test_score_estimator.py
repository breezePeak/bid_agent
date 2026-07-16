from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from score_estimator import estimate_final_score


class ScoreEstimatorTests(unittest.TestCase):
    def test_estimate_from_coverage_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix = {
                "summary": {},
                "matrix": [
                    {
                        "score_point_id": "S001",
                        "score_point_title": "技术方案",
                        "category": "score",
                        "score": 20,
                        "bound_chapters": [{"chapter_id": "01"}],
                        "review_coverage": [{"covered": True, "coverage_level": "high"}],
                        "covered": True,
                        "coverage_levels": ["high"],
                        "risk_level": "low",
                    },
                    {
                        "score_point_id": "S002",
                        "score_point_title": "实施计划",
                        "category": "score",
                        "score": 10,
                        "bound_chapters": [{"chapter_id": "02"}],
                        "review_coverage": [{"covered": True, "coverage_level": "medium"}],
                        "covered": True,
                        "coverage_levels": ["medium"],
                        "risk_level": "low",
                    },
                    {
                        "score_point_id": "S003",
                        "score_point_title": "无分值项",
                        "category": "qualification",
                        "score": None,
                        "bound_chapters": [],
                        "review_coverage": [{"covered": True, "coverage_level": "high"}],
                        "covered": True,
                        "coverage_levels": ["high"],
                        "risk_level": "low",
                    },
                ],
            }
            path = root / "workspace" / "score_coverage_matrix.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")

            out = estimate_final_score(root)
            self.assertTrue(out.exists())
            report = json.loads(out.read_text(encoding="utf-8"))
            summary = report["summary"]
            # 20*0.95 + 10*0.70 = 19 + 7 = 26
            self.assertEqual(summary["full_score_total"], 30.0)
            self.assertEqual(summary["estimated_score_total"], 26.0)
            self.assertEqual(summary["estimated_percent"], 86.7)
            self.assertEqual(summary["grade"], "B")
            self.assertEqual(summary["unscored_point_count"], 1)
            self.assertTrue((root / "outputs" / "score_estimate.md").exists())


if __name__ == "__main__":
    unittest.main()
