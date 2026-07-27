"""Frozen canonicalization and content-addressing rules for the V3 trusted kernel.

Canonicalization version and field inclusion/exclusion rules are part of the
PR-14.0 contract freeze. Changing them requires a new version and new Gate K
evidence; silent drift is forbidden.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Bump only with an explicit ADR + Gate K re-run.
CANONICALIZATION_VERSION = "v3-canon-1"

# Decision fields included in proposal_hash. Display-only fields are excluded.
PROPOSAL_HASH_FIELDS: tuple[str, ...] = (
    "workspace_id",
    "artifact_kind",
    "producer_role",
    "operation_id",
    "base_revision",
    "declared_dependencies",
    "dependency_fingerprint",
    "payload",
    "cited_source_ids",
    "prompt_version",
    "model_fingerprint",
    "payload_schema_version",
    "canonicalization_version",
)

# Explicitly excluded from proposal_hash (identity / display / non-decision).
PROPOSAL_HASH_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "proposal_id",
        "created_at",
        "status",
        "proposal_hash",
        "canonical_payload_hash",
    }
)

RECEIPT_HASH_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "receipt_id",
        "receipt_hash",
        "created_at",
    }
)


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding used for every content-addressed hash."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    """Hash of the canonical JSON encoding of an arbitrary value."""
    return sha256_hex(canonical_json(value))


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Content address of an artifact payload body only."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    return canonical_hash(payload)


def proposal_decision_document(record: dict[str, Any]) -> dict[str, Any]:
    """Project a proposal storage/API record onto the frozen decision field set.

    The declared ``canonicalization_version`` is part of the decision document so
    version skew changes the proposal hash. Runtime still fail-closes when the
    declared version is not the currently supported CANONICALIZATION_VERSION.
    """
    doc: dict[str, Any] = {}
    for field in PROPOSAL_HASH_FIELDS:
        if field not in record:
            if field == "canonicalization_version":
                doc[field] = CANONICALIZATION_VERSION
                continue
            raise KeyError(f"proposal decision field missing: {field}")
        doc[field] = record[field]
    return doc


def compute_proposal_hash(record: dict[str, Any]) -> str:
    """Trusted proposal hash over decision fields only."""
    return canonical_hash(proposal_decision_document(record))


def receipt_decision_document(record: dict[str, Any], *, exclude: frozenset[str] | None = None) -> dict[str, Any]:
    """Project a receipt onto content-addressable decision fields."""
    banned = RECEIPT_HASH_EXCLUDED_FIELDS if exclude is None else exclude
    return {key: value for key, value in record.items() if key not in banned}


def compute_receipt_hash(record: dict[str, Any]) -> str:
    return canonical_hash(receipt_decision_document(record))


def compute_dependency_fingerprint(
    *,
    resolved_dependency_snapshot: dict[str, Any],
    schema_version: str,
    policy_version: str,
    prompt_version: str,
    model_fingerprint: str,
    artifact_kind: str,
) -> str:
    """Kernel-owned dependency fingerprint. Producers may declare, never self-prove."""
    return canonical_hash(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "artifact_kind": artifact_kind,
            "resolved_dependency_snapshot": resolved_dependency_snapshot,
            "schema_version": schema_version,
            "policy_version": policy_version,
            "prompt_version": prompt_version,
            "model_fingerprint": model_fingerprint,
        }
    )
