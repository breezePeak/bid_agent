from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from utils import read_text, stringify

NUMBER_RE = re.compile(
    r"^\s*(?:人民币|￥|¥|RMB)?\s*"
    r"([-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?)"
    r"\s*(万|亿)?\s*(?:元)?\s*$"
)
SEP_RE = re.compile(r"^:?-{2,}:?$")


def split_table_row(line: str) -> list[str]:
    text = (line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def is_separator_row(cells: Iterable[str]) -> bool:
    cells = list(cells)
    if not cells:
        return False
    return all(SEP_RE.match(re.sub(r"\s+", "", cell or "")) or set(cell or "") <= {"-", ":", " "} for cell in cells)


def is_table_line(line: str) -> bool:
    text = (line or "").strip()
    return text.startswith("|") and text.count("|") >= 2


def parse_number(text: str) -> float | None:
    raw = stringify(text).replace(",", "").replace("，", "").strip()
    if not raw:
        return None
    # 纯数字或金额
    match = NUMBER_RE.match(raw)
    if match:
        try:
            value = float(match.group(1))
        except Exception:
            return None
        unit = match.group(2) or ""
        if unit == "万":
            value *= 10000
        elif unit == "亿":
            value *= 100000000
        return value
    # 宽松：提取首个数字
    m = re.search(r"([-+]?\d+(?:\.\d+)?)", raw)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except Exception:
        return None
    if "万" in raw:
        value *= 10000
    elif "亿" in raw:
        value *= 100000000
    return value


def parse_markdown_tables(text: str, *, source: str = "") -> list[dict[str, Any]]:
    lines = (text or "").splitlines()
    tables: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not is_table_line(lines[index]):
            index += 1
            continue
        start = index
        block: list[str] = []
        while index < len(lines) and is_table_line(lines[index]):
            block.append(lines[index])
            index += 1
        if len(block) < 2:
            continue
        rows = [split_table_row(line) for line in block]
        if len(rows) >= 2 and is_separator_row(rows[1]):
            header = rows[0]
            body = rows[2:]
        else:
            header = rows[0]
            body = rows[1:]
        width = max(len(header), max((len(r) for r in body), default=0))
        header = header + [""] * (width - len(header))
        normalized_body: list[list[str]] = []
        for row in body:
            if not any(cell.strip() for cell in row):
                continue
            normalized_body.append(row + [""] * (width - len(row)))
        tables.append(
            {
                "source": source,
                "start_line": start + 1,
                "end_line": index,
                "header": header,
                "rows": normalized_body,
                "raw": "\n".join(block),
            }
        )
    return tables


def collect_docx_tables(root: Path) -> list[dict[str, Any]]:
    """从 final.docx 提取表格，统一成 markdown 表结构。"""
    docx_path = root / "outputs" / "final.docx"
    if not docx_path.exists() or docx_path.stat().st_size == 0:
        return []
    try:
        from docx import Document
    except Exception:
        return []
    try:
        document = Document(str(docx_path))
    except Exception:
        return []
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(document.tables, start=1):
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", (cell.text or "").strip()) for cell in row.cells]
            # 合并单元格可能导致重复，相邻去重
            deduped: list[str] = []
            for cell in cells:
                if deduped and deduped[-1] == cell:
                    continue
                deduped.append(cell)
            if any(deduped):
                rows.append(deduped)
        if len(rows) < 2:
            continue
        header = rows[0]
        body = rows[1:]
        tables.append(
            {
                "source": f"outputs/final.docx#table{index}",
                "start_line": index,
                "end_line": index,
                "header": header,
                "rows": body,
                "raw": "",
            }
        )
    return tables


def collect_project_markdown_tables(root: Path) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    final_md = root / "outputs" / "final.md"
    if final_md.exists():
        tables.extend(parse_markdown_tables(read_text(final_md), source="outputs/final.md"))
    chapters_dir = root / "workspace" / "chapters"
    if chapters_dir.exists():
        for path in sorted(chapters_dir.glob("*.md")):
            tables.extend(
                parse_markdown_tables(
                    read_text(path),
                    source=str(path.relative_to(root)).replace("\\", "/"),
                )
            )
    # 终稿 Word 表格（模板响应表/报价表）
    tables.extend(collect_docx_tables(root))
    return tables


def header_map(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        text = re.sub(r"\s+", "", stringify(cell))
        if not text:
            continue
        if any(k in text for k in ("数量", "工程量", "qty", "Qty", "QTY", "数量(个)", "数量（个）")):
            mapping.setdefault("qty", index)
        if any(k in text for k in ("单价", "含税单价", "未税单价", "unitprice", "UnitPrice")):
            mapping.setdefault("unit_price", index)
        if any(k in text for k in ("合计", "合价", "小计", "金额", "总价", "line_total", "合价(元)")):
            # 避免把“投标总价”当行合计时优先行合计关键词
            if "总价" in text and "合计" not in text and "合价" not in text and "小计" not in text:
                mapping.setdefault("grand_total_col", index)
            else:
                mapping.setdefault("line_total", index)
        if any(k in text for k in ("税率", "税额")):
            mapping.setdefault("tax", index)
        if any(k in text for k in ("序号", "编号")):
            mapping.setdefault("number", index)
        if any(k in text for k in ("名称", "项目", "服务", "货物", "设备", "品名")):
            mapping.setdefault("name", index)
        if any(k in text for k in ("文件要求", "采购需求", "服务指标", "技术要求", "规格参数", "招标要求")):
            mapping.setdefault("requirement", index)
        if any(k in text for k in ("供应商提供", "供应商响应", "投标响应", "响应内容", "响应指标", "投标人响应", "响应描述")):
            mapping.setdefault("supplier", index)
        if any(k in text for k in ("响应程度", "偏离情况", "偏离", "响应情况", "响应结论")):
            mapping.setdefault("response", index)
        if any(k in text for k in ("说明", "备注", "偏离说明")):
            mapping.setdefault("note", index)
    return mapping


def nearly_equal(a: float, b: float, *, rel: float = 0.005, abs_tol: float = 0.05) -> bool:
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b), 1.0))
