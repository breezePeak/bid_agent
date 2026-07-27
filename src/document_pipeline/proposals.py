"""Immutable proposal and receipt contracts for the V3 trusted write path.

PR-14.0 freezes ProposalEnvelope, ValidationReport, GateReceipt,
PlanningGateReceipt and PromotionReceipt shapes plus content-addressing rules.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonicalization import (
    CANONICALIZATION_VERSION,
    canonical_payload_hash,
    compute_dependency_fingerprint,
    compute_proposal_hash,
    compute_receipt_hash,
)


class DependencyRef(BaseModel):
    """Producer-declared upstream dependency. Kernel re-resolves from active artifacts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_kind: str = Field(min_length=1)
    expected_revision: int | None = Field(default=None, ge=0)
    expected_hash: str | None = None


class ProposalEnvelope(BaseModel):
    """A candidate artifact. It is not a runtime fact until promoted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    workspace_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    producer_role: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    declared_dependencies: list[DependencyRef] = Field(default_factory=list)
    # Producer-declared claim only. Kernel recomputes and compares.
    dependency_fingerprint: str = Field(min_length=1)
    payload: dict[str, Any]
    cited_source_ids: list[str] = Field(default_factory=list)
    prompt_version: str = Field(min_length=1)
    model_fingerprint: str = Field(min_length=1)
    payload_schema_version: str = Field(default="v3", min_length=1)
    canonicalization_version: str = Field(default=CANONICALIZATION_VERSION, min_length=1)

    @field_validator("cited_source_ids")
    @classmethod
    def citations_are_unique_and_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(cleaned) != len(set(cleaned)):
            raise ValueError("cited_source_ids 必须是唯一的非空 ID")
        return cleaned

    def decision_record(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "artifact_kind": self.artifact_kind,
            "producer_role": self.producer_role,
            "operation_id": self.operation_id,
            "base_revision": self.base_revision,
            "declared_dependencies": [item.model_dump(mode="json") for item in self.declared_dependencies],
            "dependency_fingerprint": self.dependency_fingerprint,
            "payload": self.payload,
            "cited_source_ids": list(self.cited_source_ids),
            "prompt_version": self.prompt_version,
            "model_fingerprint": self.model_fingerprint,
            "payload_schema_version": self.payload_schema_version,
            "canonicalization_version": self.canonicalization_version,
        }

    def proposal_hash(self) -> str:
        return compute_proposal_hash(self.decision_record())

    def canonical_payload_hash(self) -> str:
        return canonical_payload_hash(self.payload)

    def storage_record(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["proposal_hash"] = self.proposal_hash()
        value["canonical_payload_hash"] = self.canonical_payload_hash()
        return value

    @classmethod
    def from_storage(cls, record: dict[str, Any]) -> "ProposalEnvelope":
        """Rebuild envelope from append-only store row (trusted path only)."""
        data = {
            "proposal_id": record["proposal_id"],
            "workspace_id": record.get("workspace_id") or "",
            "artifact_kind": record["artifact_kind"],
            "producer_role": record["producer_role"],
            "operation_id": record["operation_id"],
            "base_revision": int(record["base_revision"]),
            "declared_dependencies": record.get("declared_dependencies") or [],
            "dependency_fingerprint": record["dependency_fingerprint"],
            "payload": record["payload"] if isinstance(record.get("payload"), dict) else {},
            "cited_source_ids": record.get("cited_source_ids") or [],
            "prompt_version": record["prompt_version"],
            "model_fingerprint": record["model_fingerprint"],
            "payload_schema_version": record.get("payload_schema_version") or "v3",
            "canonicalization_version": record.get("canonicalization_version") or CANONICALIZATION_VERSION,
        }
        return cls.model_validate(data)


class ValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["info", "warn", "error"] = "error"


class ValidationReport(BaseModel):
    """Validator output bound to an exact stored Proposal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    report_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    workspace_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_hash: str = Field(min_length=1)
    canonical_payload_hash: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    resolved_dependency_snapshot: dict[str, Any] = Field(default_factory=dict)
    dependency_fingerprint: str = Field(min_length=1)
    validator_id: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    schema_valid: bool
    references_valid: bool
    authority_policy_valid: bool
    dependency_current: bool
    findings: list[ValidationFinding] = Field(default_factory=list)
    created_at: str | None = None

    @property
    def passed(self) -> bool:
        return all(
            (
                self.schema_valid,
                self.references_valid,
                self.authority_policy_valid,
                self.dependency_current,
            )
        )

    def compute_report_hash(self) -> str:
        """Content hash of decision fields (not a model field; avoids name clash)."""
        payload = self.model_dump(mode="json", exclude={"created_at"})
        return compute_receipt_hash(payload)

    # Back-compat alias used by existing call sites.
    def report_hash(self) -> str:
        return self.compute_report_hash()


class GateReceipt(BaseModel):
    """Content-addressed gate decision bound to exact Proposal + ValidationReport."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    # Stored content address; never compute via a same-named method.
    receipt_hash: str = Field(default="", min_length=0)
    receipt_subtype: Literal["gate", "planning"] = "gate"
    workspace_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_hash: str = Field(min_length=1)
    validation_report_id: str = Field(min_length=1)
    validation_report_hash: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    resolved_dependency_snapshot: dict[str, Any] = Field(default_factory=dict)
    dependency_fingerprint: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    gate_policy_version: str = Field(min_length=1)
    verdict: Literal["pass", "warn", "block", "needs_human"]
    findings: list[ValidationFinding] = Field(default_factory=list)
    issuer: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    issued_at: str | None = None
    expires_at: str | None = None
    # Backward-compatible alias used by older callers / rows.
    reviewed_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def default_reviewed_revision(self) -> "GateReceipt":
        if self.reviewed_revision is None:
            object.__setattr__(self, "reviewed_revision", self.base_revision)
        return self

    def compute_receipt_content_hash(self) -> str:
        """Hash decision fields only; excludes receipt_id and stored receipt_hash."""
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_hash"})
        return compute_receipt_hash(payload)

    def storage_record(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        if value.get("reviewed_revision") is None:
            value["reviewed_revision"] = self.base_revision
        value["receipt_hash"] = self.compute_receipt_content_hash()
        return value


class PlanningGateReceipt(GateReceipt):
    """Discriminated planning confirmation receipt (H1 or carry-forward)."""

    receipt_subtype: Literal["planning"] = "planning"
    planning_decision: Literal["confirm", "reject", "needs_human", "deterministic_carry_forward"] = "confirm"
    principal_id: str | None = None
    planning_confirmation_scope_hash: str | None = None
    planning_audit_snapshot_hash: str | None = None
    source_h1_receipt_id: str | None = None
    source_h1_receipt_hash: str | None = None
    g2_receipt_id: str | None = None
    g2_receipt_hash: str | None = None
    planning_dag_root_hash: str | None = None
    policy_nonce: str | None = None

    @model_validator(mode="after")
    def planning_confirmation_fields_are_complete(self) -> "PlanningGateReceipt":
        decision = self.planning_decision
        if decision == "reject":
            if not self.principal_id:
                raise ValueError("PlanningGateReceipt reject 必须绑定 principal_id")
            return self
        if decision == "needs_human":
            return self
        # confirm and deterministic_carry_forward share the planning snapshot bindings.
        required_common = {
            "planning_confirmation_scope_hash": self.planning_confirmation_scope_hash,
            "planning_audit_snapshot_hash": self.planning_audit_snapshot_hash,
            "g2_receipt_id": self.g2_receipt_id,
            "g2_receipt_hash": self.g2_receipt_hash,
            "planning_dag_root_hash": self.planning_dag_root_hash,
            "policy_nonce": self.policy_nonce,
        }
        missing = [name for name, value in required_common.items() if not value]
        if missing:
            raise ValueError(f"PlanningGateReceipt({decision}) 缺少字段: {', '.join(missing)}")
        if decision == "confirm" and not self.principal_id:
            raise ValueError("PlanningGateReceipt confirm 必须绑定认证 principal_id")
        if decision == "deterministic_carry_forward":
            if not self.source_h1_receipt_id or not self.source_h1_receipt_hash:
                raise ValueError(
                    "PlanningGateReceipt carry-forward 必须绑定原始人工 H1 receipt id/hash"
                )
            if not self.principal_id:
                raise ValueError("PlanningGateReceipt carry-forward 必须保留原确认用户 principal_id")
        return self


class GateReceiptBinding(BaseModel):
    """Immutable id+hash pair stored on PromotionReceipt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(min_length=1)
    receipt_hash: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)


class PromotionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(min_length=1)
    # Stored content address field — do not name a method receipt_hash().
    receipt_hash: str = Field(default="", min_length=0)
    workspace_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_hash: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    promoted_revision: int = Field(ge=1)
    artifact_hash: str = Field(min_length=1)
    dependency_fingerprint: str = Field(min_length=1)
    resolved_dependency_snapshot: dict[str, Any] = Field(default_factory=dict)
    gate_receipts: list[GateReceiptBinding] = Field(min_length=1)
    # Compatibility projection for callers that still read ids only.
    gate_receipt_ids: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def sync_gate_ids(self) -> "PromotionReceipt":
        if not self.gate_receipt_ids:
            object.__setattr__(self, "gate_receipt_ids", [item.receipt_id for item in self.gate_receipts])
        return self

    def compute_receipt_content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_hash", "created_at"})
        return compute_receipt_hash(payload)

    def with_content_hash(self) -> "PromotionReceipt":
        return self.model_copy(update={"receipt_hash": self.compute_receipt_content_hash()})


def dependency_fingerprint(*parts: Any) -> str:
    """Legacy helper for producers that still assemble declared claim inputs.

    Trusted fingerprint comparison always goes through
    ``compute_dependency_fingerprint`` in the kernel.
    """
    from .canonicalization import canonical_hash

    return canonical_hash(list(parts))


def trusted_dependency_fingerprint(
    *,
    resolved_dependency_snapshot: dict[str, Any],
    schema_version: str,
    policy_version: str,
    prompt_version: str,
    model_fingerprint: str,
    artifact_kind: str,
) -> str:
    return compute_dependency_fingerprint(
        resolved_dependency_snapshot=resolved_dependency_snapshot,
        schema_version=schema_version,
        policy_version=policy_version,
        prompt_version=prompt_version,
        model_fingerprint=model_fingerprint,
        artifact_kind=artifact_kind,
    )


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
