from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chapter_reviewer import normalize_review, should_auto_rewrite
from chapter_rewriter import _apply_stuck_detection, _collect_auto_rewrite_ids
from utils import write_json


class NormalizeReviewTests(unittest.TestCase):
    def test_minor_only_does_not_need_rewrite(self) -> None:
        review = normalize_review(
            {
                "score_coverage": [
                    {
                        "score_point_id": "S001",
                        "covered": True,
                        "coverage_level": "medium",
                        "evidence": "有基本响应",
                        "suggestion": "可再细化指标",
                    }
                ],
                "problems": [
                    {
                        "type": "style_polish",
                        "severity": "minor",
                        "description": "个别表述可更精炼",
                        "suggestion": "压缩空话",
                    }
                ],
                "need_rewrite": True,
            },
            {"id": "01", "title": "测试章"},
            [{"id": "S001"}],
        )
        self.assertFalse(review["need_rewrite"])
        self.assertEqual(review["max_severity"], "minor")
        self.assertEqual(review["rewrite_status"], "ok")
        self.assertTrue(review["priority_fixes"])
        self.assertTrue(all(item["severity"] == "minor" for item in review["priority_fixes"]))
        self.assertFalse(should_auto_rewrite(review))

    def test_low_coverage_and_major_problem_need_rewrite(self) -> None:
        review = normalize_review(
            {
                "score_coverage": [
                    {
                        "score_point_id": "S001",
                        "covered": True,
                        "coverage_level": "low",
                        "evidence": "仅一句话带过",
                        "suggestion": "补充方案细节",
                    }
                ],
                "problems": [
                    {
                        "type": "content_too_generic",
                        "description": "内容空泛",
                        "suggestion": "补充招标场景",
                    }
                ],
            },
            {"id": "02", "title": "方案章"},
            [{"id": "S001"}],
        )
        self.assertTrue(review["need_rewrite"])
        self.assertEqual(review["max_severity"], "major")
        self.assertEqual(review["rewrite_status"], "need_rewrite")
        self.assertTrue(should_auto_rewrite(review))
        self.assertLessEqual(len(review["priority_fixes"]), 5)
        self.assertTrue(any(item["severity"] == "major" for item in review["priority_fixes"]))
        self.assertTrue(all("action" in item and "acceptance" in item for item in review["priority_fixes"]))

    def test_uncovered_score_is_blocker(self) -> None:
        review = normalize_review(
            {
                "score_coverage": [
                    {
                        "score_point_id": "S002",
                        "covered": False,
                        "coverage_level": "none",
                        "evidence": "",
                        "suggestion": "补写该评分点响应",
                    }
                ],
                "problems": [],
            },
            {"id": "03", "title": "人员章"},
            [{"id": "S002"}],
        )
        self.assertTrue(review["need_rewrite"])
        self.assertTrue(review["need_evidence"])
        self.assertEqual(review["max_severity"], "blocker")
        self.assertEqual(review["priority_fixes"][0]["severity"], "blocker")
        # 未覆盖仍允许尝试改稿补响应
        self.assertTrue(should_auto_rewrite(review))

    def test_pure_missing_evidence_skips_auto_rewrite(self) -> None:
        review = normalize_review(
            {
                "score_coverage": [
                    {
                        "score_point_id": "S003",
                        "covered": True,
                        "coverage_level": "high",
                        "evidence": "已说明将提供证明材料",
                        "suggestion": "",
                    }
                ],
                "problems": [
                    {
                        "type": "missing_evidence",
                        "severity": "blocker",
                        "description": "缺少资质证书扫描件支撑",
                        "suggestion": "补充公司资质材料后再写",
                    }
                ],
                "need_evidence": True,
            },
            {"id": "04", "title": "资质章"},
            [{"id": "S003"}],
        )
        self.assertTrue(review["need_rewrite"])
        self.assertTrue(review["need_evidence"])
        self.assertEqual(review["rewrite_status"], "need_evidence")
        self.assertFalse(review["has_writing_fixes"])
        self.assertFalse(should_auto_rewrite(review))


class ReviewFixRoutingTests(unittest.TestCase):
    def test_collect_auto_rewrite_skips_evidence_and_stuck(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "workspace" / "reviews"
            reviews.mkdir(parents=True)
            write_json(
                reviews / "01_review.json",
                {
                    "chapter_id": "01",
                    "need_rewrite": True,
                    "rewrite_status": "need_rewrite",
                    "has_writing_fixes": True,
                    "priority_fixes": [{"id": "a", "severity": "major", "source": "problem", "score_point_id": "", "problem_type": "content_too_generic", "target": "t", "action": "a", "acceptance": "x"}],
                },
            )
            write_json(
                reviews / "02_review.json",
                {
                    "chapter_id": "02",
                    "need_rewrite": True,
                    "need_evidence": True,
                    "has_writing_fixes": False,
                    "rewrite_status": "need_evidence",
                    "priority_fixes": [],
                },
            )
            write_json(
                reviews / "03_review.json",
                {
                    "chapter_id": "03",
                    "need_rewrite": True,
                    "stuck": True,
                    "rewrite_status": "stuck",
                    "priority_fixes": [],
                },
            )
            rewrite_ids, evidence_ids, stuck_ids = _collect_auto_rewrite_ids(root)
            self.assertEqual(rewrite_ids, ["01"])
            self.assertEqual(evidence_ids, ["02"])
            self.assertEqual(stuck_ids, ["03"])

    def test_stuck_detection_marks_review(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "workspace" / "reviews"
            reviews.mkdir(parents=True)
            fix = {
                "id": "cov_S001",
                "severity": "blocker",
                "source": "score_coverage",
                "score_point_id": "S001",
                "problem_type": "incomplete_coverage",
                "target": "评分点 S001 覆盖不足（none）",
                "action": "补写",
                "acceptance": "covered",
            }
            review = {
                "chapter_id": "01",
                "need_rewrite": True,
                "has_writing_fixes": True,
                "rewrite_status": "need_rewrite",
                "priority_fixes": [fix],
            }
            write_json(reviews / "01_review.json", review)
            previous = {"01": [f"{fix['severity']}|{fix['source']}|{fix['score_point_id']}|{fix['problem_type']}|{fix['target'][:120]}"]}
            unchanged = {"01": 1}
            stuck_now = _apply_stuck_detection(root, ["01"], previous, unchanged)
            self.assertEqual(stuck_now, ["01"])
            updated = (reviews / "01_review.json").read_text(encoding="utf-8")
            self.assertIn('"rewrite_status": "stuck"', updated)


if __name__ == "__main__":
    unittest.main()
