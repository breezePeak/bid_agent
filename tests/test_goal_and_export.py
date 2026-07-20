from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import (
    create_goal,
    evaluate_criteria,
    goal_summary,
    infer_goal_from_message,
    load_goal,
    reevaluate_goal,
)
from agent.invalidation import mark_invalidated, is_stale
from agent.tool_registry import get_tool, reset_tool_index
from agent.tool_runtime import invoke


class GoalStateTests(unittest.TestCase):
    def test_infer_export_and_rewrite(self) -> None:
        g = infer_goal_from_message("请改第01章并出 Word")
        types = [o.get("type") for o in g["objectives"]]
        self.assertIn("fix_chapter", types)
        self.assertIn("export", types)
        self.assertTrue(any(c.get("check") == "artifact_exists" for c in g["success_criteria"]))

    def test_create_and_reevaluate_export_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="导出 Word",
                objectives=[{"type": "export"}],
                success_criteria=[
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                    {"check": "no_stale", "paths": ["outputs/final.md", "outputs/final.docx"]},
                ],
            )
            self.assertEqual(goal["status"], "in_progress")
            self.assertFalse(goal["all_criteria_ok"])

            # create artifacts
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
            (root / "outputs" / "final.docx").write_text("PK", encoding="utf-8")
            goal2 = reevaluate_goal(root)
            self.assertTrue(goal2["all_criteria_ok"])
            self.assertEqual(goal2["status"], "succeeded")

            # stale should fail no_stale / artifact effective readiness
            mark_invalidated(root, reason="test", chapter_ids=["01"], source_stage="write_chapters")
            goal3 = reevaluate_goal(root)
            self.assertFalse(goal3["all_criteria_ok"])
            self.assertIn("目标", goal_summary(goal3))

    def test_chapters_written_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ch = root / "workspace" / "chapters"
            ch.mkdir(parents=True)
            (ch / "01.md").write_text("x", encoding="utf-8")
            results = evaluate_criteria(
                root,
                [{"check": "chapters_written", "chapter_ids": ["01", "02"]}],
            )
            self.assertFalse(results[0]["ok"])
            (ch / "02.md").write_text("y", encoding="utf-8")
            results2 = evaluate_criteria(
                root,
                [{"check": "chapters_written", "chapter_ids": ["01", "02"]}],
            )
            self.assertTrue(results2[0]["ok"])


class BuildExportTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_registered(self) -> None:
        spec = get_tool("build_export")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.risk_level, "high")
        self.assertTrue(spec.human_confirm_required)

    def test_missing_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("build_export", {"targets": ["md"]}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "missing_requires")

    def test_dry_run_with_stale_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            (chapters / "01.md").write_text("# c", encoding="utf-8")
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("old", encoding="utf-8")
            mark_invalidated(root, reason="rewrite", chapter_ids=["01"], source_stage="write_chapters")
            self.assertTrue(is_stale(root, "outputs/final.md"))
            result = invoke(
                "build_export",
                {"targets": ["md", "docx"], "dry_run": True},
                root=root,
            )
            self.assertTrue(result.ok, result.summary_for_llm)
            stages = result.metrics.get("stages") or []
            self.assertIn("build_markdown", stages)
            self.assertIn("build_docx", stages)

    def test_export_rebuilds_stale_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # minimal tree for build_markdown
            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            (chapters / "01.md").write_text("# 01\nhello", encoding="utf-8")
            outline = {
                "chapters": [{"id": "01", "title": "T", "score_point_ids": [], "sections": []}]
            }
            (root / "workspace").mkdir(exist_ok=True)
            (root / "workspace" / "outline.json").write_text(
                json.dumps(outline, ensure_ascii=False), encoding="utf-8"
            )
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("STALE_CONTENT", encoding="utf-8")
            mark_invalidated(root, reason="rewrite", chapter_ids=["01"], source_stage="write_chapters")

            # mock stage executors to avoid full docx dependency
            def fake_stage(root_arg, stage_id, force=False, workers=1, max_retries=0, dry_run=False):
                from agent.types import ToolResult
                from agent.tool_runtime import _now

                if stage_id == "build_markdown":
                    (root_arg / "outputs" / "final.md").write_text("# rebuilt\nhello", encoding="utf-8")
                if stage_id == "build_docx":
                    (root_arg / "outputs" / "final.docx").write_bytes(b"PK\x03\x04fake")
                if stage_id == "check_format":
                    (root_arg / "workspace" / "format_check_report.json").write_text("{}", encoding="utf-8")
                return ToolResult(
                    ok=True,
                    tool=stage_id,
                    args={"force": force},
                    started_at=_now(),
                    ended_at=_now(),
                    summary_for_llm=f"fake {stage_id}",
                )

            # formal export preflight needs non-blocking review artifacts
            (root / "workspace" / "global_review.json").write_text(
                '{"blocking": false}', encoding="utf-8"
            )
            (root / "workspace" / "compliance_report.json").write_text(
                '{"blocking": false, "summary": {"blocking": false}}', encoding="utf-8"
            )
            with mock.patch("agent.tool_runtime._execute_stage", side_effect=fake_stage):
                result = invoke("build_export", {"targets": ["md", "docx", "format"]}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertFalse(is_stale(root, "outputs/final.md"))
            self.assertFalse(is_stale(root, "outputs/final.docx"))
            self.assertIn("# rebuilt", (root / "outputs" / "final.md").read_text(encoding="utf-8"))


class SupervisorGoalExportTests(unittest.TestCase):
    def test_rule_suggests_build_export(self) -> None:
        from agent.supervisor import run_supervisor_turn

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_supervisor_turn(
                "请导出 final.docx 出稿",
                root=root,
                use_llm=False,
            )
            self.assertTrue(result.get("steps"))
            self.assertEqual(result["steps"][0]["tool"], "build_export")
            self.assertFalse(result["steps"][0]["executed"])  # needs confirm
            self.assertTrue(result.get("goal_id"))
            goal = load_goal(root)
            self.assertIsNotNone(goal)


if __name__ == "__main__":
    unittest.main()
