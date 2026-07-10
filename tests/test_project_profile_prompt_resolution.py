from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from project_profile_registry import save_project_profile
from prompt_registry import resolve_prompt_spec


class ProjectProfilePromptResolutionTests(unittest.TestCase):
    def test_project_type_override_resolves_variant_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts_dir = root / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            (prompts_dir / "write_chapter.md").write_text("默认章节提示词", encoding="utf-8")
            (prompts_dir / "write_chapter.software_project.md").write_text("软件项目章节提示词", encoding="utf-8")
            save_project_profile(root, "software_project")

            resolved = resolve_prompt_spec(root, "chapter_writer")
            self.assertEqual(resolved["project_type"], "software_project")
            self.assertEqual(resolved["prompt_file"], "write_chapter.software_project.md")
            self.assertEqual(resolved["version"], "1.0.0+software_project")
            self.assertIn("软件项目", resolved["prompt_text"])


if __name__ == "__main__":
    unittest.main()
