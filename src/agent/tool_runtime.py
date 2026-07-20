from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.tool_registry import get_tool, stage_to_tool_spec
from agent.types import ToolError, ToolResult, ToolSpec
from concurrency import clamp_workers
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
    workers: int | None = None,
    max_retries: int = 0,
    dry_run: bool = False,
    actor: str = "pipeline",
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

    # A repair operation may execute only known root-cause/revalidation stages.
    # Downstream delivery stages remain protected by the normal quality gate.
    repair_stage_commands = {
        "parse-score",
        "extract-facts",
        "generate-outline",
        "select-context-all",
        "write-all",
        "review-fix-all",
        "build-score-coverage",
        "global-review",
        "compliance-check",
    }
    repair_gate_override = actor == "repair" and str(stage.command or "") in repair_stage_commands

    # quality gate (skip dry_run / pure init / whitelisted repair root actions)
    if not dry_run and not repair_gate_override and stage.command not in {"init", "validate"}:
        try:
            from agent.issues import can_proceed

            gate = can_proceed(root, next_command=str(stage.command or ""))
            if not gate.get("can_proceed", True):
                return _fail(
                    tool_name,
                    args,
                    started,
                    code="gate_blocked",
                    message=str(gate.get("message") or "质量门禁阻断"),
                    suggested_tools=["list_issues", "export_preflight", "repair_issue"],
                )
        except Exception:
            pass

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
        effective_workers = clamp_workers(workers)
        extra: dict[str, Any] = {"workers": effective_workers}
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
                workers=effective_workers,
                max_retries=int(max_retries or 0),
            )
        elif stage.id == "review_fix_chapters":
            from chapter_rewriter import review_fix_all

            review_fix_all(root, workers=effective_workers)
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


def _execute_run_pipeline_remaining(root: Path, args: dict[str, Any], *, dry_run: bool = False) -> ToolResult:
    """Run remaining incomplete pipeline stages from the first unfinished one (PR-2)."""
    started = _now()
    from pipeline_registry import workflow_stage_specs, stage_outputs_ready

    start_command = str(args.get("start_command") or "").strip()
    workers = clamp_workers(args.get("workers") or 4)
    max_retries = int(args.get("max_retries") or 1)
    max_stages = int(args.get("max_stages") or 30)
    resume = bool(args.get("resume", True))

    # Export stages are handled by build_export / export_preflight in goal plan
    export_ids = {"build_markdown", "build_docx", "check_format"}
    specs = [
        s
        for s in workflow_stage_specs(include_utility=True)
        if s.id not in export_ids and s.command
    ]

    start_idx = 0
    if start_command:
        for i, s in enumerate(specs):
            if s.command == start_command or s.id == start_command:
                start_idx = i
                break
    elif resume:
        start_idx = 0
        for i, s in enumerate(specs):
            try:
                ready = stage_outputs_ready(root, s.id)
            except Exception:
                ready = False
            if not ready:
                start_idx = i
                break
        else:
            return ToolResult(
                ok=True,
                tool="run_pipeline_remaining",
                args=args,
                started_at=started,
                ended_at=_now(),
                summary_for_llm="流水线核心阶段均已完成，可进行出稿前检查与导出。",
                metrics={
                    "status": "complete",
                    "started_from": "",
                    "completed_stages": [],
                    "blocked_reason": "",
                    "next_command": "export_preflight",
                },
            )

    if dry_run:
        remaining = [s.command for s in specs[start_idx : start_idx + max_stages]]
        return ToolResult(
            ok=True,
            tool="run_pipeline_remaining",
            args=args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=f"dry_run: 将从 {remaining[0] if remaining else '无'} 续跑 {len(remaining)} 个阶段",
            metrics={
                "status": "paused",
                "started_from": remaining[0] if remaining else "",
                "completed_stages": [],
                "blocked_reason": "",
                "next_command": remaining[0] if remaining else "",
                "dry_run": True,
                "planned": remaining,
            },
        )

    completed: list[str] = []
    started_from = specs[start_idx].command if start_idx < len(specs) else ""
    blocked_reason = ""
    status = "complete"
    next_command = ""
    last_error = ""

    for s in specs[start_idx : start_idx + max_stages]:
        # material / human block
        try:
            from agent.goal import detect_human_block

            mat = detect_human_block(root, None)
            if mat:
                status = "blocked"
                blocked_reason = mat
                next_command = s.command
                break
        except Exception:
            pass

        try:
            from agent.issues import can_proceed

            gate = can_proceed(root, next_command=str(s.command or ""))
            if not gate.get("can_proceed", True):
                status = "blocked"
                blocked_reason = str(gate.get("message") or "质量门禁阻断")
                next_command = s.command
                break
        except Exception:
            pass

        try:
            ready = stage_outputs_ready(root, s.id)
        except Exception:
            ready = False
        if ready:
            completed.append(s.command)
            continue

        result = _execute_stage(
            root,
            s.id,
            force=False,
            workers=workers,
            max_retries=max_retries,
            dry_run=False,
            actor="supervisor",
        )
        if result.ok or result.skipped:
            completed.append(s.command)
            continue

        status = "failed"
        last_error = (result.error.message if result.error else result.summary_for_llm) or "stage_failed"
        blocked_reason = last_error
        next_command = s.command
        break
    else:
        # finished loop without break
        if start_idx + max_stages < len(specs):
            status = "paused"
            next_command = specs[start_idx + max_stages].command
            blocked_reason = "达到本轮 max_stages 上限"
        else:
            status = "complete"
            next_command = "export_preflight"

    ok = status in {"complete", "paused", "blocked"}
    summary = (
        f"续跑状态={status} 起点={started_from} 完成={len(completed)} "
        f"原因={blocked_reason or '无'} 下一步={next_command or '无'}"
    )
    return ToolResult(
        ok=ok if status != "failed" else False,
        tool="run_pipeline_remaining",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=summary[:2000],
        error=(
            ToolError(code="runner_failed", message=last_error, retryable=True)
            if status == "failed"
            else None
        ),
        metrics={
            "status": status,
            "started_from": started_from,
            "completed_stages": completed,
            "blocked_reason": blocked_reason,
            "next_command": next_command,
        },
    )


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

    workers = clamp_workers(args.get("workers"))
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
    call_args = {
        "targets": targets,
        "force": force,
        "dry_run": dry_run,
        "skip_if_gate_fail": bool(args.get("skip_if_gate_fail", False)),
        "as_draft": bool(args.get("as_draft", False)),
    }

    from agent.invalidation import clear_stale_if_rebuilt, is_stale, load_stale

    stale_state = load_stale(root)
    stale_items = stale_state.get("items") or {}

    need_md = "md" in targets
    need_docx = "docx" in targets
    need_format = "format" in targets

    # requires chapters for md/docx first (clearer errors than gate)
    chapters = root / "workspace" / "chapters"
    if (need_md or need_docx) and not dry_run:
        if not chapters.exists() or not any(chapters.glob("*.md")):
            return _fail(
                "build_export",
                call_args,
                started,
                code="missing_requires",
                message="缺少 workspace/chapters/*.md，无法导出",
                suggested_tools=["write_chapters", "run_stage"],
            )

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

    # formal export gate (final.docx); draft may skip
    skip_gate = bool(args.get("skip_if_gate_fail", False)) or bool(args.get("as_draft", False))
    if not skip_gate and (need_md or need_docx):
        try:
            from agent.issues import export_preflight

            pre = export_preflight(root)
            if not pre.get("can_export"):
                # If reviews never ran, still allow rebuild path when only open_blocks fail soft
                # but missing reports block formal final — surface gate_blocked
                return _fail(
                    "build_export",
                    {"targets": targets, "force": force},
                    started,
                    code="gate_blocked",
                    message=str(pre.get("message") or "出稿前检查未通过"),
                    suggested_tools=["list_issues", "export_preflight", "repair_issue"],
                )
        except Exception:
            pass

    # compliance blocking gate (hard policy for formal export)
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
    workers = clamp_workers(args.get("workers"))
    max_rounds = max(1, min(int(args.get("max_rounds", 1) or 1), 3))
    call_args = {
        "max_chapters": max_chapters,
        "confirm_execute": confirm_execute,
        "rebuild_matrix": rebuild_matrix,
        "workers": workers,
        "max_rounds": max_rounds,
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
            metrics={**plan, "executed": False, "chapter_ids": [], "rounds": 0},
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
                f"（缺口评分点 {len(plan.get('gap_score_point_ids') or [])} 个，最多 {max_rounds} 轮）。"
                f"{' dry_run' if dry_run else ' 未执行（需 confirm_execute=true）'}。"
            ),
            metrics={**plan, "executed": False, "pending_tool": "rewrite_chapters", "max_rounds": max_rounds},
        )

    rounds_log: list[dict[str, Any]] = []
    last_rewrite_ok = True
    current_plan = plan
    touched: list[str] = []
    no_progress = False

    def _gap_signature(value: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        chapters = tuple(sorted(str(item) for item in (value.get("chapter_ids") or [])))
        gaps = value.get("gap_score_point_ids") or value.get("uncovered_score_points") or []
        gap_ids = tuple(
            sorted(
                str(item.get("score_point_id") or item.get("id") or item)
                if isinstance(item, dict)
                else str(item)
                for item in gaps
            )
        )
        return chapters, gap_ids

    for round_idx in range(1, max_rounds + 1):
        cids = list(current_plan.get("chapter_ids") or [])
        if not cids:
            break
        before_signature = _gap_signature(current_plan)
        rewrite = _execute_chapter_tool(
            root,
            tool_name="rewrite_chapters",
            args={"chapter_ids": cids, "workers": workers},
            dry_run=False,
        )
        last_rewrite_ok = bool(rewrite.ok)
        for cid in cids:
            if cid not in touched:
                touched.append(cid)
        rounds_log.append(
            {
                "round": round_idx,
                "chapter_ids": cids,
                "ok": rewrite.ok,
                "summary": (rewrite.summary_for_llm or "")[:300],
            }
        )
        if not rewrite.ok:
            break
        try:
            matrix = _load_coverage_matrix(root, rebuild=True)
            current_plan = _coverage_gap_plan(root, matrix, max_chapters=max_chapters)
        except Exception as exc:  # noqa: BLE001
            current_plan = {"chapter_ids": [], "error": str(exc)}
            break
        # stop if no more gaps
        if not (current_plan.get("chapter_ids") or current_plan.get("uncovered_score_points") or current_plan.get("weak_score_points")):
            break
        if _gap_signature(current_plan) == before_signature:
            no_progress = True
            rounds_log[-1]["no_progress"] = True
            break

    remaining = list(current_plan.get("chapter_ids") or [])
    summary = (
        f"覆盖率改稿完成：执行 {len(rounds_log)}/{max_rounds} 轮，"
        f"触达章节 {touched}，剩余建议章节 {remaining}，"
        f"剩余未覆盖 {len(current_plan.get('uncovered_score_points') or [])}。"
    )
    return ToolResult(
        ok=last_rewrite_ok,
        tool="fix_coverage",
        args=call_args,
        started_at=started,
        ended_at=_now(),
        error=None,
        summary_for_llm=summary[:2000],
        metrics={
            "executed": True,
            "chapter_ids": touched,
            "rounds": len(rounds_log),
            "max_rounds": max_rounds,
            "rounds_log": rounds_log,
            "pre": plan,
            "post": current_plan,
            "remaining_chapter_ids": remaining,
            "no_progress": no_progress,
        },
        artifacts_written=["workspace/chapters", "workspace/score_coverage_matrix.json"],
    )





def _analyze_compliance(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    sync = bool(args.get("sync", True))
    report_path = root / "workspace" / "compliance_report.json"
    if not report_path.exists():
        return _fail(
            "analyze_compliance",
            args,
            started,
            code="missing_requires",
            message="缺少 workspace/compliance_report.json，请先 compliance-check",
            suggested_tools=["run_stage"],
        )
    try:
        import json

        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _fail("analyze_compliance", args, started, code="runner_failed", message=str(exc))

    if sync:
        try:
            from compliance_feedback import sync_compliance_findings

            sync_compliance_findings(root)
        except Exception as exc:
            # non-fatal
            sync_error = str(exc)
        else:
            sync_error = ""
    else:
        sync_error = ""

    from compliance_feedback import MANUAL_ONLY_TYPES, REWRITEABLE_TYPES

    items = report.get("items") if isinstance(report, dict) else []
    if not isinstance(items, list):
        items = []

    rewriteable_fails: list[dict[str, Any]] = []
    manual_items: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "")
        severity = str(raw.get("severity") or "")
        if status not in {"fail", "warn"}:
            continue
        if severity not in {"fatal", "critical", "major"}:
            continue
        ctype = str(raw.get("check_type") or "unknown")
        entry = {
            "check_id": raw.get("check_id"),
            "check_type": ctype,
            "check_name": raw.get("check_name"),
            "severity": severity,
            "status": status,
            "suggestion": raw.get("suggestion"),
        }
        if ctype in MANUAL_ONLY_TYPES:
            manual_items.append(entry)
        elif ctype in REWRITEABLE_TYPES:
            rewriteable_fails.append(entry)
        else:
            # unknown types: treat as rewriteable major fails for planning
            rewriteable_fails.append(entry)

    # chapter ids from hints file
    hints_path = root / "workspace" / "compliance_rewrite_hints.json"
    chapter_ids: list[str] = []
    chapter_fix_counts: dict[str, int] = {}
    if hints_path.exists():
        try:
            import json

            hints = json.loads(hints_path.read_text(encoding="utf-8"))
            chapters = hints.get("chapters") if isinstance(hints, dict) else {}
            if isinstance(chapters, dict):
                for cid, fixes in chapters.items():
                    if isinstance(fixes, list) and fixes:
                        chapter_ids.append(str(cid))
                        chapter_fix_counts[str(cid)] = len(fixes)
        except Exception:
            pass

    blocking = bool(report.get("blocking")) if isinstance(report, dict) else False
    if not blocking and isinstance(report, dict):
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        blocking = bool(summary.get("blocking"))

    metrics = {
        "blocking": blocking,
        "rewriteable_count": len(rewriteable_fails),
        "manual_count": len(manual_items),
        "chapter_ids": chapter_ids,
        "chapter_fix_counts": chapter_fix_counts,
        "rewriteable_items": rewriteable_fails[:30],
        "manual_items": manual_items[:30],
        "sync_error": sync_error,
    }
    text_out = (
        f"合规分析：blocking={blocking}，可改写项 {len(rewriteable_fails)}，"
        f"人工项 {len(manual_items)}，建议章节 {chapter_ids}。"
    )
    return ToolResult(
        ok=True,
        tool="analyze_compliance",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=text_out[:2000],
        metrics=metrics,
        raw_refs=["workspace/compliance_report.json", "workspace/compliance_rewrite_hints.json"],
    )


def _fix_compliance(root: Path, args: dict[str, Any], *, dry_run: bool = False) -> ToolResult:
    started = _now()
    confirm_execute = bool(args.get("confirm_execute", False))
    rerun_check = bool(args.get("rerun_check", False))
    max_chapters = max(1, int(args.get("max_chapters", 8) or 8))
    workers = clamp_workers(args.get("workers"))
    sync = bool(args.get("sync", True))
    call_args = {
        "confirm_execute": confirm_execute,
        "rerun_check": rerun_check,
        "max_chapters": max_chapters,
        "workers": workers,
        "sync": sync,
        "dry_run": dry_run,
    }

    analysis = _analyze_compliance(root, {"sync": sync})
    if not analysis.ok:
        return analysis
    plan = analysis.metrics or {}
    chapter_ids = list(plan.get("chapter_ids") or [])[:max_chapters]
    if not chapter_ids:
        return ToolResult(
            ok=True,
            tool="fix_compliance",
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm="合规定向改稿：没有可自动回灌的章节（可能仅有人工项或报告无 fail）。",
            metrics={**plan, "executed": False, "chapter_ids": []},
            skipped=True,
        )

    if dry_run or not confirm_execute:
        return ToolResult(
            ok=True,
            tool="fix_compliance",
            args=call_args,
            started_at=started,
            ended_at=_now(),
            summary_for_llm=(
                f"合规改稿计划：rewrite 章节 {chapter_ids}；"
                f"人工项 {plan.get('manual_count')}；"
                f"{'dry_run' if dry_run else '未执行（需 confirm_execute=true）'}。"
            ),
            metrics={**plan, "executed": False, "pending_tool": "rewrite_chapters", "chapter_ids": chapter_ids},
        )

    rewrite = _execute_chapter_tool(
        root,
        tool_name="rewrite_chapters",
        args={"chapter_ids": chapter_ids, "workers": workers},
        dry_run=False,
    )
    post_check = None
    if rewrite.ok and rerun_check:
        post_check = _execute_stage(root, "compliance_check", force=True)

    summary = rewrite.summary_for_llm
    if post_check is not None:
        summary += f" 重跑合规：{'OK' if post_check.ok else 'FAIL'} {post_check.summary_for_llm}"

    return ToolResult(
        ok=rewrite.ok and (post_check.ok if post_check is not None else True),
        tool="fix_compliance",
        args=call_args,
        started_at=started,
        ended_at=_now(),
        error=rewrite.error if not rewrite.ok else (post_check.error if post_check and not post_check.ok else None),
        summary_for_llm=summary[:2000],
        metrics={
            "executed": True,
            "chapter_ids": chapter_ids,
            "pre": plan,
            "rewrite": rewrite.metrics,
            "post_check_ok": None if post_check is None else post_check.ok,
        },
        artifacts_written=rewrite.artifacts_written,
    )




def _list_issues_tool(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    from agent.issues import issues_summary, load_open_issues
    from agent.root_cause import sync_issues_from_compliance, sync_issues_from_global_review

    try:
        sync_issues_from_global_review(root)
        sync_issues_from_compliance(root)
    except Exception:
        pass
    status = str(args.get("status") or "open")
    issues = load_open_issues(root)
    if status == "open":
        issues = [i for i in issues if str(i.get("status")) in {"open", "in_progress"}]
    elif status == "block":
        issues = [
            i
            for i in issues
            if str(i.get("severity")) == "block" and str(i.get("status")) in {"open", "in_progress"}
        ]
    summary = issues_summary(root)
    text = (
        f"问题单：open_block={summary.get('block_count')}，open={summary.get('open_count')}，"
        f"can_proceed={summary.get('can_proceed')}。"
    )
    if issues:
        tops = "；".join(str(i.get("title") or i.get("code")) for i in issues[:5])
        text += " 例：" + tops
    return ToolResult(
        ok=True,
        tool="list_issues",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=text[:2000],
        metrics={"summary": summary, "issues": issues[:50], "count": len(issues)},
        raw_refs=["workspace/issues/open.json"],
    )


def _explain_issue_tool(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    issue_id = str(args.get("issue_id") or "").strip()
    if not issue_id:
        return _fail("explain_issue", args, started, code="invalid_args", message="缺少 issue_id")
    from agent.issues import load_open_issues
    from agent.root_cause import refine_issue_cause_with_llm

    issue = next((i for i in load_open_issues(root) if str(i.get("id")) == issue_id), None)
    if not issue:
        return _fail("explain_issue", args, started, code="missing_requires", message=f"未找到问题 {issue_id}")
    result = refine_issue_cause_with_llm(root, issue)
    return ToolResult(
        ok=bool(result.get("ok")),
        tool="explain_issue",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=str(result.get("message") or result)[:2000],
        metrics=result,
    )


def _repair_issue_tool(root: Path, args: dict[str, Any], *, dry_run: bool = False) -> ToolResult:
    started = _now()
    issue_id = str(args.get("issue_id") or "").strip()
    if not issue_id:
        return _fail("repair_issue", args, started, code="invalid_args", message="缺少 issue_id")
    confirm = bool(args.get("confirm_execute", False))
    from agent.repair import execute_repair_plan

    result = execute_repair_plan(root, issue_id, confirm=confirm and not dry_run, dry_run=dry_run or not confirm)
    return ToolResult(
        ok=bool(result.get("ok")),
        tool="repair_issue",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=str(result.get("message") or result.get("summary") or "")[:2000],
        metrics=result,
        skipped=not result.get("executed"),
    )


def _export_preflight_tool(root: Path, args: dict[str, Any]) -> ToolResult:
    started = _now()
    from agent.issues import export_preflight

    pre = export_preflight(root)
    return ToolResult(
        ok=True,
        tool="export_preflight",
        args=args,
        started_at=started,
        ended_at=_now(),
        summary_for_llm=str(pre.get("message") or "")[:2000],
        metrics=pre,
        raw_refs=["workspace/issues/open.json", "workspace/global_review.json", "workspace/compliance_report.json"],
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
            workers=clamp_workers(args.get("workers")),
            max_retries=int(args.get("max_retries", 0) or 0),
            dry_run=dry_run,
            actor=actor,
        )

    if name == "run_pipeline_remaining":
        spec = get_tool("run_pipeline_remaining")
        assert spec is not None
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _execute_run_pipeline_remaining(root, args, dry_run=dry_run)

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

    if name == "analyze_compliance":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _analyze_compliance(root, args)

    if name == "fix_compliance":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _fix_compliance(root, args, dry_run=dry_run)

    if name == "list_issues":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _list_issues_tool(root, args)

    if name == "explain_issue":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _explain_issue_tool(root, args)

    if name == "repair_issue":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _repair_issue_tool(root, args, dry_run=dry_run)

    if name == "export_preflight":
        err = _validate_args(spec, args)
        if err:
            return _fail(name, args, started, code="invalid_args", message=err)
        return _export_preflight_tool(root, args)

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
            workers=clamp_workers(stage_args.get("workers")),
            max_retries=int(stage_args.get("max_retries", 0) or 0),
            dry_run=dry_run,
            actor=actor,
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
