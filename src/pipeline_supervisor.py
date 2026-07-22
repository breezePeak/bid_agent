from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pipeline_registry import auto_run_commands, stage_outputs_ready, stage_spec_by_command


RunStage = Callable[[str, str, Path], int]
StatusListener = Callable[[Path, dict[str, Any]], None]
GateEvaluator = Callable[[Path, str], dict[str, Any]]
ArtifactRecorder = Callable[[Path, str, str], None]
ArtifactReadinessEvaluator = Callable[[Path, str], bool]
StageLifecycleRecorder = Callable[[Path, str, str, str, dict[str, Any] | None], None]


@dataclass
class _RunSlot:
    root: Path
    run_id: str
    operation_id: str = ""
    fencing_token: int = 0
    thread: threading.Thread | None = None
    pause: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)


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
        self._slots: dict[Path, _RunSlot] = {}
        self._status_listener: StatusListener | None = None

    @staticmethod
    def _slot_key(root: Path) -> Path:
        return root.resolve()

    def _active_slots(self) -> list[_RunSlot]:
        return [slot for slot in self._slots.values() if slot.thread and slot.thread.is_alive()]

    def _slot_for_control(self, root: Path | None = None) -> _RunSlot | None:
        if root is not None:
            slot = self._slots.get(self._slot_key(root))
            return slot if slot and slot.thread and slot.thread.is_alive() else None
        active = self._active_slots()
        return active[0] if len(active) == 1 else None

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
            for attempt in range(5):
                try:
                    temp.replace(path)
                    break
                except PermissionError:
                    if attempt >= 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
            listener = self._status_listener
        if listener:
            try:
                listener(root, dict(current))
            except Exception as exc:
                raise RuntimeError(f"control state sync failed: {exc}") from exc
        return current

    def is_running(self, root: Path | None = None) -> bool:
        with self._lock:
            if root is not None:
                return self._slot_for_control(root) is not None
            return bool(self._active_slots())

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
        gate_evaluator: GateEvaluator | None = None,
        artifact_recorder: ArtifactRecorder | None = None,
        artifact_readiness_evaluator: ArtifactReadinessEvaluator | None = None,
        stage_lifecycle_recorder: StageLifecycleRecorder | None = None,
    ) -> bool:
        with self._lock:
            key = self._slot_key(root)
            existing = self._slots.get(key)
            if existing and existing.thread and existing.thread.is_alive():
                return False
            slot = _RunSlot(
                root=root,
                run_id=run_id,
                operation_id=operation_id,
                fencing_token=int(fencing_token or 0),
            )
            self._slots[key] = slot
            self._save(
                root,
                {
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "fencing_token": slot.fencing_token,
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
            slot.thread = threading.Thread(
                target=self._loop,
                args=(slot, runner, start_command, single_command, gate_evaluator, artifact_recorder, artifact_readiness_evaluator, stage_lifecycle_recorder),
                daemon=True,
                name=f"pipeline-{run_id}",
            )
            slot.thread.start()
            return True

    def pause(self, root: Path | None = None) -> None:
        with self._lock:
            slot = self._slot_for_control(root)
            if slot:
                slot.pause.set()
                self._save(slot.root, {"status": "pausing", "message": "正在暂停流水线"})

    def cancel(self, root: Path | None = None) -> None:
        with self._lock:
            slot = self._slot_for_control(root)
            if slot:
                slot.cancel.set()
                self._save(slot.root, {"status": "cancelling", "message": "正在取消流水线"})

    def _loop(
        self,
        slot: _RunSlot,
        runner: RunStage,
        start_command: str,
        single_command: bool = False,
        gate_evaluator: GateEvaluator | None = None,
        artifact_recorder: ArtifactRecorder | None = None,
        artifact_readiness_evaluator: ArtifactReadinessEvaluator | None = None,
        stage_lifecycle_recorder: StageLifecycleRecorder | None = None,
    ) -> None:
        run_id = slot.run_id
        root = slot.root
        commands = auto_run_commands()
        if single_command and start_command in commands:
            commands = [start_command]
        elif start_command in commands:
            commands = commands[commands.index(start_command) :]
        def record_stage(
            command: str,
            status: str,
            disposition: str = "",
            error: str = "",
        ) -> None:
            if stage_lifecycle_recorder is not None:
                stage_lifecycle_recorder(
                    root,
                    command,
                    status,
                    disposition,
                    {"message": error} if error else None,
                )
        try:
            for command in commands:
                record_stage(command, "queued", "queued")
                if slot.cancel.is_set():
                    record_stage(command, "cancelled", "cancelled_before_start")
                    self._save(root, {"status": "cancelled", "current_stage": command, "worker_pid": 0})
                    return
                if slot.pause.is_set():
                    record_stage(command, "paused", "paused_before_start")
                    self._save(root, {"status": "paused", "current_stage": command, "worker_pid": 0})
                    return
                # quality gate: open block issues stop progression before next stage
                try:
                    if gate_evaluator is not None:
                        gate = gate_evaluator(root, command)
                    else:
                        from agent.issues import can_proceed

                        gate = can_proceed(root, next_command=command)
                except Exception as exc:
                    if gate_evaluator is None:
                        gate = {"can_proceed": True}
                    else:
                        message = f"质量门禁状态不可用，已拒绝执行: {exc}"
                        record_stage(command, "failed", "gate_unavailable", message)
                        self._save(
                            root,
                            {
                                "status": "failed",
                                "current_stage": command,
                                "worker_pid": 0,
                                "error": message,
                                "message": "质量门禁状态不可用，已拒绝执行",
                            },
                        )
                        return
                if not gate.get("can_proceed", gate_evaluator is None):
                    message = gate.get("message") or f"质量门禁阻断，禁止执行 {command}"
                    record_stage(command, "failed", "gate_blocked", str(message))
                    self._save(
                        root,
                        {
                            "status": "failed",
                            "current_stage": command,
                            "worker_pid": 0,
                            "error": message,
                            "message": gate.get("message") or "质量门禁阻断",
                        },
                    )
                    return
                spec = stage_spec_by_command(command)
                outputs_ready = stage_outputs_ready(root, spec.id)
                if outputs_ready and artifact_readiness_evaluator is not None:
                    try:
                        outputs_ready = artifact_readiness_evaluator(root, command)
                    except Exception as exc:
                        message = f"Artifact readiness 状态不可用，已拒绝复用: {exc}"
                        record_stage(command, "failed", "readiness_unavailable", message)
                        self._save(
                            root,
                            {
                                "status": "failed",
                                "current_stage": command,
                                "worker_pid": 0,
                                "error": message,
                            },
                        )
                        return
                if outputs_ready:
                    if artifact_recorder is not None:
                        try:
                            artifact_recorder(root, command, "reused")
                        except Exception as exc:
                            message = f"Artifact manifest 记录失败: {exc}"
                            record_stage(command, "failed", "artifact_record_failed", message)
                            self._save(
                                root,
                                {
                                    "status": "failed",
                                    "current_stage": command,
                                    "worker_pid": 0,
                                    "error": message,
                                },
                            )
                            return
                    record_stage(command, "reused", "reused")
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
                record_stage(command, "running", "started")
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
                if slot.cancel.is_set():
                    record_stage(command, "cancelled", "cancelled")
                    self._save(root, {"status": "cancelled", "current_stage": command, "worker_pid": 0})
                    return
                if slot.pause.is_set():
                    record_stage(command, "paused", "paused")
                    self._save(root, {"status": "paused", "current_stage": command, "worker_pid": 0})
                    return
                if exit_code != 0:
                    message = f"{command} 执行失败，exit_code={exit_code}"
                    record_stage(command, "failed", "runner_failed", message)
                    self._save(
                        root,
                        {
                            "status": "failed",
                            "current_stage": command,
                            "worker_pid": 0,
                            "error": message,
                        },
                    )
                    return
                if not stage_outputs_ready(root, spec.id):
                    message = f"{command} 返回成功，但阶段产物不完整"
                    record_stage(command, "failed", "outputs_incomplete", message)
                    self._save(
                        root,
                        {
                            "status": "failed",
                            "current_stage": command,
                            "worker_pid": 0,
                            "error": message,
                        },
                    )
                    return
                if artifact_recorder is not None:
                    try:
                        artifact_recorder(root, command, "produced")
                    except Exception as exc:
                        message = f"Artifact manifest 记录失败: {exc}"
                        record_stage(command, "failed", "artifact_record_failed", message)
                        self._save(
                            root,
                            {
                                "status": "failed",
                                "current_stage": command,
                                "worker_pid": 0,
                                "error": message,
                            },
                        )
                        return
                record_stage(command, "succeeded", "produced")
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
                key = self._slot_key(root)
                if self._slots.get(key) is slot:
                    self._slots.pop(key, None)

    def reconcile(
        self,
        run_id: str,
        root: Path,
        runner: RunStage,
        *,
        gate_evaluator: GateEvaluator | None = None,
        artifact_recorder: ArtifactRecorder | None = None,
        artifact_readiness_evaluator: ArtifactReadinessEvaluator | None = None,
        stage_lifecycle_recorder: StageLifecycleRecorder | None = None,
    ) -> bool:
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
                key = self._slot_key(root)
                existing = self._slots.get(key)
                if existing and existing.thread and existing.thread.is_alive():
                    return False
                slot = _RunSlot(
                    root=root,
                    run_id=run_id,
                    operation_id=operation_id,
                    fencing_token=fencing_token,
                )
                self._slots[key] = slot
                slot.thread = threading.Thread(
                    target=self._monitor_then_resume,
                    args=(slot, pid, runner, command, single_command, gate_evaluator, artifact_recorder, artifact_readiness_evaluator, stage_lifecycle_recorder),
                    daemon=True,
                    name=f"pipeline-reconcile-{run_id}",
                )
                slot.thread.start()
                return True
        self._save(root, {"status": "interrupted", "worker_pid": 0, "message": "检测到服务中断，正在断点恢复"})
        if command and stage_lifecycle_recorder is not None:
            try:
                stage_lifecycle_recorder(
                    root,
                    command,
                    "failed",
                    "worker_lost",
                    {"message": "检测到服务中断，原 Worker 未报告阶段终态。"},
                )
            except Exception as exc:
                self._save(
                    root,
                    {
                        "status": "failed",
                        "current_stage": command,
                        "worker_pid": 0,
                        "error": f"StageRun 中断审计失败: {exc}",
                    },
                )
                return False
        return self.start(
            run_id,
            root,
            runner,
            start_command=command,
            operation_id=operation_id,
            fencing_token=fencing_token,
            single_command=single_command,
            gate_evaluator=gate_evaluator,
            artifact_recorder=artifact_recorder,
            artifact_readiness_evaluator=artifact_readiness_evaluator,
            stage_lifecycle_recorder=stage_lifecycle_recorder,
        )

    def _monitor_then_resume(
        self,
        slot: _RunSlot,
        pid: int,
        runner: RunStage,
        command: str,
        single_command: bool = False,
        gate_evaluator: GateEvaluator | None = None,
        artifact_recorder: ArtifactRecorder | None = None,
        artifact_readiness_evaluator: ArtifactReadinessEvaluator | None = None,
        stage_lifecycle_recorder: StageLifecycleRecorder | None = None,
    ) -> None:
        root = slot.root
        self._save(root, {"status": "running", "message": f"重新接管仍在运行的进程 {pid}"})
        while _pid_alive(pid) and not slot.pause.wait(5) and not slot.cancel.is_set():
            self.heartbeat(root, command=command, worker_pid=pid, message="监控重启前遗留进程")
        with self._lock:
            key = self._slot_key(root)
            if self._slots.get(key) is slot:
                self._slots.pop(key, None)
        if slot.cancel.is_set():
            if command:
                stage_lifecycle_recorder and stage_lifecycle_recorder(root, command, "cancelled", "cancelled", None)
            self._save(root, {"status": "cancelled", "worker_pid": 0})
            return
        if slot.pause.is_set():
            if command:
                stage_lifecycle_recorder and stage_lifecycle_recorder(root, command, "paused", "paused", None)
            self._save(root, {"status": "paused", "worker_pid": 0})
            return
        if command and stage_lifecycle_recorder is not None:
            try:
                stage_lifecycle_recorder(
                    root,
                    command,
                    "failed",
                    "worker_lost",
                    {"message": f"重启接管的 Worker {pid} 已退出但未报告阶段终态。"},
                )
            except Exception as exc:
                self._save(
                    root,
                    {
                        "status": "failed",
                        "current_stage": command,
                        "worker_pid": 0,
                        "error": f"StageRun 中断审计失败: {exc}",
                    },
                )
                return
        self.start(
            slot.run_id,
            root,
            runner,
            start_command=command,
            operation_id=slot.operation_id,
            fencing_token=slot.fencing_token,
            single_command=single_command,
            gate_evaluator=gate_evaluator,
            artifact_recorder=artifact_recorder,
            artifact_readiness_evaluator=artifact_readiness_evaluator,
            stage_lifecycle_recorder=stage_lifecycle_recorder,
        )
