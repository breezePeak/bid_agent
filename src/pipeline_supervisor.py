from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pipeline_registry import auto_run_commands, stage_outputs_ready, stage_spec_by_command


RunStage = Callable[[str, str, Path], int]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class PipelineSupervisor:
    """Process-local supervisor with a durable control record per workspace."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._run_root: Path | None = None
        self._run_id = ""

    @staticmethod
    def control_path(root: Path) -> Path:
        return root / "workspace" / "pipeline_control.json"

    def load(self, root: Path) -> dict[str, Any]:
        path = self.control_path(root)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            path = self.control_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            current = self.load(root)
            current.update(payload)
            current["updated_at"] = _now()
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            return current

    def is_running(self, root: Path | None = None) -> bool:
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            if not alive:
                return False
            return root is None or (self._run_root is not None and self._run_root.resolve() == root.resolve())

    def heartbeat(
        self,
        root: Path,
        *,
        command: str,
        worker_pid: int = 0,
        progress_at: str = "",
        message: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "heartbeat_at": _now(),
            "current_stage": command,
        }
        if worker_pid:
            payload["worker_pid"] = worker_pid
        if progress_at:
            payload["progress_at"] = progress_at
        if message:
            payload["message"] = message
        try:
            spec = stage_spec_by_command(command)
            if spec.validator == "collection":
                from stage_validation import stage_collection_status

                collection = stage_collection_status(root, spec.id)
                payload.update(
                    {
                        "completed": collection["completed_count"],
                        "total": collection["expected_count"],
                        "missing_count": len(collection["missing_ids"]),
                    }
                )
        except Exception:
            pass
        self._save(root, payload)

    def start(self, run_id: str, root: Path, runner: RunStage, *, start_command: str = "") -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._pause.clear()
            self._run_root = root
            self._run_id = run_id
            self._save(
                root,
                {
                    "run_id": run_id,
                    "status": "running",
                    "requested_action": "run_all",
                    "current_stage": start_command,
                    "started_at": _now(),
                    "heartbeat_at": _now(),
                    "progress_at": _now(),
                    "worker_pid": 0,
                    "resume": True,
                    "error": "",
                },
            )
            self._thread = threading.Thread(
                target=self._loop,
                args=(run_id, root, runner, start_command),
                daemon=True,
                name=f"pipeline-{run_id}",
            )
            self._thread.start()
            return True

    def pause(self) -> None:
        self._pause.set()
        with self._lock:
            if self._run_root:
                self._save(self._run_root, {"status": "pausing", "message": "正在暂停流水线"})

    def _loop(self, run_id: str, root: Path, runner: RunStage, start_command: str) -> None:
        commands = auto_run_commands()
        if start_command in commands:
            commands = commands[commands.index(start_command) :]
        try:
            for command in commands:
                if self._pause.is_set():
                    self._save(root, {"status": "paused", "current_stage": command, "worker_pid": 0})
                    return
                spec = stage_spec_by_command(command)
                if stage_outputs_ready(root, spec.id):
                    self._save(
                        root,
                        {
                            "status": "running",
                            "current_stage": command,
                            "heartbeat_at": _now(),
                            "progress_at": _now(),
                            "message": f"复用已完成阶段: {command}",
                        },
                    )
                    continue
                self._save(
                    root,
                    {
                        "status": "running",
                        "current_stage": command,
                        "heartbeat_at": _now(),
                        "progress_at": _now(),
                        "worker_pid": 0,
                        "message": f"开始执行: {command}",
                    },
                )
                exit_code = runner(command, run_id, root)
                if self._pause.is_set():
                    self._save(root, {"status": "paused", "current_stage": command, "worker_pid": 0})
                    return
                if exit_code != 0:
                    self._save(
                        root,
                        {
                            "status": "failed",
                            "current_stage": command,
                            "worker_pid": 0,
                            "error": f"{command} 执行失败，exit_code={exit_code}",
                        },
                    )
                    return
                if not stage_outputs_ready(root, spec.id):
                    self._save(
                        root,
                        {
                            "status": "failed",
                            "current_stage": command,
                            "worker_pid": 0,
                            "error": f"{command} 返回成功，但阶段产物不完整",
                        },
                    )
                    return
            self._save(
                root,
                {
                    "status": "complete",
                    "current_stage": "",
                    "worker_pid": 0,
                    "heartbeat_at": _now(),
                    "progress_at": _now(),
                    "completed_at": _now(),
                    "message": "完整流程已完成",
                },
            )
        except Exception as exc:
            self._save(root, {"status": "failed", "worker_pid": 0, "error": str(exc)})
        finally:
            with self._lock:
                self._run_root = None
                self._run_id = ""

    def reconcile(self, run_id: str, root: Path, runner: RunStage) -> bool:
        control = self.load(root)
        if control.get("status") == "pausing":
            self._save(root, {"status": "paused", "worker_pid": 0, "message": "服务重启时完成暂停"})
            return False
        if control.get("status") not in {"running", "recovering", "retrying"}:
            return False
        command = str(control.get("current_stage", ""))
        pid = int(control.get("worker_pid", 0) or 0)
        if pid and _pid_alive(pid):
            with self._lock:
                if self._thread and self._thread.is_alive():
                    return False
                self._run_root = root
                self._run_id = run_id
                self._thread = threading.Thread(
                    target=self._monitor_then_resume,
                    args=(pid, run_id, root, runner, command),
                    daemon=True,
                    name=f"pipeline-reconcile-{run_id}",
                )
                self._thread.start()
                return True
        self._save(root, {"status": "interrupted", "worker_pid": 0, "message": "检测到服务中断，正在断点恢复"})
        return self.start(run_id, root, runner, start_command=command)

    def _monitor_then_resume(self, pid: int, run_id: str, root: Path, runner: RunStage, command: str) -> None:
        self._save(root, {"status": "running", "message": f"重新接管仍在运行的进程 {pid}"})
        while _pid_alive(pid) and not self._pause.wait(5):
            self.heartbeat(root, command=command, worker_pid=pid, message="监控重启前遗留进程")
        with self._lock:
            self._thread = None
        if self._pause.is_set():
            self._save(root, {"status": "paused", "worker_pid": 0})
            return
        self.start(run_id, root, runner, start_command=command)
