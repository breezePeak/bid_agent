from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app  # noqa: E402
from agent.repair_jobs import (  # noqa: E402
    claim_repair_job,
    claim_repair_job_authorized,
    create_authorized_repair_job,
    create_confirmation,
    load_repair_job,
    load_v2_repair_job,
    reconcile_interrupted_repair,
    update_repair_job,
)
from agent.tool_runtime import invoke  # noqa: E402
from chat_store import close_chat_store, load_messages  # noqa: E402


class _Request:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


def _json_response(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class RepairJobPersistenceTests(unittest.TestCase):
    def test_v1_repair_job_file_is_imported_once_then_sqlite_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace" / "repair_job.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"job_id": "repair-legacy", "status": "running", "phase": "repairing"}),
                encoding="utf-8",
            )
            self.assertEqual(load_repair_job(root)["status"], "running")
            path.write_text(
                json.dumps({"job_id": "repair-legacy", "status": "completed", "phase": "complete"}),
                encoding="utf-8",
            )
            current = load_repair_job(root)
            self.assertEqual(current["status"], "running")
            self.assertEqual(current["phase"], "repairing")

    def test_v2_repair_read_does_not_import_legacy_job_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace" / "repair_job.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"job_id": "repair-legacy", "status": "awaiting_confirmation"}),
                encoding="utf-8",
            )

            self.assertEqual(load_v2_repair_job(root), {})
            store = web_app.ControlStore(web_app.WorkspaceContext.resolve(root.parent, root.name))
            self.assertTrue(store.v1_import_pending("repair_job"))
            self.assertEqual(store.revision(), 0)

    def test_confirmation_and_duplicate_claim_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = create_confirmation(
                root,
                issue_fingerprints=["b", "a"],
                total_count=2,
                auto_count=1,
                manual_count=1,
                resume_command="build-md",
            )
            repeated = create_confirmation(
                root,
                issue_fingerprints=["a", "b"],
                total_count=2,
                auto_count=1,
                manual_count=1,
                resume_command="build-md",
            )
            self.assertEqual(first["job_id"], repeated["job_id"])
            self.assertEqual(first["confirmation_id"], repeated["confirmation_id"])
            self.assertEqual(load_repair_job(root)["status"], "awaiting_confirmation")
            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "repair-status"
            status = web_app.api_status()
            self.assertEqual(status["repair_job"]["job_id"], first["job_id"])
            self.assertEqual(status["pending_confirmation"]["confirmation_id"], first["confirmation_id"])
            current = _json_response(web_app.api_current_repair_job())
            self.assertEqual(current["repair_job"]["job_id"], first["job_id"])

            claimed = claim_repair_job(root, first["confirmation_id"])
            duplicate = claim_repair_job(root, first["confirmation_id"])
            self.assertTrue(claimed["ok"])
            self.assertFalse(claimed["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["job"]["job_id"], first["job_id"])

    def test_v2_authorized_claim_does_not_require_legacy_confirmation_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = create_confirmation(
                root,
                issue_fingerprints=["v2-issue"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            claimed = claim_repair_job_authorized(root, "operation-123")
            self.assertTrue(claimed["ok"])
            self.assertFalse(claimed["duplicate"])
            self.assertEqual(claimed["job"]["job_id"], job["job_id"])
            self.assertEqual(claimed["job"]["authorized_by_operation"], "operation-123")
            self.assertEqual(claimed["job"]["status"], "running")

    def test_v2_authorized_job_has_no_legacy_confirmation_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = create_authorized_repair_job(
                root,
                operation_id="operation-456",
                issue_fingerprints=["v2-issue"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            self.assertEqual(job["confirmation_id"], "")
            self.assertEqual(job["status"], "awaiting_v2_operation")
            claimed = claim_repair_job_authorized(root, "operation-456")
            self.assertTrue(claimed["ok"])
            self.assertEqual(claimed["job"]["status"], "running")

    def test_interrupted_worker_is_persisted_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = create_confirmation(
                root,
                issue_fingerprints=["x"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            claim_repair_job(root, job["confirmation_id"])
            reconciled = reconcile_interrupted_repair(root)
            self.assertEqual(reconciled["status"], "failed")
            self.assertEqual(reconciled["phase"], "interrupted")

    def test_terminal_duplicate_returns_same_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = create_confirmation(
                root,
                issue_fingerprints=["x"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            claim_repair_job(root, job["confirmation_id"])
            update_repair_job(root, job["job_id"], status="completed", phase="complete")
            # Terminal jobs must NOT claim as silent duplicate — force remint path
            stale = claim_repair_job(root, job["confirmation_id"])
            self.assertFalse(stale.get("ok"))
            self.assertTrue(stale.get("stale") or "结束" in str(stale.get("message") or "") or "中断" in str(stale.get("message") or ""))


class RepairIntentTests(unittest.TestCase):
    def test_repair_synonyms_are_deterministic(self) -> None:
        for text in (
            "自动修复",
            "最小修复",
            "修复啊",
            "帮我修复这些阻断",
            "处理这些阻断问题",
            "继续修复",
            "重新发起最小修复",
            "重新修复",
            "重试修复",
        ):
            with self.subTest(text=text):
                self.assertEqual(web_app._minimal_repair_intent(text, has_pending=False), "start")

    def test_query_and_negative_expressions_do_not_start(self) -> None:
        for text in ("自动修复会改什么", "怎么修", "查看修复计划", "为什么失败"):
            with self.subTest(text=text):
                self.assertEqual(web_app._minimal_repair_intent(text, has_pending=True), "query")
        for text in ("暂不修复", "不用修复", "不要自动修复", "好，先不修复", "取消"):
            with self.subTest(text=text):
                self.assertEqual(web_app._minimal_repair_intent(text, has_pending=True), "decline")

    def test_bare_yes_requires_pending_confirmation(self) -> None:
        self.assertEqual(web_app._minimal_repair_intent("是", has_pending=False), "")
        self.assertEqual(web_app._minimal_repair_intent("是", has_pending=True), "confirm")


class ChatOrchestrateTests(unittest.TestCase):
    def _activate(self, root: Path) -> None:
        web_app.ACTIVE_RUN_ROOT = root
        web_app.ACTIVE_RUN_ID = "run-test"
        web_app.RUNNING = False
        web_app.CURRENT_RUN_ROOT = None
        web_app.CURRENT_RUN_ID = ""
        web_app.CURRENT_TASK = ""

    def test_server_persists_each_turn_before_next_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._activate(root)
            captured: list[list[dict]] = []

            def planner(message, history, _status, **_kwargs):
                captured.append(list(history))
                return {"reply": f"assistant:{message}", "actions": [], "intent": "chat"}

            with mock.patch.object(web_app, "api_status", return_value={}):
                with mock.patch.object(web_app, "_minimal_repair_candidates", return_value=[]):
                    with mock.patch.object(web_app, "orchestrator_plan", side_effect=planner):
                        with mock.patch.object(web_app, "orchestrator_resolve", side_effect=lambda plan, _status: plan):
                            asyncio.run(web_app.api_chat_orchestrate(_Request({"message": "first"})))
                            asyncio.run(web_app.api_chat_orchestrate(_Request({"message": "second"})))

            self.assertEqual(captured[0], [])
            self.assertEqual([item["content"] for item in captured[1]], ["first", "assistant:first"])
            saved = load_messages(root, "run-test")
            self.assertEqual([item["role"] for item in saved], ["user", "assistant", "user", "assistant"])
            close_chat_store(root)


class RepairWorkerTests(unittest.TestCase):
    def _activate(self, root: Path) -> None:
        web_app.ACTIVE_RUN_ROOT = root
        web_app.ACTIVE_RUN_ID = "run-test"
        web_app.RUNNING = False
        web_app.CURRENT_RUN_ROOT = None
        web_app.CURRENT_RUN_ID = ""
        web_app.CURRENT_TASK = ""

    def test_partial_repair_stays_blocked_and_duplicate_does_not_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = create_confirmation(
                root,
                issue_fingerprints=["fingerprint"],
                total_count=1,
                auto_count=1,
                manual_count=0,
                resume_command="build-md",
            )
            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "run-worker"
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None

            result = {
                "ok": False,
                "resolved": [],
                "still_open": ["issue-1"],
                "manual": [],
                "failed": [],
                "message": "still blocked",
            }
            with mock.patch.object(web_app.SUPERVISOR, "load", return_value={"status": "failed"}):
                with mock.patch.object(web_app.SUPERVISOR, "is_running", return_value=False):
                    with mock.patch.object(web_app.SUPERVISOR, "start", return_value=True) as resume:
                        with mock.patch.object(
                            web_app,
                            "_minimal_repair_candidates",
                            return_value=[{"id": "issue-1"}],
                        ):
                            with mock.patch("agent.repair.execute_repair_batch", return_value=result):
                                with mock.patch.object(web_app, "save_message"):
                                    started = web_app._trigger_repair_job(root, job["confirmation_id"])
                                    self.assertTrue(started["ok"])
                                    deadline = time.monotonic() + 2
                                    current = load_repair_job(root)
                                    while current.get("status") not in {"completed", "partial", "failed"}:
                                        self.assertLess(time.monotonic(), deadline)
                                        time.sleep(0.01)
                                        current = load_repair_job(root)
                                    # Wait for worker to clear global RUNNING flag
                                    while web_app.RUNNING and time.monotonic() < deadline:
                                        time.sleep(0.01)
                                    started["_worker_thread"].join(timeout=2)

                                    # Old confirmation is stale after terminal status
                                    stale = claim_repair_job(root, job["confirmation_id"])
                                    self.assertFalse(stale.get("ok"))
                                    self.assertTrue(stale.get("stale"))

            self.assertEqual(current["status"], "partial")
            self.assertEqual(current["remaining_count"], 1)
            self.assertFalse(current["resume_attempted"])
            self.assertEqual(resume.call_count, 0)
            self.assertIn("仍有未关闭问题", current["message"])

    def test_natural_repair_command_bypasses_llm_and_proposes_v2_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._activate(root)
            context = web_app.WorkspaceContext("run-test", root)
            try:
                with mock.patch.object(web_app, "_workspace_context", return_value=context):
                    with mock.patch.object(web_app, "_minimal_repair_candidates", return_value=[{"id": "issue-1"}]):
                        with mock.patch.object(web_app, "orchestrator_plan") as planner:
                            body = _json_response(
                                asyncio.run(web_app.api_chat_orchestrate(_Request({"message": "修复啊"})))
                            )
                planner.assert_not_called()
                self.assertFalse(body["triggered_repair"])
                self.assertEqual(body["intent"], "minimal_repair_confirmation")
                self.assertEqual(body["actions"][0]["type"], "confirm_v2_command")
            finally:
                close_chat_store(root)

    def test_legacy_repair_confirmation_is_translated_to_v2_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._activate(root)
            context = web_app.WorkspaceContext("run-test", root)
            try:
                with mock.patch.object(web_app, "_workspace_context", return_value=context):
                    with mock.patch.object(web_app, "_minimal_repair_candidates", return_value=[{"id": "issue-1"}]):
                        with mock.patch.object(web_app, "orchestrator_plan") as planner:
                            body = _json_response(
                                asyncio.run(
                                    web_app.api_chat_orchestrate(
                                        _Request(
                                            {
                                                "message": "是，执行最小修复",
                                                "action": {
                                                    "type": "confirm_minimal_repair",
                                                    "confirmation_id": "confirm-1",
                                                },
                                            }
                                        )
                                    )
                                )
                            )
                planner.assert_not_called()
                self.assertFalse(body["triggered_repair"])
                self.assertEqual(body["actions"][0]["type"], "confirm_v2_command")
            finally:
                close_chat_store(root)


class RepairActorGateTests(unittest.TestCase):
    def test_repair_actor_only_bypasses_root_action_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = {"can_proceed": False, "message": "blocked"}
            with mock.patch("agent.issues.can_proceed", return_value=blocked):
                pipeline = invoke(
                    "run_stage",
                    {"command": "extract-facts", "force": True},
                    root=root,
                    actor="pipeline",
                )
                repair = invoke(
                    "run_stage",
                    {"command": "extract-facts", "force": True},
                    root=root,
                    actor="repair",
                )
                downstream = invoke(
                    "run_stage",
                    {"command": "build-md", "force": True},
                    root=root,
                    actor="repair",
                )

            self.assertFalse(pipeline.ok)
            self.assertEqual(pipeline.error.code, "gate_blocked")
            self.assertFalse(repair.ok)
            self.assertNotEqual(repair.error.code, "gate_blocked")
            self.assertFalse(downstream.ok)
            self.assertEqual(downstream.error.code, "gate_blocked")


if __name__ == "__main__":
    unittest.main()
