from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import read_json, stringify


FORBIDDEN_CERTAINTY_PHRASES = ("已具备", "已提供", "已完成", "完全满足", "均已落实")
SAFE_HEDGE_PHRASES = ("拟", "将", "按要求", "待", "计划", "可", "如需", "随投标文件附后")


def anti_fabrication_enabled() -> bool:
    """Whether write-time anti-fabrication blockers are enabled."""
    import os

    value = str(os.environ.get("BID_AGENT_ANTI_FABRICATION_GATE", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


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
    if not anti_fabrication_enabled():
        return
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


def global_review_blocking_reasons(global_review: dict[str, Any] | None) -> list[str]:
    """全文审核阻断原因。有实质质量问题时阻断后续合规/出稿。"""
    if not isinstance(global_review, dict):
        return []
    reasons: list[str] = []

    consistency_fields = [
        ("project_name_consistent", "项目名称前后不一致"),
        ("bidder_name_consistent", "投标人名称前后不一致"),
        ("service_period_consistent", "服务期前后不一致"),
        ("warranty_period_consistent", "质保期前后不一致"),
    ]
    for key, label in consistency_fields:
        if key in global_review and global_review.get(key) is False:
            reasons.append(label)

    conflicts = global_review.get("chapter_conflicts")
    if isinstance(conflicts, list) and conflicts:
        reasons.append(f"章节冲突 {len(conflicts)} 项")

    fabrication = global_review.get("fabrication_risks")
    if isinstance(fabrication, list) and fabrication:
        reasons.append(f"编造风险 {len(fabrication)} 项")

    missing = global_review.get("missing_chapters")
    if isinstance(missing, list) and missing:
        reasons.append(f"缺失章节 {len(missing)} 项")

    uncovered = global_review.get("uncovered_score_points")
    if isinstance(uncovered, list) and uncovered:
        reasons.append(f"未覆盖评分点 {len(uncovered)} 个: {', '.join(str(x) for x in uncovered[:12])}"
                       + ("…" if len(uncovered) > 12 else ""))

    # 明确标注阻断
    if global_review.get("blocking") is True and not reasons:
        extra = global_review.get("blocking_reasons")
        if isinstance(extra, list) and extra:
            reasons.extend(str(x) for x in extra if str(x).strip())
        else:
            reasons.append("全文审核标记为 blocking")

    return reasons


def final_review_status(global_review: dict[str, Any]) -> str:
    if not isinstance(global_review, dict):
        return "ok"
    if global_review_blocking_reasons(global_review):
        return "error"
    return "warn" if bool(global_review.get("need_manual_review")) else "ok"


def validate_global_review_blocking(root: Path, *, required: bool = False) -> None:
    """全文审核质量门禁：存在不一致/冲突/编造风险/未覆盖评分点时阻断后续阶段。"""
    import os

    # allow opt-out: GLOBAL_REVIEW_GATE=0/false
    flag = str(os.environ.get("GLOBAL_REVIEW_GATE", "1")).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return

    report_path = root / "workspace" / "global_review.json"
    if not report_path.exists():
        if required:
            raise ValueError(f"全文审核报告不存在，无法通过质量门禁: {report_path}")
        return
    review = read_json(report_path)
    if not isinstance(review, dict):
        raise ValueError("global_review.json 必须是 JSON 对象")
    try:
        from agent.root_cause import sync_issues_from_global_review

        sync_issues_from_global_review(root, review)
    except Exception:
        pass
    reasons = global_review_blocking_reasons(review)
    if reasons:
        detail = "；".join(reasons)
        raise RuntimeError(
            "全文审核质量门禁阻断：存在未解决问题，请先处理 global-review 问题后再继续。"
            f" 原因: {detail}。报告: {report_path}"
        )


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
    try:
        from agent.root_cause import sync_issues_from_compliance

        sync_issues_from_compliance(root, report)
    except Exception:
        pass
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
    if raise_on_blocker and blockers and anti_fabrication_enabled():
        samples = [stringify(item.get("value") or item.get("description"))[:60] for item in blockers[:3]]
        raise ValueError(
            f"章节 {chapter_id} claim 防编造门禁失败（{len(blockers)} 项 blocker）: {samples}"
        )
    return result
