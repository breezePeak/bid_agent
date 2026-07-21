from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.flags import agent_supervisor_enabled
from agent.policy import evaluate_tool_call, is_readonly_tool
from agent.supervisor import plan_with_supervisor, run_supervisor_turn
from agent.tool_registry import reset_tool_index
from agent.trace import load_decisions


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_readonly_allowed(self) -> None:
        decision = evaluate_tool_call("query_status", {})
        self.assertTrue(decision.allow)
        self.assertTrue(is_readonly_tool("query_status"))

    def test_mutation_needs_confirm(self) -> None:
        decision = evaluate_tool_call("run_stage", {"command": "parse-score"}, auto_execute=False)
        self.assertFalse(decision.allow)
        self.assertTrue(decision.ask_human)

    def test_mutation_allowed_after_user_confirm(self) -> None:
        decision = evaluate_tool_call(
            "run_stage",
            {"command": "parse-score"},
            auto_execute=False,
            user_confirmed=True,
        )
        self.assertTrue(decision.allow)


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_flag_default_on(self) -> None:
        """PR-A3: Supervisor is the default product entry."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_SUPERVISOR_ENABLED", None)
            self.assertTrue(agent_supervisor_enabled())

    def test_rule_based_status_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_supervisor_turn(
                "当前进度怎么样",
                root=root,
                status={},
                history=[],
                use_llm=False,
                max_steps=2,
            )
            self.assertTrue(result.get("supervisor"))
            self.assertTrue(result.get("steps"))
            self.assertIn("进度", result.get("reply", ""))
            decisions = load_decisions(root, tail=5)
            self.assertTrue(decisions)
            self.assertEqual(decisions[-1].get("selected_tool"), "query_status")

    def test_rule_based_diagnose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_supervisor_turn(
                "帮我诊断失败原因",
                root=root,
                use_llm=False,
            )
            self.assertTrue(result["steps"])
            self.assertEqual(result["steps"][0]["tool"], "diagnose_failure")
            self.assertTrue(result["steps"][0]["executed"])

    def test_continue_suggests_confirm_not_auto_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = {
                "next_step": {"command": "parse-score", "label": "解析评分"},
                "workflow": [],
            }
            result = run_supervisor_turn(
                "继续下一步",
                root=root,
                status=status,
                use_llm=False,
            )
            # should not auto-execute mutation without prior goal confirmation scope
            executed_mutate = any(
                s.get("executed") and s.get("tool") == "run_stage" for s in result.get("steps", [])
            )
            self.assertFalse(executed_mutate)
            action_types = {a.get("type") for a in result.get("actions", [])}
            self.assertTrue(action_types & {"run_command", "confirm_tool", "auto_run"})

    def test_confirm_tool_executes_run_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = {
                "next_step": {"command": "parse-score", "label": "解析评分"},
                "workflow": [],
            }
            with mock.patch("agent.tool_runtime.invoke") as mocked_invoke:
                from agent.types import ToolResult
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc).isoformat()
                mocked_invoke.return_value = ToolResult(
                    ok=True,
                    tool="run_stage",
                    args={"command": "parse-score"},
                    started_at=now,
                    ended_at=now,
                    summary_for_llm="parse-score done",
                )
                result = run_supervisor_turn(
                    "确认执行 parse-score",
                    root=root,
                    status=status,
                    use_llm=False,
                    user_confirmed=True,
                    confirmed_tools=["run_stage"],
                )
            executed = any(
                s.get("executed") and s.get("tool") == "run_stage" for s in result.get("steps", [])
            )
            self.assertTrue(executed)

    def test_plan_with_supervisor_respects_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR_ENABLED": "false"}):
                self.assertIsNone(plan_with_supervisor("状态", [], {}, llm_chat=None))
            with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR_ENABLED": "true", "BID_AGENT_ROOT": str(root)}):
                # plan_with_supervisor uses project_root; set env root
                with mock.patch("agent.supervisor.project_root", return_value=root):
                    with mock.patch("agent.supervisor.run_supervisor_turn") as mocked:
                        mocked.return_value = {
                            "reply": "ok",
                            "actions": [],
                            "steps": [{"tool": "query_status"}],
                            "goal_id": "abc",
                            "intent": "supervisor",
                        }
                        plan = plan_with_supervisor("状态", [], {"next_step": None})
                        self.assertIsNotNone(plan)
                        assert plan is not None
                        self.assertTrue(plan.get("supervisor"))
                        self.assertEqual(plan.get("action"), "chat")


class OrchestratorIntegrationTests(unittest.TestCase):

    def test_orchestrator_uses_supervisor_when_flag_on(self) -> None:
        from session_orchestrator import plan

        supervised = {
            "intent": "supervisor",
            "action": "chat",
            "reply": "进度 0/20",
            "actions": [],
            "supervisor_steps": [{"tool": "query_status"}],
            "goal_id": "g1",
            "supervisor": True,
            "auto_execute": False,
        }
        with mock.patch("agent.flags.agent_supervisor_enabled", return_value=True):
            with mock.patch("agent.supervisor.plan_with_supervisor", return_value=supervised):
                result = plan(
                    "当前状态",
                    [],
                    {
                        "next_step": None,
                        "workflow": [],
                        "run_state": {},
                        "sources": {},
                        "inputs": {},
                        "outputs": {},
                        "manual_review_summary": {},
                    },
                    llm_chat=lambda messages, temperature=0.1: "should-not-be-called",
                )
        self.assertTrue(result.get("supervisor"))
        self.assertEqual(result.get("reply"), "进度 0/20")
        self.assertEqual(result.get("goal_id"), "g1")

    def test_legacy_plan_when_flag_off(self) -> None:
        from session_orchestrator import plan

        with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR_ENABLED": "0"}):
            # force fallback without LLM
            result = plan(
                "当前状态",
                [],
                {
                    "next_step": {"command": "parse-score", "label": "解析评分"},
                    "workflow": [],
                    "run_state": {},
                    "sources": {},
                    "inputs": {},
                    "outputs": {},
                    "manual_review_summary": {},
                },
                llm_chat=lambda messages, temperature=0.1: "not-json",
            )
            # legacy fallback path
            self.assertIn(result.get("action"), {"chat", "query", "run_command", "auto_run"})
            self.assertFalse(result.get("supervisor"))


if __name__ == "__main__":
    unittest.main()
