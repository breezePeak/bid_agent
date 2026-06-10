from __future__ import annotations

import shutil
from pathlib import Path

from company_extractor import run_company_import
from tender_extractor import run_tender_import
from utils import ensure_dirs, project_root


def prepare_inputs(root: Path | None = None) -> None:
    root = root or project_root()

    sources_dir = root / "sources"
    if not sources_dir.exists() or not any(sources_dir.iterdir()):
        print("[提示] sources/ 目录为空或不存在。")
        print("  请将原始资料放入以下目录：")
        print(f"    {sources_dir / 'tender'}     ← 招标文件（.pdf/.docx/.md）")
        print(f"    {sources_dir / 'company'}    ← 公司资料（.pdf/.docx/.md）")
        print(f"    {sources_dir / 'template'}   ← 标书模板（.docx）")
        print("  然后重新运行: python src/main.py prepare-inputs")
        return

    _copy_template(root)
    run_tender_import(root)
    run_company_import(root)

    print("[完成] 资料导入完毕。")


def _copy_template(root: Path) -> None:
    template_src_dir = root / "sources" / "template"
    if not template_src_dir.exists():
        return

    docx_files = sorted(template_src_dir.glob("*.docx"))
    if not docx_files:
        return

    src_file = docx_files[0]
    dst_path = root / "inputs" / "template.docx"
    ensure_dirs(root, ["inputs"])
    shutil.copy2(str(src_file), str(dst_path))
    print(f"[完成] 已复制模板: {dst_path}")
