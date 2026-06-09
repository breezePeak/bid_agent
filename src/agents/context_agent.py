from __future__ import annotations

from pathlib import Path
from typing import Any

from context_selector import select_context_for_job, select_contexts_for_jobs


def run_job(job: dict[str, Any], root: Path) -> Path:
    return select_context_for_job(job, root)


def run_all(jobs: list[dict[str, Any]], root: Path) -> list[Path]:
    return select_contexts_for_jobs(jobs, root)
