from __future__ import annotations

from pathlib import Path

from chapter_reviewer import review_chapter


def run(chapter_id: str, root: Path) -> Path:
    return review_chapter(chapter_id, root)
