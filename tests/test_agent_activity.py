from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.activity import activity_for_api, begin_phase, end_phase, mark_agent


class AgentActivityTests(unittest.TestCase):
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
