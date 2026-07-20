from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from concurrency import (
    chapter_workers_scope,
    clamp_workers,
    concurrency_snapshot,
    is_load_shedding,
    llm_concurrency_limit,
    llm_slot,
    note_rate_limit_429,
    reset_for_tests,
    workers_default,
    workers_max,
)


class ConcurrencyConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            k: os.environ.get(k)
            for k in (
                "BID_AGENT_WORKERS_DEFAULT",
                "BID_AGENT_WORKERS_MAX",
                "BID_AGENT_LLM_CONCURRENCY",
            )
        }
        reset_for_tests()

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_for_tests()

    def test_defaults_are_clamped(self) -> None:
        os.environ.pop("BID_AGENT_WORKERS_DEFAULT", None)
        os.environ.pop("BID_AGENT_WORKERS_MAX", None)
        self.assertEqual(workers_max(), 10)
        self.assertEqual(workers_default(), 4)
        self.assertEqual(clamp_workers(None), 4)
        self.assertEqual(clamp_workers(1), 1)
        self.assertEqual(clamp_workers(10), 10)
        self.assertEqual(clamp_workers(999), 10)
        self.assertEqual(clamp_workers(0), 1)
        self.assertEqual(clamp_workers(-3), 1)

    def test_single_worker(self) -> None:
        os.environ["BID_AGENT_WORKERS_DEFAULT"] = "1"
        os.environ["BID_AGENT_WORKERS_MAX"] = "1"
        self.assertEqual(workers_default(), 1)
        self.assertEqual(clamp_workers(5), 1)
        self.assertEqual(clamp_workers(None), 1)

    def test_default_within_max(self) -> None:
        os.environ["BID_AGENT_WORKERS_DEFAULT"] = "8"
        os.environ["BID_AGENT_WORKERS_MAX"] = "6"
        self.assertEqual(workers_max(), 6)
        self.assertEqual(workers_default(), 6)
        self.assertEqual(clamp_workers(100), 6)

    def test_max_concurrency_cannot_be_bypassed(self) -> None:
        os.environ["BID_AGENT_WORKERS_MAX"] = "3"
        os.environ["BID_AGENT_WORKERS_DEFAULT"] = "2"
        for raw in (3, 4, 10, 100, "99"):
            self.assertLessEqual(clamp_workers(raw), 3)

    def test_llm_concurrency_env(self) -> None:
        os.environ["BID_AGENT_LLM_CONCURRENCY"] = "2"
        self.assertEqual(llm_concurrency_limit(), 2)
        os.environ["BID_AGENT_LLM_CONCURRENCY"] = "0"
        self.assertEqual(llm_concurrency_limit(), 1)


class ConcurrencyRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            k: os.environ.get(k)
            for k in (
                "BID_AGENT_WORKERS_DEFAULT",
                "BID_AGENT_WORKERS_MAX",
                "BID_AGENT_LLM_CONCURRENCY",
            )
        }
        os.environ["BID_AGENT_LLM_CONCURRENCY"] = "2"
        reset_for_tests()

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_for_tests()

    def test_chapter_workers_scope_metrics(self) -> None:
        with chapter_workers_scope(5) as n:
            self.assertEqual(n, min(5, workers_max()))
            snap = concurrency_snapshot()
            self.assertGreaterEqual(snap["active_workers"], n)
        snap = concurrency_snapshot()
        self.assertEqual(snap["active_workers"], 0)
        self.assertGreaterEqual(snap["peak_workers"], 1)

    def test_llm_slot_limits_parallelism(self) -> None:
        os.environ["BID_AGENT_LLM_CONCURRENCY"] = "1"
        reset_for_tests()
        active = []
        max_active = 0
        lock = threading.Lock()

        def worker() -> None:
            nonlocal max_active
            with llm_slot():
                with lock:
                    active.append(1)
                    max_active = max(max_active, len(active))
                time.sleep(0.05)
                with lock:
                    active.pop()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(max_active, 1)
        snap = concurrency_snapshot()
        self.assertEqual(snap["llm_acquire_count"], 4)
        self.assertEqual(snap["active_llm"], 0)

    def test_429_triggers_load_shed(self) -> None:
        note_rate_limit_429(backoff_seconds=1.0)
        self.assertTrue(is_load_shedding())
        snap = concurrency_snapshot()
        self.assertGreaterEqual(snap["rate_limit_429_count"], 1)
        self.assertGreaterEqual(snap["load_shed_count"], 1)
        self.assertGreaterEqual(snap["retry_after_backoff_count"], 1)

    def test_chat_with_meta_uses_llm_slot(self) -> None:
        from llm_client import chat_with_meta

        with patch("llm_client.get_settings") as gs, patch("llm_client._openai_request") as req:
            settings = type(
                "S",
                (),
                {
                    "base_url": "https://example.com/v1",
                    "api_key": "k",
                    "model": "m",
                    "provider": "openai",
                    "timeout": 5,
                    "max_retries": 1,
                    "retry_initial_delay": 0.01,
                    "retry_max_delay": 0.05,
                    "stream": False,
                    "verify_ssl": True,
                },
            )()
            gs.return_value = settings
            req.return_value = ("ok", "")
            out = chat_with_meta([{"role": "user", "content": "hi"}])
            self.assertEqual(out["content"], "ok")
            self.assertGreaterEqual(concurrency_snapshot()["llm_acquire_count"], 1)


if __name__ == "__main__":
    unittest.main()
