from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


VALIDATION_FAILURE_BLOCKS_ENV = (
    "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE"
)
_ACTIVE_VALIDATION_FAILURE_BLOCKS: ContextVar[bool | None] = ContextVar(
    "bid_agent_validation_failure_blocks",
    default=None,
)


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def configured_validation_failure_blocks() -> bool:
    """MVP policy: validation findings are warnings and never block execution."""

    return False


def validation_failure_blocks() -> bool:
    """Return the command-scoped policy; MVP always continues on warnings."""

    active = _ACTIVE_VALIDATION_FAILURE_BLOCKS.get()
    return False if active is None else active


def validation_warnings_allowed() -> bool:
    return not validation_failure_blocks()


@contextmanager
def validation_policy_scope(blocks: bool) -> Iterator[None]:
    token = _ACTIVE_VALIDATION_FAILURE_BLOCKS.set(bool(blocks))
    try:
        yield
    finally:
        _ACTIVE_VALIDATION_FAILURE_BLOCKS.reset(token)
