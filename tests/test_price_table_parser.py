from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from price_table_parser import analyze_price_table, parse_price_tables, price_table_compliance_items
from table_utils import parse_markdown_tables


class PriceTableParserTests(unittest.TestCase):
    def test_qty_unit_price_ok(self) -> None:
        md = """
| 序号 | 名称 | 数量 | 单价 | 合价 |
| --- | --- | --- | --- | --- |
| 1 | 服务A | 2 | 1000 | 2000 |
| 2 | 服务B | 3 | 500 | 1500 |
"""
        tables = parse_markdown_tables(md, source="t.md")
        result = analyze_price_table(tables[0])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 2)

    def test_qty_unit_price_mismatch(self) -> None:
        md = """
| 名称 | 数量 | 单价 | 合计 |
| --- | --- | --- | --- |
| 项1 | 2 | 100 | 300 |
"""
        tables = parse_markdown_tables(md, source="t.md")
        result = analyze_price_table(tables[0])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertTrue(any(i["type"] == "qty_unit_mismatch" for i in result["issues"]))

    def test_compliance_items_fail_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            (chapters / "01.md").write_text(
                "\n".join(
                    [
                        "# 01 报价",
                        "| 名称 | 数量 | 单价 | 合价 |",
                        "| --- | --- | --- | --- |",
                        "| A | 2 | 10 | 50 |",
                    ]
                ),
                encoding="utf-8",
            )
            items = price_table_compliance_items(root)
            self.assertTrue(any(i.get("status") == "fail" for i in items))
            report = json.loads((root / "workspace" / "price_table_report.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(report["issue_count"], 1)


if __name__ == "__main__":
    unittest.main()
