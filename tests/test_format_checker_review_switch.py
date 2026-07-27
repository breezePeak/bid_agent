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

import format_checker


class FormatCheckerReviewSwitchTests(unittest.TestCase):
    def test_disabled_review_skips_compliance_and_claim_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace").mkdir(parents=True)
            checks = (
                "_check_template_schema",
                "_check_template_evidence",
                "_check_markdown",
                "_check_docx",
                "_check_template_fill_report",
                "_check_price_and_deviation_tables",
            )
            patches = [mock.patch.object(format_checker, name) for name in checks]
            started = [patch.start() for patch in patches]
            self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])
            with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
                with mock.patch(
                    "compliance_checker.enforce_final_compliance_gate"
                ) as compliance:
                    output = format_checker.check_output_format(root)

            self.assertTrue(all(item is not None for item in started))
            compliance.assert_not_called()
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["fail_count"], 0)
            self.assertTrue(
                any(item["name"] == "review_disabled" for item in report["results"])
            )


if __name__ == "__main__":
    unittest.main()
