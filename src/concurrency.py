from __future__ import annotations

"""Unified worker / LLM concurrency control (PR-A0).

Single source of truth for:
- BID_AGENT_WORKERS_DEFAULT
- BID_AGENT_WORKERS_MAX
- BID_AGENT_LLM_CONCURRENCY

All chapter workers and LLM calls must go through this module so no API/CLI
path can bypass the global caps.
"""

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def workers_max() -> int:
    return max(1, _env_int("BID_AGENT_WORKERS_MAX", 10))


def workers_default() -> int:
    """Default chapter worker count, always clamped to [1, workers_max]."""
    return clamp_workers(_env_int("BID_AGENT_WORKERS_DEFAULT", 4))


def llm_concurrency_limit() -> int:
    """Process-wide concurrent LLM HTTP calls (shared across workspaces)."""
    return max(1, _env_int("BID_AGENT_LLM_CONCURRENCY", 8))


def clamp_workers(value: int | None, *, default: int | None = None) -> int:
    """Clamp user/API/CLI worker input into [1, workers_max]."""
    cap = workers_max()
    if value is None:
        base = workers_default() if default is None else int(default)
    else:
        try:
            base = int(value)
        except (TypeError, ValueError):
            base = workers_default() if default is None else int(default)
    if base < 1:
        base = 1
    if base > cap:
        base = cap
    return base


@dataclass
class ConcurrencyMetrics:
    """Process-wide concurrency telemetry (thread-safe)."""

    active_workers: int = 0
    peak_workers: int = 0
    active_llm: int = 0
    peak_llm: int = 0
    llm_acquire_count: int = 0
    llm_queue_wait_ms_total: float = 0.0
    rate_limit_429_count: int = 0
    load_shed_count: int = 0
    retry_after_backoff_count: int = 0
    last_429_at: float = 0.0
    last_load_shed_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_wait = (
                self.llm_queue_wait_ms_total / self.llm_acquire_count
                if self.llm_acquire_count
                else 0.0
            )
            return {
                "workers_default": workers_default(),
                "workers_max": workers_max(),
                "llm_concurrency": llm_concurrency_limit(),
                "active_workers": self.active_workers,
                "peak_workers": self.peak_workers,
                "active_llm": self.active_llm,
                "peak_llm": self.peak_llm,
                "llm_acquire_count": self.llm_acquire_count,
                "llm_queue_wait_ms_avg": round(avg_wait, 2),
                "rate_limit_429_count": self.rate_limit_429_count,
                "load_shed_count": self.load_shed_count,
                "retry_after_backoff_count": self.retry_after_backoff_count,
                "last_429_at": self.last_429_at or None,
                "last_load_shed_at": self.last_load_shed_at or None,
            }

    def note_workers_start(self, n: int) -> None:
        with self._lock:
            self.active_workers += max(0, int(n))
            if self.active_workers > self.peak_workers:
                self.peak_workers = self.active_workers

    def note_workers_end(self, n: int) -> None:
        with self._lock:
            self.active_workers = max(0, self.active_workers - max(0, int(n)))

    def note_llm_acquire(self, wait_ms: float) -> None:
        with self._lock:
            self.active_llm += 1
            self.llm_acquire_count += 1
            self.llm_queue_wait_ms_total += max(0.0, float(wait_ms))
            if self.active_llm > self.peak_llm:
                self.peak_llm = self.active_llm

    def note_llm_release(self) -> None:
        with self._lock:
            self.active_llm = max(0, self.active_llm - 1)

    def note_429(self) -> None:
        with self._lock:
            self.rate_limit_429_count += 1
            self.last_429_at = time.time()

    def note_load_shed(self) -> None:
        with self._lock:
            self.load_shed_count += 1
            self.last_load_shed_at = time.time()

    def note_backoff(self) -> None:
        with self._lock:
            self.retry_after_backoff_count += 1


_METRICS = ConcurrencyMetrics()
_LLM_SEMAPHORE: threading.Semaphore | None = None
_LLM_SEMAPHORE_LIMIT = 0
_LLM_SEMAPHORE_LOCK = threading.Lock()
_LOAD_SHED_UNTIL = 0.0
_LOAD_SHED_LOCK = threading.Lock()


def metrics() -> ConcurrencyMetrics:
    return _METRICS


def concurrency_snapshot() -> dict[str, Any]:
    return _METRICS.snapshot()


def _llm_semaphore() -> threading.Semaphore:
    """Lazy (re)create global LLM semaphore when limit env changes."""
    global _LLM_SEMAPHORE, _LLM_SEMAPHORE_LIMIT
    limit = llm_concurrency_limit()
    with _LLM_SEMAPHORE_LOCK:
        if _LLM_SEMAPHORE is None or _LLM_SEMAPHORE_LIMIT != limit:
            _LLM_SEMAPHORE = threading.Semaphore(limit)
            _LLM_SEMAPHORE_LIMIT = limit
        return _LLM_SEMAPHORE


def is_load_shedding() -> bool:
    with _LOAD_SHED_LOCK:
        return time.time() < _LOAD_SHED_UNTIL


def trigger_load_shed(seconds: float = 5.0) -> None:
    """Temporarily throttle new LLM acquisitions after 429 storms."""
    global _LOAD_SHED_UNTIL
    delay = max(0.5, float(seconds))
    with _LOAD_SHED_LOCK:
        _LOAD_SHED_UNTIL = max(_LOAD_SHED_UNTIL, time.time() + delay)
    _METRICS.note_load_shed()


def note_rate_limit_429(*, backoff_seconds: float | None = None) -> None:
    _METRICS.note_429()
    _METRICS.note_backoff()
    # Exponential-ish shed: longer if many 429s recently
    snap = _METRICS.snapshot()
    count = int(snap.get("rate_limit_429_count") or 0)
    base = float(backoff_seconds) if backoff_seconds is not None else min(30.0, 2.0 * (2 ** min(count, 4)))
    trigger_load_shed(base)


@contextmanager
def llm_slot() -> Iterator[None]:
    """Acquire a process-wide LLM concurrency slot (blocks if saturated)."""
    if is_load_shedding():
        with _LOAD_SHED_LOCK:
            remaining = max(0.0, _LOAD_SHED_UNTIL - time.time())
        if remaining > 0:
            time.sleep(min(remaining, 5.0))
    sem = _llm_semaphore()
    started = time.perf_counter()
    sem.acquire()
    wait_ms = (time.perf_counter() - started) * 1000.0
    _METRICS.note_llm_acquire(wait_ms)
    try:
        yield
    finally:
        _METRICS.note_llm_release()
        sem.release()


@contextmanager
def chapter_workers_scope(n: int) -> Iterator[int]:
    """Track active chapter worker pool size for metrics."""
    effective = clamp_workers(n)
    _METRICS.note_workers_start(effective)
    try:
        yield effective
    finally:
        _METRICS.note_workers_end(effective)


def reset_for_tests() -> None:
    """Reset process-global concurrency state (tests only)."""
    global _LLM_SEMAPHORE, _LLM_SEMAPHORE_LIMIT, _LOAD_SHED_UNTIL
    with _LLM_SEMAPHORE_LOCK:
        _LLM_SEMAPHORE = None
        _LLM_SEMAPHORE_LIMIT = 0
    with _LOAD_SHED_LOCK:
        _LOAD_SHED_UNTIL = 0.0
    with _METRICS._lock:
        _METRICS.active_workers = 0
        _METRICS.peak_workers = 0
        _METRICS.active_llm = 0
        _METRICS.peak_llm = 0
        _METRICS.llm_acquire_count = 0
        _METRICS.llm_queue_wait_ms_total = 0.0
        _METRICS.rate_limit_429_count = 0
        _METRICS.load_shed_count = 0
        _METRICS.retry_after_backoff_count = 0
        _METRICS.last_429_at = 0.0
        _METRICS.last_load_shed_at = 0.0
