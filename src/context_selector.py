from __future__ import annotations

from pathlib import Path
from typing import Any

from chunk_ranker import rank_chunks_for_job, rank_for_job_separate
from file_loader import load_global_facts, load_score_points
from llm_client import chat
from utils import (
    compact_json,
    load_prompt,
    parse_json_from_model,
    project_root,
    read_json,
    select_score_points,
    stringify,
    write_json,
)


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


def _fallback_select(
    job: dict[str, Any],
    tender_chunks: list[dict[str, Any]],
    company_chunks: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    warnings.append("LLM 上下文选择失败，已使用标题顺序兜底选择。")
    return {
        "chapter_id": stringify(job.get("chapter_id")),
        "selected_tender_chunks": [
            {"id": stringify(chunk.get("id")), "reason": "兜底选择招标文件前序相关片段"}
            for chunk in tender_chunks[:8]
        ],
        "selected_company_chunks": [
            {"id": stringify(chunk.get("id")), "reason": "兜底选择公司资料前序相关片段"}
            for chunk in company_chunks[:8]
        ],
        "warnings": warnings,
    }


def select_context_for_job(job: dict[str, Any], root: Path | None = None) -> Path:
    root = root or project_root()
    chapter_id = stringify(job.get("chapter_id"))
    tender_chunks = read_json(root / "workspace" / "chunks" / "tender_chunks.json")
    company_chunks = read_json(root / "workspace" / "chunks" / "company_chunks.json")
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    prompt = load_prompt(root, "select_context.md")
    warnings: list[str] = []

    if not isinstance(tender_chunks, list) or not isinstance(company_chunks, list):
        raise ValueError("文档切分结果必须是 JSON 数组。")

    ranked_tender = tender_chunks
    ranked_company = company_chunks
    try:
        ranked_result = rank_for_job_separate(job, related_score_points, tender_chunks, company_chunks)
        ranked_tender = [c for c in tender_chunks if any(r["id"] == c["id"] for r in ranked_result["tender_top_chunks"])]
        ranked_company = [c for c in company_chunks if any(r["id"] == c["id"] for r in ranked_result["company_top_chunks"])]
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

    try:
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
                        "## 招标文件 chunk 目录\n\n"
                        f"{compact_json(_chunk_catalog(ranked_tender))}\n\n"
                        "## 公司资料 chunk 目录\n\n"
                        f"{compact_json(_chunk_catalog(ranked_company))}"
                    ),
                },
            ],
            temperature=0.1,
        )
        data = parse_json_from_model(raw, root / "workspace" / f"debug_select_context_{chapter_id}_raw.txt")
    except Exception as exc:
        warnings.append(str(exc))
        context = _fallback_select(job, ranked_tender, ranked_company, warnings)
        output_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
        write_json(output_path, context)
        print(f"[警告] 章节 {chapter_id} 上下文选择失败，已兜底: {exc}")
        return output_path

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
