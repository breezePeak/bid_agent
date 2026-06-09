from __future__ import annotations

import operator
from typing import Any, TypedDict

from typing_extensions import Annotated


class BidState(TypedDict, total=False):
    root_dir: str
    workers: int

    tender_path: str
    score_path: str
    company_path: str
    template_path: str

    tender_chunks_path: str
    company_chunks_path: str

    score_points_path: str
    global_facts_path: str
    outline_path: str

    jobs_dir: str
    contexts_dir: str
    chapters_dir: str
    reviews_dir: str

    final_md_path: str
    final_docx_path: str
    global_review_path: str

    chapter_jobs: list[dict[str, Any]]

    completed_chapters: Annotated[list[str], operator.add]
    failed_chapters: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]


class ChapterState(TypedDict, total=False):
    root_dir: str
    job: dict[str, Any]
    context: dict[str, Any]
    chapter_id: str
    chapter_markdown: str
    self_check: dict[str, Any]
    output_path: str
    self_check_path: str
    error: str
