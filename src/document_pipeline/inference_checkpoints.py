"""Durable, content-addressed checkpoints for validated inference candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .canonicalization import canonical_hash, canonical_json


@dataclass(frozen=True, slots=True)
class InferenceCheckpointHit:
    checkpoint_id: str
    cache_key: str
    created_at: str
    operation_id: str
    record: dict[str, Any]


class InferenceCheckpointService:
    def __init__(self, context: WorkspaceContext) -> None:
        self.store = ControlStore(context)

    @staticmethod
    def cache_key(
        *,
        capability_id: str,
        capability_version: str,
        prompt_version: str,
        prompt_hash: str,
        schema_version: str,
        provider_fingerprint: str,
        model_fingerprint: str,
        temperature: float,
        input_snapshot: Any,
    ) -> str:
        snapshot = (
            input_snapshot.model_dump(mode="json")
            if isinstance(input_snapshot, BaseModel)
            else input_snapshot
        )
        return canonical_hash(
            {
                "capability_id": capability_id,
                "capability_version": capability_version,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "schema_version": schema_version,
                "provider_fingerprint": provider_fingerprint,
                "model_fingerprint": model_fingerprint,
                "temperature": float(temperature),
                "input_snapshot": snapshot,
            }
        )

    def load(
        self,
        *,
        cache_key: str,
        candidate_model: type[BaseModel],
        expected_input_snapshot: Any,
    ) -> InferenceCheckpointHit | None:
        checkpoint = self.store.latest_v3_inference_checkpoint(cache_key)
        if checkpoint is None:
            return None
        record = dict(checkpoint.get("record") or {})
        expected = canonical_json(
            expected_input_snapshot.model_dump(mode="json")
            if isinstance(expected_input_snapshot, BaseModel)
            else expected_input_snapshot
        )
        if str(record.get("input_snapshot") or "") != expected:
            raise ControlPlaneError(
                "V3_INFERENCE_CHECKPOINT_INVALID",
                "推理断点输入快照损坏或类型不匹配，已停止自动重试。",
                status_code=409,
                details={"checkpoint_id": checkpoint.get("checkpoint_id")},
            )
        try:
            candidate = candidate_model.model_validate(
                record.get("candidate"),
                strict=True,
            )
        except Exception as exc:
            raise ControlPlaneError(
                "V3_INFERENCE_CHECKPOINT_INVALID",
                "推理断点候选损坏或类型不匹配，已停止自动重试。",
                status_code=409,
                details={"checkpoint_id": checkpoint.get("checkpoint_id")},
            ) from exc
        record["candidate"] = candidate
        checkpoint_id = str(checkpoint["checkpoint_id"])
        self.store.use_v3_inference_checkpoint(checkpoint_id)
        return InferenceCheckpointHit(
            checkpoint_id=checkpoint_id,
            cache_key=str(cache_key),
            created_at=str(checkpoint.get("created_at") or ""),
            operation_id=str(checkpoint.get("operation_id") or ""),
            record=record,
        )

    def save(
        self,
        *,
        cache_key: str,
        operation_id: str,
        result: Any,
        capability_version: str,
        supersede_existing: bool = False,
    ) -> dict[str, Any]:
        candidate = result.candidate
        record = {
            "candidate": (
                candidate.model_dump(mode="json")
                if isinstance(candidate, BaseModel)
                else candidate
            ),
            "raw_output": str(result.raw_output),
            "normalized_output": str(result.normalized_output),
            "reasoning": str(getattr(result, "reasoning", "") or ""),
            "input_snapshot": str(result.input_snapshot),
            "attempt_count": int(result.attempt_count),
            "capability_id": str(result.capability_id),
            "capability_version": str(capability_version),
            "prompt_version": str(result.prompt_version),
            "prompt_hash": str(result.prompt_hash),
            "schema_version": str(result.schema_version),
            "provider_fingerprint": str(result.provider_fingerprint),
            "model_fingerprint": str(result.model_fingerprint),
            "temperature": float(result.temperature),
            "warnings": list(getattr(result, "warnings", ()) or ()),
            "normalized_reference_count": int(
                getattr(result, "normalized_reference_count", 0) or 0
            ),
            "validation_errors": list(
                getattr(result, "validation_errors", ()) or ()
            ),
        }
        return self.store.append_v3_inference_checkpoint(
            cache_key=cache_key,
            capability_id=str(result.capability_id),
            operation_id=str(operation_id or ""),
            record=record,
            supersede_existing=supersede_existing,
        )

    def record_postprocess_error(self, checkpoint_id: str, error: BaseException) -> None:
        self.store.record_v3_inference_checkpoint_error(checkpoint_id, str(error))
