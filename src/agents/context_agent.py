from __future__ import annotations

from pathlib import Path
from typing import Any

from context_selector import select_context_for_job, select_contexts_for_jobs
from utils import read_json


def run_job(job: dict[str, Any], root: Path) -> Path:
    return select_context_for_job(job, root)


def run_all(
    jobs: list[dict[str, Any]],
    root: Path,
    *,
    workers: int | None = None,
    max_retries: int = 0,
    resume: bool = True,
    force: bool = False,
) -> list[Path]:
    return select_contexts_for_jobs(
        jobs,
        root,
        workers=workers,
        max_retries=max_retries,
        resume=resume,
        force=force,
    )


def run(
    root: Path,
    *,
    workers: int | None = None,
    max_retries: int = 0,
    resume: bool = True,
    force: bool = False,
) -> list[Path]:
    jobs_dir = root / "workspace" / "jobs"
    jobs = [read_json(path) for path in sorted(jobs_dir.glob("*.json"))]
    if not jobs:
        raise FileNotFoundError(f"缺少章节任务: {jobs_dir}")
    return run_all(
        jobs,
        root,
        workers=workers,
        max_retries=max_retries,
        resume=resume,
        force=force,
    )
