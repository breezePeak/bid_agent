from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from context_selector import (  # noqa: E402
    ContextSelectionBatchError,
    context_output_valid_for_job,
    load_context_selection_checkpoint,
    reconcile_interrupted_context_selection,
    select_contexts_for_jobs,
)
from graph.state_recorder import record_agent_run_artifact  # noqa: E402
from utils import write_json  # noqa: E402


def _shared() -> dict:
    return {
        "tender_chunks": [],
        "company_chunks": [],
        "score_points": [],
        "global_facts": {},
        "template_evidence": {},
        "prompt": "prompt",
        "shared_input_fingerprint": "shared-v1",
    }


class ContextSelectorBatchTests(unittest.TestCase):
    def _activity_patches(self):
        return (
            patch("agent.activity.begin_phase", return_value={}),
            patch("agent.activity.end_phase", return_value={}),
            patch("agent.activity.mark_agent", return_value={}),
            patch("agent.activity.set_context_selection_progress", return_value={}),
        )

    def test_batch_runs_in_parallel_and_preserves_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [{"chapter_id": f"{i:02d}"} for i in range(1, 5)]
            active = 0
            peak = 0
            lock = threading.Lock()

            def fake_select(job, selected_root, *, shared_inputs=None):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.04)
                chapter_id = job["chapter_id"]
                path = selected_root / "workspace" / "contexts" / f"{chapter_id}_context.json"
                write_json(path, {"chapter_id": chapter_id})
                with lock:
                    active -= 1
                return path

            activity_patches = self._activity_patches()
            with (
                patch("context_selector._load_shared_inputs", return_value=_shared()),
                patch("context_selector.context_output_valid_for_job", return_value=False),
                patch("context_selector.select_context_for_job", side_effect=fake_select),
                activity_patches[0],
                activity_patches[1],
                activity_patches[2],
                activity_patches[3],
            ):
                paths = select_contexts_for_jobs(jobs, root, workers=2)

            self.assertEqual([path.stem for path in paths], [f"{i:02d}_context" for i in range(1, 5)])
            self.assertEqual(peak, 2)
            checkpoint = load_context_selection_checkpoint(root)
            self.assertEqual(checkpoint["status"], "completed")
            self.assertEqual(checkpoint["completed_chapter_ids"], ["01", "02", "03", "04"])

    def test_duplicate_or_empty_chapter_ids_are_rejected_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "重复"):
                select_contexts_for_jobs(
                    [{"chapter_id": "01"}, {"chapter_id": "01"}],
                    root,
                )
            with self.assertRaisesRegex(ValueError, "空"):
                select_contexts_for_jobs([{"chapter_id": ""}], root)

    def test_resume_dispatches_only_invalid_or_missing_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [{"chapter_id": "01"}, {"chapter_id": "02"}, {"chapter_id": "03"}]
            existing = root / "workspace" / "contexts" / "01_context.json"
            write_json(existing, {"chapter_id": "01"})
            called: list[str] = []

            def is_valid(job, _root, *, shared_inputs=None, **_kwargs):
                return job["chapter_id"] == "01"

            def fake_select(job, selected_root, *, shared_inputs=None):
                chapter_id = job["chapter_id"]
                called.append(chapter_id)
                path = selected_root / "workspace" / "contexts" / f"{chapter_id}_context.json"
                write_json(path, {"chapter_id": chapter_id})
                return path

            activity_patches = self._activity_patches()
            with (
                patch("context_selector._load_shared_inputs", return_value=_shared()),
                patch("context_selector.context_output_valid_for_job", side_effect=is_valid),
                patch("context_selector.select_context_for_job", side_effect=fake_select),
                activity_patches[0],
                activity_patches[1],
                activity_patches[2],
                activity_patches[3],
            ):
                paths = select_contexts_for_jobs(jobs, root, workers=3, resume=True)

            self.assertCountEqual(called, ["02", "03"])
            self.assertEqual([path.stem for path in paths], ["01_context", "02_context", "03_context"])

    def test_resume_migrates_legacy_context_without_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"chapter_id": "01", "title": "历史章节"}
            context_path = root / "workspace" / "contexts" / "01_context.json"
            write_json(
                context_path,
                {
                    "chapter_id": "01",
                    "selected_tender_chunks": [],
                    "selected_company_chunks": [],
                },
            )

            activity_patches = self._activity_patches()
            with (
                patch("context_selector._load_shared_inputs", return_value=_shared()),
                patch("context_selector.context_input_fingerprint", return_value="current-fingerprint"),
                patch("context_selector.select_context_for_job") as select_mock,
                activity_patches[0],
                activity_patches[1],
                activity_patches[2],
                activity_patches[3],
            ):
                paths = select_contexts_for_jobs([job], root, resume=True)

            select_mock.assert_not_called()
            self.assertEqual([path.resolve() for path in paths], [context_path.resolve()])
            migrated = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["selection_meta"]["input_fingerprint"],
                "current-fingerprint",
            )
            self.assertEqual(
                migrated["selection_meta"]["fingerprint_source"],
                "legacy_context_baseline",
            )
            self.assertEqual(migrated["warnings"], [])
            checkpoint = load_context_selection_checkpoint(root)
            self.assertEqual(checkpoint["status"], "completed")
            self.assertEqual(checkpoint["migrated_legacy_chapter_ids"], ["01"])

    def test_partial_failure_keeps_success_and_records_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [{"chapter_id": "01"}, {"chapter_id": "02"}]

            def fake_select(job, selected_root, *, shared_inputs=None):
                if job["chapter_id"] == "02":
                    raise RuntimeError("model unavailable")
                path = selected_root / "workspace" / "contexts" / "01_context.json"
                write_json(path, {"chapter_id": "01"})
                return path

            activity_patches = self._activity_patches()
            with (
                patch("context_selector._load_shared_inputs", return_value=_shared()),
                patch("context_selector.context_output_valid_for_job", return_value=False),
                patch("context_selector.select_context_for_job", side_effect=fake_select),
                activity_patches[0],
                activity_patches[1],
                activity_patches[2],
                activity_patches[3],
            ):
                with self.assertRaises(ContextSelectionBatchError) as raised:
                    select_contexts_for_jobs(jobs, root, workers=2)

            self.assertEqual(raised.exception.completed, ["01"])
            self.assertTrue((root / "workspace" / "contexts" / "01_context.json").exists())
            checkpoint = load_context_selection_checkpoint(root)
            self.assertEqual(checkpoint["status"], "partial_failed")
            self.assertEqual(checkpoint["completed_chapter_ids"], ["01"])
            self.assertEqual(checkpoint["failed"][0]["chapter_id"], "02")

    def test_reconcile_marks_running_checkpoint_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace" / "contexts" / "select_contexts_checkpoint.json"
            write_json(
                path,
                {
                    "batch_id": "batch-1",
                    "status": "running",
                    "expected_chapter_ids": ["01", "02"],
                    "completed_chapter_ids": ["01"],
                    "failed": [],
                },
            )
            with patch("agent.activity.set_context_selection_progress", return_value={}):
                checkpoint = reconcile_interrupted_context_selection(root)

            self.assertEqual(checkpoint["status"], "interrupted")
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "interrupted")
            self.assertEqual(persisted["completed_chapter_ids"], ["01"])

    def test_changed_input_fingerprint_invalidates_completed_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context_path = root / "workspace" / "contexts" / "01_context.json"
            write_json(
                context_path,
                {
                    "chapter_id": "01",
                    "selected_tender_chunks": [],
                    "selected_company_chunks": [],
                    "warnings": [],
                    "selection_meta": {"input_fingerprint": "old"},
                },
            )
            write_json(
                root / "workspace" / "contexts" / "select_contexts_checkpoint.json",
                {"status": "interrupted", "expected_chapter_ids": ["01"]},
            )
            with patch("context_selector.context_input_fingerprint", return_value="new"):
                self.assertFalse(
                    context_output_valid_for_job(
                        {"chapter_id": "01"},
                        root,
                        shared_inputs=_shared(),
                    )
                )

    def test_concurrent_agent_metrics_do_not_drop_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "agent_name": "chapter_context_selector",
                "llm_calls": 1,
                "input_tokens_est": 10,
                "output_tokens_est": 5,
                "duration_ms": 20,
            }
            threads = [
                threading.Thread(
                    target=record_agent_run_artifact,
                    args=(root, "select_contexts", payload),
                    kwargs={
                        "artifact_path": root / "workspace" / "agent_runs" / f"{i}.json",
                        "chapter_id": f"{i:02d}",
                    },
                )
                for i in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(
                [thread for thread in threads if thread.is_alive()],
                "并发指标写入线程未在超时内完成",
            )

            metrics = json.loads(
                (root / "workspace" / "run_metrics.json").read_text(encoding="utf-8")
            )
            stage = metrics["stages"]["select_contexts"]
            self.assertEqual(stage["llm_calls"], 8)
            self.assertEqual(len(stage["agent_runs"]), 8)


if __name__ == "__main__":
    unittest.main()
