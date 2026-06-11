from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

sys.path.insert(0, str(ROOT / "src"))

app = FastAPI(title="标书 Agent 控制台", docs_url=None, redoc_url=None)

LOG_LINES: list[str] = []
LOG_MAX = 2000
RUNNING = False
CURRENT_TASK = ""

WORKFLOW_STEPS: list[dict[str, Any]] = [
    {
        "id": "init",
        "label": "初始化项目",
        "command": "init",
        "kind": "utility",
        "requires": [],
        "produces": ["基础目录", "默认提示词"],
    },
    {
        "id": "prepare_inputs",
        "label": "导入资料",
        "command": "prepare-inputs",
        "kind": "core",
        "requires": ["sources/tender", "sources/company", "sources/template"],
        "produces": ["inputs/tender.md", "inputs/score.md", "inputs/company.md", "workspace/imported/*"],
    },
    {
        "id": "split_docs",
        "label": "切分文档",
        "command": "split-docs",
        "kind": "core",
        "requires": ["inputs/tender.md", "inputs/company.md"],
        "produces": ["workspace/chunks/tender_chunks.json", "workspace/chunks/company_chunks.json"],
    },
    {
        "id": "parse_score",
        "label": "解析评分",
        "command": "parse-score",
        "kind": "core",
        "requires": ["inputs/score.md"],
        "produces": ["workspace/score_points.json"],
    },
    {
        "id": "extract_facts",
        "label": "提取事实",
        "command": "extract-facts",
        "kind": "core",
        "requires": ["inputs/tender.md", "inputs/company.md"],
        "produces": ["workspace/global_facts.json"],
    },
    {
        "id": "generate_outline",
        "label": "生成大纲",
        "command": "generate-outline",
        "kind": "core",
        "requires": ["workspace/score_points.json", "workspace/global_facts.json", "inputs/tender.md"],
        "produces": ["workspace/outline.json"],
    },
    {
        "id": "plan_jobs",
        "label": "生成任务",
        "command": "plan-jobs",
        "kind": "core",
        "requires": ["workspace/outline.json"],
        "produces": ["workspace/jobs/*.json"],
    },
    {
        "id": "select_context",
        "label": "选择上下文",
        "command": "select-context-all",
        "kind": "core",
        "requires": ["workspace/jobs/*.json", "workspace/chunks/tender_chunks.json", "workspace/chunks/company_chunks.json"],
        "produces": ["workspace/contexts/*_context.json", "workspace/contexts/*_ranked_chunks.json"],
    },
    {
        "id": "write_all",
        "label": "生成章节",
        "command": "write-all",
        "kind": "core",
        "requires": ["workspace/contexts/*_context.json"],
        "produces": ["workspace/chapters/*.md"],
    },
    {
        "id": "review_fix_all",
        "label": "审核改稿",
        "command": "review-fix-all",
        "kind": "core",
        "requires": ["workspace/chapters/*.md"],
        "produces": ["workspace/reviews/*_review.json", "workspace/rewrites/*_rewrite_log.json"],
    },
    {
        "id": "summarize_all",
        "label": "生成摘要",
        "command": "summarize-all",
        "kind": "core",
        "requires": ["workspace/chapters/*.md", "workspace/reviews/*_review.json"],
        "produces": ["workspace/summaries/*_summary.json"],
    },
    {
        "id": "global_review",
        "label": "全文审核",
        "command": "global-review",
        "kind": "core",
        "requires": ["workspace/summaries/*_summary.json", "workspace/score_points.json", "workspace/global_facts.json", "workspace/outline.json"],
        "produces": ["workspace/global_review.json"],
    },
    {
        "id": "build_md",
        "label": "拼接 MD",
        "command": "build-md",
        "kind": "core",
        "requires": ["workspace/chapters/*.md", "workspace/outline.json"],
        "produces": ["outputs/final.md"],
    },
    {
        "id": "build_docx",
        "label": "生成 Word",
        "command": "build-docx",
        "kind": "core",
        "requires": ["outputs/final.md", "inputs/template.docx"],
        "produces": ["outputs/final.docx"],
    },
]


def _append_log(line: str) -> None:
    global LOG_LINES
    LOG_LINES.append(line)
    if len(LOG_LINES) > LOG_MAX:
        LOG_LINES = LOG_LINES[-LOG_MAX:]


def _decode_log_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------
#  Static files & templates
# ---------------------------------------------------------------

if (WEB_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    index_path = WEB_DIR / "templates" / "index.html"
    if not index_path.exists():
        return "<h1>缺少 web/templates/index.html</h1>"
    return index_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------
#  Status
# ---------------------------------------------------------------

def _exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _count_glob(directory: Path, pattern: str) -> int:
    return len(list(directory.glob(pattern))) if directory.exists() else 0


def _list_source_files(category: str) -> list[dict[str, Any]]:
    source_dir = ROOT / "sources" / category
    if not source_dir.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(source_dir.iterdir()):
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
                }
            )
    return files


def _latest_mtime_in_dir(directory: Path) -> float:
    if not directory.exists():
        return 0.0
    mtimes = [directory.stat().st_mtime]
    mtimes.extend(item.stat().st_mtime for item in directory.iterdir() if item.is_file())
    return max(mtimes) if mtimes else 0.0


def _latest_mtime(paths: list[Path]) -> float:
    mtimes = [path.stat().st_mtime for path in paths if path.exists() and path.is_file()]
    return max(mtimes) if mtimes else 0.0


def _path_status(path: str) -> bool:
    if path.endswith("/*"):
        target = ROOT / path[:-2]
        return target.exists() and any(target.glob("*"))
    target = ROOT / path
    return target.exists() and target.is_file() and target.stat().st_size > 0


def _step_status(step: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    key_map = {
        "inputs/tender.md": status["inputs"]["tender_md"],
        "inputs/company.md": status["inputs"]["company_md"],
        "inputs/score.md": status["inputs"]["score_md"],
        "inputs/template.docx": status["inputs"]["template_docx"],
        "workspace/imported/*": all(status["imported"].values()),
        "workspace/imported/tender_raw.md": status["imported"]["tender_raw"],
        "workspace/imported/tender_blocks.json": status["imported"]["tender_blocks"],
        "workspace/imported/tender_classified_blocks.json": status["imported"]["tender_classified_blocks"],
        "workspace/imported/tender_classification_report.json": status["imported"]["tender_classification_report"],
        "workspace/imported/tender_other.md": status["imported"]["tender_other"],
        "workspace/chunks/tender_chunks.json": status["chunks"]["tender_chunks"],
        "workspace/chunks/company_chunks.json": status["chunks"]["company_chunks"],
        "workspace/score_points.json": status["workspace"]["score_points"],
        "workspace/global_facts.json": status["workspace"]["global_facts"],
        "workspace/outline.json": status["workspace"]["outline"],
        "workspace/jobs/*.json": status["workspace"]["jobs_count"] > 0,
        "workspace/contexts/*_context.json": status["workspace"]["contexts_count"] > 0,
        "workspace/contexts/*_ranked_chunks.json": _count_glob(ROOT / "workspace" / "contexts", "*_ranked_chunks.json") > 0,
        "workspace/chapters/*.md": status["workspace"]["chapters_count"] > 0,
        "workspace/reviews/*_review.json": status["workspace"]["reviews_count"] > 0,
        "workspace/rewrites/*_rewrite_log.json": status["workspace"]["rewrites_count"] > 0,
        "workspace/summaries/*_summary.json": status["workspace"]["summaries_count"] > 0,
        "workspace/global_review.json": status["workspace"]["global_review"],
        "outputs/final.md": status["outputs"]["final_md"],
        "outputs/final.docx": status["outputs"]["final_docx"],
        "sources/tender": _path_status("sources/tender/*"),
        "sources/company": _path_status("sources/company/*"),
        "sources/template": _path_status("sources/template/*"),
    }

    requirements = [bool(key_map.get(req, False)) for req in step.get("requires", [])]
    ready = all(requirements)
    done = all(key_map.get(prod, False) for prod in step.get("produces", []))

    missing_requires = [req for req in step.get("requires", []) if not key_map.get(req, False)]
    source_stale = status["sync"]["source_stale"]
    if step["command"] == "prepare-inputs" and source_stale:
        done = False
        state = "ready"
        message = "sources/ 有新文件，需重新导入"
    elif source_stale and step["command"] != "prepare-inputs" and step["command"] != "init":
        done = False
        state = "blocked"
        message = "请先重新执行导入资料"
    elif done:
        state = "done"
        message = "已完成"
    elif ready:
        state = "ready"
        message = "可执行"
    else:
        state = "blocked"
        message = "等待前置步骤"

    return {
        **step,
        "ready": ready,
        "done": done,
        "state": state,
        "message": message,
        "missing_requires": missing_requires,
    }


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    source_latest = max(
        _latest_mtime_in_dir(ROOT / "sources" / "tender"),
        _latest_mtime_in_dir(ROOT / "sources" / "company"),
        _latest_mtime_in_dir(ROOT / "sources" / "template"),
    )
    imported_latest = _latest_mtime(
        [
            ROOT / "inputs" / "tender.md",
            ROOT / "inputs" / "score.md",
            ROOT / "inputs" / "company.md",
            ROOT / "inputs" / "template.docx",
            ROOT / "workspace" / "imported" / "tender_raw.md",
            ROOT / "workspace" / "imported" / "tender_blocks.json",
            ROOT / "workspace" / "imported" / "tender_classified_blocks.json",
            ROOT / "workspace" / "imported" / "tender_classification_report.json",
            ROOT / "workspace" / "imported" / "tender_other.md",
        ]
    )
    source_stale = bool(source_latest and imported_latest and source_latest > imported_latest)

    status = {
        "inputs": {
            "tender_md": _exists(ROOT / "inputs" / "tender.md"),
            "company_md": _exists(ROOT / "inputs" / "company.md"),
            "score_md": _exists(ROOT / "inputs" / "score.md"),
            "template_docx": _exists(ROOT / "inputs" / "template.docx"),
        },
        "sources": {
            "tender": _list_source_files("tender"),
            "company": _list_source_files("company"),
            "template": _list_source_files("template"),
        },
        "imported": {
            "tender_raw": _exists(ROOT / "workspace" / "imported" / "tender_raw.md"),
            "tender_blocks": _exists(ROOT / "workspace" / "imported" / "tender_blocks.json"),
            "tender_classified_blocks": _exists(ROOT / "workspace" / "imported" / "tender_classified_blocks.json"),
            "tender_classification_report": _exists(ROOT / "workspace" / "imported" / "tender_classification_report.json"),
            "tender_other": _exists(ROOT / "workspace" / "imported" / "tender_other.md"),
        },
        "chunks": {
            "tender_chunks": _exists(ROOT / "workspace" / "chunks" / "tender_chunks.json"),
            "company_chunks": _exists(ROOT / "workspace" / "chunks" / "company_chunks.json"),
        },
        "workspace": {
            "score_points": _exists(ROOT / "workspace" / "score_points.json"),
            "global_facts": _exists(ROOT / "workspace" / "global_facts.json"),
            "outline": _exists(ROOT / "workspace" / "outline.json"),
            "jobs_count": _count_glob(ROOT / "workspace" / "jobs", "*.json"),
            "contexts_count": _count_glob(ROOT / "workspace" / "contexts", "*_context.json"),
            "chapters_count": _count_glob(ROOT / "workspace" / "chapters", "*.md"),
            "reviews_count": _count_glob(ROOT / "workspace" / "reviews", "*_review.json"),
            "summaries_count": _count_glob(ROOT / "workspace" / "summaries", "*_summary.json"),
            "global_review": _exists(ROOT / "workspace" / "global_review.json"),
            "rewrites_count": _count_glob(ROOT / "workspace" / "rewrites", "*_rewrite_log.json"),
        },
        "outputs": {
            "final_md": _exists(ROOT / "outputs" / "final.md"),
            "final_docx": _exists(ROOT / "outputs" / "final.docx"),
        },
        "sync": {
            "source_stale": source_stale,
            "source_latest": source_latest,
            "imported_latest": imported_latest,
        },
        "running": RUNNING,
        "current_task": CURRENT_TASK,
    }

    workflow = [_step_status(step, status) for step in WORKFLOW_STEPS]
    next_step = next(
        (step for step in workflow if step["kind"] != "utility" and not step["done"] and step["ready"]),
        None,
    )
    if next_step is None:
        next_step = next((step for step in workflow if not step["done"] and step["ready"]), None)
    blocked_step = next((step for step in workflow if not step["done"] and not step["ready"]), None)

    return {
        **status,
        "workflow": workflow,
        "next_step": next_step,
        "blocked_step": blocked_step,
    }


# ---------------------------------------------------------------
#  Command execution
# ---------------------------------------------------------------

COMMANDS: dict[str, list[str]] = {
    "init": [],
    "init-demo": [],
    "prepare-inputs": [],
    "split-docs": [],
    "parse-score": [],
    "extract-facts": [],
    "generate-outline": [],
    "plan-jobs": [],
    "select-context-all": [],
    "write-all": ["--workers", "2"],
    "review-fix-all": [],
    "summarize-all": [],
    "global-review": [],
    "build-md": [],
    "build-docx": [],
    "validate": [],
    "run": ["--workers", "2"],
    "graph-run": ["--workers", "2"],
}


def _run_sync(command: str) -> int:
    global RUNNING, CURRENT_TASK
    RUNNING = True
    CURRENT_TASK = command

    args = ["src/main.py", command, *COMMANDS.get(command, [])]
    _append_log(f"--- [{time.strftime('%H:%M:%S')}] 开始: python {' '.join(args)} ---")

    try:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(
            [sys.executable, *args],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=1,
        )
        assert process.stdout is not None
        while True:
            raw_line = process.stdout.readline()
            if not raw_line:
                break
            line = _decode_log_bytes(raw_line).rstrip("\n").rstrip("\r")
            if line:
                _append_log(line)
        process.wait()
        exit_code = process.returncode
    except Exception as exc:
        _append_log(f"[错误] 命令执行异常: {exc}")
        exit_code = 1

    if exit_code != 0:
        _append_log(f"[错误] 流程已停止: {command} 执行失败，请查看上方报错。")
    _append_log(f"--- [{time.strftime('%H:%M:%S')}] 完成: exit_code={exit_code} ---")
    RUNNING = False
    CURRENT_TASK = ""
    return exit_code


@app.post("/api/run-command")
async def api_run_command(request: Request) -> JSONResponse:
    global RUNNING
    if RUNNING:
        return JSONResponse(
            {"ok": False, "message": "当前已有任务正在运行，请等待完成。"},
            status_code=409,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)

    command = body.get("command", "").strip()
    if not command:
        return JSONResponse({"ok": False, "message": "缺少 command 字段。"}, status_code=400)
    if command not in COMMANDS:
        return JSONResponse(
            {"ok": False, "message": f"未知命令: {command}，可用: {', '.join(sorted(COMMANDS))}"},
            status_code=400,
        )

    threading.Thread(target=_run_sync, args=(command,), daemon=True).start()
    return JSONResponse({"ok": True, "message": f"命令已启动: {command}"})


# ---------------------------------------------------------------
#  Logs
# ---------------------------------------------------------------

@app.get("/api/logs")
def api_logs(lines: int = Query(200, ge=1, le=2000)) -> JSONResponse:
    return JSONResponse({"lines": LOG_LINES[-lines:], "total": len(LOG_LINES)})


@app.get("/api/logs/stream")
async def api_logs_stream(request: Request) -> StreamingResponse:
    async def stream():
        last = 0
        while True:
            if await request.is_disconnected():
                break
            while last < len(LOG_LINES):
                yield f"data: {json.dumps({'line': LOG_LINES[last]}, ensure_ascii=False)}\n\n"
                last += 1
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------
#  File upload
# ---------------------------------------------------------------

VALID_CATEGORIES = {"tender", "company", "template"}


@app.post("/api/upload")
async def api_upload(category: str = "tender", files: list[UploadFile] = File(...)) -> JSONResponse:
    if category not in VALID_CATEGORIES:
        return JSONResponse({"ok": False, "message": f"无效 category: {category}"}, status_code=400)

    dest_dir = ROOT / "sources" / category
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        content = await f.read()
        dest = dest_dir / f.filename
        dest.write_bytes(content)
        saved.append(f.filename)
        _append_log(f"[上传] {category} → {f.filename}")

    return JSONResponse({"ok": True, "saved": saved, "count": len(saved)})


# ---------------------------------------------------------------
#  Download
# ---------------------------------------------------------------

@app.get("/api/download/final-md", response_model=None)
def download_final_md() -> FileResponse | JSONResponse:
    path = ROOT / "outputs" / "final.md"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md"}, status_code=404)
    return FileResponse(str(path), filename="final.md", media_type="text/markdown")


@app.get("/api/download/final-docx", response_model=None)
def download_final_docx() -> FileResponse | JSONResponse:
    path = ROOT / "outputs" / "final.docx"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.docx 不存在，请先执行 build-docx"}, status_code=404)
    return FileResponse(
        str(path),
        filename="final.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------
#  Global review JSON
# ---------------------------------------------------------------

@app.get("/api/file/global-review")
def api_global_review() -> JSONResponse:
    path = ROOT / "workspace" / "global_review.json"
    if not path.exists():
        return JSONResponse(
            {"ok": False, "message": "global_review.json 不存在，请先执行 global-review"},
            status_code=404,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse({"ok": True, "data": data})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"读取失败: {exc}"}, status_code=500)


# ---------------------------------------------------------------
#  Clean workspace
# ---------------------------------------------------------------

@app.post("/api/clean-workspace")
def api_clean_workspace() -> JSONResponse:
    global LOG_LINES

    for sub in ["workspace", "outputs"]:
        target = ROOT / sub
        if target.exists():
            shutil.rmtree(str(target))

    ROOT.joinpath("workspace").mkdir(parents=True, exist_ok=True)
    ROOT.joinpath("outputs").mkdir(parents=True, exist_ok=True)

    _append_log(f"[清空] 已清空 workspace/ 和 outputs/")
    return JSONResponse({"ok": True, "message": "workspace/ 和 outputs/ 已清空"})


# ---------------------------------------------------------------
#  Startup
# ---------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    _append_log("[系统] 标书 Agent Web 控制台已启动")
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="warning")
