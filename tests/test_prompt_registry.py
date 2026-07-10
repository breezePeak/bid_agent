from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from prompt_registry import AGENT_SPECS, load_agent_prompt, prompt_checksum
from runtime_context import agent_run


class PromptRegistryTests(unittest.TestCase):
    def test_agent_run_persists_prompt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts_dir = root / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            prompt_text = "你是测试助手。"
            prompt_path = prompts_dir / AGENT_SPECS["chapter_writer"].prompt_file
            prompt_path.write_text(prompt_text, encoding="utf-8")

            with agent_run(root, "write_chapters", "chapter_writer", input_summary={"chapter_id": "01"}, chapter_id="01", temperature=0.2):
                loaded = load_agent_prompt(root, "chapter_writer")
                self.assertEqual(prompt_text, loaded)

            artifact_path = root / "workspace" / "agent_runs" / "write_chapters__chapter_writer__01.json"
            self.assertTrue(artifact_path.exists())
            payload = artifact_path.read_text(encoding="utf-8")
            self.assertIn('"prompt_file": "write_chapter.md"', payload)
            self.assertIn(f'"prompt_checksum": "{prompt_checksum(prompt_text)}"', payload)


if __name__ == "__main__":
    unittest.main()
