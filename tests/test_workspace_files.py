from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class WorkspaceFilesApiTests(unittest.TestCase):
    def test_build_workspace_file_tree_lists_stage_intermediates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "sources" / "tender" / "招标.pdf", "pdf")
            _write(root / "sources" / "company" / "公司.md", "# co")
            _write(root / "inputs" / "tender.md", "# t")
            _write(root / "inputs" / "score.md", "# s")
            _write(root / "workspace" / "chunks" / "tender_chunks.json", "[]")
            _write(root / "workspace" / "chunks" / "company_chunks.json", "[]")
            _write(root / "workspace" / "outline.json", "{}")
            _write(root / "workspace" / "jobs" / "01.json", "{}")
            _write(root / "workspace" / "chapters" / "01.md", "# c1")
            _write(root / "workspace" / "reviews" / "01_review.json", "{}")
            _write(root / "outputs" / "final.md", "# final")
            _write(root / "workspace" / "debug_something.txt", "dbg")

            tree = web_app.build_workspace_file_tree(root)
            self.assertTrue(tree["ok"])
            self.assertGreater(tree["total"], 5)
            by_key = {section["key"]: section for section in tree["sections"]}
            self.assertEqual(len(by_key["tender"]["items"]), 1)
            self.assertTrue(any(i["path"] == "workspace/chapters/01.md" for i in by_key["stage_write_chapters"]["items"]))
            self.assertTrue(any(i["path"] == "workspace/reviews/01_review.json" for i in by_key["stage_review_fix_chapters"]["items"]))
            self.assertTrue(any(i["path"] == "outputs/final.md" for i in by_key["outputs"]["items"]))
            other_paths = {i["path"] for i in by_key.get("other_workspace", {}).get("items", [])}
            self.assertIn("workspace/debug_something.txt", other_paths)

    def test_api_workspace_files_uses_active_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "workspace" / "global_facts.json", "{}")
            _write(root / "outputs" / "final.docx", "docx")
            web_app.ACTIVE_RUN_ROOT = root
            web_app.ACTIVE_RUN_ID = "run-x"
            response = web_app.api_workspace_files()
            payload = json.loads(response.body.decode("utf-8"))
            self.assertTrue(payload["ok"])
            paths = [item["path"] for section in payload["sections"] for item in section["items"]]
            self.assertIn("workspace/global_facts.json", paths)
            self.assertIn("outputs/final.docx", paths)


if __name__ == "__main__":
    unittest.main()
