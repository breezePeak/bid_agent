from __future__ import annotations

import re
from pathlib import Path

from document_converter import convert_to_markdown
from utils import project_root, write_text


TENDER_EXTENSIONS = {".md", ".docx", ".pdf"}

SCORE_KEYWORDS = [
    r"评[分审]标[准程]",
    r"评分[标细]则",
    r"评[标审]办?法",
    r"综合[评打]分",
    r"技术[评打]分",
    r"商务[评打]分",
    r"价格[评打]分",
    r"评分项",
    r"评分点",
    r"评分表",
    r"废标项",
]


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


def extract_score_section(tender_markdown: str) -> str | None:
    lines = tender_markdown.splitlines()
    in_score_section = False
    score_lines: list[str] = []
    score_start = -1
    score_end = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            heading_text = heading_match.group(2)
            level = len(heading_match.group(1))

            was_in_score = in_score_section
            in_score_section = False
            for keyword_pattern in SCORE_KEYWORDS:
                if re.search(keyword_pattern, heading_text):
                    in_score_section = True
                    break

            if in_score_section and score_start < 0:
                score_start = i
            elif not in_score_section and was_in_score and score_end < 0:
                score_end = i

    if score_start < 0:
        return None

    if score_end < 0:
        score_end = len(lines)

    score_lines = lines[score_start:score_end]
    return "\n".join(score_lines).strip()


def run_tender_import(root: Path | None = None) -> Path:
    root = root or project_root()
    merged = scan_and_merge_tender(root)
    output_path = root / "inputs" / "tender.md"
    write_text(output_path, merged)
    print(f"[完成] 已生成招标文件: {output_path} ({len(merged)} 字符)")

    score_path = root / "inputs" / "score.md"
    if not score_path.exists() or not score_path.read_text(encoding="utf-8").strip():
        score_section = extract_score_section(merged)
        if score_section:
            write_text(score_path, score_section)
            print(f"[完成] 已提取评分标准: {score_path} ({len(score_section)} 字符)")
        else:
            print(f"[提示] 未在招标文件中识别到评分标准章节，请手动填写: {score_path}")
    else:
        print(f"[提示] 评分标准文件已存在，跳过提取: {score_path}")

    return output_path
