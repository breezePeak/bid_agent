from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graph.nodes import _stage_progress
from pipeline_registry import workflow_stage_specs


class StageProgressTests(unittest.TestCase):
    def test_progress_matches_registry(self) -> None:
        specs = workflow_stage_specs()
        total = len(specs)
        first = specs[0]
        last = specs[-1]
        self.assertEqual(_stage_progress(first.id), f"[1/{total}] {first.label}")
        self.assertEqual(_stage_progress(last.id), f"[{total}/{total}] {last.label}")
        mid = next(s for s in specs if s.id == "compliance_check")
        idx = next(i for i, s in enumerate(specs, start=1) if s.id == "compliance_check")
        self.assertEqual(_stage_progress("compliance_check"), f"[{idx}/{total}] {mid.label}")


if __name__ == "__main__":
    unittest.main()
