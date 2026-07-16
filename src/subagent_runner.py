from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from graph.chapter_subgraph import build_chapter_subgraph
from utils import project_root


def _write_worker(chapter_id: str, root: Path) -> None:
    graph = build_chapter_subgraph()
    graph.invoke({"root_dir": str(root), "chapter_id": chapter_id})


def _review_worker(chapter_id: str, root: Path) -> None:
    from chapter_reviewer import review_chapter

    review_chapter(chapter_id, root)


def _rewrite_worker(chapter_id: str, root: Path) -> None:
    from chapter_rewriter import rewrite_chapter

    rewrite_chapter(chapter_id, root)


def _run_with_retry(
    worker: Callable[[str, Path], None],
    chapter_id: str,
    root: Path,
    max_retries: int,
) -> tuple[str, str | None, int]:
    attempts = max(1, max_retries + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            print(f"[执行] 章节 {chapter_id} 开始（第 {attempt}/{attempts} 次）…", flush=True)
            worker(chapter_id, root)
            print(f"[完成] 章节 {chapter_id} 本轮执行成功", flush=True)
            return chapter_id, None, attempt
        except Exception as exc:
            last_error = str(exc)
            print(f"[重试] 章节 {chapter_id} 第 {attempt}/{attempts} 次失败: {last_error}")
    return chapter_id, last_error, attempts


def _resolve_chapter_ids(root: Path, chapter_ids: list[str] | None) -> list[str]:
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
    all_ids = [f.stem for f in job_files]
    if chapter_ids is None:
        return all_ids
    requested = {str(chapter_id) for chapter_id in chapter_ids}
    selected = [chapter_id for chapter_id in all_ids if chapter_id in requested]
    missing = sorted(requested.difference(selected))
    if missing:
        raise FileNotFoundError(f"未找到章节任务: {missing}")
    return selected


def run_per_chapter(
    worker: Callable[[str, Path], None],
    root: Path | None = None,
    workers: int = 2,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
    label: str = "SubAgent",
) -> dict[str, Any]:
    root = root or project_root()
    selected = _resolve_chapter_ids(root, chapter_ids)
    effective_workers = max(1, min(workers, 5))
    print(
        f"[启动] 并发执行 {len(selected)} 个章节 {label}, "
        f"workers={effective_workers}, max_retries={max(0, max_retries)}"
    )

    completed: list[str] = []
    failed: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(_run_with_retry, worker, cid, root, max_retries): cid
            for cid in selected
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

    print(f"[完成] {label} 成功 {len(completed)} 个, 失败 {len(failed)} 个")
    if failed:
        print(f"[详情] 失败章节: {[f['chapter_id'] for f in failed]}")
    return {"completed": completed, "failed": failed}


def run_write_chapter(
    chapter_id: str,
    root: Path | None = None,
    max_retries: int = 0,
) -> tuple[str, str | None, int]:
    root = root or project_root()
    return _run_with_retry(_write_worker, chapter_id, root, max_retries)


def run_write_all(
    root: Path | None = None,
    workers: int = 2,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _write_worker, root, workers, chapter_ids, max_retries, label="写作 SubAgent"
    )


def run_review_all(
    root: Path | None = None,
    workers: int = 2,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _review_worker, root, workers, chapter_ids, max_retries, label="审核 SubAgent"
    )


def run_rewrite_all(
    root: Path | None = None,
    workers: int = 2,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _rewrite_worker, root, workers, chapter_ids, max_retries, label="改稿 SubAgent"
    )


def run_global_review(root: Path | None = None) -> Path:
    from global_reviewer import run_global_review as _run_global_review

    root = root or project_root()
    return _run_global_review(root)
