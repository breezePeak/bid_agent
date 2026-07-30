"""PR-23 deterministic assembly of the Writer's only permitted input."""

from __future__ import annotations

from pathlib import Path

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json, write_json

from .canonicalization import canonical_hash
from .chapter_blueprint import load_promoted_chapter_blueprint
from .contracts import (
    DOCUMENT_CONTRACT_ADAPTER,
    ContractNode,
    DocumentPlan,
    TemplateContract,
    WriterInputBundle,
)
from .document_contract import DOCUMENT_CONTRACT_PATH
from .document_planner import DOCUMENT_PLAN_PATH
from .input_manifest import V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger
from .score_model import load_promoted_score_model
from .artifact_promotion import HumanGateService
from .project_model import load_promoted_project_model
from .research_service import load_published_batch
from .writer_policy import (
    WRITER_PROMPT_VERSION,
    writer_model_identity,
)


BUNDLE_DIR = V3_ROOT / "writer_bundles"
PROMPT_VERSION = WRITER_PROMPT_VERSION
MODEL_CONFIG_HASH = "runtime_writer_model"


class WriterInputBundleAssembler:
    """Service-only compiler; writers receive its returned Bundle, never workspace state."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        deterministic_test: bool = False,
    ) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)
        self.deterministic_test = bool(deterministic_test)

    def _evidence_snapshot(
        self,
        *,
        node_ids: set[str],
        score_ids: set[str],
        requirement_ids: set[str],
    ) -> list[dict]:
        """Freeze only published evidence relevant to this Writer unit."""

        allowed_topics = {
            *(f"chapter:{item}" for item in node_ids),
            *(f"score:{item}" for item in score_ids),
            *(f"requirement:{item}" for item in requirement_ids),
        }
        snapshot: list[dict] = []
        for need in self.store.evidence_needs():
            if str(need.get("topic_id") or "") not in allowed_topics:
                continue
            batch_id = str(need.get("active_batch_id") or "")
            batch = load_published_batch(self.context, batch_id)
            if batch is None or not batch.items:
                continue
            contents: list[str] = []
            sources: list[dict] = []
            evidence_ids: list[str] = []
            for item in batch.items:
                evidence_ids.append(item.evidence_id)
                content = str(item.content or "").strip()
                if content and content not in contents:
                    contents.append(content)
                sources.append(
                    {
                        "evidence_id": item.evidence_id,
                        "title": item.title,
                        "publisher": item.publisher,
                        "source_url": item.source_url,
                        "source_type": item.source_type.value,
                        "retrieved_at": item.retrieved_at,
                    }
                )
            combined = "\n\n".join(contents)
            if len(combined) > 8_000:
                combined = combined[:8_000].rstrip() + "…"
            snapshot.append(
                {
                    "need_id": str(need["need_id"]),
                    "topic_id": str(need["topic_id"]),
                    "question": str(need["question"]),
                    "batch_id": batch.batch_id,
                    "evidence_ids": evidence_ids,
                    "content": combined,
                    "sources": sources,
                }
            )
        return snapshot

    def assemble(self, unit_id: str, node_ids: list[str]) -> WriterInputBundle:
        h1 = HumanGateService(self.context).require_current_confirmation()
        blueprint_artifact = self.store.v3_active_artifact("ChapterBlueprint")
        assert blueprint_artifact is not None
        contract = DOCUMENT_CONTRACT_ADAPTER.validate_python(read_json(self.root / DOCUMENT_CONTRACT_PATH))
        plan = DocumentPlan.model_validate(read_json(self.root / DOCUMENT_PLAN_PATH))
        if contract.source_blueprint_hash != str(blueprint_artifact["artifact_hash"]) or plan.source_blueprint_hash != str(blueprint_artifact["artifact_hash"]):
            raise ControlPlaneError("WRITER_BUNDLE_BLOCKED", "DocumentContract/DocumentPlan 未绑定当前 H1 Blueprint。", status_code=409)
        blueprint = load_promoted_chapter_blueprint(self.context)
        if blueprint.planning_model != "score_direct":
            raise ControlPlaneError(
                "WRITER_BUNDLE_LEGACY_READ_ONLY",
                "legacy TopicGraph Blueprint 仅支持历史查看；请重新生成评分直连目录后再写作。",
                status_code=409,
            )
        ledger = load_promoted_requirement_ledger(self.context)
        scores = load_promoted_score_model(self.context)
        node_id_set = set(node_ids)
        if not node_id_set or not node_id_set.issubset({node.node_id for node in contract.nodes}):
            raise ControlPlaneError("WRITER_BUNDLE_BLOCKED", "ContentUnit 包含未授权章节目标。", status_code=409)
        blueprint_by_node = {node.chapter_id: node for node in blueprint.nodes}
        if unknown_nodes := node_id_set - set(blueprint_by_node):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"ContentUnit 指向 Blueprint 未知章节: {sorted(unknown_nodes)}",
                status_code=409,
            )
        nodes = [blueprint_by_node[item] for item in node_ids]
        requirement_ids = sorted(
            {
                requirement_id
                for node in nodes
                for requirement_id in node.requirement_ids
            }
        )
        primary_response_unit_ids = {
            unit_id
            for node in nodes
            for unit_id in node.primary_response_unit_ids
        }
        supporting_response_unit_ids = {
            unit_id
            for node in nodes
            for unit_id in node.supporting_response_unit_ids
        }
        response_unit_ids = (
            primary_response_unit_ids | supporting_response_unit_ids
        )
        condition_ids = {
            condition_id
            for node in nodes
            for condition_id in node.score_condition_ids
        }
        requirements = {item.requirement_id: item for item in ledger.requirements}
        score_points = {item.score_point_id: item for item in scores.points}
        response_units = {
            unit.unit_id: (point, unit)
            for point in scores.points
            for unit in point.response_units
        }
        conditions = {
            condition.condition_id: (point, condition)
            for point in scores.points
            for condition in point.score_conditions
        }
        if unknown := set(requirement_ids) - set(requirements):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"章节绑定未知 requirement_id: {sorted(unknown)}",
                status_code=409,
            )
        if unknown := response_unit_ids - set(response_units):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"章节绑定未知 response_unit_id: {sorted(unknown)}",
                status_code=409,
            )
        if unknown := condition_ids - set(conditions):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"章节绑定未知 score_condition_id: {sorted(unknown)}",
                status_code=409,
            )
        condition_ids.update(
            condition_id
            for unit_id in response_unit_ids
            for condition_id in response_units[unit_id][1].condition_ids
        )
        score_ids = {
            point.score_point_id
            for unit_id in response_unit_ids
            for point in (response_units[unit_id][0],)
        } | {
            point.score_point_id
            for condition_id in condition_ids
            for point in (conditions[condition_id][0],)
        } | {
            score_point_id
            for node in nodes
            for score_point_id in node.score_point_ids
        }
        if unknown := score_ids - set(score_points):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"章节绑定未知 score_point_id: {sorted(unknown)}",
                status_code=409,
            )
        evidence_snapshot = self._evidence_snapshot(
            node_ids=node_id_set,
            score_ids=score_ids,
            requirement_ids=set(requirement_ids),
        )
        try:
            project = load_promoted_project_model(self.context)
            project_context = {
                "identity": dict(project.identity),
                "background": list(project.background),
                "goals": list(project.goals),
                "scope": list(project.scope),
                "work_packages": list(project.work_packages),
                "deliverables": list(project.deliverables),
                "acceptance_conditions": list(project.acceptance_conditions),
                "constraints": list(project.constraints),
                "risks": list(project.risks),
                "unknowns": list(project.unknowns),
                "terminology": dict(project.terminology),
            }
            project_constraints = [
                *project.constraints,
                *project.boundaries,
                *project.risks,
            ]
            terminology = dict(project.terminology)
        except Exception:
            project_context = {}
            project_constraints = []
            terminology = {}
        targets = [item for item in contract.nodes if item.node_id in node_id_set]
        writable_targets: list[tuple[ContractNode, str]] = []
        if isinstance(contract, TemplateContract):
            slots_by_id = {slot.slot_id: slot for slot in contract.slots}
            for target in targets:
                slot = slots_by_id.get(target.writable_target)
                if slot is None:
                    continue
                if slot.node_id != target.node_id:
                    raise ControlPlaneError(
                        "WRITER_BUNDLE_BLOCKED",
                        "TemplateContract writable_target 与章节 Slot 映射不一致。",
                        status_code=409,
                    )
                writable_targets.append((target, slot.slot_id))
        else:
            writable_targets = [(target, target.node_id) for target in targets]
        if not writable_targets:
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                "ContentUnit 不包含已确认 Blueprint 的可写目标。",
                status_code=409,
            )
        dependencies = {
            kind: {"artifact_id": str(item["artifact_id"]), "revision": int(item["revision"]), "hash": str(item["artifact_hash"])}
            for kind in (
                "RequirementLedger",
                "ScoreModel",
                "ChapterBlueprint",
                "TemplateStructureContract",
            )
            if (item := self.store.v3_active_artifact(kind)) is not None
        }
        score_obligations: list[dict] = []
        provided_requirement_ids = set(requirement_ids)
        for score_id in sorted(score_ids):
            point = score_points[score_id]
            payload = point.model_dump(mode="json")
            selected_conditions = [
                condition
                for condition in point.score_conditions
                if condition.condition_id in condition_ids
            ]
            selected_condition_ids = {
                condition.condition_id for condition in selected_conditions
            }
            # Condition-only Blueprint slices (evidence/content child chapters)
            # often omit primary/supporting unit ids on purpose. Still freeze the
            # owning response unit so G4 can map evidence conditions to units.
            selected_units = [
                unit
                for unit in point.response_units
                if unit.unit_id in response_unit_ids
                or any(
                    condition_id in selected_condition_ids
                    for condition_id in unit.condition_ids
                )
            ]
            # Project each unit to this ContentUnit's condition slice. A unit may
            # own sibling conditions bound to other chapters; freezing the full
            # condition_ids list would make G4 report CONDITION_OUT_OF_BUNDLE.
            payload["response_units"] = []
            for unit in selected_units:
                unit_payload = unit.model_dump(mode="json")
                unit_payload["condition_ids"] = [
                    condition_id
                    for condition_id in unit.condition_ids
                    if condition_id in selected_condition_ids
                ]
                payload["response_units"].append(unit_payload)
            payload["score_conditions"] = [
                condition.model_dump(mode="json")
                for condition in selected_conditions
            ]
            payload["full_score_conditions"] = [
                condition.text for condition in selected_conditions
            ]
            # The score row already arrives through source-bound conditions.
            # Expose only procurement requirements whose original text is
            # actually present in this least-privilege bundle.
            payload["linked_requirement_ids"] = [
                requirement_id
                for requirement_id in point.linked_requirement_ids
                if requirement_id in provided_requirement_ids
            ]
            payload["context_requirement_ids"] = [
                requirement_id
                for requirement_id in point.context_requirement_ids
                if requirement_id in provided_requirement_ids
            ]
            score_obligations.append(payload)
        body = {
            "unit_id": unit_id,
            "source_blueprint_artifact_id": str(blueprint_artifact["artifact_id"]),
            "source_blueprint_revision": int(blueprint_artifact["revision"]),
            "source_blueprint_hash": str(blueprint_artifact["artifact_hash"]),
            "h1_receipt_id": h1.receipt_id,
            "dependency_refs": dependencies,
            "blueprint_slice": [item.model_dump(mode="json") for item in nodes],
            "topic_and_duty_slice": [],
            "requirement_excerpts": [requirements[item].model_dump(mode="json") for item in requirement_ids],
            "score_obligations": score_obligations,
            "evidence_snapshot": evidence_snapshot,
            "research_decisions": [],
            "project_context": project_context,
            "project_constraints": project_constraints,
            "terminology": terminology,
            "document_target_constraints": [
                {
                    "node_id": item.node_id,
                    "target": item.writable_target,
                    "output_target": output_target,
                    "title": item.title,
                    "primary_requirement_ids": blueprint_by_node[
                        item.node_id
                    ].requirement_ids,
                    "primary_response_unit_ids": blueprint_by_node[
                        item.node_id
                    ].primary_response_unit_ids,
                    "supporting_response_unit_ids": blueprint_by_node[
                        item.node_id
                    ].supporting_response_unit_ids,
                    "score_point_ids": blueprint_by_node[
                        item.node_id
                    ].score_point_ids,
                    "score_condition_ids": blueprint_by_node[
                        item.node_id
                    ].score_condition_ids,
                    "target_size": blueprint_by_node[
                        item.node_id
                    ].target_size,
                    "section_domain": blueprint_by_node[
                        item.node_id
                    ].section_domain,
                    "content_policy": blueprint_by_node[
                        item.node_id
                    ].content_policy,
                    "deferred_reason": blueprint_by_node[
                        item.node_id
                    ].deferred_reason,
                }
                for item, output_target in writable_targets
                if blueprint_by_node[item.node_id].content_policy == "full"
            ],
            "prompt_version": PROMPT_VERSION,
            "model_config_hash": canonical_hash(
                writer_model_identity(
                    self.root,
                    deterministic_test=self.deterministic_test,
                )
            ),
        }
        source_hashes = dict(blueprint.source_hashes)
        for item in evidence_snapshot:
            source_hashes[
                f"evidence:{item['batch_id']}"
            ] = canonical_hash(item)
        bundle = WriterInputBundle(
            revision=int(blueprint_artifact["revision"]), source_hashes=source_hashes,
            bundle_id=f"bundle-{unit_id}-{canonical_hash(body)[:16]}", bundle_hash=canonical_hash(body), **body,
        )
        write_json(self.root / BUNDLE_DIR / f"{bundle.bundle_id}.json", bundle.model_dump(mode="json"))
        return bundle


def load_writer_bundle(root: Path, bundle_id: str) -> WriterInputBundle:
    path = root / BUNDLE_DIR / f"{bundle_id}.json"
    if not path.is_file():
        raise ValueError("WRITER_BUNDLE_NOT_FOUND")
    bundle = WriterInputBundle.model_validate(read_json(path))
    body = bundle.model_dump(mode="json", exclude={"revision", "source_hashes", "bundle_id", "bundle_hash"})
    body_hash = canonical_hash(body)
    if body_hash != bundle.bundle_hash and not bundle.evidence_snapshot:
        # Read-only compatibility for bundles created before evidence_snapshot
        # became part of the frozen contract.
        legacy_body = dict(body)
        legacy_body.pop("evidence_snapshot", None)
        body_hash = canonical_hash(legacy_body)
    if body_hash != bundle.bundle_hash:
        raise ValueError("WRITER_BUNDLE_HASH_MISMATCH")
    return bundle
