from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_registry import stage_outputs_ready
from stage_validation import missing_ids_for_stage, stage_collection_status


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class StageValidationTests(unittest.TestCase):
    def test_partial_context_glob_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for chapter_id in ("01", "02"):
                _write_json(root / "workspace" / "jobs" / f"{chapter_id}.json", {"chapter_id": chapter_id})
            _write_json(
                root / "workspace" / "contexts" / "01_context.json",
                {"chapter_id": "01", "selected_tender_chunks": []},
            )

            with patch("context_selector.valid_context_ids", return_value={"01"}):
                status = stage_collection_status(root, "select_contexts")
            self.assertFalse(status["complete"])
            self.assertEqual(status["missing_ids"], ["02"])
            with patch("context_selector.valid_context_ids", return_value={"01"}):
                self.assertFalse(stage_outputs_ready(root, "select_contexts"))

    def test_invalid_json_is_treated_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "workspace" / "jobs" / "01.json", {"chapter_id": "01"})
            path = root / "workspace" / "contexts" / "01_context.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{bad", encoding="utf-8")
            self.assertEqual(missing_ids_for_stage(root, "select_contexts"), ["01"])

    def test_collection_complete_when_every_expected_id_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for chapter_id in ("01", "02"):
                _write_json(root / "workspace" / "jobs" / f"{chapter_id}.json", {"chapter_id": chapter_id})
                _write_json(
                    root / "workspace" / "contexts" / f"{chapter_id}_context.json",
                    {"chapter_id": chapter_id},
                )
            with patch("context_selector.valid_context_ids", return_value={"01", "02"}):
                self.assertTrue(stage_outputs_ready(root, "select_contexts"))


if __name__ == "__main__":
    unittest.main()
