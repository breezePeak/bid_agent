from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from chapter_writer import write_chapter
from utils import project_root


def run_write_chapter(chapter_id: str, root: Path | None = None) -> tuple[str, str | None]:
    root = root or project_root()
    try:
        output_path = write_chapter(chapter_id, root)
        return chapter_id, None
    except Exception as exc:
        return chapter_id, str(exc)


def run_write_all(root: Path | None = None, workers: int = 2) -> dict[str, Any]:
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

    chapter_ids = [f.stem for f in job_files]
    effective_workers = max(1, min(workers, 5))
    print(f"[启动] 并发执行 {len(chapter_ids)} 个章节 SubAgent, workers={effective_workers}")

    completed: list[str] = []
    failed: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(run_write_chapter, cid, root): cid for cid in chapter_ids}
        for future in as_completed(futures):
            chapter_id = futures[future]
            try:
                result_id, error = future.result()
            except Exception as exc:
                error = str(exc)
                result_id = chapter_id

            if error:
                print(f"[失败] 章节 {result_id}: {error}")
                failed.append({"chapter_id": result_id, "error": error})
            else:
                completed.append(result_id)

    print(f"[完成] 成功 {len(completed)} 个章节, 失败 {len(failed)} 个章节")
    if failed:
        print(f"[详情] 失败章节: {[f['chapter_id'] for f in failed]}")
    return {"completed": completed, "failed": failed}
