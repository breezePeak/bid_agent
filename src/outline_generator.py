from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from file_loader import (
    load_global_facts,
    load_score_points,
    load_template_evidence_map,
    load_template_outline,
    load_tender_requirements,
    read_required_input,
)
from llm_client import chat
from prompt_registry import load_agent_prompt
from quality_gates import validate_outline_score_coverage
from runtime_context import agent_run
from utils import compact_json, listify, parse_json_from_model, project_root, stringify, write_json


def _normalize_id_list(value: Any, known_ids: set[str]) -> list[str]:
    ids = []
    for item in listify(value):
        score_id = stringify(item)
        if score_id and score_id in known_ids and score_id not in ids:
            ids.append(score_id)
    return ids


def _normalize_requirements(value: Any) -> list[str]:
    requirements = [stringify(item) for item in listify(value) if stringify(item)]
    return requirements or ["围绕本章节绑定评分点进行具体响应"]


def _is_valid_chapter_id(chapter_id: str) -> bool:
    return bool(chapter_id) and bool(re.fullmatch(r"\d+(?:\.\d+)*", chapter_id))


def _template_index(template_outline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    headings = template_outline.get("headings") if isinstance(template_outline, dict) else []
    if not isinstance(headings, list):
        return {}
    return {stringify(item.get("id")): item for item in headings if isinstance(item, dict) and stringify(item.get("id"))}


def _template_children(template_outline: dict[str, Any], parent_id: str) -> list[dict[str, Any]]:
    headings = template_outline.get("headings") if isinstance(template_outline, dict) else []
    if not isinstance(headings, list):
        return []
    return [
        item for item in headings
        if isinstance(item, dict) and stringify(item.get("parent_id")) == parent_id
    ]


def _flatten_outline_items(items: list[Any]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if not isinstance(item, dict):
            return
        item_id = stringify(item.get("id"))
        if item_id:
            flattened[item_id] = item
        for section in item.get("sections", []):
            visit(section)

    for item in items:
        visit(item)
    return flattened


def _template_evidence_items(evidence_map: dict[str, Any], heading_id: str) -> list[dict[str, Any]]:
    items = evidence_map.get("items") if isinstance(evidence_map, dict) else []
    if not isinstance(items, list):
        return []
    return [
        item for item in items
        if isinstance(item, dict) and stringify(item.get("heading_id")) == heading_id
    ]


def _score_ids_from_evidence(evidence_items: list[dict[str, Any]], known_ids: set[str]) -> list[str]:
    ids: list[str] = []
    for item in evidence_items:
        evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        for point in evidence.get("score_points", []):
            if not isinstance(point, dict):
                continue
            score_id = stringify(point.get("id"))
            if score_id and score_id in known_ids and score_id not in ids:
                ids.append(score_id)
    return ids


def _requirements_from_evidence(evidence_items: list[dict[str, Any]]) -> list[str]:
    requirements: list[str] = []
    for item in evidence_items:
        item_type = stringify(item.get("type")) or "template_item"
        title = stringify(item.get("title")) or stringify(item.get("label")) or stringify(item.get("semantic_key"))
        status = stringify(item.get("status"))
        if title:
            text = f"覆盖模板{item_type}: {title}"
            if status in {"weak", "missing"}:
                text += "；证据不足时使用拟响应/按要求提交/随响应文件附后等谨慎表述"
            if text not in requirements:
                requirements.append(text)
    return requirements


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            item = stringify(item)
            if item and item not in merged:
                merged.append(item)
    return merged


def _compact_template_evidence(evidence_map: dict[str, Any], limit: int = 80) -> dict[str, Any]:
    items = evidence_map.get("items") if isinstance(evidence_map, dict) else []
    compact_items: list[dict[str, Any]] = []
    if not isinstance(items, list):
        items = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        compact_items.append(
            {
                "id": stringify(item.get("id")),
                "type": stringify(item.get("type")),
                "heading_id": stringify(item.get("heading_id")),
                "title": stringify(item.get("title")),
                "label": stringify(item.get("label")),
                "semantic_key": stringify(item.get("semantic_key")),
                "status": stringify(item.get("status")),
                "evidence_sources": item.get("evidence_sources", []),
                "tender_chunk_ids": [
                    stringify(chunk.get("id"))
                    for chunk in evidence.get("tender_chunks", [])
                    if isinstance(chunk, dict)
                ][:5],
                "company_chunk_ids": [
                    stringify(chunk.get("id"))
                    for chunk in evidence.get("company_chunks", [])
                    if isinstance(chunk, dict)
                ][:5],
                "score_point_ids": [
                    stringify(point.get("id"))
                    for point in evidence.get("score_points", [])
                    if isinstance(point, dict)
                ][:5],
            }
        )
    return {
        "summary": evidence_map.get("summary", {}) if isinstance(evidence_map, dict) else {},
        "items": compact_items,
    }


def normalize_outline(
    data: Any,
    score_points: list[dict[str, Any]],
    template_outline: dict[str, Any] | None = None,
    template_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(data, list):
        data = {"chapters": data}
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise ValueError("大纲结果必须是包含 chapters 数组的 JSON 对象。")

    known_ids = {str(item.get("id")) for item in score_points}
    score_index = {str(item.get("id")): item for item in score_points}
    template_outline = template_outline or {"headings": []}
    template_evidence = template_evidence or {"items": []}
    template_by_id = _template_index(template_outline)
    chapters: list[dict[str, Any]] = []
    covered_ids: set[str] = set()

    if template_by_id:
        raw_by_id = _flatten_outline_items(data["chapters"])
        for order_index, template_heading in enumerate(template_outline.get("headings", []), start=1):
            if not isinstance(template_heading, dict):
                continue
            chapter_id = stringify(template_heading.get("id"))
            chapter_title = stringify(template_heading.get("title"))
            if not chapter_id or not chapter_title:
                continue

            raw_item = raw_by_id.get(chapter_id, {})
            raw_title = stringify(raw_item.get("title")) if isinstance(raw_item, dict) else ""
            if raw_title and raw_title != chapter_title and raw_title not in chapter_title and chapter_title not in raw_title:
                raw_item = {}
            evidence_items = _template_evidence_items(template_evidence, chapter_id)
            score_ids = _merge_unique(
                _normalize_id_list(raw_item.get("score_point_ids"), known_ids),
                _score_ids_from_evidence(evidence_items, known_ids),
            )
            raw_requirements = (
                _normalize_requirements(raw_item.get("writing_requirements"))
                if raw_item.get("writing_requirements")
                else []
            )
            writing_requirements = _merge_unique(
                raw_requirements,
                _requirements_from_evidence(evidence_items),
            )
            if not evidence_items and not raw_item.get("writing_requirements"):
                writing_requirements = ["严格围绕模板标题进行响应，不新增或替换模板标题"]

            covered_ids.update(score_ids)
            chapters.append(
                {
                    "id": chapter_id,
                    "title": chapter_title,
                    "level": int(template_heading.get("level", 1) or 1),
                    "parent_id": stringify(template_heading.get("parent_id")),
                    "template_order": order_index,
                    "score_point_ids": score_ids,
                    "description": stringify(raw_item.get("description")),
                    "writing_requirements": writing_requirements,
                    "sections": [],
                }
            )

        uncovered = [score_id for score_id in sorted(known_ids) if score_id not in covered_ids]
        if uncovered and chapters:
            chapters[-1]["score_point_ids"] = sorted(set(chapters[-1]["score_point_ids"] + uncovered))
            titles = "、".join(stringify(score_index[score_id].get("title")) for score_id in uncovered)
            chapters[-1]["writing_requirements"].append(f"补充覆盖未绑定评分点：{titles}")
            print(f"[警告] 模型/模板依据未覆盖 {len(uncovered)} 个评分点，已并入模板最后一个标题。")

        return {"chapters": chapters}

    for index, item in enumerate(data["chapters"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个章节不是 JSON 对象。")

        chapter_id = stringify(item.get("id"))
        if template_by_id:
            if chapter_id not in template_by_id:
                raise ValueError(f"章节 {chapter_id or index} 不在模板目录中，必须沿用模板章节。")
            chapter_title = stringify(template_by_id[chapter_id].get("title"))
        else:
            chapter_id = chapter_id or f"{index:02d}"
            if not _is_valid_chapter_id(chapter_id):
                chapter_id = f"{index:02d}"
            elif "." not in chapter_id:
                chapter_id = chapter_id.zfill(2)
            chapter_title = stringify(item.get("title")) or f"第 {index} 章"

        score_ids = _normalize_id_list(item.get("score_point_ids"), known_ids)
        if not score_ids:
            raise ValueError(f"章节 {chapter_id} 缺少有效 score_point_ids。")

        sections: list[dict[str, Any]] = []
        raw_sections = item.get("sections") if isinstance(item.get("sections"), list) else []
        template_sections = _template_children(template_outline, chapter_id)
        if not raw_sections:
            if template_sections:
                raw_sections = template_sections
            else:
                raw_sections = [
                    {
                        "title": "评分点响应",
                        "score_point_ids": score_ids,
                        "writing_requirements": ["结合招标文件、评分标准和公司资料进行完整响应"],
                    }
                ]

        for section_index, section in enumerate(raw_sections, start=1):
            section = section if isinstance(section, dict) else {}
            section_score_ids = _normalize_id_list(section.get("score_point_ids"), known_ids) or score_ids
            if template_sections:
                section_id = stringify(section.get("id"))
                template_section = next(
                    (candidate for candidate in template_sections if stringify(candidate.get("id")) == section_id),
                    None,
                )
                if template_section is None:
                    raise ValueError(f"章节 {chapter_id} 的小节 {section_id or section_index} 不在模板目录中。")
                section_title = stringify(template_section.get("title"))
            else:
                section_id = stringify(section.get("id")) or f"{chapter_id}.{section_index:02d}"
                section_title = stringify(section.get("title")) or f"章节要点 {section_index}"
            sections.append(
                {
                    "id": section_id,
                    "title": section_title,
                    "score_point_ids": section_score_ids,
                    "writing_requirements": _normalize_requirements(section.get("writing_requirements")),
                }
            )

        covered_ids.update(score_ids)
        chapters.append(
            {
                "id": chapter_id,
                "title": chapter_title,
                "score_point_ids": score_ids,
                "description": stringify(item.get("description")),
                "sections": sections,
            }
        )

    uncovered = [score_id for score_id in sorted(known_ids) if score_id not in covered_ids]
    if uncovered:
        if template_by_id and chapters:
            chapters[-1]["score_point_ids"] = sorted(set(chapters[-1]["score_point_ids"] + uncovered))
            if chapters[-1]["sections"]:
                chapters[-1]["sections"][-1]["score_point_ids"] = sorted(
                    set(chapters[-1]["sections"][-1]["score_point_ids"] + uncovered)
                )
            print(f"[警告] 大纲模型输出未覆盖 {len(uncovered)} 个评分点，已并入模板最后一个章节。")
        else:
            next_id = f"{len(chapters) + 1:02d}"
            titles = "、".join(stringify(score_index[score_id].get("title")) for score_id in uncovered)
            chapters.append(
                {
                    "id": next_id,
                    "title": "补充评分点响应",
                    "score_point_ids": uncovered,
                    "description": f"补充覆盖未绑定评分点：{titles}",
                    "sections": [
                        {
                            "id": f"{next_id}.01",
                            "title": "未覆盖评分点补充响应",
                            "score_point_ids": uncovered,
                            "writing_requirements": [
                                "逐项回应本章节绑定的评分点",
                                "避免引入招标文件和公司资料之外的事实",
                            ],
                        }
                    ],
                }
            )
            print(f"[警告] 大纲模型输出未覆盖 {len(uncovered)} 个评分点，已追加补充章节。")

    return {"chapters": chapters}


def generate_outline(root: Path | None = None) -> Path:
    root = root or project_root()
    tender_markdown = read_required_input(root, "tender.md", "招标文件 inputs/tender.md")
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    tender_requirements = load_tender_requirements(root)
    template_outline = load_template_outline(root)
    template_evidence = load_template_evidence_map(root)
    prompt = load_agent_prompt(root, "outline_generator")
    has_template_guidance = bool(template_outline.get("headings")) or bool(template_evidence.get("items"))
    if template_outline.get("headings"):
        write_json(root / "workspace" / "template_outline.json", template_outline)

    template_section = ""
    if has_template_guidance:
        template_section = (
            "## 模板章节目录\n\n"
            f"{compact_json(template_outline)}\n\n"
            "硬性要求补充：生成的大纲必须严格沿用模板目录中的章节 id 和标题，不允许新造章节编号或章节标题；"
            "如果模板某章节已有子标题，优先保持这些子标题结构；"
            "模板 schema 中的 writing_tasks 表示必须扩写的章节任务，fill_slots 表示模板中需要事实依据填充的位置，"
            "后续章节规划必须覆盖这些任务和槽位。\n\n"
            "## 模板依据映射摘要\n\n"
            f"{compact_json(_compact_template_evidence(template_evidence))}\n\n"
            "硬性要求补充：优先让每个模板 writing_task 归入对应 heading_id 的章节；"
            "fill_slots 中的事实字段必须在相关章节或最终 Word 填充阶段能够找到依据；"
            "若某项模板任务证据状态为 weak/missing，章节要求中必须写明需要谨慎表述或人工补证。\n\n"
        )

    with agent_run(
        root,
        "generate_outline",
        "outline_generator",
        input_summary={
            "score_point_count": len(score_points),
            "template_heading_count": len(template_outline.get("headings", [])),
            "template_item_count": len(template_evidence.get("items", [])) if isinstance(template_evidence.get("items"), list) else 0,
            "tender_chars": len(tender_markdown),
        },
        temperature=0.2,
    ):
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请根据招标文件、评分点和全局事实生成标书大纲。\n\n"
                        f"{template_section}"
                        "## 招标文件\n\n"
                        f"{tender_markdown}\n\n"
                        "## 评分点 JSON\n\n"
                        f"{compact_json(score_points)}\n\n"
                        "## 招标需求摘要 JSON\n\n"
                        f"{compact_json(tender_requirements)}\n\n"
                        "## 全局事实 JSON\n\n"
                        f"{compact_json(global_facts)}"
                    ),
                },
            ],
            temperature=0.2,
        )
    data = parse_json_from_model(raw, root / "workspace" / "debug_outline_raw.txt")
    outline = normalize_outline(
        data,
        score_points,
        template_outline=template_outline,
        template_evidence=template_evidence,
    )
    validate_outline_score_coverage(outline, score_points)

    output_path = root / "workspace" / "outline.json"
    write_json(output_path, outline)
    print(f"[完成] 已生成 {len(outline['chapters'])} 个章节/模板标题节点: {output_path}")
    return output_path
