from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import project_root, read_json, stringify, write_json

REWRITEABLE_TYPES = {
    "mandatory_param",
    "completeness",
    "consistency",
    "commercial",
    "bid_validity",
    "qualification",
    "disqualification",
    "bid_bond",
}
# 签章等无法靠改文案解决，只进人工复核
MANUAL_ONLY_TYPES = {"signature", "final_gate", "system"}


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def _load_chapter_ids(root: Path) -> list[str]:
    chapters_dir = root / "workspace" / "chapters"
    if not chapters_dir.exists():
        return []
    return [path.stem for path in sorted(chapters_dir.glob("*.md"))]


def _guess_chapter_ids(root: Path, item: dict[str, Any], chapter_ids: list[str]) -> list[str]:
    if not chapter_ids:
        return []
    haystacks: list[str] = [
        stringify(item.get("requirement")),
        stringify(item.get("suggestion")),
        stringify(item.get("check_name")),
    ]
    for evidence in item.get("bid_evidence") or []:
        haystacks.append(stringify(evidence))
    text = "\n".join(haystacks)

    # 若线索太泛，默认回灌到全部章节的人工说明（仅 major+）
    hits = [cid for cid in chapter_ids if cid and cid in text]
    if hits:
        return hits

    # 按关键词粗分到可能章节标题
    jobs_dir = root / "workspace" / "jobs"
    scored: list[tuple[int, str]] = []
    if jobs_dir.exists():
        for job_path in jobs_dir.glob("*.json"):
            try:
                job = read_json(job_path)
            except Exception:
                continue
            if not isinstance(job, dict):
                continue
            chapter_id = stringify(job.get("chapter_id")) or job_path.stem
            title = stringify(job.get("chapter_title"))
            desc = stringify(job.get("description"))
            blob = f"{title}\n{desc}"
            score = 0
            for token in ("报价", "商务", "资格", "偏离", "服务", "技术", "人员", "业绩", "承诺", "签章", "保证金"):
                if token in stringify(item.get("check_name")) + stringify(item.get("requirement")) and token in blob:
                    score += 2
            if score:
                scored.append((score, chapter_id))
    if scored:
        scored.sort(reverse=True)
        return [scored[0][1]]

    # 一致性/完整性问题默认挂到第一章，避免空回灌
    return [chapter_ids[0]] if chapter_ids else []


def sync_compliance_findings(root: Path | None = None) -> Path:
    """将 compliance_report 失败项回灌到 manual_review 与章节改稿线索。"""
    root = root or project_root()
    report_path = root / "workspace" / "compliance_report.json"
    report = _safe_read_json(report_path) or {}
    items = report.get("items") if isinstance(report, dict) and isinstance(report.get("items"), list) else []
    chapter_ids = _load_chapter_ids(root)

    manual_dir = root / "workspace" / "manual_review"
    manual_dir.mkdir(parents=True, exist_ok=True)

    compliance_items: dict[str, Any] = {}
    rewrite_hints: dict[str, list[dict[str, Any]]] = {cid: [] for cid in chapter_ids}
    global_actions: dict[str, Any] = {}

    for raw in items:
        if not isinstance(raw, dict):
            continue
        status = stringify(raw.get("status"))
        severity = stringify(raw.get("severity")) or "info"
        if status not in {"fail", "warn"}:
            continue
        if severity not in {"fatal", "critical", "major"}:
            continue

        check_id = stringify(raw.get("check_id")) or "UNKNOWN"
        check_type = stringify(raw.get("check_type")) or "unknown"
        check_name = stringify(raw.get("check_name")) or check_id
        suggestion = stringify(raw.get("suggestion")) or "请人工核对并修复"
        requirement = stringify(raw.get("requirement"))

        compliance_items[check_id] = {
            "item_id": check_id,
            "status": "pending",
            "check_type": check_type,
            "check_name": check_name,
            "severity": severity,
            "requirement": requirement,
            "operator_note": "",
            "operator_instruction": suggestion,
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        }

        if check_type in MANUAL_ONLY_TYPES or severity == "fatal" and check_type == "signature":
            global_actions[check_id] = {
                "item_id": check_id,
                "status": "pending",
                "risk_type": "compliance",
                "target_scope": check_name,
                "operator_instruction": suggestion,
                "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            }
            continue

        if check_type not in REWRITEABLE_TYPES and status != "fail":
            continue

        targets = _guess_chapter_ids(root, raw, chapter_ids)
        fix = {
            "id": f"COMP-{check_id}",
            "severity": "blocker" if severity in {"fatal", "critical"} else "major",
            "problem_type": f"compliance_{check_type}",
            "target": check_name,
            "action": suggestion,
            "acceptance": f"满足合规项 {check_id}",
            "check_id": check_id,
            "check_type": check_type,
            "requirement": requirement[:300],
        }
        for chapter_id in targets:
            rewrite_hints.setdefault(chapter_id, []).append(fix)

    # 合并写入 manual_review/compliance_actions.json
    write_json(
        manual_dir / "compliance_actions.json",
        {
            "items": compliance_items,
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "compliance_report.json",
        },
    )

    # 合并进 global_review_actions（不覆盖人工已处理项）
    existing_global = _safe_read_json(manual_dir / "global_review_actions.json") or {}
    existing_items = existing_global.get("items") if isinstance(existing_global.get("items"), dict) else {}
    for key, value in global_actions.items():
        prev = existing_items.get(key)
        if isinstance(prev, dict) and stringify(prev.get("status")) in {"accepted", "resolved"}:
            continue
        existing_items[key] = value
    write_json(
        manual_dir / "global_review_actions.json",
        {
            "items": existing_items,
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )

    # 章节改稿线索
    hints_path = root / "workspace" / "compliance_rewrite_hints.json"
    # 限制每章最多 8 条
    compact_hints = {
        chapter_id: fixes[:8]
        for chapter_id, fixes in rewrite_hints.items()
        if fixes
    }
    write_json(
        hints_path,
        {
            "version": "1.0.0",
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "chapters": compact_hints,
            "summary": {
                "chapter_count": len(compact_hints),
                "fix_count": sum(len(v) for v in compact_hints.values()),
                "manual_only_count": len(global_actions),
                "compliance_item_count": len(compliance_items),
            },
        },
    )

    # 若已有 review，把合规 fix 注入 priority_fixes / problems，驱动自动改稿
    injected = 0
    for chapter_id, fixes in compact_hints.items():
        review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
        if not review_path.exists():
            continue
        try:
            review = read_json(review_path)
        except Exception:
            continue
        if not isinstance(review, dict):
            continue
        problems = review.get("problems") if isinstance(review.get("problems"), list) else []
        priority_fixes = review.get("priority_fixes") if isinstance(review.get("priority_fixes"), list) else []
        existing_ids = {
            stringify(item.get("id"))
            for item in priority_fixes
            if isinstance(item, dict)
        }
        changed = False
        for fix in fixes:
            fix_id = stringify(fix.get("id"))
            if fix_id in existing_ids:
                continue
            priority_fixes.append(fix)
            problems.append(
                {
                    "type": stringify(fix.get("problem_type")) or "compliance",
                    "severity": "blocker" if fix.get("severity") == "blocker" else "major",
                    "description": f"合规项 {fix.get('check_id')}: {fix.get('target')}",
                    "suggestion": stringify(fix.get("action")),
                }
            )
            changed = True
            injected += 1
        if not changed:
            continue
        # 截断，保持 reviewer 契约
        review["problems"] = problems[:30]
        review["priority_fixes"] = priority_fixes[:8]
        review["need_rewrite"] = True
        review["has_writing_fixes"] = True
        if stringify(review.get("rewrite_status")) in {"", "ok", "need_evidence"}:
            review["rewrite_status"] = "need_rewrite"
        # max_severity
        if any(stringify(item.get("severity")) == "blocker" for item in review["priority_fixes"] if isinstance(item, dict)):
            review["max_severity"] = "blocker"
        elif not stringify(review.get("max_severity")):
            review["max_severity"] = "major"
        write_json(review_path, review)

    summary_path = manual_dir / "summary.json"
    summary = _safe_read_json(summary_path) or {}
    if not isinstance(summary, dict):
        summary = {}
    summary["compliance_pending"] = len(compliance_items)
    summary["compliance_rewrite_chapters"] = len(compact_hints)
    summary["compliance_injected_fixes"] = injected
    summary["updated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    write_json(summary_path, summary)

    print(
        f"[完成] 合规结果已回灌: pending={len(compliance_items)}, "
        f"rewrite_chapters={len(compact_hints)}, injected_fixes={injected} -> {hints_path}"
    )
    return hints_path


def compliance_hints_for_chapter(root: Path | None, chapter_id: str) -> list[dict[str, Any]]:
    root = root or project_root()
    data = _safe_read_json(root / "workspace" / "compliance_rewrite_hints.json") or {}
    chapters = data.get("chapters") if isinstance(data, dict) else {}
    if not isinstance(chapters, dict):
        return []
    items = chapters.get(stringify(chapter_id))
    return items if isinstance(items, list) else []
