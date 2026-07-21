from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.activity import activity_for_api, begin_phase, end_phase, load_activity, mark_agent


class AgentActivityTests(unittest.TestCase):
    def test_v1_activity_file_is_imported_once_then_sqlite_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace" / "agent" / "activity.json"
            path.parent.mkdir(parents=True)
            original = {
                "status": "running",
                "phase": "write",
                "agents": [],
                "summary": {"total": 0, "running": 0, "done": 0, "failed": 0, "queued": 0},
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            self.assertEqual(load_activity(root)["phase"], "write")
            path.write_text(json.dumps({**original, "status": "done", "phase": "stale"}), encoding="utf-8")
            current = load_activity(root)
            self.assertEqual(current["status"], "running")
            self.assertEqual(current["phase"], "write")

    def test_phase_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            begin_phase(
                root,
                phase="write",
                phase_label="写作 SubAgent",
                role="chapter_writer",
                chapter_ids=["01", "02", "03"],
            )
            data = activity_for_api(root)
            self.assertEqual(data["summary"]["queued"], 3)
            mark_agent(root, role="chapter_writer", chapter_id="01", status="running", message="writing", attempt=1)
            data = activity_for_api(root)
            self.assertEqual(data["summary"]["running"], 1)
            mark_agent(root, role="chapter_writer", chapter_id="01", status="done", message="ok", attempt=1)
            mark_agent(root, role="chapter_writer", chapter_id="02", status="failed", message="err", attempt=2)
            end_phase(root, status="partial_failed")
            data = activity_for_api(root)
            statuses = {a["chapter_id"]: a["status"] for a in data["agents"]}
            self.assertEqual(statuses["01"], "done")
            self.assertEqual(statuses["02"], "failed")
            self.assertEqual(statuses["03"], "skipped")


if __name__ == "__main__":
    unittest.main()
