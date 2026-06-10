from __future__ import annotations

from pathlib import Path

from document_converter import convert_to_markdown
from utils import project_root, write_text


COMPANY_EXTENSIONS = {".md", ".docx", ".pdf"}


def scan_and_merge_company(root: Path | None = None) -> str:
    root = root or project_root()
    sources_dir = root / "sources" / "company"
    if not sources_dir.exists() or not any(sources_dir.iterdir()):
        raise FileNotFoundError(
            f"公司资料文件夹为空或不存在: {sources_dir}，请先将公司资料放入 sources/company/"
        )

    files = sorted(sources_dir.iterdir())
    supported_files = [f for f in files if f.suffix.lower() in COMPANY_EXTENSIONS and f.is_file()]
    if not supported_files:
        raise FileNotFoundError(
            f"sources/company/ 中没有可识别的文件（支持: {COMPANY_EXTENSIONS}）"
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
        raise ValueError("所有公司资料文件转换失败，请检查文件格式。")

    return "\n\n---\n\n".join(all_parts)


def run_company_import(root: Path | None = None) -> Path:
    root = root or project_root()
    merged = scan_and_merge_company(root)
    output_path = root / "inputs" / "company.md"
    write_text(output_path, merged)
    print(f"[完成] 已生成公司资料: {output_path} ({len(merged)} 字符)")
    return output_path
