"""Compile score conditions into a chapter writing outline."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.chapter_writing_outline import (  # noqa: E402
    compile_chapter_writing_outline,
)


class ChapterWritingOutlineTests(unittest.TestCase):
    def test_compiles_conditions_into_ordered_write_blocks(self) -> None:
        chapter = {
            "chapter_id": "ch-method",
            "title": "关键技术方法",
            "blueprint_node": {
                "purpose": "展开各阶段核心技术方法",
                "score_condition_ids": ["SC-1", "SC-2"],
                "primary_response_unit_ids": ["RU-1"],
            },
        }
        outline = compile_chapter_writing_outline(
            chapter,
            scoring_requirements=[
                {
                    "score_point_id": "SP-1",
                    "title": "技术方案",
                    "response_expectation": "给出可执行方法",
                    "conditions": [
                        {
                            "condition_id": "SC-1",
                            "condition_role": "content",
                            "subject": "内业核查方法",
                            "response_intent": "写清内业核查步骤与质控节点",
                            "text": "应说明内业核查方法",
                        },
                        {
                            "condition_id": "SC-2",
                            "condition_role": "evidence",
                            "subject": "方法证明",
                            "response_intent": "列出可核验的方法证明材料",
                            "text": "提供证明材料",
                        },
                        {
                            "condition_id": "SC-DOC",
                            "condition_role": "document",
                            "subject": "全文格式",
                            "response_intent": "目录完整",
                            "text": "目录完整",
                        },
                    ],
                    "response_units": [
                        {
                            "unit_id": "RU-1",
                            "condition_ids": ["SC-1", "SC-2"],
                            "linked_requirement_ids": ["R-1"],
                        }
                    ],
                }
            ],
            tender_requirements=[{"requirement_id": "R-1", "text": "应开展内业核查"}],
        )
        kinds = [item["kind"] for item in outline["blocks"]]
        self.assertEqual(kinds, ["response", "evidence"])
        self.assertEqual(outline["blocks"][0]["heading"], "内业核查方法")
        self.assertIn("可执行做法", outline["blocks"][0]["write_as"])
        self.assertEqual(outline["blocks"][0]["outcome_kind"], "")
        self.assertNotIn("本章交付物", outline["blocks"][0]["write_as"])
        self.assertIn("证明类型", outline["blocks"][1]["write_as"])
        self.assertNotIn("满分条件", outline["blocks"][0]["must_answer"])
        self.assertEqual(outline["blocks"][0]["ownership"], "primary")

    def test_falls_back_to_purpose_when_no_score_conditions(self) -> None:
        outline = compile_chapter_writing_outline(
            {
                "chapter_id": "ch-bg",
                "title": "项目任务背景",
                "blueprint_node": {"purpose": "说明本项目对象、任务和需求"},
            }
        )
        self.assertEqual(outline["block_count"], 1)
        self.assertIn("本项目对象", outline["blocks"][0]["must_answer"])
        self.assertNotIn("交付物", outline["blocks"][0]["must_answer"])

    def test_marks_outcome_only_when_the_block_has_an_explicit_requirement(self) -> None:
        outline = compile_chapter_writing_outline(
            {
                "chapter_id": "ch-delivery",
                "title": "成果提交与验收",
                "blueprint_node": {"score_condition_ids": ["SC-1"]},
            },
            scoring_requirements=[
                {
                    "score_point_id": "SP-1",
                    "conditions": [
                        {
                            "condition_id": "SC-1",
                            "condition_role": "content",
                            "response_intent": "按要求提交成果报告并配合验收",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(outline["blocks"][0]["outcome_kind"], "acceptance")
        self.assertIn("验收口径", outline["blocks"][0]["write_as"])


if __name__ == "__main__":
    unittest.main()
