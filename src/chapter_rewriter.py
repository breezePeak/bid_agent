from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from chapter_reviewer import (
    find_chapter,
    load_global_facts,
    load_outline,
    load_score_points,
    read_nonempty_text,
    review_chapter_markdown,
    select_score_points,
)
from chapter_writer import _ensure_chapter_heading, _load_selected_chunks
from llm_client import chat
from utils import (
    compact_json,
    load_prompt,
    project_root,
    read_json,
    stringify,
    write_json,
    write_text,
)


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
    prompt = load_prompt(root, "rewrite_chapter.md")

    problems_count = len(review.get("problems", []))

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
                    "## 审核结果\n\n"
                    f"{compact_json(review)}\n\n"
                    "## 选中的招标文件片段\n\n"
                    f"{compact_json(selected_tender)}\n\n"
                    "## 选中的公司资料片段\n\n"
                    f"{compact_json(selected_company)}\n\n"
                    "## 原章节正文\n\n"
                    f"{old_md}"
                ),
            },
        ],
        temperature=0.35,
    )
    new_md = _ensure_chapter_heading(raw, chapter_info)
    new_length = len(new_md)

    write_text(chapter_path, new_md)
    print(f"[完成] 已重写章节 {chapter_id}（{old_length} → {new_length} 字符）")

    rewrites_dir = root / "workspace" / "rewrites"
    rewrites_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "chapter_id": chapter_id,
        "old_length": old_length,
        "new_length": new_length,
        "review_need_rewrite": review.get("need_rewrite", False),
        "fixed_problem_count": problems_count,
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
    reviews_dir = root / "workspace" / "reviews"
    paths: list[Path] = []
    rewrites_dir = root / "workspace" / "rewrites"
    rewrites_dir.mkdir(parents=True, exist_ok=True)

    review_files = sorted(reviews_dir.glob("*_review.json"))
    rewritten = 0
    for rf in review_files:
        try:
            review = read_json(rf)
        except Exception:
            continue
        if not review.get("need_rewrite", False):
            continue
        chapter_id = review.get("chapter_id") or rf.stem.replace("_review", "")
        try:
            paths.append(rewrite_chapter(chapter_id, root))
            rewritten += 1
        except Exception as exc:
            print(f"[错误] 章节 {chapter_id} 重写失败: {exc}")

    if rewritten == 0:
        print("[提示] 没有需要重写的章节。")
    else:
        print(f"[完成] 已重写 {rewritten} 个章节")
    return paths


def review_fix_all(root: Path | None = None, max_rounds: int = 2) -> None:
    root = root or project_root()
    outlines = load_outline(root)
    chapter_count = len(outlines.get("chapters", []))

    print(f"[1/{max_rounds + 1}] 初次审核所有章节...")
    from chapter_reviewer import review_all

    review_all(root)

    total_rewritten = 0
    for round_num in range(1, max_rounds + 1):
        reviews_dir = root / "workspace" / "reviews"
        review_files = sorted(reviews_dir.glob("*_review.json"))
        need_rewrite_ids: list[str] = []
        for rf in review_files:
            try:
                review = read_json(rf)
                if review.get("need_rewrite", False):
                    need_rewrite_ids.append(review.get("chapter_id") or rf.stem.replace("_review", ""))
            except Exception:
                continue

        if not need_rewrite_ids:
            print(f"[完成] 第 {round_num} 轮无章节需要重写。")
            break

        print(f"\n[{round_num + 1}/{max_rounds + 1}] 第 {round_num} 轮改稿：{len(need_rewrite_ids)} 个章节需要重写...")

        for chapter_id in need_rewrite_ids:
            try:
                rewrite_chapter(chapter_id, root)
                total_rewritten += 1
            except Exception as exc:
                print(f"[错误] 章节 {chapter_id} 重写失败: {exc}")

        from chapter_reviewer import review_chapter

        for chapter_id in need_rewrite_ids:
            try:
                review_chapter(chapter_id, root)
            except Exception as exc:
                print(f"[错误] 章节 {chapter_id} 审核失败: {exc}")

    reviews_dir = root / "workspace" / "reviews"
    still_failed = 0
    for rf in sorted(reviews_dir.glob("*_review.json")):
        try:
            review = read_json(rf)
            if review.get("need_rewrite", False):
                still_failed += 1
        except Exception:
            continue

    print()
    print(f"--- review-fix-all 完成 ---")
    print(f"总章节数: {chapter_count}")
    print(f"已重写数: {total_rewritten}")
    print(f"仍未通过数: {still_failed}")
