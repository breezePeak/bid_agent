from __future__ import annotations

import re
from pathlib import Path

from document_converter import convert_to_markdown
from utils import project_root, write_text


TENDER_EXTENSIONS = {".md", ".docx", ".pdf"}

SCORE_KEYWORDS = [
    "评分",
    "评分标准",
    "评分细则",
    "评分办法",
    "评分项",
    "评分点",
    "分值",
    "评审",
    "评审因素",
    "评审标准",
    "评审办法",
    "评标办法",
    "综合评分",
    "技术评分",
    "商务评分",
    "价格评分",
    "详细评审",
    "符合性审查",
    "资格性审查",
    "废标",
    "否决投标",
]

_SCORE_EXTEND_MIN_LINES = 20
_SCORE_MERGE_GAP = 0
_KEYWORD_WINDOW_SIZE = 15
_KEYWORD_WINDOW_THRESHOLD = 3


def _contains_score_keyword(text: str) -> bool:
    for kw in SCORE_KEYWORDS:
        if kw in text:
            return True
    return False


def _heading_info(line: str) -> tuple[bool, int]:
    stripped = line.strip()

    m = re.match(r"^(#{1,6})\s+(.+)", stripped)
    if m:
        return True, len(m.group(1))

    if re.match(r"^第[一二三四五六七八九十百零\d]+章", stripped):
        return True, 1
    if re.match(r"^[一二三四五六七八九十]+[、．，,]", stripped):
        return True, 2
    if re.match(r"^（[一二三四五六七八九十]+）", stripped):
        return True, 3
    if re.match(r"^\d+[\.\、]\s", stripped):
        return True, 3
    if re.match(r"^\d+\.\d+[\.\s]", stripped):
        return True, 4
    if re.match(r"^[A-Z]\.\s", stripped):
        return True, 5

    return False, 0


def _find_section_end(lines: list[str], start: int, start_level: int) -> int:
    total = len(lines)
    best_end = min(start + _SCORE_EXTEND_MIN_LINES, total)

    for i in range(start + 1, min(start + 300, total)):
        is_h, level = _heading_info(lines[i])
        if not is_h:
            continue
        if _contains_score_keyword(lines[i]):
            continue
        if level <= start_level:
            return i

    return best_end


def _is_table_separator(line: str) -> bool:
    return bool(re.match(r"^\|[\s\-:|]+\|$", line.strip()))


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and not _is_table_separator(stripped)


def _find_table_range(lines: list[str], anchor: int, total: int) -> tuple[int, int]:
    table_start = anchor
    table_end = anchor + 1

    for j in range(anchor - 1, max(anchor - 15, -1), -1):
        if _is_table_separator(lines[j]):
            table_start = j - 1
            break
        if _is_table_row(lines[j]):
            table_start = j
        else:
            break

    for j in range(anchor + 1, min(anchor + 30, total)):
        if _is_table_row(lines[j]) or _is_table_separator(lines[j]):
            table_end = j + 1
        else:
            break

    return table_start, table_end


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []

    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]

    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + _SCORE_MERGE_GAP:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def _is_already_covered(i: int, intervals: list[tuple[int, int]]) -> bool:
    return any(s <= i < e for s, e in intervals)


def split_tender_and_score(raw_markdown: str) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    lines = raw_markdown.splitlines()
    total = len(lines)

    if total == 0:
        return "", "", ["招标文件内容为空。"]

    intervals: list[tuple[int, int]] = []

    # Strategy 1 & 2: heading-based detection (Markdown + plain text headings)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_h, level = _heading_info(line)
        if not is_h:
            continue
        if not _contains_score_keyword(stripped):
            continue

        end = _find_section_end(lines, i, level)
        intervals.append((i, end))

    # Strategy 3: keyword window — sliding window with score keyword density
    # Skip lines already covered by heading-based intervals
    for win_start in range(total - _KEYWORD_WINDOW_SIZE + 1):
        win_end = win_start + _KEYWORD_WINDOW_SIZE
        keyword_hits = 0
        first_hit = None
        for j in range(win_start, win_end):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if _is_already_covered(j, intervals):
                continue
            if _contains_score_keyword(stripped):
                keyword_hits += 1
                if first_hit is None:
                    first_hit = j
        if keyword_hits >= _KEYWORD_WINDOW_THRESHOLD and first_hit is not None:
            intervals.append((first_hit, win_end))

    # Strategy 4: table recognition — keyword lines near markdown tables
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_already_covered(i, intervals):
            continue
        if not _contains_score_keyword(stripped):
            continue

        table_start, table_end = _find_table_range(lines, i, total)
        if table_end > table_start + 1:
            intervals.append((min(i, table_start), max(table_end, i + _SCORE_EXTEND_MIN_LINES)))

    # Merge overlapping / nearby intervals
    merged = _merge_intervals(intervals)

    if not merged:
        warnings.append("未在招标文件中识别到评分标准章节，请人工检查并填写 score.md。")
        return raw_markdown, "", warnings

    # Build output
    score_lines_list: list[str] = []
    tender_lines_list: list[str] = []

    last_end = 0
    for start, end in merged:
        tender_lines_list.extend(lines[last_end:start])
        score_lines_list.extend(lines[start:end])
        last_end = end
    tender_lines_list.extend(lines[last_end:])

    score_md = "\n".join(score_lines_list).strip()
    tender_md = "\n".join(tender_lines_list).strip()

    if not score_md:
        warnings.append("提取的评分标准内容为空，请人工检查。")

    return tender_md, score_md, warnings


def scan_and_merge_tender(root: Path | None = None) -> str:
    root = root or project_root()
    sources_dir = root / "sources" / "tender"
    if not sources_dir.exists() or not any(sources_dir.iterdir()):
        raise FileNotFoundError(
            f"招标文件夹为空或不存在: {sources_dir}，请先将招标文件放入 sources/tender/"
        )

    files = sorted(sources_dir.iterdir())
    supported_files = [f for f in files if f.suffix.lower() in TENDER_EXTENSIONS and f.is_file()]
    if not supported_files:
        raise FileNotFoundError(
            f"sources/tender/ 中没有可识别的文件（支持: {TENDER_EXTENSIONS}）"
        )

    all_parts: list[str] = []
    for file_path in supported_files:
        print(f"  [转换] {file_path.name} ...")
        try:
            content = convert_to_markdown(file_path)
            header = f"<!-- 来源: {file_path.name} -->\n\n"
            all_parts.append(header + content)
        except Exception as exc:
            print(f"  [警告] 转换 {file_path.name} 失败: {exc}")

    if not all_parts:
        raise ValueError("所有招标文件转换失败，请检查文件格式。")

    return "\n\n---\n\n".join(all_parts)


def run_tender_import(root: Path | None = None) -> Path:
    root = root or project_root()
    merged = scan_and_merge_tender(root)

    imported_dir = root / "workspace" / "imported"
    imported_dir.mkdir(parents=True, exist_ok=True)
    raw_path = imported_dir / "tender_raw.md"
    write_text(raw_path, merged)
    print(f"[完成] 已写入招标原文: {raw_path} ({len(merged)} 字符)")

    tender_md, score_md, warnings = split_tender_and_score(merged)

    tender_path = root / "inputs" / "tender.md"
    score_path = root / "inputs" / "score.md"

    write_text(tender_path, tender_md)
    write_text(score_path, score_md)

    print(f"[完成] 已生成招标文件: {tender_path} ({len(tender_md)} 字符)")
    print(f"[完成] 已生成评分标准: {score_path} ({len(score_md)} 字符)")

    if warnings:
        warnings_path = imported_dir / "score_extract_warnings.txt"
        write_text(warnings_path, "\n".join(warnings) + "\n")
        print(f"[警告] 评分标准提取存在问题，详见: {warnings_path}")
        for w in warnings:
            print(f"  - {w}")

    return tender_path
