from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graph.chapter_subgraph import route_after_self_check, self_check_chapter


class ChapterSubgraphLoopTests(unittest.TestCase):
    def test_route_pass_saves(self) -> None:
        state = {"chapter_status": "passed", "rewrite_round": 0, "max_rewrite_rounds": 2}
        self.assertEqual(route_after_self_check(state), "save_chapter")

    def test_route_need_evidence_saves(self) -> None:
        state = {
            "chapter_status": "deferred_material",
            "rewrite_round": 0,
            "max_rewrite_rounds": 2,
            "self_check": {"need_evidence": True, "need_rewrite": True, "has_writing_fixes": False},
        }
        self.assertEqual(route_after_self_check(state), "save_chapter")

    def test_route_need_rewrite_loops(self) -> None:
        state = {
            "chapter_status": "need_rewrite",
            "rewrite_round": 0,
            "max_rewrite_rounds": 2,
            "self_check": {
                "need_rewrite": True,
                "need_evidence": False,
                "has_writing_fixes": True,
                "rewrite_status": "need_rewrite",
            },
        }
        self.assertEqual(route_after_self_check(state), "rewrite_chapter")

    def test_route_max_rounds_saves(self) -> None:
        state = {
            "chapter_status": "need_rewrite",
            "rewrite_round": 2,
            "max_rewrite_rounds": 2,
            "self_check": {
                "need_rewrite": True,
                "need_evidence": False,
                "has_writing_fixes": True,
                "rewrite_status": "need_rewrite",
            },
        }
        self.assertEqual(route_after_self_check(state), "save_chapter")

    def test_stuck_on_same_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {
                "chapter_id": "01",
                "chapter_title": "资格",
                "score_point_ids": [],
                "description": "",
                "sections": [],
            }
            review = {
                "chapter_id": "01",
                "need_rewrite": True,
                "need_evidence": False,
                "has_writing_fixes": True,
                "rewrite_status": "need_rewrite",
                "max_severity": "blocker",
                "priority_fixes": [
                    {
                        "severity": "blocker",
                        "source": "problem",
                        "score_point_id": "SP1",
                        "problem_type": "gap",
                        "target": "缺响应",
                    }
                ],
                "problems": [],
            }
            with mock.patch("graph.chapter_subgraph.review_chapter_markdown", return_value=review):
                with mock.patch("graph.chapter_subgraph.load_score_points", return_value=[]):
                    with mock.patch("graph.chapter_subgraph.load_global_facts", return_value={}):
                        state1 = self_check_chapter(
                            {
                                "root_dir": str(root),
                                "job": job,
                                "chapter_id": "01",
                                "chapter_markdown": "# 01\n内容",
                                "problem_fingerprints": [],
                                "rewrite_round": 0,
                            }
                        )
                        sig = state1.get("last_problem_signature")
                        self.assertTrue(sig)
                        state2 = self_check_chapter(
                            {
                                "root_dir": str(root),
                                "job": job,
                                "chapter_id": "01",
                                "chapter_markdown": "# 01\n内容",
                                "problem_fingerprints": [sig],
                                "rewrite_round": 1,
                            }
                        )
                        self.assertEqual(state2.get("chapter_status"), "stuck")


if __name__ == "__main__":
    unittest.main()
