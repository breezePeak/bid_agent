from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph


def _escape_cell(value: str) -> str:
    return " ".join(value.split()).replace("|", r"\|")


def _heading_level(paragraph: Paragraph, title_levels: dict[str, int]) -> int:
    text = paragraph.text.strip()
    if text in title_levels:
        return title_levels[text]
    style_name = str(getattr(paragraph.style, "name", "") or "")
    match = re.search(r"(?:Heading|标题)\s*([1-9])", style_name, re.I)
    return int(match.group(1)) if match else 0


def _iter_blocks(document: DocumentType):
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def project_docx_to_markdown(
    docx_path: Path,
    markdown_path: Path,
    *,
    contract_nodes: Iterable[object] = (),
) -> Path:
    """Project visible DOCX content into safe, deterministic Markdown."""

    title_levels = {
        str(getattr(node, "title", "") or "").strip(): max(
            1,
            min(int(getattr(node, "level", 1) or 1), 6),
        )
        for node in contract_nodes
        if str(getattr(node, "title", "") or "").strip()
    }
    document = Document(str(docx_path))
    rendered: list[str] = []
    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            level = _heading_level(block, title_levels)
            rendered.append(f"{'#' * level} {text}" if level else text)
            continue
        rows = [
            [_escape_cell(cell.text) for cell in row.cells]
            for row in block.rows
        ]
        if not rows:
            continue
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]
        rendered.append("| " + " | ".join(normalized[0]) + " |")
        rendered.append("| " + " | ".join(["---"] * width) + " |")
        rendered.extend(
            "| " + " | ".join(row) + " |"
            for row in normalized[1:]
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        "\n\n".join(rendered).strip() + "\n",
        encoding="utf-8",
    )
    return markdown_path
