from __future__ import annotations

from pathlib import Path
from typing import Any

from chunk_ranker import rank_for_job_separate
from context_budget import summarize_for_prompt
from file_loader import load_global_facts, load_score_points, load_template_evidence_map
from llm_client import chat
from manual_review import manual_review_context_for_chapter
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


def select_context_for_job(job: dict[str, Any], root: Path | None = None) -> Path:
    root = root or project_root()
    chapter_id = stringify(job.get("chapter_id"))
    tender_chunks = read_json(root / "workspace" / "chunks" / "tender_chunks.json")
    company_chunks = read_json(root / "workspace" / "chunks" / "company_chunks.json")
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    template_evidence = load_template_evidence_map(root)
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    warnings: list[str] = []
    manual_review = manual_review_context_for_chapter(root, chapter_id)

    if not isinstance(tender_chunks, list) or not isinstance(company_chunks, list):
        raise ValueError("文档切分结果必须是 JSON 数组。")

    ranked_tender = tender_chunks
    ranked_company = company_chunks
    try:
        ranked_result = rank_for_job_separate(job, related_score_points, tender_chunks, company_chunks)
        ranked_tender = [c for c in tender_chunks if any(r["id"] == c["id"] for r in ranked_result["tender_top_chunks"])]
        ranked_company = [c for c in company_chunks if any(r["id"] == c["id"] for r in ranked_result["company_top_chunks"])]
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
        ranked_path = root / "workspace" / "contexts" / f"{chapter_id}_ranked_chunks.json"
        write_json(ranked_path, ranked_result)
        print(f"[完成] 章节 {chapter_id} chunk-ranker: tender {len(ranked_result['tender_top_chunks'])} / company {len(ranked_result['company_top_chunks'])}")
    except Exception as exc:
        warnings.append(f"chunk-ranker 失败，已回退到全量 chunks: {exc}")
        print(f"[警告] 章节 {chapter_id} chunk-ranker 失败: {exc}")

    tender_catalog = _trim_catalog(
        _chunk_catalog(ranked_tender),
        max_chars=MAX_RANKED_CONTEXT_CHARS // 2,
        max_items=MAX_RANKED_CHUNKS_PER_SIDE,
        label="招标文件",
        warnings=warnings,
    )
    company_catalog = _trim_catalog(
        _chunk_catalog(ranked_company),
        max_chars=MAX_RANKED_CONTEXT_CHARS // 2,
        max_items=MAX_RANKED_CHUNKS_PER_SIDE,
        label="公司资料",
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
                        f"{compact_json(company_catalog)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    data = parse_json_from_model(raw, root / "workspace" / f"debug_select_context_{chapter_id}_raw.txt")

    tender_ids = {stringify(chunk.get("id")) for chunk in ranked_tender}
    company_ids = {stringify(chunk.get("id")) for chunk in ranked_company}
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
        "warnings": warnings,
        "selection_meta": {
            "tender_candidates_total": len(ranked_tender),
            "company_candidates_total": len(ranked_company),
            "tender_candidates_in_prompt": len(tender_catalog),
            "company_candidates_in_prompt": len(company_catalog),
            "max_context_chars": MAX_RANKED_CONTEXT_CHARS,
            "max_chunks_per_side": MAX_RANKED_CHUNKS_PER_SIDE,
            "dropped_reason": "budget_trimmed" if len(tender_catalog) < len(ranked_tender) or len(company_catalog) < len(ranked_company) else "",
        },
    }

    output_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    write_json(output_path, context)
    if warnings:
        print(f"[警告] 章节 {chapter_id} 上下文选择存在 {len(warnings)} 条警告。")
    print(f"[完成] 已选择章节 {chapter_id} 上下文: {output_path}")
    return output_path


def select_contexts_for_jobs(jobs: list[dict[str, Any]], root: Path | None = None) -> list[Path]:
    root = root or project_root()
    output_paths = []
    for job in jobs:
        output_paths.append(select_context_for_job(job, root))
    return output_paths
