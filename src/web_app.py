from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
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
RUNS_DIR = ROOT / "runs"
ACTIVE_RUN_FILE = RUNS_DIR / ".active_run"

sys.path.insert(0, str(ROOT / "src"))

from graph.state_recorder import load_run_events, load_stage_metrics, save_run_state
from manual_review import apply_manual_review_update, manual_review_items, manual_review_summary
from pipeline_registry import auto_run_commands, stage_command_map, workflow_stage_specs
from project_profile_registry import load_project_profile, project_profile_choices, save_project_profile
from prompt_registry import AGENT_SPECS

app = FastAPI(title="标书 Agent 控制台", docs_url=None, redoc_url=None)

LOG_LINES: list[str] = []
LOG_MAX = 2000
RUNNING = False
CURRENT_TASK = ""
CURRENT_PROCESS: subprocess.Popen | None = None
CURRENT_RUN_ID = ""
CURRENT_RUN_ROOT: Path | None = None
PAUSE_REQUESTED = False
ACTIVE_RUN_ID = ""
ACTIVE_RUN_ROOT: Path | None = None
_PENDING_LINE_EDITS: dict[Path, dict[str, Any]] = {}

def _workflow_step_payload() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for spec in workflow_stage_specs():
        steps.append(
            {
                "id": spec.id,
                "label": spec.label,
                "command": spec.command,
                "kind": spec.kind,
                "requires": [{"path": artifact.path, "kind": artifact.kind} for artifact in spec.requires],
                "produces": [{"path": artifact.path, "kind": artifact.kind} for artifact in spec.produces],
            }
        )
    return steps


WORKFLOW_STEPS: list[dict[str, Any]] = _workflow_step_payload()
WORKFLOW_COMMAND_LABELS = {step["command"]: step["label"] for step in WORKFLOW_STEPS}


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


def _active_root() -> Path:
    _load_active_run_from_disk()
    return ACTIVE_RUN_ROOT or ROOT


def _load_active_run_from_disk() -> None:
    global ACTIVE_RUN_ID, ACTIVE_RUN_ROOT
    if ACTIVE_RUN_ROOT and ACTIVE_RUN_ROOT.exists():
        return
    run_id = ""
    if ACTIVE_RUN_FILE.exists():
        run_id = ACTIVE_RUN_FILE.read_text(encoding="utf-8").strip()
    if run_id:
        run_root = RUNS_DIR / run_id
        if run_root.exists():
            ACTIVE_RUN_ID = run_id
            ACTIVE_RUN_ROOT = run_root
            return
    if RUNS_DIR.exists():
        run_dirs = sorted([path for path in RUNS_DIR.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime)
        if run_dirs:
            ACTIVE_RUN_ROOT = run_dirs[-1]
            ACTIVE_RUN_ID = ACTIVE_RUN_ROOT.name


def _active_run_payload() -> dict[str, Any]:
    root = _active_root()
    return {
        "id": ACTIVE_RUN_ID,
        "root": str(root),
        "relative_root": str(root.relative_to(ROOT)) if root != ROOT and root.is_relative_to(ROOT) else str(root),
        "isolated": ACTIVE_RUN_ROOT is not None,
    }


def _same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _read_run_state(root: Path) -> dict[str, Any]:
    run_state_path = root / "workspace" / "run_state.json"
    if not run_state_path.exists():
        return {}
    try:
        loaded = json.loads(run_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(float(seconds))))
    minutes, second = divmod(total, 60)
    if minutes == 0:
        return f"{second}秒"
    hours, minute = divmod(minutes, 60)
    if hours == 0:
        return f"{minute}分{second:02d}秒"
    return f"{hours}小时{minute:02d}分{second:02d}秒"


def _load_run_history(root: Path) -> list[dict[str, Any]]:
    history_path = root / "workspace" / "run_state_history.jsonl"
    if not history_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _copy_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(str(target))
            shutil.copytree(str(item), str(target))
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))


def _normalize_run_name(name: str) -> str:
    cleaned = re.sub(r"\s+", "_", name.strip())
    cleaned = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned[:48]


def _create_run_workspace(name: str, project_type: str | None = None) -> tuple[str, Path]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _normalize_run_name(name)
    if not safe_name:
        raise ValueError("请先设置工作空间名称。")
    base_id = f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}"
    run_id = base_id
    run_root = RUNS_DIR / run_id
    suffix = 1
    while run_root.exists():
        suffix += 1
        run_id = f"{base_id}_{suffix}"
        run_root = RUNS_DIR / run_id

    for relative_dir in [
        "sources/tender",
        "sources/company",
        "sources/template",
        "inputs",
        "workspace",
        "workspace/chunks",
        "workspace/imported",
        "workspace/jobs",
        "workspace/contexts",
        "workspace/chapters",
        "workspace/reviews",
        "workspace/rewrites",
        "workspace/summaries",
        "outputs",
    ]:
        (run_root / relative_dir).mkdir(parents=True, exist_ok=True)

    _copy_tree_contents(ROOT / "sources", run_root / "sources")
    _copy_tree_contents(ROOT / "prompts", run_root / "prompts")
    save_project_profile(run_root, project_type)
    return run_id, run_root


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
    source_dir = _active_root() / "sources" / category
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


def _path_status(root: Path, path: str) -> bool:
    if path.endswith("/*"):
        target = root / path[:-2]
        return target.exists() and any(target.glob("*"))
    target = root / path
    return target.exists() and target.is_file() and target.stat().st_size > 0


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _load_agent_runs(
    root: Path,
    *,
    stage: str = "",
    chapter_id: str = "",
    agent_name: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    agent_runs_dir = root / "workspace" / "agent_runs"
    if not agent_runs_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(agent_runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json_file(path)
        if not isinstance(payload, dict):
            continue
        if stage and str(payload.get("stage", "")).strip() != stage:
            continue
        if chapter_id and str(payload.get("chapter_id", "")).strip() != chapter_id:
            continue
        if agent_name and str(payload.get("agent_name", "")).strip() != agent_name:
            continue
        rows.append(
            {
                "path": _safe_relative(root, path),
                "stage": str(payload.get("stage", "")),
                "agent_name": str(payload.get("agent_name", "")),
                "chapter_id": str(payload.get("chapter_id", "")),
                "started_at": str(payload.get("started_at", "")),
                "finished_at": str(payload.get("finished_at", "")),
                "duration_ms": payload.get("duration_ms", 0),
                "llm_calls": payload.get("llm_calls", 0),
                "model": str(payload.get("model", "")),
                "temperature": payload.get("temperature", 0),
                "prompt_file": str(payload.get("prompt_file", "")),
                "prompt_version": str(payload.get("prompt_version", "")),
                "prompt_checksum": str(payload.get("prompt_checksum", "")),
                "project_type": str(payload.get("project_type", "")),
                "context_budget": payload.get("context_budget", {}),
                "input_summary": payload.get("input_summary", {}),
                "output_summary": payload.get("output_summary", {}),
                "input_tokens_est": payload.get("input_tokens_est", 0),
                "output_tokens_est": payload.get("output_tokens_est", 0),
            }
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def _latest_agent_runs(root: Path, limit: int = 8) -> list[dict[str, Any]]:
    return _load_agent_runs(root, limit=limit)


def _budget_hits_for_command(root: Path, command: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if command == "select-context-all":
        contexts_dir = root / "workspace" / "contexts"
        for path in sorted(contexts_dir.glob("*_context.json")) if contexts_dir.exists() else []:
            payload = _read_json_file(path)
            if not isinstance(payload, dict):
                continue
            meta = payload.get("selection_meta", {})
            if not isinstance(meta, dict):
                meta = {}
            rows.append(
                {
                    "chapter_id": str(payload.get("chapter_id", "")),
                    "metric": "context_selection",
                    "max_context_chars": meta.get("max_context_chars", 0),
                    "max_chunks_per_side": meta.get("max_chunks_per_side", 0),
                    "tender_candidates_total": meta.get("tender_candidates_total", 0),
                    "tender_candidates_in_prompt": meta.get("tender_candidates_in_prompt", 0),
                    "company_candidates_total": meta.get("company_candidates_total", 0),
                    "company_candidates_in_prompt": meta.get("company_candidates_in_prompt", 0),
                    "dropped_reason": str(meta.get("dropped_reason", "")),
                }
            )
    stage = _command_for_stage(command) if command not in STAGE_TO_COMMAND.values() else next(
        (stage_name for stage_name, stage_command in STAGE_TO_COMMAND.items() if stage_command == command),
        command,
    )
    for run in _load_agent_runs(root, stage=stage, limit=100):
        budget = run.get("context_budget", {})
        if not isinstance(budget, dict) or not budget:
            continue
        summary = run.get("input_summary", {})
        if not isinstance(summary, dict):
            summary = {}
        rows.append(
            {
                "chapter_id": run.get("chapter_id", ""),
                "agent_name": run.get("agent_name", ""),
                "metric": "agent_prompt_budget",
                "prompt_file": run.get("prompt_file", ""),
                "prompt_checksum": run.get("prompt_checksum", ""),
                "context_budget": budget,
                "input_summary": summary,
            }
        )
    return rows[:24]


def _file_payload(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    exists = path.exists()
    payload: dict[str, Any] = {
        "path": relative_path,
        "exists": exists,
        "size": path.stat().st_size if exists and path.is_file() else 0,
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)) if exists else "",
    }
    if path.is_dir():
        payload["type"] = "dir"
        payload["count"] = len(list(path.iterdir()))
    else:
        payload["type"] = "file"
    return payload


def _artifact_path(artifact: Any) -> str:
    if isinstance(artifact, dict):
        return str(artifact.get("path", ""))
    return str(artifact)


def _artifact_kind(artifact: Any) -> str:
    if isinstance(artifact, dict):
        return str(artifact.get("kind", "file"))
    text = str(artifact)
    if text in {"基础目录", "默认提示词"}:
        return "virtual"
    if "*" in text:
        return "glob"
    return "file"


def _artifact_payloads(root: Path, artifacts: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_path = _artifact_path(artifact)
        artifact_kind = _artifact_kind(artifact)
        if artifact_kind == "glob":
            directory_text, pattern = artifact_path.rsplit("/", 1)
            directory = root / directory_text
            files = sorted(directory.glob(pattern)) if directory.exists() else []
            latest = max((path.stat().st_mtime for path in files), default=0)
            output.append(
                {
                    "path": artifact_path,
                    "type": "glob",
                    "exists": bool(files),
                    "count": len(files),
                    "size": sum(path.stat().st_size for path in files if path.is_file()),
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest)) if latest else "",
                    "samples": [path.name for path in files[:8]],
                    "previewable": True,
                }
            )
            continue
        if artifact_kind == "virtual":
            output.append({"path": artifact_path, "type": "virtual", "exists": True, "size": 0, "modified": "", "previewable": False})
            continue
        payload = _file_payload(root, artifact_path)
        payload["previewable"] = payload.get("exists", False) and payload.get("type") == "file"
        output.append(payload)
    return output


def _json_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("items", "data", "chapters", "score_points", "score_requirements"):
            nested = value.get(key)
            if isinstance(nested, list):
                return len(nested)
        return len(value)
    return 0


def _summarize_review_step(root: Path) -> dict[str, Any]:
    reviews_dir = root / "workspace" / "reviews"
    rewrites_dir = root / "workspace" / "rewrites"
    review_files = sorted(reviews_dir.glob("*_review.json")) if reviews_dir.exists() else []
    rewrite_files = sorted(rewrites_dir.glob("*_rewrite_log.json")) if rewrites_dir.exists() else []
    need_rewrite: list[str] = []
    problem_count = 0
    coverage = {"high": 0, "medium": 0, "low": 0, "none": 0}
    problem_samples: list[str] = []
    for path in review_files:
        data = _read_json_file(path)
        if not isinstance(data, dict):
            continue
        chapter_id = str(data.get("chapter_id") or path.name.replace("_review.json", ""))
        if data.get("need_rewrite"):
            need_rewrite.append(chapter_id)
        problems = data.get("problems") if isinstance(data.get("problems"), list) else []
        problem_count += len(problems)
        for problem in problems[:2]:
            if isinstance(problem, dict):
                problem_samples.append(f"{chapter_id}: {problem.get('description') or problem.get('type') or '需修订'}")
        for item in data.get("score_coverage") or []:
            if isinstance(item, dict):
                level = str(item.get("coverage_level") or "").lower()
                if level in coverage:
                    coverage[level] += 1
    return {
        "审核文件": len(review_files),
        "打回重写日志": len(rewrite_files),
        "当前仍需重写": len(need_rewrite),
        "问题数": problem_count,
        "高覆盖": coverage["high"],
        "中覆盖": coverage["medium"],
        "低覆盖": coverage["low"],
        "未覆盖": coverage["none"],
        "仍需重写章节": need_rewrite[:12],
        "问题示例": problem_samples[:8],
    }


def _review_detail_rows(root: Path) -> list[dict[str, Any]]:
    reviews_dir = root / "workspace" / "reviews"
    rewrites_dir = root / "workspace" / "rewrites"
    review_files = sorted(reviews_dir.glob("*_review.json")) if reviews_dir.exists() else []
    rewrite_ids = {
        path.name.replace("_rewrite_log.json", "")
        for path in rewrites_dir.glob("*_rewrite_log.json")
    } if rewrites_dir.exists() else set()
    rows: list[dict[str, Any]] = []
    for path in review_files:
        data = _read_json_file(path)
        if not isinstance(data, dict):
            continue
        chapter_id = str(data.get("chapter_id") or path.name.replace("_review.json", ""))
        problems = [
            str(item.get("description") or item.get("type") or "")
            for item in (data.get("problems") or [])
            if isinstance(item, dict)
        ]
        weak_coverage: list[str] = []
        for item in data.get("score_coverage") or []:
            if not isinstance(item, dict):
                continue
            level = str(item.get("coverage_level") or "").lower()
            if level in {"low", "none"} or not item.get("covered", True):
                score_id = str(item.get("score_point_id") or "")
                weak_coverage.append(f"{score_id}({level or '未覆盖'})")
        rows.append(
            {
                "chapter_id": chapter_id,
                "chapter_title": str(data.get("chapter_title") or ""),
                "need_rewrite": bool(data.get("need_rewrite")),
                "rewritten": chapter_id in rewrite_ids,
                "problem_count": len(problems),
                "problems": problems[:3],
                "weak_coverage": weak_coverage[:6],
                "review_path": str(path.relative_to(root)).replace("\\", "/"),
                "rewrite_path": f"workspace/rewrites/{chapter_id}_rewrite_log.json" if chapter_id in rewrite_ids else "",
            }
        )
    rows.sort(key=lambda item: (not item["need_rewrite"], item["chapter_id"]))
    return rows


def _score_point_rows(root: Path) -> list[dict[str, Any]]:
    points = _read_json_file(root / "workspace" / "score_points.json")
    if not isinstance(points, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in points[:80]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "category": str(item.get("category", "")),
                "score": item.get("score"),
                "requirement": str(item.get("requirement", "")),
                "response_strategy": str(item.get("response_strategy", "")),
                "keywords": item.get("keywords", []) if isinstance(item.get("keywords"), list) else [],
            }
        )
    return rows


def _score_requirement_rows(root: Path) -> list[dict[str, Any]]:
    requirements = _read_json_file(root / "workspace" / "score_requirements.json")
    if not isinstance(requirements, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in requirements[:80]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "category": str(item.get("category", "")),
                "title": str(item.get("title", "")),
                "score": item.get("score"),
                "requirement": str(item.get("requirement", "")),
                "scoring_criteria": str(item.get("scoring_criteria", "")),
                "keywords": item.get("keywords", []) if isinstance(item.get("keywords"), list) else [],
            }
        )
    return rows


def _stage_prompt_summary(root: Path, command: str) -> list[dict[str, Any]]:
    from pipeline_registry import STAGE_SPECS

    stage = next((st for st in STAGE_SPECS if st.command == command), None)
    if stage is None or not stage.prompt_agents:
        return []
    results: list[dict[str, Any]] = []
    for agent_name in stage.prompt_agents:
        spec = AGENT_SPECS.get(agent_name)
        if spec is None:
            results.append({"agent_name": agent_name, "prompt_file": "", "version": "", "file_exists": False, "missing": True})
            continue
        prompt_path = root / "prompts" / spec.prompt_file
        results.append({
            "agent_name": spec.name,
            "prompt_file": spec.prompt_file,
            "version": spec.version,
            "file_exists": prompt_path.exists() and prompt_path.is_file() and prompt_path.stat().st_size > 0,
            "context_budget": spec.context_budget if spec.context_budget else None,
        })
    return results


def _step_detail_summary(root: Path, command: str) -> dict[str, Any]:
    workspace = root / "workspace"
    if command == "prepare-inputs":
        report = _read_json_file(workspace / "imported" / "tender_classification_report.json")
        schema = _read_json_file(workspace / "template_schema.json")
        return {
            "分类块总数": report.get("total_blocks", 0) if isinstance(report, dict) else 0,
            "评分块": report.get("score_blocks", 0) if isinstance(report, dict) else 0,
            "需求块": report.get("tender_blocks", 0) if isinstance(report, dict) else 0,
            "其他块": report.get("other_blocks", 0) if isinstance(report, dict) else 0,
            "分类警告": report.get("warnings", []) if isinstance(report, dict) else [],
            "模板标题": len(schema.get("headings", [])) if isinstance(schema, dict) and isinstance(schema.get("headings"), list) else 0,
            "模板表格": len(schema.get("tables", [])) if isinstance(schema, dict) and isinstance(schema.get("tables"), list) else 0,
        }
    if command == "split-docs":
        tender = _read_json_file(workspace / "chunks" / "tender_chunks.json")
        company = _read_json_file(workspace / "chunks" / "company_chunks.json")
        return {"招标片段": _json_count(tender), "公司片段": _json_count(company)}
    if command == "parse-score":
        points = _read_json_file(workspace / "score_points.json")
        requirements = _read_json_file(workspace / "score_requirements.json")
        return {"评分点": _json_count(points), "原始评分要求": _json_count(requirements)}
    if command == "extract-facts":
        facts = _read_json_file(workspace / "global_facts.json")
        if isinstance(facts, dict):
            return {"事实字段": len(facts), "项目名称": facts.get("project_name", ""), "投标人": facts.get("bidder_name", "")}
        return {}
    if command == "build-template-evidence":
        quality = _read_json_file(workspace / "template_quality_report.json")
        if isinstance(quality, dict):
            return {
                "失败项": quality.get("fail_count", 0),
                "警告项": quality.get("warn_count", 0),
                "通过项": quality.get("ok_count", 0),
            }
        return {}
    if command == "generate-outline":
        outline = _read_json_file(workspace / "outline.json")
        return {"章节数": len(outline.get("chapters", [])) if isinstance(outline, dict) else 0}
    if command == "plan-jobs":
        return {"任务文件": _count_glob(workspace / "jobs", "*.json")}
    if command == "select-context-all":
        return {
            "上下文文件": _count_glob(workspace / "contexts", "*_context.json"),
            "排序片段文件": _count_glob(workspace / "contexts", "*_ranked_chunks.json"),
        }
    if command == "write-all":
        chapters = list((workspace / "chapters").glob("*.md")) if (workspace / "chapters").exists() else []
        return {"章节文件": len(chapters), "总字数": sum(len(path.read_text(encoding="utf-8", errors="ignore")) for path in chapters)}
    if command == "review-fix-all":
        return _summarize_review_step(root)
    if command == "build-source-trace":
        index = _read_json_file(workspace / "source_trace_index.json")
        return {"来源索引项": _json_count(index), "章节来源文件": _count_glob(workspace / "source_traces", "*_sources.json")}
    if command == "build-score-coverage":
        matrix = _read_json_file(workspace / "score_coverage_matrix.json")
        return matrix.get("summary", {}) if isinstance(matrix, dict) else {}
    if command == "summarize-all":
        return {"章节摘要": _count_glob(workspace / "summaries", "*_summary.json")}
    if command == "global-review":
        review = _read_json_file(workspace / "global_review.json")
        if isinstance(review, dict):
            return {
                "需人工复核": bool(review.get("need_manual_review")),
                "未覆盖评分点": len(review.get("uncovered_score_points") or []),
                "章节冲突": len(review.get("chapter_conflicts") or []),
                "编造风险": len(review.get("fabrication_risks") or []),
                "建议": review.get("suggestions", [])[:6] if isinstance(review.get("suggestions"), list) else [],
                "风险示例": review.get("fabrication_risks", [])[:6] if isinstance(review.get("fabrication_risks"), list) else [],
            }
        return {}
    if command == "build-md":
        final_md = root / "outputs" / "final.md"
        text = final_md.read_text(encoding="utf-8", errors="ignore") if final_md.exists() else ""
        return {"字数": len(text), "标题数": len(re.findall(r"(?m)^#{1,6}\\s+", text))}
    if command == "build-docx":
        report = _read_json_file(workspace / "template_fill_report.json")
        return {"模板填充项": _json_count(report), "填充报告存在": bool(report)}
    if command == "check-format":
        report = _read_json_file(workspace / "format_check_report.json")
        if isinstance(report, dict):
            return {
                "检查通过": bool(report.get("ok")),
                "通过项": report.get("ok_count", 0),
                "警告项": report.get("warn_count", 0),
                "失败项": report.get("fail_count", 0),
                "结果": [
                    item.get("message", "")
                    for item in report.get("results", [])
                    if isinstance(item, dict) and item.get("level") != "ok"
                ][:8],
            }
        return {}
    return {}


def _step_history(root: Path, command: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _load_run_history(root):
        if _command_for_stage(str(record.get("stage", ""))) == command:
            records.append(
                {
                    "status": record.get("status", ""),
                    "message": record.get("message", ""),
                    "updated_at": record.get("updated_at", ""),
                }
            )
    return records[-8:]


def _step_extra_details(root: Path, command: str) -> dict[str, Any]:
    if command == "parse-score":
        return {
            "score_point_rows": _score_point_rows(root),
            "score_requirement_rows": _score_requirement_rows(root),
        }
    if command == "review-fix-all":
        return {"review_rows": _review_detail_rows(root)}
    return {}


STAGE_TO_COMMAND: dict[str, str] = stage_command_map()


def _command_for_stage(stage: str) -> str:
    return STAGE_TO_COMMAND.get(stage, stage)


def _current_command_from_status(status: dict[str, Any]) -> str:
    if not status.get("running"):
        return ""
    task = str(status.get("current_task", "")).strip()
    if task in {"run", "graph-run"}:
        run_state = status.get("run_state", {}) if isinstance(status.get("run_state"), dict) else {}
        return _command_for_stage(str(run_state.get("stage", "")))
    return task


def _workflow_timings(
    root: Path,
    *,
    running: bool,
    current_task: str,
    run_state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    starts: dict[str, datetime] = {}
    timings: dict[str, dict[str, Any]] = {}
    workflow_commands = {step["command"] for step in WORKFLOW_STEPS}

    for record in _load_run_history(root):
        command = _command_for_stage(str(record.get("stage", "")))
        if command not in workflow_commands:
            continue
        updated_at = _parse_timestamp(record.get("updated_at"))
        if updated_at is None:
            continue

        state = str(record.get("status", "")).strip()
        timing = timings.setdefault(command, {})
        if state == "running":
            starts[command] = updated_at
            timing["started_at"] = updated_at.isoformat(timespec="seconds")
            timing["status"] = "running"
            timing["message"] = record.get("message", "")
            continue

        if state in {"ok", "error", "warn"}:
            started_at = starts.get(command) or _parse_timestamp(timing.get("started_at"))
            if started_at is not None:
                duration_seconds = max(0, (updated_at - started_at).total_seconds())
                timing["duration_seconds"] = int(round(duration_seconds))
                timing["duration_label"] = _format_duration(duration_seconds)
            timing["finished_at"] = updated_at.isoformat(timespec="seconds")
            timing["status"] = state
            timing["message"] = record.get("message", "")

    active_command = current_task
    if current_task in {"run", "graph-run"}:
        active_command = _command_for_stage(str(run_state.get("stage", "")))
    if running and active_command in workflow_commands:
        timing = timings.setdefault(active_command, {})
        started_at = _parse_timestamp(timing.get("started_at"))
        if started_at is None and str(run_state.get("status", "")) == "running":
            started_at = _parse_timestamp(run_state.get("updated_at"))
            if started_at is not None:
                timing["started_at"] = started_at.isoformat(timespec="seconds")
        if started_at is not None:
            duration_seconds = max(0, (datetime.now() - started_at).total_seconds())
            timing["duration_seconds"] = int(round(duration_seconds))
            timing["duration_label"] = _format_duration(duration_seconds)
        timing["status"] = "running"

    return timings


def _step_status(root: Path, step: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
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
        "workspace/tender_requirements.json": status["workspace"]["tender_requirements"],
        "workspace/company_facts.json": status["workspace"]["company_facts"],
        "workspace/score_requirements.json": status["workspace"]["score_requirements"],
        "workspace/score_points.json": status["workspace"]["score_points"],
        "workspace/global_facts.json": status["workspace"]["global_facts"],
        "workspace/template_evidence_map.json": status["workspace"]["template_evidence_map"],
        "workspace/template_quality_report.json": status["workspace"]["template_quality_report"],
        "workspace/outline.json": status["workspace"]["outline"],
        "workspace/jobs/*.json": status["workspace"]["jobs_count"] > 0,
        "workspace/contexts/*_context.json": status["workspace"]["contexts_count"] > 0,
        "workspace/contexts/*_ranked_chunks.json": _count_glob(root / "workspace" / "contexts", "*_ranked_chunks.json") > 0,
        "workspace/chapters/*.md": status["workspace"]["chapters_count"] > 0,
        "workspace/reviews/*_review.json": status["workspace"]["reviews_count"] > 0,
        "workspace/rewrites/*_rewrite_log.json": status["workspace"]["rewrites_count"] > 0,
        "workspace/source_trace_index.json": status["workspace"]["source_trace_index"],
        "workspace/score_coverage_matrix.json": status["workspace"]["score_coverage_matrix"],
        "workspace/summaries/*_summary.json": status["workspace"]["summaries_count"] > 0,
        "workspace/global_review.json": status["workspace"]["global_review"],
        "outputs/final.md": status["outputs"]["final_md"],
        "outputs/final.docx": status["outputs"]["final_docx"],
        "workspace/format_check_report.json": status["workspace"]["format_check_report"],
        "workspace/template_schema.json": status["workspace"]["template_schema"],
        "workspace/template_fill_report.json": status["workspace"]["template_fill_report"],
        "sources/tender": _path_status(root, "sources/tender/*"),
        "sources/company": _path_status(root, "sources/company/*"),
        "sources/template": _path_status(root, "sources/template/*"),
    }

    requirement_paths = [_artifact_path(req) for req in step.get("requires", [])]
    produce_paths = [_artifact_path(prod) for prod in step.get("produces", [])]
    requirements = [bool(key_map.get(req, False)) for req in requirement_paths]
    ready = all(requirements)
    done = all(key_map.get(prod, False) for prod in produce_paths)

    missing_requires = [req for req in requirement_paths if not key_map.get(req, False)]
    source_stale = status["sync"]["source_stale"]
    run_state = status.get("run_state", {}) if isinstance(status.get("run_state"), dict) else {}
    failed_command = _command_for_stage(str(run_state.get("stage", "")))
    active_command = _current_command_from_status(status)
    timing = {}
    if isinstance(status.get("timings"), dict):
        timing = status["timings"].get(step["command"], {}) or {}
    duration_label = str(timing.get("duration_label") or "")
    if step["command"] == "prepare-inputs" and source_stale:
        done = False
        state = "ready"
        message = "sources/ 有新文件，需重新导入"
    elif source_stale and step["command"] != "prepare-inputs" and step["command"] != "init":
        done = False
        state = "blocked"
        message = "请先重新执行导入资料"
    elif status.get("running") and step["command"] == active_command:
        done = False
        state = "ready"
        message = f"已运行 {duration_label}" if duration_label else "运行中"
    elif done:
        state = "done"
        message = f"用时 {duration_label}" if duration_label else "用时 --"
    elif ready:
        state = "ready"
        message = "可执行"
    else:
        state = "blocked"
        message = "等待前置步骤"

    if not done and run_state.get("status") in {"error", "paused"} and step["command"] == failed_command:
        done = False
        state = "ready" if ready else "blocked"
        detail = str(run_state.get("message", "")).strip()
        message = f"上次执行失败，流程已暂停{f'：{detail}' if detail else ''}"

    return {
        **step,
        "ready": ready,
        "done": done,
        "state": state,
        "message": message,
        "missing_requires": missing_requires,
    }


def _step_label(command: str) -> str:
    return WORKFLOW_COMMAND_LABELS.get(command, command)


def _chat_step_payload(status: dict[str, Any], command: str) -> dict[str, Any] | None:
    workflow = status.get("workflow", []) if isinstance(status.get("workflow"), list) else []
    return next((item for item in workflow if isinstance(item, dict) and item.get("command") == command), None)


def _chat_has_any(text: str, normalized: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text or keyword.lower() in normalized for keyword in keywords)


def _chat_reply(root: Path, message: str, selected_command: str = "") -> dict[str, Any]:
    status = api_status()
    text = message.strip()
    normalized = text.lower()
    next_step = status.get("next_step")
    blocked_step = status.get("blocked_step")
    manual_summary = status.get("manual_review_summary", {})
    sources = status.get("sources", {}) if isinstance(status.get("sources"), dict) else {}
    inputs = status.get("inputs", {}) if isinstance(status.get("inputs"), dict) else {}
    outputs = status.get("outputs", {}) if isinstance(status.get("outputs"), dict) else {}
    workspace = status.get("workspace", {}) if isinstance(status.get("workspace"), dict) else {}

    if not text:
        return {
            "reply": "可以直接问我当前状态、下一步、某个节点详情，或者让我继续执行流程。",
            "actions": [],
        }

    if _chat_has_any(text, normalized, ("继续", "下一步", "执行下一步", "继续执行", "next")):
        if isinstance(next_step, dict):
            return {
                "reply": f"当前下一步是“{next_step.get('label', '')}”，命令 `{next_step.get('command', '')}`，可以直接执行。",
                "actions": [
                    {"type": "run_command", "command": str(next_step.get("command", "")), "label": f"执行 {next_step.get('label', '')}"},
                    {"type": "show_step", "command": str(next_step.get("command", "")), "label": "查看节点详情"},
                ],
            }
        return {"reply": "当前没有可继续执行的下一步，可能已经完成或还未创建工作空间。", "actions": []}

    if _chat_has_any(text, normalized, ("人工复核", "未覆盖评分点", "弱证据", "模板缺口")):
        reply = (
            f"当前人工复核待处理总数 {manual_summary.get('total_pending', 0)} 项，"
            f"其中弱证据/缺口 {manual_summary.get('template_evidence_pending', 0)}，"
            f"评分点覆盖 {manual_summary.get('score_coverage_pending', 0)}，"
            f"章节问题 {manual_summary.get('chapter_review_pending', 0)}，"
            f"全文风险 {manual_summary.get('global_review_pending', 0)}。"
        )
        return {
            "reply": reply,
            "actions": [
                {"type": "show_manual_review", "category": "score_coverage", "label": "看未覆盖评分点"},
                {"type": "show_manual_review", "category": "template_evidence", "label": "看弱证据项"},
            ],
        }

    if _chat_has_any(text, normalized, ("评分", "分数", "得分", "score", "覆盖")):
        score_done = bool(workspace.get("score_points"))
        matrix_done = bool(workspace.get("score_coverage_matrix"))
        pending = manual_summary.get("score_coverage_pending", 0)
        reply = (
            f"你关心的是评分覆盖。评分点解析：{'已完成' if score_done else '未完成'}；"
            f"评分覆盖矩阵：{'已生成' if matrix_done else '未生成'}；"
            f"未覆盖评分点待处理 {pending} 项。"
        )
        actions: list[dict[str, str]] = [
            {"type": "show_step", "command": "parse-score", "label": "查看评分解析"},
            {"type": "show_step", "command": "build-score-coverage", "label": "查看覆盖矩阵"},
            {"type": "show_manual_review", "category": "score_coverage", "label": "看未覆盖项"},
        ]
        if isinstance(next_step, dict):
            actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": "执行下一步"})
        return {"reply": reply, "actions": actions}

    if _chat_has_any(text, normalized, ("风险", "质量", "问题", "审核", "复核", "risk", "review")):
        reply = (
            f"你关心的是质量风险。当前人工复核待处理总数 {manual_summary.get('total_pending', 0)} 项，"
            f"章节问题 {manual_summary.get('chapter_review_pending', 0)}，"
            f"全文风险 {manual_summary.get('global_review_pending', 0)}，"
            f"弱证据/模板缺口 {manual_summary.get('template_evidence_pending', 0)}。"
        )
        return {
            "reply": reply,
            "actions": [
                {"type": "show_manual_review", "category": "global_review", "label": "看全文风险"},
                {"type": "show_manual_review", "category": "chapter_review", "label": "看章节问题"},
                {"type": "show_step", "command": "global-review", "label": "查看全文审核"},
            ],
        }

    if _chat_has_any(text, normalized, ("输出", "结果", "word", "docx", "final", "下载", "文件")):
        reply = (
            f"你关心的是最终输出。Markdown：{'已生成' if outputs.get('final_md') else '未生成'}；"
            f"Word：{'已生成' if outputs.get('final_docx') else '未生成'}。"
        )
        actions = [
            {"type": "show_step", "command": "build-md", "label": "查看 Markdown 节点"},
            {"type": "show_step", "command": "build-docx", "label": "查看 Word 节点"},
        ]
        if isinstance(next_step, dict):
            actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": "继续生成"})
        return {"reply": reply, "actions": actions}

    if _chat_has_any(text, normalized, ("资料", "上传", "齐全", "招标", "公司", "模板", "source", "input")):
        tender_count = len(sources.get("tender", [])) if isinstance(sources.get("tender"), list) else 0
        company_count = len(sources.get("company", [])) if isinstance(sources.get("company"), list) else 0
        template_count = len(sources.get("template", [])) if isinstance(sources.get("template"), list) else 0
        reply = (
            f"你关心的是输入资料。招标文件 {tender_count} 个，公司资料 {company_count} 个，Word 模板 {template_count} 个。"
            f" 已导入状态：招标 {'是' if inputs.get('tender_md') else '否'}，"
            f"公司 {'是' if inputs.get('company_md') else '否'}，模板 {'是' if inputs.get('template_docx') else '否'}。"
        )
        return {
            "reply": reply,
            "actions": [
                {"type": "show_step", "command": "prepare-inputs", "label": "查看导入资料"},
                {"type": "run_command", "command": "prepare-inputs", "label": "重新导入资料"},
            ],
        }

    if _chat_has_any(text, normalized, ("状态", "进度", "卡", "为什么", "失败", "暂停", "blocked", "error")):
        run_state = status.get("run_state", {})
        active = status.get("active_run", {})
        reply = (
            f"当前工作空间是 `{active.get('relative_root', active.get('id', ''))}`。"
            f" 运行状态：{run_state.get('status', '未知')}。"
        )
        if isinstance(next_step, dict):
            reply += f" 下一步是“{next_step.get('label', '')}”。"
        if isinstance(blocked_step, dict):
            reply += f" 当前阻塞点是“{blocked_step.get('label', '')}”。"
        if run_state.get("message"):
            reply += f" 说明：{run_state.get('message')}。"
        actions: list[dict[str, str]] = []
        if isinstance(next_step, dict):
            actions.append({"type": "show_step", "command": str(next_step.get("command", "")), "label": "打开下一步详情"})
        return {"reply": reply, "actions": actions}

    for step in WORKFLOW_STEPS:
        command = str(step.get("command", ""))
        label = str(step.get("label", ""))
        if command and command in normalized or label and label in text:
            step_payload = _chat_step_payload(status, command) or {}
            summary = _step_detail_summary(root, command)
            summary_text = "；".join(f"{key}: {value}" for key, value in list(summary.items())[:6]) or "暂无摘要。"
            reply = f"“{label}”当前状态是 {step_payload.get('state', '未知')}。{summary_text}"
            actions = [{"type": "show_step", "command": command, "label": f"打开 {label} 详情"}]
            if step_payload.get("ready") and not step_payload.get("done"):
                actions.append({"type": "run_command", "command": command, "label": f"执行 {label}"})
            return {"reply": reply, "actions": actions}

    if selected_command:
        label = _step_label(selected_command)
        summary = _step_detail_summary(root, selected_command)
        summary_text = "；".join(f"{key}: {value}" for key, value in list(summary.items())[:6]) or "暂无摘要。"
        return {
            "reply": f"当前你正在查看“{label}”。它的关键结果是：{summary_text}",
            "actions": [{"type": "show_step", "command": selected_command, "label": "重新打开节点详情"}],
        }

    return {
        "reply": "我现在更适合回答流程状态、节点详情、人工复核和下一步执行。你可以试试：当前状态怎么样 / 打开解析评分详情 / 继续执行下一步。",
        "actions": [],
    }


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    root = _active_root()
    run_state = _read_run_state(root)
    project_profile = load_project_profile(root)
    review_summary = manual_review_summary(root)
    selected_running = RUNNING and _same_path(CURRENT_RUN_ROOT, root)
    run_state_stage = str(run_state.get("stage", ""))
    run_state_command = _command_for_stage(run_state_stage)
    run_state_status = str(run_state.get("status", ""))
    run_state_message = str(run_state.get("message", ""))
    stale_run_error = run_state_status in {"error", "paused"} and _step_outputs_present(root, run_state_command)
    if stale_run_error:
        run_state_status = "progress"
        run_state_message = ""

    source_latest = max(
        _latest_mtime_in_dir(root / "sources" / "tender"),
        _latest_mtime_in_dir(root / "sources" / "company"),
        _latest_mtime_in_dir(root / "sources" / "template"),
    )
    imported_latest = _latest_mtime(
        [
            root / "inputs" / "tender.md",
            root / "inputs" / "score.md",
            root / "inputs" / "company.md",
            root / "inputs" / "template.docx",
            root / "workspace" / "imported" / "tender_raw.md",
            root / "workspace" / "imported" / "tender_blocks.json",
            root / "workspace" / "imported" / "tender_classified_blocks.json",
            root / "workspace" / "imported" / "tender_classification_report.json",
            root / "workspace" / "imported" / "tender_other.md",
        ]
    )
    source_stale = bool(source_latest and imported_latest and source_latest > imported_latest)

    status = {
        "inputs": {
            "tender_md": _exists(root / "inputs" / "tender.md"),
            "company_md": _exists(root / "inputs" / "company.md"),
            "score_md": _exists(root / "inputs" / "score.md"),
            "template_docx": _exists(root / "inputs" / "template.docx"),
        },
        "sources": {
            "tender": _list_source_files("tender"),
            "company": _list_source_files("company"),
            "template": _list_source_files("template"),
        },
        "imported": {
            "tender_raw": _exists(root / "workspace" / "imported" / "tender_raw.md"),
            "tender_blocks": _exists(root / "workspace" / "imported" / "tender_blocks.json"),
            "tender_classified_blocks": _exists(root / "workspace" / "imported" / "tender_classified_blocks.json"),
            "tender_classification_report": _exists(root / "workspace" / "imported" / "tender_classification_report.json"),
            "tender_other": _exists(root / "workspace" / "imported" / "tender_other.md"),
        },
        "chunks": {
            "tender_chunks": _exists(root / "workspace" / "chunks" / "tender_chunks.json"),
            "company_chunks": _exists(root / "workspace" / "chunks" / "company_chunks.json"),
        },
        "workspace": {
            "tender_requirements": _exists(root / "workspace" / "tender_requirements.json"),
            "company_facts": _exists(root / "workspace" / "company_facts.json"),
            "score_requirements": _exists(root / "workspace" / "score_requirements.json"),
            "score_points": _exists(root / "workspace" / "score_points.json"),
            "global_facts": _exists(root / "workspace" / "global_facts.json"),
            "template_evidence_map": _exists(root / "workspace" / "template_evidence_map.json"),
            "template_quality_report": _exists(root / "workspace" / "template_quality_report.json"),
            "outline": _exists(root / "workspace" / "outline.json"),
            "jobs_count": _count_glob(root / "workspace" / "jobs", "*.json"),
            "contexts_count": _count_glob(root / "workspace" / "contexts", "*_context.json"),
            "chapters_count": _count_glob(root / "workspace" / "chapters", "*.md"),
            "reviews_count": _count_glob(root / "workspace" / "reviews", "*_review.json"),
            "source_trace_index": _exists(root / "workspace" / "source_trace_index.json"),
            "score_coverage_matrix": _exists(root / "workspace" / "score_coverage_matrix.json"),
            "format_check_report": _exists(root / "workspace" / "format_check_report.json"),
            "template_schema": _exists(root / "workspace" / "template_schema.json"),
            "template_fill_report": _exists(root / "workspace" / "template_fill_report.json"),
            "summaries_count": _count_glob(root / "workspace" / "summaries", "*_summary.json"),
            "global_review": _exists(root / "workspace" / "global_review.json"),
            "rewrites_count": _count_glob(root / "workspace" / "rewrites", "*_rewrite_log.json"),
        },
        "outputs": {
            "final_md": _exists(root / "outputs" / "final.md"),
            "final_docx": _exists(root / "outputs" / "final.docx"),
        },
        "sync": {
            "source_stale": source_stale,
            "source_latest": source_latest,
            "imported_latest": imported_latest,
        },
        "running": selected_running,
        "current_task": CURRENT_TASK if selected_running else "",
        "global_running": RUNNING,
        "running_run": {
            "id": CURRENT_RUN_ID,
            "root": str(CURRENT_RUN_ROOT) if CURRENT_RUN_ROOT else "",
            "relative_root": str(CURRENT_RUN_ROOT.relative_to(ROOT)) if CURRENT_RUN_ROOT and CURRENT_RUN_ROOT.is_relative_to(ROOT) else (str(CURRENT_RUN_ROOT) if CURRENT_RUN_ROOT else ""),
            "command": CURRENT_TASK,
        },
        "active_run": _active_run_payload(),
        "run_state": {
            "stage": run_state.get("stage", ""),
            "status": run_state_status,
            "message": run_state_message,
            "updated_at": run_state.get("updated_at", ""),
            "summary": run_state.get("summary", {}),
        },
        "project_profile": project_profile,
        "project_profile_choices": project_profile_choices(),
        "manual_review_summary": review_summary,
        "latest_agent_runs": _latest_agent_runs(root),
        "run_metrics": load_stage_metrics(root),
        "run_events_tail": load_run_events(root)[-20:],
    }
    status["timings"] = _workflow_timings(
        root,
        running=selected_running,
        current_task=CURRENT_TASK if selected_running else "",
        run_state=status["run_state"],
    )

    workflow = [_step_status(root, step, status) for step in WORKFLOW_STEPS]
    core_workflow = [step for step in workflow if step["kind"] != "utility"]
    next_step = next(
        (step for step in core_workflow if not step["done"] and step["ready"]),
        None,
    )
    if next_step is None and not any(step["done"] for step in core_workflow):
        next_step = next((step for step in workflow if not step["done"] and step["ready"]), None)
    blocked_step = next((step for step in core_workflow if not step["done"] and not step["ready"]), None)

    return {
        **status,
        "workflow": workflow,
        "next_step": next_step,
        "blocked_step": blocked_step,
    }


@app.get("/api/workflow-step-detail")
def api_workflow_step_detail(command: str = Query(..., min_length=1)) -> JSONResponse:
    root = _active_root()
    step = next((item for item in WORKFLOW_STEPS if item.get("command") == command), None)
    if step is None:
        return JSONResponse({"ok": False, "message": f"未知流程节点: {command}"}, status_code=404)

    status = api_status()
    workflow = status.get("workflow", []) if isinstance(status, dict) else []
    step_status = next((item for item in workflow if isinstance(item, dict) and item.get("command") == command), {})
    timings = status.get("timings", {}) if isinstance(status.get("timings"), dict) else {}
    return JSONResponse(
        {
            "ok": True,
            "step": step_status or step,
            "summary": _step_detail_summary(root, command),
            "details": _step_extra_details(root, command),
            "requires": _artifact_payloads(root, list(step.get("requires", []))),
            "produces": _artifact_payloads(root, list(step.get("produces", []))),
            "timing": timings.get(command, {}),
            "history": _step_history(root, command),
            "stage_metrics": load_stage_metrics(root).get(next((stage_name for stage_name, stage_command in STAGE_TO_COMMAND.items() if stage_command == command), command), {}),
            "agent_runs": _load_agent_runs(
                root,
                stage=next((stage_name for stage_name, stage_command in STAGE_TO_COMMAND.items() if stage_command == command), command),
                limit=12,
            ),
            "budget_hits": _budget_hits_for_command(root, command),
            "prompt_summary": _stage_prompt_summary(root, command),
            "manual_review_summary": manual_review_summary(root),
            "project_profile": load_project_profile(root),
            "run_root": str(root),
        }
    )


@app.post("/api/chat")
async def api_chat(request: Request) -> JSONResponse:
    root = _active_root()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    message = str(body.get("message", "")).strip()
    selected_command = str(body.get("selected_command", "")).strip()
    payload = _chat_reply(root, message, selected_command=selected_command)
    return JSONResponse({"ok": True, **payload})


@app.get("/api/manual-review/summary")
def api_manual_review_summary() -> JSONResponse:
    root = _active_root()
    return JSONResponse({"ok": True, "summary": manual_review_summary(root)})


@app.get("/api/manual-review/items")
def api_manual_review_items(category: str = Query(..., min_length=1)) -> JSONResponse:
    root = _active_root()
    return JSONResponse({"ok": True, "category": category, "items": manual_review_items(root, category)})


@app.post("/api/manual-review/update")
async def api_manual_review_update(request: Request) -> JSONResponse:
    root = _active_root()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    category = str(body.get("category", "")).strip()
    payload = body.get("payload")
    if not category or not isinstance(payload, dict):
        return JSONResponse({"ok": False, "message": "缺少 category 或 payload。"}, status_code=400)
    try:
        result = apply_manual_review_update(root, category, payload)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "result": result, "summary": manual_review_summary(root)})


@app.get("/api/agent-runs")
def api_agent_runs(
    stage: str = Query("", min_length=0),
    chapter_id: str = Query("", min_length=0),
    agent_name: str = Query("", min_length=0),
) -> JSONResponse:
    root = _active_root()
    return JSONResponse(
        {
            "ok": True,
            "items": _load_agent_runs(root, stage=stage, chapter_id=chapter_id, agent_name=agent_name, limit=100),
        }
    )


@app.get("/api/project-profile")
def api_project_profile() -> JSONResponse:
    root = _active_root()
    return JSONResponse({"ok": True, "profile": load_project_profile(root), "choices": project_profile_choices()})


@app.post("/api/project-profile")
async def api_set_project_profile(request: Request) -> JSONResponse:
    root = _active_root()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    path = save_project_profile(root, str(body.get("project_type", "")).strip())
    return JSONResponse({"ok": True, "profile": load_project_profile(root), "path": _safe_relative(root, path)})


@app.get("/api/file-preview")
def api_file_preview(path: str = Query(..., min_length=1)) -> JSONResponse:
    root = _active_root().resolve()
    relative = path.strip().replace("\\", "/")
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return JSONResponse({"ok": False, "message": "无效文件路径。"}, status_code=400)

    if "*" in relative:
        directory_text, pattern = relative.rsplit("/", 1)
        directory = (root / directory_text).resolve()
        if not directory.is_relative_to(root) or not directory.exists() or not directory.is_dir():
            return JSONResponse({"ok": False, "message": f"目录不存在: {directory_text}"}, status_code=404)
        files = sorted(path for path in directory.glob(pattern) if path.is_file())
        return JSONResponse(
            {
                "ok": True,
                "path": relative,
                "kind": "list",
                "items": [
                    {
                        "name": item.name,
                        "path": str(item.relative_to(root)).replace("\\", "/"),
                        "size": item.stat().st_size,
                        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.stat().st_mtime)),
                    }
                    for item in files[:200]
                ],
                "total": len(files),
            }
        )

    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.exists() or not target.is_file():
        return JSONResponse({"ok": False, "message": f"文件不存在: {relative}"}, status_code=404)

    suffix = target.suffix.lower()
    metadata = {
        "size": target.stat().st_size,
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(target.stat().st_mtime)),
    }
    if suffix in {".json", ".md", ".txt", ".log", ".jsonl", ".csv"}:
        text = target.read_text(encoding="utf-8", errors="replace")
        kind = "json" if suffix == ".json" else "text"
        if suffix == ".json":
            loaded = _read_json_file(target)
            if loaded is not None:
                text = json.dumps(loaded, ensure_ascii=False, indent=2)
        truncated = len(text) > 30000
        return JSONResponse(
            {
                "ok": True,
                "path": relative,
                "kind": kind,
                "metadata": metadata,
                "content": text[:30000],
                "truncated": truncated,
            }
        )

    if suffix == ".docx":
        try:
            from docx import Document

            document = Document(str(target))
            blocks: list[dict[str, Any]] = []
            for paragraph in document.paragraphs:
                content = paragraph.text.strip()
                if content:
                    blocks.append({"type": "paragraph", "text": content})
            for table_index, table in enumerate(document.tables, start=1):
                rows: list[list[str]] = []
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    blocks.append({"type": "table", "index": table_index, "rows": rows})
            return JSONResponse(
                {
                    "ok": True,
                    "path": relative,
                    "kind": "docx",
                    "metadata": metadata,
                    "blocks": blocks[:300],
                    "truncated": len(blocks) > 300,
                }
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"Word 预览失败: {exc}",
                    "path": relative,
                    "metadata": metadata,
                },
                status_code=500,
            )

    return JSONResponse(
        {
            "ok": True,
            "path": relative,
            "kind": "binary",
            "metadata": metadata,
            "message": "该文件类型不支持内嵌文本预览，请使用下载或本地 Word/PDF 工具查看。",
        }
    )


# ---------------------------------------------------------------
#  Command execution
# ---------------------------------------------------------------

COMMANDS: dict[str, list[str]] = {
    "init": [],
    "init-demo": [],
    "prepare-inputs": [],
    "analyze-template": [],
    "split-docs": [],
    "parse-score": [],
    "extract-facts": [],
    "build-template-evidence": [],
    "generate-outline": [],
    "plan-jobs": [],
    "select-context-all": [],
    "write-all": ["--workers", "2"],
    "review-fix-all": [],
    "build-source-trace": [],
    "build-score-coverage": [],
    "summarize-all": [],
    "global-review": [],
    "build-md": [],
    "build-docx": [],
    "check-format": [],
    "validate": [],
    "run": ["--workers", "2"],
    "graph-run": ["--workers", "2"],
}


def _run_sync(command: str, run_id: str, run_root: Path) -> int:
    global RUNNING, CURRENT_TASK, CURRENT_PROCESS, CURRENT_RUN_ID, CURRENT_RUN_ROOT, PAUSE_REQUESTED
    RUNNING = True
    CURRENT_TASK = command
    CURRENT_PROCESS = None
    CURRENT_RUN_ID = run_id
    CURRENT_RUN_ROOT = run_root
    PAUSE_REQUESTED = False

    args = ["src/main.py", command, *COMMANDS.get(command, [])]
    _append_log(f"--- [{time.strftime('%H:%M:%S')}] 开始: python {' '.join(args)} ---")
    _append_log(f"[运行目录] {run_root}")
    record_state = command not in {"validate", "init-demo"}
    if record_state:
        save_run_state(
            run_root,
            {"root_dir": str(run_root), "current_command": command},
            stage=command,
            status="running",
            message=f"开始执行: {COMMANDS.get(command, [])}",
        )

    try:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["BID_AGENT_ROOT"] = str(run_root)
        env["BID_AGENT_CONFIG_ROOT"] = str(ROOT)
        process = subprocess.Popen(
            [sys.executable, *args],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=1,
        )
        CURRENT_PROCESS = process
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

    was_paused = PAUSE_REQUESTED
    if exit_code != 0 and was_paused:
        _append_log(f"[暂停] {command} 已暂停，可从断点继续。")
    elif exit_code != 0:
        _append_log(f"[错误] 流程已停止: {command} 执行失败，请查看上方报错。")
    if record_state:
        state_status = "paused" if exit_code != 0 and was_paused else ("ok" if exit_code == 0 else "error")
        state_message = (
            f"{command} 已暂停，可从断点继续"
            if state_status == "paused"
            else (f"{command} 执行完成" if exit_code == 0 else f"{command} 执行失败，exit_code={exit_code}")
        )
        save_run_state(
            run_root,
            {"root_dir": str(run_root), "current_command": command},
            stage=command,
            status=state_status,
            message=state_message,
        )
    _append_log(f"--- [{time.strftime('%H:%M:%S')}] 完成: exit_code={exit_code} ---")
    RUNNING = False
    CURRENT_TASK = ""
    CURRENT_PROCESS = None
    CURRENT_RUN_ID = ""
    CURRENT_RUN_ROOT = None
    PAUSE_REQUESTED = False
    return exit_code


def _artifact_present(root: Path, artifact: Any) -> bool:
    artifact_path = _artifact_path(artifact)
    artifact_kind = _artifact_kind(artifact)
    if artifact_kind == "virtual":
        return True
    if artifact_kind == "glob":
        relative = Path(artifact_path)
        directory = root / relative.parent
        return directory.exists() and any(directory.glob(relative.name))
    target = root / artifact_path
    if target.is_dir():
        return any(target.iterdir())
    return target.exists() and target.is_file() and target.stat().st_size > 0


def _step_by_command(command: str) -> dict[str, Any] | None:
    return next((step for step in WORKFLOW_STEPS if step["command"] == command), None)


def _step_outputs_present(root: Path, command: str) -> bool:
    step = _step_by_command(command)
    if not step:
        return False
    return all(_artifact_present(root, artifact) for artifact in step.get("produces", []))


def _latest_tree_mtime(root: Path) -> float:
    latest = root.stat().st_mtime if root.exists() else 0.0
    for relative in [
        "workspace/run_state.json",
        "workspace/run_state_history.jsonl",
        "workspace/format_check_report.json",
        "outputs/final.md",
        "outputs/final.docx",
    ]:
        path = root / relative
        if path.exists():
            latest = max(latest, path.stat().st_mtime)
    return latest


def _run_progress_summary(run_root: Path) -> dict[str, Any]:
    core_steps = [step for step in WORKFLOW_STEPS if step.get("kind") != "utility"]
    done_count = sum(
        1
        for step in core_steps
        if all(_artifact_present(run_root, artifact) for artifact in step.get("produces", []))
    )
    run_state = _read_run_state(run_root)
    failed_command = _command_for_stage(str(run_state.get("stage", "")))
    failed_step = next((step for step in core_steps if step["command"] == failed_command), None)
    failed_outputs_present = _step_outputs_present(run_root, failed_command)
    latest = _latest_tree_mtime(run_root)
    state = str(run_state.get("status", "")).strip()
    is_running_run = RUNNING and _same_path(CURRENT_RUN_ROOT, run_root)
    effective_state = "" if state in {"error", "paused"} and failed_outputs_present else state
    if is_running_run:
        display_state = "running"
        running_step = next((step for step in core_steps if step["command"] == CURRENT_TASK), None)
        display_label = f"运行中 {running_step['label']}" if running_step else "运行中"
    elif failed_step and state in {"error", "paused"} and not failed_outputs_present:
        display_state = "error"
        display_label = f"暂停于 {failed_step['label']}"
    elif done_count >= len(core_steps) and core_steps:
        display_state = "done"
        display_label = "完整流程已完成"
    elif run_state.get("updated_at"):
        display_state = effective_state or "progress"
        display_label = "已开始"
    else:
        display_state = "new"
        display_label = "未开始"
    return {
        "done": done_count,
        "total": len(core_steps),
        "status": display_state,
        "status_label": display_label,
        "stage": run_state.get("stage", ""),
        "message": "" if failed_outputs_present and state in {"error", "paused"} else run_state.get("message", ""),
        "updated_at": run_state.get("updated_at", ""),
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest)) if latest else "",
    }


@app.post("/api/start-run")
async def api_start_run(request: Request) -> JSONResponse:
    global ACTIVE_RUN_ID, ACTIVE_RUN_ROOT

    if RUNNING:
        return JSONResponse(
            {"ok": False, "message": "当前已有任务正在运行，请等待完成。"},
            status_code=409,
        )

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    run_name = str(body.get("name", "")).strip()
    project_type = str(body.get("project_type", "")).strip()
    if not run_name:
        return JSONResponse(
            {"ok": False, "message": "请先设置工作空间名称。"},
            status_code=400,
        )

    try:
        run_id, run_root = _create_run_workspace(run_name, project_type)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"创建运行工作空间失败: {exc}"}, status_code=500)

    ACTIVE_RUN_ID = run_id
    ACTIVE_RUN_ROOT = run_root
    ACTIVE_RUN_FILE.write_text(run_id, encoding="utf-8")
    _append_log(f"[运行] 已创建独立工作空间: {run_root.relative_to(ROOT)}")
    return JSONResponse({"ok": True, "run": _active_run_payload()})


@app.get("/api/runs")
def api_runs() -> JSONResponse:
    _load_active_run_from_disk()
    runs: list[dict[str, Any]] = []
    if RUNS_DIR.exists():
        run_dirs = [path for path in RUNS_DIR.iterdir() if path.is_dir()]
        for run_root in sorted(run_dirs, key=_latest_tree_mtime, reverse=True):
            progress = _run_progress_summary(run_root)
            runs.append(
                {
                    "id": run_root.name,
                    "root": str(run_root),
                    "relative_root": str(run_root.relative_to(ROOT)) if run_root.is_relative_to(ROOT) else str(run_root),
                    "active": run_root == ACTIVE_RUN_ROOT,
                    "progress": progress,
                }
            )
    return JSONResponse({"ok": True, "active_run_id": ACTIVE_RUN_ID, "runs": runs})


@app.post("/api/select-run")
async def api_select_run(request: Request) -> JSONResponse:
    global ACTIVE_RUN_ID, ACTIVE_RUN_ROOT
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)

    run_id = str(body.get("run_id", "")).strip()
    if not run_id or Path(run_id).name != run_id:
        return JSONResponse({"ok": False, "message": "无效工作空间。"}, status_code=400)

    run_root = (RUNS_DIR / run_id).resolve()
    runs_root = RUNS_DIR.resolve()
    if not run_root.is_relative_to(runs_root) or not run_root.exists() or not run_root.is_dir():
        return JSONResponse({"ok": False, "message": f"工作空间不存在: {run_id}"}, status_code=404)

    ACTIVE_RUN_ID = run_id
    ACTIVE_RUN_ROOT = run_root
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_RUN_FILE.write_text(run_id, encoding="utf-8")
    _append_log(f"[工作空间] 已切换到: {run_root.relative_to(ROOT)}")
    return JSONResponse({"ok": True, "run": _active_run_payload(), "progress": _run_progress_summary(run_root)})


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
    if command not in {"validate", "init-demo"} and ACTIVE_RUN_ROOT is None:
        return JSONResponse(
            {"ok": False, "message": "请先点击“开始生成”，创建本次运行工作空间后再执行流程命令。"},
            status_code=409,
        )
    if command not in {"validate", "init-demo"} and str(body.get("run_id", "")).strip() != ACTIVE_RUN_ID:
        return JSONResponse(
            {"ok": False, "message": "运行工作空间已变化，请刷新页面后重新开始。"},
            status_code=409,
        )

    run_id = ACTIVE_RUN_ID
    run_root = _active_root()
    threading.Thread(target=_run_sync, args=(command, run_id, run_root), daemon=True).start()
    return JSONResponse({"ok": True, "message": f"命令已启动: {command}"})


@app.post("/api/pause-run")
def api_pause_run() -> JSONResponse:
    global PAUSE_REQUESTED
    if not RUNNING:
        return JSONResponse({"ok": True, "message": "当前没有正在运行的任务。"})

    PAUSE_REQUESTED = True
    process = CURRENT_PROCESS
    _append_log(f"[暂停] 正在停止当前任务: {CURRENT_TASK}")
    if process and process.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.terminate()
        except Exception as exc:
            return JSONResponse({"ok": False, "message": f"暂停失败: {exc}"}, status_code=500)
    return JSONResponse({"ok": True, "message": "已发送暂停指令。"})


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
        last_event = 0
        while True:
            if await request.is_disconnected():
                break
            while last < len(LOG_LINES):
                yield f"data: {json.dumps({'type': 'log', 'line': LOG_LINES[last]}, ensure_ascii=False)}\n\n"
                last += 1
            events = load_run_events(_active_root())
            while last_event < len(events):
                event = events[last_event]
                event_line = f"[{event.get('stage', '')}] {event.get('event_type', '')} {event.get('message', '')}".strip()
                yield f"data: {json.dumps({'type': 'run_event', 'event': event, 'line': event_line}, ensure_ascii=False)}\n\n"
                last_event += 1
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
        active_root = _active_root()
        if active_root != ROOT and not RUNNING:
            active_dest = active_root / "sources" / category / f.filename
            active_dest.parent.mkdir(parents=True, exist_ok=True)
            active_dest.write_bytes(content)
        saved.append(f.filename)
        _append_log(f"[上传] {category} → {f.filename}")

    return JSONResponse({"ok": True, "saved": saved, "count": len(saved)})


# ---------------------------------------------------------------
#  Download
# ---------------------------------------------------------------

@app.get("/api/final-md/lines")
def api_final_md_lines() -> JSONResponse:
    root = _active_root()
    path = root / "outputs" / "final.md"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md。"}, status_code=404)
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    return JSONResponse(
        {
            "ok": True,
            "path": "outputs/final.md",
            "line_count": len(lines),
            "lines": [{"number": index + 1, "text": line} for index, line in enumerate(lines)],
            "docx_exists": (root / "outputs" / "final.docx").exists(),
        }
    )


def _save_final_md_line_edit(root: Path, line_number: int, new_text: str, instruction: str, source: str) -> dict[str, Any]:
    path = root / "outputs" / "final.md"
    original = path.read_text(encoding="utf-8", errors="ignore")
    lines = original.splitlines()
    if line_number > len(lines):
        raise ValueError(f"行号超出范围：{line_number}")

    old_text = lines[line_number - 1]
    lines[line_number - 1] = new_text
    backup_dir = root / "workspace" / "manual_line_edits"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"final_md_before_line_{line_number}_{stamp}.md"
    backup_path.write_text(original, encoding="utf-8")
    path.write_text("\n".join(lines) + ("\n" if original.endswith("\n") else ""), encoding="utf-8")

    review = {
        "line_number": line_number,
        "old_text": old_text,
        "new_text": new_text,
        "instruction": instruction,
        "source": source,
        "review_status": "accepted_for_docx_rebuild",
        "review_notes": [
            "已完成选中行改写。",
            "已记录修改前备份。",
            "将后台重新执行 build-docx 生成 Word。",
        ],
        "backup_path": str(backup_path.relative_to(root)).replace("\\", "/"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    review_path = backup_dir / f"line_{line_number}_{stamp}_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    threading.Thread(target=_run_sync, args=("build-docx", ACTIVE_RUN_ID, root), daemon=True).start()
    return {"review": review, "review_path": str(review_path.relative_to(root)).replace("\\", "/")}


@app.post("/api/final-md/line-edit")
async def api_final_md_line_edit(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再修改。"}, status_code=409)
    path = root / "outputs" / "final.md"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md。"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)

    try:
        line_number = int(body.get("line_number", 0))
    except Exception:
        line_number = 0
    new_text = str(body.get("new_text", "")).rstrip("\n\r")
    instruction = str(body.get("instruction", "")).strip()
    if line_number < 1:
        return JSONResponse({"ok": False, "message": "请选择要修改的行。"}, status_code=400)
    if not new_text.strip():
        return JSONResponse({"ok": False, "message": "请填写修改后的内容。"}, status_code=400)

    try:
        result = _save_final_md_line_edit(root, line_number, new_text, instruction, "manual")
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    _append_log(f"[人工改写] final.md 第 {line_number} 行已修改，开始重新生成 Word。")
    return JSONResponse(
        {
            "ok": True,
            "message": "已保存该行修改，并开始重新生成 Word。",
            **result,
        }
    )


@app.post("/api/final-md/line-regenerate")
async def api_final_md_line_regenerate(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再重生成。"}, status_code=409)
    path = root / "outputs" / "final.md"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md。"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)

    try:
        line_number = int(body.get("line_number", 0))
    except Exception:
        line_number = 0
    instruction = str(body.get("instruction", "")).strip()
    if line_number < 1:
        return JSONResponse({"ok": False, "message": "请选择要重生成的行。"}, status_code=400)
    if not instruction:
        return JSONResponse({"ok": False, "message": "请填写 AI 生成要求。"}, status_code=400)

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if line_number > len(lines):
        return JSONResponse({"ok": False, "message": f"行号超出范围：{line_number}"}, status_code=400)
    old_text = lines[line_number - 1]
    start = max(0, line_number - 6)
    end = min(len(lines), line_number + 5)
    context = "\n".join(f"{index + 1}: {line}" for index, line in enumerate(lines[start:end], start=start))

    try:
        from llm_client import chat

        generated = chat(
            [
                {"role": "system", "content": "你是标书 Word 定稿改写子 agent。只输出改写后的单行内容，不要解释，不要添加 Markdown 代码块。"},
                {
                    "role": "user",
                    "content": (
                        f"请按用户要求重生成 final.md 第 {line_number} 行。\n"
                        f"用户要求：{instruction}\n\n"
                        f"当前行：{old_text}\n\n"
                        f"上下文：\n{context}"
                    ),
                },
            ],
            temperature=0.3,
        ).strip()
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"AI 重生成失败: {exc}"}, status_code=500)

    new_text = generated.strip().strip("`").strip()
    if not new_text:
        return JSONResponse({"ok": False, "message": "AI 未生成有效内容。"}, status_code=500)
    _PENDING_LINE_EDITS[root.resolve()] = {
        "line_number": line_number,
        "old_text": old_text,
        "new_text": new_text,
        "instruction": instruction,
        "source": "ai_regenerate",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _append_log(f"[AI改写] 已为 final.md 第 {line_number} 行生成预览，等待用户确认后再保存。")
    return JSONResponse(
        {
            "ok": True,
            "message": "AI 已生成预览，请确认后再保存并重建 Word。",
            "line_number": line_number,
            "old_text": old_text,
            "generated_text": new_text,
            "instruction": instruction,
        }
    )


@app.post("/api/final-md/line-confirm")
async def api_final_md_line_confirm(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再确认。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = root.resolve()
    pending = _PENDING_LINE_EDITS.get(key)
    if not pending:
        return JSONResponse({"ok": False, "message": "没有待确认的修改预览。"}, status_code=404)
    new_text = str(body.get("new_text", pending.get("new_text", ""))).rstrip("\n\r") or pending["new_text"]
    try:
        result = _save_final_md_line_edit(root, pending["line_number"], new_text, pending.get("instruction", ""), pending.get("source", "ai_regenerate"))
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    _PENDING_LINE_EDITS.pop(key, None)
    _append_log(f"[AI改写] final.md 第 {pending['line_number']} 行已确认保存，开始重新生成 Word。")
    return JSONResponse({"ok": True, "message": "已确认保存，并开始重新生成 Word。", **result})


@app.post("/api/final-md/line-discard")
async def api_final_md_line_discard() -> JSONResponse:
    root = _active_root()
    removed = _PENDING_LINE_EDITS.pop(root.resolve(), None)
    if removed:
        _append_log(f"[AI改写] final.md 第 {removed['line_number']} 行的预览修改已放弃。")
    return JSONResponse({"ok": True, "discarded": bool(removed)})


@app.get("/api/final-md/pending")
def api_final_md_pending() -> JSONResponse:
    root = _active_root()
    pending = _PENDING_LINE_EDITS.get(root.resolve())
    return JSONResponse({"ok": True, "pending": pending})

# ---------------------------------------------------------------
#  Final doc WYSIWYG editor (block-level render / edit / AI rewrite)
# ---------------------------------------------------------------

_PENDING_DOC_EDIT: dict[Path, dict[str, Any]] = {}


def triggerDocRefresh() -> None:
    pass


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^- \s*(.*)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.*)$")
_TBL_LINE_RE = re.compile(r"^\|.*\|\s*$")
_TBL_SEP_RE = re.compile(r"^:?-{3,}:?$")


def _split_md_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_md_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_md_table_separator(cells: list[str]) -> bool:
    return all(bool(_TBL_SEP_RE.fullmatch(cell.strip())) for cell in cells)


def _parse_final_md_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    i = 0
    block_seq = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            blocks.append({
                "block_id": f"b{block_seq}",
                "type": "heading",
                "level": level,
                "text": heading.group(2).strip(),
                "raw": line,
                "start_line": i + 1,
                "end_line": i + 1,
            })
            block_seq += 1
            i += 1
            continue

        if _is_md_table_line(line) and i + 1 < len(lines) and _is_md_table_line(lines[i + 1]):
            header = _split_md_table_row(line)
            separator = _split_md_table_row(lines[i + 1])
            if _is_md_table_separator(separator):
                rows = [header]
                start = i + 1
                i += 2
                while i < len(lines) and _is_md_table_line(lines[i]):
                    rows.append(_split_md_table_row(lines[i]))
                    i += 1
                blocks.append({
                    "block_id": f"b{block_seq}",
                    "type": "table",
                    "header": header,
                    "rows": rows,
                    "raw": "\n".join(lines[start - 1:i]),
                    "start_line": start,
                    "end_line": i,
                })
                block_seq += 1
                continue

        bullet = _BULLET_RE.match(stripped)
        if bullet:
            blocks.append({
                "block_id": f"b{block_seq}",
                "type": "bullet",
                "text": bullet.group(1).strip(),
                "raw": line,
                "start_line": i + 1,
                "end_line": i + 1,
            })
            block_seq += 1
            i += 1
            continue

        numbered = _NUMBERED_RE.match(stripped)
        if numbered:
            blocks.append({
                "block_id": f"b{block_seq}",
                "type": "numbered",
                "text": numbered.group(1).strip(),
                "raw": line,
                "start_line": i + 1,
                "end_line": i + 1,
            })
            block_seq += 1
            i += 1
            continue

        paragraph_lines = [stripped]
        start = i + 1
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith("#") or _is_md_table_line(nxt):
                break
            if _BULLET_RE.match(nxt) or _NUMBERED_RE.match(nxt):
                break
            paragraph_lines.append(nxt)
            i += 1
        blocks.append({
            "block_id": f"b{block_seq}",
            "type": "paragraph",
            "text": " ".join(paragraph_lines),
            "lines": paragraph_lines,
            "raw": "\n".join(paragraph_lines),
            "start_line": start,
            "end_line": i,
        })
        block_seq += 1

    return blocks


def _final_md_path(root: Path) -> Path:
    return root / "outputs" / "final.md"


def _final_docx_path(root: Path) -> Path:
    return root / "outputs" / "final.docx"


def _trigger_build_docx_async(root: Path) -> None:
    threading.Thread(target=_run_sync, args=("build-docx", ACTIVE_RUN_ID, root), daemon=True).start()


def _backup_final_md(root: Path, original: str, label: str) -> Path:
    backup_dir = root / "workspace" / "manual_line_edits"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"final_md_before_{label}_{stamp}.md"
    backup_path.write_text(original, encoding="utf-8")
    return backup_path


def _block_new_raw(block: dict[str, Any], new_text: str) -> str:
    btype = block.get("type")
    if btype == "heading":
        level = int(block.get("level", 2) or 2)
        return f"{'#' * max(1, min(6, level))} {new_text.strip()}"
    if btype == "bullet":
        return f"- {new_text.strip()}"
    if btype == "numbered":
        return f"1. {new_text.strip()}"
    if btype == "table":
        return new_text.strip("\n")
    return new_text.strip("\n")


def _replace_final_md_block(root: Path, block_id: str, new_text: str, instruction: str, source: str) -> dict[str, Any]:
    path = _final_md_path(root)
    if not path.exists():
        raise FileNotFoundError("final.md 不存在，请先执行 build-md。")
    original = path.read_text(encoding="utf-8", errors="ignore")
    blocks = _parse_final_md_blocks(original)
    target = next((b for b in blocks if b.get("block_id") == block_id), None)
    if target is None:
        raise ValueError(f"找不到 block_id={block_id}")

    lines = original.splitlines()
    new_raw = _block_new_raw(target, new_text)
    new_lines = new_raw.splitlines()
    start_line = int(target.get("start_line", 0))
    end_line = int(target.get("end_line", 0))
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError("行号越界，无法替换。")

    before = lines[: start_line - 1]
    after = lines[end_line:]
    merged = before + new_lines + after
    new_text_full = "\n".join(merged) + ("\n" if original.endswith("\n") else "")

    backup_path = _backup_final_md(root, original, f"block_{block_id}")
    path.write_text(new_text_full, encoding="utf-8")

    review = {
        "block_id": block_id,
        "block_type": target.get("type", ""),
        "old_text": target.get("raw", ""),
        "new_text": new_raw,
        "instruction": instruction,
        "source": source,
        "review_status": "accepted_for_docx_rebuild",
        "backup_path": str(backup_path.relative_to(root)).replace("\\", "/"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    backup_dir = root / "workspace" / "manual_line_edits"
    review_path = backup_dir / f"block_{block_id}_{time.strftime('%Y%m%d_%H%M%S')}_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    _trigger_build_docx_async(root)
    return {"review": review, "review_path": str(review_path.relative_to(root)).replace("\\", "/")}


def _overwrite_final_md(root: Path, new_md: str, instruction: str, source: str) -> dict[str, Any]:
    path = _final_md_path(root)
    if not path.exists():
        raise FileNotFoundError("final.md 不存在，请先执行 build-md。")
    original = path.read_text(encoding="utf-8", errors="ignore")
    backup_path = _backup_final_md(root, original, "chat_edit")
    new_clean = new_md.strip("\n") + "\n"
    path.write_text(new_clean, encoding="utf-8")
    review = {
        "instruction": instruction,
        "source": source,
        "old_md_length": len(original),
        "new_md_length": len(new_clean),
        "backup_path": str(backup_path.relative_to(root)).replace("\\", "/"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    backup_dir = root / "workspace" / "manual_line_edits"
    review_path = backup_dir / f"chat_edit_{time.strftime('%Y%m%d_%H%M%S')}_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    _trigger_build_docx_async(root)
    return {"review": review, "review_path": str(review_path.relative_to(root)).replace("\\", "/")}


@app.get("/api/final-doc/render")
def api_final_doc_render() -> JSONResponse:
    root = _active_root()
    path = _final_md_path(root)
    if not path.exists():
        return JSONResponse({
            "ok": True,
            "final_md_exists": False,
            "final_docx_exists": _final_docx_path(root).exists(),
            "blocks": [],
            "pending": _PENDING_DOC_EDIT.get(root.resolve()),
        })
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = _parse_final_md_blocks(text)
    return JSONResponse({
        "ok": True,
        "final_md_exists": True,
        "final_docx_exists": _final_docx_path(root).exists(),
        "blocks": blocks,
        "final_md_len": len(text),
        "pending": _PENDING_DOC_EDIT.get(root.resolve()),
    })


@app.post("/api/final-doc/block-edit")
async def api_final_doc_block_edit(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再修改。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    block_id = str(body.get("block_id", "")).strip()
    new_text = str(body.get("new_text", "")).rstrip("\n\r")
    instruction = str(body.get("instruction", "")).strip()
    if not block_id or not new_text.strip():
        return JSONResponse({"ok": False, "message": "缺少 block_id 或 new_text。"}, status_code=400)
    try:
        result = _replace_final_md_block(root, block_id, new_text, instruction, "manual_block_edit")
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    _append_log(f"[WYSIWYG] 块 {block_id} 已修改，开始重新生成 Word。")
    triggerDocRefresh()
    return JSONResponse({"ok": True, "message": "已保存该块修改，并开始重新生成 Word。", **result})


@app.post("/api/final-doc/selection-rewrite")
async def api_final_doc_selection_rewrite(request: Request) -> JSONResponse:
    root = _active_root()
    path = _final_md_path(root)
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md。"}, status_code=404)
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再改写。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    block_id = str(body.get("block_id", "")).strip()
    selected_text = str(body.get("selected_text", "")).strip()
    instruction = str(body.get("instruction", "")).strip()
    if not block_id or not selected_text or not instruction:
        return JSONResponse({"ok": False, "message": "请同时提供 block_id、selected_text 和修改意见。"}, status_code=400)

    original = path.read_text(encoding="utf-8", errors="ignore")
    blocks = _parse_final_md_blocks(original)
    target = next((b for b in blocks if b.get("block_id") == block_id), None)
    if target is None:
        return JSONResponse({"ok": False, "message": f"找不到 block_id={block_id}"}, status_code=400)
    full_text = target.get("text") or target.get("raw", "")
    if selected_text not in full_text:
        return JSONResponse({"ok": False, "message": "选区文本未在该块中找到，无法定位。"}, status_code=400)

    try:
        from llm_client import chat

        generated = chat(
            [
                {"role": "system", "content": "你是标书 Word 定稿改写子 agent。按照用户的批注要求，仅改写用户选中的文字片段，输出该块改写后的完整内容（不要仅输出片段）。保留原有结构和措辞风格，直接输出最终文本，不要解释，不要代码块。"},
                {
                    "role": "user",
                    "content": (
                        f"块的完整文本：\n{full_text}\n\n"
                        f"用户选中的片段：\n{selected_text}\n\n"
                        f"用户的批注要求：{instruction}"
                    ),
                },
            ],
            temperature=0.3,
        ).strip().strip("`").strip()
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"AI 改写失败: {exc}"}, status_code=500)

    if not generated:
        return JSONResponse({"ok": False, "message": "AI 未生成有效内容。"}, status_code=500)

    _PENDING_DOC_EDIT[root.resolve()] = {
        "block_id": block_id,
        "instruction": instruction,
        "selected_text": selected_text,
        "old_text": full_text,
        "new_text": generated,
        "source": "ai_selection_rewrite",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return JSONResponse({
        "ok": True,
        "block_id": block_id,
        "old_text": full_text,
        "new_text": generated,
        "selected_text": selected_text,
        "instruction": instruction,
        "message": "AI 已生成预览，预览效果见中间文档区。",
    })


@app.post("/api/final-doc/selection-apply")
async def api_final_doc_selection_apply(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再确认。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = root.resolve()
    pending = _PENDING_DOC_EDIT.get(key)
    if not pending:
        return JSONResponse({"ok": False, "message": "没有待确认的选区改写预览。"}, status_code=404)
    new_text = str(body.get("new_text", pending.get("new_text", ""))).rstrip("\n\r") or pending["new_text"]
    try:
        result = _replace_final_md_block(root, pending["block_id"], new_text, pending.get("instruction", ""), pending.get("source", "ai_selection_rewrite"))
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    _PENDING_DOC_EDIT.pop(key, None)
    _append_log(f"[WYSIWYG] 块 {pending['block_id']} 已应用 AI 选区改写，开始重新生成 Word。")
    triggerDocRefresh()
    return JSONResponse({"ok": True, "message": "已确认写入，并开始重新生成 Word。", **result})


@app.post("/api/final-doc/selection-discard")
async def api_final_doc_selection_discard() -> JSONResponse:
    root = _active_root()
    removed = _PENDING_DOC_EDIT.pop(root.resolve(), None)
    if removed:
        _append_log(f"[WYSIWYG] 块 {removed['block_id']} 的选区改写预览已放弃。")
    return JSONResponse({"ok": True, "discarded": bool(removed)})


@app.post("/api/final-doc/chat-edit")
async def api_final_doc_chat_edit(request: Request) -> JSONResponse:
    root = _active_root()
    path = _final_md_path(root)
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md。"}, status_code=404)
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再改写。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "请求体必须是 JSON。"}, status_code=400)
    instruction = str(body.get("instruction", "")).strip()
    if not instruction:
        return JSONResponse({"ok": False, "message": "请填写修改意见。"}, status_code=400)

    original = path.read_text(encoding="utf-8", errors="ignore")
    try:
        from llm_client import chat

        generated = chat(
            [
                {"role": "system", "content": "你是标书 Word 全文改写子 agent。按照用户的要求改写整份 Markdown 标书文档。保持 Markdown 结构（# 标题、表格、列表等）不变，直接输出改写后的完整 Markdown，不要解释，不要包裹代码块。"},
                {
                    "role": "user",
                    "content": (
                        f"请按下列要求改写整份标书 final.md：\n{instruction}\n\n"
                        f"原文 Markdown：\n{original}"
                    ),
                },
            ],
            temperature=0.3,
        ).strip()
        if generated.startswith("```"):
            generated = re.sub(r"^```(?:markdown)?\s*\n", "", generated)
            generated = re.sub(r"\n```\s*$", "", generated).strip()
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"AI 改写失败: {exc}"}, status_code=500)
    if not generated.strip():
        return JSONResponse({"ok": False, "message": "AI 未生成有效内容。"}, status_code=500)

    _PENDING_DOC_EDIT[root.resolve()] = {
        "kind": "chat_edit",
        "instruction": instruction,
        "old_md": original,
        "new_md": generated,
        "source": "ai_chat_edit",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return JSONResponse({
        "ok": True,
        "instruction": instruction,
        "new_md": generated,
        "old_md_length": len(original),
        "new_md_length": len(generated),
        "message": "AI 已生成全文改写预览，请确认后再写入并重建 Word。",
    })


@app.post("/api/final-doc/chat-apply")
async def api_final_doc_chat_apply(request: Request) -> JSONResponse:
    root = _active_root()
    if RUNNING:
        return JSONResponse({"ok": False, "message": "当前已有任务正在运行，请稍后再确认。"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = root.resolve()
    pending = _PENDING_DOC_EDIT.get(key)
    instruction = str(body.get("instruction", "")).strip() or (pending.get("instruction", "") if pending else "")
    new_md = str(body.get("new_md", "")).strip()
    if not new_md and pending and pending.get("new_md"):
        new_md = pending["new_md"]
    if not new_md:
        return JSONResponse({"ok": False, "message": "缺少 new_md。"}, status_code=400)
    try:
        result = _overwrite_final_md(root, new_md, instruction, "ai_chat_edit")
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    _PENDING_DOC_EDIT.pop(key, None)
    _append_log("[WYSIWYG] 全文改写已写入，开始重新生成 Word。")
    triggerDocRefresh()
    return JSONResponse({"ok": True, "message": "已写入全文改写，并开始重新生成 Word。", **result})


@app.post("/api/final-doc/chat-discard")
async def api_final_doc_chat_discard() -> JSONResponse:
    root = _active_root()
    key = root.resolve()
    pending = _PENDING_DOC_EDIT.pop(key, None) or {}
    if pending.get("kind") == "chat_edit":
        _append_log("[WYSIWYG] 全文改写预览已放弃。")
        return JSONResponse({"ok": True, "discarded": True})
    return JSONResponse({"ok": True, "discarded": False})


@app.get("/api/final-doc/pending")
def api_final_doc_pending() -> JSONResponse:
    root = _active_root()
    pending = _PENDING_DOC_EDIT.get(root.resolve())
    return JSONResponse({"ok": True, "pending": pending})


@app.get("/api/download/final-md", response_model=None)
def download_final_md() -> FileResponse | JSONResponse:
    path = _active_root() / "outputs" / "final.md"
    if not path.exists():
        return JSONResponse({"ok": False, "message": "final.md 不存在，请先执行 build-md"}, status_code=404)
    return FileResponse(str(path), filename="final.md", media_type="text/markdown")


@app.get("/api/download/final-docx", response_model=None)
def download_final_docx() -> FileResponse | JSONResponse:
    path = _active_root() / "outputs" / "final.docx"
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
    path = _active_root() / "workspace" / "global_review.json"
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

    root = _active_root()
    for sub in ["workspace", "outputs"]:
        target = root / sub
        if target.exists():
            shutil.rmtree(str(target))

    root.joinpath("workspace").mkdir(parents=True, exist_ok=True)
    root.joinpath("outputs").mkdir(parents=True, exist_ok=True)

    _append_log(f"[清空] 已清空 {root} 下的 workspace/ 和 outputs/")
    return JSONResponse({"ok": True, "message": "workspace/ 和 outputs/ 已清空"})


# ---------------------------------------------------------------
#  Startup
# ---------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    _append_log("[系统] 标书 Agent Web 控制台已启动")
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="warning")
