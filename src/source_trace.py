from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_outline, load_score_points
from utils import project_root, read_json, read_text, select_score_points, stringify, write_json


def _load_job(root: Path, chapter_id: str) -> dict[str, Any]:
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    data = read_json(job_path)
    if not isinstance(data, dict):
        raise ValueError(f"章节任务必须是 JSON 对象: {job_path}")
    return data


def _load_context(root: Path, chapter_id: str) -> dict[str, Any]:
    context_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    data = read_json(context_path)
    if not isinstance(data, dict):
        raise ValueError(f"章节上下文必须是 JSON 对象: {context_path}")
    return data


def _chunk_index(root: Path, filename: str) -> dict[str, dict[str, Any]]:
    path = root / "workspace" / "chunks" / filename
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"切块文件必须是 JSON 数组: {path}")
    return {
        stringify(item.get("id")): item
        for item in data
        if isinstance(item, dict) and stringify(item.get("id"))
    }


def _normalize_selected_chunk(
    item: Any,
    index: dict[str, dict[str, Any]],
    chapter_id: str,
    label: str,
    preview_limit: int = 300,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {"id": stringify(item), "reason": ""}
    chunk_id = stringify(item.get("id"))
    if not chunk_id:
        raise ValueError(f"章节 {chapter_id} 的{label}来源存在空 chunk id")
    if chunk_id not in index:
        raise ValueError(f"章节 {chapter_id} 的{label}来源 chunk 不存在: {chunk_id}")

    chunk = index[chunk_id]
    content = stringify(chunk.get("content"))
    return {
        "id": chunk_id,
        "selected_reason": stringify(item.get("reason")),
        "source": stringify(chunk.get("source")),
        "title_path": chunk.get("title_path", []) if isinstance(chunk.get("title_path"), list) else [],
        "keywords": chunk.get("keywords", []) if isinstance(chunk.get("keywords"), list) else [],
        "char_count": chunk.get("char_count", len(content)),
        "content_preview": content[:preview_limit],
    }


def build_chapter_source_trace(chapter_id: str, root: Path | None = None) -> Path:
    root = root or project_root()
    job = _load_job(root, chapter_id)
    context = _load_context(root, chapter_id)
    chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    if not chapter_path.exists():
        raise FileNotFoundError(f"章节文件不存在: {chapter_path}")

    score_points = load_score_points(root)
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    tender_index = _chunk_index(root, "tender_chunks.json")
    company_index = _chunk_index(root, "company_chunks.json")

    selected_tender = [
        _normalize_selected_chunk(item, tender_index, chapter_id, "招标文件")
        for item in context.get("selected_tender_chunks", [])
    ]
    selected_company = [
        _normalize_selected_chunk(item, company_index, chapter_id, "公司资料")
        for item in context.get("selected_company_chunks", [])
    ]

    chapter_text = read_text(chapter_path)
    # 为对齐补全 chunk 全文（不仅 preview）
    selected_tender_full = []
    for item in selected_tender:
        full = dict(item)
        chunk = tender_index.get(stringify(item.get("id")), {})
        full["content"] = stringify(chunk.get("content"))
        selected_tender_full.append(full)
    selected_company_full = []
    for item in selected_company:
        full = dict(item)
        chunk = company_index.get(stringify(item.get("id")), {})
        full["content"] = stringify(chunk.get("content"))
        selected_company_full.append(full)

    trace = {
        "chapter_id": chapter_id,
        "chapter_title": stringify(job.get("chapter_title")),
        "chapter_path": str(chapter_path.relative_to(root)),
        "chapter_preview": " ".join(line.strip() for line in chapter_text.splitlines() if line.strip())[:400],
        "score_point_ids": job.get("score_point_ids", []) if isinstance(job.get("score_point_ids"), list) else [],
        "related_score_points": related_score_points,
        "selected_tender_chunk_count": len(selected_tender_full),
        "selected_company_chunk_count": len(selected_company_full),
        "selected_tender_chunks": selected_tender_full,
        "selected_company_chunks": selected_company_full,
        "context_path": str((root / "workspace" / "contexts" / f"{chapter_id}_context.json").relative_to(root)),
        "job_path": str((root / "workspace" / "jobs" / f"{chapter_id}.json").relative_to(root)),
    }

    # claim ↔ source_trace 逐句对齐（使用本轮已解析的 chunk 全文，避免读写环）
    try:
        from claim_validator import build_evidence_corpus, validate_claims_against_evidence

        chunk_payload = []
        for item in selected_company_full:
            chunk_payload.append(
                {
                    "chunk_id": stringify(item.get("id")),
                    "source": "company",
                    "content": stringify(item.get("content")),
                    "selected_reason": stringify(item.get("selected_reason")),
                }
            )
        for item in selected_tender_full:
            chunk_payload.append(
                {
                    "chunk_id": stringify(item.get("id")),
                    "source": "tender",
                    "content": stringify(item.get("content")),
                    "selected_reason": stringify(item.get("selected_reason")),
                }
            )
        evidence = build_evidence_corpus(root)
        claim_result = validate_claims_against_evidence(chapter_text, evidence, chunks=chunk_payload)
        trace["claim_alignments"] = claim_result.get("claim_alignments") or []
        trace["claim_alignment_summary"] = {
            "claim_count": claim_result.get("claim_count", 0),
            "aligned_count": claim_result.get("aligned_count", 0),
            "finding_count": claim_result.get("finding_count", 0),
            "blocker_count": claim_result.get("blocker_count", 0),
            "grounded_count": claim_result.get("grounded_count", 0),
        }
    except Exception as exc:
        trace["claim_alignment_error"] = str(exc)

    output_dir = root / "workspace" / "source_traces"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{chapter_id}_sources.json"
    write_json(output_path, trace)
    print(f"[完成] 已生成章节 {chapter_id} 来源映射: {output_path}")
    return output_path


def build_source_trace_index(root: Path | None = None) -> Path:
    root = root or project_root()
    outline = load_outline(root)
    traces: list[dict[str, Any]] = []
    missing_chapters: list[str] = []
    for chapter in outline.get("chapters", []):
        chapter_id = stringify(chapter.get("id"))
        if not chapter_id:
            continue
        try:
            trace_path = build_chapter_source_trace(chapter_id, root)
            trace = read_json(trace_path)
            if isinstance(trace, dict):
                traces.append(trace)
        except Exception as exc:
            missing_chapters.append(f"{chapter_id}: {exc}")

    aligned_total = 0
    claim_total = 0
    for trace in traces:
        summary = trace.get("claim_alignment_summary") if isinstance(trace.get("claim_alignment_summary"), dict) else {}
        aligned_total += int(summary.get("aligned_count") or 0)
        claim_total += int(summary.get("claim_count") or 0)

    index = {
        "summary": {
            "chapter_count": len(traces),
            "missing_chapter_count": len(missing_chapters),
            "tender_chunk_reference_count": sum(int(trace.get("selected_tender_chunk_count", 0)) for trace in traces),
            "company_chunk_reference_count": sum(int(trace.get("selected_company_chunk_count", 0)) for trace in traces),
            "claim_count": claim_total,
            "claim_aligned_count": aligned_total,
        },
        "missing_chapters": missing_chapters,
        "chapters": traces,
    }

    output_path = root / "workspace" / "source_trace_index.json"
    write_json(output_path, index)
    print(f"[完成] 已生成来源追溯索引: {output_path}")
    return output_path
