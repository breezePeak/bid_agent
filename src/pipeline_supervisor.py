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
StatusListener = Callable[[Path, dict[str, Any]], None]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    """Return True if process appears alive. Never raise (Windows-safe)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    # Windows: os.kill(pid, 0) can raise SystemError / WinError 87 on stale PIDs.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, wintypes.DWORD(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            err = ctypes.get_last_error()
            # 5 = ACCESS_DENIED => process exists
            if err == 5:
                return True
            return False
        except Exception:
            # last resort: never crash startup
            try:
                os.kill(pid, 0)
                return True
            except Exception:
                return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


class PipelineSupervisor:
    """Process-local supervisor with a durable control record per workspace."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._run_root: Path | None = None
        self._run_id = ""
        self._operation_id = ""
        self._fencing_token = 0
        self._status_listener: StatusListener | None = None

    def set_status_listener(self, listener: StatusListener | None) -> None:
        with self._lock:
            self._status_listener = listener

    @staticmethod
    def control_path(root: Path) -> Path:
        return root / "workspace" / "pipeline_control.json"

    def load(self, root: Path) -> dict[str, Any]:
        with self._lock:
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
            temp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            listener = self._status_listener
        if listener:
            try:
                listener(root, dict(current))
            except Exception:
                # V1 compatibility: a control-plane projection failure must not
                # corrupt the deterministic runner's own durable control file.
                pass
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

    def start(
        self,
        run_id: str,
        root: Path,
        runner: RunStage,
        *,
        start_command: str = "",
        operation_id: str = "",
        fencing_token: int = 0,
        single_command: bool = False,
    ) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._pause.clear()
            self._cancel.clear()
            self._run_root = root
            self._run_id = run_id
            self._operation_id = operation_id
            self._fencing_token = int(fencing_token or 0)
            self._save(
                root,
                {
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "fencing_token": self._fencing_token,
                    "status": "running",
                    "requested_action": "run_stage" if single_command else "run_all",
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
                args=(run_id, root, runner, start_command, single_command),
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

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._run_root:
                self._save(self._run_root, {"status": "cancelling", "message": "正在取消流水线"})

    def _loop(
        self,
        run_id: str,
        root: Path,
        runner: RunStage,
        start_command: str,
        single_command: bool = False,
    ) -> None:
        commands = auto_run_commands()
        if single_command and start_command in commands:
            commands = [start_command]
        elif start_command in commands:
            commands = commands[commands.index(start_command) :]
        try:
            for command in commands:
                if self._cancel.is_set():
                    self._save(root, {"status": "cancelled", "current_stage": command, "worker_pid": 0})
                    return
                if self._pause.is_set():
                    self._save(root, {"status": "paused", "current_stage": command, "worker_pid": 0})
                    return
                # quality gate: open block issues stop progression before next stage
                try:
                    from agent.issues import can_proceed

                    gate = can_proceed(root, next_command=command)
                    if not gate.get("can_proceed", True):
                        self._save(
                            root,
                            {
                                "status": "failed",
                                "current_stage": command,
                                "worker_pid": 0,
                                "error": gate.get("message") or f"质量门禁阻断，禁止执行 {command}",
                                "message": gate.get("message") or "质量门禁阻断",
                            },
                        )
                        return
                except Exception:
                    pass
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
                if self._cancel.is_set():
                    self._save(root, {"status": "cancelled", "current_stage": command, "worker_pid": 0})
                    return
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
                self._operation_id = ""
                self._fencing_token = 0

    def reconcile(self, run_id: str, root: Path, runner: RunStage) -> bool:
        control = self.load(root)
        operation_id = str(control.get("operation_id") or "")
        fencing_token = int(control.get("fencing_token") or 0)
        single_command = str(control.get("requested_action") or "") == "run_stage"
        if control.get("status") == "pausing":
            self._save(root, {"status": "paused", "worker_pid": 0, "message": "服务重启时完成暂停"})
            return False
        if control.get("status") not in {"running", "recovering", "retrying"}:
            return False
        command = str(control.get("current_stage", ""))
        try:
            pid = int(control.get("worker_pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        # Stale Windows PID from previous boot can be invalid; never crash here.
        alive = False
        try:
            alive = bool(pid and _pid_alive(pid))
        except Exception:
            alive = False
            pid = 0
        if alive:
            with self._lock:
                if self._thread and self._thread.is_alive():
                    return False
                self._run_root = root
                self._run_id = run_id
                self._operation_id = operation_id
                self._fencing_token = fencing_token
                self._thread = threading.Thread(
                    target=self._monitor_then_resume,
                    args=(pid, run_id, root, runner, command, operation_id, fencing_token, single_command),
                    daemon=True,
                    name=f"pipeline-reconcile-{run_id}",
                )
                self._thread.start()
                return True
        self._save(root, {"status": "interrupted", "worker_pid": 0, "message": "检测到服务中断，正在断点恢复"})
        return self.start(
            run_id,
            root,
            runner,
            start_command=command,
            operation_id=operation_id,
            fencing_token=fencing_token,
            single_command=single_command,
        )

    def _monitor_then_resume(
        self,
        pid: int,
        run_id: str,
        root: Path,
        runner: RunStage,
        command: str,
        operation_id: str = "",
        fencing_token: int = 0,
        single_command: bool = False,
    ) -> None:
        self._save(root, {"status": "running", "message": f"重新接管仍在运行的进程 {pid}"})
        while _pid_alive(pid) and not self._pause.wait(5) and not self._cancel.is_set():
            self.heartbeat(root, command=command, worker_pid=pid, message="监控重启前遗留进程")
        with self._lock:
            self._thread = None
        if self._cancel.is_set():
            self._save(root, {"status": "cancelled", "worker_pid": 0})
            return
        if self._pause.is_set():
            self._save(root, {"status": "paused", "worker_pid": 0})
            return
        self.start(
            run_id,
            root,
            runner,
            start_command=command,
            operation_id=operation_id,
            fencing_token=fencing_token,
            single_command=single_command,
        )
