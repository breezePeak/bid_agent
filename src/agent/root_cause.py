from __future__ import annotations

"""Root-cause mapping and adapters: reports -> Issues."""


from pathlib import Path
from typing import Any

from agent.issues import make_issue, upsert_issues
from quality_gates import global_review_blocking_reasons
from utils import project_root, read_json, stringify


# IssueCode -> cause + default actions + revalidate
ROOT_CAUSE_TABLE: dict[str, dict[str, Any]] = {
    "NAME_INCONSISTENT": {
        "likely_cause_stage": "extract_facts",
        "actions": [
            {"type": "rerun_stage", "label": "重跑事实提取", "params": {"command": "extract-facts"}},
            {"type": "rewrite_chapters", "label": "定向改写相关章节", "params": {}},
            {"type": "revalidate_gate", "label": "重验全文审核", "params": {"command": "global-review"}},
        ],
        "revalidate": ["global-review"],
    },
    "UNCOVERED_SCORE": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "fix_coverage", "label": "按覆盖缺口改稿", "params": {"confirm_execute": False}},
            {"type": "rewrite_chapters", "label": "定向重写相关章节", "params": {}},
            {"type": "revalidate_gate", "label": "重验全文审核", "params": {"command": "global-review"}},
        ],
        "revalidate": ["build-score-coverage", "global-review"],
    },
    "CHAPTER_CONFLICT": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "rewrite_chapters", "label": "重写冲突章节", "params": {}},
            {"type": "revalidate_gate", "label": "重验全文审核", "params": {"command": "global-review"}},
        ],
        "revalidate": ["global-review"],
    },
    "FABRICATION_RISK": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "rewrite_chapters", "label": "改写风险表述", "params": {}},
            {"type": "upload_evidence", "label": "补充公司资料证据", "params": {}},
            {"type": "revalidate_gate", "label": "重验全文审核", "params": {"command": "global-review"}},
        ],
        "revalidate": ["global-review"],
    },
    "MISSING_CHAPTER": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "rewrite_chapters", "label": "补写缺失章节", "params": {}},
            {"type": "rerun_stage", "label": "重跑章节写作", "params": {"command": "write-all"}},
            {"type": "revalidate_gate", "label": "重验全文审核", "params": {"command": "global-review"}},
        ],
        "revalidate": ["global-review"],
    },
    "COMPLIANCE_BLOCK": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "fix_compliance", "label": "合规定向改稿计划", "params": {"confirm_execute": False}},
            {"type": "upload_evidence", "label": "人工补材料/响应", "params": {}},
            {"type": "revalidate_gate", "label": "重验合规检查", "params": {"command": "compliance-check"}},
        ],
        "revalidate": ["compliance-check"],
    },
    "CHAPTER_REVIEW_BLOCKER": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "rewrite_chapters", "label": "定向重写问题章节", "params": {}},
            {"type": "revalidate_gate", "label": "重验章节审核", "params": {"command": "review-fix-all"}},
        ],
        "revalidate": ["review-fix-all"],
    },
    "WRITE_CHAPTER_FAILED": {
        "likely_cause_stage": "write_chapters",
        "actions": [
            {"type": "rewrite_chapters", "label": "重试写作失败章节", "params": {}},
            {"type": "revalidate_gate", "label": "重跑写作", "params": {"command": "write-all"}},
        ],
        "revalidate": ["write-all"],
    },
    "OUTLINE_UNBOUND_SCORE": {
        "likely_cause_stage": "generate_outline",
        "actions": [
            {"type": "rerun_stage", "label": "重跑大纲生成", "params": {"command": "generate-outline"}},
            {"type": "revalidate_gate", "label": "重验大纲", "params": {"command": "generate-outline"}},
        ],
        "revalidate": ["generate-outline"],
    },
    "EMPTY_SCORE_POINTS": {
        "likely_cause_stage": "parse_score",
        "actions": [
            {"type": "rerun_stage", "label": "重跑评分解析", "params": {"command": "parse-score"}},
            {"type": "upload_evidence", "label": "检查 inputs/score.md", "params": {}},
        ],
        "revalidate": ["parse-score"],
    },
}


def _actions_for(code: str, extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    row = ROOT_CAUSE_TABLE.get(code) or {}
    actions = []
    for a in row.get("actions") or []:
        item = dict(a)
        params = dict(item.get("params") or {})
        if extra_params:
            params.update({k: v for k, v in extra_params.items() if v is not None})
        item["params"] = params
        actions.append(item)
    if not actions:
        actions = [{"type": "open_detail", "label": "查看节点详情", "params": {}}]
    return actions


def _cause(code: str, default_stage: str) -> str:
    row = ROOT_CAUSE_TABLE.get(code) or {}
    return str(row.get("likely_cause_stage") or default_stage)


def issues_from_global_review(review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(review, dict):
        return []
    issues: list[dict[str, Any]] = []

    mapping = [
        ("project_name_consistent", "NAME_INCONSISTENT", "项目名称前后不一致"),
        ("bidder_name_consistent", "NAME_INCONSISTENT", "投标人名称前后不一致"),
        ("service_period_consistent", "NAME_INCONSISTENT", "服务期前后不一致"),
        ("warranty_period_consistent", "NAME_INCONSISTENT", "质保期前后不一致"),
    ]
    for key, code, title in mapping:
        if key in review and review.get(key) is False:
            issues.append(
                make_issue(
                    stage_id="global_review",
                    command="global-review",
                    severity="block",
                    code=code,
                    title=title,
                    detail=f"字段 {key}=false",
                    target_type="global",
                    likely_cause_stage=_cause(code, "extract_facts"),
                    suggested_actions=_actions_for(code),
                    evidence={"field": key, "value": False},
                )
            )

    conflicts = review.get("chapter_conflicts")
    if isinstance(conflicts, list) and conflicts:
        issues.append(
            make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="CHAPTER_CONFLICT",
                title=f"章节冲突 {len(conflicts)} 项",
                detail=str(conflicts[:3]),
                target_type="chapter",
                target_ids=[],
                likely_cause_stage=_cause("CHAPTER_CONFLICT", "write_chapters"),
                suggested_actions=_actions_for("CHAPTER_CONFLICT"),
                evidence={"count": len(conflicts), "sample": conflicts[:5]},
            )
        )

    fabrication = review.get("fabrication_risks")
    if isinstance(fabrication, list) and fabrication:
        issues.append(
            make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="FABRICATION_RISK",
                title=f"编造风险 {len(fabrication)} 项",
                detail=str(fabrication[:3]),
                target_type="global",
                likely_cause_stage=_cause("FABRICATION_RISK", "write_chapters"),
                suggested_actions=_actions_for("FABRICATION_RISK"),
                evidence={"count": len(fabrication), "sample": fabrication[:5]},
            )
        )

    missing = review.get("missing_chapters")
    if isinstance(missing, list) and missing:
        ids = [stringify(x) for x in missing if stringify(x)]
        issues.append(
            make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="MISSING_CHAPTER",
                title=f"缺失章节 {len(ids)} 个",
                detail=", ".join(ids[:20]),
                target_type="chapter",
                target_ids=ids,
                likely_cause_stage=_cause("MISSING_CHAPTER", "write_chapters"),
                suggested_actions=_actions_for("MISSING_CHAPTER", {"chapter_ids": ids}),
                evidence={"chapter_ids": ids},
            )
        )

    uncovered = review.get("uncovered_score_points")
    if isinstance(uncovered, list) and uncovered:
        ids = [stringify(x) for x in uncovered if stringify(x)]
        issues.append(
            make_issue(
                stage_id="global_review",
                command="global-review",
                severity="block",
                code="UNCOVERED_SCORE",
                title=f"未覆盖评分点 {len(ids)} 个",
                detail=", ".join(ids[:20]) + ("…" if len(ids) > 20 else ""),
                target_type="score_point",
                target_ids=ids,
                likely_cause_stage=_cause("UNCOVERED_SCORE", "write_chapters"),
                suggested_actions=_actions_for("UNCOVERED_SCORE"),
                evidence={"score_point_ids": ids},
            )
        )

    # fallback from reasons text if structured empty but blocking_reasons present
    if not issues:
        for reason in global_review_blocking_reasons(review):
            issues.append(
                make_issue(
                    stage_id="global_review",
                    command="global-review",
                    severity="block",
                    code="GLOBAL_REVIEW_BLOCK",
                    title=str(reason)[:120],
                    detail=str(reason),
                    target_type="global",
                    likely_cause_stage="write_chapters",
                    suggested_actions=_actions_for("UNCOVERED_SCORE"),
                )
            )
    return issues


def issues_from_compliance_report(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    blocking = bool(report.get("blocking") or summary.get("blocking"))
    items = report.get("items") if isinstance(report.get("items"), list) else []
    issues: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        status = stringify(item.get("status"))
        severity_raw = stringify(item.get("severity")) or "info"
        if status not in {"fail", "warn"}:
            continue
        # only fail+fatal/critical/major as block when report blocking; else warn
        is_hard = severity_raw in {"fatal", "critical"} or (
            blocking and status == "fail" and severity_raw in {"fatal", "critical", "major"}
        )
        if status == "warn" and not is_hard:
            sev = "warn"
        elif is_hard or (blocking and status == "fail"):
            sev = "block"
        else:
            sev = "warn"

        check_id = stringify(item.get("check_id")) or "UNKNOWN"
        check_type = stringify(item.get("check_type")) or "unknown"
        title = f"{check_id} {stringify(item.get('check_name')) or check_type}".strip()
        issues.append(
            make_issue(
                stage_id="compliance_check",
                command="compliance-check",
                severity=sev,
                code=f"COMPLIANCE_{severity_raw.upper()}",
                title=title[:160],
                detail=stringify(item.get("requirement") or item.get("suggestion")),
                target_type="compliance_item",
                target_ids=[check_id],
                likely_cause_stage=_cause("COMPLIANCE_BLOCK", "write_chapters"),
                suggested_actions=_actions_for("COMPLIANCE_BLOCK"),
                evidence={
                    "check_id": check_id,
                    "check_type": check_type,
                    "severity": severity_raw,
                    "status": status,
                    "suggestion": item.get("suggestion"),
                    "auto_fixable": item.get("auto_fixable"),
                    "need_manual_review": item.get("need_manual_review"),
                },
            )
        )
    return issues


def sync_issues_from_global_review(root: Path | None = None, review: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    root = root or project_root()
    if review is None:
        path = root / "workspace" / "global_review.json"
        if not path.exists():
            return upsert_issues(root, [], replace_stage_id="global_review")
        review = read_json(path)
    issues = issues_from_global_review(review if isinstance(review, dict) else {})
    return upsert_issues(root, issues, replace_stage_id="global_review")


def sync_issues_from_compliance(root: Path | None = None, report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    root = root or project_root()
    if report is None:
        path = root / "workspace" / "compliance_report.json"
        if not path.exists():
            return upsert_issues(root, [], replace_stage_id="compliance_check")
        report = read_json(path)
    issues = issues_from_compliance_report(report if isinstance(report, dict) else {})
    return upsert_issues(root, issues, replace_stage_id="compliance_check")


def issues_from_review_fix(
    *,
    need_rewrite_ids: list[str] | None = None,
    need_evidence_ids: list[str] | None = None,
    stuck_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for cid in need_rewrite_ids or []:
        cid = stringify(cid)
        if not cid:
            continue
        issues.append(
            make_issue(
                stage_id="review_fix_chapters",
                command="review-fix-all",
                severity="block",
                code="CHAPTER_REVIEW_BLOCKER",
                title=f"章节 {cid} 审核未通过，需要改稿",
                detail=f"章节 {cid} need_rewrite=true",
                target_type="chapter",
                target_ids=[cid],
                likely_cause_stage=_cause("CHAPTER_REVIEW_BLOCKER", "write_chapters"),
                suggested_actions=_actions_for("CHAPTER_REVIEW_BLOCKER", {"chapter_ids": [cid]}),
            )
        )
    for cid in need_evidence_ids or []:
        cid = stringify(cid)
        if not cid:
            continue
        issues.append(
            make_issue(
                stage_id="review_fix_chapters",
                command="review-fix-all",
                severity="block",
                code="CHAPTER_REVIEW_BLOCKER",
                title=f"章节 {cid} 缺证据，无法自动改稿",
                detail=f"章节 {cid} need_evidence=true",
                target_type="chapter",
                target_ids=[cid],
                likely_cause_stage="select_contexts",
                suggested_actions=[
                    {"type": "upload_evidence", "label": "补充资料后重选上下文", "params": {}},
                    {"type": "rerun_stage", "label": "重跑上下文选择", "params": {"command": "select-context-all"}},
                    {"type": "rewrite_chapters", "label": "补写章节", "params": {"chapter_ids": [cid]}},
                ],
            )
        )
    for cid in stuck_ids or []:
        cid = stringify(cid)
        if not cid:
            continue
        issues.append(
            make_issue(
                stage_id="review_fix_chapters",
                command="review-fix-all",
                severity="block",
                code="CHAPTER_REVIEW_BLOCKER",
                title=f"章节 {cid} 审核卡住未收敛",
                detail=f"章节 {cid} stuck",
                target_type="chapter",
                target_ids=[cid],
                likely_cause_stage=_cause("CHAPTER_REVIEW_BLOCKER", "write_chapters"),
                suggested_actions=_actions_for("CHAPTER_REVIEW_BLOCKER", {"chapter_ids": [cid]}),
            )
        )
    return issues


def issues_from_write_failures(failed: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in failed or []:
        if not isinstance(item, dict):
            continue
        cid = stringify(item.get("chapter_id"))
        if not cid:
            continue
        err = stringify(item.get("error"))[:300]
        issues.append(
            make_issue(
                stage_id="write_chapters",
                command="write-all",
                severity="block",
                code="WRITE_CHAPTER_FAILED",
                title=f"章节 {cid} 写作失败",
                detail=err or "writing failed",
                target_type="chapter",
                target_ids=[cid],
                likely_cause_stage="write_chapters",
                suggested_actions=_actions_for("WRITE_CHAPTER_FAILED", {"chapter_ids": [cid]}),
                evidence={"error": err, "attempts": item.get("attempts")},
            )
        )
    return issues


def issues_from_outline_error(message: str, missing_ids: list[str] | None = None) -> list[dict[str, Any]]:
    ids = [stringify(x) for x in (missing_ids or []) if stringify(x)]
    return [
        make_issue(
            stage_id="generate_outline",
            command="generate-outline",
            severity="block",
            code="OUTLINE_UNBOUND_SCORE",
            title="大纲未绑定全部评分点",
            detail=message[:500],
            target_type="score_point",
            target_ids=ids,
            likely_cause_stage="generate_outline",
            suggested_actions=_actions_for("OUTLINE_UNBOUND_SCORE"),
            evidence={"missing_score_point_ids": ids},
        )
    ]


def issues_from_empty_score_points() -> list[dict[str, Any]]:
    return [
        make_issue(
            stage_id="parse_score",
            command="parse-score",
            severity="block",
            code="EMPTY_SCORE_POINTS",
            title="评分点解析结果为空",
            detail="score_points.json 为空，无法继续大纲与写作",
            target_type="global",
            likely_cause_stage="parse_score",
            suggested_actions=_actions_for("EMPTY_SCORE_POINTS"),
        )
    ]


def sync_issues_from_review_fix(
    root: Path | None = None,
    *,
    need_rewrite_ids: list[str] | None = None,
    need_evidence_ids: list[str] | None = None,
    stuck_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    root = root or project_root()
    issues = issues_from_review_fix(
        need_rewrite_ids=need_rewrite_ids,
        need_evidence_ids=need_evidence_ids,
        stuck_ids=stuck_ids,
    )
    return upsert_issues(root, issues, replace_stage_id="review_fix_chapters")


def sync_issues_from_write_failures(
    root: Path | None = None,
    failed: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    root = root or project_root()
    issues = issues_from_write_failures(failed)
    return upsert_issues(root, issues, replace_stage_id="write_chapters")
