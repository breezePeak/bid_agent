from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from file_loader import load_outline
from utils import project_root, read_nonempty_text, read_text, write_text


def build_markdown(root: Path | None = None) -> Path:
    root = root or project_root()
    outline = load_outline(root)
    chunks: list[str] = []

    for chapter in outline.get("chapters", []):
        chapter_id = str(chapter.get("id"))
        chapter_path = root / "workspace" / "chapters" / f"{chapter_id}.md"
        if not chapter_path.exists():
            print(f"[警告] 章节文件不存在，已跳过: {chapter_path}")
            continue
        content = read_text(chapter_path).strip()
        if content:
            chunks.append(content)

    output_path = root / "outputs" / "final.md"
    write_text(output_path, "\n\n".join(chunks).strip() + "\n")
    print(f"[完成] 已拼接 Markdown: {output_path}")
    return output_path


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(cells: Iterable[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _add_table(document, rows: list[list[str]]) -> None:
    if not rows:
        return
    column_count = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=column_count)
    try:
        table.style = "Table Grid"
    except Exception:
        pass

    for row_index, row in enumerate(rows):
        for col_index in range(column_count):
            table.cell(row_index, col_index).text = row[col_index] if col_index < len(row) else ""


def _add_markdown_to_document(document, markdown: str) -> None:
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 4)
            document.add_heading(heading.group(2).strip(), level=level)
            i += 1
            continue

        if _is_table_line(line) and i + 1 < len(lines) and _is_table_line(lines[i + 1]):
            header = _split_table_row(line)
            separator = _split_table_row(lines[i + 1])
            if _is_separator_row(separator):
                rows = [header]
                i += 2
                while i < len(lines) and _is_table_line(lines[i]):
                    rows.append(_split_table_row(lines[i]))
                    i += 1
                _add_table(document, rows)
                continue

        if stripped.startswith("- "):
            document.add_paragraph(stripped[2:].strip(), style="List Bullet")
            i += 1
            continue

        if re.match(r"^\d+\.\s+", stripped):
            document.add_paragraph(re.sub(r"^\d+\.\s+", "", stripped), style="List Number")
            i += 1
            continue

        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line or next_line.startswith("#") or _is_table_line(next_line):
                break
            if next_line.startswith("- ") or re.match(r"^\d+\.\s+", next_line):
                break
            paragraph_lines.append(next_line)
            i += 1
        document.add_paragraph(" ".join(paragraph_lines))


def build_docx(root: Path | None = None) -> Path:
    root = root or project_root()
    final_markdown_path = root / "outputs" / "final.md"
    markdown = read_nonempty_text(final_markdown_path, f"最终 Markdown {final_markdown_path}")

    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("缺少依赖 python-docx，请先执行: pip install -r requirements.txt") from exc

    template_path = root / "inputs" / "template.docx"
    if template_path.exists() and template_path.stat().st_size > 0:
        try:
            document = Document(str(template_path))
        except Exception as exc:
            print(f"[警告] template.docx 无法读取，将新建空白 Word: {exc}")
            document = Document()
    else:
        document = Document()

    _add_markdown_to_document(document, markdown)

    output_path = root / "outputs" / "final.docx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    print(f"[完成] 已生成 Word: {output_path}")
    return output_path
