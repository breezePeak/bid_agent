from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_outline, load_score_points, read_required_input
from llm_client import chat
from utils import compact_json, find_chapter, load_prompt, project_root, select_score_points, stringify, write_text


def _ensure_chapter_heading(content: str, chapter: dict[str, Any]) -> str:
    expected = f"# {chapter['id']} {chapter['title']}"
    stripped = content.strip()
    if not stripped:
        return expected + "\n"
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith("# ") and str(chapter["id"]) in first_line:
        return stripped + "\n"
    return expected + "\n\n" + stripped + "\n"


def write_chapter(chapter_id: str, root: Path | None = None) -> Path:
    root = root or project_root()
    outline = load_outline(root)
    score_points = load_score_points(root)
    global_facts = load_global_facts(root)
    tender_markdown = read_required_input(root, "tender.md", "招标文件 inputs/tender.md")
    company_markdown = read_required_input(root, "company.md", "公司资料 inputs/company.md")
    chapter = find_chapter(outline, chapter_id)
    related_score_points = select_score_points(score_points, chapter.get("score_point_ids", []))
    prompt = load_prompt(root, "write_chapter.md")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请只生成当前章节 Markdown 内容。\n\n"
                    "## 当前章节\n\n"
                    f"{compact_json(chapter)}\n\n"
                    "## 当前章节绑定评分点\n\n"
                    f"{compact_json(related_score_points)}\n\n"
                    "## 全局事实\n\n"
                    f"{compact_json(global_facts)}\n\n"
                    "## 招标文件\n\n"
                    f"{tender_markdown}\n\n"
                    "## 公司资料\n\n"
                    f"{company_markdown}"
                ),
            },
        ],
        temperature=0.35,
    )

    content = _ensure_chapter_heading(raw, chapter)
    output_path = root / "workspace" / "chapters" / f"{stringify(chapter['id'])}.md"
    write_text(output_path, content)
    print(f"[完成] 已生成章节 {chapter['id']} {chapter['title']}: {output_path}")
    return output_path


def write_all(root: Path | None = None) -> list[Path]:
    root = root or project_root()
    outline = load_outline(root)
    output_paths: list[Path] = []
    for chapter in outline.get("chapters", []):
        output_paths.append(write_chapter(str(chapter["id"]), root))
    return output_paths
