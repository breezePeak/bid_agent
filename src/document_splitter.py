from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from utils import project_root, read_nonempty_text, write_json


DEFAULT_MAX_CHARS = 3500


def _heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return len(match.group(1))


def _heading_title(line: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", line).strip()


def _split_by_heading(markdown: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    title_stack: list[str] = []
    current_lines: list[str] = []
    current_path: list[str] = ["正文"]

    def flush() -> None:
        nonlocal current_lines
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append({"title_path": current_path.copy(), "content": content})
        current_lines = []

    for line in markdown.splitlines():
        level = _heading_level(line)
        if level is not None:
            flush()
            title = _heading_title(line)
            title_stack = title_stack[: level - 1]
            title_stack.append(title)
            current_path = title_stack.copy()
        current_lines.append(line)

    flush()
    return chunks


def _split_long_content(content: str, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    parts: list[str] = []
    paragraphs = re.split(r"(\n\s*\n)", content)
    buffer = ""
    for part in paragraphs:
        if len(buffer) + len(part) <= max_chars:
            buffer += part
            continue
        if buffer.strip():
            parts.append(buffer.strip())
            buffer = ""
        while len(part) > max_chars:
            parts.append(part[:max_chars].strip())
            part = part[max_chars:]
        buffer = part
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def split_markdown_document(
    markdown: str,
    source: str,
    id_prefix: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict[str, Any]]:
    raw_blocks = _split_by_heading(markdown)
    output: list[dict[str, Any]] = []
    counter = 1

    for block in raw_blocks:
        title_path = block["title_path"]
        for part in _split_long_content(block["content"], max_chars):
            chunk = {
                "id": f"{id_prefix}_{counter:03d}",
                "source": source,
                "title_path": title_path,
                "content": part,
                "keywords": [],
                "char_count": len(part),
            }
            output.append(chunk)
            counter += 1

    return output


def split_docs(root: Path | None = None, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[Path, Path]:
    root = root or project_root()
    tender_md = read_nonempty_text(root / "inputs" / "tender.md", "招标文件 inputs/tender.md")
    company_md = read_nonempty_text(root / "inputs" / "company.md", "公司资料 inputs/company.md")

    chunks_dir = root / "workspace" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    tender_chunks = split_markdown_document(tender_md, "tender.md", "TENDER", max_chars=max_chars)
    company_chunks = split_markdown_document(company_md, "company.md", "COMPANY", max_chars=max_chars)

    tender_path = chunks_dir / "tender_chunks.json"
    company_path = chunks_dir / "company_chunks.json"
    write_json(tender_path, tender_chunks)
    write_json(company_path, company_chunks)

    print(f"[完成] 招标文件切分 {len(tender_chunks)} 个片段: {tender_path}")
    print(f"[完成] 公司资料切分 {len(company_chunks)} 个片段: {company_path}")
    return tender_path, company_path
