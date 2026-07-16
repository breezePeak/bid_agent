from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deviation_table_checker import analyze_deviation_table, check_deviation_tables, deviation_compliance_items
from table_utils import parse_markdown_tables


class DeviationTableCheckerTests(unittest.TestCase):
    def test_full_response_rows_pass(self) -> None:
        md = """
| 序号 | 文件要求 | 投标响应 | 响应程度 |
| --- | --- | --- | --- |
| 1 | 提供驻场 | 提供驻场 | 完全响应 |
| 2 | 服务期一年 | 服务期一年 | 无偏离 |
"""
        tables = parse_markdown_tables(md, source="t.md")
        result = analyze_deviation_table(tables[0])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["ok"])

    def test_negative_and_empty_fail(self) -> None:
        md = """
| 技术要求 | 供应商响应 | 偏离情况 |
| --- | --- | --- |
| 必须 7x24 | 仅工作日 | 负偏离 |
| 提供报告 |  | 完全响应 |
"""
        tables = parse_markdown_tables(md, source="t.md")
        result = analyze_deviation_table(tables[0])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertGreaterEqual(result["negative_count"], 1)
        self.assertGreaterEqual(result["empty_count"], 1)

    def test_compliance_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapters = root / "workspace" / "chapters"
            chapters.mkdir(parents=True)
            (chapters / "01.md").write_text(
                "\n".join(
                    [
                        "# 01",
                        "| 文件要求 | 投标响应 | 响应程度 |",
                        "| --- | --- | --- |",
                        "| 要求A | 无法满足 | 负偏离 |",
                    ]
                ),
                encoding="utf-8",
            )
            items = deviation_compliance_items(root)
            self.assertTrue(any(i.get("severity") == "fatal" for i in items))


if __name__ == "__main__":
    unittest.main()
