from __future__ import annotations

from typing import Any, TypedDict


class BidState(TypedDict, total=False):
    root_dir: str
    workers: int
    max_retries: int
    resume: bool

    tender_path: str
    score_path: str
    company_path: str
    template_path: str
    template_schema_path: str
    template_evidence_map_path: str
    template_quality_report_path: str
    template_fill_report_path: str

    tender_chunks_path: str
    company_chunks_path: str

    score_points_path: str
    score_coverage_matrix_path: str
    source_trace_index_path: str
    global_facts_path: str
    outline_path: str

    jobs_dir: str
    contexts_dir: str
    chapters_dir: str
    reviews_dir: str
    rewrites_dir: str
    summaries_dir: str

    final_md_path: str
    final_docx_path: str
    format_check_report_path: str
    global_review_path: str

    chapter_jobs: list[dict[str, Any]]

    completed_chapters: list[str]
    failed_chapters: list[dict[str, Any]]
    errors: list[str]


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
