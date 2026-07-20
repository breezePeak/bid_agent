"""Strict Supervisor state-machine acceptance (Stable Beta plan).

No live LLM. No loose assertions (budget_exceeded must not count as success).
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
    build_plan_for_objectives,
    confirmation_allows,
    create_goal,
    evaluate_criteria,
    explicit_resume_intent,
    grant_confirmation,
    handle_plan_step_result,
    infer_goal_from_message,
    load_goal,
    next_plan_step,
    reevaluate_goal,
    resume_goal_after_materials,
    save_goal,
)
from agent.goal_compiler import validate_goal_draft
from agent.supervisor import run_supervisor_turn
from agent.tool_registry import get_tool, reset_tool_index
from agent.types import ToolError, ToolResult


def _ok(name: str, summary: str = "ok", **metrics):
    return ToolResult(
        ok=True,
        tool=name,
        args={},
        started_at="",
        ended_at="",
        summary_for_llm=summary,
        metrics=metrics or {},
    )


def _fail(name: str, summary: str = "fail", *, code: str = "runner_failed", retryable: bool = True):
    return ToolResult(
        ok=False,
        tool=name,
        args={},
        started_at="",
        ended_at="",
        summary_for_llm=summary,
        error=ToolError(code=code, message=summary, retryable=retryable),
    )


class AgentStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    # --- Scene 1 ---
    def test_01_status_query_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[str] = []

            def fake_invoke(name, args=None, **kwargs):
                calls.append(name)
                return _ok(name, "进度 0/20")

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                result = run_supervisor_turn(
                    "当前进度怎么样",
                    root=root,
                    use_llm=False,
                    max_steps=6,
                )
            self.assertEqual(calls.count("query_status"), 1)
            self.assertEqual(result.get("terminal_status"), "succeeded")
            goal = load_goal(root)
            self.assertIsNotNone(goal)
            self.assertEqual(goal.get("status"), "succeeded")
            self.assertNotEqual(result.get("terminal_status"), "budget_exceeded")

    # --- Scene 2 ---
    def test_02_diagnose_once_no_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[str] = []

            def fake_invoke(name, args=None, **kwargs):
                calls.append(name)
                return _ok(name, "诊断完成")

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                result = run_supervisor_turn(
                    "为什么失败了",
                    root=root,
                    use_llm=False,
                    max_steps=6,
                )
            self.assertEqual(calls.count("diagnose_failure"), 1)
            mutation = {
                "fix_coverage",
                "fix_compliance",
                "rewrite_chapters",
                "build_export",
                "run_pipeline_remaining",
                "write_chapters",
            }
            self.assertFalse(mutation.intersection(calls))
            self.assertEqual(result.get("terminal_status"), "succeeded")

    # --- Scene 3 ---
    def test_03_full_generate_no_empty_command(self) -> None:
        plan = build_plan_for_objectives([{"type": "full_generate"}])
        tools = [s.get("tool") for s in plan]
        self.assertIn("run_pipeline_remaining", tools)
        self.assertNotIn("run_stage", tools)
        for step in plan:
            if step.get("tool") == "run_stage":
                self.assertTrue(str((step.get("args") or {}).get("command") or "").strip())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[tuple[str, dict]] = []

            def fake_invoke(name, args=None, **kwargs):
                args = dict(args or {})
                calls.append((name, args))
                if name == "run_pipeline_remaining":
                    return _ok(name, "complete", status="complete")
                if name == "build_export":
                    (root / "outputs").mkdir(exist_ok=True)
                    (root / "outputs" / "final.md").write_text("# ok", encoding="utf-8")
                    (root / "outputs" / "final.docx").write_bytes(b"PK")
                return _ok(name)

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                result = run_supervisor_turn(
                    "一键生成完整标书并导出 Word",
                    root=root,
                    use_llm=False,
                    confirmed_tools=["run_pipeline_remaining", "build_export"],
                    max_steps=10,
                )
            tool_names = [c[0] for c in calls]
            self.assertIn("run_pipeline_remaining", tool_names)
            for name, args in calls:
                if name == "run_stage":
                    self.assertTrue(str(args.get("command") or "").strip(), "empty run_stage command")
            self.assertNotEqual(result.get("terminal_status"), "budget_exceeded")
            self.assertNotEqual(result.get("terminal_status"), "failed")
            # must not fail due to invalid_args
            for step in result.get("steps") or []:
                obs = str(step.get("observation") or "")
                self.assertNotIn("invalid_args", obs)

    # --- Scene 4 ---
    def test_04_dependency_blocks_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = [
                {
                    "step_id": "A",
                    "tool": "query_status",
                    "args": {},
                    "depends_on": [],
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 1,
                    "label": "A",
                },
                {
                    "step_id": "B",
                    "tool": "analyze_coverage",
                    "args": {},
                    "depends_on": ["A"],
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 1,
                    "label": "B",
                },
                {
                    "step_id": "C",
                    "tool": "build_export",
                    "args": {"targets": ["md"]},
                    "depends_on": ["B"],
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 1,
                    "label": "C",
                },
            ]
            goal = create_goal(
                root,
                raw_user_goal="dep test",
                objectives=[{"type": "fix_coverage"}, {"type": "export"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
                plan=plan,
            )
            calls: list[str] = []

            def fake_invoke(name, args=None, **kwargs):
                calls.append(name)
                if name == "analyze_coverage":
                    return _fail(name, "boom", code="runner_failed", retryable=False)
                return _ok(name)

            with mock.patch("agent.supervisor.invoke", side_effect=fake_invoke):
                run_supervisor_turn(
                    "继续上一个任务",
                    root=root,
                    use_llm=False,
                    confirmed_tools=["analyze_coverage", "build_export"],
                    max_steps=6,
                )
            self.assertEqual(calls.count("build_export"), 0)
            goal2 = load_goal(root) or goal
            c_step = next(s for s in goal2["plan"] if s["step_id"] == "C")
            self.assertNotEqual(c_step.get("status"), "done")

    # --- Scene 5 ---
    def test_05_plan_retry_max_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = [
                {
                    "step_id": "fix_coverage",
                    "tool": "fix_coverage",
                    "args": {},
                    "depends_on": [],
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 2,
                    "label": "fix",
                }
            ]
            goal = create_goal(
                root,
                raw_user_goal="retry",
                objectives=[{"type": "fix_coverage"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
                plan=plan,
            )
            # first fail → pending with attempts=1
            goal = mark_running_then_handle(root, goal, "fix_coverage", ok=False, code="runner_failed")
            step = next(s for s in goal["plan"] if s["step_id"] == "fix_coverage")
            self.assertEqual(step.get("status"), "pending")
            self.assertEqual(int(step.get("attempts") or 0), 1)

            # second fail → failed
            goal = mark_running_then_handle(root, goal, "fix_coverage", ok=False, code="runner_failed")
            step = next(s for s in goal["plan"] if s["step_id"] == "fix_coverage")
            self.assertEqual(step.get("status"), "failed")
            self.assertEqual(goal.get("status"), "failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = [
                {
                    "step_id": "fix_coverage",
                    "tool": "fix_coverage",
                    "args": {},
                    "depends_on": [],
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 2,
                    "label": "fix",
                }
            ]
            goal = create_goal(
                root,
                raw_user_goal="retry ok",
                objectives=[{"type": "fix_coverage"}],
                success_criteria=[{"check": "score_coverage_min", "ratio": 0.95}],
                plan=plan,
            )
            goal = mark_running_then_handle(root, goal, "fix_coverage", ok=False, code="timeout")
            goal = mark_running_then_handle(root, goal, "fix_coverage", ok=True)
            step = next(s for s in goal["plan"] if s["step_id"] == "fix_coverage")
            self.assertEqual(step.get("status"), "done")
            self.assertEqual(int(step.get("attempts") or 0), 2)

    # --- Scene 6 ---
    def test_06_confirmation_scope_not_broad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="export",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.docx"}],
            )
            goal = grant_confirmation(root, tools=["build_export"], all_mutations=False)
            self.assertTrue(confirmation_allows(goal, "build_export"))
            self.assertFalse(confirmation_allows(goal, "rewrite_chapters"))
            self.assertFalse(confirmation_allows(goal, "fix_compliance"))
            # all_mutations=True with tools list must still stay scoped
            goal = grant_confirmation(root, tools=["build_export"], all_mutations=True)
            self.assertFalse(confirmation_allows(goal, "rewrite_chapters"))
            scope = goal.get("confirmation_scope") or {}
            self.assertFalse(bool(scope.get("all_mutations")))

    # --- Scene 7 ---
    def test_07_partial_materials_reblock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            # materials checklist with A and B missing
            checklist = {
                "items": [
                    {
                        "item_id": "A",
                        "requirement": "材料A",
                        "severity": "block",
                        "response_status": "missing",
                        "evidence_status": "missing",
                    },
                    {
                        "item_id": "B",
                        "requirement": "材料B",
                        "severity": "block",
                        "response_status": "missing",
                        "evidence_status": "missing",
                    },
                ]
            }
            (ws / "materials_checklist.json").write_text(
                json.dumps(checklist, ensure_ascii=False), encoding="utf-8"
            )
            goal = create_goal(
                root,
                raw_user_goal="一键生成",
                objectives=[{"type": "full_generate"}],
                success_criteria=[
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                ],
            )
            goal = reevaluate_goal(root, goal)
            # force blocked if not already
            if goal.get("status") != "blocked_human":
                from agent.goal import set_goal_status

                set_goal_status(
                    root,
                    "blocked_human",
                    blocked_reason="缺少不可自动补齐的材料: 材料A；材料B",
                    goal=goal,
                )
                goal = load_goal(root)

            # verify A only
            checklist["items"][0]["response_status"] = "provided"
            checklist["items"][0]["evidence_status"] = "verified"
            (ws / "materials_checklist.json").write_text(
                json.dumps(checklist, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch(
                "agent.goal.detect_human_block",
                return_value="缺少不可自动补齐的材料: 材料B",
            ):
                goal = resume_goal_after_materials(root, note="A verified", item_ids=["A"])
                # resume_context is one-shot; second evaluate must re-block on B only
                goal = reevaluate_goal(root, goal)
            self.assertEqual(goal.get("status"), "blocked_human")
            reason = str(goal.get("blocked_reason") or "")
            self.assertIn("B", reason)
            self.assertNotIn("材料A", reason)

    # --- Scene 8 ---
    def test_08_budget_exceeded_new_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = create_goal(
                root,
                raw_user_goal="old",
                objectives=[{"type": "full_generate"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.docx"}],
            )
            from agent.goal import set_goal_status

            set_goal_status(root, "budget_exceeded", blocked_reason="熔断", goal=old)
            old_id = old["goal_id"]

            with mock.patch("agent.supervisor.invoke", return_value=_ok("query_status")):
                result = run_supervisor_turn(
                    "当前进度怎么样",
                    root=root,
                    use_llm=False,
                    max_steps=4,
                )
            new_goal = load_goal(root)
            self.assertIsNotNone(new_goal)
            self.assertNotEqual(new_goal.get("goal_id"), old_id)
            types = [o.get("type") for o in (new_goal.get("normalized_objectives") or [])]
            self.assertIn("status", types)
            self.assertEqual(result.get("terminal_status"), "succeeded")

    # --- Scene 9 ---
    def test_09_explicit_resume_keeps_goal(self) -> None:
        self.assertTrue(explicit_resume_intent("继续上一个任务"))
        self.assertFalse(explicit_resume_intent("当前进度怎么样"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = create_goal(
                root,
                raw_user_goal="生成",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.docx"}],
            )
            from agent.goal import set_goal_status

            set_goal_status(root, "awaiting_confirmation", goal=goal)
            old_id = goal["goal_id"]

            with mock.patch("agent.supervisor.invoke", return_value=_ok("build_export")):
                run_supervisor_turn(
                    "继续上一个任务",
                    root=root,
                    use_llm=False,
                    confirmed_tools=["build_export"],
                    max_steps=4,
                )
            g2 = load_goal(root)
            self.assertEqual(g2.get("goal_id"), old_id)

    # --- Scene 10 ---
    def test_10_progress_after_issue_fix(self) -> None:
        from agent.budgets import AgentBudget, issues_fingerprint

        budget = AgentBudget(max_steps=5, max_no_progress_steps=3, max_same_tool_streak=5)
        budget.last_issues_fp = issues_fingerprint([{"code": "X", "id": "1", "status": "open"}], [])
        budget.record_step(
            tool="repair_issue",
            args={},
            observation="fixed",
            criteria_fp="aaa",
            issues_fp=issues_fingerprint([], []),
            executed=True,
            ok=True,
        )
        self.assertEqual(budget.no_progress_steps, 0)

    # --- Scene 11 ---
    def test_11_compiler_check_names(self) -> None:
        ok, reason, normalized = validate_goal_draft(
            {
                "objectives": [{"type": "export"}],
                "success_criteria": [
                    {"check": "export_preflight_ok"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                ],
            }
        )
        self.assertTrue(ok, reason)
        checks = [c.get("check") for c in normalized.get("success_criteria") or []]
        allowed = {
            "artifact_exists",
            "stage_ready",
            "no_stale",
            "score_coverage_min",
            "no_open_blocks",
            "chapters_written",
            "export_preflight",
        }
        for c in checks:
            self.assertIn(c, allowed)
        # evaluator accepts alias
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = evaluate_criteria(root, [{"check": "export_preflight_ok"}])
            self.assertEqual(results[0].get("check"), "export_preflight")
            self.assertNotEqual(results[0].get("detail"), "unsupported_check")

    # --- Scene 12 ---
    def test_12_no_loose_assertions(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        # forbid tautology pass-through like: assert x or <True literal>
        banned = " or " + "True"
        self.assertNotIn(banned, src)
        self.assertIn("run_pipeline_remaining", src)

    def test_tool_registered(self) -> None:
        self.assertIsNotNone(get_tool("run_pipeline_remaining"))

    def test_infer_full_generate_plan(self) -> None:
        g = infer_goal_from_message("一键生成完整标书")
        tools = [s.get("tool") for s in g.get("plan") or []]
        self.assertIn("run_pipeline_remaining", tools)
        self.assertEqual(g.get("completion_mode"), "criteria")


def mark_running_then_handle(root, goal, step_id, *, ok: bool, code: str = ""):
    from agent.goal import mark_plan_step

    goal = mark_plan_step(root, step_id, status="running", goal=goal)
    return handle_plan_step_result(
        root,
        goal,
        step_id,
        ok=ok,
        error="err" if not ok else "",
        error_code=code if not ok else "",
        retryable=True if not ok else None,
    )


if __name__ == "__main__":
    unittest.main()
