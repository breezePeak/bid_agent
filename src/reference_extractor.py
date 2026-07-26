from __future__ import annotations

from pathlib import Path

from document_converter import convert_to_markdown
from utils import project_root, write_text


REFERENCE_EXTENSIONS = {".md", ".docx", ".pdf"}


def _merge_optional_sources(root: Path, folder: str, label: str) -> str:
    source_dir = root / "sources" / folder
    if not source_dir.exists():
        return ""

    files = sorted(
        file_path
        for file_path in source_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in REFERENCE_EXTENSIONS
    )
    parts: list[str] = []
    for file_path in files:
        print(f"  [转换] {label}: {file_path.name} ...")
        try:
            content = convert_to_markdown(file_path).strip()
        except Exception as exc:
            print(f"  [警告] 转换 {file_path.name} 失败: {exc}")
            continue
        if content:
            parts.append(f"<!-- 来源: {file_path.name}；资料类型: {label} -->\n\n{content}")
    return "\n\n---\n\n".join(parts)


def run_reference_import(root: Path | None = None) -> tuple[Path | None, Path | None]:
    """Import optional domain references and the operator's project writing brief.

    Reference material is deliberately kept separate from company evidence: it may
    support background, standards and technical methods, but must never be used to
    assert bidder qualifications, staff, certificates or completed performance.
    """
    root = root or project_root()
    inputs_dir = root / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    reference_text = _merge_optional_sources(root, "reference", "外部参考资料")
    reference_path: Path | None = None
    if reference_text:
        reference_path = inputs_dir / "reference.md"
        write_text(reference_path, reference_text)
        print(f"[完成] 已生成外部参考资料: {reference_path} ({len(reference_text)} 字符)")

    default_brief_path = root / "prompts" / "project_writing_brief.md"
    if not default_brief_path.exists():
        default_brief_path = (
            Path(__file__).resolve().parent.parent
            / "prompts"
            / "project_writing_brief.md"
        )
    default_brief = (
        default_brief_path.read_text(encoding="utf-8").strip()
        if default_brief_path.exists()
        else ""
    )
    custom_brief = _merge_optional_sources(root, "guidance", "项目写作要求")
    brief_text = "\n\n---\n\n".join(
        part for part in (default_brief, custom_brief) if part
    )
    brief_path: Path | None = None
    if brief_text:
        brief_path = inputs_dir / "writing_brief.md"
        write_text(brief_path, brief_text)
        print(f"[完成] 已生成项目写作要求: {brief_path} ({len(brief_text)} 字符)")

    return reference_path, brief_path
