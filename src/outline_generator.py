from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_score_points, read_required_input
from llm_client import chat
from utils import compact_json, listify, load_prompt, parse_json_from_model, project_root, stringify, write_json


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


def normalize_outline(data: Any, score_points: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(data, list):
        data = {"chapters": data}
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise ValueError("大纲结果必须是包含 chapters 数组的 JSON 对象。")

    known_ids = {str(item.get("id")) for item in score_points}
    score_index = {str(item.get("id")): item for item in score_points}
    chapters: list[dict[str, Any]] = []
    covered_ids: set[str] = set()

    for index, item in enumerate(data["chapters"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个章节不是 JSON 对象。")

        chapter_id = stringify(item.get("id")) or f"{index:02d}"
        if not chapter_id.isdigit() or len(chapter_id) < 2:
            chapter_id = f"{index:02d}"
        else:
            chapter_id = chapter_id.zfill(2)

        score_ids = _normalize_id_list(item.get("score_point_ids"), known_ids)
        if not score_ids:
            raise ValueError(f"章节 {chapter_id} 缺少有效 score_point_ids。")

        sections: list[dict[str, Any]] = []
        raw_sections = item.get("sections") if isinstance(item.get("sections"), list) else []
        if not raw_sections:
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
            sections.append(
                {
                    "id": stringify(section.get("id")) or f"{chapter_id}.{section_index:02d}",
                    "title": stringify(section.get("title")) or f"章节要点 {section_index}",
                    "score_point_ids": section_score_ids,
                    "writing_requirements": _normalize_requirements(section.get("writing_requirements")),
                }
            )

        covered_ids.update(score_ids)
        chapters.append(
            {
                "id": chapter_id,
                "title": stringify(item.get("title")) or f"第 {index} 章",
                "score_point_ids": score_ids,
                "description": stringify(item.get("description")),
                "sections": sections,
            }
        )

    uncovered = [score_id for score_id in sorted(known_ids) if score_id not in covered_ids]
    if uncovered:
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
    prompt = load_prompt(root, "generate_outline.md")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请根据招标文件、评分点和全局事实生成标书大纲。\n\n"
                    "## 招标文件\n\n"
                    f"{tender_markdown}\n\n"
                    "## 评分点 JSON\n\n"
                    f"{compact_json(score_points)}\n\n"
                    "## 全局事实 JSON\n\n"
                    f"{compact_json(global_facts)}"
                ),
            },
        ],
        temperature=0.2,
    )
    data = parse_json_from_model(raw, root / "workspace" / "debug_outline_raw.txt")
    outline = normalize_outline(data, score_points)

    output_path = root / "workspace" / "outline.json"
    write_json(output_path, outline)
    print(f"[完成] 已生成 {len(outline['chapters'])} 个一级章节: {output_path}")
    return output_path
