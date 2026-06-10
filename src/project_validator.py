from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from utils import project_root


def _check(
    name: str,
    level: str,
    message: str,
    suggestion: str = "",
) -> dict[str, str]:
    return {
        "name": name,
        "level": level,
        "message": message,
        "suggestion": suggestion,
    }


def _file_ok(path: Path) -> bool:
    return path.exists() and path.is_file()


def _has_files(path: Path, extensions: set[str]) -> bool:
    if not path.exists():
        return False
    return any(
        f.is_file() and f.suffix.lower() in extensions
        for f in path.iterdir()
    )


def validate_project(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()

    results: list[dict[str, str]] = []
    ok_count = 0
    warn_count = 0
    fail_count = 0

    def add(entry: dict[str, str]) -> None:
        nonlocal ok_count, warn_count, fail_count
        results.append(entry)
        level = entry["level"]
        if level == "ok":
            ok_count += 1
        elif level == "warn":
            warn_count += 1
        elif level == "fail":
            fail_count += 1

    TENDER_EXTS = {".pdf", ".docx", ".md"}
    PROMPT_FILES = [
        "parse_score.md",
        "extract_facts.md",
        "generate_outline.md",
        "write_chapter.md",
        "review_chapter.md",
        "select_context.md",
        "global_review.md",
    ]

    # 1. sources/tender
    tender_dir = root / "sources" / "tender"
    if not tender_dir.exists():
        add(_check("sources/tender", "fail", "sources/tender 目录不存在", "请创建 sources/tender/ 并放入招标文件"))
    else:
        add(_check("sources/tender", "ok", "sources/tender 目录存在"))

    # 2. sources/tender 下有文件
    if tender_dir.exists() and not _has_files(tender_dir, TENDER_EXTS):
        add(_check("sources/tender 文件", "warn", "sources/tender 下无 .pdf/.docx/.md 文件", "请放入招标文件后执行 prepare-inputs"))
    elif tender_dir.exists():
        add(_check("sources/tender 文件", "ok", "sources/tender 下存在可识别文件"))
    else:
        add(_check("sources/tender 文件", "fail", "sources/tender 不存在，无法检查文件", "请先创建 sources/tender/"))

    # 3. sources/company
    company_dir = root / "sources" / "company"
    if not company_dir.exists():
        add(_check("sources/company", "warn", "sources/company 目录不存在", "如需公司资料请创建 sources/company/"))
    else:
        add(_check("sources/company", "ok", "sources/company 目录存在"))

    # 4. sources/company 下有文件
    if company_dir.exists() and not _has_files(company_dir, TENDER_EXTS):
        add(_check("sources/company 文件", "warn", "sources/company 下无 .pdf/.docx/.md 文件", "请放入公司资料后执行 prepare-inputs"))
    elif company_dir.exists():
        add(_check("sources/company 文件", "ok", "sources/company 下存在可识别文件"))
    else:
        add(_check("sources/company 文件", "warn", "sources/company 不存在，无法检查文件"))

    # 5. inputs/tender.md
    tender_path = root / "inputs" / "tender.md"
    if not _file_ok(tender_path):
        add(_check("inputs/tender.md", "fail", "inputs/tender.md 不存在", "请执行 prepare-inputs"))
    elif tender_path.stat().st_size == 0:
        add(_check("inputs/tender.md", "fail", "inputs/tender.md 为空", "请检查 sources/tender/ 中文件是否可读"))
    else:
        add(_check("inputs/tender.md", "ok", f"inputs/tender.md 存在 ({tender_path.stat().st_size} bytes)"))

    # 6. inputs/score.md
    score_path = root / "inputs" / "score.md"
    if not _file_ok(score_path):
        add(_check("inputs/score.md", "fail", "inputs/score.md 不存在", "请执行 prepare-inputs"))
    elif score_path.stat().st_size == 0:
        add(_check("inputs/score.md", "fail", "inputs/score.md 为空", "评分标准未识别到，请人工检查或填写"))
    else:
        add(_check("inputs/score.md", "ok", f"inputs/score.md 存在 ({score_path.stat().st_size} bytes)"))

    # 7. inputs/company.md
    company_path = root / "inputs" / "company.md"
    if not _file_ok(company_path):
        add(_check("inputs/company.md", "warn", "inputs/company.md 不存在", "请执行 prepare-inputs 或放入公司资料"))
    elif company_path.stat().st_size == 0:
        add(_check("inputs/company.md", "warn", "inputs/company.md 为空", "如需公司资料请放入 sources/company/"))
    else:
        add(_check("inputs/company.md", "ok", f"inputs/company.md 存在 ({company_path.stat().st_size} bytes)"))

    # 8-14. prompts
    for prompt_file in PROMPT_FILES:
        p = root / "prompts" / prompt_file
        if not _file_ok(p):
            add(_check(f"prompts/{prompt_file}", "fail", f"prompts/{prompt_file} 不存在", "请执行 init"))
        else:
            add(_check(f"prompts/{prompt_file}", "ok", f"prompts/{prompt_file} 存在"))

    # 15. .env
    env_path = root / ".env"
    has_env_file = _file_ok(env_path)
    has_api_key = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    has_base_url = bool(os.environ.get("OPENAI_BASE_URL", "").strip())
    has_model = bool(os.environ.get("OPENAI_MODEL", "").strip())

    if not has_env_file and not has_api_key:
        add(_check(".env 文件", "fail", ".env 不存在且环境变量 OPENAI_API_KEY 未设置", "请在项目根目录创建 .env 文件"))
    else:
        add(_check(".env 文件", "ok", ".env 存在或环境变量已设置"))

    # 16. OPENAI_API_KEY
    if not has_env_file:
        if has_api_key:
            add(_check("OPENAI_API_KEY", "ok", "环境变量 OPENAI_API_KEY 已设置"))
        else:
            add(_check("OPENAI_API_KEY", "fail", "OPENAI_API_KEY 未设置", "请设置环境变量或创建 .env"))
    else:
        add(_check("OPENAI_API_KEY", "ok", "OPENAI_API_KEY 已配置"))

    # 17. OPENAI_BASE_URL
    if not has_env_file:
        if has_base_url:
            add(_check("OPENAI_BASE_URL", "ok", "环境变量 OPENAI_BASE_URL 已设置"))
        else:
            add(_check("OPENAI_BASE_URL", "fail", "OPENAI_BASE_URL 未设置", "请设置环境变量或创建 .env"))
    else:
        add(_check("OPENAI_BASE_URL", "ok", "OPENAI_BASE_URL 已配置"))

    # 18. OPENAI_MODEL
    if not has_env_file:
        if has_model:
            add(_check("OPENAI_MODEL", "ok", "环境变量 OPENAI_MODEL 已设置"))
        else:
            add(_check("OPENAI_MODEL", "fail", "OPENAI_MODEL 未设置", "请设置环境变量或创建 .env"))
    else:
        add(_check("OPENAI_MODEL", "ok", "OPENAI_MODEL 已配置"))

    # 19. workspace/imported/tender_raw.md
    raw_path = root / "workspace" / "imported" / "tender_raw.md"
    if _file_ok(raw_path):
        add(_check("workspace/imported/tender_raw.md", "ok", f"tender_raw.md 存在 ({raw_path.stat().st_size} bytes)"))
    else:
        add(_check("workspace/imported/tender_raw.md", "warn", "tender_raw.md 不存在", "请执行 prepare-inputs"))

    # 20. workspace/chunks/tender_chunks.json
    tender_chunks = root / "workspace" / "chunks" / "tender_chunks.json"
    if _file_ok(tender_chunks):
        add(_check("workspace/chunks/tender_chunks.json", "ok", "tender_chunks.json 存在"))
    else:
        add(_check("workspace/chunks/tender_chunks.json", "warn", "tender_chunks.json 不存在", "请执行 split-docs"))

    # 21. workspace/chunks/company_chunks.json
    company_chunks = root / "workspace" / "chunks" / "company_chunks.json"
    if _file_ok(company_chunks):
        add(_check("workspace/chunks/company_chunks.json", "ok", "company_chunks.json 存在"))
    else:
        add(_check("workspace/chunks/company_chunks.json", "warn", "company_chunks.json 不存在", "请执行 split-docs"))

    # 22. workspace/jobs 下 json 文件
    jobs_dir = root / "workspace" / "jobs"
    if jobs_dir.exists() and list(jobs_dir.glob("*.json")):
        add(_check("workspace/jobs", "ok", "jobs 目录下存在任务文件"))
    else:
        add(_check("workspace/jobs", "warn", "workspace/jobs 下无 json 文件", "请执行 plan-jobs"))

    # 23. workspace/contexts 下 context json
    contexts_dir = root / "workspace" / "contexts"
    if contexts_dir.exists() and list(contexts_dir.glob("*_context.json")):
        add(_check("workspace/contexts", "ok", "contexts 目录下存在上下文文件"))
    else:
        add(_check("workspace/contexts", "warn", "workspace/contexts 下无上下文文件", "请执行 select-context-all"))

    # 24. workspace/chapters 下 md 文件
    chapters_dir = root / "workspace" / "chapters"
    if chapters_dir.exists() and list(chapters_dir.glob("*.md")):
        add(_check("workspace/chapters", "ok", "chapters 目录下存在章节文件"))
    else:
        add(_check("workspace/chapters", "warn", "workspace/chapters 下无 md 文件", "请执行 write-all"))

    # 25. outputs/final.md
    final_md = root / "outputs" / "final.md"
    if _file_ok(final_md):
        add(_check("outputs/final.md", "ok", f"final.md 存在 ({final_md.stat().st_size} bytes)"))
    else:
        add(_check("outputs/final.md", "warn", "outputs/final.md 不存在", "请执行 build-md"))

    # 26. outputs/final.docx
    final_docx = root / "outputs" / "final.docx"
    if _file_ok(final_docx):
        add(_check("outputs/final.docx", "ok", f"final.docx 存在 ({final_docx.stat().st_size} bytes)"))
    else:
        add(_check("outputs/final.docx", "warn", "outputs/final.docx 不存在", "请执行 build-docx"))

    return {
        "results": results,
        "ok": ok_count,
        "warn": warn_count,
        "fail": fail_count,
    }
