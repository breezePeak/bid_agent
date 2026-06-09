from __future__ import annotations

from pathlib import Path
from typing import Any

from chapter_writer import write_chapter_from_job_context


def run(job: dict[str, Any], context: dict[str, Any], root: Path) -> str:
    return write_chapter_from_job_context(job, context, root)
