from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from control_plane import ControlStore, WorkspaceContext


_LOCAL = threading.local()


def _stack() -> list[dict[str, Any]]:
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _LOCAL.stack = stack
    return stack


def _request_metadata_stack() -> list[dict[str, Any]]:
    stack = getattr(_LOCAL, "request_metadata_stack", None)
    if stack is None:
        stack = []
        _LOCAL.request_metadata_stack = stack
    return stack


@contextmanager
def llm_request_metadata(**metadata: Any) -> Iterator[None]:
    """Attach logical inference lineage to the next transport request."""

    value = {
        str(key): item
        for key, item in metadata.items()
        if item is not None and str(key).strip()
    }
    stack = _request_metadata_stack()
    stack.append(value)
    try:
        yield
    finally:
        stack.pop()


def _current_request_metadata() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in _request_metadata_stack():
        merged.update(value)
    return merged


def _transport_parameters(context: WorkspaceContext) -> dict[str, Any]:
    try:
        from config import get_settings

        settings = get_settings(context.root)
    except Exception:
        return {}
    return {
        "provider": settings.provider,
        "base_url": settings.base_url,
        "timeout": settings.timeout,
        "max_retries": settings.max_retries,
        "stream": settings.stream,
        "verify_ssl": settings.verify_ssl,
    }


@contextmanager
def llm_stage_context(
    context: WorkspaceContext,
    operation_id: str | None,
    stage_id: str,
    *,
    capability_id: str,
    prompt_version: str,
    schema_version: str,
    model: str,
    temperature: float,
) -> Iterator[None]:
    operation = str(operation_id or "").strip()
    if not operation:
        yield
        return
    value = {
        "store": ControlStore(context),
        "operation_id": operation,
        "stage_id": stage_id,
        "capability_id": capability_id,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "model": model,
        "temperature": temperature,
        "transport": _transport_parameters(context),
    }
    _stack().append(value)
    try:
        yield
    finally:
        _stack().pop()


@contextmanager
def record_llm_request(
    messages: list[dict[str, str]],
    *,
    parameters: dict[str, Any] | None = None,
) -> Iterator[None]:
    stack = _stack()
    if not stack:
        yield
        return
    context = stack[-1]
    store: ControlStore = context["store"]
    request = store.start_llm_request(
        context["operation_id"],
        context["stage_id"],
        parameters={
            "capability_id": context["capability_id"],
            "prompt_version": context["prompt_version"],
            "schema_version": context["schema_version"],
            "model": context["model"],
            "temperature": context["temperature"],
            **context["transport"],
            **_current_request_metadata(),
            "messages": messages,
            **(parameters or {}),
        },
    )
    request_id = str(request["request_id"])
    try:
        yield
    except Exception as exc:
        store.finish_llm_request(request_id, status="failed", error=str(exc))
        raise
    else:
        store.finish_llm_request(request_id, status="succeeded")
