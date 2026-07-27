"""Immutable proposal and receipt contracts for the V3 trusted write path."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProposalEnvelope(BaseModel):
    """A candidate artifact. It is not a runtime fact until promoted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    artifact_kind: str = Field(min_length=1)
    producer_role: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    dependency_fingerprint: str = Field(min_length=1)
    payload: dict[str, Any]
    cited_source_ids: list[str] = Field(default_factory=list)
    prompt_version: str = Field(min_length=1)
    model_fingerprint: str = Field(min_length=1)

    @field_validator("cited_source_ids")
    @classmethod
    def citations_are_unique_and_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(cleaned) != len(set(cleaned)):
            raise ValueError("cited_source_ids 必须是唯一的非空 ID")
        return cleaned

    def proposal_hash(self) -> str:
        """Hash only the decision content, never the generated proposal ID."""
        value = self.model_dump(mode="json", exclude={"proposal_id"})
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def storage_record(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["proposal_hash"] = self.proposal_hash()
        return value


class ValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["info", "warn", "error"] = "error"


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(min_length=1)
    schema_valid: bool
    references_valid: bool
    authority_policy_valid: bool
    dependency_current: bool
    findings: list[ValidationFinding] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all((self.schema_valid, self.references_valid, self.authority_policy_valid, self.dependency_current))


class GateReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_hash: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    verdict: Literal["pass", "warn", "block", "needs_human"]
    findings: list[ValidationFinding] = Field(default_factory=list)
    reviewer: str = Field(min_length=1)
    reviewed_revision: int = Field(ge=0)


class PromotionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    promoted_revision: int = Field(ge=1)
    artifact_hash: str = Field(min_length=1)
    dependency_fingerprint: str = Field(min_length=1)
    gate_receipt_ids: list[str] = Field(min_length=1)
    created_at: str = Field(min_length=1)


def dependency_fingerprint(*parts: Any) -> str:
    """Stable fingerprint for frozen upstream revisions, prompts and model config."""
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ExtractionProposalPayload(BaseModel):
    """Payload schema for candidate RequirementLedger extraction proposals."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirements: list[dict[str, Any]] = Field(default_factory=list)
    reconciled_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    audit_summary: dict[str, Any] = Field(default_factory=dict)


class ScoreModelProposalPayload(BaseModel):
    """Payload schema for a candidate ScoreModel proposal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    score_model: dict[str, Any]
    audit_summary: dict[str, Any] = Field(default_factory=dict)
