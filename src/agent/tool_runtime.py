from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.tool_registry import get_tool, stage_to_tool_spec
from agent.types import ToolError, ToolResult, ToolSpec
from pipeline_registry import (
    STAGE_SPECS,
    artifact_exists,
    stage_outputs_ready,
    stage_spec_by_command,
    stage_spec_by_id,
)
from utils import project_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fail(
    tool: str,
    args: dict[str, Any],
    started_at: str,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    suggested_tools: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool=tool,
        args=args,
        started_at=started_at,
        ended_at=_now(),
        error=ToolError(
            code=code,
            message=message,
            retryable=retryable,
            suggested_tools=list(suggested_tools or []),
        ),
        summary_for_llm=f"{code}: {message}",
    )


def _validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    schema = spec.params_schema or {}
    if not schema:
        return None
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        extra = sorted(set(args.keys()) - allowed)
        if extra:
            return f"不支持的参数: {', '.join(extra)}"
    properties = schema.get("properties") or {}
    for key, value in args.items():
        prop = properties.get(key)
        if not prop:
            continue
        expected = prop.get("type")
        if expected == "boolean" and not isinstance(value, bool):
            return f"参数 {key} 应为 boolean"
        if expected == "integer" and not isinstance(value, int):
            return f"参数 {key} 应为 integer"
        if expected == "string" and not isinstance(value, str):
            return f"参数 {key} 应为 string"
        if expected == "string" and "enum" in prop and value not in prop["enum"]:
            return f"参数 {key} 不在允许枚举中: {value}"
        if expected == "integer" and isinstance(value, int):
            if "minimum" in prop and value < prop["minimum"]:
                return f"参数 {key} 不能小于 {prop['minimum']}"
        if expected == "array" and not isinstance(value, list):
            return f"参数 {key} 应为 array"
    required = schema.get("required") or []
    for key in required:
        if key not in args:
            return f"缺少必填参数: {key}"
    return None


def _resolve_stage_callable(runner: str) -> Callable[..., Any]:
    if not runner or "." not in runner:
        raise ValueError(f"无效 runner: {runner!r}")
    module_name, func_name = runner.rsplit(".", 1)
    # Prefer package-relative imports from src/
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        raise ValueError(f"runner 不可调用: {runner}")
    return func


def _call_with_supported_kwargs(func: Callable[..., Any], root: Path, kwargs: dict[str, Any]) -> Any:
    """Call stage runner with only kwargs it accepts (root + optional workers/max_retries)."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(root)

    params = signature.parameters
    call_kwargs: dict[str, Any] = {}
    # positional/root
    accepts_root = "root" in params
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    for key, value in kwargs.items():
        if key in params or accepts_var_kw:
            call_kwargs[key] = value

    if accepts_root:
        return func(root=root, **call_kwargs)
    # some runners take root as first positional only
    if call_kwargs:
        try:
            return func(root, **call_kwargs)
        except TypeError:
            return func(root)
    return func(root)


def _missing_requires(root: Path, stage_id: str) -> list[str]:
    stage = stage_spec_by_id(stage_id)
    missing: list[str] = []
    for artifact in stage.requires:
        if not artifact_exists(root, artifact):
            missing.append(artifact.path)
    return missing


def _resolve_stage_from_args(args: dict[str, Any]):
    command = str(args.get("command") or "").strip()
    stage_id = str(args.get("stage_id") or "").strip()
    if command:
        return stage_spec_by_command(command)
    if stage_id:
        return stage_spec_by_id(stage_id)
    raise KeyError("必须提供 command 或 stage_id")


def _execute_stage(
    root: Path,
    stage_id: str,
    *,
    force: bool = False,
    workers: int = 1,
    max_retries: int = 0,
    dry_run: bool = False,
) -> ToolResult:
    started = _now()
    stage = stage_spec_by_id(stage_id)
    tool_name = stage.command or stage.id
    args = {
        "command": stage.command,
        "stage_id": stage.id,
        "force": force,
        "workers": workers,
        "max_retries": max_retries,
        "dry_run": dry_run,
    }

    missing = _missing_requires(root, stage.id)
    if missing:
        return _fail(
            tool_name,
            args,
            started,
            code="missing_requires",
            message=f"阶段 {stage.id} 缺少前置产物: {', '.join(missing)}",
            retryable=False,
            suggested_tools=["run_stage"],
        )

    # Virtual-only produces (e.g. init) always report ready; do not treat as skip targets.
    concrete_produces = [a for a in stage.produces if a.kind != "virtual"]
    if not force and concrete_produces and stage_outputs_ready(root, stage.id):
        return ToolResult(
            ok=True,
            tool=tool_name,
            args=args,
            started_at=started,
            ended_at=_now(),
            skipped=True,
            summary_for_llm=f"阶段 {stage.id} 产物已就绪，已幂等跳过（force=false）。",
            artifacts_written=[a.path for a in stage.produces],
            metrics={"skipped": True, "stage_id": stage.id},
        )

    if dry_run:
        return ToolResult(
            ok=True,
            tool=tool_name,
            args=args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=(
                f"dry_run: 将执行阶段 {stage.id} ({stage.command})，"
                f"runner={stage.runner}，workers={workers}。"
            ),
            metrics={"dry_run": True, "stage_id": stage.id, "runner": stage.runner},
            raw_refs=[a.path for a in stage.produces],
        )

    if not stage.runner:
        return _fail(
            tool_name,
            args,
            started,
            code="runner_failed",
            message=f"阶段 {stage.id} 未配置 runner",
        )

    try:
        func = _resolve_stage_callable(stage.runner)
        extra: dict[str, Any] = {}
        if workers != 1:
            extra["workers"] = workers
        if max_retries:
            extra["max_retries"] = max_retries
        # Special-case main.init_project which lives in main module
        if stage.runner == "main.init_project":
            import main as main_mod

            main_mod.init_project(root)
        elif stage.id == "write_chapters":
            # Prefer concurrent runner (supports workers/chapter_ids); falls back to write_all.
            from subagent_runner import run_write_all

            run_write_all(
                root,
                workers=max(1, int(workers or 1)),
                max_retries=int(max_retries or 0),
            )
        elif stage.id == "review_fix_chapters":
            from chapter_rewriter import review_fix_all

            review_fix_all(root, workers=max(1, int(workers or 1)))
        else:
            _call_with_supported_kwargs(func, root, extra)
    except Exception as exc:  # noqa: BLE001 - surface to ToolResult
        return _fail(
            tool_name,
            args,
            started,
            code="runner_failed",
            message=f"{type(exc).__name__}: {exc}",
            retryable=True,
            suggested_tools=["diagnose_failure", "retry_stage", "run_stage"],
        )

    produced = [a.path for a in stage.produces if artifact_exists(root, a)]
    ready = stage_outputs_ready(root, stage.id) if stage.produces else True
    if stage.produces and not ready:
        return _fail(
            tool_name,
            args,
            started,
            code="runner_failed",
            message=f"阶段 {stage.id} 执行后产物校验未通过",
            retryable=True,
            suggested_tools=["diagnose_failure", "run_stage"],
        )

    try:
        from agent.invalidation import mark_invalidated, clear_stale_if_rebuilt

        if stage.id in {"write_chapters", "review_fix_chapters", "parse_score", "generate_outline", "plan_chapter_jobs"}:
            mark_invalidated(root, reason=f"stage {stage.id}", source_stage=stage.id)
        if stage.id in {"build_markdown", "build_docx", "check_format", "build_score_coverage_matrix", "build_source_trace_index"}:
            clear_stale_if_rebuilt(root, [a.path for a in stage.produces])
    except Exception:
        pass

    return ToolResult(
        ok=True,
        tool=tool_name,
        args=args,
        started_at=started,
        ended_at=_now(),
        artifacts_written=produced,
        summary_for_llm=f"阶段 {stage.id} ({stage.command}) 执行成功。",
        metrics={"stage_id": stage.id, "command": stage.command, "produced": len(produced)},
        raw_refs=produced,
    )




_ARTIFACT_ALLOW_PREFIXES = (
    "workspace/",
    "inputs/",
    "outputs/",
    "sources/",
    "prompts/",
    "runs/",
)


def _safe_resolve_under_root(root: Path, relative: str) -> Path:
    rel = str(relative or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("非法路径")
    if not any(rel == p.rstrip("/") or rel.startswith(p) for p in _ARTIFACT_ALLOW_PREFIXES):
        # also allow exact top-level known files
        if rel not in {"readme.MD", "readme.md", ".env.example"}:
            raise ValueError(f"路径不在白名单目录内: {rel}")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise ValueError("路径穿越被拒绝")
    return target


def _query_status(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    view = str(args.get("view") or "summary")
    from pipeline_registry import workflow_stage_specs, stage_outputs_ready

    try:
        from graph.state_recorder import load_run_state
        run_state = load_run_state(root)
    except Exception:
        run_state = {}

    workflow = []
    done_count = 0
    next_command = None
    for spec in workflow_stage_specs(include_utility=True):
        ready = False
        try:
            ready = stage_outputs_ready(root, spec.id)
        except Exception:
            ready = False
        if ready:
            done_count += 1
        else:
            if next_command is None and spec.command:
                next_command = {"command": spec.command, "label": spec.label, "stage_id": spec.id}
        workflow.append(
            {
                "id": spec.id,
                "command": spec.command,
                "label": spec.label,
                "done": ready,
            }
        )

    summary = {
        "view": view,
        "stages_total": len(workflow),
        "stages_done": done_count,
        "next_step": next_command,
        "run_state_status": (run_state or {}).get("status"),
        "run_state_message": (run_state or {}).get("message"),
        "current_stage": (run_state or {}).get("current_stage") or (run_state or {}).get("stage"),
    }
    if view == "workflow":
        summary["workflow"] = workflow
    if view == "errors":
        summary["failed"] = (run_state or {}).get("error") or (run_state or {}).get("last_error")
        summary["failed_stage"] = (run_state or {}).get("failed_stage") or (run_state or {}).get("current_stage")

    text = (
        f"进度 {done_count}/{len(workflow)}；"
        f"状态 {summary.get('run_state_status') or '未知'}；"
        f"下一步 {(next_command or {}).get('command') or '无'}。"
    )
    return ToolResult(
        ok=True,
        tool="query_status",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=text[:2000],
        metrics=summary,
        raw_refs=["workspace/run_state.json"],
    )


def _query_artifacts(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    rel = str(args.get("path") or "").strip()
    max_chars = int(args.get("max_chars") or 4000)
    max_chars = max(100, min(max_chars, 20000))
    try:
        target = _safe_resolve_under_root(root, rel)
    except ValueError as exc:
        return _fail("query_artifacts", args, started, code="invalid_args", message=str(exc))

    if not target.exists():
        return _fail(
            "query_artifacts",
            args,
            started,
            code="missing_requires",
            message=f"文件不存在: {rel}",
            suggested_tools=["query_status"],
        )
    if target.is_dir():
        names = sorted([p.name for p in target.iterdir()])[:50]
        summary = f"目录 {rel} 含 {len(names)} 项（最多列 50）: {', '.join(names)}"
        return ToolResult(
            ok=True,
            tool="query_artifacts",
            args=args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=summary[:2000],
            metrics={"path": rel, "is_dir": True, "entries": names},
            raw_refs=[rel],
        )

    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _fail("query_artifacts", args, started, code="runner_failed", message=str(exc))

    truncated = raw[:max_chars]
    note = "" if len(raw) <= max_chars else f"（已截断，原文 {len(raw)} 字符）"
    return ToolResult(
        ok=True,
        tool="query_artifacts",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=f"{rel}{note}:\n{truncated}"[:2000],
        metrics={"path": rel, "size": len(raw), "returned_chars": len(truncated)},
        raw_refs=[rel],
    )


def _diagnose_failure(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    command = str(args.get("command") or "").strip()
    tail_events = int(args.get("tail_events") or 30)
    tail_events = max(1, min(tail_events, 200))

    run_state: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    try:
        from graph.state_recorder import load_run_events, load_run_state

        run_state = load_run_state(root) or {}
        events = load_run_events(root) or []
    except Exception as exc:
        run_state = {"load_error": str(exc)}

    recent = events[-tail_events:] if events else []
    error_events = [
        e
        for e in recent
        if str(e.get("type") or e.get("event") or "").lower() in {"error", "stage_failed", "failed"}
        or e.get("level") in {"error", "fail"}
        or "error" in str(e.get("message") or "").lower()
    ]
    focus = []
    if command:
        focus = [
            e
            for e in recent
            if command in str(e.get("command") or "")
            or command in str(e.get("stage") or "")
            or command in str(e.get("stage_id") or "")
        ]

    status = run_state.get("status")
    message = run_state.get("message") or run_state.get("error") or ""
    failed_stage = run_state.get("failed_stage") or run_state.get("current_stage") or command or ""

    suggested = ["query_status"]
    if failed_stage or command:
        suggested.append("run_stage")
        suggested.append("retry_stage")

    summary = (
        f"诊断：run_state.status={status!r}；failed/current={failed_stage!r}；"
        f"message={str(message)[:300]!r}；"
        f"最近事件 {len(recent)} 条，疑似错误事件 {len(error_events)} 条。"
    )
    if error_events:
        last = error_events[-1]
        summary += f" 最近错误摘要: {str(last.get('message') or last)[:400]}"

    return ToolResult(
        ok=True,
        tool="diagnose_failure",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=summary[:2000],
        metrics={
            "run_state_status": status,
            "failed_stage": failed_stage,
            "message": str(message)[:500],
            "error_event_count": len(error_events),
            "recent_event_count": len(recent),
            "focus_event_count": len(focus),
            "suggested_tools": suggested,
        },
        raw_refs=["workspace/run_state.json", "workspace/run_events.jsonl"],
        error=None,
    )




def _normalize_chapter_ids(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("chapter_ids 必须是字符串数组")
    ids = [str(x).strip() for x in value if str(x).strip()]
    return ids or None


def _execute_chapter_tool(
    root: Path,
    *,
    tool_name: str,
    args: dict[str, Any],
    dry_run: bool = False,
) -> ToolResult:
    started = _now()
    try:
        chapter_ids = _normalize_chapter_ids(args.get("chapter_ids"))
    except ValueError as exc:
        return _fail(tool_name, args, started, code="invalid_args", message=str(exc))

    workers = int(args.get("workers", 2) or 2)
    max_retries = int(args.get("max_retries", 0) or 0)
    call_args = {
        "chapter_ids": chapter_ids,
        "workers": workers,
        "max_retries": max_retries,
        "dry_run": dry_run,
    }

    # requires: jobs directory for all chapter tools
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists() or not any(jobs_dir.glob("*.json")):
        return _fail(
            tool_name,
            call_args,
            started,
            code="missing_requires",
            message="缺少 workspace/jobs/*.json，请先执行 plan-jobs",
            suggested_tools=["run_stage"],
        )

    if dry_run:
        selected = []
        try:
            from subagent_runner import _resolve_chapter_ids

            selected = _resolve_chapter_ids(root, chapter_ids)
        except Exception as exc:
            return _fail(tool_name, call_args, started, code="invalid_args", message=str(exc))
        return ToolResult(
            ok=True,
            tool=tool_name,
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=f"dry_run: {tool_name} 将处理 {len(selected)} 章: {', '.join(selected[:20])}",
            metrics={"dry_run": True, "chapter_ids": selected, "count": len(selected)},
        )

    try:
        from subagent_runner import run_review_all, run_rewrite_all, run_write_all

        if tool_name == "write_chapters":
            result = run_write_all(
                root, workers=max(1, workers), chapter_ids=chapter_ids, max_retries=max_retries
            )
            source_stage = "write_chapters"
        elif tool_name == "review_chapters":
            result = run_review_all(
                root, workers=max(1, workers), chapter_ids=chapter_ids, max_retries=max_retries
            )
            source_stage = "review_fix_chapters"
        elif tool_name == "rewrite_chapters":
            result = run_rewrite_all(
                root, workers=max(1, workers), chapter_ids=chapter_ids, max_retries=max_retries
            )
            source_stage = "write_chapters"
        else:
            return _fail(tool_name, call_args, started, code="unknown_tool", message=tool_name)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            tool_name,
            call_args,
            started,
            code="runner_failed",
            message=f"{type(exc).__name__}: {exc}",
            retryable=True,
            suggested_tools=["diagnose_failure"],
        )

    touched = chapter_ids
    if isinstance(result, dict):
        touched = result.get("chapter_ids") or result.get("selected") or chapter_ids
        if not touched and result.get("ok_ids"):
            touched = result.get("ok_ids")

    from agent.invalidation import mark_invalidated, stale_summary

    stale = mark_invalidated(
        root,
        reason=f"{tool_name} completed",
        chapter_ids=touched if isinstance(touched, list) else chapter_ids,
        source_stage=source_stage,
    )

    summary = f"{tool_name} 完成。"
    if isinstance(result, dict):
        summary += f" result_keys={list(result.keys())[:8]}"
    summary += " " + stale_summary(root)

    return ToolResult(
        ok=True,
        tool=tool_name,
        args=call_args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=summary[:2000],
        metrics={
            "chapter_ids": touched,
            "stale_count": len((stale.get("items") or {})),
            "runner_result": result if isinstance(result, dict) else {"raw": str(result)[:200]},
        },
        artifacts_written=["workspace/chapters"] if tool_name != "review_chapters" else ["workspace/reviews"],
    )




def _execute_build_export(
    root: Path,
    args: dict[str, Any],
    *,
    dry_run: bool = False,
) -> ToolResult:
    """Rebuild export artifacts; force rebuild when marked stale."""
    started = _now()
    targets_raw = args.get("targets")
    if targets_raw is None:
        targets = ["md", "docx", "format"]
    elif isinstance(targets_raw, list):
        targets = [str(x).strip().lower() for x in targets_raw if str(x).strip()]
    else:
        return _fail("build_export", args, started, code="invalid_args", message="targets 应为数组")

    allowed = {"md", "docx", "format"}
    bad = [t for t in targets if t not in allowed]
    if bad:
        return _fail("build_export", args, started, code="invalid_args", message=f"未知 targets: {bad}")
    if not targets:
        targets = ["md", "docx", "format"]

    force = bool(args.get("force", False))
    call_args = {"targets": targets, "force": force, "dry_run": dry_run, "skip_if_gate_fail": bool(args.get("skip_if_gate_fail", False))}

    from agent.invalidation import clear_stale_if_rebuilt, is_stale, load_stale

    stale_state = load_stale(root)
    stale_items = stale_state.get("items") or {}

    need_md = "md" in targets
    need_docx = "docx" in targets
    need_format = "format" in targets

    md_stale = is_stale(root, "outputs/final.md") or force
    docx_stale = is_stale(root, "outputs/final.docx") or force
    # coverage/source aggregates if stale should rebuild before export when present in stale set
    rebuild_plan: list[str] = []
    if need_md and (md_stale or not (root / "outputs" / "final.md").exists()):
        rebuild_plan.append("build_markdown")
    if need_docx and (docx_stale or md_stale or not (root / "outputs" / "final.docx").exists()):
        # docx depends on md
        if "build_markdown" not in rebuild_plan and (md_stale or not (root / "outputs" / "final.md").exists()):
            rebuild_plan.append("build_markdown")
        rebuild_plan.append("build_docx")
    if need_format:
        rebuild_plan.append("check_format")

    # if score coverage stale, rebuild before export for consistency (optional but safer)
    if is_stale(root, "workspace/score_coverage_matrix.json"):
        rebuild_plan.insert(0, "build_score_coverage_matrix")
    if is_stale(root, "workspace/source_trace_index.json"):
        rebuild_plan.insert(0, "build_source_trace_index")

    # de-dupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in rebuild_plan:
        if sid not in seen:
            ordered.append(sid)
            seen.add(sid)

    if dry_run:
        return ToolResult(
            ok=True,
            tool="build_export",
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=(
                f"dry_run: build_export targets={targets}, force={force}, "
                f"will_run_stages={ordered}, stale_count={len(stale_items)}"
            ),
            metrics={"dry_run": True, "stages": ordered, "stale_count": len(stale_items)},
        )

    # compliance blocking gate (hard policy for formal export)
    skip_gate = bool(args.get("skip_if_gate_fail", False))
    compliance_path = root / "workspace" / "compliance_report.json"
    if (need_md or need_docx) and compliance_path.exists() and not skip_gate:
        try:
            import json

            report = json.loads(compliance_path.read_text(encoding="utf-8"))
            blocking = bool(report.get("blocking")) if isinstance(report, dict) else False
            if not blocking and isinstance(report, dict):
                summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
                blocking = bool(summary.get("blocking")) or str(summary.get("status") or "").lower() in {"fail", "blocked"}
            if blocking:
                return _fail(
                    "build_export",
                    call_args,
                    started,
                    code="gate_blocked",
                    message="合规检查 blocking=true，已阻止导出正式稿。请先处理合规项或专项修复。",
                    suggested_tools=["query_artifacts", "fix_coverage", "run_stage"],
                )
        except Exception:
            pass

    # requires chapters for md
    chapters = root / "workspace" / "chapters"
    if need_md or need_docx:
        if not chapters.exists() or not any(chapters.glob("*.md")):
            return _fail(
                "build_export",
                call_args,
                started,
                code="missing_requires",
                message="缺少 workspace/chapters/*.md，无法导出",
                suggested_tools=["write_chapters", "run_stage"],
            )

    ran: list[str] = []
    for stage_id in ordered:
        # force=True so idempotent skip does not keep stale finals
        result = _execute_stage(root, stage_id, force=True)
        ran.append(stage_id)
        if not result.ok:
            return ToolResult(
                ok=False,
                tool="build_export",
                args=call_args,
                started_at=started,
                ended_at=_now(),
                error=result.error,
                summary_for_llm=f"build_export 在阶段 {stage_id} 失败: {result.summary_for_llm}",
                metrics={"ran_stages": ran, "failed_stage": stage_id},
            )

    clear_stale_if_rebuilt(
        root,
        [
            "outputs/final.md",
            "outputs/final.docx",
            "workspace/format_check_report.json",
            "workspace/score_coverage_matrix.json",
            "workspace/source_trace_index.json",
        ],
    )

    produced = []
    for rel in ("outputs/final.md", "outputs/final.docx", "workspace/format_check_report.json"):
        if (root / rel).exists():
            produced.append(rel)

    return ToolResult(
        ok=True,
        tool="build_export",
        args=call_args,
        started_at=started,
        ended_at=_now(),
        artifacts_written=produced,
        summary_for_llm=f"build_export 完成: stages={ran}, produced={produced}",
        metrics={"ran_stages": ran, "produced": produced, "targets": targets},
        raw_refs=produced,
    )




def _load_coverage_matrix(root: Path, *, rebuild: bool = False) -> dict[str, Any]:
    path = root / "workspace" / "score_coverage_matrix.json"
    if rebuild or not path.exists():
        from score_coverage_matrix import build_score_coverage_matrix

        build_score_coverage_matrix(root)
    if not path.exists():
        return {}
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _coverage_gap_plan(root: Path, matrix: dict[str, Any], *, max_chapters: int = 5) -> dict[str, Any]:
    """Derive rewrite chapter targets from coverage matrix rows."""
    uncovered = [str(x) for x in (matrix.get("uncovered_score_points") or [])]
    weak = [str(x) for x in (matrix.get("weak_score_points") or [])]
    hard_uncovered = [str(x) for x in (matrix.get("hard_uncovered_score_points") or [])]
    gap_ids = list(dict.fromkeys(hard_uncovered + uncovered + weak))

    rows = matrix.get("matrix") if isinstance(matrix.get("matrix"), list) else []
    chapter_scores: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        spid = str(row.get("score_point_id") or row.get("id") or "")
        if not spid or spid not in set(gap_ids):
            # also include rows marked not covered
            level = str(row.get("coverage_level") or row.get("status") or "").lower()
            covered = row.get("covered")
            if covered is True and level in {"high", "full", "fully_covered"}:
                continue
            if spid and spid not in set(gap_ids):
                if covered is False or level in {"none", "low", "weak", "uncovered"}:
                    gap_ids.append(spid)
                else:
                    continue

        bound = row.get("bound_chapters") or row.get("chapters") or []
        review = row.get("review_coverage") or []
        candidates: list[str] = []
        if isinstance(bound, list):
            for b in bound:
                if isinstance(b, dict) and b.get("chapter_id"):
                    candidates.append(str(b.get("chapter_id")))
                elif isinstance(b, str):
                    candidates.append(b)
        if isinstance(review, list):
            for r in review:
                if isinstance(r, dict) and r.get("chapter_id"):
                    # prefer chapters where covered is false
                    if r.get("covered") is False or str(r.get("coverage_level") or "").lower() in {"none", "low", "weak"}:
                        candidates.insert(0, str(r.get("chapter_id")))
                    else:
                        candidates.append(str(r.get("chapter_id")))
        for cid in candidates:
            bucket = chapter_scores.setdefault(cid, {"chapter_id": cid, "score_point_ids": [], "weight": 0})
            if spid and spid not in bucket["score_point_ids"]:
                bucket["score_point_ids"].append(spid)
                bucket["weight"] += 2 if spid in set(hard_uncovered) else 1

    # fallback: jobs binding for uncovered points with no row candidates
    if gap_ids and not chapter_scores:
        jobs_dir = root / "workspace" / "jobs"
        if jobs_dir.exists():
            import json

            for jf in jobs_dir.glob("*.json"):
                try:
                    job = json.loads(jf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ids = job.get("score_point_ids") if isinstance(job, dict) else None
                if not isinstance(ids, list):
                    continue
                hit = [str(x) for x in ids if str(x) in set(gap_ids)]
                if hit:
                    chapter_scores[jf.stem] = {
                        "chapter_id": jf.stem,
                        "score_point_ids": hit,
                        "weight": len(hit),
                    }

    ranked = sorted(chapter_scores.values(), key=lambda x: (-int(x.get("weight") or 0), str(x.get("chapter_id"))))
    max_chapters = max(1, int(max_chapters or 5))
    selected = ranked[:max_chapters]
    chapter_ids = [str(x["chapter_id"]) for x in selected]
    summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    return {
        "gap_score_point_ids": gap_ids,
        "uncovered_score_points": uncovered,
        "weak_score_points": weak,
        "hard_uncovered_score_points": hard_uncovered,
        "suggested_chapters": selected,
        "chapter_ids": chapter_ids,
        "summary": summary,
    }


def _analyze_coverage(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    rebuild = bool(args.get("rebuild", False))
    max_chapters = int(args.get("max_chapters", 5) or 5)
    matrix_path = root / "workspace" / "score_coverage_matrix.json"
    if not matrix_path.exists() and not rebuild:
        # auto rebuild if possible, else missing
        rebuild = True
    try:
        matrix = _load_coverage_matrix(root, rebuild=rebuild)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "analyze_coverage",
            args,
            started,
            code="runner_failed",
            message=f"无法构建/读取覆盖矩阵: {exc}",
            suggested_tools=["run_stage"],
        )
    if not matrix:
        return _fail(
            "analyze_coverage",
            args,
            started,
            code="missing_requires",
            message="缺少 score_coverage_matrix.json，请先 build-score-coverage",
            suggested_tools=["run_stage"],
        )
    plan = _coverage_gap_plan(root, matrix, max_chapters=max_chapters)
    summary = plan.get("summary") or {}
    text = (
        f"覆盖分析：评分点 {summary.get('score_point_count', '?')}，"
        f"未覆盖 {len(plan.get('uncovered_score_points') or [])}，"
        f"弱覆盖 {len(plan.get('weak_score_points') or [])}；"
        f"建议改写章节 {plan.get('chapter_ids') or []}。"
    )
    return ToolResult(
        ok=True,
        tool="analyze_coverage",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=text[:2000],
        metrics=plan,
        raw_refs=["workspace/score_coverage_matrix.json"],
    )


def _fix_coverage(root: Path, args: dict[str, Any], *, dry_run: bool = False) -> ToolResult:
    started = _now()
    max_chapters = int(args.get("max_chapters", 5) or 5)
    confirm_execute = bool(args.get("confirm_execute", False))
    rebuild_matrix = bool(args.get("rebuild_matrix", True))
    workers = int(args.get("workers", 2) or 2)
    call_args = {
        "max_chapters": max_chapters,
        "confirm_execute": confirm_execute,
        "rebuild_matrix": rebuild_matrix,
        "workers": workers,
        "dry_run": dry_run,
    }

    analysis = _analyze_coverage(
        root,
        {"rebuild": rebuild_matrix, "max_chapters": max_chapters},
    )
    if not analysis.ok:
        return analysis

    plan = analysis.metrics or {}
    chapter_ids = list(plan.get("chapter_ids") or [])
    if not chapter_ids:
        return ToolResult(
            ok=True,
            tool="fix_coverage",
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm="覆盖率驱动改稿：未发现需要改写的章节（无未覆盖/弱覆盖缺口或无法定位章节）。",
            metrics={**plan, "executed": False, "chapter_ids": []},
            skipped=True,
        )

    if dry_run or not confirm_execute:
        return ToolResult(
            ok=True,
            tool="fix_coverage",
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=(
                f"覆盖率改稿计划：将 rewrite 章节 {chapter_ids} "
                f"（缺口评分点 {len(plan.get('gap_score_point_ids') or [])} 个）。"
                f"{' dry_run' if dry_run else ' 未执行（需 confirm_execute=true）'}。"
            ),
            metrics={**plan, "executed": False, "pending_tool": "rewrite_chapters"},
        )

    # execute rewrite
    rewrite = _execute_chapter_tool(
        root,
        tool_name="rewrite_chapters",
        args={"chapter_ids": chapter_ids, "workers": workers},
        dry_run=False,
    )
    # rebuild matrix after rewrite for fresh status
    try:
        matrix = _load_coverage_matrix(root, rebuild=True)
        post = _coverage_gap_plan(root, matrix, max_chapters=max_chapters)
    except Exception:
        post = {}

    summary = rewrite.summary_for_llm
    if post:
        summary += (
            f" 改后建议章节 {post.get('chapter_ids') or []}，"
            f"剩余未覆盖 {len(post.get('uncovered_score_points') or [])}。"
        )
    return ToolResult(
        ok=rewrite.ok,
        tool="fix_coverage",
        args=call_args,
        started_at=started,
        ended_at=_now(),
        error=rewrite.error,
        summary_for_llm=summary[:2000],
        metrics={
            "executed": rewrite.ok,
            "chapter_ids": chapter_ids,
            "pre": plan,
            "post": post,
            "rewrite": rewrite.metrics,
        },
        artifacts_written=rewrite.artifacts_written,
    )


def invoke(
    tool_name: str,
    args: dict[str, Any] | None = None,
    root: Path | None = None,
    *,
    dry_run: bool = False,
    actor: str = "pipeline",
) -> ToolResult:
    """Invoke a registered tool by name.

    PR-1 supports:
    - run_stage(command=...|stage_id=...)
    - any stage command / stage id as alias of that stage
    """
    started = _now()
    root = root or project_root()
    args = dict(args or {})
    name = str(tool_name or "").strip()
    if not name:
        return _fail("", args, started, code="unknown_tool", message="tool 名为空")

    # Normalize dry_run from args too
    if "dry_run" in args:
        dry_run = bool(args.pop("dry_run"))

    if name == "run_stage":
        spec = get_tool("run_stage")
        assert spec is not None
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        try:
            stage = _resolve_stage_from_args(args)
        except KeyError as exc:
            return _fail(name, args, started, code="invalid_args", message=str(exc))
        return _execute_stage(
            root,
            stage.id,
            force=bool(args.get("force", False)),
            workers=int(args.get("workers", 1) or 1),
            max_retries=int(args.get("max_retries", 0) or 0),
            dry_run=dry_run,
        )

    spec = get_tool(name)
    if spec is None:
        return _fail(
            name,
            args,
            started,
            code="unknown_tool",
            message=f"未知 tool: {name}",
            suggested_tools=["run_stage"],
        )

    if name == "query_status":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _query_status(root, args)

    if name == "query_artifacts":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        if "path" not in args:
            return _fail(name, args, started, code="invalid_args", message="缺少必填参数: path")
        return _query_artifacts(root, args)

    if name == "diagnose_failure":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _diagnose_failure(root, args)

    if name in {"write_chapters", "review_chapters", "rewrite_chapters"}:
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _execute_chapter_tool(root, tool_name=name, args=args, dry_run=dry_run)

    if name == "build_export":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _execute_build_export(root, args, dry_run=dry_run)

    if name == "analyze_coverage":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _analyze_coverage(root, args)

    if name == "fix_coverage":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _fix_coverage(root, args, dry_run=dry_run)

    # Stage-bound tools: execute that stage
    if spec.stage_id:
        # allow only known stage params
        stage_args = {
            k: v for k, v in args.items() if k in {"force", "workers", "max_retries"}
        }
        err = _validate_args(spec, stage_args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _execute_stage(
            root,
            spec.stage_id,
            force=bool(stage_args.get("force", False)),
            workers=int(stage_args.get("workers", 1) or 1),
            max_retries=int(stage_args.get("max_retries", 0) or 0),
            dry_run=dry_run,
        )

    return _fail(
        name,
        args,
        started,
        code="unknown_tool",
        message=f"tool {name} 已注册但 PR-1 尚未实现执行器",
        suggested_tools=["run_stage"],
    )


def list_stage_ids() -> list[str]:
    return [s.id for s in STAGE_SPECS]
