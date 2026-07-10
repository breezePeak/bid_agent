from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import main
from chapter_rewriter import review_fix_all


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class IncrementalPipelineTests(unittest.TestCase):
    def test_select_context_only_dispatches_missing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for chapter_id in ("01", "02"):
                _write_json(root / "workspace" / "jobs" / f"{chapter_id}.json", {"chapter_id": chapter_id})
            _write_json(root / "workspace" / "contexts" / "01_context.json", {"chapter_id": "01"})

            with patch("context_selector.select_contexts_for_jobs") as select_all:
                main._run_select_context_all(root)

            jobs = select_all.call_args.args[0]
            self.assertEqual([job["chapter_id"] for job in jobs], ["02"])

    def test_write_only_dispatches_missing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for chapter_id in ("01", "02"):
                _write_json(root / "workspace" / "jobs" / f"{chapter_id}.json", {"chapter_id": chapter_id})
                _write_json(root / "workspace" / "contexts" / f"{chapter_id}_context.json", {"chapter_id": chapter_id})
            chapter = root / "workspace" / "chapters" / "01.md"
            chapter.parent.mkdir(parents=True, exist_ok=True)
            chapter.write_text("# 已完成章节", encoding="utf-8")

            with patch("subagent_runner.run_write_all", return_value={"completed": ["02"], "failed": []}) as write_all:
                main._run_write_all(root, workers=2)

            self.assertEqual(write_all.call_args.kwargs["chapter_ids"], ["02"])

    def test_review_only_dispatches_chapters_without_valid_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "workspace" / "outline.json",
                {"chapters": [{"id": "01"}, {"id": "02"}]},
            )
            for chapter_id in ("01", "02"):
                chapter = root / "workspace" / "chapters" / f"{chapter_id}.md"
                chapter.parent.mkdir(parents=True, exist_ok=True)
                chapter.write_text(f"# {chapter_id}", encoding="utf-8")
            _write_json(
                root / "workspace" / "reviews" / "01_review.json",
                {"chapter_id": "01", "need_rewrite": False},
            )

            with patch("subagent_runner.run_review_all", return_value={"completed": ["02"], "failed": []}) as review_all:
                review_fix_all(root, workers=2)

            self.assertEqual(review_all.call_args.kwargs["chapter_ids"], ["02"])


if __name__ == "__main__":
    unittest.main()
