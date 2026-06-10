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


def _append_log(line: str) -> None:
    global LOG_LINES
    LOG_LINES.append(line)
    if len(LOG_LINES) > LOG_MAX:
        LOG_LINES = LOG_LINES[-LOG_MAX:]


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


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return {
        "inputs": {
            "tender_md": _exists(ROOT / "inputs" / "tender.md"),
            "company_md": _exists(ROOT / "inputs" / "company.md"),
            "score_md": _exists(ROOT / "inputs" / "score.md"),
            "template_docx": _exists(ROOT / "inputs" / "template.docx"),
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
        "running": RUNNING,
        "current_task": CURRENT_TASK,
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
        process = subprocess.Popen(
            [sys.executable, *args],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in process.stdout:
            line = line.rstrip("\n").rstrip("\r")
            if line:
                _append_log(line)
        process.wait()
        exit_code = process.returncode
    except Exception as exc:
        _append_log(f"[错误] 命令执行异常: {exc}")
        exit_code = 1

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
