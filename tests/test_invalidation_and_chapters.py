from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.invalidation import (
    INVALIDATION_MAP,
    clear_stale_if_rebuilt,
    is_stale,
    load_stale,
    mark_invalidated,
    stale_summary,
)
from agent.tool_registry import get_tool, list_tools, reset_tool_index
from agent.tool_runtime import invoke


class InvalidationTests(unittest.TestCase):
    def test_mark_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = mark_invalidated(
                root,
                reason="test rewrite",
                chapter_ids=["01", "02"],
                source_stage="write_chapters",
            )
            self.assertTrue(state["items"])
            self.assertTrue(is_stale(root, "outputs/final.docx"))
            self.assertIn("失效产物", stale_summary(root))
            clear_stale_if_rebuilt(root, ["outputs/final.docx", "outputs/final.md"])
            self.assertFalse(is_stale(root, "outputs/final.docx"))

    def test_map_covers_write(self) -> None:
        self.assertIn("write_chapters", INVALIDATION_MAP)
        self.assertIn("outputs/final.docx", INVALIDATION_MAP["write_chapters"])


class ParameterizedChapterToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_tools_registered(self) -> None:
        for name in ("write_chapters", "review_chapters", "rewrite_chapters"):
            spec = get_tool(name)
            self.assertIsNotNone(spec, name)
            assert spec is not None
            self.assertIn("parameterized", spec.tags)
            self.assertIn("chapter_ids", (spec.params_schema.get("properties") or {}))

    def test_write_all_alias_still_stage(self) -> None:
        # command alias remains available
        spec = get_tool("write-all")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.stage_id, "write_chapters")

    def test_missing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("write_chapters", {"chapter_ids": ["01"]}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "missing_requires")

    def test_dry_run_filters_chapter_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "workspace" / "jobs"
            jobs.mkdir(parents=True)
            (jobs / "01.json").write_text("{}", encoding="utf-8")
            (jobs / "02.json").write_text("{}", encoding="utf-8")
            (jobs / "03.json").write_text("{}", encoding="utf-8")
            result = invoke(
                "write_chapters",
                {"chapter_ids": ["01", "03"], "dry_run": True},
                root=root,
            )
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertEqual(result.metrics.get("chapter_ids"), ["01", "03"])
            self.assertEqual(result.metrics.get("count"), 2)

    def test_dry_run_unknown_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "workspace" / "jobs"
            jobs.mkdir(parents=True)
            (jobs / "01.json").write_text("{}", encoding="utf-8")
            result = invoke(
                "write_chapters",
                {"chapter_ids": ["99"], "dry_run": True},
                root=root,
            )
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "invalid_args")

    def test_invalid_chapter_ids_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("rewrite_chapters", {"chapter_ids": "01"}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "invalid_args")

    def test_list_tools_includes_parameterized(self) -> None:
        names = {t.name for t in list_tools()}
        self.assertIn("write_chapters", names)
        self.assertIn("review_chapters", names)
        self.assertIn("rewrite_chapters", names)


if __name__ == "__main__":
    unittest.main()
