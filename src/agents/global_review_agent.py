from __future__ import annotations

from pathlib import Path

from global_reviewer import run_global_review


def run(root: Path) -> Path:
    return run_global_review(root)
