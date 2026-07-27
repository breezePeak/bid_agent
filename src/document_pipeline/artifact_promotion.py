"""The only V3 path from a candidate proposal to a promoted artifact.

PR-15.1 trusted kernel rules:
- Validator accepts only proposal_id and reloads the stored Proposal.
- Validation/Gate/Promotion bind exact proposal_hash and dependency snapshot.
- GatePolicyRegistry decides required gates and legal issuers.
- Dependency fingerprints are recomputed by the kernel; producers cannot self-prove.
- CAS active pointer, artifact revision and PromotionReceipt commit atomically.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent.capability_registry import CapabilityRegistry
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .artifact_registry import ARTIFACT_REGISTRY, ArtifactKindRegistry
from .canonicalization import CANONICALIZATION_VERSION, compute_proposal_hash
from .gate_policy_registry import (
    GATE_POLICY_REGISTRY,
    ISSUER_GATE_SERVICE,
    ISSUER_HUMAN_GATE_SERVICE,
    GatePolicyRegistry,
)
from .proposals import (
    GateReceipt,
    GateReceiptBinding,
    PromotionReceipt,
    ProposalEnvelope,
    ValidationFinding,
    ValidationReport,
    trusted_dependency_fingerprint,
)

VALIDATOR_VERSION = "v3-validator-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ProposalValidator:
    """Trusted validator: loads Proposal exclusively from append-only Store."""

    def __init__(
        self,
        context: WorkspaceContext,
        registry: CapabilityRegistry | None = None,
        artifact_registry: ArtifactKindRegistry | None = None,
        gate_policies: GatePolicyRegistry | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.registry = registry or CapabilityRegistry()
        self.artifact_registry = artifact_registry or ARTIFACT_REGISTRY
        self.gate_policies = gate_policies or GATE_POLICY_REGISTRY

    def validate(
        self,
        proposal_id: str,
        *,
        reference_checker: Callable[[str], bool] | None = None,
    ) -> ValidationReport:
        stored = self.store.v3_proposal(proposal_id)
        if stored is None:
            raise ControlPlaneError("V3_PROPOSAL_NOT_FOUND", "Proposal 不存在。", status_code=404)
        if str(stored.get("workspace_id") or self.context.workspace_id) != self.context.workspace_id:
            raise ControlPlaneError("V3_PROPOSAL_WORKSPACE_MISMATCH", "Proposal 不属于当前工作空间。", status_code=409)

        proposal = ProposalEnvelope.from_storage({**stored, "workspace_id": self.context.workspace_id})
        recomputed_hash = proposal.proposal_hash()
        if str(stored.get("proposal_hash") or "") != recomputed_hash:
            raise ControlPlaneError(
                "V3_PROPOSAL_HASH_MISMATCH",
                "Store 中的 Proposal hash 与决策内容不一致。",
                status_code=409,
            )

        findings: list[ValidationFinding] = []
        schema_valid = self._payload_is_valid(proposal, findings)

        try:
            self.registry.authorize_proposal(proposal.producer_role, proposal.artifact_kind)
            registration = self.artifact_registry.require_promotable(proposal.artifact_kind)
            if proposal.producer_role not in registration.legal_producers:
                raise PermissionError(
                    f"V3_CAPABILITY_DENIED: {proposal.producer_role} 不是 {proposal.artifact_kind} 的合法 producer"
                )
            authority_policy_valid = True
        except (PermissionError, KeyError) as exc:
            authority_policy_valid = False
            findings.append(ValidationFinding(code="CAPABILITY_DENIED", message=str(exc)))
            registration = None

        references_valid = True
        for source_id in proposal.cited_source_ids:
            ok = reference_checker(source_id) if reference_checker is not None else bool(source_id)
            if not ok:
                references_valid = False
                findings.append(
                    ValidationFinding(code="REFERENCE_INVALID", message=f"引用无效或不可访问: {source_id}")
                )

        try:
            policy = self.gate_policies.require_policy(proposal.artifact_kind)
        except KeyError as exc:
            findings.append(ValidationFinding(code="GATE_POLICY_UNKNOWN", message=str(exc)))
            policy = None

        resolved_snapshot, dep_findings, dependency_current = self._resolve_dependencies(proposal, registration)
        findings.extend(dep_findings)

        active = self.store.v3_active_artifact(proposal.artifact_kind)
        active_revision = int(active["revision"]) if active is not None else 0
        if proposal.base_revision != active_revision:
            dependency_current = False
            findings.append(
                ValidationFinding(
                    code="BASE_REVISION_STALE",
                    message=f"base_revision={proposal.base_revision} 与 active={active_revision} 不一致。",
                )
            )

        trusted_fp = ""
        if policy is not None:
            trusted_fp = trusted_dependency_fingerprint(
                resolved_dependency_snapshot=resolved_snapshot,
                schema_version=policy.schema_version,
                policy_version=policy.policy_version,
                prompt_version=proposal.prompt_version,
                model_fingerprint=proposal.model_fingerprint,
                artifact_kind=proposal.artifact_kind,
            )
            if proposal.dependency_fingerprint != trusted_fp:
                dependency_current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_FINGERPRINT_MISMATCH",
                        message="producer 声明的 dependency_fingerprint 与可信内核重算结果不一致。",
                    )
                )

        if proposal.canonicalization_version != CANONICALIZATION_VERSION:
            schema_valid = False
            findings.append(
                ValidationFinding(
                    code="CANONICALIZATION_VERSION_MISMATCH",
                    message=f"不支持的 canonicalization_version: {proposal.canonicalization_version}",
                )
            )

        return ValidationReport(
            report_id=uuid4().hex,
            workspace_id=self.context.workspace_id,
            proposal_id=proposal.proposal_id,
            proposal_hash=recomputed_hash,
            canonical_payload_hash=proposal.canonical_payload_hash(),
            artifact_kind=proposal.artifact_kind,
            resolved_dependency_snapshot=resolved_snapshot,
            dependency_fingerprint=trusted_fp or proposal.dependency_fingerprint,
            validator_id=(policy.validator_id if policy is not None else "v3.validator.unknown"),
            validator_version=VALIDATOR_VERSION,
            schema_version=(policy.schema_version if policy is not None else proposal.payload_schema_version),
            policy_version=(policy.policy_version if policy is not None else "unknown"),
            schema_valid=schema_valid,
            references_valid=references_valid,
            authority_policy_valid=authority_policy_valid,
            dependency_current=dependency_current,
            findings=findings,
            created_at=_now(),
        )

    def _resolve_dependencies(
        self,
        proposal: ProposalEnvelope,
        registration: Any,
    ) -> tuple[dict[str, Any], list[ValidationFinding], bool]:
        findings: list[ValidationFinding] = []
        snapshot: dict[str, Any] = {}
        current = True
        required_kinds = list(registration.dependency_kinds) if registration is not None else []
        declared = {item.artifact_kind: item for item in proposal.declared_dependencies}

        for kind in required_kinds:
            active = self.store.v3_active_artifact(kind)
            if active is None:
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_MISSING",
                        message=f"缺少已晋级依赖 {kind}",
                    )
                )
                continue
            if str(active.get("workspace_id") or self.context.workspace_id) not in {"", self.context.workspace_id}:
                current = False
                findings.append(
                    ValidationFinding(code="DEPENDENCY_CROSS_WORKSPACE", message=f"依赖 {kind} 跨工作空间")
                )
                continue
            entry = {
                "artifact_kind": kind,
                "artifact_id": str(active["artifact_id"]),
                "revision": int(active["revision"]),
                "artifact_hash": str(active["artifact_hash"]),
            }
            snapshot[kind] = entry
            declared_ref = declared.get(kind)
            if declared_ref is not None:
                if declared_ref.expected_revision is not None and int(declared_ref.expected_revision) != entry["revision"]:
                    current = False
                    findings.append(
                        ValidationFinding(
                            code="DEPENDENCY_REVISION_MISMATCH",
                            message=f"{kind} 期望 revision={declared_ref.expected_revision} 实际={entry['revision']}",
                        )
                    )
                if declared_ref.expected_hash and declared_ref.expected_hash != entry["artifact_hash"]:
                    current = False
                    findings.append(
                        ValidationFinding(
                            code="DEPENDENCY_HASH_MISMATCH",
                            message=f"{kind} 期望 hash 与 active 不一致",
                        )
                    )

        # Unknown declared kinds (not in policy dependency set) fail closed when they
        # claim a specific promoted revision that cannot be resolved in-workspace.
        for kind, ref in declared.items():
            if kind in snapshot or kind in required_kinds:
                continue
            if ref.expected_revision is None and not ref.expected_hash:
                continue
            active = self.store.v3_active_artifact(kind)
            if active is None:
                current = False
                findings.append(
                    ValidationFinding(code="DEPENDENCY_UNRESOLVED", message=f"声明依赖 {kind} 无法解析")
                )
            else:
                snapshot[kind] = {
                    "artifact_kind": kind,
                    "artifact_id": str(active["artifact_id"]),
                    "revision": int(active["revision"]),
                    "artifact_hash": str(active["artifact_hash"]),
                }
        return snapshot, findings, current

    def _payload_is_valid(self, proposal: ProposalEnvelope, findings: list[ValidationFinding]) -> bool:
        try:
            self.artifact_registry.validate_payload(proposal.artifact_kind, proposal.payload)
            return True
        except KeyError as exc:
            findings.append(ValidationFinding(code="ARTIFACT_KIND_UNKNOWN", message=str(exc)))
            return False
        except PermissionError as exc:
            findings.append(ValidationFinding(code="ARTIFACT_KIND_DISABLED", message=str(exc)))
            return False
        except Exception as exc:  # pydantic ValidationError and ValueError
            findings.append(ValidationFinding(code="PAYLOAD_SCHEMA_INVALID", message=str(exc)))
            return False


class AgentProposalSandbox:
    """Narrow agent facade: submit candidates only; no database or promotion API."""

    def __init__(self, context: WorkspaceContext, role: str, registry: CapabilityRegistry | None = None) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.role = role
        self.registry = registry or CapabilityRegistry()

    def submit(self, proposal: ProposalEnvelope) -> dict[str, Any]:
        if proposal.producer_role != self.role:
            raise PermissionError("V3_CAPABILITY_DENIED: Agent 不得冒用其他角色。")
        if proposal.workspace_id != self.context.workspace_id:
            raise PermissionError("V3_CAPABILITY_DENIED: Agent 不得跨工作空间提交 Proposal。")
        self.registry.authorize_proposal(self.role, proposal.artifact_kind)
        record = proposal.storage_record()
        # Trusted service recomputes hashes at append time.
        record["proposal_hash"] = compute_proposal_hash(proposal.decision_record())
        record["canonical_payload_hash"] = proposal.canonical_payload_hash()
        record["workspace_id"] = self.context.workspace_id
        return self.store.append_v3_proposal(record)


class GateService:
    """Deterministic gate issuer. A passing receipt binds exact proposal content."""

    def __init__(
        self,
        context: WorkspaceContext,
        gate_policies: GatePolicyRegistry | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.gate_policies = gate_policies or GATE_POLICY_REGISTRY

    def evaluate(self, proposal_id: str, *, gate_id: str, reviewer: str = "system") -> GateReceipt:
        proposal_row = self.store.v3_proposal(proposal_id)
        if proposal_row is None:
            raise ControlPlaneError("V3_PROPOSAL_NOT_FOUND", "Proposal 不存在。", status_code=404)
        if str(proposal_row.get("workspace_id") or self.context.workspace_id) != self.context.workspace_id:
            raise ControlPlaneError("V3_PROPOSAL_WORKSPACE_MISMATCH", "Proposal 不属于当前工作空间。", status_code=409)

        report_raw = self.store.v3_validation_report(proposal_id)
        if report_raw is None:
            raise ControlPlaneError("V3_GATE_FORBIDDEN", "Proposal 尚未完成验证。", status_code=409)
        report = ValidationReport.model_validate(report_raw)
        if report.proposal_hash != str(proposal_row["proposal_hash"]):
            raise ControlPlaneError("V3_GATE_STALE", "ValidationReport 未绑定当前 Proposal hash。", status_code=409)
        if report.workspace_id != self.context.workspace_id:
            raise ControlPlaneError("V3_GATE_WORKSPACE_MISMATCH", "ValidationReport 跨工作空间。", status_code=409)

        artifact_kind = str(proposal_row["artifact_kind"])
        try:
            requirement = self.gate_policies.resolve_gate_requirement(artifact_kind, gate_id)
        except KeyError as exc:
            raise ControlPlaneError("V3_GATE_UNKNOWN", str(exc), status_code=400) from exc

        if requirement.human_required:
            issuer = ISSUER_HUMAN_GATE_SERVICE
            if not reviewer or reviewer in {"system", "agent", "stage_runner", "gate_service"}:
                raise ControlPlaneError(
                    "V3_GATE_ISSUER_DENIED",
                    "人工 Gate 需要已认证用户 principal，禁止 system/agent 代签。",
                    status_code=403,
                )
        else:
            issuer = ISSUER_GATE_SERVICE
            if issuer not in requirement.allowed_issuers:
                raise ControlPlaneError("V3_GATE_ISSUER_DENIED", f"issuer {issuer} 不被允许。", status_code=403)

        # Fail closed on failed validation for automated promotion gates.
        if not report.passed and not requirement.human_required:
            verdict: str = "block"
        elif not report.passed and requirement.human_required:
            verdict = "needs_human"
        else:
            verdict = "pass" if report.passed else "block"

        # Unknown findings never promote as pass.
        if any(item.severity == "error" for item in report.findings) and verdict == "pass":
            verdict = "block"

        receipt = GateReceipt(
            workspace_id=self.context.workspace_id,
            proposal_id=proposal_id,
            proposal_hash=str(proposal_row["proposal_hash"]),
            validation_report_id=report.report_id,
            validation_report_hash=report.report_hash(),
            artifact_kind=artifact_kind,
            base_revision=int(proposal_row["base_revision"]),
            resolved_dependency_snapshot=report.resolved_dependency_snapshot,
            dependency_fingerprint=report.dependency_fingerprint,
            gate_id=gate_id,
            gate_policy_version=self.gate_policies.VERSION,
            verdict=verdict,  # type: ignore[arg-type]
            findings=report.findings,
            issuer=issuer,
            reviewer=reviewer,
            issued_at=_now(),
            expires_at=None,
            reviewed_revision=int(proposal_row["base_revision"]),
        )
        stored = self.store.issue_v3_gate_receipt(receipt.storage_record())
        merged = receipt.model_dump(mode="json")
        for key in GateReceipt.model_fields:
            if key in stored and stored[key] is not None:
                merged[key] = stored[key]
        return GateReceipt.model_validate(merged)


class ArtifactPromotionService:
    """Service-owned CAS promotion; agents cannot obtain this capability."""

    def __init__(
        self,
        context: WorkspaceContext,
        gate_policies: GatePolicyRegistry | None = None,
        artifact_registry: ArtifactKindRegistry | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.gate_policies = gate_policies or GATE_POLICY_REGISTRY
        self.artifact_registry = artifact_registry or ARTIFACT_REGISTRY

    def promote(self, proposal_id: str, gate_receipt_ids: list[str]) -> PromotionReceipt:
        return PromotionReceipt.model_validate(
            self.store.promote_v3_proposal(
                proposal_id=proposal_id,
                gate_receipt_ids=gate_receipt_ids,
                workspace_id=self.context.workspace_id,
                gate_policy_registry=self.gate_policies,
                artifact_registry=self.artifact_registry,
            )
        )


def validate_and_record(
    context: WorkspaceContext,
    proposal_id: str,
    *,
    reference_checker: Callable[[str], bool] | None = None,
    # Deprecated: callers must not pass payload. Kept only to fail closed if misused.
    proposal: ProposalEnvelope | None = None,
    expected_dependency_fingerprint: str | None = None,
) -> ValidationReport:
    """Trusted entry: validate exactly the Proposal stored under proposal_id."""
    if proposal is not None and proposal.proposal_id != proposal_id and proposal_id:
        raise ControlPlaneError(
            "V3_VALIDATION_PAYLOAD_REJECTED",
            "Validator 禁止调用方同时传入与 proposal_id 不一致的 payload。",
            status_code=400,
        )
    # Ignore any caller-supplied proposal body; always reload from Store.
    target_id = proposal_id or (proposal.proposal_id if proposal is not None else "")
    if not target_id:
        raise ControlPlaneError("V3_VALIDATION_INVALID", "缺少 proposal_id。", status_code=400)
    if expected_dependency_fingerprint is not None:
        # Explicitly unused: kernel recomputes dependencies. Presence is tolerated for
        # migration of call sites but never trusted as authority.
        pass

    report = ProposalValidator(context).validate(target_id, reference_checker=reference_checker)
    ControlStore(context).record_v3_validation_report(
        target_id,
        report.model_dump(mode="json"),
        proposal_hash=report.proposal_hash,
        report_hash=report.report_hash(),
    )
    return report


# Re-export binding helper used by tests when constructing declared claims.
def build_declared_dependency_fingerprint(
    *,
    resolved_dependency_snapshot: dict[str, Any],
    artifact_kind: str,
    prompt_version: str,
    model_fingerprint: str,
    schema_version: str = "v3",
    policy_version: str = GATE_POLICY_REGISTRY.VERSION,
) -> str:
    return trusted_dependency_fingerprint(
        resolved_dependency_snapshot=resolved_dependency_snapshot,
        schema_version=schema_version,
        policy_version=policy_version,
        prompt_version=prompt_version,
        model_fingerprint=model_fingerprint,
        artifact_kind=artifact_kind,
    )
