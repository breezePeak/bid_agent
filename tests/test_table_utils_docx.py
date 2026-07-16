from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_utils import collect_docx_tables, collect_project_markdown_tables


class TableUtilsDocxTests(unittest.TestCase):
    def test_collect_docx_tables(self) -> None:
        try:
            from docx import Document
        except Exception:
            self.skipTest("python-docx not available")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "outputs"
            outputs.mkdir(parents=True)
            doc = Document()
            table = doc.add_table(rows=3, cols=4)
            headers = ["名称", "数量", "单价", "合价"]
            for i, h in enumerate(headers):
                table.rows[0].cells[i].text = h
            table.rows[1].cells[0].text = "服务A"
            table.rows[1].cells[1].text = "2"
            table.rows[1].cells[2].text = "100"
            table.rows[1].cells[3].text = "200"
            table.rows[2].cells[0].text = "服务B"
            table.rows[2].cells[1].text = "1"
            table.rows[2].cells[2].text = "50"
            table.rows[2].cells[3].text = "50"
            doc_path = outputs / "final.docx"
            doc.save(str(doc_path))

            tables = collect_docx_tables(root)
            self.assertEqual(len(tables), 1)
            self.assertEqual(tables[0]["header"][0], "名称")
            self.assertEqual(len(tables[0]["rows"]), 2)

            all_tables = collect_project_markdown_tables(root)
            self.assertTrue(any("final.docx" in str(t.get("source")) for t in all_tables))


if __name__ == "__main__":
    unittest.main()
