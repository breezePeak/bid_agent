from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from utils import read_json, write_json


ACTIVE_REPAIR_STATUSES = {"awaiting_confirmation", "running", "revalidating"}
RUNNING_REPAIR_STATUSES = {"running", "revalidating"}
TERMINAL_REPAIR_STATUSES = {"completed", "partial", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def repair_job_path(root: Path) -> Path:
    return root / "workspace" / "repair_job.json"


def _lock_path(root: Path) -> Path:
    return root / "workspace" / ".repair_job.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@contextmanager
def _job_lock(root: Path, *, timeout: float = 3.0) -> Iterator[None]:
    """Small cross-process lock used only around atomic repair-job transitions."""
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("repair job is busy")
            try:
                lock_pid = int(path.read_text(encoding="ascii", errors="ignore").strip() or 0)
                if not _pid_alive(lock_pid) or time.time() - path.stat().st_mtime > 30:
                    path.unlink(missing_ok=True)
                    continue
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.close(fd)
        finally:
            path.unlink(missing_ok=True)


def load_repair_job(root: Path) -> dict[str, Any]:
    path = repair_job_path(root)
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_job(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job)
    payload["updated_at"] = _now()
    write_json(repair_job_path(root), payload)
    return payload


def create_confirmation(
    root: Path,
    *,
    issue_fingerprints: list[str],
    total_count: int,
    auto_count: int,
    manual_count: int,
    resume_command: str,
) -> dict[str, Any]:
    """Create or reuse the persisted awaiting-confirmation job."""
    fingerprints = sorted({str(item) for item in issue_fingerprints if str(item)})
    with _job_lock(root):
        current = load_repair_job(root)
        status = str(current.get("status") or "")
        if status in RUNNING_REPAIR_STATUSES:
            return current
        if status == "awaiting_confirmation" and current.get("issue_fingerprints") == fingerprints:
            current.update(
                {
                    "total_count": int(total_count),
                    "auto_count": int(auto_count),
                    "manual_count": int(manual_count),
                    "resume_command": str(resume_command or current.get("resume_command") or ""),
                }
            )
            return _write_job(root, current)

        now = _now()
        job = {
            "job_id": f"repair-{uuid.uuid4().hex[:12]}",
            "confirmation_id": f"confirm-{uuid.uuid4().hex[:12]}",
            "status": "awaiting_confirmation",
            "phase": "awaiting_confirmation",
            "issue_fingerprints": fingerprints,
            "total_count": int(total_count),
            "auto_count": int(auto_count),
            "manual_count": int(manual_count),
            "resolved_count": 0,
            "remaining_count": int(total_count),
            "failed_count": 0,
            "progress_percent": 0,
            "phase_completed": 0,
            "phase_total": 0,
            "resume_command": str(resume_command or ""),
            "resume_attempted": False,
            "message": f"发现 {int(total_count)} 个阻断问题，等待确认最小修复",
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "result": {},
        }
        return _write_job(root, job)


def claim_repair_job(root: Path, confirmation_id: str) -> dict[str, Any]:
    """Atomically claim a confirmed job; duplicate confirmation is idempotent."""
    with _job_lock(root):
        job = load_repair_job(root)
        if not job:
            return {"ok": False, "message": "没有待确认的修复任务"}
        status = str(job.get("status") or "")
        if status in RUNNING_REPAIR_STATUSES:
            return {"ok": True, "duplicate": True, "job": job}
        if status in TERMINAL_REPAIR_STATUSES:
            return {"ok": True, "duplicate": True, "job": job}
        expected = str(job.get("confirmation_id") or "")
        if not confirmation_id or confirmation_id != expected:
            return {"ok": False, "message": "修复确认已失效，请重新确认"}
        if status != "awaiting_confirmation":
            return {"ok": False, "message": "修复任务状态不可确认"}
        job.update(
            {
                "status": "running",
                "phase": "analyzing",
                "started_at": job.get("started_at") or _now(),
                "finished_at": "",
                "message": "正在分析阻断问题并合并根因动作",
            }
        )
        job = _write_job(root, job)
        return {"ok": True, "duplicate": False, "job": job}


def update_repair_job(root: Path, job_id: str, **changes: Any) -> dict[str, Any]:
    with _job_lock(root):
        job = load_repair_job(root)
        if not job or str(job.get("job_id") or "") != str(job_id):
            return {}
        job.update(changes)
        if str(job.get("status") or "") in TERMINAL_REPAIR_STATUSES and not job.get("finished_at"):
            job["finished_at"] = _now()
        return _write_job(root, job)


def decline_repair_job(root: Path, confirmation_id: str) -> dict[str, Any]:
    with _job_lock(root):
        job = load_repair_job(root)
        if not job or str(job.get("status") or "") != "awaiting_confirmation":
            return job
        if confirmation_id and str(job.get("confirmation_id") or "") != confirmation_id:
            return job
        job.update(
            {
                "status": "completed",
                "phase": "declined",
                "message": "用户选择暂不执行最小修复",
                "finished_at": _now(),
                "result": {"declined": True},
            }
        )
        return _write_job(root, job)


def reconcile_interrupted_repair(root: Path) -> dict[str, Any]:
    """Keep confirmations after restart, but close a worker state that cannot resume."""
    job = load_repair_job(root)
    if str(job.get("status") or "") not in RUNNING_REPAIR_STATUSES:
        return job
    return update_repair_job(
        root,
        str(job.get("job_id") or ""),
        status="failed",
        phase="interrupted",
        failed_count=max(1, int(job.get("failed_count") or 0)),
        message="服务重启中断了修复任务，请重新发起自动修复",
        result={"error": "repair_interrupted_by_restart"},
    )
