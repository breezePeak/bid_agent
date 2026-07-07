from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.fact_agent import run as fact_agent
from agents.global_review_agent import run as global_review_agent
from agents.outline_agent import run as outline_agent
from agents.score_agent import run as score_agent
from chapter_rewriter import review_fix_all
from chapter_summarizer import summarize_chapter
from context_selector import select_context_for_job
from docx_builder import build_docx, build_markdown
from document_splitter import split_docs
from format_checker import check_output_format
from graph.state_recorder import (
    record_stage_finish,
    record_stage_start,
    save_run_state,
    stage_outputs_valid,
    stage_resume_ready,
)
from input_preparer import prepare_inputs
from job_planner import plan_chapter_jobs
from pipeline_registry import stage_spec_by_id
from quality_gates import final_review_status, validate_template_fill_report
from score_coverage_matrix import build_score_coverage_matrix
from source_trace import build_source_trace_index
from subagent_runner import run_write_all as concurrent_write_all
from template_evidence import build_template_evidence
from utils import ensure_dirs, ensure_file, project_root, read_json, stringify


def _root(state) -> Path:
    return Path(state.get("root_dir") or project_root())


def _is_resume(state: dict[str, Any]) -> bool:
    return bool(state.get("resume"))


def _file_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def _text_file_ready(path: Path) -> bool:
    return _file_exists(path) and path.stat().st_size > 0


def _load_jobs_from_disk(root: Path) -> list[dict[str, Any]]:
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists():
        return []
    jobs: list[dict[str, Any]] = []
    for job_file in sorted(jobs_dir.glob("*.json")):
        data = read_json(job_file)
        if isinstance(data, dict):
            jobs.append(data)
    return jobs


def _state_jobs(state: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    jobs = state.get("chapter_jobs")
    if isinstance(jobs, list) and jobs:
        return [job for job in jobs if isinstance(job, dict)]
    return _load_jobs_from_disk(root)


def _chapter_ids_from_jobs(jobs: list[dict[str, Any]]) -> list[str]:
    chapter_ids: list[str] = []
    for job in jobs:
        chapter_id = stringify(job.get("chapter_id") or job.get("id"))
        if chapter_id:
            chapter_ids.append(chapter_id)
    return chapter_ids


def _existing_chapter_ids(root: Path, pattern: str, suffix_to_trim: str = "") -> list[str]:
    parent = root / "workspace"
    matched: list[str] = []
    for path in sorted(parent.glob(pattern)):
        chapter_id = path.stem
        if suffix_to_trim and chapter_id.endswith(suffix_to_trim):
            chapter_id = chapter_id[: -len(suffix_to_trim)]
        matched.append(chapter_id)
    return matched


def _merge_string_lists(current: Any, incoming: Any) -> list[str]:
    current_items = current if isinstance(current, list) else [current] if current is not None else []
    incoming_items = incoming if isinstance(incoming, list) else [incoming] if incoming is not None else []
    merged: list[str] = []
    for value in [*current_items, *incoming_items]:
        text = stringify(value)
        if text and text not in merged:
            merged.append(text)
    return merged


def _merge_failed_lists(current: Any, incoming: Any) -> list[dict[str, Any]]:
    current_items = current if isinstance(current, list) else [current] if current is not None else []
    incoming_items = incoming if isinstance(incoming, list) else [incoming] if incoming is not None else []
    merged_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for item in [*current_items, *incoming_items]:
        if not isinstance(item, dict):
            item = {"chapter_id": "", "error": stringify(item)}
        chapter_id = stringify(item.get("chapter_id"))
        key = chapter_id or f"__index_{len(ordered_ids)}"
        normalized = dict(item)
        normalized["chapter_id"] = chapter_id
        normalized["error"] = stringify(item.get("error"))
        if key not in merged_by_id:
            ordered_ids.append(key)
        merged_by_id[key] = normalized
    return [merged_by_id[key] for key in ordered_ids]


def _missing_ids(expected_ids: list[str], done_ids: list[str]) -> list[str]:
    done_set = set(done_ids)
    return [chapter_id for chapter_id in expected_ids if chapter_id not in done_set]


def _persist_state(
    state: dict,
    update: dict,
    stage: str,
    status: str = "ok",
    message: str = "",
) -> None:
    update = dict(update)
    remove_failed_ids = {
        stringify(chapter_id)
        for chapter_id in update.pop("_remove_failed_chapter_ids", [])
        if stringify(chapter_id)
    }
    merged = dict(state)
    for key, value in update.items():
        if key in {"completed_chapters", "errors"}:
            merged[key] = _merge_string_lists(merged.get(key, []), value)
            continue
        if key == "failed_chapters":
            merged[key] = _merge_failed_lists(merged.get(key, []), value)
            continue
        merged[key] = value
    if remove_failed_ids:
        merged["failed_chapters"] = [
            item
            for item in merged.get("failed_chapters", [])
            if stringify(item.get("chapter_id")) not in remove_failed_ids
        ]

    root = _root(merged)
    save_run_state(root, merged, stage=stage, status=status, message=message)
    if status == "error":
        record_stage_finish(root, stage, "fail", message=message, status=status)
    elif message.startswith("resume:"):
        record_stage_finish(root, stage, "reuse", message=message, status=status)
    elif message.startswith("skip:"):
        record_stage_finish(root, stage, "skip", message=message, status=status)
    else:
        artifact_path = ""
        try:
            spec = stage_spec_by_id(stage)
            if spec.produces:
                artifact_path = spec.produces[0].path
        except Exception:
            artifact_path = ""
        record_stage_finish(root, stage, "success", message=message or "stage_success", artifact_path=artifact_path, status=status)


def _persist_error_state(state: dict, stage: str, exc: Exception, extra_update: dict | None = None) -> None:
    update = dict(extra_update or {})
    update["errors"] = [str(exc)]
    _persist_state(state, update, stage=stage, status="error", message=str(exc))


def _start_stage(state: dict, stage: str, message: str) -> None:
    record_stage_start(_root(state), stage, state=state, message=message)


def init_workspace(state) -> dict:
    root = Path(state.get("root_dir") or project_root())
    print("[1/18] 初始化工作区...")
    _start_stage(state, "init_workspace", "初始化工作区")
    try:
        ensure_dirs(
            root,
            [
                "sources/tender",
                "sources/company",
                "sources/template",
                "inputs",
                "workspace",
                "workspace/chunks",
                "workspace/jobs",
                "workspace/contexts",
                "workspace/chapters",
                "workspace/reviews",
                "workspace/summaries",
                "workspace/rewrites",
                "outputs",
                "prompts",
            ],
        )
        ensure_file(root / "inputs" / "tender.md")
        ensure_file(root / "inputs" / "score.md")
        ensure_file(root / "inputs" / "company.md")
        update = {
            "root_dir": str(root),
            "workers": int(state.get("workers") or 1),
            "max_retries": max(0, int(state.get("max_retries") or 0)),
            "resume": bool(state.get("resume", False)),
            "tender_path": str(root / "inputs" / "tender.md"),
            "score_path": str(root / "inputs" / "score.md"),
            "company_path": str(root / "inputs" / "company.md"),
            "template_path": str(root / "inputs" / "template.docx"),
            "template_schema_path": str(root / "workspace" / "template_schema.json"),
            "jobs_dir": str(root / "workspace" / "jobs"),
            "contexts_dir": str(root / "workspace" / "contexts"),
            "chapters_dir": str(root / "workspace" / "chapters"),
            "reviews_dir": str(root / "workspace" / "reviews"),
            "rewrites_dir": str(root / "workspace" / "rewrites"),
            "summaries_dir": str(root / "workspace" / "summaries"),
        }
        _persist_state(state, update, stage="init_workspace")
        return update
    except Exception as exc:
        _persist_error_state(state, "init_workspace", exc, {"root_dir": str(root)})
        raise


def prepare_inputs_node(state) -> dict:
    root = _root(state)
    print("[2/18] 导入原始资料...")
    _start_stage(state, "prepare_inputs", "导入原始资料")
    try:
        if _is_resume(state) and stage_resume_ready(root, "prepare_inputs"):
            update = {
                "tender_path": str(root / "inputs" / "tender.md"),
                "score_path": str(root / "inputs" / "score.md"),
                "company_path": str(root / "inputs" / "company.md"),
                "template_path": str(root / "inputs" / "template.docx"),
                "template_schema_path": str(root / "workspace" / "template_schema.json"),
            }
            _persist_state(state, update, stage="prepare_inputs", status="ok", message="resume: 复用已导入资料")
            return update
        prepare_inputs(root)
        update = {
            "tender_path": str(root / "inputs" / "tender.md"),
            "score_path": str(root / "inputs" / "score.md"),
            "company_path": str(root / "inputs" / "company.md"),
            "template_path": str(root / "inputs" / "template.docx"),
            "template_schema_path": str(root / "workspace" / "template_schema.json"),
        }
        _persist_state(state, update, stage="prepare_inputs")
        return update
    except Exception as exc:
        _persist_error_state(state, "prepare_inputs", exc)
        raise


def split_docs_node(state) -> dict:
    root = _root(state)
    print("[3/18] 切分文档...")
    _start_stage(state, "split_docs", "切分文档")
    try:
        tender_chunks = root / "workspace" / "chunks" / "tender_chunks.json"
        company_chunks = root / "workspace" / "chunks" / "company_chunks.json"
        if _is_resume(state) and stage_resume_ready(root, "split_docs"):
            update = {"tender_chunks_path": str(tender_chunks), "company_chunks_path": str(company_chunks)}
            _persist_state(state, update, stage="split_docs", status="ok", message="resume: 复用已切分 chunks")
            return update
        tender_chunks_path, company_chunks_path = split_docs(root)
        update = {"tender_chunks_path": str(tender_chunks_path), "company_chunks_path": str(company_chunks_path)}
        _persist_state(state, update, stage="split_docs")
        return update
    except Exception as exc:
        _persist_error_state(state, "split_docs", exc)
        raise


def parse_score_node(state) -> dict:
    root = _root(state)
    print("[4/18] 解析评分标准...")
    _start_stage(state, "parse_score", "解析评分标准")
    try:
        score_points_path = root / "workspace" / "score_points.json"
        if _is_resume(state) and stage_resume_ready(root, "parse_score"):
            update = {"score_points_path": str(score_points_path)}
            _persist_state(state, update, stage="parse_score", status="ok", message="resume: 复用评分点结果")
            return update
        score_points_path = score_agent(root)
        update = {"score_points_path": str(score_points_path)}
        _persist_state(state, update, stage="parse_score")
        return update
    except Exception as exc:
        _persist_error_state(state, "parse_score", exc)
        raise


def extract_facts_node(state) -> dict:
    root = _root(state)
    print("[5/18] 提取全局事实...")
    _start_stage(state, "extract_facts", "提取全局事实")
    try:
        global_facts_path = root / "workspace" / "global_facts.json"
        if _is_resume(state) and stage_resume_ready(root, "extract_facts"):
            update = {"global_facts_path": str(global_facts_path)}
            _persist_state(state, update, stage="extract_facts", status="ok", message="resume: 复用全局事实")
            return update
        global_facts_path = fact_agent(root)
        update = {"global_facts_path": str(global_facts_path)}
        _persist_state(state, update, stage="extract_facts")
        return update
    except Exception as exc:
        _persist_error_state(state, "extract_facts", exc)
        raise


def build_template_evidence_node(state) -> dict:
    root = _root(state)
    print("[6/18] 生成模板依据映射...")
    _start_stage(state, "build_template_evidence", "生成模板依据映射")
    try:
        evidence_path = root / "workspace" / "template_evidence_map.json"
        quality_path = root / "workspace" / "template_quality_report.json"
        if _is_resume(state) and stage_resume_ready(root, "build_template_evidence"):
            update = {
                "template_evidence_map_path": str(evidence_path),
                "template_quality_report_path": str(quality_path),
            }
            _persist_state(state, update, stage="build_template_evidence", status="ok", message="resume: 复用模板依据映射")
            return update
        evidence_path, quality_path = build_template_evidence(root)
        update = {
            "template_evidence_map_path": str(evidence_path),
            "template_quality_report_path": str(quality_path),
        }
        _persist_state(state, update, stage="build_template_evidence")
        return update
    except Exception as exc:
        _persist_error_state(state, "build_template_evidence", exc)
        raise


def generate_outline_node(state) -> dict:
    root = _root(state)
    print("[7/18] 生成大纲...")
    _start_stage(state, "generate_outline", "生成大纲")
    try:
        outline_path = root / "workspace" / "outline.json"
        if _is_resume(state) and stage_resume_ready(root, "generate_outline"):
            update = {"outline_path": str(outline_path)}
            _persist_state(state, update, stage="generate_outline", status="ok", message="resume: 复用大纲")
            return update
        outline_path = outline_agent(root)
        update = {"outline_path": str(outline_path)}
        _persist_state(state, update, stage="generate_outline")
        return update
    except Exception as exc:
        _persist_error_state(state, "generate_outline", exc)
        raise


def plan_chapter_jobs_node(state) -> dict:
    root = _root(state)
    print("[8/18] 生成章节任务...")
    _start_stage(state, "plan_chapter_jobs", "生成章节任务")
    try:
        if _is_resume(state) and stage_resume_ready(root, "plan_chapter_jobs"):
            existing_jobs = _load_jobs_from_disk(root)
            if existing_jobs:
                update = {"chapter_jobs": existing_jobs, "jobs_dir": str(root / "workspace" / "jobs")}
                _persist_state(state, update, stage="plan_chapter_jobs", status="ok", message="resume: 复用章节任务")
                return update
        jobs = plan_chapter_jobs(root)
        update = {"chapter_jobs": jobs, "jobs_dir": str(root / "workspace" / "jobs")}
        _persist_state(state, update, stage="plan_chapter_jobs")
        return update
    except Exception as exc:
        _persist_error_state(state, "plan_chapter_jobs", exc)
        raise


def select_contexts_node(state) -> dict:
    root = _root(state)
    print("[9/18] 选择章节上下文...")
    _start_stage(state, "select_contexts", "选择章节上下文")
    jobs = _state_jobs(state, root)
    expected_chapter_ids = _chapter_ids_from_jobs(jobs)
    existing_context_ids = _existing_chapter_ids(root, "contexts/*_context.json", "_context")
    if _is_resume(state):
        pending_ids = _missing_ids(expected_chapter_ids, existing_context_ids)
        if not pending_ids:
            update = {
                "chapter_jobs": jobs,
                "contexts_dir": str(root / "workspace" / "contexts"),
            }
            _persist_state(state, update, stage="select_contexts", status="ok", message="resume: 复用章节上下文")
            return update
        jobs = [job for job in jobs if stringify(job.get("chapter_id")) in set(pending_ids)]

    errors: list[str] = []
    for job in jobs:
        chapter_id = stringify(job.get("chapter_id"))
        try:
            output_path = select_context_for_job(job, root)
            try:
                context_data = read_json(output_path)
            except Exception as exc:
                errors.append(f"章节 {chapter_id} 上下文结果读取失败: {exc}")
                continue

            for warning in context_data.get("warnings", []):
                errors.append(f"章节 {chapter_id} 上下文警告: {warning}")
        except Exception as exc:
            errors.append(f"章节 {chapter_id} 上下文选择失败: {exc}")

    update = {
        "chapter_jobs": _state_jobs(state, root),
        "contexts_dir": str(root / "workspace" / "contexts"),
        "errors": errors,
    }
    _persist_state(
        state,
        update,
        stage="select_contexts",
        status="error" if errors else "ok",
        message="; ".join(errors[:5]),
    )
    if errors:
        raise RuntimeError("；".join(errors[:5]))
    return update


def write_chapters_node(state) -> dict:
    root = _root(state)
    workers = int(state.get("workers") or 2)
    max_retries = max(0, int(state.get("max_retries") or 0))
    print(f"[10/18] 章节 SubAgent 写作... workers={workers}")
    _start_stage(state, "write_chapters", "章节写作")
    effective_workers = max(1, min(workers, 5))
    jobs = _state_jobs(state, root)
    expected_chapter_ids = _chapter_ids_from_jobs(jobs)
    existing_chapter_ids = _existing_chapter_ids(root, "chapters/*.md")
    pending_ids = _missing_ids(expected_chapter_ids, existing_chapter_ids)
    if _is_resume(state) and not pending_ids:
        update = {
            "chapter_jobs": jobs,
            "chapters_dir": str(root / "workspace" / "chapters"),
            "completed_chapters": existing_chapter_ids,
            "failed_chapters": [],
            "_remove_failed_chapter_ids": existing_chapter_ids,
        }
        _persist_state(state, update, stage="write_chapters", status="ok", message="resume: 复用已生成章节")
        return update
    try:
        result = concurrent_write_all(
            root,
            workers=effective_workers,
            chapter_ids=pending_ids or None,
            max_retries=max_retries,
        )
        completed = _merge_string_lists(existing_chapter_ids, result.get("completed", []))
        failed = result.get("failed", [])
        error_messages = [
            f"章节 {item['chapter_id']} 写作失败(已重试 {item.get('attempts', 1)} 次): {item['error']}"
            for item in failed
        ]
        update = {
            "chapter_jobs": jobs,
            "chapters_dir": str(root / "workspace" / "chapters"),
            "completed_chapters": completed,
            "failed_chapters": failed,
            "errors": error_messages,
            "_remove_failed_chapter_ids": completed,
        }
        _persist_state(
            state,
            update,
            stage="write_chapters",
            status="error" if failed else "ok",
            message="；".join(error_messages[:5]),
        )
        if failed:
            raise RuntimeError("；".join(error_messages))
        return update
    except Exception as exc:
        if "update" not in locals():
            update = {
                "chapters_dir": str(root / "workspace" / "chapters"),
                "failed_chapters": [{"chapter_id": "", "error": str(exc)}],
                "errors": [str(exc)],
            }
            _persist_state(
                state,
                update,
                stage="write_chapters",
                status="error",
                message=str(exc),
            )
        raise


def review_fix_chapters_node(state) -> dict:
    root = _root(state)
    print("[11/18] 审核并自动改稿...")
    _start_stage(state, "review_fix_chapters", "审核并自动改稿")
    jobs = _state_jobs(state, root)
    expected_chapter_ids = _chapter_ids_from_jobs(jobs)
    existing_review_ids = _existing_chapter_ids(root, "reviews/*_review.json", "_review")
    if _is_resume(state):
        pending_ids = _missing_ids(expected_chapter_ids, existing_review_ids)
        if not pending_ids:
            update = {
                "chapter_jobs": jobs,
                "reviews_dir": str(root / "workspace" / "reviews"),
                "rewrites_dir": str(root / "workspace" / "rewrites"),
            }
            _persist_state(state, update, stage="review_fix_chapters", status="ok", message="resume: 复用审核结果")
            return update
    try:
        review_fix_all(root)
        update = {
            "chapter_jobs": jobs,
            "reviews_dir": str(root / "workspace" / "reviews"),
            "rewrites_dir": str(root / "workspace" / "rewrites"),
        }
        _persist_state(state, update, stage="review_fix_chapters")
        return update
    except Exception as exc:
        update = {
            "reviews_dir": str(root / "workspace" / "reviews"),
            "rewrites_dir": str(root / "workspace" / "rewrites"),
            "errors": [str(exc)],
        }
        _persist_state(
            state,
            update,
            stage="review_fix_chapters",
            status="error",
            message=str(exc),
        )
        raise


def build_score_coverage_matrix_node(state) -> dict:
    root = _root(state)
    print("[13/18] 生成评分点覆盖矩阵...")
    _start_stage(state, "build_score_coverage_matrix", "生成评分点覆盖矩阵")
    try:
        matrix_path = root / "workspace" / "score_coverage_matrix.json"
        if _is_resume(state) and stage_resume_ready(root, "build_score_coverage_matrix"):
            update = {"score_coverage_matrix_path": str(matrix_path)}
            _persist_state(state, update, stage="build_score_coverage_matrix", status="ok", message="resume: 复用评分点覆盖矩阵")
            return update
        matrix_path = build_score_coverage_matrix(root)
        update = {"score_coverage_matrix_path": str(matrix_path)}
        _persist_state(state, update, stage="build_score_coverage_matrix")
        return update
    except Exception as exc:
        _persist_error_state(state, "build_score_coverage_matrix", exc)
        raise


def build_source_trace_index_node(state) -> dict:
    root = _root(state)
    print("[12/18] 生成来源追溯索引...")
    _start_stage(state, "build_source_trace_index", "生成来源追溯索引")
    try:
        trace_index_path = root / "workspace" / "source_trace_index.json"
        if _is_resume(state) and stage_resume_ready(root, "build_source_trace_index"):
            update = {"source_trace_index_path": str(trace_index_path)}
            _persist_state(state, update, stage="build_source_trace_index", status="ok", message="resume: 复用来源追溯索引")
            return update
        trace_index_path = build_source_trace_index(root)
        update = {"source_trace_index_path": str(trace_index_path)}
        _persist_state(state, update, stage="build_source_trace_index")
        return update
    except Exception as exc:
        _persist_error_state(state, "build_source_trace_index", exc)
        raise


def summarize_chapters_node(state) -> dict:
    root = _root(state)
    print("[14/18] 生成章节摘要...")
    _start_stage(state, "summarize_chapters", "生成章节摘要")
    jobs = _state_jobs(state, root)
    completed_ids = _existing_chapter_ids(root, "chapters/*.md")
    expected_summary_ids = completed_ids if completed_ids else _chapter_ids_from_jobs(jobs)
    existing_summary_ids = _existing_chapter_ids(root, "summaries/*_summary.json", "_summary")
    pending_ids = _missing_ids(expected_summary_ids, existing_summary_ids)
    if _is_resume(state) and not pending_ids:
        update = {
            "chapter_jobs": jobs,
            "summaries_dir": str(root / "workspace" / "summaries"),
        }
        _persist_state(state, update, stage="summarize_chapters", status="ok", message="resume: 复用章节摘要")
        return update

    completed_set = set(pending_ids if _is_resume(state) else completed_ids)
    errors: list[str] = []
    for job in jobs:
        chapter_id = stringify(job.get("chapter_id"))
        if chapter_id not in completed_set:
            continue
        try:
            summarize_chapter(chapter_id, root)
        except Exception as exc:
            errors.append(f"章节 {chapter_id} 摘要生成失败: {exc}")

    update = {
        "chapter_jobs": jobs,
        "summaries_dir": str(root / "workspace" / "summaries"),
        "errors": errors,
    }
    _persist_state(
        state,
        update,
        stage="summarize_chapters",
        status="error" if errors else "ok",
        message="; ".join(errors[:5]),
    )
    if errors:
        raise RuntimeError("；".join(errors[:5]))
    return update


def global_review_node(state) -> dict:
    root = _root(state)
    print("[15/18] 全文一致性审核...")
    _start_stage(state, "global_review", "全文一致性审核")
    try:
        global_review_path = root / "workspace" / "global_review.json"
        if _is_resume(state) and stage_resume_ready(root, "global_review"):
            update = {"global_review_path": str(global_review_path)}
            _persist_state(state, update, stage="global_review", status="ok", message="resume: 复用全文审核结果")
            return update
        global_review_path = global_review_agent(root)
        review_data = read_json(global_review_path)
        review_status = final_review_status(review_data)
        update = {"global_review_path": str(global_review_path)}
        _persist_state(state, update, stage="global_review", status=review_status, message="need_manual_review" if review_status == "warn" else "")
        return update
    except Exception as exc:
        _persist_error_state(state, "global_review", exc)
        raise


def build_markdown_node(state) -> dict:
    root = _root(state)
    print("[16/18] 拼接 Markdown...")
    _start_stage(state, "build_markdown", "拼接 Markdown")
    try:
        final_md_path = root / "outputs" / "final.md"
        if _is_resume(state) and stage_resume_ready(root, "build_markdown"):
            update = {"final_md_path": str(final_md_path)}
            _persist_state(state, update, stage="build_markdown", status="ok", message="resume: 复用 Markdown 输出")
            return update
        final_md_path = build_markdown(root)
        update = {"final_md_path": str(final_md_path)}
        _persist_state(state, update, stage="build_markdown")
        return update
    except Exception as exc:
        _persist_error_state(state, "build_markdown", exc)
        raise


def build_docx_node(state) -> dict:
    root = _root(state)
    print("[17/18] 生成 Word...")
    _start_stage(state, "build_docx", "生成 Word")
    try:
        final_docx_path = root / "outputs" / "final.docx"
        template_fill_report_path = root / "workspace" / "template_fill_report.json"
        if _is_resume(state) and stage_resume_ready(root, "build_docx"):
            update = {
                "final_docx_path": str(final_docx_path),
                "template_fill_report_path": str(template_fill_report_path),
            }
            _persist_state(state, update, stage="build_docx", status="ok", message="resume: 复用 Word 输出")
            return update
        final_docx_path = build_docx(root)
        validate_template_fill_report(root)
        update = {
            "final_docx_path": str(final_docx_path),
            "template_fill_report_path": str(template_fill_report_path),
        }
        _persist_state(state, update, stage="build_docx")
        return update
    except Exception as exc:
        _persist_error_state(state, "build_docx", exc)
        raise


def check_format_node(state) -> dict:
    root = _root(state)
    print("[18/18] 检查输出格式...")
    _start_stage(state, "check_format", "检查输出格式")
    try:
        report_path = root / "workspace" / "format_check_report.json"
        if _is_resume(state) and stage_resume_ready(root, "check_format"):
            update = {"format_check_report_path": str(report_path)}
            _persist_state(state, update, stage="check_format", status="ok", message="resume: 复用格式检查报告")
            return update
        report_path = check_output_format(root)
        update = {"format_check_report_path": str(report_path)}
        _persist_state(state, update, stage="check_format")
        return update
    except Exception as exc:
        _persist_error_state(state, "check_format", exc)
        raise

