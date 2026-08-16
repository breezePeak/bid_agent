"""The only V3 path from a candidate proposal to a promoted artifact.

PR-15.1 trusted kernel rules:
- Validator accepts only proposal_id and reloads the stored Proposal.
- Validation/Gate/Promotion bind exact proposal_hash and dependency snapshot.
- GatePolicyRegistry decides required gates and legal issuers.
- Dependency fingerprints are recomputed by the kernel; producers cannot self-prove.
- CAS active pointer, artifact revision and PromotionReceipt commit atomically.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent.capability_registry import CapabilityRegistry
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .artifact_registry import ARTIFACT_REGISTRY, ArtifactKindRegistry
from .canonicalization import CANONICALIZATION_VERSION, canonical_hash, compute_proposal_hash
from .gate_policy_registry import (
    GATE_POLICY_REGISTRY,
    ISSUER_GATE_SERVICE,
    ISSUER_HUMAN_GATE_SERVICE,
    GatePolicyRegistry,
)
from .kernel_seal import KERNEL_SEAL
from .inference_runtime import (
    INFERENCE_RUNTIME_REGISTRY,
    metadata_from_provider,
)
from .pipeline_policy import validation_warnings_allowed
from .proposals import (
    GateReceipt,
    GateReceiptBinding,
    PlanningGateReceipt,
    PromotionReceipt,
    ProposalEnvelope,
    ValidationFinding,
    ValidationReport,
    trusted_dependency_fingerprint,
)

VALIDATOR_VERSION = "v3-validator-4"


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

        checker = reference_checker or (lambda source_id: self._source_id_exists(source_id))
        references_valid = True
        for source_id in proposal.cited_source_ids:
            ok = bool(checker(source_id))
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
        inference_valid = self._inference_receipts_are_valid(
            proposal,
            resolved_snapshot,
            findings,
        )
        if not inference_valid:
            schema_valid = False
        if schema_valid:
            schema_valid = self._payload_domain_is_valid(proposal, findings)

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
        optional_kinds = set(
            registration.optional_dependency_kinds
            if registration is not None
            else ()
        )
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
            if declared_ref is None:
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_DECLARATION_MISSING",
                        message=f"必需依赖 {kind} 未出现在 declared_dependencies",
                    )
                )
                continue
            if (
                declared_ref.expected_revision is None
                or not declared_ref.expected_hash
            ):
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_REQUIRED_UNPINNED",
                        message=(
                            f"必需依赖 {kind} 必须同时声明 exact revision/hash"
                        ),
                    )
                )
            if (
                declared_ref.expected_revision is not None
                and int(declared_ref.expected_revision) != entry["revision"]
            ):
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_REVISION_MISMATCH",
                        message=(
                            f"{kind} 期望 revision="
                            f"{declared_ref.expected_revision} "
                            f"实际={entry['revision']}"
                        ),
                    )
                )
            if (
                declared_ref.expected_hash
                and declared_ref.expected_hash != entry["artifact_hash"]
            ):
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_HASH_MISMATCH",
                        message=f"{kind} 期望 hash 与 active 不一致",
                    )
                )

        # Optional dependencies are decision inputs only when the producer
        # explicitly declares an active exact revision/hash.
        for kind, ref in declared.items():
            if kind in snapshot or kind in required_kinds:
                continue
            if kind not in optional_kinds:
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_KIND_UNDECLARED",
                        message=f"{proposal.artifact_kind} 不允许声明依赖 {kind}",
                    )
                )
                continue
            if ref.expected_revision is None or not ref.expected_hash:
                current = False
                findings.append(
                    ValidationFinding(
                        code="DEPENDENCY_OPTIONAL_UNPINNED",
                        message=f"可选依赖 {kind} 必须声明 exact revision/hash",
                    )
                )
                continue
            active = self.store.v3_active_artifact(kind)
            if active is None:
                current = False
                findings.append(
                    ValidationFinding(code="DEPENDENCY_UNRESOLVED", message=f"声明依赖 {kind} 无法解析")
                )
            else:
                entry = {
                    "artifact_kind": kind,
                    "artifact_id": str(active["artifact_id"]),
                    "revision": int(active["revision"]),
                    "artifact_hash": str(active["artifact_hash"]),
                }
                snapshot[kind] = entry
                if (
                    ref.expected_revision is not None
                    and int(ref.expected_revision) != entry["revision"]
                ):
                    current = False
                    findings.append(
                        ValidationFinding(
                            code="DEPENDENCY_REVISION_MISMATCH",
                            message=(
                                f"{kind} 期望 revision={ref.expected_revision} "
                                f"实际={entry['revision']}"
                            ),
                        )
                    )
                if (
                    ref.expected_hash
                    and ref.expected_hash != entry["artifact_hash"]
                ):
                    current = False
                    findings.append(
                        ValidationFinding(
                            code="DEPENDENCY_HASH_MISMATCH",
                            message=f"{kind} 期望 hash 与 active 不一致",
                        )
                    )
        return snapshot, findings, current

    def _inference_receipts_are_valid(
        self,
        proposal: ProposalEnvelope,
        resolved_snapshot: dict[str, Any],
        findings: list[ValidationFinding],
    ) -> bool:
        """Bind semantic proposals to an immutable, exact inference invocation."""

        required_capability = {
            "ScoreModel": "score.semantic_reconcile",
            "ProjectModel": "planning.project_understanding",
            "ResponseTopicGraph": "planning.topic_duty_plan",
            "ChapterBlueprint": "planning.chapter_outline_split",
        }.get(proposal.artifact_kind)
        if required_capability is None:
            if proposal.inference_receipt_refs:
                findings.append(
                    ValidationFinding(
                        code="INFERENCE_RECEIPT_UNEXPECTED",
                        message=f"{proposal.artifact_kind} 不允许声明推理凭证",
                    )
                )
                return False
            return True
        if len(proposal.inference_receipt_refs) != 1:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_REQUIRED",
                    message=f"{proposal.artifact_kind} 必须绑定且只能绑定一个推理凭证",
                )
            )
            return False

        from .canonicalization import canonical_payload_hash
        from .planning_inference import (
            OUTLINE_CAPABILITY_VERSION,
            PROJECT_CAPABILITY_VERSION,
            TOPIC_CAPABILITY_VERSION,
        )
        from .proposals import InferenceReceipt
        from .score_semantic import SCORE_SEMANTIC_CAPABILITY_VERSION

        required_capability_version = {
            "ScoreModel": SCORE_SEMANTIC_CAPABILITY_VERSION,
            "ProjectModel": PROJECT_CAPABILITY_VERSION,
            "ResponseTopicGraph": TOPIC_CAPABILITY_VERSION,
            "ChapterBlueprint": OUTLINE_CAPABILITY_VERSION,
        }[proposal.artifact_kind]

        ref = proposal.inference_receipt_refs[0]
        stored = self.store.v3_inference_receipt(ref.receipt_id)
        if stored is None:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_NOT_FOUND",
                    message=f"推理凭证不存在: {ref.receipt_id}",
                )
            )
            return False
        if str(stored.get("receipt_hash") or "") != ref.receipt_hash:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_HASH_MISMATCH",
                    message=f"推理凭证 hash 不匹配: {ref.receipt_id}",
                )
            )
            return False
        try:
            receipt = InferenceReceipt.model_validate(
                {
                    key: value
                    for key, value in stored.items()
                    if key not in {"receipt_hash", "created_at"}
                }
            )
        except Exception as exc:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_INVALID",
                    message=f"推理凭证无法解析: {exc}",
                )
            )
            return False
        valid = True
        if receipt.compute_receipt_hash() != ref.receipt_hash:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_TAMPERED",
                    message=f"推理凭证内容校验失败: {ref.receipt_id}",
                )
            )
            valid = False
        if not receipt.input_snapshot_hash_is_valid():
            findings.append(
                ValidationFinding(
                    code="INFERENCE_INPUT_CONTENT_HASH_MISMATCH",
                    message="推理凭证中的 exact input_snapshot 与其 hash 不一致",
                )
            )
            valid = False
        try:
            from .inference_inputs import (
                reconstruct_inference_input_snapshot,
            )

            dependency_payloads: dict[str, dict[str, Any]] = {}
            for kind, expected_ref in resolved_snapshot.items():
                active_dependency = self.store.v3_active_artifact(kind)
                if active_dependency is None:
                    raise ValueError(f"缺少 active dependency: {kind}")
                actual_ref = {
                    "artifact_kind": kind,
                    "artifact_id": str(active_dependency["artifact_id"]),
                    "revision": int(active_dependency["revision"]),
                    "artifact_hash": str(
                        active_dependency["artifact_hash"]
                    ),
                }
                if actual_ref != expected_ref:
                    raise ValueError(
                        f"active dependency 在输入重建期间变化: {kind}"
                    )
                dependency_payloads[kind] = dict(
                    active_dependency["payload"]
                )
            reconstructed_input = reconstruct_inference_input_snapshot(
                self.context,
                artifact_kind=proposal.artifact_kind,
                proposal_payload=proposal.payload,
                dependency_payloads=dependency_payloads,
            )
        except Exception as exc:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_INPUT_RECONSTRUCTION_FAILED",
                    message=(
                        "Validator 无法从 exact active dependencies 独立重建 "
                        f"Provider 输入: {exc}"
                    ),
                )
            )
            valid = False
        else:
            if receipt.input_snapshot != reconstructed_input:
                findings.append(
                    ValidationFinding(
                        code="INFERENCE_EXACT_INPUT_MISMATCH",
                        message=(
                            "推理凭证保存的 input_snapshot 不等于 Validator "
                            "从受控依赖独立重建的精确 Provider 输入"
                        ),
                    )
                )
                valid = False
        if receipt.workspace_id != self.context.workspace_id:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RECEIPT_WORKSPACE_MISMATCH",
                    message="推理凭证不属于当前工作空间",
                )
            )
            valid = False
        if receipt.capability_id != required_capability:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_CAPABILITY_MISMATCH",
                    message=(
                        f"{proposal.artifact_kind} 需要 {required_capability}，"
                        f"凭证实际为 {receipt.capability_id}"
                    ),
                )
            )
            valid = False
        if receipt.capability_version != required_capability_version:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_CAPABILITY_VERSION_MISMATCH",
                    message=(
                        f"{proposal.artifact_kind} 需要 capability version "
                        f"{required_capability_version}，凭证实际为 "
                        f"{receipt.capability_version}"
                    ),
                )
            )
            valid = False
        if (
            receipt.prompt_version != proposal.prompt_version
            or receipt.model_fingerprint != proposal.model_fingerprint
        ):
            findings.append(
                ValidationFinding(
                    code="INFERENCE_DECISION_FINGERPRINT_MISMATCH",
                    message="推理凭证与 Proposal 的 Prompt/模型指纹不一致",
                )
            )
            valid = False
        expected_runtime = INFERENCE_RUNTIME_REGISTRY.metadata(
            self.context,
            proposal.artifact_kind,
        )
        if expected_runtime is None:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_RUNTIME_METADATA_MISSING",
                    message=(
                        "缺少当前受控 Provider/Prompt/模型/Schema 运行时注册，"
                        "禁止验证推理 Proposal"
                    ),
                )
            )
            valid = False
        else:
            runtime_fields = (
                "capability_id",
                "capability_version",
                "prompt_version",
                "prompt_hash",
                "provider_fingerprint",
                "model_fingerprint",
                "output_schema_version",
                "temperature",
            )
            if any(
                str(getattr(receipt, field_name))
                != str(expected_runtime[field_name])
                for field_name in runtime_fields
            ):
                findings.append(
                    ValidationFinding(
                        code="INFERENCE_RUNTIME_METADATA_MISMATCH",
                        message=(
                            "推理凭证与当前受控 Provider/Prompt/模型/Schema "
                            "运行时注册不一致"
                        ),
                    )
                )
                valid = False
        if receipt.compiled_payload_hash != canonical_payload_hash(proposal.payload):
            findings.append(
                ValidationFinding(
                    code="INFERENCE_OUTPUT_MISMATCH",
                    message="推理凭证未绑定当前 Proposal payload",
                )
            )
            valid = False
        if receipt.input_artifact_refs != resolved_snapshot:
            findings.append(
                ValidationFinding(
                    code="INFERENCE_INPUT_SNAPSHOT_MISMATCH",
                    message="推理凭证未绑定 Proposal 的 exact active dependency snapshot",
                )
            )
            valid = False
        return valid

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

    def _payload_domain_is_valid(
        self,
        proposal: ProposalEnvelope,
        findings: list[ValidationFinding],
    ) -> bool:
        """Run artifact-specific policy against the exact stored Proposal payload."""

        try:
            if proposal.artifact_kind == "ScoreModel":
                from .contracts import RequirementLedger, ScoreModel, SourceIndex
                from .score_model import (
                    audit_score_model,
                    partition_score_model_audit,
                )

                ledger_artifact = self.store.v3_active_artifact("RequirementLedger")
                source_artifact = self.store.v3_active_artifact("SourceIndex")
                if ledger_artifact is None or source_artifact is None:
                    raise ValueError("ScoreModel 领域校验缺少已晋级 RequirementLedger 或 SourceIndex")
                score_audit = audit_score_model(
                    ScoreModel.model_validate(proposal.payload),
                    RequirementLedger.model_validate(ledger_artifact["payload"]),
                    SourceIndex.model_validate(source_artifact["payload"]).blocks,
                    require_semantic=True,
                )
                blocking_findings, _ = partition_score_model_audit(score_audit)
                audit = {
                    "passed": not blocking_findings,
                    **blocking_findings,
                }
            elif proposal.artifact_kind == "ProjectModel":
                from .contracts import (
                    ProjectModel,
                    RequirementLedger,
                    ScoreModel,
                    SourceIndex,
                )
                from .project_model import audit_project_model

                ledger_artifact = self.store.v3_active_artifact(
                    "RequirementLedger"
                )
                score_artifact = self.store.v3_active_artifact("ScoreModel")
                source_artifact = self.store.v3_active_artifact("SourceIndex")
                if (
                    ledger_artifact is None
                    or score_artifact is None
                    or source_artifact is None
                ):
                    raise ValueError(
                        "ProjectModel 领域校验缺少已晋级 RequirementLedger、"
                        "ScoreModel 或 SourceIndex"
                    )
                audit = audit_project_model(
                    ProjectModel.model_validate(proposal.payload),
                    RequirementLedger.model_validate(
                        ledger_artifact["payload"]
                    ),
                    ScoreModel.model_validate(score_artifact["payload"]),
                    SourceIndex.model_validate(source_artifact["payload"]),
                )
            elif proposal.artifact_kind == "ResponseTopicGraph":
                from .contracts import (
                    ProjectModel,
                    RequirementLedger,
                    ResponseTopicGraph,
                    ScoreModel,
                    SourceIndex,
                )
                from .topic_graph import audit_response_topic_graph

                ledger_artifact = self.store.v3_active_artifact(
                    "RequirementLedger"
                )
                score_artifact = self.store.v3_active_artifact("ScoreModel")
                project_artifact = self.store.v3_active_artifact(
                    "ProjectModel"
                )
                source_artifact = self.store.v3_active_artifact("SourceIndex")
                if (
                    ledger_artifact is None
                    or score_artifact is None
                    or project_artifact is None
                    or source_artifact is None
                ):
                    raise ValueError(
                        "ResponseTopicGraph 领域校验缺少已晋级 "
                        "RequirementLedger、ScoreModel、ProjectModel 或 SourceIndex"
                    )
                audit = audit_response_topic_graph(
                    ScoreModel.model_validate(score_artifact["payload"]),
                    ResponseTopicGraph.model_validate(proposal.payload),
                    requirement_ledger=RequirementLedger.model_validate(
                        ledger_artifact["payload"]
                    ),
                    project_model=ProjectModel.model_validate(
                        project_artifact["payload"]
                    ),
                    source_index=SourceIndex.model_validate(
                        source_artifact["payload"]
                    ),
                )
            elif proposal.artifact_kind == "ChapterBlueprint":
                from .chapter_blueprint import (
                    audit_chapter_blueprint,
                    partition_chapter_blueprint_audit,
                )
                from .contracts import (
                    ChapterBlueprint,
                    DocumentMode,
                    RequirementLedger,
                    ResponseTopicGraph,
                    ScoreModel,
                    TemplateStructureContract,
                )

                ledger_artifact = self.store.v3_active_artifact(
                    "RequirementLedger"
                )
                graph_artifact = self.store.v3_active_artifact(
                    "ResponseTopicGraph"
                )
                score_artifact = self.store.v3_active_artifact("ScoreModel")
                if score_artifact is None:
                    raise ValueError(
                        "ChapterBlueprint 领域校验缺少已晋级 ScoreModel"
                    )
                blueprint = ChapterBlueprint.model_validate(proposal.payload)
                template_artifact = self.store.v3_active_artifact(
                    "TemplateStructureContract"
                )
                template_structure = (
                    TemplateStructureContract.model_validate(
                        template_artifact["payload"]
                    )
                    if template_artifact is not None
                    else None
                )
                score_model = ScoreModel.model_validate(
                    score_artifact["payload"]
                )
                if blueprint.planning_model == "score_direct":
                    if ledger_artifact is None:
                        raise ValueError(
                            "score_direct ChapterBlueprint 领域校验缺少已晋级 "
                            "RequirementLedger"
                        )
                    audit = audit_chapter_blueprint(
                        blueprint,
                        RequirementLedger.model_validate(
                            ledger_artifact["payload"]
                        ),
                        score_model=score_model,
                        template_structure=template_structure,
                    )
                else:
                    if graph_artifact is None:
                        raise ValueError(
                            "topic_graph ChapterBlueprint 领域校验缺少已晋级 "
                            "ResponseTopicGraph"
                        )
                    audit = audit_chapter_blueprint(
                        blueprint,
                        ResponseTopicGraph.model_validate(
                            graph_artifact["payload"]
                        ),
                        score_model=score_model,
                        template_structure=template_structure,
                    )
                if blueprint.mode is DocumentMode.TEMPLATE_STRICT:
                    template_ref = next(
                        (
                            item
                            for item in proposal.declared_dependencies
                            if item.artifact_kind
                            == "TemplateStructureContract"
                        ),
                        None,
                    )
                    template_findings = audit.setdefault("findings", [])
                    if template_artifact is None:
                        # The policy audit already records the missing contract.
                        pass
                    elif (
                        template_ref is None
                        or template_ref.expected_revision is None
                        or not template_ref.expected_hash
                    ):
                        template_findings.append(
                            {
                                "code": "TEMPLATE_DEPENDENCY_NOT_EXACT",
                                "message": (
                                    "template_strict Blueprint 必须声明当前 "
                                    "TemplateStructureContract 的 exact revision/hash"
                                ),
                            }
                        )
                    elif (
                        int(template_ref.expected_revision)
                        != int(template_artifact["revision"])
                        or template_ref.expected_hash
                        != str(template_artifact["artifact_hash"])
                    ):
                        template_findings.append(
                            {
                                "code": "TEMPLATE_DEPENDENCY_STALE",
                                "message": (
                                    "Blueprint 声明的 TemplateStructureContract "
                                    "revision/hash 与当前活动模板不一致"
                                ),
                            }
                        )
                    if (
                        isinstance(template_findings, list)
                        and template_findings
                    ):
                        audit["passed"] = False
                is_controlled_audit_fallback = bool(
                    blueprint.coverage_summary.get("program_audit_warning")
                    and blueprint.coverage_summary.get("needs_human")
                    and blueprint.coverage_summary.get("review_status")
                    == "needs_review"
                    and str(proposal.prompt_version).endswith(
                        ".program_audit_fallback.v2"
                    )
                )
                if is_controlled_audit_fallback and not bool(
                    audit.get("passed")
                ):
                    blocking_findings, _ = (
                        partition_chapter_blueprint_audit(audit)
                    )
                    audit = {
                        "passed": not blocking_findings,
                        "findings": blocking_findings,
                    }
            else:
                return True
        except Exception as exc:
            findings.append(
                ValidationFinding(
                    code="DOMAIN_VALIDATION_FAILED",
                    message=f"{proposal.artifact_kind} 领域校验无法完成: {exc}",
                )
            )
            return False

        if bool(audit.get("passed")):
            return True
        if validation_warnings_allowed():
            findings.append(
                ValidationFinding(
                    code="DOMAIN_POLICY_WARNING",
                    message=(
                        f"{proposal.artifact_kind} 领域校验未通过；"
                        "当前任务配置为记录风险并继续。"
                    ),
                    severity="warn",
                )
            )
            return True
        finding_count = len(findings)
        audit_findings = audit.get("findings")
        if isinstance(audit_findings, list):
            for item in audit_findings:
                if not isinstance(item, dict):
                    continue
                findings.append(
                    ValidationFinding(
                        code=str(item.get("code") or "DOMAIN_POLICY_BLOCKED"),
                        message=str(item.get("message") or "领域策略校验未通过"),
                    )
                )
        else:
            for key, value in audit.items():
                if key == "passed" or not value:
                    continue
                findings.append(
                    ValidationFinding(
                        code=f"DOMAIN_{str(key).upper()}",
                        message=f"{proposal.artifact_kind} {key}: {value}",
                    )
                )
        if len(findings) == finding_count:
            findings.append(
                ValidationFinding(
                    code="DOMAIN_POLICY_BLOCKED",
                    message=f"{proposal.artifact_kind} 领域策略校验未通过",
                )
            )
        return False

    def _source_id_exists(self, source_id: str) -> bool:
        """Fail closed: cited sources must resolve to a known workspace input."""
        if not str(source_id or "").strip():
            return False
        try:
            from .input_manifest import InputManifestService

            manifest = InputManifestService(self.context).load()
            known = {item.input_id for item in manifest.inputs}
            return source_id in known
        except Exception:
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
        stored = self.store.issue_v3_gate_receipt(receipt.storage_record(), kernel_seal=KERNEL_SEAL)
        merged = receipt.model_dump(mode="json")
        for key in GateReceipt.model_fields:
            if key in stored and stored[key] is not None:
                merged[key] = stored[key]
        return GateReceipt.model_validate(merged)


class HumanGateService:
    """Issue H1 only after an authenticated user explicitly confirms one frozen plan."""

    _SCORE_DIRECT_PLANNING_DEPENDENCIES = (
        "InputManifest",
        "SourceIndex",
        "RequirementLedger",
        "ScoreModel",
        "ChapterBlueprint",
    )
    _LEGACY_PLANNING_DEPENDENCIES = (
        "InputManifest",
        "SourceIndex",
        "RequirementLedger",
        "ScoreModel",
        "ProjectModel",
        "ResponseTopicGraph",
        "ChapterBlueprint",
    )

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def planning_snapshot(self) -> dict[str, Any]:
        blueprint = self.store.v3_active_artifact("ChapterBlueprint")
        if blueprint is None:
            raise ControlPlaneError(
                "PLANNING_CONFIRM_BLOCKED",
                "H1 缺少已晋级 Artifact: ChapterBlueprint",
                status_code=409,
            )
        blueprint_payload = (
            blueprint["payload"]
            if isinstance(blueprint.get("payload"), dict)
            else {}
        )
        # Older score-direct blueprints did not persist planning_model.  They
        # must remain confirmable: requiring the legacy topic graph dependencies
        # hides the confirmation CTA and leaves the workspace in a dead end.
        planning_model = str(blueprint_payload.get("planning_model") or "")
        if not planning_model:
            planning_model = (
                "score_direct"
                if self.store.v3_active_artifact("ProjectModel") is None
                else "topic_graph"
            )
        dependency_kinds = (
            self._SCORE_DIRECT_PLANNING_DEPENDENCIES
            if planning_model == "score_direct"
            else self._LEGACY_PLANNING_DEPENDENCIES
        )
        dependencies: dict[str, dict[str, Any]] = {}
        for kind in dependency_kinds:
            artifact = self.store.v3_active_artifact(kind)
            if artifact is None:
                raise ControlPlaneError("PLANNING_CONFIRM_BLOCKED", f"H1 缺少已晋级 Artifact: {kind}", status_code=409)
            dependencies[kind] = {
                "artifact_id": str(artifact["artifact_id"]),
                "revision": int(artifact["revision"]),
                "hash": str(artifact["artifact_hash"]),
            }
        template = self.store.v3_active_artifact("TemplateStructureContract")
        if template is not None:
            dependencies["TemplateStructureContract"] = {
                "artifact_id": str(template["artifact_id"]),
                "revision": int(template["revision"]),
                "hash": str(template["artifact_hash"]),
            }
        g2 = self.store.latest_v3_gate_receipt(str(blueprint["proposal_id"]), "G2_BLUEPRINT_INTEGRITY")
        if not g2 or g2.get("verdict") != "pass":
            raise ControlPlaneError("PLANNING_CONFIRM_BLOCKED", "H1 缺少当前 ChapterBlueprint 的 G2 通过 Receipt。", status_code=409)
        generation_trace = self._generation_trace(planning_model)
        scope = self._scope_snapshot(blueprint["payload"])
        audit = {
            "dependencies": dependencies,
            "blueprint": blueprint["payload"],
            "g2_receipt": {"receipt_id": g2["receipt_id"], "receipt_hash": g2["receipt_hash"]},
            "generation_trace": generation_trace,
            "scope": scope,
        }
        return {
            "dependencies": dependencies,
            "generation_trace": generation_trace,
            "planning_dag_root_hash": canonical_hash(dependencies),
            "planning_confirmation_scope_hash": canonical_hash(scope),
            "planning_audit_snapshot_hash": canonical_hash(audit),
            "g2_receipt_id": str(g2["receipt_id"]),
            "g2_receipt_hash": str(g2["receipt_hash"]),
        }

    def _generation_trace(
        self,
        planning_model: str,
    ) -> list[dict[str, Any]]:
        """Expose the exact semantic invocations that produced the frozen plan."""

        expected_runtime = self._current_inference_metadata(planning_model)
        trace: list[dict[str, Any]] = []
        artifact_kinds = (
            ("ScoreModel", "ChapterBlueprint")
            if planning_model == "score_direct"
            else (
                "ScoreModel",
                "ProjectModel",
                "ResponseTopicGraph",
                "ChapterBlueprint",
            )
        )
        for artifact_kind in artifact_kinds:
            artifact = self.store.v3_active_artifact(artifact_kind)
            if artifact is None:
                raise ControlPlaneError(
                    "PLANNING_CONFIRM_BLOCKED",
                    f"H1 缺少已晋级 Artifact: {artifact_kind}",
                    status_code=409,
                )
            proposal = self.store.v3_proposal(str(artifact["proposal_id"]))
            refs = (proposal or {}).get("inference_receipt_refs") or []
            if len(refs) != 1:
                raise ControlPlaneError(
                    "PLANNING_CONFIRM_BLOCKED",
                    f"{artifact_kind} 缺少唯一推理凭证。",
                    status_code=409,
                )
            ref = refs[0]
            receipt = self.store.v3_inference_receipt(str(ref.get("receipt_id") or ""))
            if receipt is None or str(receipt.get("receipt_hash") or "") != str(ref.get("receipt_hash") or ""):
                raise ControlPlaneError(
                    "PLANNING_CONFIRM_BLOCKED",
                    f"{artifact_kind} 推理凭证不存在或已失配。",
                    status_code=409,
                )
            expected = expected_runtime.get(artifact_kind)
            if expected is not None and any(
                str(
                    receipt.get(field)
                    if receipt.get(field) is not None
                    else ""
                )
                != str(expected[field])
                for field in (
                    "capability_id",
                    "capability_version",
                    "prompt_version",
                    "prompt_hash",
                    "provider_fingerprint",
                    "model_fingerprint",
                    "output_schema_version",
                    "temperature",
                )
            ):
                raise ControlPlaneError(
                    "PLANNING_CONFIRM_STALE",
                    f"{artifact_kind} 的推理凭证不再匹配当前 "
                    "Capability/Prompt/模型/Schema，请重新规划。",
                    status_code=409,
                )
            trace.append(
                {
                    "artifact_kind": artifact_kind,
                    "artifact_id": str(artifact["artifact_id"]),
                    "artifact_hash": str(artifact["artifact_hash"]),
                    "receipt_id": str(receipt["receipt_id"]),
                    "receipt_hash": str(receipt["receipt_hash"]),
                    "invocation_id": str(receipt["invocation_id"]),
                    "capability_id": str(receipt["capability_id"]),
                    "capability_version": str(receipt["capability_version"]),
                    "prompt_version": str(receipt["prompt_version"]),
                    "prompt_hash": str(receipt["prompt_hash"]),
                    "provider_fingerprint": str(
                        receipt["provider_fingerprint"]
                    ),
                    "model_fingerprint": str(receipt["model_fingerprint"]),
                    "input_snapshot_hash": str(
                        receipt["input_snapshot_hash"]
                    ),
                    "output_schema_version": str(receipt["output_schema_version"]),
                    "temperature": float(receipt["temperature"]),
                }
            )
        return trace

    def _current_inference_metadata(
        self,
        planning_model: str,
    ) -> dict[str, dict[str, str | float]]:
        """Resolve deployment-time inference policy for automatic H1 invalidation."""

        runtime_mode = (
            os.environ.get("BID_AGENT_INFERENCE_MODE", "llm")
            .strip()
            .lower()
        )
        required_kinds = (
            {"ScoreModel", "ChapterBlueprint"}
            if planning_model == "score_direct"
            else {
                "ScoreModel",
                "ProjectModel",
                "ResponseTopicGraph",
                "ChapterBlueprint",
            }
        )
        registered = INFERENCE_RUNTIME_REGISTRY.snapshot(self.context)
        if required_kinds.issubset(registered):
            return {
                kind: {
                    field: value
                    for field, value in registered[kind].items()
                    if field != "runtime_mode"
                }
                for kind in required_kinds
            }
        if runtime_mode == "deterministic_test":
            raise ControlPlaneError(
                "PLANNING_CONFIRM_BLOCKED",
                "当前测试推理 Provider 运行时元数据不完整，禁止跳过 H1 "
                "推理新鲜度校验。",
                status_code=409,
            )
        try:
            from .planning_inference import LLMOutlineDecompositionProvider
            from .score_semantic import LLMScoreSemanticProvider

            providers = {
                "ScoreModel": LLMScoreSemanticProvider(),
                "ChapterBlueprint": LLMOutlineDecompositionProvider(),
            }
            if planning_model != "score_direct":
                from .planning_inference import (
                    LLMProjectUnderstandingProvider,
                    LLMTopicDutyPlanningProvider,
                )

                providers.update(
                    {
                        "ProjectModel": LLMProjectUnderstandingProvider(),
                        "ResponseTopicGraph": LLMTopicDutyPlanningProvider(),
                    }
                )
            resolved: dict[str, dict[str, str | float]] = {}
            for artifact_kind, provider in providers.items():
                metadata = metadata_from_provider(
                    provider,
                    runtime_mode=runtime_mode,
                )
                INFERENCE_RUNTIME_REGISTRY.publish(
                    self.context,
                    artifact_kind,
                    metadata,
                )
                resolved[artifact_kind] = {
                    field: value
                    for field, value in metadata.as_dict().items()
                    if field != "runtime_mode"
                }
            return resolved
        except Exception as exc:
            raise ControlPlaneError(
                "PLANNING_CONFIRM_BLOCKED",
                "无法解析当前推理 Capability/Prompt/模型/Schema，"
                "禁止沿用旧规划确认。",
                status_code=409,
            ) from exc

    def confirm_planning(self, *, principal_id: str, submitted_snapshot: dict[str, Any], nonce: str) -> PlanningGateReceipt:
        principal = str(principal_id or "").strip()
        if not principal:
            raise ControlPlaneError("AUTH_REQUIRED", "H1 需要已认证用户 principal。", status_code=401)
        self.store.require_workspace_access(principal, write=True)
        expected = self.planning_snapshot()
        required = (
            "planning_dag_root_hash",
            "planning_confirmation_scope_hash",
            "planning_audit_snapshot_hash",
            "g2_receipt_id",
            "g2_receipt_hash",
        )
        if any(str(submitted_snapshot.get(key) or "") != expected[key] for key in required):
            raise ControlPlaneError("PLANNING_CONFIRM_STALE", "规划快照已变化，请重新审阅后确认。", status_code=409)
        blueprint = self.store.v3_active_artifact("ChapterBlueprint")
        assert blueprint is not None
        proposal = self.store.v3_proposal(str(blueprint["proposal_id"]))
        if proposal is None:
            raise ControlPlaneError("PLANNING_CONFIRM_BLOCKED", "ChapterBlueprint Proposal 不存在。", status_code=409)
        report_raw = self.store.v3_validation_report(str(blueprint["proposal_id"]))
        if report_raw is None:
            raise ControlPlaneError("PLANNING_CONFIRM_BLOCKED", "ChapterBlueprint 尚未完成验证。", status_code=409)
        report = ValidationReport.model_validate(report_raw)
        receipt = PlanningGateReceipt(
            workspace_id=self.context.workspace_id,
            proposal_id=str(blueprint["proposal_id"]),
            proposal_hash=str(blueprint["proposal_hash"]),
            validation_report_id=report.report_id,
            validation_report_hash=report.compute_report_hash(),
            artifact_kind="ChapterBlueprint",
            base_revision=int(proposal["base_revision"]),
            resolved_dependency_snapshot=expected["dependencies"],
            dependency_fingerprint=report.dependency_fingerprint,
            gate_id="H1_PLANNING_CONFIRM",
            gate_policy_version=GATE_POLICY_REGISTRY.VERSION,
            verdict="pass",
            issuer=ISSUER_HUMAN_GATE_SERVICE,
            reviewer=principal,
            issued_at=_now(),
            planning_decision="confirm",
            principal_id=principal,
            planning_confirmation_scope_hash=expected["planning_confirmation_scope_hash"],
            planning_audit_snapshot_hash=expected["planning_audit_snapshot_hash"],
            planning_dag_root_hash=expected["planning_dag_root_hash"],
            g2_receipt_id=expected["g2_receipt_id"],
            g2_receipt_hash=expected["g2_receipt_hash"],
            policy_nonce=str(nonce or "").strip(),
            reviewed_revision=int(proposal["base_revision"]),
        )
        if not receipt.policy_nonce:
            raise ControlPlaneError("PLANNING_CONFIRM_INVALID", "H1 确认缺少一次性命令 nonce。", status_code=400)
        stored = self.store.issue_v3_gate_receipt(receipt.storage_record(), kernel_seal=KERNEL_SEAL)
        stored.pop("created_at", None)
        return PlanningGateReceipt.model_validate(stored)

    def require_current_confirmation(self) -> PlanningGateReceipt:
        """Fail closed unless the active Blueprint has an H1 bound to this exact DAG."""
        blueprint = self.store.v3_active_artifact("ChapterBlueprint")
        if blueprint is None:
            raise ControlPlaneError("PLANNING_CONFIRM_REQUIRED", "尚未晋级 ChapterBlueprint。", status_code=409)
        raw = self.store.latest_v3_gate_receipt(str(blueprint["proposal_id"]), "H1_PLANNING_CONFIRM")
        if not raw or raw.get("verdict") != "pass" or raw.get("receipt_subtype") != "planning":
            raise ControlPlaneError("PLANNING_CONFIRM_REQUIRED", "当前 Blueprint 尚未获得 H1 人工确认。", status_code=409)
        receipt = PlanningGateReceipt.model_validate({key: value for key, value in raw.items() if key != "created_at"})
        current = self.planning_snapshot()
        if (
            receipt.proposal_hash != str(blueprint["proposal_hash"])
            or receipt.planning_dag_root_hash != current["planning_dag_root_hash"]
            or receipt.planning_confirmation_scope_hash != current["planning_confirmation_scope_hash"]
            or receipt.planning_audit_snapshot_hash != current["planning_audit_snapshot_hash"]
            or receipt.g2_receipt_id != current["g2_receipt_id"]
            or receipt.g2_receipt_hash != current["g2_receipt_hash"]
        ):
            raise ControlPlaneError("PLANNING_CONFIRM_STALE", "H1 不再适用于当前规划依赖，请重新确认。", status_code=409)
        return receipt

    def _scope_snapshot(self, blueprint_payload: Any) -> dict[str, Any]:
        blueprint = blueprint_payload if isinstance(blueprint_payload, dict) else {}
        ledger = self.store.v3_active_artifact("RequirementLedger") or {}
        scores = self.store.v3_active_artifact("ScoreModel") or {}
        blocking_requirements = [
            item for item in (ledger.get("payload") or {}).get("requirements", [])
            if item.get("severity") == "blocking"
        ]
        blocking_scores = [
            item for item in (scores.get("payload") or {}).get("points", [])
            if item.get("disqualifying") or item.get("review_status") == "blocked"
        ]
        if str(blueprint.get("planning_model") or "topic_graph") == "score_direct":
            nodes = [
                item
                for item in blueprint.get("nodes", [])
                if isinstance(item, dict)
            ]
            return {
                "planning_model": "score_direct",
                "blocking_requirements": blocking_requirements,
                "blocking_scores": blocking_scores,
                "chapter_tree": nodes,
                "chapter_bindings": [
                    {
                        "chapter_id": item.get("chapter_id"),
                        "primary_response_unit_ids": item.get(
                            "primary_response_unit_ids",
                            [],
                        ),
                        "supporting_response_unit_ids": item.get(
                            "supporting_response_unit_ids",
                            [],
                        ),
                        "score_point_ids": item.get("score_point_ids", []),
                        "score_condition_ids": item.get(
                            "score_condition_ids",
                            [],
                        ),
                        "requirement_ids": item.get("requirement_ids", []),
                    }
                    for item in nodes
                ],
                "document_quality_gates": blueprint.get(
                    "document_quality_gates",
                    [],
                ),
                "template_targets": [
                    item.get("template_target") for item in nodes
                ],
            }
        graph = self.store.v3_active_artifact("ResponseTopicGraph") or {}
        return {
            "planning_model": "topic_graph",
            "blocking_requirements": blocking_requirements,
            "blocking_scores": blocking_scores,
            "core_topics": (graph.get("payload") or {}).get("topics", []),
            "core_duties": (graph.get("payload") or {}).get("duties", []),
            "chapter_tree": blueprint.get("nodes", []),
            "primary_assignments": [item for item in blueprint.get("assignments", []) if item.get("role") == "primary"],
            "document_quality_gates": blueprint.get("document_quality_gates", []),
            "template_targets": [item.get("template_target") for item in blueprint.get("nodes", [])],
        }


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
        report_hash=report.compute_report_hash(),
        kernel_seal=KERNEL_SEAL,
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
