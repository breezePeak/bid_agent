from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402
from document_pipeline.template_contract import TemplateContractCompiler  # noqa: E402


class V3SourceStructureTests(unittest.TestCase):
    def _context(self, base: Path) -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    def test_docx_source_index_preserves_heading_paragraph_and_table_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "tender.docx"
            document = Document()
            document.add_heading("第一章 项目概述", level=1)
            document.add_paragraph("项目范围。")
            document.add_table(rows=1, cols=2).cell(0, 0).text = "交付成果"
            document.tables[0].cell(0, 1).text = "实施报告"
            document.add_heading("第二章 服务要求", level=1)
            document.save(path)
            context = self._context(base)
            InputManifestService(context).register_local_file(path, InputRole.TENDER)
            index = SourceNormalizer(context).normalize_active_inputs()
            blocks = index["blocks"]
            self.assertEqual([block["block_kind"] for block in blocks], ["heading", "paragraph", "table_cell", "table_cell", "heading"])
            self.assertEqual(blocks[1]["heading_path"], ["第一章 项目概述"])
            self.assertEqual(blocks[2]["source_anchor"]["location"], "table:1:row:1:cell:1")
            self.assertTrue(all(block["content_hash"] for block in blocks))

    def test_amendment_keeps_issued_at_and_explicit_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tender = base / "tender.md"
            amendment = base / "amendment.md"
            tender.write_text("原工期为30日。", encoding="utf-8")
            amendment.write_text("更正工期为20日。", encoding="utf-8")
            context = self._context(base)
            inputs = InputManifestService(context)
            original = inputs.register_local_file(tender, InputRole.TENDER)
            inputs.register_local_file(
                amendment,
                InputRole.AMENDMENT,
                issued_at="2026-07-27T10:00:00+08:00",
                supersedes_input_ids=[original.item.input_id],
            )
            index = SourceNormalizer(context).normalize_active_inputs()
            self.assertEqual(index["amendments"][0]["supersedes_input_ids"], [original.item.input_id])
            self.assertEqual(index["amendments"][0]["issued_at"], "2026-07-27T10:00:00+08:00")

    def test_template_structure_compiles_without_requirement_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "template.docx"
            document = Document()
            document.add_heading("技术方案", level=1)
            document.add_paragraph("项目名称：{{项目名称}}")
            document.save(path)
            context = self._context(base)
            item = InputManifestService(context).register_local_file(path, InputRole.TEMPLATE).item
            structure = TemplateContractCompiler(context).compile_structure(item)
            self.assertEqual([node.title for node in structure.nodes], ["技术方案"])
            self.assertEqual(structure.slots[0].kind, "text_slot")
            self.assertEqual(structure.template_input_id, item.input_id)


if __name__ == "__main__":
    unittest.main()
