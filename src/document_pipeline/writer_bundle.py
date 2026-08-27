"""PR-23 deterministic assembly of the Writer's only permitted input."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json, write_json

from .canonicalization import canonical_hash, chapter_context_hash
from .chapter_blueprint import load_promoted_chapter_blueprint
from .contracts import BlueprintNode, WriterInputBundle
from .input_manifest import V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger
from .score_model import load_promoted_score_model
from .artifact_promotion import HumanGateService
from .global_project_context import GlobalProjectContextService
from .research_service import load_published_batch
from .source_artifacts import load_promoted_template_structure
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
                extracted_points = [
                    str(point).strip()
                    for point in (item.extracted_points or [])
                    if str(point).strip()
                ]
                supporting_excerpt = str(item.supporting_excerpt or "").strip()
                # Older immutable batches have no semantic fields. Retain only
                # their narrow evidence excerpt, never the complete web page.
                content = "\n".join([*extracted_points, supporting_excerpt]).strip()
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
                        "relevance_tier": item.relevance_tier.value,
                        "matched_project_anchors": list(
                            item.matched_project_anchors
                        ),
                        "matched_task_anchors": list(
                            item.matched_task_anchors
                        ),
                        "usage_constraints": list(item.usage_constraints),
                        "supporting_excerpt": supporting_excerpt,
                        "extracted_points": extracted_points,
                        "relevance_reason": item.relevance_reason,
                        "relevance_confidence": item.relevance_confidence,
                        "usage_category": item.usage_category,
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
        blueprint = load_promoted_chapter_blueprint(self.context)
        if blueprint.planning_model not in {"score_direct", "rewrite_merge"}:
            raise ControlPlaneError(
                "WRITER_BUNDLE_LEGACY_READ_ONLY",
                "topic_graph Blueprint 仅支持历史查看；请使用 score_direct 或 rewrite_merge 目录后再写作。",
                status_code=409,
            )
        node_id_set = set(node_ids)
        if not node_id_set:
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                "ContentUnit 未声明章节目标。",
                status_code=409,
            )
        blueprint_by_node = {node.chapter_id: node for node in blueprint.nodes}
        parent_chapter_ids = {
            str(node.parent_chapter_id)
            for node in blueprint.nodes
            if node.parent_chapter_id is not None
        }
        leaf_chapter_ids = set(blueprint_by_node) - parent_chapter_ids
        if unknown_nodes := node_id_set - set(blueprint_by_node):
            raise ControlPlaneError(
                "WRITER_BUNDLE_BLOCKED",
                f"ContentUnit 指向 Blueprint 未知章节: {sorted(unknown_nodes)}",
                status_code=409,
            )
        nodes = [blueprint_by_node[item] for item in node_ids]
        non_leaf_nodes = node_id_set - leaf_chapter_ids
        non_full_nodes = {
            node.chapter_id for node in nodes if node.content_policy != "full"
        }
        if non_leaf_nodes or non_full_nodes:
            raise ControlPlaneError(
                "CHAPTER_NOT_WRITABLE",
                "ContentUnit 只允许写入 content_policy=full 的叶子章节。",
                status_code=409,
                details={
                    "parent_chapter_ids": sorted(non_leaf_nodes),
                    "non_full_chapter_ids": sorted(non_full_nodes),
                },
            )

        generation_modes = {
            str(node.rewrite_mode or "new_write") for node in nodes
        }
        if not generation_modes <= {"copy", "light_edit", "restructure", "new_write"}:
            raise ControlPlaneError(
                "CHAPTER_GENERATION_MODE_INVALID",
                "章节正文生成模式无效。",
                status_code=409,
                details={"modes": sorted(generation_modes)},
            )
        if len(generation_modes) != 1:
            raise ControlPlaneError(
                "CHAPTER_GENERATION_MODE_MIXED",
                "一个写作单元只能使用一种章节正文生成模式。",
                status_code=409,
                details={"modes": sorted(generation_modes)},
            )
        effective_generation_mode = next(iter(generation_modes))

        writable_targets: list[BlueprintNode] = []
        if blueprint.mode.value == "template_strict":
            structure = load_promoted_template_structure(self.context)
            slots_by_id = (
                {slot.slot_id: slot for slot in structure.slots}
                if structure is not None
                else {}
            )
            for node in nodes:
                if node.template_slot_ids:
                    for slot_id in node.template_slot_ids:
                        slot = slots_by_id.get(slot_id)
                        if (
                            slot is None
                            or node.template_node_id is None
                            or slot.node_id != node.template_node_id
                        ):
                            raise ControlPlaneError(
                                "WRITER_BUNDLE_BLOCKED",
                                "Blueprint 中的严格模板 Slot 映射与当前模板不一致。",
                                status_code=409,
                                details={
                                    "chapter_id": node.chapter_id,
                                    "template_slot_id": slot_id,
                                },
                            )
                    writable_targets.append(node)
                elif node.template_target:
                    writable_targets.append(node)
                else:
                    raise ControlPlaneError(
                        "WRITER_BUNDLE_BLOCKED",
                        "严格模板章节缺少已确认的可写映射。",
                        status_code=409,
                        details={"chapter_id": node.chapter_id},
                    )
        else:
            writable_targets = list(nodes)
        ledger = load_promoted_requirement_ledger(self.context)
        scores = load_promoted_score_model(self.context)
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
        evidence_snapshot = (
            self._evidence_snapshot(
                node_ids=node_id_set,
                score_ids=score_ids,
                requirement_ids=set(requirement_ids),
            )
            if effective_generation_mode == "new_write"
            else []
        )
        global_context_service = GlobalProjectContextService(self.context)
        global_project_context = (
            global_context_service.load_for_deterministic_tests()
            if self.deterministic_test
            else global_context_service.load()
        )
        project_constraints = [
            *list(global_project_context.get("constraints") or []),
            *list(global_project_context.get("boundaries") or []),
            *list(global_project_context.get("risks") or []),
        ]
        terminology = dict(global_project_context.get("terminology") or {})
        dependencies = {
            kind: {"artifact_id": str(item["artifact_id"]), "revision": int(item["revision"]), "hash": str(item["artifact_hash"])}
            for kind in (
                "RequirementLedger",
                "ScoreModel",
                "ChapterBlueprint",
                "ProjectModel",
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
        legacy_blocks: dict[str, dict[str, Any]] = {}
        if any(node.legacy_sources for node in nodes):
            legacy_artifact = self.store.v3_active_artifact("LegacyBidIndex") or {}
            legacy_blocks = {
                str(item.get("block_id") or ""): item
                for item in (legacy_artifact.get("payload") or {}).get("blocks") or []
                if isinstance(item, dict) and item.get("block_id")
            }
        blueprint_slice: list[dict[str, Any]] = []
        for node in nodes:
            node_payload = node.model_dump(mode="json")
            resolved_sources: list[dict[str, Any]] = []
            for source in node_payload.get("legacy_sources") or []:
                source_payload = dict(source)
                block_id = str(source_payload.get("block_id") or "")
                expected_hash = str(source_payload.get("content_hash") or "")
                block = legacy_blocks.get(block_id)
                content = str((block or {}).get("content") or "")
                if node.rewrite_mode in {"copy", "light_edit", "restructure"} and (
                    block is None
                    or str(block.get("content_hash") or "") != expected_hash
                    or not content.strip()
                ):
                    raise ControlPlaneError(
                        "LEGACY_SOURCE_STALE",
                        "Blueprint 引用的旧稿正文已失效，禁止自动替换来源。",
                        status_code=409,
                        details={
                            "chapter_id": node.chapter_id,
                            "block_id": block_id,
                            "expected_content_hash": expected_hash,
                        },
                    )
                if (
                    block is not None
                    and str(block.get("content_hash") or "") == expected_hash
                ):
                    source_payload["content"] = content
                resolved_sources.append(source_payload)
            node_payload["legacy_sources"] = resolved_sources
            blueprint_slice.append(node_payload)

        body = {
            "unit_id": unit_id,
            "source_blueprint_artifact_id": str(blueprint_artifact["artifact_id"]),
            "source_blueprint_revision": int(blueprint_artifact["revision"]),
            "source_blueprint_hash": str(blueprint_artifact["artifact_hash"]),
            "h1_receipt_id": h1.receipt_id,
            "dependency_refs": dependencies,
            "blueprint_slice": blueprint_slice,
            "effective_generation_mode": effective_generation_mode,
            "topic_and_duty_slice": [],
            "requirement_excerpts": [requirements[item].model_dump(mode="json") for item in requirement_ids],
            "score_obligations": score_obligations,
            "evidence_snapshot": evidence_snapshot,
            "research_decisions": [],
            # Kept only as a read-compatibility field for historical bundles.
            # New bundles store the shared facts exactly once below.
            "project_context": {},
            "global_project_context": global_project_context,
            "chapter_grounding_context": {},
            "chapter_grounding_contexts": {},
            "project_constraints": project_constraints,
            "terminology": terminology,
            "document_target_constraints": [
                {
                    "node_id": item.chapter_id,
                    "target": item.chapter_id,
                    "output_target": item.chapter_id,
                    "template_node_id": item.template_node_id,
                    "template_slot_ids": list(item.template_slot_ids),
                    "template_target": item.template_target,
                    "title": item.title,
                    "purpose": item.purpose,
                    "writing_objectives": item.writing_objectives,
                    "primary_requirement_ids": item.requirement_ids,
                    "primary_response_unit_ids": item.primary_response_unit_ids,
                    "supporting_response_unit_ids": item.supporting_response_unit_ids,
                    "score_point_ids": item.score_point_ids,
                    "score_condition_ids": item.score_condition_ids,
                    "target_size": item.target_size,
                    "section_domain": item.section_domain,
                    "content_policy": item.content_policy,
                    "deferred_reason": item.deferred_reason,
                    "is_leaf": item.chapter_id in leaf_chapter_ids,
                }
                for item in writable_targets
            ],
            "prompt_version": PROMPT_VERSION,
            "model_config_hash": canonical_hash(
                writer_model_identity(
                    self.root,
                    deterministic_test=self.deterministic_test,
                )
            ),
        }
        # Phase 7: attach chapter-local context/locks when a workspace is materialised.
        primary_chapter_id = ""
        for item in body.get("document_target_constraints") or []:
            if isinstance(item, dict) and str(item.get("node_id") or "").strip():
                primary_chapter_id = str(item.get("node_id") or "").strip()
                break
        if primary_chapter_id:
            workspace = self.store.chapter_workspace(primary_chapter_id)
            if workspace is not None:
                context_head = self.store.chapter_context_head(primary_chapter_id)
                content_head = self.store.chapter_content_head(primary_chapter_id)
                locked = [
                    block
                    for block in ((content_head or {}).get("blocks") or [])
                    if isinstance(block, dict)
                    and (
                        block.get("human_locked")
                        or str(block.get("lock_state") or "") == "USER_LOCKED"
                    )
                ]
                history = [
                    {
                        "content_revision": item.get("content_revision"),
                        "content_hash": item.get("content_hash"),
                        "source": item.get("source"),
                        "created_at": item.get("created_at"),
                        "block_count": len(item.get("blocks") or []),
                    }
                    for item in self.store.chapter_content_revisions(
                        primary_chapter_id, limit=5
                    )
                ]
                body.update(
                    {
                        "chapter_id": primary_chapter_id,
                        "chapter_context_revision": int(
                            workspace.get("head_context_revision") or 0
                        ),
                        "chapter_context_items": list(
                            (context_head or {}).get("items") or []
                        ),
                        "head_content_revision": int(
                            workspace.get("head_content_revision") or 0
                        ),
                        "existing_content": "\n\n".join(
                            str(block.get("content") or "")
                            for block in ((content_head or {}).get("blocks") or [])
                            if isinstance(block, dict)
                            and str(block.get("content") or "").strip()
                        ),
                        "locked_blocks": locked,
                        "content_history_summary": history,
                    }
                )
        chapter_grounding_contexts: dict[str, dict[str, Any]] = {}
        requirement_rows = list(body.get("requirement_excerpts") or [])
        score_rows = list(body.get("score_obligations") or [])
        for target in body.get("document_target_constraints") or []:
            if not isinstance(target, dict):
                continue
            target_chapter_id = str(target.get("node_id") or "").strip()
            if not target_chapter_id:
                continue
            context_head = self.store.chapter_context_head(target_chapter_id) or {}
            target_requirement_ids = {
                str(item) for item in target.get("primary_requirement_ids") or []
            }
            target_score_ids = {
                str(item) for item in target.get("score_point_ids") or []
            }
            chapter_grounding_contexts[target_chapter_id] = (
                global_context_service.build_chapter_context(
                    target_chapter_id,
                    requirement_excerpts=[
                        item
                        for item in requirement_rows
                        if isinstance(item, dict)
                        and str(item.get("requirement_id") or "")
                        in target_requirement_ids
                    ],
                    score_obligations=[
                        item
                        for item in score_rows
                        if isinstance(item, dict)
                        and (
                            not target_score_ids
                            or str(item.get("score_point_id") or "")
                            in target_score_ids
                        )
                    ],
                    chapter_context_items=list(context_head.get("items") or []),
                    chapter_context_revision=int(
                        context_head.get("context_revision") or 0
                    ),
                    chapter_context_hash=chapter_context_hash(
                        target_chapter_id,
                        int(context_head.get("context_revision") or 0),
                        context_head.get("items") or [],
                    ),
                    global_context_override=(
                        global_project_context
                        if self.deterministic_test
                        else None
                    ),
                )
            )
        body["chapter_grounding_contexts"] = chapter_grounding_contexts
        body["chapter_grounding_context"] = dict(
            chapter_grounding_contexts.get(primary_chapter_id) or {}
        )
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
    if body_hash != bundle.bundle_hash:
        # Read-only compatibility for older bundle field sets.
        legacy_body = dict(body)
        legacy_body.pop("evidence_snapshot", None)
        for key in (
            "chapter_id",
            "chapter_context_revision",
            "chapter_context_items",
            "head_content_revision",
            "locked_blocks",
            "content_history_summary",
            "research_decisions",
            "operation",
            "user_instruction",
            "existing_content",
            "overwrite_locked",
        ):
            legacy_body.pop(key, None)
        body_hash = canonical_hash(legacy_body)
    if body_hash != bundle.bundle_hash and not bundle.evidence_snapshot:
        legacy_body = dict(body)
        legacy_body.pop("evidence_snapshot", None)
        body_hash = canonical_hash(legacy_body)
    if body_hash != bundle.bundle_hash:
        raise ValueError("WRITER_BUNDLE_HASH_MISMATCH")
    return bundle
