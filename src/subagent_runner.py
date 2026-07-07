from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from graph.chapter_subgraph import build_chapter_subgraph
from utils import project_root


def run_write_chapter(
    chapter_id: str,
    root: Path | None = None,
    max_retries: int = 0,
) -> tuple[str, str | None, int]:
    root = root or project_root()
    attempts = max(1, max_retries + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            graph = build_chapter_subgraph()
            graph.invoke({"root_dir": str(root), "chapter_id": chapter_id})
            return chapter_id, None, attempt
        except Exception as exc:
            last_error = str(exc)
            print(f"[重试] 章节 {chapter_id} 第 {attempt}/{attempts} 次失败: {last_error}")
    return chapter_id, last_error, attempts


def run_write_all(
    root: Path | None = None,
    workers: int = 2,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    root = root or project_root()
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists():
        raise FileNotFoundError(
            f"缺少章节任务目录: {jobs_dir}，请先执行 plan-jobs"
        )

    job_files = sorted(jobs_dir.glob("*.json"))
    if not job_files:
        raise FileNotFoundError(
            f"章节任务目录为空: {jobs_dir}，请先执行 plan-jobs"
        )

    all_chapter_ids = [f.stem for f in job_files]
    if chapter_ids is None:
        selected_chapter_ids = all_chapter_ids
    else:
        requested = {str(chapter_id) for chapter_id in chapter_ids}
        selected_chapter_ids = [chapter_id for chapter_id in all_chapter_ids if chapter_id in requested]
        missing_ids = sorted(requested.difference(selected_chapter_ids))
        if missing_ids:
            raise FileNotFoundError(f"未找到章节任务: {missing_ids}")

    effective_workers = max(1, min(workers, 5))
    print(
        f"[启动] 并发执行 {len(selected_chapter_ids)} 个章节 SubAgent, "
        f"workers={effective_workers}, max_retries={max(0, max_retries)}"
    )

    completed: list[str] = []
    failed: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(run_write_chapter, cid, root, max_retries): cid
            for cid in selected_chapter_ids
        }
        for future in as_completed(futures):
            chapter_id = futures[future]
            try:
                result_id, error, attempts = future.result()
            except Exception as exc:
                error = str(exc)
                result_id = chapter_id
                attempts = max(1, max_retries + 1)

            if error:
                print(f"[失败] 章节 {result_id}: {error}")
                failed.append({"chapter_id": result_id, "error": error, "attempts": attempts})
            else:
                completed.append(result_id)

    print(f"[完成] 成功 {len(completed)} 个章节, 失败 {len(failed)} 个章节")
    if failed:
        print(f"[详情] 失败章节: {[f['chapter_id'] for f in failed]}")
    return {"completed": completed, "failed": failed}
