from __future__ import annotations

from pathlib import Path
from typing import Any

from context_budget import summarize_for_prompt, trim_text
from file_loader import load_global_facts, load_outline, load_score_points
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import (
    compact_json,
    find_chapter,
    listify,
    parse_json_from_model,
    project_root,
    read_json,
    read_nonempty_text,
    select_score_points,
    stringify,
    write_json,
)

REVIEW_CONTEXT_MAX_CHARS = 14000
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2}
REWRITE_SEVERITIES = frozenset({"blocker", "major"})
MAX_PRIORITY_FIXES = 5
BLOCKER_PROBLEM_TYPES = frozenset(
    {
        "fabrication",
        "fabricated_claim",
        "fact_conflict",
        "factual_conflict",
        "missing_score_coverage",
        "self_check_failed",
        "hallucination",
    }
)
MAJOR_PROBLEM_TYPES = frozenset(
    {
        "content_too_generic",
        "weak_response",
        "incomplete_coverage",
        "low_coverage",
        "missing_detail",
        "section_incomplete",
    }
)
EVIDENCE_PROBLEM_TYPES = frozenset(
    {
        "missing_evidence",
        "insufficient_materials",
        "need_materials",
        "no_supporting_evidence",
        "evidence_gap",
    }
)
WRITING_PROBLEM_TYPES = frozenset(
    {
        "content_too_generic",
        "weak_response",
        "incomplete_coverage",
        "low_coverage",
        "missing_detail",
        "section_incomplete",
        "fabrication",
        "fabricated_claim",
        "fact_conflict",
        "factual_conflict",
        "hallucination",
        "self_check_failed",
    }
)


def _chapter_from_job_or_outline(root: Path, chapter_id: str, outline: dict[str, Any]) -> dict[str, Any]:
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    if job_path.exists():
        job = read_json(job_path)
        return {
            "id": stringify(job.get("chapter_id")) or chapter_id,
            "title": stringify(job.get("chapter_title")),
            "score_point_ids": job.get("score_point_ids", []),
            "description": stringify(job.get("description")),
            "sections": job.get("sections", []),
        }
    return find_chapter(outline, chapter_id)


def _normalize_severity(value: Any, fallback: str = "major") -> str:
    severity = stringify(value).lower()
    if severity in SEVERITY_RANK:
        return severity
    return fallback


def _severity_for_problem_type(problem_type: str) -> str:
    normalized = problem_type.lower().strip()
    if normalized in BLOCKER_PROBLEM_TYPES or any(token in normalized for token in ("fabricat", "conflict", "hallucin")):
        return "blocker"
    if normalized in MAJOR_PROBLEM_TYPES or any(token in normalized for token in ("generic", "weak", "incomplete", "missing")):
        return "major"
    return "minor"


def _coverage_severity(covered: bool, coverage_level: str) -> str | None:
    level = coverage_level.lower().strip()
    if not covered or level == "none":
        return "blocker"
    if level in {"low", "unknown"}:
        return "major"
    return None


def _make_fix(
    *,
    fix_id: str,
    severity: str,
    source: str,
    target: str,
    action: str,
    acceptance: str,
    score_point_id: str = "",
    problem_type: str = "",
) -> dict[str, str]:
    return {
        "id": fix_id,
        "severity": severity,
        "source": source,
        "score_point_id": score_point_id,
        "problem_type": problem_type,
        "target": target,
        "action": action,
        "acceptance": acceptance,
    }


def _is_evidence_problem_type(problem_type: str) -> bool:
    normalized = problem_type.lower().strip()
    if normalized in EVIDENCE_PROBLEM_TYPES:
        return True
    return any(token in normalized for token in ("evidence", "material", "资料", "证明", "缺证"))


def _is_writing_fix(fix: dict[str, str]) -> bool:
    problem_type = stringify(fix.get("problem_type")).lower()
    source = stringify(fix.get("source")).lower()
    if _is_evidence_problem_type(problem_type):
        return False
    if source == "score_coverage" and stringify(fix.get("severity")) == "blocker":
        # 完全未覆盖：允许改稿尝试补响应，同时可标记 need_evidence
        return True
    if problem_type in WRITING_PROBLEM_TYPES:
        return True
    if source == "score_coverage":
        return True
    return stringify(fix.get("severity")) in REWRITE_SEVERITIES and not _is_evidence_problem_type(problem_type)


def fix_signature(fix: dict[str, Any]) -> str:
    return "|".join(
        [
            stringify(fix.get("severity")),
            stringify(fix.get("source")),
            stringify(fix.get("score_point_id")),
            stringify(fix.get("problem_type")),
            stringify(fix.get("target"))[:120],
        ]
    )


def rewrite_fix_signatures(review: dict[str, Any]) -> list[str]:
    signatures: list[str] = []
    for item in review.get("priority_fixes") or []:
        if not isinstance(item, dict):
            continue
        if stringify(item.get("severity")) not in REWRITE_SEVERITIES:
            continue
        signatures.append(fix_signature(item))
    return sorted(set(signatures))


def should_auto_rewrite(review: dict[str, Any]) -> bool:
    if stringify(review.get("rewrite_status")) == "stuck":
        return False
    if not bool(review.get("need_rewrite", False)):
        return False
    if stringify(review.get("rewrite_status")) == "need_evidence":
        return False
    if bool(review.get("need_evidence")) and not bool(review.get("has_writing_fixes", True)):
        return False
    return True


def _build_priority_fixes(
    score_coverage: list[dict[str, Any]],
    problems: list[dict[str, str]],
    raw_priority_fixes: Any,
) -> list[dict[str, str]]:
    fixes: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    def _append(fix: dict[str, str]) -> None:
        key = "|".join(
            [
                fix["severity"],
                fix["source"],
                fix["score_point_id"],
                fix["problem_type"],
                fix["target"],
            ]
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        fixes.append(fix)

    for item in listify(raw_priority_fixes):
        if not isinstance(item, dict):
            continue
        severity = _normalize_severity(item.get("severity"), "major")
        target = stringify(item.get("target")) or stringify(item.get("description"))
        action = stringify(item.get("action")) or stringify(item.get("suggestion"))
        if not target and not action:
            continue
        _append(
            _make_fix(
                fix_id=stringify(item.get("id")) or f"model_{len(fixes) + 1:02d}",
                severity=severity,
                source=stringify(item.get("source")) or "model",
                target=target or action,
                action=action or target,
                acceptance=stringify(item.get("acceptance")) or "复审时对应问题已消除或覆盖提升。",
                score_point_id=stringify(item.get("score_point_id")),
                problem_type=stringify(item.get("problem_type")) or stringify(item.get("type")),
            )
        )

    for item in score_coverage:
        severity = _coverage_severity(bool(item.get("covered")), stringify(item.get("coverage_level")) or "unknown")
        if severity is None:
            continue
        score_id = stringify(item.get("score_point_id"))
        suggestion = stringify(item.get("suggestion")) or f"补充评分点 {score_id} 的具体响应内容"
        evidence = stringify(item.get("evidence"))
        target = f"评分点 {score_id} 覆盖不足（{item.get('coverage_level') or 'unknown'}）"
        if evidence:
            target = f"{target}；现有证据：{evidence}"
        _append(
            _make_fix(
                fix_id=f"cov_{score_id or f'{len(fixes) + 1:02d}'}",
                severity=severity,
                source="score_coverage",
                target=target,
                action=suggestion,
                acceptance=f"评分点 {score_id} 达到 medium 及以上且 covered=true。",
                score_point_id=score_id,
                problem_type="incomplete_coverage",
            )
        )

    for index, item in enumerate(problems, start=1):
        problem_type = stringify(item.get("type")) or "unknown"
        severity = _normalize_severity(item.get("severity"), _severity_for_problem_type(problem_type))
        description = stringify(item.get("description")) or problem_type
        suggestion = stringify(item.get("suggestion")) or "按审核意见修订相关段落"
        _append(
            _make_fix(
                fix_id=f"prob_{index:02d}",
                severity=severity,
                source="problem",
                target=description,
                action=suggestion,
                acceptance="对应 problem 在复审中不再出现，或降为可接受表述。",
                problem_type=problem_type,
            )
        )

    fixes.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 9), item["id"]))
    return fixes[:MAX_PRIORITY_FIXES]


def normalize_review(data: Any, chapter: dict[str, Any], score_points: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("章节审核结果必须是 JSON 对象。")

    bound_ids = [str(item.get("id")) for item in score_points]
    raw_coverage = data.get("score_coverage") if isinstance(data.get("score_coverage"), list) else []
    coverage_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_coverage:
        if not isinstance(item, dict):
            continue
        score_id = stringify(item.get("score_point_id"))
        if score_id:
            coverage_by_id[score_id] = item

    score_coverage: list[dict[str, Any]] = []
    for score_id in bound_ids:
        item = coverage_by_id.get(score_id, {})
        score_coverage.append(
            {
                "score_point_id": score_id,
                "covered": bool(item.get("covered", False)),
                "coverage_level": stringify(item.get("coverage_level")) or "unknown",
                "evidence": stringify(item.get("evidence")),
                "suggestion": stringify(item.get("suggestion")),
            }
        )

    problems: list[dict[str, str]] = []
    for item in listify(data.get("problems")):
        if not isinstance(item, dict):
            continue
        problem_type = stringify(item.get("type")) or "unknown"
        problems.append(
            {
                "type": problem_type,
                "severity": _normalize_severity(item.get("severity"), _severity_for_problem_type(problem_type)),
                "description": stringify(item.get("description")),
                "suggestion": stringify(item.get("suggestion")),
            }
        )

    priority_fixes = _build_priority_fixes(score_coverage, problems, data.get("priority_fixes"))
    rewrite_fixes = [item for item in priority_fixes if item["severity"] in REWRITE_SEVERITIES]
    writing_fixes = [item for item in rewrite_fixes if _is_writing_fix(item)]
    need_rewrite = bool(rewrite_fixes)

    need_evidence = bool(data.get("need_evidence", False))
    if any((not item["covered"]) or item["coverage_level"] == "none" for item in score_coverage):
        need_evidence = True
    if any(_is_evidence_problem_type(item.get("type", "")) for item in problems):
        need_evidence = True
    if any(_is_evidence_problem_type(item.get("problem_type", "")) for item in priority_fixes):
        need_evidence = True

    max_severity = ""
    if priority_fixes:
        max_severity = min(priority_fixes, key=lambda item: SEVERITY_RANK.get(item["severity"], 9))["severity"]

    if need_rewrite and need_evidence and not writing_fixes:
        rewrite_status = "need_evidence"
    elif need_rewrite:
        rewrite_status = "need_rewrite"
    else:
        rewrite_status = "ok"

    return {
        "chapter_id": stringify(chapter.get("id")),
        "chapter_title": stringify(chapter.get("title")),
        "score_coverage": score_coverage,
        "problems": problems,
        "priority_fixes": priority_fixes,
        "max_severity": max_severity,
        "need_rewrite": need_rewrite,
        "need_evidence": need_evidence,
        "has_writing_fixes": bool(writing_fixes),
        "rewrite_status": rewrite_status,
    }


def review_chapter_markdown(
    chapter: dict[str, Any],
    related_score_points: list[dict[str, Any]],
    global_facts: dict[str, Any],
    chapter_markdown: str,
    root: Path | None = None,
    debug_name: str | None = None,
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    prompt = load_agent_prompt(root, "chapter_reviewer")
    chapter_id = stringify(chapter.get("id"))
    chapter_excerpt = trim_text(chapter_markdown, REVIEW_CONTEXT_MAX_CHARS // 2)
    focus = focus if isinstance(focus, dict) else {}
    prior_fixes = focus.get("prior_priority_fixes") if isinstance(focus.get("prior_priority_fixes"), list) else []
    rewrite_log = focus.get("rewrite_log") if isinstance(focus.get("rewrite_log"), dict) else {}
    focused_review = bool(prior_fixes or rewrite_log)

    focus_section = ""
    if focused_review:
        focus_section = (
            "## 定向复审焦点\n\n"
            "请优先验收上轮改稿是否落实下列修复项；未再出现的问题不要重复扩写。\n\n"
            f"### 上轮优先修复项\n\n{compact_json(prior_fixes)}\n\n"
            f"### 改稿摘要\n\n{compact_json(rewrite_log)}\n\n"
        )

    with agent_run(
        root,
        "chapter_review",
        "chapter_reviewer",
        input_summary={
            "chapter_id": chapter_id,
            "score_point_count": len(related_score_points),
            "chapter_chars": len(chapter_markdown),
            "focused_review": focused_review,
            "prior_fix_count": len(prior_fixes),
        },
        chapter_id=chapter_id,
        temperature=0.1,
    ):
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请审核当前章节是否覆盖绑定评分点，并检查空泛、编造和事实冲突问题。"
                        "请按 blocker/major/minor 分级，并输出最多 5 条 priority_fixes；"
                        "仅 blocker/major 才将 need_rewrite 设为 true。"
                        "若资料不足以支撑评分点，将 need_evidence 设为 true。\n\n"
                        f"{focus_section}"
                        "## 当前章节信息\n\n"
                        f"{compact_json(chapter)}\n\n"
                        "## 绑定评分点\n\n"
                        f"{compact_json(related_score_points)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}\n\n"
                        "## 上下文摘要\n\n"
                        f"{summarize_for_prompt({'max_context_chars': REVIEW_CONTEXT_MAX_CHARS, 'chapter_excerpt_chars': len(chapter_excerpt), 'focused_review': focused_review}, 800)}\n\n"
                        "## 章节正文\n\n"
                        f"{chapter_excerpt}"
                    ),
                },
            ],
            temperature=0.1,
        )
    debug_file = debug_name or f"debug_review_{chapter_id}_raw.txt"
    data = parse_json_from_model(raw, root / "workspace" / debug_file)
    review = normalize_review(data, chapter, related_score_points)
    return _merge_claim_and_compliance_findings(root, chapter_id, chapter_markdown, review)


def _merge_claim_and_compliance_findings(
    root: Path,
    chapter_id: str,
    chapter_markdown: str,
    review: dict[str, Any],
) -> dict[str, Any]:
    """把规则型 claim 校验 + 合规回灌线索并入审核结果，驱动改稿。"""
    updated = dict(review)
    problems = list(updated.get("problems") or []) if isinstance(updated.get("problems"), list) else []
    priority_fixes = (
        list(updated.get("priority_fixes") or []) if isinstance(updated.get("priority_fixes"), list) else []
    )
    existing_fix_ids = {
        stringify(item.get("id")) for item in priority_fixes if isinstance(item, dict)
    }
    existing_problem_desc = {
        stringify(item.get("description")) for item in problems if isinstance(item, dict)
    }

    # 1) claim 防编造
    try:
        from claim_validator import (
            claim_findings_as_priority_fixes,
            claim_findings_as_review_problems,
            validate_chapter_claims,
        )

        claim_result = validate_chapter_claims(root, chapter_id, chapter_markdown)
        for problem in claim_findings_as_review_problems(claim_result):
            desc = stringify(problem.get("description"))
            if desc and desc not in existing_problem_desc:
                problems.append(problem)
                existing_problem_desc.add(desc)
        for fix in claim_findings_as_priority_fixes(claim_result):
            fix_id = stringify(fix.get("id"))
            if fix_id and fix_id not in existing_fix_ids:
                priority_fixes.append(fix)
                existing_fix_ids.add(fix_id)
        updated["claim_validation"] = {
            "finding_count": claim_result.get("finding_count", 0),
            "blocker_count": claim_result.get("blocker_count", 0),
            "ok": claim_result.get("ok", True),
        }
    except Exception as exc:
        updated["claim_validation"] = {"ok": False, "error": str(exc)}

    # 2) 合规回灌线索
    try:
        from compliance_feedback import compliance_hints_for_chapter

        for fix in compliance_hints_for_chapter(root, chapter_id):
            if not isinstance(fix, dict):
                continue
            fix_id = stringify(fix.get("id"))
            if fix_id and fix_id not in existing_fix_ids:
                priority_fixes.append(fix)
                existing_fix_ids.add(fix_id)
                desc = f"合规项 {fix.get('check_id')}: {fix.get('target')}"
                if desc not in existing_problem_desc:
                    problems.append(
                        {
                            "type": stringify(fix.get("problem_type")) or "compliance",
                            "severity": "blocker" if fix.get("severity") == "blocker" else "major",
                            "description": desc,
                            "suggestion": stringify(fix.get("action")),
                        }
                    )
                    existing_problem_desc.add(desc)
    except Exception:
        pass

    # 截断，保持契约
    priority_fixes = priority_fixes[:8]
    problems = problems[:30]
    rewrite_fixes = [
        item
        for item in priority_fixes
        if isinstance(item, dict) and stringify(item.get("severity")) in REWRITE_SEVERITIES
    ]
    writing_fixes = [item for item in rewrite_fixes if _is_writing_fix(item)]
    need_rewrite = bool(rewrite_fixes) or bool(updated.get("need_rewrite"))
    need_evidence = bool(updated.get("need_evidence", False))
    if any(_is_evidence_problem_type(stringify(item.get("problem_type"))) for item in priority_fixes if isinstance(item, dict)):
        need_evidence = True
    if any(_is_evidence_problem_type(stringify(item.get("type"))) for item in problems if isinstance(item, dict)):
        need_evidence = True

    if need_rewrite and need_evidence and not writing_fixes:
        rewrite_status = "need_evidence"
    elif need_rewrite:
        rewrite_status = "need_rewrite"
    else:
        rewrite_status = stringify(updated.get("rewrite_status")) or "ok"

    max_severity = stringify(updated.get("max_severity"))
    if priority_fixes:
        top = min(
            (item for item in priority_fixes if isinstance(item, dict)),
            key=lambda item: SEVERITY_RANK.get(stringify(item.get("severity")), 9),
            default=None,
        )
        if isinstance(top, dict):
            max_severity = stringify(top.get("severity"))

    updated["problems"] = problems
    updated["priority_fixes"] = priority_fixes
    updated["need_rewrite"] = need_rewrite
    updated["need_evidence"] = need_evidence
    updated["has_writing_fixes"] = bool(writing_fixes)
    updated["rewrite_status"] = rewrite_status
    if max_severity:
        updated["max_severity"] = max_severity
    return updated


def _load_review_focus(root: Path, chapter_id: str) -> dict[str, Any]:
    rewrite_path = root / "workspace" / "rewrites" / f"{chapter_id}_rewrite_log.json"
    if not rewrite_path.exists():
        return {}
    try:
        rewrite_log = read_json(rewrite_path)
    except Exception:
        return {}
    if not isinstance(rewrite_log, dict):
        return {}
    prior_fixes = rewrite_log.get("priority_fixes")
    if not isinstance(prior_fixes, list):
        prior_fixes = []
    return {
        "prior_priority_fixes": prior_fixes,
        "rewrite_log": {
            "chapter_id": stringify(rewrite_log.get("chapter_id")) or chapter_id,
            "priority_fix_ids": rewrite_log.get("priority_fix_ids") or [],
            "priority_fix_count": rewrite_log.get("priority_fix_count"),
            "old_length": rewrite_log.get("old_length"),
            "new_length": rewrite_log.get("new_length"),
            "rewrite_time": rewrite_log.get("rewrite_time"),
            "review_max_severity": rewrite_log.get("review_max_severity"),
        },
    }


def mark_review_stuck(
    review: dict[str, Any],
    *,
    stuck_signatures: list[str],
    rounds_unchanged: int,
) -> dict[str, Any]:
    updated = dict(review)
    updated["rewrite_status"] = "stuck"
    updated["stuck"] = True
    updated["stuck_fix_signatures"] = stuck_signatures
    updated["stuck_rounds"] = rounds_unchanged
    updated["need_rewrite"] = True
    return updated


def review_chapter(chapter_id: str, root: Path | None = None, focus: dict[str, Any] | None = None) -> Path:
    root = root or project_root()
    outline = load_outline(root)
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    chapter = _chapter_from_job_or_outline(root, chapter_id, outline)
    related_score_points = select_score_points(score_points, chapter.get("score_point_ids", []))
    chapter_path = root / "workspace" / "chapters" / f"{chapter['id']}.md"
    chapter_markdown = read_nonempty_text(chapter_path, f"章节文件 {chapter_path}")
    resolved_focus = focus if focus is not None else _load_review_focus(root, chapter_id)
    review = review_chapter_markdown(
        chapter,
        related_score_points,
        global_facts,
        chapter_markdown,
        root,
        focus=resolved_focus,
    )

    output_path = root / "workspace" / "reviews" / f"{chapter['id']}_review.json"
    write_json(output_path, review)
    status = stringify(review.get("rewrite_status")) or ("need_rewrite" if review.get("need_rewrite") else "ok")
    print(f"[完成] 已审核章节 {chapter['id']} {chapter['title']} ({status}): {output_path}")
    return output_path


def review_all(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    output_paths: list[Path] = []
    jobs_dir = root / "workspace" / "jobs"
    job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.exists() else []
    if job_files:
        for job_file in job_files:
            output_paths.append(review_chapter(job_file.stem, root))
        return output_paths

    outline = load_outline(root)
    for chapter in outline.get("chapters", []):
        output_paths.append(review_chapter(stringify(chapter.get("id")), root))
    return output_paths
