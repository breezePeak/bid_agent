from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import create_goal, evaluate_criteria, infer_goal_from_message, reevaluate_goal
from agent.policy import evaluate_tool_call, is_readonly_tool
from agent.tool_registry import get_tool, reset_tool_index
from agent.tool_runtime import invoke


def _write_matrix(root: Path) -> None:
    matrix = {
        "summary": {
            "score_point_count": 3,
            "fully_covered_score_point_count": 1,
            "uncovered_score_point_count": 1,
            "weak_score_point_count": 1,
        },
        "uncovered_score_points": ["S002"],
        "weak_score_points": ["S003"],
        "fully_covered_score_points": ["S001"],
        "matrix": [
            {
                "score_point_id": "S001",
                "covered": True,
                "coverage_level": "high",
                "bound_chapters": [{"chapter_id": "01"}],
                "review_coverage": [{"chapter_id": "01", "covered": True, "coverage_level": "high"}],
            },
            {
                "score_point_id": "S002",
                "covered": False,
                "coverage_level": "none",
                "bound_chapters": [{"chapter_id": "02"}, {"chapter_id": "01"}],
                "review_coverage": [{"chapter_id": "02", "covered": False, "coverage_level": "none"}],
            },
            {
                "score_point_id": "S003",
                "covered": False,
                "coverage_level": "weak",
                "bound_chapters": [{"chapter_id": "03"}],
                "review_coverage": [{"chapter_id": "03", "covered": False, "coverage_level": "low"}],
            },
        ],
    }
    path = root / "workspace" / "score_coverage_matrix.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")


class CoverageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_tools_registered(self) -> None:
        self.assertIsNotNone(get_tool("analyze_coverage"))
        self.assertIsNotNone(get_tool("fix_coverage"))
        self.assertTrue(is_readonly_tool("analyze_coverage"))

    def test_analyze_coverage_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_matrix(root)
            result = invoke("analyze_coverage", {"rebuild": False, "max_chapters": 2}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            chapters = result.metrics.get("chapter_ids") or []
            self.assertTrue(chapters)
            self.assertLessEqual(len(chapters), 2)
            self.assertIn("S002", result.metrics.get("uncovered_score_points") or [])

    def test_fix_coverage_plan_without_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_matrix(root)
            result = invoke(
                "fix_coverage",
                {"max_chapters": 2, "confirm_execute": False, "rebuild_matrix": False},
                root=root,
            )
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertFalse(result.metrics.get("executed"))
            self.assertEqual(result.metrics.get("pending_tool"), "rewrite_chapters")
            self.assertTrue(result.metrics.get("chapter_ids"))

    def test_fix_coverage_execute_calls_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_matrix(root)
            with mock.patch("agent.tool_runtime._execute_chapter_tool") as mocked:
                from agent.tool_runtime import _now
                from agent.types import ToolResult

                mocked.return_value = ToolResult(
                    ok=True,
                    tool="rewrite_chapters",
                    args={},
                    started_at=_now(),
                    ended_at=_now(),
                    summary_for_llm="rewritten",
                    metrics={"chapter_ids": ["02"]},
                    artifacts_written=["workspace/chapters"],
                )
                # prevent rebuild matrix real call
                with mock.patch(
                    "agent.tool_runtime._load_coverage_matrix",
                    side_effect=[
                        json.loads((root / "workspace" / "score_coverage_matrix.json").read_text(encoding="utf-8")),
                        {
                            "summary": {"score_point_count": 3, "fully_covered_score_point_count": 2},
                            "uncovered_score_points": [],
                            "weak_score_points": [],
                            "matrix": [],
                        },
                    ],
                ):
                    result = invoke(
                        "fix_coverage",
                        {"max_chapters": 2, "confirm_execute": True, "rebuild_matrix": False},
                        root=root,
                    )
                self.assertTrue(result.ok, result.summary_for_llm)
                self.assertTrue(result.metrics.get("executed"))
                self.assertTrue(mocked.called)

    def test_build_export_blocked_by_compliance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            (chapters / "01.md").write_text("# a", encoding="utf-8")
            (root / "workspace" / "compliance_report.json").write_text(
                json.dumps({"blocking": True, "summary": {"blocking": True}}),
                encoding="utf-8",
            )
            result = invoke("build_export", {"targets": ["md"]}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "gate_blocked")


class CoverageGoalTests(unittest.TestCase):
    def test_infer_coverage_goal(self) -> None:
        g = infer_goal_from_message("请补齐评分点覆盖率并出 Word")
        types = [o.get("type") for o in g["objectives"]]
        self.assertIn("fix_coverage", types)
        self.assertIn("export", types)
        self.assertTrue(any(c.get("check") == "score_coverage_min" for c in g["success_criteria"]))

    def test_score_coverage_min_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_matrix(root)
            results = evaluate_criteria(root, [{"check": "score_coverage_min", "ratio": 0.95}])
            self.assertFalse(results[0]["ok"])
            # improve matrix
            data = json.loads((root / "workspace" / "score_coverage_matrix.json").read_text(encoding="utf-8"))
            data["summary"]["fully_covered_score_point_count"] = 3
            data["uncovered_score_points"] = []
            data["weak_score_points"] = []
            (root / "workspace" / "score_coverage_matrix.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            results2 = evaluate_criteria(root, [{"check": "score_coverage_min", "ratio": 0.95}])
            self.assertTrue(results2[0]["ok"])


class CoverageSupervisorTests(unittest.TestCase):
    def test_rule_analyze_and_fix(self) -> None:
        from agent.supervisor import run_supervisor_turn

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_matrix(root)
            r1 = run_supervisor_turn("当前评分覆盖率如何", root=root, use_llm=False)
            self.assertEqual(r1["steps"][0]["tool"], "analyze_coverage")
            self.assertTrue(r1["steps"][0]["executed"])
            r2 = run_supervisor_turn("请根据覆盖缺口修复评分点", root=root, use_llm=False)
            self.assertEqual(r2["steps"][0]["tool"], "fix_coverage")
            self.assertFalse(r2["steps"][0]["executed"])


if __name__ == "__main__":
    unittest.main()
