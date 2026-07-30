"""Versioned Gate policy: which Gates, issuers and verdicts each Artifact kind needs.

Promotion must satisfy the full required Gate set for the artifact kind.
A single named `pass` receipt is never sufficient by itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact_registry import ARTIFACT_REGISTRY_VERSION

GATE_POLICY_REGISTRY_VERSION = "v3-gate-policy-3"

# Fixed service issuers. Agents and free-form reviewer strings are never issuers.
ISSUER_GATE_SERVICE = "gate_service"
ISSUER_HUMAN_GATE_SERVICE = "human_gate_service"


@dataclass(frozen=True)
class GateRequirement:
    gate_id: str
    allowed_issuers: frozenset[str]
    promotion_verdicts: frozenset[str] = frozenset({"pass"})
    human_required: bool = False


@dataclass(frozen=True)
class ArtifactGatePolicy:
    artifact_kind: str
    policy_version: str
    required_gates: tuple[GateRequirement, ...]
    validator_id: str
    schema_version: str = "v3"

    def gate_ids(self) -> tuple[str, ...]:
        return tuple(item.gate_id for item in self.required_gates)

    def requirement_for(self, gate_id: str) -> GateRequirement | None:
        for item in self.required_gates:
            if item.gate_id == gate_id:
                return item
        return None


class GatePolicyRegistry:
    """Maps each promotable artifact kind to its required Gate set."""

    VERSION = GATE_POLICY_REGISTRY_VERSION

    def __init__(self) -> None:
        system = frozenset({ISSUER_GATE_SERVICE})
        self._policies: dict[str, ArtifactGatePolicy] = {
            "InputManifest": ArtifactGatePolicy(
                artifact_kind="InputManifest",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G0_INPUT_MANIFEST_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.input_manifest",
            ),
            "SourceIndex": ArtifactGatePolicy(
                artifact_kind="SourceIndex",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G0_SOURCE_STRUCTURE", allowed_issuers=system),
                ),
                validator_id="v3.validator.source_index",
            ),
            "TemplateStructureContract": ArtifactGatePolicy(
                artifact_kind="TemplateStructureContract",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G0_TEMPLATE_STRUCTURE", allowed_issuers=system),
                ),
                validator_id="v3.validator.template_structure",
            ),
            "RequirementLedger": ArtifactGatePolicy(
                artifact_kind="RequirementLedger",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G1_REQUIREMENT_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.requirement_ledger",
            ),
            "ScoreModel": ArtifactGatePolicy(
                artifact_kind="ScoreModel",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G1_SCORE_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.score_model",
            ),
            "ProjectModel": ArtifactGatePolicy(
                artifact_kind="ProjectModel",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G1_PROJECT_MODEL_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.project_model",
            ),
            "ResponseTopicGraph": ArtifactGatePolicy(
                artifact_kind="ResponseTopicGraph",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G1_TOPIC_GRAPH_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.response_topic_graph",
            ),
            "ChapterBlueprint": ArtifactGatePolicy(
                artifact_kind="ChapterBlueprint",
                policy_version=self.VERSION,
                required_gates=(
                    GateRequirement(gate_id="G2_BLUEPRINT_INTEGRITY", allowed_issuers=system),
                ),
                validator_id="v3.validator.chapter_blueprint",
            ),
        }
        # H1 is a planning confirmation gate over an already-promoted Blueprint.
        # It is not part of Blueprint promotion itself; Writer consumes it later.
        self._standalone_gates: dict[str, GateRequirement] = {
            "H1_PLANNING_CONFIRM": GateRequirement(
                gate_id="H1_PLANNING_CONFIRM",
                allowed_issuers=frozenset({ISSUER_HUMAN_GATE_SERVICE}),
                promotion_verdicts=frozenset({"pass"}),
                human_required=True,
            ),
        }

    def policy_for(self, artifact_kind: str) -> ArtifactGatePolicy:
        policy = self._policies.get(str(artifact_kind))
        if policy is None:
            raise KeyError(f"V3_GATE_POLICY_UNKNOWN: {artifact_kind}")
        return policy

    def require_policy(self, artifact_kind: str) -> ArtifactGatePolicy:
        return self.policy_for(artifact_kind)

    def standalone_gate(self, gate_id: str) -> GateRequirement | None:
        return self._standalone_gates.get(str(gate_id))

    def resolve_gate_requirement(self, artifact_kind: str, gate_id: str) -> GateRequirement:
        policy = self.policy_for(artifact_kind)
        requirement = policy.requirement_for(gate_id)
        if requirement is not None:
            return requirement
        standalone = self.standalone_gate(gate_id)
        if standalone is not None:
            return standalone
        raise KeyError(f"V3_GATE_UNKNOWN: kind={artifact_kind} gate={gate_id}")

    def registry_fingerprint(self) -> str:
        from .canonicalization import canonical_hash

        return canonical_hash(
            {
                "gate_policy_registry_version": self.VERSION,
                "artifact_registry_version": ARTIFACT_REGISTRY_VERSION,
                "policies": {
                    kind: {
                        "policy_version": policy.policy_version,
                        "validator_id": policy.validator_id,
                        "gates": [
                            {
                                "gate_id": gate.gate_id,
                                "issuers": sorted(gate.allowed_issuers),
                                "verdicts": sorted(gate.promotion_verdicts),
                                "human_required": gate.human_required,
                            }
                            for gate in policy.required_gates
                        ],
                    }
                    for kind, policy in sorted(self._policies.items())
                },
            }
        )


GATE_POLICY_REGISTRY = GatePolicyRegistry()
