from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from chunk_ranker import rank_chunks_for_job, rank_for_job_separate
from concurrency import chapter_workers_scope, clamp_workers, workers_default
from context_budget import summarize_for_prompt
from file_loader import load_global_facts, load_score_points, load_template_evidence_map
from llm_client import chat
from manual_review import (
    manual_review_context_for_chapter,
    manual_review_contexts_for_chapters,
)
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import (
    compact_json,
    parse_json_from_model,
    project_root,
    read_json,
    select_score_points,
    stringify,
    write_json,
)

MAX_RANKED_CONTEXT_CHARS = 18000
MAX_RANKED_CHUNKS_PER_SIDE = 30
CONTEXT_CHECKPOINT_FILE = "select_contexts_checkpoint.json"
CONTEXT_SELECTOR_ROLE = "chapter_context_selector"
_BATCH_LOCKS: dict[str, threading.Lock] = {}
_BATCH_LOCKS_GUARD = threading.Lock()
_SHARED_INPUT_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_SHARED_INPUT_CACHE_LOCK = threading.Lock()


class ContextSelectionBatchError(RuntimeError):
    def __init__(self, failed: list[dict[str, Any]], completed: list[str]) -> None:
        self.failed = failed
        self.completed = completed
        ids = [str(item.get("chapter_id") or "") for item in failed]
        super().__init__(f"上下文选择失败章节: {ids}")


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_path(root: Path) -> Path:
    return root / "workspace" / "contexts" / CONTEXT_CHECKPOINT_FILE


def load_context_selection_checkpoint(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    path = _checkpoint_path(root)
    if not path.exists():
        return {}
    try:
        value = read_json(path)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _persist_context_checkpoint(root: Path, checkpoint: dict[str, Any]) -> None:
    write_json(_checkpoint_path(root), checkpoint)
    try:
        from agent.activity import set_context_selection_progress

        set_context_selection_progress(root, checkpoint)
    except Exception:
        pass


def reconcile_interrupted_context_selection(root: Path | None = None) -> dict[str, Any]:
    """Close an orphaned context batch without automatically spending LLM tokens."""
    root = root or project_root()
    checkpoint = load_context_selection_checkpoint(root)
    if str(checkpoint.get("status") or "") != "running":
        return checkpoint
    expected = [stringify(item) for item in checkpoint.get("expected_chapter_ids", [])]
    try:
        jobs_by_id = {
            stringify(job.get("chapter_id")): job
            for job in (
                read_json(path)
                for path in sorted((root / "workspace" / "jobs").glob("*.json"))
            )
            if isinstance(job, dict)
        }
        shared = _load_shared_inputs(root)
        manual_reviews = manual_review_contexts_for_chapters(root, expected)
        completed = [
            chapter_id
            for chapter_id in expected
            if chapter_id in jobs_by_id
            and (
                context_output_valid_for_job(
                    jobs_by_id[chapter_id],
                    root,
                    shared_inputs=shared,
                    require_fingerprint=True,
                    manual_review=manual_reviews.get(chapter_id),
                )
                or _migrate_legacy_context_checkpoint(
                    jobs_by_id[chapter_id],
                    root,
                    shared_inputs=shared,
                    manual_review=manual_reviews.get(chapter_id),
                )
            )
        ]
        checkpoint["completed_chapter_ids"] = completed
        checkpoint["failed"] = [
            item
            for item in checkpoint.get("failed", [])
            if stringify(item.get("chapter_id")) not in set(completed)
        ]
    except Exception as exc:
        checkpoint["reconcile_warning"] = str(exc)
    checkpoint["status"] = "interrupted"
    checkpoint["interrupted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    checkpoint["message"] = "服务重启中断；请显式继续上下文选择"
    _persist_context_checkpoint(root, checkpoint)
    return checkpoint


def _shared_input_signature(root: Path) -> tuple[Any, ...]:
    paths = [
        root / "workspace" / "chunks" / "tender_chunks.json",
        root / "workspace" / "chunks" / "company_chunks.json",
        root / "workspace" / "chunks" / "reference_chunks.json",
        root / "inputs" / "writing_brief.md",
        root / "workspace" / "score_points.json",
        root / "workspace" / "global_facts.json",
        root / "workspace" / "template_evidence_map.json",
        root / "workspace" / "project_profile.json",
    ]
    prompts_dir = root / "prompts"
    if prompts_dir.exists():
        paths.extend(sorted(prompts_dir.glob("*.md")))
    signature: list[Any] = []
    for path in paths:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None, None))
    return tuple(signature)


def _load_shared_inputs(root: Path) -> dict[str, Any]:
    root = root.resolve()
    signature = _shared_input_signature(root)
    cache_key = str(root)
    with _SHARED_INPUT_CACHE_LOCK:
        cached = _SHARED_INPUT_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    tender_chunks = read_json(root / "workspace" / "chunks" / "tender_chunks.json")
    company_chunks = read_json(root / "workspace" / "chunks" / "company_chunks.json")
    reference_path = root / "workspace" / "chunks" / "reference_chunks.json"
    reference_chunks = read_json(reference_path) if reference_path.exists() else []
    if not isinstance(tender_chunks, list) or not isinstance(company_chunks, list):
        raise ValueError("文档切分结果必须是 JSON 数组。")
    if not isinstance(reference_chunks, list):
        reference_chunks = []
    writing_brief_path = root / "inputs" / "writing_brief.md"
    writing_brief = (
        writing_brief_path.read_text(encoding="utf-8").strip()
        if writing_brief_path.exists()
        else ""
    )
    prompt = load_agent_prompt(root, "chapter_context_selector")
    shared = {
        "tender_chunks": tender_chunks,
        "company_chunks": company_chunks,
        "reference_chunks": reference_chunks,
        "writing_brief": writing_brief,
        "score_points": load_score_points(root),
        "global_facts": load_global_facts(root),
        "template_evidence": load_template_evidence_map(root),
        "prompt": prompt,
    }
    shared["shared_input_fingerprint"] = _stable_hash(shared)
    with _SHARED_INPUT_CACHE_LOCK:
        _SHARED_INPUT_CACHE[cache_key] = (signature, shared)
    return shared


def context_input_fingerprint(
    job: dict[str, Any],
    root: Path,
    *,
    shared_inputs: dict[str, Any] | None = None,
    manual_review: dict[str, Any] | None = None,
) -> str:
    shared = shared_inputs or _load_shared_inputs(root)
    chapter_id = stringify(job.get("chapter_id"))
    review = manual_review
    if review is None:
        review = manual_review_context_for_chapter(root, chapter_id)
    return _stable_hash(
        {
            "chapter_job": job,
            "manual_review": review,
            "shared_input_fingerprint": shared.get("shared_input_fingerprint"),
        }
    )


def context_output_valid_for_job(
    job: dict[str, Any],
    root: Path,
    *,
    shared_inputs: dict[str, Any] | None = None,
    require_fingerprint: bool | None = None,
    manual_review: dict[str, Any] | None = None,
) -> bool:
    chapter_id = stringify(job.get("chapter_id"))
    if not chapter_id:
        return False
    path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    try:
        payload = read_json(path)
    except Exception:
        return False
    if not isinstance(payload, dict) or stringify(payload.get("chapter_id")) != chapter_id:
        return False
    if require_fingerprint is None:
        require_fingerprint = bool(load_context_selection_checkpoint(root))
    # One-version compatibility: workspaces created before durable context
    # checkpoints keep their prior validity semantics. New/resumed batches are
    # always fingerprint-strict.
    if not require_fingerprint:
        return True
    if not isinstance(payload.get("selected_tender_chunks"), list):
        return False
    if not isinstance(payload.get("selected_company_chunks"), list):
        return False
    if not isinstance(payload.get("warnings"), list):
        return False
    meta = payload.get("selection_meta")
    if not isinstance(meta, dict):
        return False
    expected = context_input_fingerprint(
        job,
        root,
        shared_inputs=shared_inputs,
        manual_review=manual_review,
    )
    return stringify(meta.get("input_fingerprint")) == expected


def _migrate_legacy_context_checkpoint(
    job: dict[str, Any],
    root: Path,
    *,
    shared_inputs: dict[str, Any],
    manual_review: dict[str, Any] | None = None,
) -> bool:
    """Adopt a structurally valid pre-checkpoint context as this run's baseline.

    Old workspaces did not record an input fingerprint. Requiring one without a
    compatibility migration makes every historical context incur a new LLM
    request on the first resume. The migration is deliberately one-way and
    atomic: after adoption, all later job/material/review/prompt changes are
    detected by the normal fingerprint comparison.
    """
    chapter_id = stringify(job.get("chapter_id"))
    if not chapter_id:
        return False
    path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    try:
        payload = read_json(path)
    except Exception:
        return False
    if not isinstance(payload, dict) or stringify(payload.get("chapter_id")) != chapter_id:
        return False
    if not isinstance(payload.get("selected_tender_chunks"), list):
        return False
    if not isinstance(payload.get("selected_company_chunks"), list):
        return False
    warnings = payload.get("warnings")
    if warnings is None:
        payload["warnings"] = []
    elif not isinstance(warnings, list):
        return False
    meta = payload.get("selection_meta")
    if meta is None:
        meta = {}
    elif not isinstance(meta, dict):
        return False
    if stringify(meta.get("input_fingerprint")):
        return False
    meta = {
        **meta,
        "input_fingerprint": context_input_fingerprint(
            job,
            root,
            shared_inputs=shared_inputs,
            manual_review=manual_review,
        ),
        "fingerprint_source": "legacy_context_baseline",
        "fingerprint_migrated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.localtime(),
        ),
    }
    payload["selection_meta"] = meta
    write_json(path, payload)
    return True


def valid_context_ids(
    root: Path,
    jobs: list[dict[str, Any]] | None = None,
) -> set[str]:
    shared = _load_shared_inputs(root)
    checkpoint = load_context_selection_checkpoint(root)
    require_fingerprint = bool(checkpoint)
    if jobs is None:
        jobs = [
            read_json(path)
            for path in sorted((root / "workspace" / "jobs").glob("*.json"))
        ]
    manual_reviews = manual_review_contexts_for_chapters(
        root,
        [stringify(job.get("chapter_id")) for job in jobs],
    )
    valid = {
        stringify(job.get("chapter_id"))
        for job in jobs
        if context_output_valid_for_job(
            job,
            root,
            shared_inputs=shared,
            require_fingerprint=require_fingerprint,
            manual_review=manual_reviews.get(stringify(job.get("chapter_id"))),
        )
    }
    if str(checkpoint.get("status") or "") in {
        "running",
        "partial_failed",
        "interrupted",
    }:
        # Per-chapter files remain reusable by the context dispatcher, but no
        # downstream stage may consume a partially completed/interrupted batch.
        return set()
    return valid


def _chunk_catalog(chunks: list[dict[str, Any]], preview_chars: int = 700) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for chunk in chunks:
        content = stringify(chunk.get("content"))
        catalog.append(
            {
                "id": stringify(chunk.get("id")),
                "source": stringify(chunk.get("source")),
                "title_path": chunk.get("title_path", []),
                "keywords": chunk.get("keywords", []),
                "char_count": chunk.get("char_count", len(content)),
                "preview": content[:preview_chars],
            }
        )
    return catalog


def _chunk_ids_from_template_tasks(job: dict[str, Any], key: str) -> list[str]:
    ids: list[str] = []
    for task in job.get("template_tasks", []):
        if not isinstance(task, dict):
            continue
        for chunk_id in task.get(key, []):
            chunk_id = stringify(chunk_id)
            if chunk_id and chunk_id not in ids:
                ids.append(chunk_id)
    return ids


def _prepend_chunks_by_id(
    chunks: list[dict[str, Any]],
    preferred_ids: list[str],
    all_chunks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not preferred_ids:
        return chunks
    index = {stringify(chunk.get("id")): chunk for chunk in (all_chunks or chunks)}
    selected = [index[chunk_id] for chunk_id in preferred_ids if chunk_id in index]
    selected_ids = {stringify(chunk.get("id")) for chunk in selected}
    return selected + [chunk for chunk in chunks if stringify(chunk.get("id")) not in selected_ids]


def _compact_template_tasks(job: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in job.get("template_tasks", []):
        if not isinstance(task, dict):
            continue
        tasks.append(
            {
                "id": stringify(task.get("id")),
                "type": stringify(task.get("type")),
                "heading_id": stringify(task.get("heading_id")),
                "title": stringify(task.get("title")),
                "label": stringify(task.get("label")),
                "semantic_key": stringify(task.get("semantic_key")),
                "status": stringify(task.get("status")),
                "evidence_sources": task.get("evidence_sources", []),
                "tender_chunk_ids": task.get("tender_chunk_ids", []),
                "company_chunk_ids": task.get("company_chunk_ids", []),
                "score_point_ids": task.get("score_point_ids", []),
                "notes": task.get("notes", []),
            }
        )
    return tasks


def _normalize_selected(
    raw_items: Any,
    valid_ids: set[str],
    limit: int,
    warnings: list[str],
    label: str,
) -> list[dict[str, str]]:
    if not isinstance(raw_items, list):
        return []

    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, str):
            chunk_id = stringify(item)
            reason = ""
        elif isinstance(item, dict):
            chunk_id = stringify(item.get("id"))
            reason = stringify(item.get("reason"))
        else:
            continue

        if not chunk_id:
            continue
        if chunk_id not in valid_ids:
            warnings.append(f"{label} 选择了不存在的 chunk id，已过滤: {chunk_id}")
            continue
        if chunk_id in seen:
            continue
        selected.append({"id": chunk_id, "reason": reason})
        seen.add(chunk_id)
        if len(selected) >= limit:
            break
    return selected


def _trim_catalog(catalog: list[dict[str, Any]], *, max_chars: int, max_items: int, label: str, warnings: list[str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total_chars = 0
    for item in catalog[:max_items]:
        encoded = compact_json(item)
        if selected and total_chars + len(encoded) > max_chars:
            warnings.append(f"{label} 候选已按预算截断，保留 {len(selected)} 条。")
            break
        selected.append(item)
        total_chars += len(encoded)
    return selected


def select_context_for_job(
    job: dict[str, Any],
    root: Path | None = None,
    *,
    shared_inputs: dict[str, Any] | None = None,
) -> Path:
    root = root or project_root()
    chapter_id = stringify(job.get("chapter_id"))
    if not chapter_id:
        raise ValueError("章节任务缺少 chapter_id")
    shared = shared_inputs or _load_shared_inputs(root)
    tender_chunks = shared["tender_chunks"]
    company_chunks = shared["company_chunks"]
    reference_chunks = shared.get("reference_chunks", [])
    writing_brief = stringify(shared.get("writing_brief"))
    score_points = shared["score_points"]
    global_facts = shared["global_facts"]
    template_evidence = shared["template_evidence"]
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    warnings: list[str] = []
    manual_review = manual_review_context_for_chapter(root, chapter_id)
    input_fingerprint = context_input_fingerprint(
        job,
        root,
        shared_inputs=shared,
        manual_review=manual_review,
    )

    ranked_tender = tender_chunks
    ranked_company = company_chunks
    ranked_reference = reference_chunks
    try:
        ranked_result = rank_for_job_separate(job, related_score_points, tender_chunks, company_chunks)
        ranked_tender = [c for c in tender_chunks if any(r["id"] == c["id"] for r in ranked_result["tender_top_chunks"])]
        ranked_company = [c for c in company_chunks if any(r["id"] == c["id"] for r in ranked_result["company_top_chunks"])]
        ranked_reference = rank_chunks_for_job(
            job,
            related_score_points,
            reference_chunks,
            top_k=MAX_RANKED_CHUNKS_PER_SIDE,
        )
        ranked_tender = _prepend_chunks_by_id(ranked_tender, _chunk_ids_from_template_tasks(job, "tender_chunk_ids"), tender_chunks)
        ranked_company = _prepend_chunks_by_id(ranked_company, _chunk_ids_from_template_tasks(job, "company_chunk_ids"), company_chunks)
        ranked_tender = _prepend_chunks_by_id(ranked_tender, manual_review.get("preferred_tender_chunk_ids", []), tender_chunks)
        ranked_company = _prepend_chunks_by_id(ranked_company, manual_review.get("preferred_company_chunk_ids", []), company_chunks)
        if not ranked_tender:
            ranked_tender = tender_chunks[:30]
            warnings.append("chunk-ranker 未选出 tender chunks，已回退到前 30 个。")
        if not ranked_company:
            ranked_company = company_chunks[:30]
            warnings.append("chunk-ranker 未选出 company chunks，已回退到前 30 个。")
        if reference_chunks and not ranked_reference:
            ranked_reference = reference_chunks[:30]
            warnings.append("chunk-ranker 未选出 reference chunks，已回退到前 30 个。")
        ranked_result["reference_top_chunks"] = [
            {
                "id": c["id"],
                "rank_score": c.get("rank_score", 0),
                "rank_reasons": c.get("rank_reasons", []),
            }
            for c in ranked_reference
        ]
        ranked_path = root / "workspace" / "contexts" / f"{chapter_id}_ranked_chunks.json"
        write_json(ranked_path, ranked_result)
        print(
            f"[完成] 章节 {chapter_id} chunk-ranker: tender {len(ranked_result['tender_top_chunks'])} "
            f"/ company {len(ranked_result['company_top_chunks'])} "
            f"/ reference {len(ranked_result['reference_top_chunks'])}"
        )
    except Exception as exc:
        warnings.append(f"chunk-ranker 失败，已回退到全量 chunks: {exc}")
        print(f"[警告] 章节 {chapter_id} chunk-ranker 失败: {exc}")

    tender_catalog = _trim_catalog(
        _chunk_catalog(ranked_tender),
        max_chars=MAX_RANKED_CONTEXT_CHARS // 3,
        max_items=MAX_RANKED_CHUNKS_PER_SIDE,
        label="招标文件",
        warnings=warnings,
    )
    company_catalog = _trim_catalog(
        _chunk_catalog(ranked_company),
        max_chars=MAX_RANKED_CONTEXT_CHARS // 3,
        max_items=MAX_RANKED_CHUNKS_PER_SIDE,
        label="公司资料",
        warnings=warnings,
    )
    reference_catalog = _trim_catalog(
        _chunk_catalog(ranked_reference),
        max_chars=MAX_RANKED_CONTEXT_CHARS // 3,
        max_items=MAX_RANKED_CHUNKS_PER_SIDE,
        label="外部参考资料",
        warnings=warnings,
    )

    with agent_run(
        root,
        "select_contexts",
        "chapter_context_selector",
        input_summary={
            "chapter_id": chapter_id,
            "score_point_count": len(related_score_points),
            "tender_candidates": len(tender_catalog),
            "company_candidates": len(company_catalog),
            "reference_candidates": len(reference_catalog),
        },
        chapter_id=chapter_id,
        temperature=0.1,
    ):
        prompt = load_agent_prompt(root, "chapter_context_selector")
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请为当前章节选择最相关的资料片段 ID。\n\n"
                        "## 章节任务\n\n"
                        f"{compact_json(job)}\n\n"
                        "## 绑定评分点\n\n"
                        f"{compact_json(related_score_points)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}\n\n"
                        "## 项目写作要求（用户经验与编写偏好）\n\n"
                        f"{writing_brief or '未提供'}\n\n"
                        "## 当前章节相关模板任务\n\n"
                        f"{compact_json(_compact_template_tasks(job))}\n\n"
                        "## 人工复核补充说明\n\n"
                        f"{compact_json(manual_review)}\n\n"
                        "## 模板依据映射摘要\n\n"
                        f"{compact_json({'summary': template_evidence.get('summary', {})})}\n\n"
                        "## 上下文预算摘要\n\n"
                        f"{summarize_for_prompt({'max_context_chars': MAX_RANKED_CONTEXT_CHARS, 'max_chunks_per_side': MAX_RANKED_CHUNKS_PER_SIDE}, 800)}\n\n"
                        "## 招标文件 chunk 目录\n\n"
                        f"{compact_json(tender_catalog)}\n\n"
                        "## 公司资料 chunk 目录\n\n"
                        f"{compact_json(company_catalog)}\n\n"
                        "## 外部参考资料 chunk 目录\n\n"
                        f"{compact_json(reference_catalog)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    data = parse_json_from_model(raw, root / "workspace" / f"debug_select_context_{chapter_id}_raw.txt")

    tender_ids = {stringify(chunk.get("id")) for chunk in ranked_tender}
    company_ids = {stringify(chunk.get("id")) for chunk in ranked_company}
    reference_ids = {stringify(chunk.get("id")) for chunk in ranked_reference}
    context = {
        "chapter_id": chapter_id,
        "selected_tender_chunks": _normalize_selected(
            data.get("selected_tender_chunks"),
            tender_ids,
            8,
            warnings,
            "招标文件",
        ),
        "selected_company_chunks": _normalize_selected(
            data.get("selected_company_chunks"),
            company_ids,
            8,
            warnings,
            "公司资料",
        ),
        "selected_reference_chunks": _normalize_selected(
            data.get("selected_reference_chunks"),
            reference_ids,
            8,
            warnings,
            "外部参考资料",
        ),
        "warnings": warnings,
        "selection_meta": {
            "tender_candidates_total": len(ranked_tender),
            "company_candidates_total": len(ranked_company),
            "reference_candidates_total": len(ranked_reference),
            "tender_candidates_in_prompt": len(tender_catalog),
            "company_candidates_in_prompt": len(company_catalog),
            "reference_candidates_in_prompt": len(reference_catalog),
            "max_context_chars": MAX_RANKED_CONTEXT_CHARS,
            "max_chunks_per_side": MAX_RANKED_CHUNKS_PER_SIDE,
            "dropped_reason": "budget_trimmed" if len(tender_catalog) < len(ranked_tender) or len(company_catalog) < len(ranked_company) else "",
            "input_fingerprint": input_fingerprint,
        },
    }

    output_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    write_json(output_path, context)
    if warnings:
        print(f"[警告] 章节 {chapter_id} 上下文选择存在 {len(warnings)} 条警告。")
    print(f"[完成] 已选择章节 {chapter_id} 上下文: {output_path}")
    return output_path


def _validate_batch_jobs(jobs: list[dict[str, Any]]) -> list[str]:
    chapter_ids: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        chapter_id = stringify(job.get("chapter_id"))
        if not chapter_id:
            raise ValueError("上下文选择任务存在空 chapter_id")
        if chapter_id in seen:
            raise ValueError(f"上下文选择任务存在重复 chapter_id: {chapter_id}")
        seen.add(chapter_id)
        chapter_ids.append(chapter_id)
    return chapter_ids


def _run_job_with_retry(
    job: dict[str, Any],
    root: Path,
    shared_inputs: dict[str, Any],
    max_retries: int,
    attempt_offset: int = 0,
) -> tuple[str, Path | None, str | None, int]:
    from agent.activity import mark_agent

    chapter_id = stringify(job.get("chapter_id"))
    attempts = max(1, int(max_retries) + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        recorded_attempt = attempt_offset + attempt
        mark_agent(
            root,
            role=CONTEXT_SELECTOR_ROLE,
            chapter_id=chapter_id,
            status="running",
            message=f"第 {attempt}/{attempts} 次选择中",
            attempt=recorded_attempt,
        )
        try:
            path = select_context_for_job(job, root, shared_inputs=shared_inputs)
            mark_agent(
                root,
                role=CONTEXT_SELECTOR_ROLE,
                chapter_id=chapter_id,
                status="done",
                message="上下文选择完成",
                attempt=recorded_attempt,
            )
            return chapter_id, path, None, recorded_attempt
        except Exception as exc:
            last_error = str(exc)
            mark_agent(
                root,
                role=CONTEXT_SELECTOR_ROLE,
                chapter_id=chapter_id,
                status="running" if attempt < attempts else "failed",
                message=f"失败: {last_error[:120]}",
                attempt=recorded_attempt,
            )
    return chapter_id, None, last_error, attempt_offset + attempts


def _select_contexts_for_jobs_unlocked(
    jobs: list[dict[str, Any]],
    root: Path | None = None,
    workers: int | None = None,
    max_retries: int = 0,
    resume: bool = True,
    force: bool = False,
) -> list[Path]:
    root = root or project_root()
    chapter_ids = _validate_batch_jobs(jobs)
    if not jobs:
        return []

    shared = _load_shared_inputs(root)
    previous_checkpoint = load_context_selection_checkpoint(root)
    manual_reviews = manual_review_contexts_for_chapters(root, chapter_ids)
    reusable: set[str] = set()
    migrated: set[str] = set()
    if resume and not force:
        for job in jobs:
            chapter_id = stringify(job.get("chapter_id"))
            if context_output_valid_for_job(
                job,
                root,
                shared_inputs=shared,
                require_fingerprint=True,
                manual_review=manual_reviews.get(chapter_id),
            ):
                reusable.add(chapter_id)
                continue
            if _migrate_legacy_context_checkpoint(
                job,
                root,
                shared_inputs=shared,
                manual_review=manual_reviews.get(chapter_id),
            ):
                reusable.add(chapter_id)
                migrated.add(chapter_id)
        if str(previous_checkpoint.get("status") or "") in {
            "running",
            "partial_failed",
            "interrupted",
        }:
            previously_completed = {
                stringify(item)
                for item in previous_checkpoint.get("completed_chapter_ids", [])
            }
            # Migrated legacy files predate the batch ledger and therefore cannot
            # appear in its completed list. They are still valid checkpoints.
            reusable.intersection_update(previously_completed | migrated)
    pending_jobs = [
        job for job in jobs
        if stringify(job.get("chapter_id")) not in reusable
    ]
    output_by_id = {
        chapter_id: root / "workspace" / "contexts" / f"{chapter_id}_context.json"
        for chapter_id in reusable
    }
    batch_attempt = int(previous_checkpoint.get("batch_attempt") or 0) + 1
    checkpoint = {
        "batch_id": uuid.uuid4().hex,
        "previous_batch_id": stringify(previous_checkpoint.get("batch_id")),
        "batch_attempt": batch_attempt,
        "status": "running" if pending_jobs else "completed",
        "resume": bool(resume),
        "force": bool(force),
        "shared_input_fingerprint": shared["shared_input_fingerprint"],
        "expected_chapter_ids": chapter_ids,
        "completed_chapter_ids": [
            chapter_id for chapter_id in chapter_ids if chapter_id in reusable
        ],
        "migrated_legacy_chapter_ids": [
            chapter_id for chapter_id in chapter_ids if chapter_id in migrated
        ],
        "failed": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "message": "",
    }
    _persist_context_checkpoint(root, checkpoint)
    if not pending_jobs:
        checkpoint["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        checkpoint["message"] = "全部章节上下文检查点有效"
        _persist_context_checkpoint(root, checkpoint)
        return [output_by_id[chapter_id] for chapter_id in chapter_ids]

    from agent.activity import begin_phase, end_phase

    pending_ids = [stringify(job.get("chapter_id")) for job in pending_jobs]
    requested = workers_default() if workers is None else clamp_workers(workers)
    failed: list[dict[str, Any]] = []
    with chapter_workers_scope(min(requested, len(pending_jobs))) as effective_workers:
        begin_phase(
            root,
            phase="select_contexts",
            phase_label="上下文选择 SubAgent",
            role=CONTEXT_SELECTOR_ROLE,
            chapter_ids=pending_ids,
        )
        checkpoint["effective_workers"] = effective_workers
        _persist_context_checkpoint(root, checkpoint)
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {
                executor.submit(
                    _run_job_with_retry,
                    job,
                    root,
                    shared,
                    max(0, int(max_retries)),
                    batch_attempt - 1,
                ): stringify(job.get("chapter_id"))
                for job in pending_jobs
            }
            for future in as_completed(futures):
                chapter_id = futures[future]
                try:
                    result_id, path, error, attempts = future.result()
                except Exception as exc:
                    result_id, path, error, attempts = chapter_id, None, str(exc), 1
                if error or path is None:
                    failed.append(
                        {
                            "chapter_id": result_id,
                            "error": error or "未生成上下文文件",
                            "attempts": attempts,
                        }
                    )
                else:
                    output_by_id[result_id] = path
                checkpoint["completed_chapter_ids"] = [
                    cid for cid in chapter_ids if cid in output_by_id
                ]
                checkpoint["failed"] = list(failed)
                _persist_context_checkpoint(root, checkpoint)

        checkpoint["status"] = "partial_failed" if failed else "completed"
        checkpoint["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        checkpoint["message"] = (
            f"成功 {len(output_by_id)} / 失败 {len(failed)} / 总计 {len(chapter_ids)}"
        )
        _persist_context_checkpoint(root, checkpoint)
        end_phase(
            root,
            status="partial_failed" if failed else "done",
            message=checkpoint["message"],
        )

    if failed:
        raise ContextSelectionBatchError(
            failed,
            [chapter_id for chapter_id in chapter_ids if chapter_id in output_by_id],
        )
    return [output_by_id[chapter_id] for chapter_id in chapter_ids]


def select_contexts_for_jobs(
    jobs: list[dict[str, Any]],
    root: Path | None = None,
    workers: int | None = None,
    max_retries: int = 0,
    resume: bool = True,
    force: bool = False,
) -> list[Path]:
    root = (root or project_root()).resolve()
    lock_key = str(root)
    with _BATCH_LOCKS_GUARD:
        batch_lock = _BATCH_LOCKS.setdefault(lock_key, threading.Lock())
    if not batch_lock.acquire(blocking=False):
        raise RuntimeError(f"工作区已有上下文选择批次运行中: {root}")
    try:
        return _select_contexts_for_jobs_unlocked(
            jobs,
            root,
            workers=workers,
            max_retries=max_retries,
            resume=resume,
            force=force,
        )
    finally:
        batch_lock.release()
