from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class WebStatusContractTests(unittest.TestCase):
    def test_successful_agent_progress_clears_stale_retry_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "workspace" / "run_state.json",
                {
                    "stage": "select-context-all",
                    "status": "retrying",
                    "message": "自动修复后重试 select-context-all（2/2）",
                    "updated_at": "2026-07-10T14:58:39",
                    "summary": {},
                },
            )
            _write_json(
                root / "workspace" / "pipeline_control.json",
                {"status": "running", "current_stage": "select-context-all"},
            )
            _write_json(
                root / "workspace" / "recovery_state.json",
                {
                    "command": "select-context-all",
                    "attempt": 2,
                    "max_attempts": 2,
                    "updated_at": "2026-07-10T14:58:33",
                },
            )
            (root / "workspace" / "run_events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-07-10T14:59:15",
                        "stage": "select_contexts",
                        "event_type": "agent_artifact",
                        "metrics": {"llm_calls": 1},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "run-1"
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            web_app.CURRENT_TASK = ""

            status = web_app.api_status()

            self.assertTrue(status["recovery_resolved"])
            self.assertEqual(status["run_state"]["status"], "running")
            self.assertNotIn("recovery", status)
            step = next(item for item in status["workflow"] if item["command"] == "select-context-all")
            self.assertEqual(step["state"], "running")

    def test_status_and_step_detail_include_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "workspace" / "run_state.json", {"stage": "select_contexts", "status": "ok", "message": "done", "updated_at": "2026-07-01T10:00:00", "summary": {}})
            _write_json(root / "workspace" / "run_metrics.json", {"run_id": "run_1", "stages": {"select_contexts": {"attempts": 1, "duration_ms": 1200, "llm_calls": 1, "input_tokens_est": 200, "output_tokens_est": 80, "agent_runs": []}}})
            (root / "workspace" / "run_events.jsonl").write_text(
                json.dumps({"ts": "2026-07-01T10:00:00", "stage": "select_contexts", "event_type": "success", "message": "ok"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _write_json(root / "workspace" / "project_profile.json", {"project_type": "software_project", "updated_at": "2026-07-01T10:00:00"})
            _write_json(root / "workspace" / "template_evidence_map.json", {"summary": {}, "items": []})
            _write_json(
                root / "workspace" / "agent_runs" / "select_contexts__chapter_context_selector__01.json",
                {
                    "stage": "select_contexts",
                    "agent_name": "chapter_context_selector",
                    "chapter_id": "01",
                    "prompt_file": "select_context.software_project.md",
                    "prompt_version": "1.0.0+software_project",
                    "prompt_checksum": "abc123456789",
                    "project_type": "software_project",
                    "context_budget": {"max_context_chars": 18000, "max_chunks": 30},
                    "input_summary": {"chapter_id": "01", "tender_candidates": 2},
                    "output_summary": {},
                    "model": "test-model",
                    "temperature": 0.1,
                    "duration_ms": 300,
                    "llm_calls": 1,
                    "input_tokens_est": 120,
                    "output_tokens_est": 40,
                },
            )
            _write_json(
                root / "workspace" / "contexts" / "01_context.json",
                {
                    "chapter_id": "01",
                    "selected_tender_chunks": [{"id": "T-1"}],
                    "selected_company_chunks": [{"id": "C-1"}],
                    "warnings": [],
                    "selection_meta": {
                        "tender_candidates_total": 5,
                        "company_candidates_total": 3,
                        "tender_candidates_in_prompt": 2,
                        "company_candidates_in_prompt": 1,
                        "max_context_chars": 18000,
                        "max_chunks_per_side": 30,
                        "dropped_reason": "budget_trimmed",
                    },
                },
            )

            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "run_1"
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            web_app.CURRENT_RUN_ID = ""
            web_app.CURRENT_TASK = ""

            status = web_app.api_status()
            for key in ("workflow", "run_state", "run_metrics", "run_events_tail", "next_step", "blocked_step"):
                self.assertIn(key, status)
            self.assertIn("manual_review_summary", status)
            self.assertIn("project_profile", status)
            self.assertIn("latest_agent_runs", status)
            self.assertIn("select_contexts", status["run_metrics"])
            self.assertIn("ts", status["run_events_tail"][0])
            self.assertIn("stage", status["run_events_tail"][0])
            self.assertIn("event_type", status["run_events_tail"][0])
            self.assertIn("message", status["run_events_tail"][0])

            detail = json.loads(web_app.api_workflow_step_detail("select-context-all").body.decode("utf-8"))
            self.assertTrue(detail["ok"])
            for key in ("summary", "requires", "produces", "history", "agent_runs", "stage_metrics", "budget_hits"):
                self.assertIn(key, detail)

    def test_chat_reply_supports_status_and_step_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "workspace" / "run_state.json", {"stage": "parse-score", "status": "ok", "message": "done", "updated_at": "2026-07-01T10:00:00", "summary": {}})
            (root / "inputs").mkdir(parents=True, exist_ok=True)
            (root / "inputs" / "tender.md").write_text("招标文件", encoding="utf-8")
            (root / "inputs" / "company.md").write_text("公司资料", encoding="utf-8")
            (root / "inputs" / "score.md").write_text("评分标准", encoding="utf-8")
            _write_json(root / "workspace" / "score_points.json", [{"id": "S1", "title": "实施能力", "score": 10, "category": "技术", "requirement": "覆盖实施能力"}])
            _write_json(root / "workspace" / "score_requirements.json", [{"title": "实施能力", "score": 10, "requirement": "覆盖实施能力"}])
            _write_json(root / "workspace" / "global_facts.json", {"project_name": "测试项目"})
            _write_json(root / "workspace" / "template_evidence_map.json", {"summary": {}, "items": []})
            _write_json(root / "workspace" / "run_metrics.json", {"run_id": "run_1", "stages": {}})

            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "run_1"
            web_app.RUNNING = False
            web_app.CURRENT_RUN_ROOT = None
            web_app.CURRENT_RUN_ID = ""
            web_app.CURRENT_TASK = ""

            next_reply = web_app._chat_reply(root, "继续执行下一步")
            self.assertIn("下一步", next_reply["reply"])
            self.assertTrue(next_reply["actions"])

            parse_reply = web_app._chat_reply(root, "解析评分结果是什么")
            self.assertIn("解析评分", parse_reply["reply"])
            self.assertTrue(parse_reply["actions"])


if __name__ == "__main__":
    unittest.main()
