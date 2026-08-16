from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.stream_think import StreamThinkSplitter, strip_think_tags  # noqa: E402


class StreamThinkTests(unittest.TestCase):
    def test_splits_think_tags_across_chunks(self) -> None:
        splitter = StreamThinkSplitter()
        first_think, first_body = splitter.feed("<th")
        self.assertEqual(first_think, "")
        self.assertEqual(first_body, "")
        think, body = splitter.feed("ink>目录位置确认</think>正文第一段")
        self.assertEqual(think, "目录位置确认")
        self.assertEqual(body, "正文第一段")

    def test_strip_removes_think_blocks_from_finished_text(self) -> None:
        text = "<think>不要进 Word</think>\n\n项目实施方案"
        self.assertEqual(strip_think_tags(text), "项目实施方案")


if __name__ == "__main__":
    unittest.main()
