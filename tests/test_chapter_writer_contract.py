from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chapter_writer import write_chapter_from_job_context
from subagent_runner import run_per_chapter


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChapterWriterContractTests(unittest.TestCase):
    def _build_workspace(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        (root / "prompts").mkdir(parents=True, exist_ok=True)
        (root / "prompts" / "write_chapter.md").write_text("你是章节写作助手。", encoding="utf-8")
        _write_json(root / "workspace" / "score_points.json", [{"id": "S1", "title": "服务能力"}])
        _write_json(root / "workspace" / "global_facts.json", {"project_name": "测试项目"})
        _write_json(root / "workspace" / "tender_requirements.json", {"service_period": "3年"})
        _write_json(root / "workspace" / "template_evidence_map.json", {"summary": {}, "items": []})
        _write_json(
            root / "workspace" / "chunks" / "tender_chunks.json",
            [{"id": "T-1", "source": "tender", "content": "实施服务要求", "title_path": ["第一章"]}],
        )
        _write_json(
            root / "workspace" / "chunks" / "company_chunks.json",
            [{"id": "C-1", "source": "company", "content": "服务团队与案例", "title_path": ["第二章"]}],
        )
        job = {
            "chapter_id": "2.1",
            "chapter_title": "服务方案",
            "heading_level": 2,
            "score_point_ids": ["S1"],
            "description": "说明服务方案",
            "writing_requirements": ["覆盖实施与服务承诺"],
            "sections": [],
            "template_tasks": [{"id": "TT-1", "title": "服务承诺", "status": "weak"}],
        }
        context = {
            "chapter_id": "2.1",
            "selected_tender_chunks": [{"id": "T-1", "reason": "需求明确"}],
            "selected_company_chunks": [{"id": "C-1", "reason": "案例匹配"}],
            "warnings": [],
            "selection_meta": {
                "tender_candidates_total": 1,
                "company_candidates_total": 1,
                "max_context_chars": 16000,
                "max_chunks_per_side": 16,
                "dropped_reason": "",
            },
        }
        return job, context

    def test_writer_persists_prompt_metadata_and_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, context = self._build_workspace(root)
            with patch("chapter_writer.chat", return_value="服务承诺部分将按招标要求逐项响应。"):
                content = write_chapter_from_job_context(job, context, root)

            self.assertTrue(content.startswith("## 2.1 服务方案"))
            artifact_path = root / "workspace" / "agent_runs" / "write_chapters__chapter_writer__2.1.json"
            self.assertTrue(artifact_path.exists())
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["prompt_file"], "write_chapter.md")
            self.assertEqual(payload["prompt_version"], "1.0.0")
            self.assertTrue(payload["prompt_checksum"])
            self.assertIn("context_budget", payload)

    def test_writer_enforces_weak_evidence_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, context = self._build_workspace(root)
            with patch("chapter_writer.chat", return_value="服务承诺已具备完整能力并完全满足要求。"):
                with self.assertRaisesRegex(ValueError, "弱证据模板任务被写成既成事实"):
                    write_chapter_from_job_context(job, context, root)

    def test_writer_failure_fallback_setting_produces_completed_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, _context = self._build_workspace(root)
            _write_json(root / "workspace" / "jobs" / "2.1.json", job)

            def always_fail(_chapter_id: str, _root: Path) -> None:
                raise RuntimeError("model unavailable")

            with (
                patch.dict(os.environ, {"BID_AGENT_WRITE_FAILURE_FALLBACK": "1", "BID_AGENT_WRITE_BATCH_RETRIES": "0"}),
                patch("subagent_runner.begin_phase"),
                patch("subagent_runner.end_phase"),
                patch("subagent_runner.mark_agent"),
            ):
                result = run_per_chapter(always_fail, root, workers=1, label="写作 SubAgent")

            self.assertEqual(result["failed"], [])
            self.assertEqual(result["completed"], ["2.1"])
            draft = (root / "workspace" / "chapters" / "2.1.md").read_text(encoding="utf-8")
            self.assertIn("## 2.1 服务方案", draft)
            self.assertIn("草稿说明", draft)


if __name__ == "__main__":
    unittest.main()
