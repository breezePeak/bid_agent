from __future__ import annotations

from pathlib import Path
from typing import Any

from table_utils import (
    collect_project_markdown_tables,
    header_map,
    nearly_equal,
    parse_number,
)
from utils import project_root, stringify, write_json


def _is_price_table(header: list[str], columns: dict[str, int]) -> bool:
    if "qty" in columns and "unit_price" in columns:
        return True
    if "unit_price" in columns and "line_total" in columns:
        return True
    joined = "".join(header)
    return any(k in joined for k in ("分项报价", "报价明细", "单价", "合价", "数量"))


def analyze_price_table(table: dict[str, Any]) -> dict[str, Any] | None:
    header = table.get("header") if isinstance(table.get("header"), list) else []
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    columns = header_map([stringify(c) for c in header])
    if not _is_price_table([stringify(c) for c in header], columns):
        return None
    if "qty" not in columns and "unit_price" not in columns:
        return None

    analyzed_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    computed_sum = 0.0
    declared_sum = 0.0
    usable_rows = 0

    for offset, row in enumerate(rows, start=1):
        if not isinstance(row, list):
            continue
        cells = [stringify(c) for c in row]
        qty = parse_number(cells[columns["qty"]]) if "qty" in columns and columns["qty"] < len(cells) else None
        unit_price = (
            parse_number(cells[columns["unit_price"]])
            if "unit_price" in columns and columns["unit_price"] < len(cells)
            else None
        )
        declared_total = (
            parse_number(cells[columns["line_total"]])
            if "line_total" in columns and columns["line_total"] < len(cells)
            else None
        )
        # 跳过合计行
        row_text = "".join(cells)
        if any(k in row_text for k in ("合计", "总计", "总价", "小计")) and qty is None:
            if declared_total is not None:
                declared_sum = max(declared_sum, declared_total)
            continue
        if qty is None and unit_price is None and declared_total is None:
            continue
        usable_rows += 1
        computed = None
        ok = True
        delta = None
        if qty is not None and unit_price is not None:
            computed = qty * unit_price
            computed_sum += computed
            if declared_total is not None:
                ok = nearly_equal(computed, declared_total)
                delta = declared_total - computed
                if not ok:
                    issues.append(
                        {
                            "row_index": offset,
                            "type": "qty_unit_mismatch",
                            "message": f"第{offset}行 数量×单价={computed:.4f} 与合计={declared_total:.4f} 不一致",
                            "qty": qty,
                            "unit_price": unit_price,
                            "declared_total": declared_total,
                            "computed_total": computed,
                        }
                    )
            declared_sum += declared_total if declared_total is not None else computed
        elif declared_total is not None:
            declared_sum += declared_total
            if qty is None or unit_price is None:
                issues.append(
                    {
                        "row_index": offset,
                        "type": "missing_qty_or_price",
                        "message": f"第{offset}行缺少数量或单价，无法验算",
                    }
                )
                ok = False
        analyzed_rows.append(
            {
                "row_index": offset,
                "cells": cells,
                "qty": qty,
                "unit_price": unit_price,
                "declared_total": declared_total,
                "computed_total": computed,
                "ok": ok,
                "delta": delta,
            }
        )

    if usable_rows == 0:
        return None

    # 行合计交叉验证：若有明确行合计列汇总
    row_ok = all(bool(r.get("ok", True)) for r in analyzed_rows)
    report = {
        "source": table.get("source"),
        "start_line": table.get("start_line"),
        "header": header,
        "columns": columns,
        "row_count": usable_rows,
        "rows": analyzed_rows,
        "computed_sum": computed_sum,
        "declared_sum": declared_sum,
        "ok": row_ok and not issues,
        "issues": issues,
    }
    if computed_sum > 0 and declared_sum > 0 and not nearly_equal(computed_sum, declared_sum, rel=0.01, abs_tol=1.0):
        # 仅当两边都来自行数据时提示
        if "line_total" in columns and "qty" in columns and "unit_price" in columns:
            report["ok"] = False
            report["issues"].append(
                {
                    "type": "sum_mismatch",
                    "message": f"分项计算合计 {computed_sum:.2f} 与申报合计 {declared_sum:.2f} 差异较大",
                    "computed_sum": computed_sum,
                    "declared_sum": declared_sum,
                }
            )
    return report


def parse_price_tables(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    tables = collect_project_markdown_tables(root)
    analyzed: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for table in tables:
        result = analyze_price_table(table)
        if not result:
            continue
        analyzed.append(result)
        for issue in result.get("issues") or []:
            item = dict(issue)
            item["source"] = result.get("source")
            issues.append(item)

    report = {
        "version": "1.0.0",
        "table_count": len(analyzed),
        "issue_count": len(issues),
        "ok": len(issues) == 0,
        "tables": analyzed,
        "issues": issues[:100],
    }
    output = root / "workspace" / "price_table_report.json"
    write_json(output, report)
    return report


def price_table_compliance_items(root: Path | None = None) -> list[dict[str, Any]]:
    """转换为 compliance check items。"""
    from compliance_checker import make_check_item, STATUS_FAIL, STATUS_PASS, STATUS_SKIP, STATUS_WARN

    root = root or project_root()
    report = parse_price_tables(root)
    items: list[dict[str, Any]] = []
    if report["table_count"] == 0:
        items.append(
            make_check_item(
                check_id="PRICE-CALC-000",
                check_type="commercial",
                check_name="报价表确定性验算",
                status=STATUS_SKIP,
                severity="info",
                requirement="正文中存在可解析的数量/单价/合价表时进行验算",
                suggestion="若有分项报价表，请使用 Markdown 表格并包含数量、单价、合价列",
                confidence=0.5,
                need_manual_review=True,
            )
        )
        return items

    if report["ok"]:
        items.append(
            make_check_item(
                check_id="PRICE-CALC-001",
                check_type="commercial",
                check_name="报价表数量×单价验算",
                status=STATUS_PASS,
                severity="info",
                requirement="数量×单价应等于行合计",
                bid_evidence=[f"已验算 {report['table_count']} 张报价表"],
                confidence=0.95,
                auto_fixable=False,
            )
        )
        return items

    # 有问题
    mismatch = [i for i in report["issues"] if i.get("type") == "qty_unit_mismatch"]
    missing = [i for i in report["issues"] if i.get("type") == "missing_qty_or_price"]
    sums = [i for i in report["issues"] if i.get("type") == "sum_mismatch"]
    if mismatch or sums:
        items.append(
            make_check_item(
                check_id="PRICE-CALC-001",
                check_type="commercial",
                check_name="报价表数量×单价验算",
                status=STATUS_FAIL,
                severity="fatal" if mismatch else "critical",
                requirement="数量×单价应等于行合计，分项合计应一致",
                bid_evidence=[stringify(i.get("message")) for i in (mismatch + sums)[:8]],
                suggestion="修正报价表计算错误后再提交",
                confidence=0.95,
                need_manual_review=True,
                extra={"issue_count": len(report["issues"])},
            )
        )
    if missing and not mismatch:
        items.append(
            make_check_item(
                check_id="PRICE-CALC-002",
                check_type="commercial",
                check_name="报价表字段完整性",
                status=STATUS_WARN,
                severity="major",
                requirement="报价行应同时具备数量与单价",
                bid_evidence=[stringify(i.get("message")) for i in missing[:8]],
                suggestion="补全数量/单价列以便确定性验算",
                confidence=0.8,
                need_manual_review=True,
            )
        )
    return items
