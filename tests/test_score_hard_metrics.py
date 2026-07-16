from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from score_coverage_matrix import build_score_coverage_matrix
from score_hard_metrics import compute_score_point_hard_metrics


class ScoreHardMetricsTests(unittest.TestCase):
    def test_keyword_hit_rate(self) -> None:
        score_point = {
            "title": "实施方案",
            "requirement": "应提供详细实施计划、进度安排和风险控制措施",
            "keywords": ["实施计划", "进度安排", "风险控制"],
        }
        chapter_texts = {
            "01": "本章给出实施计划与进度安排，并说明风险控制措施和保障机制。"
        }
        hard = compute_score_point_hard_metrics(score_point, chapter_texts, ["01"])
        self.assertGreaterEqual(hard["keyword_hit_rate"], 0.9)
        self.assertIn(hard["level_hint"], {"high", "medium"})

    def test_none_when_unrelated(self) -> None:
        score_point = {
            "title": "报价要求",
            "requirement": "投标报价不得超过最高限价",
            "keywords": ["最高限价", "投标报价", "分项报价"],
        }
        chapter_texts = {"01": "本章介绍公司发展历程与企业文化。"}
        hard = compute_score_point_hard_metrics(score_point, chapter_texts, ["01"])
        self.assertEqual(hard["level_hint"], "none")

    def test_matrix_includes_hard_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            (ws / "chapters").mkdir(parents=True)
            (ws / "jobs").mkdir(parents=True)
            (ws / "reviews").mkdir(parents=True)
            (ws / "score_points.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "S001",
                            "title": "技术服务方案",
                            "score": 20,
                            "requirement": "提供技术服务方案和实施计划",
                            "keywords": ["技术服务", "实施计划"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (ws / "outline.json").write_text(
                json.dumps({"chapters": [{"id": "01", "title": "技术方案"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (ws / "jobs" / "01.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "01",
                        "chapter_title": "技术方案",
                        "score_point_ids": ["S001"],
                        "description": "写技术方案",
                        "sections": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (ws / "chapters" / "01.md").write_text(
                "# 01 技术方案\n我们提供完整技术服务方案与实施计划。",
                encoding="utf-8",
            )
            (ws / "reviews" / "01_review.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "01",
                        "chapter_title": "技术方案",
                        "score_coverage": [
                            {
                                "score_point_id": "S001",
                                "covered": True,
                                "coverage_level": "high",
                                "evidence": "有方案",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            path = build_score_coverage_matrix(root)
            matrix = json.loads(path.read_text(encoding="utf-8"))
            row = matrix["matrix"][0]
            self.assertIn("hard_metrics", row)
            self.assertIn("level_hint", row["hard_metrics"])
            self.assertIn("hard_strong_score_points", matrix)


if __name__ == "__main__":
    unittest.main()
