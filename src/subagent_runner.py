from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from concurrency import chapter_workers_scope, clamp_workers, workers_default
from graph.chapter_subgraph import build_chapter_subgraph
from utils import project_root

try:
    from agent.activity import begin_phase, end_phase, mark_agent
except Exception:  # pragma: no cover
    def begin_phase(*args, **kwargs):  # type: ignore
        return {}
    def end_phase(*args, **kwargs):  # type: ignore
        return {}
    def mark_agent(*args, **kwargs):  # type: ignore
        return {}


def _write_worker(chapter_id: str, root: Path) -> None:
    graph = build_chapter_subgraph()
    graph.invoke({"root_dir": str(root), "chapter_id": chapter_id})


def _review_worker(chapter_id: str, root: Path) -> None:
    from chapter_reviewer import review_chapter

    review_chapter(chapter_id, root)


def _rewrite_worker(chapter_id: str, root: Path) -> None:
    from chapter_rewriter import rewrite_chapter

    rewrite_chapter(chapter_id, root)


def _run_with_retry(
    worker: Callable[[str, Path], None],
    chapter_id: str,
    root: Path,
    max_retries: int,
    role: str = "chapter_writer",
) -> tuple[str, str | None, int]:
    attempts = max(1, max_retries + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            print(f"[执行] 章节 {chapter_id} 开始（第 {attempt}/{attempts} 次）…", flush=True)
            mark_agent(
                root,
                role=role,
                chapter_id=chapter_id,
                status="running",
                message=f"第 {attempt}/{attempts} 次执行中",
                attempt=attempt,
            )
            worker(chapter_id, root)
            print(f"[完成] 章节 {chapter_id} 本轮执行成功", flush=True)
            mark_agent(
                root,
                role=role,
                chapter_id=chapter_id,
                status="done",
                message="完成",
                attempt=attempt,
            )
            return chapter_id, None, attempt
        except Exception as exc:
            last_error = str(exc)
            print(f"[重试] 章节 {chapter_id} 第 {attempt}/{attempts} 次失败: {last_error}")
            mark_agent(
                root,
                role=role,
                chapter_id=chapter_id,
                status="running" if attempt < attempts else "failed",
                message=f"失败: {last_error[:120]}",
                attempt=attempt,
            )
    return chapter_id, last_error, attempts


def _resolve_chapter_ids(root: Path, chapter_ids: list[str] | None) -> list[str]:
    jobs_dir = root / "workspace" / "jobs"
    if not jobs_dir.exists():
        raise FileNotFoundError(
            f"缺少章节任务目录: {jobs_dir}，请先执行 plan-jobs"
        )
    job_files = sorted(jobs_dir.glob("*.json"))
    if not job_files:
        raise FileNotFoundError(
            f"章节任务目录为空: {jobs_dir}，请先执行 plan-jobs"
        )
    all_ids = [f.stem for f in job_files]
    if chapter_ids is None:
        return all_ids
    requested = {str(chapter_id) for chapter_id in chapter_ids}
    selected = [chapter_id for chapter_id in all_ids if chapter_id in requested]
    missing = sorted(requested.difference(selected))
    if missing:
        raise FileNotFoundError(f"未找到章节任务: {missing}")
    return selected


def _label_to_role(label: str) -> str:
    text = str(label or "")
    if "审核" in text or "review" in text.lower():
        return "chapter_reviewer"
    if "改稿" in text or "rewrite" in text.lower():
        return "chapter_rewriter"
    if "写作" in text or "write" in text.lower():
        return "chapter_writer"
    return "chapter_writer"


def _writer_batch_retries() -> int:
    """How many extra full-batch retries after the first write pass fails."""
    import os

    try:
        return max(0, min(20, int(os.environ.get("BID_AGENT_WRITE_BATCH_RETRIES", "5"))))
    except (TypeError, ValueError):
        return 5


def _writer_fallback_enabled() -> bool:
    import os

    value = str(os.environ.get("BID_AGENT_WRITE_FAILURE_FALLBACK", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def run_per_chapter(
    worker: Callable[[str, Path], None],
    root: Path | None = None,
    workers: int | None = None,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
    label: str = "SubAgent",
) -> dict[str, Any]:
    root = root or project_root()
    selected = _resolve_chapter_ids(root, chapter_ids)
    requested = workers_default() if workers is None else workers
    role = _label_to_role(label)
    completed: list[str] = []
    failed: list[dict[str, Any]] = []
    # Writing: keep retrying failed chapters automatically (no "fire desk" handoff).
    batch_retries = _writer_batch_retries() if role == "chapter_writer" else 0
    pending = list(selected)

    with chapter_workers_scope(requested) as effective_workers:
        print(
            f"[启动] 并发执行 {len(selected)} 个章节 {label}, "
            f"workers={effective_workers}, max_retries={max(0, max_retries)}"
            + (f", batch_retries={batch_retries}" if batch_retries else "")
        )
        begin_phase(
            root,
            phase=role,
            phase_label=label,
            role=role,
            chapter_ids=selected,
        )

        batch_round = 0
        while pending:
            batch_round += 1
            round_failed: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = {
                    executor.submit(_run_with_retry, worker, cid, root, max_retries, role): cid
                    for cid in pending
                }
                for future in as_completed(futures):
                    chapter_id = futures[future]
                    try:
                        result_id, error, attempts = future.result()
                    except Exception as exc:
                        error = str(exc)
                        result_id = chapter_id
                        attempts = max(1, max_retries + 1)

                    if error:
                        print(f"[失败] 章节 {result_id}: {error}")
                        round_failed.append(
                            {"chapter_id": result_id, "error": error, "attempts": attempts}
                        )
                    else:
                        completed.append(result_id)

            if not round_failed:
                failed = []
                break

            failed = round_failed
            if role != "chapter_writer" or batch_round > batch_retries:
                break

            pending = [str(item.get("chapter_id") or "") for item in failed if item.get("chapter_id")]
            if not pending:
                break
            print(
                f"[自动重试] 第 {batch_round}/{batch_retries} 批：写作失败章节继续写 "
                f"({len(pending)} 章) → {pending}"
            )

    if failed and role == "chapter_writer" and _writer_fallback_enabled():
        from chapter_writer import write_fallback_chapter

        unresolved: list[dict[str, Any]] = []
        for item in failed:
            chapter_id = str(item.get("chapter_id") or "").strip()
            try:
                write_fallback_chapter(chapter_id, root, failure_reason=str(item.get("error") or ""))
                completed.append(chapter_id)
                mark_agent(
                    root,
                    role=role,
                    chapter_id=chapter_id,
                    status="done",
                    message="已生成保底草稿",
                    attempt=int(item.get("attempts") or 1),
                )
            except Exception as exc:
                unresolved.append({**item, "fallback_error": str(exc)})
        failed = unresolved

    print(f"[完成] {label} 成功 {len(completed)} 个, 失败 {len(failed)} 个")
    if failed:
        print(f"[详情] 失败章节: {[f['chapter_id'] for f in failed]}")
    end_phase(
        root,
        status="done" if not failed else "partial_failed",
        message=f"成功 {len(completed)} / 失败 {len(failed)}",
    )
    if failed and role == "chapter_writer":
        try:
            from agent.root_cause import sync_issues_from_write_failures

            sync_issues_from_write_failures(root, failed)
        except Exception as exc:
            print(f"[警告] 同步写作失败 Issue 失败: {exc}")
        raise RuntimeError(
            "章节写作仍失败（已自动重试）："
            + str([f.get("chapter_id") for f in failed])
            + f"。已重试批次上限 batch_retries={batch_retries}；"
            "可增大 BID_AGENT_WRITE_BATCH_RETRIES 或检查模型/材料后重跑 write-all。"
        )
    if failed and role in {"chapter_reviewer", "chapter_rewriter"}:
        try:
            from agent.root_cause import sync_issues_from_write_failures
            # reuse write failure shape for failed review/rewrite chapters
            sync_issues_from_write_failures(root, failed)
        except Exception:
            pass
        # review path also raises via review_fix gate; keep soft here
    return {"completed": completed, "failed": failed}


def run_write_chapter(
    chapter_id: str,
    root: Path | None = None,
    max_retries: int = 0,
) -> tuple[str, str | None, int]:
    root = root or project_root()
    return _run_with_retry(_write_worker, chapter_id, root, max_retries, role="chapter_writer")


def run_write_all(
    root: Path | None = None,
    workers: int | None = None,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _write_worker,
        root,
        clamp_workers(workers) if workers is not None else None,
        chapter_ids,
        max_retries,
        label="写作 SubAgent",
    )


def run_review_all(
    root: Path | None = None,
    workers: int | None = None,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _review_worker,
        root,
        clamp_workers(workers) if workers is not None else None,
        chapter_ids,
        max_retries,
        label="审核 SubAgent",
    )


def run_rewrite_all(
    root: Path | None = None,
    workers: int | None = None,
    chapter_ids: list[str] | None = None,
    max_retries: int = 0,
) -> dict[str, Any]:
    return run_per_chapter(
        _rewrite_worker,
        root,
        clamp_workers(workers) if workers is not None else None,
        chapter_ids,
        max_retries,
        label="改稿 SubAgent",
    )


def run_global_review(root: Path | None = None) -> Path:
    from global_reviewer import run_global_review as _run_global_review

    root = root or project_root()
    return _run_global_review(root)
