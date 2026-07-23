from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from materials_checklist import (
    build_materials_checklist,
    chapters_ready_for_refill,
    ensure_placeholders_in_content,
    items_for_chapter,
    load_materials_checklist,
    material_gap_ids_in_text,
    render_placeholder_block,
    strip_material_gap_blocks,
    update_item_response,
    writing_requirement_lines,
)
from pipeline_registry import stage_spec_by_id, workflow_stage_specs
from utils import write_json, write_text


class MaterialsChecklistTests(unittest.TestCase):
    def _seed(self, root: Path) -> None:
        (root / "inputs").mkdir(parents=True)
        (root / "workspace").mkdir(parents=True)
        write_text(
            root / "inputs" / "tender.md",
            "\n".join(
                [
                    "# 招标文件",
                    "投标人须具备有效营业执照。",
                    "须提交授权委托书。",
                    "未按要求缴纳投标保证金的，作废标处理。",
                    "投标文件应包含资格审查表、业绩表。",
                ]
            ),
        )
        write_text(root / "inputs" / "score.md", "评分标准：技术方案 40 分。")
        write_text(root / "inputs" / "company.md", "本公司专注软件开发，暂无证书明细。")
        write_json(
            root / "workspace" / "tender_requirements.json",
            {
                "project_name": "演示项目",
                "qualification_requirements": [
                    "具备有效的信息系统集成资质",
                    "项目经理具备 PMP 证书",
                ],
                "evidence_notes": ["须提供近三年类似项目合同复印件"],
            },
        )
        write_json(
            root / "workspace" / "company_facts.json",
            {"bidder_name": "演示科技有限公司", "core_products": ["平台"], "similar_cases": []},
        )
        write_json(root / "workspace" / "global_facts.json", {"project_name": "演示项目"})

    def test_build_checklist_marks_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            path = build_materials_checklist(root)
            self.assertTrue(path.exists())
            data = load_materials_checklist(root)
            self.assertGreaterEqual(data["summary"]["total"], 3)
            self.assertGreaterEqual(data["summary"]["deferred"], 1)
            quals = [i for i in data["items"] if i.get("category") == "qualification"]
            self.assertTrue(quals)
            self.assertTrue(all(i.get("response_status") == "deferred" for i in quals))
            self.assertTrue(any(i.get("category") == "disqualification" for i in data["items"]))
            self.assertTrue(any(i.get("category") == "mandatory_doc" for i in data["items"]))

    def test_override_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            build_materials_checklist(root)
            data = load_materials_checklist(root)
            first = data["items"][0]["item_id"]
            write_json(
                root / "workspace" / "manual_review" / "materials_checklist_overrides.json",
                {"items": [{"item_id": first, "response_status": "ready", "reason": "已线下核验"}]},
            )
            build_materials_checklist(root)
            data = load_materials_checklist(root)
            item = next(i for i in data["items"] if i["item_id"] == first)
            self.assertEqual(item["response_status"], "ready")
            self.assertEqual(item.get("reason"), "已线下核验")

    def test_placeholder_block_and_ensure(self) -> None:
        item = {
            "item_id": "MAT-QUAL-001",
            "response_status": "deferred",
            "requirement": "具备有效集成资质",
            "reason": "公司资料未提供",
            "suggested_attachment": "资质证书扫描件",
            "suggested_placeholder_language": "拟提供资质证书并随投标文件附后。",
        }
        block = render_placeholder_block(item)
        self.assertIn("MATERIAL_GAP", block)
        self.assertIn("MAT-QUAL-001", block)
        self.assertIn("留白原因", block)
        content = ensure_placeholders_in_content("# 1 资格\n\n正文。\n", [item])
        self.assertIn("MATERIAL_GAP", content)
        self.assertIn("材料待补清单", content)
        # already present -> no duplicate
        again = ensure_placeholders_in_content(content, [item])
        self.assertEqual(again.count("MAT-QUAL-001"), content.count("MAT-QUAL-001"))

    def test_strip_material_gap_for_compliance_scan(self) -> None:
        item = {
            "item_id": "MAT-DOC-001",
            "response_status": "deferred",
            "requirement": "授权委托书",
            "reason": "缺材料",
            "suggested_attachment": "授权委托书",
            "suggested_placeholder_language": "拟提供",
        }
        text = "正常正文\n" + render_placeholder_block(item) + "\n还有 XXX 占位"
        stripped = strip_material_gap_blocks(text)
        self.assertNotIn("MATERIAL_GAP", stripped)
        self.assertIn("XXX", stripped)
        self.assertIn("正常正文", stripped)

    def test_items_for_chapter_and_writing_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            build_materials_checklist(root)
            job = {
                "chapter_title": "资格审查响应",
                "description": "响应资格与证明材料",
                "writing_requirements": ["覆盖资格条件"],
            }
            matched = items_for_chapter(root, job=job)
            self.assertTrue(matched)
            lines = writing_requirement_lines(matched)
            self.assertTrue(any("deferred" in line for line in lines))

    def test_stage_registered_after_extract_facts(self) -> None:
        specs = workflow_stage_specs()
        ids = [s.id for s in specs]
        self.assertIn("build_materials_checklist", ids)
        self.assertLess(ids.index("extract_facts"), ids.index("build_materials_checklist"))
        self.assertLess(ids.index("build_materials_checklist"), ids.index("build_template_evidence"))
        stage = stage_spec_by_id("build_materials_checklist")
        self.assertEqual(stage.command, "build-materials-checklist")
        self.assertTrue(any(a.path == "control.db:material_states" and a.kind == "virtual" for a in stage.produces))

    def test_update_item_response_and_refill_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            build_materials_checklist(root)
            data = load_materials_checklist(root)
            item_id = data["items"][0]["item_id"]
            result = update_item_response(root, item_id, response_status="ready", reason="已上传证书")
            self.assertTrue(result["ok"], result)
            data = load_materials_checklist(root)
            item = next(i for i in data["items"] if i["item_id"] == item_id)
            self.assertEqual(item["response_status"], "ready")

            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            block = render_placeholder_block(item)
            write_text(chapters / "01.md", f"# 1 资格\n\n{block}\n")
            self.assertIn(item_id, material_gap_ids_in_text(block))
            plans = chapters_ready_for_refill(root)
            self.assertTrue(any(p["chapter_id"] == "01" and item_id in p["ready_item_ids"] for p in plans))


if __name__ == "__main__":
    unittest.main()
