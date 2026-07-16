from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from table_utils import collect_project_markdown_tables, header_map
from utils import project_root, stringify, write_json

POSITIVE = re.compile(r"(完全响应|无偏离|正偏离|优于|高于|满足|响应)")
NEGATIVE = re.compile(r"(负偏离|不满足|无法满足|低于|缺项|不响应|部分响应)")
EMPTYISH = re.compile(r"^(—|-|无|N/?A|NA|暂无)?$", re.IGNORECASE)


def _is_deviation_table(header: list[str], columns: dict[str, int]) -> bool:
    if {"requirement", "supplier", "response"}.issubset(columns):
        return True
    joined = "".join(header)
    return any(k in joined for k in ("偏离表", "响应程度", "技术偏离", "商务偏离", "响应表"))


def analyze_deviation_table(table: dict[str, Any]) -> dict[str, Any] | None:
    header = [stringify(c) for c in (table.get("header") or [])]
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    columns = header_map(header)
    if not _is_deviation_table(header, columns):
        return None
    # 至少要有响应/偏离列，或 要求+响应
    if "response" not in columns and not ({"requirement", "supplier"} <= set(columns)):
        return None

    analyzed: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for offset, row in enumerate(rows, start=1):
        if not isinstance(row, list):
            continue
        cells = [stringify(c) for c in row]
        if not any(cells):
            continue
        requirement = cells[columns["requirement"]] if "requirement" in columns and columns["requirement"] < len(cells) else ""
        supplier = cells[columns["supplier"]] if "supplier" in columns and columns["supplier"] < len(cells) else ""
        response = cells[columns["response"]] if "response" in columns and columns["response"] < len(cells) else ""
        note = cells[columns["note"]] if "note" in columns and columns["note"] < len(cells) else ""

        # 跳过空数据行
        if not requirement and not supplier and not response:
            continue

        empty_required = False
        negative = False
        inconsistent = False
        status = "ok"
        messages: list[str] = []

        if "requirement" in columns and not requirement.strip():
            empty_required = True
            messages.append("要求列为空")
        if "supplier" in columns and (not supplier.strip() or EMPTYISH.match(supplier.strip() or "")):
            empty_required = True
            messages.append("响应内容为空")
        if "response" in columns and (not response.strip() or EMPTYISH.match(response.strip() or "")):
            empty_required = True
            messages.append("偏离/响应程度为空")

        if response and NEGATIVE.search(response):
            negative = True
            messages.append(f"负偏离: {response}")
        if response and "无偏离" in response and supplier and NEGATIVE.search(supplier):
            inconsistent = True
            messages.append("声称无偏离但响应内容含负向表述")
        if response and "完全响应" in response and supplier and NEGATIVE.search(supplier):
            inconsistent = True
            messages.append("声称完全响应但响应内容含负向表述")
        if response and "无偏离" in response and note and NEGATIVE.search(note):
            inconsistent = True
            messages.append("无偏离与备注冲突")

        if empty_required or negative or inconsistent:
            status = "fail"
            issues.append(
                {
                    "row_index": offset,
                    "empty_required": empty_required,
                    "negative_deviation": negative,
                    "inconsistent": inconsistent,
                    "message": "；".join(messages),
                    "requirement": requirement[:120],
                    "supplier": supplier[:120],
                    "response": response[:80],
                }
            )
        analyzed.append(
            {
                "row_index": offset,
                "requirement": requirement,
                "supplier": supplier,
                "response": response,
                "note": note,
                "empty_required": empty_required,
                "negative_deviation": negative,
                "inconsistent": inconsistent,
                "ok": status == "ok",
            }
        )

    if not analyzed:
        return None
    return {
        "source": table.get("source"),
        "start_line": table.get("start_line"),
        "header": header,
        "columns": columns,
        "row_count": len(analyzed),
        "rows": analyzed,
        "fail_rows": issues,
        "ok": len(issues) == 0,
        "negative_count": sum(1 for r in analyzed if r.get("negative_deviation")),
        "empty_count": sum(1 for r in analyzed if r.get("empty_required")),
        "inconsistent_count": sum(1 for r in analyzed if r.get("inconsistent")),
    }


def check_deviation_tables(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    tables = collect_project_markdown_tables(root)
    analyzed: list[dict[str, Any]] = []
    fail_rows: list[dict[str, Any]] = []
    for table in tables:
        result = analyze_deviation_table(table)
        if not result:
            continue
        analyzed.append(result)
        for row in result.get("fail_rows") or []:
            item = dict(row)
            item["source"] = result.get("source")
            fail_rows.append(item)

    report = {
        "version": "1.0.0",
        "table_count": len(analyzed),
        "fail_row_count": len(fail_rows),
        "ok": len(fail_rows) == 0,
        "tables": analyzed,
        "fail_rows": fail_rows[:100],
    }
    output = root / "workspace" / "deviation_table_report.json"
    write_json(output, report)
    return report


def deviation_compliance_items(root: Path | None = None) -> list[dict[str, Any]]:
    from compliance_checker import make_check_item, STATUS_FAIL, STATUS_PASS, STATUS_SKIP, STATUS_WARN

    root = root or project_root()
    report = check_deviation_tables(root)
    items: list[dict[str, Any]] = []
    if report["table_count"] == 0:
        items.append(
            make_check_item(
                check_id="DEV-000",
                check_type="responsiveness",
                check_name="偏离表逐行检查",
                status=STATUS_SKIP,
                severity="info",
                requirement="正文存在技术/商务偏离表或响应表时逐行检查",
                suggestion="建议使用含“要求/响应/偏离”列的 Markdown 表格",
                confidence=0.5,
                need_manual_review=True,
            )
        )
        return items

    if report["ok"]:
        items.append(
            make_check_item(
                check_id="DEV-001",
                check_type="responsiveness",
                check_name="偏离表逐行检查",
                status=STATUS_PASS,
                severity="info",
                requirement="偏离表每行应填写完整且不得隐瞒负偏离",
                bid_evidence=[f"已检查 {report['table_count']} 张表，{sum(t.get('row_count', 0) for t in report['tables'])} 行"],
                confidence=0.9,
            )
        )
        return items

    neg = [r for r in report["fail_rows"] if r.get("negative_deviation")]
    empty = [r for r in report["fail_rows"] if r.get("empty_required")]
    inconsistent = [r for r in report["fail_rows"] if r.get("inconsistent")]

    if neg:
        items.append(
            make_check_item(
                check_id="DEV-001",
                check_type="responsiveness",
                check_name="偏离表负偏离检查",
                status=STATUS_FAIL,
                severity="fatal",
                requirement="不得存在未声明处理的负偏离",
                bid_evidence=[stringify(r.get("message")) for r in neg[:8]],
                suggestion="消除负偏离或按招标要求明示并评估废标风险",
                confidence=0.9,
                need_manual_review=True,
            )
        )
    if empty:
        items.append(
            make_check_item(
                check_id="DEV-002",
                check_type="responsiveness",
                check_name="偏离表空行检查",
                status=STATUS_FAIL,
                severity="critical",
                requirement="偏离/响应表关键列不得为空",
                bid_evidence=[stringify(r.get("message")) for r in empty[:8]],
                suggestion="补全要求、响应内容与响应程度",
                confidence=0.92,
                need_manual_review=True,
            )
        )
    if inconsistent:
        items.append(
            make_check_item(
                check_id="DEV-003",
                check_type="responsiveness",
                check_name="偏离表一致性检查",
                status=STATUS_WARN,
                severity="major",
                requirement="响应程度与响应内容不得矛盾",
                bid_evidence=[stringify(r.get("message")) for r in inconsistent[:8]],
                suggestion="统一正文与偏离表表述",
                confidence=0.8,
                need_manual_review=True,
            )
        )
    if not items:
        items.append(
            make_check_item(
                check_id="DEV-001",
                check_type="responsiveness",
                check_name="偏离表逐行检查",
                status=STATUS_WARN,
                severity="major",
                requirement="偏离表需人工复核",
                bid_evidence=[stringify(r.get("message")) for r in report["fail_rows"][:8]],
                need_manual_review=True,
                confidence=0.7,
            )
        )
    return items
