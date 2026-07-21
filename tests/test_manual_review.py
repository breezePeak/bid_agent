from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext
from manual_review import apply_manual_review_update, manual_review_items, manual_review_summary, recommended_replay_stage


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ManualReviewTests(unittest.TestCase):
    def test_manual_review_overrides_do_not_mutate_source_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_map = {
                "summary": {"weak": 1},
                "items": [{"id": "TE-1", "heading_id": "1", "title": "承诺函", "status": "weak", "analysis": {}, "notes": [], "evidence": {}}],
            }
            _write_json(root / "workspace" / "template_evidence_map.json", original_map)
            _write_json(root / "workspace" / "score_coverage_matrix.json", {"matrix": [{"score_point_id": "S1", "score_point_title": "能力", "risk_level": "high"}]})

            result = apply_manual_review_update(
                root,
                "template_evidence",
                {
                    "item_id": "TE-1",
                    "status": "confirmed",
                    "operator_note": "人工确认后补充材料",
                    "preferred_tender_chunk_ids": ["T-9"],
                },
            )
            summary = manual_review_summary(root)

            self.assertEqual(result["recommended_stage"], "select_contexts")
            # closed override should drop out of pending list/summary
            self.assertEqual(summary["template_evidence_pending"], 0)
            self.assertEqual(recommended_replay_stage("score_coverage", {"target_chapter_id": "2.1"}), "plan_chapter_jobs")

            reloaded_map = json.loads((root / "workspace" / "template_evidence_map.json").read_text(encoding="utf-8"))
            self.assertEqual(reloaded_map, original_map)
            overrides_path = root / "workspace" / "manual_review" / "template_evidence_overrides.json"
            self.assertTrue(overrides_path.exists())

    def test_sqlite_manual_review_decision_overrides_tampered_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            _write_json(
                root / "workspace" / "score_coverage_matrix.json",
                {"matrix": [{"score_point_id": "S1", "score_point_title": "能力", "risk_level": "high"}]},
            )
            _write_json(
                root / "workspace" / "manual_review" / "score_coverage_overrides.json",
                {"items": {"S1": {"item_id": "S1", "status": "pending"}}},
            )
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context).record_policy_decision(
                issue_id="manual-review:score_coverage:S1",
                decision_type="manual_review",
                decision={"payload": {"item_id": "S1", "status": "accepted"}},
                actor={"type": "user", "id": "reviewer"},
            )

            pending = manual_review_items(root, "score_coverage")
            all_items = manual_review_items(root, "score_coverage", include_closed=True)

            self.assertEqual(pending, [])
            self.assertEqual(all_items[0]["override"]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
