from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compliance_feedback import compliance_hints_for_chapter, sync_compliance_findings


class ComplianceFeedbackTests(unittest.TestCase):
    def test_sync_injects_rewrite_hints_and_review_fixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            (workspace / "chapters").mkdir(parents=True)
            (workspace / "reviews").mkdir(parents=True)
            (workspace / "jobs").mkdir(parents=True)
            (workspace / "manual_review").mkdir(parents=True)

            (workspace / "chapters" / "01.md").write_text("# 01 商务响应\n内容", encoding="utf-8")
            (workspace / "jobs" / "01.json").write_text(
                json.dumps({"chapter_id": "01", "chapter_title": "商务响应与报价", "description": "报价与商务"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (workspace / "reviews" / "01_review.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "01",
                        "problems": [],
                        "priority_fixes": [],
                        "need_rewrite": False,
                        "rewrite_status": "ok",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (workspace / "compliance_report.json").write_text(
                json.dumps(
                    {
                        "blocking": True,
                        "items": [
                            {
                                "check_id": "PRICE-010",
                                "check_type": "commercial",
                                "check_name": "最高限价检查",
                                "status": "fail",
                                "severity": "fatal",
                                "requirement": "最高限价 10 万",
                                "suggestion": "降低报价至限价内",
                            },
                            {
                                "check_id": "SIGN-001",
                                "check_type": "signature",
                                "check_name": "投标函签字盖章",
                                "status": "fail",
                                "severity": "fatal",
                                "requirement": "须签章",
                                "suggestion": "人工核验签章",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            hints_path = sync_compliance_findings(root)
            self.assertTrue(hints_path.exists())
            hints = json.loads(hints_path.read_text(encoding="utf-8"))
            self.assertIn("01", hints.get("chapters", {}))
            self.assertTrue(any(item.get("check_id") == "PRICE-010" for item in hints["chapters"]["01"]))

            # 签章只进人工，不进改稿
            self.assertFalse(any(item.get("check_id") == "SIGN-001" for item in hints["chapters"]["01"]))

            review = json.loads((workspace / "reviews" / "01_review.json").read_text(encoding="utf-8"))
            self.assertTrue(review.get("need_rewrite"))
            self.assertEqual(review.get("rewrite_status"), "need_rewrite")
            self.assertTrue(any("PRICE-010" in stringify(item) for item in review.get("priority_fixes", [])))

            chapter_hints = compliance_hints_for_chapter(root, "01")
            self.assertTrue(chapter_hints)


def stringify(item) -> str:
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    return str(item)


if __name__ == "__main__":
    unittest.main()
