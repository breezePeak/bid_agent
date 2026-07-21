"""Strict Goal state transitions: Tool success must NOT imply Goal success.

No loose assertions. Every case checks real status fields.
"""

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
    evaluate_goal_success,
    handle_plan_step_result,
    load_goal,
    reevaluate_goal,
    set_goal_status,
    validate_goal_transition,
)
from agent.issues import make_issue, open_block_issues, upsert_issues
from agent.supervisor import run_supervisor_turn
from agent.types import ToolError, ToolResult


def _tool(
    name: str,
    *,
    ok: bool = True,
    outcome: str = "completed",
    summary: str = "ok",
    error: ToolError | None = None,
) -> ToolResult:
    return ToolResult(
        ok=ok,
        tool=name,
        args={},
        started_at="",
        ended_at="",
        summary_for_llm=summary,
        summary=summary,
        outcome=outcome,
        error=error,
    )


def _seed_block_issue(root: Path, *, code: str = "WRITE_CHAPTER_FAILED", chapter_id: str = "4.1") -> dict:
    iss = make_issue(
        stage_id="write_chapters",
        command="write-chapters",
        severity="block",
        code=code,
        title=f"章节 {chapter_id} 写作失败",
        detail="test block",
        target_type="chapter",
        target_ids=[chapter_id],
    )
    upsert_issues(root, [iss])
    return iss


def _seed_chapter_file(root: Path, chapter_id: str = "4.1") -> None:
    ch = root / "workspace" / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    (ch / f"{chapter_id}.md").write_text(f"# {chapter_id}\ncontent", encoding="utf-8")


class GoalStateTransitionTests(unittest.TestCase):
    def test_v1_goal_file_is_imported_once_then_sqlite_remains_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace" / "agent" / "goal_state.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"goal_id": "goal-legacy", "status": "in_progress", "raw_user_goal": "original"}),
                encoding="utf-8",
            )
            self.assertEqual(load_goal(root)["raw_user_goal"], "original")
            path.write_text(
                json.dumps({"goal_id": "goal-legacy", "status": "succeeded", "raw_user_goal": "stale"}),
                encoding="utf-8",
            )
            current = load_goal(root)
            self.assertEqual(current["status"], "in_progress")
            self.assertEqual(current["raw_user_goal"], "original")

    def test_01_rewrite_success_does_not_succeed_goal_with_open_issue(self) -> None:
        """修复章节成功，不代表 Goal 成功。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_block_issue(root, chapter_id="4.1")
            _seed_chapter_file(root, "4.1")
            goal = create_goal(
                root,
                raw_user_goal="将写作失败的重新写",
                objectives=[{"type": "fix_chapter", "chapter_ids": ["4.1"]}],
                success_criteria=[
                    {"check": "chapters_written", "chapter_ids": ["4.1"]},
                    {"check": "no_open_blocks"},
                ],
                plan=[
                    {
                        "step_id": "rewrite_chapters",
                        "tool": "rewrite_chapters",
                        "args": {"chapter_ids": ["4.1"]},
                        "depends_on": [],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 2,
                        "label": "rewrite",
                    },
                    {
                        "step_id": "review_chapters",
                        "tool": "review_chapters",
                        "args": {"chapter_ids": ["4.1"]},
                        "depends_on": ["rewrite_chapters"],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 2,
                        "label": "review",
                    },
                ],
                completion_mode="criteria",
                constraints={
                    "chapter_ids": ["4.1"],
                    "block_on_missing_materials": False,
                },
            )
            self.assertEqual(len(open_block_issues(root)), 1)

            # Simulate rewrite tool: action completed, still need re-eval
            tr = _tool(
                "rewrite_chapters",
                ok=True,
                outcome="partial_completed",
                summary="rewrite done",
            )
            self.assertEqual(tr.outcome, "partial_completed")
            self.assertTrue(tr.ok)
            self.assertTrue(tr.step_done())

            goal = handle_plan_step_result(
                root,
                goal,
                "rewrite_chapters",
                ok=True,
                outcome="partial_completed",
            )
            step = next(s for s in goal["plan"] if s["step_id"] == "rewrite_chapters")
            self.assertEqual(step.get("status"), "done")

            goal = reevaluate_goal(root, goal)
            self.assertNotEqual(goal.get("status"), "succeeded")
            self.assertIn(goal.get("status"), {"in_progress", "blocked_human", "pending"})
            evaluation = evaluate_goal_success(root, goal)
            self.assertFalse(evaluation["ok"])
            self.assertGreater(int(evaluation.get("open_block_count") or 0), 0)

    def test_02_review_clears_issues_then_goal_succeeds(self) -> None:
        """重新审核通过、issues 清空后 Goal 才成功。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_block_issue(root, chapter_id="4.1")
            _seed_chapter_file(root, "4.1")
            goal = create_goal(
                root,
                raw_user_goal="将写作失败的重新写",
                objectives=[{"type": "fix_chapter", "chapter_ids": ["4.1"]}],
                success_criteria=[
                    {"check": "chapters_written", "chapter_ids": ["4.1"]},
                    {"check": "no_open_blocks"},
                ],
                plan=[
                    {
                        "step_id": "rewrite_chapters",
                        "tool": "rewrite_chapters",
                        "args": {"chapter_ids": ["4.1"]},
                        "depends_on": [],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 2,
                        "label": "rewrite",
                    },
                    {
                        "step_id": "review_chapters",
                        "tool": "review_chapters",
                        "args": {"chapter_ids": ["4.1"]},
                        "depends_on": ["rewrite_chapters"],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 2,
                        "label": "review",
                    },
                ],
                completion_mode="criteria",
                constraints={
                    "chapter_ids": ["4.1"],
                    "block_on_missing_materials": False,
                },
            )

            goal = handle_plan_step_result(
                root, goal, "rewrite_chapters", ok=True, outcome="partial_completed"
            )
            goal = reevaluate_goal(root, goal)
            self.assertNotEqual(goal.get("status"), "succeeded")

            # Clear issues as if review passed quality
            upsert_issues(root, [], replace_stage_id="write_chapters")
            # Also wipe all open blocks
            from agent.issues import save_open_issues

            save_open_issues(root, [])
            self.assertEqual(len(open_block_issues(root)), 0)

            goal = handle_plan_step_result(
                root, goal, "review_chapters", ok=True, outcome="completed"
            )
            goal = reevaluate_goal(root, goal)
            self.assertEqual(goal.get("status"), "succeeded")
            evaluation = evaluate_goal_success(root, goal)
            self.assertTrue(evaluation["ok"])
            self.assertTrue(evaluation["goal_success_evaluation"])

    def test_03_material_block_not_bypassed_by_tool_success(self) -> None:
        """材料缺失阻断不能被 Tool success 绕过。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="继续生成",
                objectives=[{"type": "full_generate"}],
                success_criteria=[
                    {"check": "artifact_exists", "path": "outputs/final.md"},
                ],
                plan=[
                    {
                        "step_id": "run_pipeline_remaining",
                        "tool": "run_pipeline_remaining",
                        "args": {},
                        "depends_on": [],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 2,
                        "label": "pipeline",
                    }
                ],
                completion_mode="criteria",
                constraints={"block_on_missing_materials": True},
            )

            # Tool reports blocked (materials) with ok=True historically
            goal = handle_plan_step_result(
                root,
                goal,
                "run_pipeline_remaining",
                ok=True,
                outcome="blocked",
                error="缺少不可自动补齐的材料: 营业执照",
                error_code="blocked",
            )
            step = next(s for s in goal["plan"] if s["step_id"] == "run_pipeline_remaining")
            self.assertEqual(step.get("status"), "blocked")
            self.assertNotEqual(step.get("status"), "done")

            # Force materials block in detect_human_block
            with mock.patch(
                "agent.goal.detect_human_block",
                return_value="缺少不可自动补齐的材料: 营业执照",
            ):
                goal = reevaluate_goal(root, load_goal(root) or goal)
                self.assertEqual(goal.get("status"), "blocked_human")
                self.assertNotEqual(goal.get("status"), "succeeded")
                # Even set_goal_status cannot force succeeded
                g2 = set_goal_status(root, "succeeded", goal=goal)
                self.assertNotEqual(g2.get("status"), "succeeded")
                self.assertEqual(g2.get("status"), "blocked_human")

    def test_04_open_block_issue_blocks_success_after_mutation_tool(self) -> None:
        """质量门禁阻断不能自动成功：open block issue + mutation tool 完成后 Goal != succeeded。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_block_issue(root, code="CHAPTER_REVIEW_BLOCKER", chapter_id="2.1")
            _seed_chapter_file(root, "2.1")
            goal = create_goal(
                root,
                raw_user_goal="修复质量问题",
                objectives=[{"type": "fix_chapter", "chapter_ids": ["2.1"]}],
                success_criteria=[
                    {"check": "chapters_written", "chapter_ids": ["2.1"]},
                    {"check": "no_open_blocks"},
                ],
                plan=[
                    {
                        "step_id": "rewrite_chapters",
                        "tool": "rewrite_chapters",
                        "args": {"chapter_ids": ["2.1"]},
                        "depends_on": [],
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 1,
                        "label": "rewrite",
                    }
                ],
                completion_mode="criteria",
                constraints={
                    "chapter_ids": ["2.1"],
                    "block_on_missing_materials": False,
                },
            )
            self.assertGreater(len(open_block_issues(root)), 0)

            def fake_invoke(name, args=None, **kwargs):
                return _tool(
                    name,
                    ok=True,
                    outcome="partial_completed",
                    summary=f"{name} partial",
                )

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                result = run_supervisor_turn(
                    "继续上一个任务",
                    root=root,
                    use_llm=False,
                    confirmed_tools=["rewrite_chapters", "review_chapters"],
                    max_steps=6,
                )
            goal2 = load_goal(root) or goal
            self.assertNotEqual(goal2.get("status"), "succeeded")
            self.assertNotEqual(result.get("terminal_status"), "succeeded")
            self.assertIn(
                goal2.get("status"),
                {"in_progress", "blocked_human", "pending", "awaiting_confirmation"},
            )
            self.assertGreater(len(open_block_issues(root)), 0)

    def test_05_validate_transition_requires_evaluation_for_succeeded(self) -> None:
        self.assertFalse(
            validate_goal_transition("in_progress", "succeeded", context={})
        )
        self.assertFalse(
            validate_goal_transition(
                "blocked_human",
                "succeeded",
                context={"goal_success_evaluation": True, "materials_revalidated": False},
            )
        )
        self.assertTrue(
            validate_goal_transition(
                "in_progress",
                "succeeded",
                context={"goal_success_evaluation": True},
            )
        )
        self.assertTrue(
            validate_goal_transition(
                "blocked_human",
                "succeeded",
                context={
                    "goal_success_evaluation": True,
                    "materials_revalidated": True,
                    "issues_revalidated": True,
                },
            )
        )

    def test_06_tool_result_outcome_fields(self) -> None:
        r = ToolResult(
            ok=True,
            tool="rewrite_chapters",
            args={},
            started_at="",
            ended_at="",
            outcome="partial_completed",
            summary="rewrote 1 chapter",
            affected_items=["4.1"],
        )
        d = r.to_dict()
        self.assertEqual(d["outcome"], "partial_completed")
        self.assertEqual(d["affected_items"], ["4.1"])
        self.assertTrue(d["ok"])
        self.assertTrue(r.step_done())

        blocked = ToolResult(
            ok=True,
            tool="run_pipeline_remaining",
            args={},
            started_at="",
            ended_at="",
            outcome="blocked",
            summary="materials missing",
        )
        self.assertFalse(blocked.step_done())
        self.assertEqual(blocked.outcome, "blocked")

        failed = ToolResult(
            ok=False,
            tool="x",
            args={},
            started_at="",
            ended_at="",
            error=ToolError(code="runner_failed", message="boom"),
        )
        self.assertEqual(failed.outcome, "failed")
        self.assertFalse(failed.step_done())


if __name__ == "__main__":
    unittest.main()
