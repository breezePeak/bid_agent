from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from context_selector import MAX_RANKED_CHUNKS_PER_SIDE, select_context_for_job


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ContextSelectorContractTests(unittest.TestCase):
    def test_select_context_output_contract_and_budget_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts").mkdir(parents=True, exist_ok=True)
            (root / "prompts" / "select_context.md").write_text("你是上下文选择器。", encoding="utf-8")

            tender_chunks = [
                {"id": "T-1", "source": "tender", "content": "项目范围与服务要求", "title_path": ["第一章"]},
                {"id": "T-2", "source": "tender", "content": "实施周期与交付要求", "title_path": ["第二章"]},
            ]
            company_chunks = [
                {"id": "C-1", "source": "company", "content": "实施案例与团队简历", "title_path": ["案例"]},
                {"id": "C-2", "source": "company", "content": "资质证书与售后服务", "title_path": ["资质"]},
            ]
            _write_json(root / "workspace" / "chunks" / "tender_chunks.json", tender_chunks)
            _write_json(root / "workspace" / "chunks" / "company_chunks.json", company_chunks)
            _write_json(root / "workspace" / "score_points.json", [{"id": "S1", "title": "实施能力"}])
            _write_json(root / "workspace" / "global_facts.json", {"project_name": "测试项目"})
            _write_json(root / "workspace" / "template_evidence_map.json", {"summary": {"weak": 1}, "items": []})

            job = {
                "chapter_id": "1.1",
                "chapter_title": "实施方案",
                "score_point_ids": ["S1"],
                "template_tasks": [{"id": "TE-1", "tender_chunk_ids": ["T-1"], "company_chunk_ids": ["C-1"]}],
            }

            ranked = {
                "tender_top_chunks": [{"id": "T-1"}, {"id": "T-2"}],
                "company_top_chunks": [{"id": "C-1"}, {"id": "C-2"}],
            }
            raw = json.dumps(
                {
                    "selected_tender_chunks": [{"id": "T-1", "reason": "项目范围直接相关"}],
                    "selected_company_chunks": [{"id": "C-1", "reason": "案例可支撑实施能力"}],
                },
                ensure_ascii=False,
            )

            with patch("context_selector.rank_for_job_separate", return_value=ranked), patch("context_selector.chat", return_value=raw):
                output_path = select_context_for_job(job, root)

            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["chapter_id"], "1.1")
            self.assertIn("selected_tender_chunks", data)
            self.assertIn("selected_company_chunks", data)
            self.assertIn("warnings", data)
            self.assertIn("selection_meta", data)

            meta = data["selection_meta"]
            for key in (
                "tender_candidates_total",
                "company_candidates_total",
                "max_context_chars",
                "max_chunks_per_side",
                "dropped_reason",
            ):
                self.assertIn(key, meta)

            selected_tender_ids = {item["id"] for item in data["selected_tender_chunks"]}
            selected_company_ids = {item["id"] for item in data["selected_company_chunks"]}
            self.assertTrue(selected_tender_ids.issubset({"T-1", "T-2"}))
            self.assertTrue(selected_company_ids.issubset({"C-1", "C-2"}))
            self.assertLessEqual(len(selected_tender_ids), MAX_RANKED_CHUNKS_PER_SIDE)
            self.assertLessEqual(len(selected_company_ids), MAX_RANKED_CHUNKS_PER_SIDE)


if __name__ == "__main__":
    unittest.main()
