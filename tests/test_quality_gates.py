from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quality_gates import validate_outline_score_coverage


class QualityGateTests(unittest.TestCase):
    def test_outline_coverage_gate_rejects_missing_score_points(self) -> None:
        outline = {"chapters": [{"id": "01", "score_point_ids": ["S001"]}]}
        score_points = [{"id": "S001"}, {"id": "S002"}]
        with self.assertRaises(ValueError):
            validate_outline_score_coverage(outline, score_points)


if __name__ == "__main__":
    unittest.main()
