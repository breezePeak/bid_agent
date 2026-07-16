from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import read_json, stringify


FORBIDDEN_CERTAINTY_PHRASES = ("已具备", "已提供", "已完成", "完全满足", "均已落实")
SAFE_HEDGE_PHRASES = ("拟", "将", "按要求", "待", "计划", "可", "如需", "随投标文件附后")


def validate_outline_score_coverage(outline: dict[str, Any], score_points: list[dict[str, Any]]) -> None:
    known = {stringify(item.get("id")) for item in score_points if stringify(item.get("id"))}
    covered: set[str] = set()
    for chapter in outline.get("chapters", []):
        if not isinstance(chapter, dict):
            continue
        for score_id in chapter.get("score_point_ids", []):
            score_id = stringify(score_id)
            if score_id:
                covered.add(score_id)
    missing = sorted(score_id for score_id in known if score_id not in covered)
    if missing:
        raise ValueError(f"大纲质量门禁失败：仍有评分点未绑定章节: {missing}")


def validate_weak_evidence_language(job: dict[str, Any], chapter_markdown: str) -> None:
    weak_terms: list[str] = []
    for task in job.get("template_tasks", []):
        if not isinstance(task, dict):
            continue
        if stringify(task.get("status")) not in {"weak", "missing"}:
            continue
        for key in ("title", "label", "semantic_key"):
            term = stringify(task.get(key))
            if term and term not in weak_terms:
                weak_terms.append(term)
    if not weak_terms:
        return

    lines = [line.strip() for line in chapter_markdown.splitlines() if line.strip()]
    suspicious: list[str] = []
    for line in lines:
        if not any(term in line for term in weak_terms):
            continue
        if any(hedge in line for hedge in SAFE_HEDGE_PHRASES):
            continue
        if any(phrase in line for phrase in FORBIDDEN_CERTAINTY_PHRASES):
            suspicious.append(line[:120])
    if suspicious:
        raise ValueError(f"章节质量门禁失败：弱证据模板任务被写成既成事实: {suspicious[:3]}")


def validate_template_fill_report(root: Path) -> None:
    report_path = root / "workspace" / "template_fill_report.json"
    if not report_path.exists():
        return
    report = read_json(report_path)
    if not isinstance(report, dict):
        return
    if not bool(report.get("ok", True)):
        raise ValueError(f"Word 模板填充门禁失败，请检查: {report_path}")


def final_review_status(global_review: dict[str, Any]) -> str:
    if not isinstance(global_review, dict):
        return "ok"
    return "warn" if bool(global_review.get("need_manual_review")) else "ok"


def compliance_review_status(compliance_report: dict[str, Any]) -> str:
    if not isinstance(compliance_report, dict):
        return "ok"
    summary = compliance_report.get("summary") if isinstance(compliance_report.get("summary"), dict) else {}
    blocking = bool(compliance_report.get("blocking") or summary.get("blocking"))
    if blocking:
        return "error"
    need_manual = bool(compliance_report.get("need_manual_review") or summary.get("need_manual_review"))
    return "warn" if need_manual else "ok"


def validate_compliance_blocking(root: Path, *, required: bool = True) -> None:
    """交付级门禁：fatal/critical 失败阻止流程成功完成。"""
    report_path = root / "workspace" / "compliance_report.json"
    if not report_path.exists():
        if required:
            raise ValueError(f"合规检查报告不存在，无法通过交付门禁: {report_path}")
        return
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise ValueError("compliance_report.json 必须是 JSON 对象")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    blocking = bool(report.get("blocking") or summary.get("blocking"))
    if blocking:
        raise RuntimeError(f"专项合规检查阻断交付，请修复后重跑 compliance-check: {report_path}")


def validate_chapter_claims_gate(
    root: Path,
    chapter_id: str,
    chapter_markdown: str,
    *,
    raise_on_blocker: bool = True,
) -> dict[str, Any]:
    """章节 claim 防编造门禁：金额/资质/业绩既成事实必须能在公司资料中找到支撑。"""
    from claim_validator import validate_chapter_claims

    result = validate_chapter_claims(root, chapter_id, chapter_markdown)
    blockers = [
        item
        for item in (result.get("findings") or [])
        if isinstance(item, dict) and stringify(item.get("severity")) == "blocker"
    ]
    if raise_on_blocker and blockers:
        samples = [stringify(item.get("value") or item.get("description"))[:60] for item in blockers[:3]]
        raise ValueError(
            f"章节 {chapter_id} claim 防编造门禁失败（{len(blockers)} 项 blocker）: {samples}"
        )
    return result
