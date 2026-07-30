"""Trusted persistence for content-addressed model/internal-Skill invocation receipts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from control_plane import ControlStore, WorkspaceContext

from .canonicalization import canonical_hash, canonical_payload_hash
from .kernel_seal import KERNEL_SEAL
from .proposals import InferenceReceipt, InferenceReceiptRef


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class InferenceReceiptService:
    """The only service that may persist inference provenance."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def record(
        self,
        *,
        invocation_id: str | None,
        capability_id: str,
        capability_version: str,
        prompt_version: str,
        prompt_hash: str,
        provider_fingerprint: str,
        model_fingerprint: str,
        temperature: float,
        output_schema_version: str,
        input_artifact_refs: dict[str, dict[str, Any]],
        input_snapshot: str,
        raw_output: str,
        normalized_candidate: Any,
        compiled_payload: dict[str, Any],
    ) -> InferenceReceiptRef:
        receipt = InferenceReceipt(
            workspace_id=self.context.workspace_id,
            invocation_id=invocation_id or uuid4().hex,
            capability_id=capability_id,
            capability_version=capability_version,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            provider_fingerprint=provider_fingerprint,
            model_fingerprint=model_fingerprint,
            temperature=temperature,
            output_schema_version=output_schema_version,
            input_artifact_refs=input_artifact_refs,
            input_snapshot=input_snapshot,
            input_snapshot_hash=canonical_hash(input_snapshot),
            raw_output_hash=canonical_hash(str(raw_output)),
            normalized_candidate_hash=canonical_hash(normalized_candidate),
            compiled_payload_hash=canonical_payload_hash(compiled_payload),
            issued_at=_now(),
        )
        stored = self.store.append_v3_inference_receipt(
            receipt.model_dump(mode="json"),
            kernel_seal=KERNEL_SEAL,
        )
        return InferenceReceiptRef(
            receipt_id=str(stored["receipt_id"]),
            receipt_hash=str(stored["receipt_hash"]),
        )
