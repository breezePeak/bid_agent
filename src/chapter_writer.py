from __future__ import annotations

from pathlib import Path
from typing import Any

from context_budget import summarize_chunk_payload, summarize_for_prompt
from file_loader import load_global_facts, load_score_points, load_tender_requirements
from llm_client import chat
from manual_review import manual_review_context_for_chapter
from materials_checklist import ensure_placeholders_in_content, items_for_chapter
from project_profile_registry import load_project_profile
from prompt_registry import load_agent_prompt
from quality_gates import validate_chapter_claims_gate, validate_weak_evidence_language
from runtime_context import agent_run
from utils import (
    compact_json,
    project_root,
    read_json,
    select_score_points,
    stringify,
    write_text,
)

WRITER_CONTEXT_MAX_CHARS = 16000


def _ensure_chapter_heading(content: str, chapter: dict[str, Any]) -> str:
    level = max(1, min(int(chapter.get("heading_level") or 1), 6))
    expected = f"{'#' * level} {chapter['id']} {chapter['title']}"
    stripped = content.strip()
    if not stripped:
        return expected + "\n"
    lines = stripped.splitlines()
    first_line = lines[0].strip()
    if first_line.startswith("#") and (
        str(chapter["title"]) in first_line or str(chapter["id"]) in first_line
    ):
        lines[0] = expected
        return "\n".join(lines).strip() + "\n"
    return expected + "\n\n" + stripped + "\n"


def _chunks_by_id(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {stringify(chunk.get("id")): chunk for chunk in chunks}


def _build_chunk_payload(
    selected_chunks: list[dict[str, Any]],
    context_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reason_by_id = {
        stringify(item.get("id")): stringify(item.get("reason"))
        for item in context_items
        if isinstance(item, dict)
    }
    payload: list[dict[str, Any]] = []
    for chunk in selected_chunks:
        chunk_id = stringify(chunk.get("id"))
        payload.append(
            {
                "id": chunk_id,
                "source": stringify(chunk.get("source")),
                "title_path": chunk.get("title_path", []),
                "keywords": chunk.get("keywords", []),
                "selected_reason": reason_by_id.get(chunk_id, ""),
                "content": stringify(chunk.get("content")),
            }
        )
    return payload


def _load_selected_chunks(root: Path, context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tender_chunks = read_json(root / "workspace" / "chunks" / "tender_chunks.json")
    company_chunks = read_json(root / "workspace" / "chunks" / "company_chunks.json")
    tender_index = _chunks_by_id(tender_chunks if isinstance(tender_chunks, list) else [])
    company_index = _chunks_by_id(company_chunks if isinstance(company_chunks, list) else [])

    selected_tender = []
    for item in context.get("selected_tender_chunks", []):
        chunk_id = stringify(item.get("id")) if isinstance(item, dict) else stringify(item)
        if chunk_id in tender_index:
            selected_tender.append(tender_index[chunk_id])
        else:
            print(f"[警告] 招标文件 chunk id 未找到: {chunk_id}")

    selected_company = []
    for item in context.get("selected_company_chunks", []):
        chunk_id = stringify(item.get("id")) if isinstance(item, dict) else stringify(item)
        if chunk_id in company_index:
            selected_company.append(company_index[chunk_id])
        else:
            print(f"[警告] 公司资料 chunk id 未找到: {chunk_id}")

    return selected_tender, selected_company


def write_chapter_from_job_context(
    job: dict[str, Any],
    context: dict[str, Any],
    root: Path | None = None,
) -> str:
    """Generate one chapter from its task package and selected chunks only."""
    root = root or project_root()
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    tender_requirements = load_tender_requirements(root)
    related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
    selected_tender_chunks, selected_company_chunks = _load_selected_chunks(root, context)
    tender_payload = _build_chunk_payload(selected_tender_chunks, context.get("selected_tender_chunks", []))
    company_payload = _build_chunk_payload(selected_company_chunks, context.get("selected_company_chunks", []))
    manual_review = manual_review_context_for_chapter(root, stringify(job.get("chapter_id")))
    materials_items = job.get("materials_checklist_items")
    if not isinstance(materials_items, list) or not materials_items:
        materials_items = items_for_chapter(root, job=job)
    chapter = {
        "id": stringify(job.get("chapter_id")),
        "title": stringify(job.get("chapter_title")),
        "heading_level": int(job.get("heading_level", 1) or 1),
        "score_point_ids": job.get("score_point_ids", []),
        "description": stringify(job.get("description")),
        "writing_requirements": job.get("writing_requirements", []),
        "sections": job.get("sections", []),
    }
    tender_context = summarize_chunk_payload(
        tender_payload,
        total_max_chars=WRITER_CONTEXT_MAX_CHARS // 2,
        per_chunk_chars=1200,
    )
    company_context = summarize_chunk_payload(
        company_payload,
        total_max_chars=WRITER_CONTEXT_MAX_CHARS // 2,
        per_chunk_chars=1000,
    )
    profile = load_project_profile(root)
    expected_pages = int(profile.get("expected_pages", 0) or 0)
    length_guidance = ""
    if expected_pages > 0:
        length_guidance = (
            f"篇幅控制：本次投标文件目标总篇幅约 {expected_pages} 页（A4），"
            "请按本章节任务包 description 中给出的目标页数占比/区间控制本章篇幅，"
            "目标页数较多时加深细节、扩充论据与方案，较少时精简避免注水；"
            "不要为凑篇幅编造招标文件和公司资料之外的事实。"
        )

    with agent_run(
        root,
        "write_chapters",
        "chapter_writer",
        input_summary={
            "chapter_id": chapter["id"],
            "score_point_count": len(related_score_points),
            "tender_chunk_count": len(tender_context),
            "company_chunk_count": len(company_context),
            "manual_review_instructions": len(manual_review.get("operator_instructions", [])),
            "expected_pages": expected_pages,
        },
        chapter_id=chapter["id"],
        temperature=0.2,
    ):
        prompt = load_agent_prompt(root, "chapter_writer")
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请只基于当前章节任务包和选中的资料片段生成章节 Markdown。"
                        "不要读取或假设未提供的完整招标文件、完整公司资料。\n\n"
                        "## 当前章节任务包\n\n"
                        f"{compact_json(job)}\n\n"
                        "## 当前章节绑定评分点\n\n"
                        f"{compact_json(related_score_points)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}\n\n"
                        "## 招标需求摘要\n\n"
                        f"{compact_json(tender_requirements)}\n\n"
                        "## 当前章节相关模板任务\n\n"
                        f"{compact_json(job.get('template_tasks', []))}\n\n"
                        "## 人工复核补充要求\n\n"
                        f"{compact_json(manual_review)}\n\n"
                        "## 材料/资格待响应清单\n\n"
                        f"{compact_json(materials_items)}\n\n"
                        "## 上下文摘要\n\n"
                        f"{summarize_for_prompt({'max_context_chars': WRITER_CONTEXT_MAX_CHARS, 'tender_chunks': len(tender_context), 'company_chunks': len(company_context)}, 1200)}\n\n"
                        "## 选中的招标文件片段\n\n"
                        f"{compact_json(tender_context)}\n\n"
                        "## 选中的公司资料片段\n\n"
                        f"{compact_json(company_context)}\n\n"
                        "写作提醒：先覆盖当前章节 writing_requirements，再覆盖每个 section 的 writing_requirements；"
                        "如果 sections 为空，不要额外创造小节标题；"
                        "章节标题必须使用当前章节任务包中的 heading_level 对应的 Markdown 标题层级；"
                        "模板任务中的 fill_slot 和 writing_task 必须优先响应；"
                        "如果模板任务状态为 weak/missing 或证据不足，不要硬写成既成事实；"
                        "对 materials_checklist_items 中 response_status=deferred 的项，必须输出 MATERIAL_GAP 结构化占位块"
                        "（含要求/留白原因/建议附件），禁止 XXX/TODO/待填写，禁止写已具备/已提供。"
                        f"{length_guidance}"
                    ),
                },
            ],
            temperature=0.2,
        )
    content = _ensure_chapter_heading(raw, chapter)
    content = ensure_placeholders_in_content(content, materials_items if isinstance(materials_items, list) else [])
    validate_weak_evidence_language(job, content)
    # claim 防编造：无证据的金额/资质/业绩既成事实直接失败，迫使改写或补材料
    validate_chapter_claims_gate(root, chapter["id"], content, raise_on_blocker=True)
    return content


def write_chapter(chapter_id: str, root: Path | None = None) -> Path:
    """Write a single chapter using its job and context files."""
    root = root or project_root()
    job_path = root / "workspace" / "jobs" / f"{chapter_id}.json"
    context_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"

    if not job_path.exists():
        raise FileNotFoundError(
            f"缺少章节任务: {job_path}，请先执行 plan-jobs"
        )
    if not context_path.exists():
        raise FileNotFoundError(
            f"缺少上下文文件: {context_path}，请先执行 select-context --chapter {chapter_id}"
        )

    job = read_json(job_path)
    context = read_json(context_path)
    content = write_chapter_from_job_context(job, context, root)

    output_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
    write_text(output_path, content)
    print(f"[完成] 已生成章节 {chapter_id}: {output_path}")
    return output_path


def write_all(root: Path | None = None) -> list[Path]:
    """Serial write all chapters from jobs and contexts."""
    root = root or project_root()
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists() or not list(jobs_dir.glob("*.json")):
        raise FileNotFoundError(
            f"缺少章节任务目录: {jobs_dir}，请先执行 plan-jobs"
        )

    output_paths: list[Path] = []
    job_files = sorted(jobs_dir.glob("*.json"))
    for job_file in job_files:
        chapter_id = job_file.stem
        output_paths.append(write_chapter(chapter_id, root))
    return output_paths
