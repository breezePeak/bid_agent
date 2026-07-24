from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from chapter_reviewer import (
    find_chapter,
    load_global_facts,
    load_outline,
    load_score_points,
    mark_review_stuck,
    read_nonempty_text,
    rewrite_fix_signatures,
    should_auto_rewrite,
    select_score_points,
)
from context_budget import summarize_chunk_payload, summarize_for_prompt, trim_text
from chapter_writer import _ensure_chapter_heading, _load_selected_chunks
from llm_client import chat
from prompt_registry import load_agent_prompt
from quality_gates import validate_weak_evidence_language
from runtime_context import agent_run
from utils import (
    compact_json,
    project_root,
    read_json,
    stringify,
    write_json,
    write_text,
)

REWRITE_CONTEXT_MAX_CHARS = 17000
STUCK_UNCHANGED_ROUNDS = 2


def _collect_auto_rewrite_ids(root: Path) -> tuple[list[str], list[str], list[str]]:
    reviews_dir = root / "workspace" / "reviews"
    need_rewrite_ids: list[str] = []
    need_evidence_ids: list[str] = []
    stuck_ids: list[str] = []
    if not reviews_dir.exists():
        return need_rewrite_ids, need_evidence_ids, stuck_ids

    for rf in sorted(reviews_dir.glob("*_review.json")):
        try:
            review = read_json(rf)
        except Exception:
            continue
        if not isinstance(review, dict):
            continue
        chapter_id = stringify(review.get("chapter_id")) or rf.stem.replace("_review", "")
        status = stringify(review.get("rewrite_status"))
        if status == "stuck" or bool(review.get("stuck")):
            stuck_ids.append(chapter_id)
            continue
        if status == "need_evidence" or (
            bool(review.get("need_evidence")) and not bool(review.get("has_writing_fixes", True))
        ):
            need_evidence_ids.append(chapter_id)
            continue
        if should_auto_rewrite(review):
            need_rewrite_ids.append(chapter_id)
    return need_rewrite_ids, need_evidence_ids, stuck_ids


def _apply_stuck_detection(
    root: Path,
    chapter_ids: list[str],
    previous_signatures: dict[str, list[str]],
    unchanged_rounds: dict[str, int],
) -> list[str]:
    stuck_now: list[str] = []
    for chapter_id in chapter_ids:
        review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
        if not review_path.exists():
            continue
        try:
            review = read_json(review_path)
        except Exception:
            continue
        if not isinstance(review, dict):
            continue
        current_sigs = rewrite_fix_signatures(review)
        previous_sigs = previous_signatures.get(chapter_id, [])
        if current_sigs and previous_sigs and set(current_sigs) == set(previous_sigs):
            unchanged_rounds[chapter_id] = unchanged_rounds.get(chapter_id, 0) + 1
        else:
            unchanged_rounds[chapter_id] = 0
        previous_signatures[chapter_id] = current_sigs

        if unchanged_rounds.get(chapter_id, 0) >= STUCK_UNCHANGED_ROUNDS and should_auto_rewrite(review):
            stuck_review = mark_review_stuck(
                review,
                stuck_signatures=current_sigs,
                rounds_unchanged=unchanged_rounds[chapter_id],
            )
            write_json(review_path, stuck_review)
            stuck_now.append(chapter_id)
            print(f"[卡住] 章节 {chapter_id} 连续 {unchanged_rounds[chapter_id]} 轮 blocker/major 未收敛，停止自动改稿")
    return stuck_now


def rewrite_chapter(chapter_id: str, root: Path | None = None) -> Path:
    root = root or project_root()

    chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    context_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"

    if not chapter_path.exists():
        raise FileNotFoundError(f"章节文件不存在: {chapter_path}")
    if not review_path.exists():
        raise FileNotFoundError(f"审核文件不存在: {review_path}，请先执行 review-chapter --chapter {chapter_id}")
    if not job_path.exists():
        raise FileNotFoundError(f"章节任务不存在: {job_path}")
    if not context_path.exists():
        raise FileNotFoundError(f"上下文文件不存在: {context_path}，请先执行 select-context --chapter {chapter_id}")

    old_md = read_nonempty_text(chapter_path, f"章节文件 {chapter_path}")
    old_length = len(old_md)
    review = read_json(review_path)
    if not should_auto_rewrite(review):
        status = stringify(review.get("rewrite_status")) or "skip"
        raise RuntimeError(f"章节 {chapter_id} 当前状态为 {status}，跳过自动改稿（缺证据/卡住/无需重写）")

    job = read_json(job_path)
    context = read_json(context_path)
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    related_sps = select_score_points(score_points, job.get("score_point_ids", []))
    selected_tender, selected_company = _load_selected_chunks(root, context)

    chapter_info = {
        "id": stringify(job.get("chapter_id")),
        "title": stringify(job.get("chapter_title")),
        "score_point_ids": job.get("score_point_ids", []),
        "description": stringify(job.get("description")),
        "sections": job.get("sections", []),
    }
    prompt = load_agent_prompt(root, "chapter_rewriter")
    tender_context = summarize_chunk_payload(selected_tender, total_max_chars=REWRITE_CONTEXT_MAX_CHARS // 2, per_chunk_chars=1200)
    company_context = summarize_chunk_payload(selected_company, total_max_chars=REWRITE_CONTEXT_MAX_CHARS // 2, per_chunk_chars=1000)

    problems_count = len(review.get("problems", []))
    priority_fixes = review.get("priority_fixes") if isinstance(review.get("priority_fixes"), list) else []
    # 合并合规回灌线索（若审核结果尚未注入）
    try:
        from compliance_feedback import compliance_hints_for_chapter

        existing_ids = {
            stringify(item.get("id")) for item in priority_fixes if isinstance(item, dict)
        }
        for fix in compliance_hints_for_chapter(root, chapter_id):
            if not isinstance(fix, dict):
                continue
            fix_id = stringify(fix.get("id"))
            if fix_id and fix_id not in existing_ids:
                priority_fixes.append(fix)
                existing_ids.add(fix_id)
    except Exception:
        pass
    rewrite_fixes = [
        item
        for item in priority_fixes
        if isinstance(item, dict) and stringify(item.get("severity")) in {"blocker", "major"}
    ]
    if not rewrite_fixes and priority_fixes:
        rewrite_fixes = [item for item in priority_fixes if isinstance(item, dict)][:5]
    if not rewrite_fixes:
        for item in review.get("problems") or []:
            if not isinstance(item, dict):
                continue
            rewrite_fixes.append(
                {
                    "id": f"legacy_{len(rewrite_fixes) + 1:02d}",
                    "severity": stringify(item.get("severity")) or "major",
                    "source": "problem",
                    "target": stringify(item.get("description")) or stringify(item.get("type")),
                    "action": stringify(item.get("suggestion")) or "按审核意见修订",
                    "acceptance": "对应问题在复审中不再出现。",
                    "problem_type": stringify(item.get("type")),
                }
            )

    with agent_run(
        root,
        "review_fix_chapters",
        "chapter_rewriter",
        input_summary={
            "chapter_id": chapter_id,
            "problem_count": problems_count,
            "priority_fix_count": len(rewrite_fixes),
            "max_severity": stringify(review.get("max_severity")),
            "tender_chunk_count": len(tender_context),
            "company_chunk_count": len(company_context),
        },
        chapter_id=chapter_id,
        temperature=0.35,
    ):
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"请根据审核意见重写章节 {chapter_id}。\n\n"
                        "## 当前章节信息\n\n"
                        f"{compact_json(chapter_info)}\n\n"
                        "## 绑定评分点\n\n"
                        f"{compact_json(related_sps)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}\n\n"
                        "## 本轮优先修复项（必须逐项处理）\n\n"
                        f"{compact_json(rewrite_fixes)}\n\n"
                        "## 完整审核结果\n\n"
                        f"{compact_json(review)}\n\n"
                        "## 上下文摘要\n\n"
                        f"{summarize_for_prompt({'max_context_chars': REWRITE_CONTEXT_MAX_CHARS, 'old_md_chars': len(old_md), 'priority_fix_count': len(rewrite_fixes)}, 800)}\n\n"
                        "## 选中的招标文件片段\n\n"
                        f"{compact_json(tender_context)}\n\n"
                        "## 选中的公司资料片段\n\n"
                        f"{compact_json(company_context)}\n\n"
                        "## 原章节正文\n\n"
                        f"{trim_text(old_md, REWRITE_CONTEXT_MAX_CHARS // 2)}"
                    ),
                },
            ],
            temperature=0.35,
        )
    new_md = _ensure_chapter_heading(raw, chapter_info)
    validate_weak_evidence_language(job, new_md)
    new_length = len(new_md)

    write_text(chapter_path, new_md)
    print(f"[完成] 已重写章节 {chapter_id}（{old_length} → {new_length} 字符，优先修复 {len(rewrite_fixes)} 项）")

    rewrites_dir = root / "workspace" / "rewrites"
    rewrites_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "chapter_id": chapter_id,
        "old_length": old_length,
        "new_length": new_length,
        "review_need_rewrite": review.get("need_rewrite", False),
        "review_max_severity": stringify(review.get("max_severity")),
        "fixed_problem_count": problems_count,
        "priority_fix_count": len(rewrite_fixes),
        "priority_fix_ids": [stringify(item.get("id")) for item in rewrite_fixes if isinstance(item, dict)],
        "priority_fixes": rewrite_fixes,
        "priority_fix_signatures": rewrite_fix_signatures({"priority_fixes": rewrite_fixes}),
        "rewrite_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "review_file": str(review_path.relative_to(root)),
        "chapter_file": str(chapter_path.relative_to(root)),
    }
    log_path = rewrites_dir / f"{chapter_id}_rewrite_log.json"
    write_json(log_path, log)
    print(f"[完成] 重写日志: {log_path}")

    return chapter_path


def rewrite_all(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    paths: list[Path] = []
    rewrites_dir = root / "workspace" / "rewrites"
    rewrites_dir.mkdir(parents=True, exist_ok=True)

    need_rewrite_ids, need_evidence_ids, stuck_ids = _collect_auto_rewrite_ids(root)
    if need_evidence_ids:
        print(f"[跳过改稿] 缺证据章节: {need_evidence_ids}")
    if stuck_ids:
        print(f"[跳过改稿] 已卡住章节: {stuck_ids}")

    rewritten = 0
    for chapter_id in need_rewrite_ids:
        paths.append(rewrite_chapter(chapter_id, root))
        rewritten += 1

    if rewritten == 0:
        print("[提示] 没有需要重写的章节。")
    else:
        print(f"[完成] 已重写 {rewritten} 个章节")
    return paths


def review_fix_all(
    root: Path | None = None,
    max_rounds: int = 2,
    workers: int | None = None,
    chapter_ids: list[str] | None = None,
) -> None:
    """Review and repair chapters, optionally constrained to an explicit scope.

    A repair revalidation must never turn a handful of Issue targets into a
    whole-document rewrite.  An omitted scope preserves the normal pipeline
    behaviour; a supplied scope is authoritative for every review, rewrite,
    and Issue synchronization in this invocation.
    """
    root = root or project_root()
    from subagent_runner import run_review_all, run_rewrite_all
    from stage_validation import chapter_ids as valid_chapter_ids, review_ids as valid_review_ids

    outlines = load_outline(root)
    scope = {stringify(item) for item in (chapter_ids or []) if stringify(item)}
    existing_ids = valid_chapter_ids(root)
    if scope:
        unknown_ids = sorted(scope - existing_ids)
        if unknown_ids:
            raise RuntimeError(f"定向审核章节不存在: {unknown_ids}")
    active_ids = scope or existing_ids
    chapter_count = len(active_ids)

    pending_review_ids = sorted(active_ids - valid_review_ids(root))
    if pending_review_ids:
        print(f"[1/{max_rounds + 1}] 补审 {len(pending_review_ids)} 个缺失章节（并发子 agent）...")
        review_result = run_review_all(root, workers=workers, chapter_ids=pending_review_ids)
        if review_result.get("failed"):
            details = [f"{item['chapter_id']}: {item['error']}" for item in review_result["failed"][:10]]
            raise RuntimeError("章节审核失败：" + "；".join(details))
    else:
        print("[跳过] 所有现有章节均已有有效审核结果。")

    total_rewritten = 0
    previous_signatures: dict[str, list[str]] = {}
    unchanged_rounds: dict[str, int] = {}
    total_stuck: set[str] = set()
    total_need_evidence: set[str] = set()

    for round_num in range(1, max_rounds + 1):
        need_rewrite_ids, need_evidence_ids, stuck_ids = _collect_auto_rewrite_ids(root)
        if scope:
            need_rewrite_ids = [item for item in need_rewrite_ids if item in scope]
            need_evidence_ids = [item for item in need_evidence_ids if item in scope]
            stuck_ids = [item for item in stuck_ids if item in scope]
        total_need_evidence.update(need_evidence_ids)
        total_stuck.update(stuck_ids)

        if need_evidence_ids:
            print(f"[分流] 第 {round_num} 轮缺证据章节（不自动改稿）: {need_evidence_ids}")
        if stuck_ids:
            print(f"[分流] 第 {round_num} 轮已卡住章节（不自动改稿）: {stuck_ids}")

        if not need_rewrite_ids:
            print(f"[完成] 第 {round_num} 轮无章节需要自动重写。")
            break

        # 记录改稿前签名，供复审后判断是否收敛
        for chapter_id in need_rewrite_ids:
            review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
            try:
                review = read_json(review_path)
                previous_signatures[chapter_id] = rewrite_fix_signatures(review)
            except Exception:
                previous_signatures[chapter_id] = []

        print(f"\n[{round_num + 1}/{max_rounds + 1}] 第 {round_num} 轮改稿：{len(need_rewrite_ids)} 个章节并发改稿...")
        rewrite_result = run_rewrite_all(root, workers=workers, chapter_ids=need_rewrite_ids)
        if rewrite_result.get("failed"):
            details = [f"{item['chapter_id']}: {item['error']}" for item in rewrite_result["failed"][:10]]
            raise RuntimeError("章节改稿失败：" + "；".join(details))
        total_rewritten += len(rewrite_result.get("completed", []))

        print(f"[{round_num + 1}/{max_rounds + 1}] 改稿后定向复审（带上轮 priority_fixes）...")
        rereview_result = run_review_all(root, workers=workers, chapter_ids=need_rewrite_ids)
        if rereview_result.get("failed"):
            details = [f"{item['chapter_id']}: {item['error']}" for item in rereview_result["failed"][:10]]
            raise RuntimeError("章节复审失败：" + "；".join(details))

        stuck_now = _apply_stuck_detection(root, need_rewrite_ids, previous_signatures, unchanged_rounds)
        total_stuck.update(stuck_now)

    reviews_dir = root / "workspace" / "reviews"
    still_failed = 0
    need_evidence_final: list[str] = []
    stuck_final: list[str] = []
    for rf in sorted(reviews_dir.glob("*_review.json")) if reviews_dir.exists() else []:
        try:
            review = read_json(rf)
        except Exception:
            continue
        if not isinstance(review, dict):
            continue
        chapter_id = stringify(review.get("chapter_id")) or rf.stem.replace("_review", "")
        if scope and chapter_id not in scope:
            continue
        status = stringify(review.get("rewrite_status"))
        if status == "stuck" or bool(review.get("stuck")):
            stuck_final.append(chapter_id)
            still_failed += 1
        elif status == "need_evidence" or (
            bool(review.get("need_evidence")) and not bool(review.get("has_writing_fixes", True))
        ):
            need_evidence_final.append(chapter_id)
            still_failed += 1
        elif review.get("need_rewrite", False):
            still_failed += 1

    print()
    print(f"--- review-fix-all 完成 ---")
    print(f"总章节数: {chapter_count}")
    print(f"已重写数: {total_rewritten}")
    print(f"仍未通过数: {still_failed}")
    if need_evidence_final:
        print(f"缺证据章节: {need_evidence_final}")
    if stuck_final:
        print(f"卡住章节: {stuck_final}")

    # collect still-need-rewrite ids
    need_rewrite_final: list[str] = []
    try:
        reviews_dir = root / "workspace" / "reviews"
        if reviews_dir.exists():
            for path in sorted(reviews_dir.glob("*_review.json")):
                data = read_json(path) if "read_json" in globals() else None
    except Exception:
        pass
    # derive from still_failed lists we already have
    try:
        from agent.root_cause import sync_issues_from_review_fix
        import os

        # reopen reviews for need_rewrite
        need_rewrite_final = []
        reviews_dir = root / "workspace" / "reviews"
        if reviews_dir.exists():
            from utils import read_json as _rj

            for path in sorted(reviews_dir.glob("*_review.json")):
                data = _rj(path)
                if not isinstance(data, dict):
                    continue
                cid = str(data.get("chapter_id") or path.name.replace("_review.json", ""))
                if scope and cid not in scope:
                    continue
                if cid in need_evidence_final or cid in stuck_final:
                    continue
                if bool(data.get("need_rewrite")):
                    need_rewrite_final.append(cid)
        synced = sync_issues_from_review_fix(
            root,
            need_rewrite_ids=need_rewrite_final,
            need_evidence_ids=need_evidence_final,
            stuck_ids=stuck_final,
            chapter_ids=sorted(scope) or None,
        )
        if synced:
            print(f"[问题单] 已同步章节审核 Issue {len(synced)} 条")
        gate = str(os.environ.get("CHAPTER_REVIEW_GATE", "1")).strip().lower()
        if gate not in {"0", "false", "no", "off"} and (need_rewrite_final or need_evidence_final or stuck_final):
            raise RuntimeError(
                "章节审核质量门禁阻断：仍有未通过章节，请定向改稿/补证据后再继续。"
                f" need_rewrite={need_rewrite_final}; need_evidence={need_evidence_final}; stuck={stuck_final}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[警告] 章节审核 Issue/门禁处理失败: {exc}")
