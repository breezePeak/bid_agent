"""Chapter Workspace control service (Phase 1–2).

A Chapter Workspace is a logical aggregate inside a project Workspace. It does
not create a second control.db, runner, or canonical Artifact write path.
ChapterBlueprint remains the structural authority; materialization is an
idempotent control-plane projection of one blueprint node.

Phase 2 adds chapter-local Context as an overlay on shared global artifacts:
Blueprint projection seeds once; subsequent user saves append-only revisions.
"""

from __future__ import annotations

from typing import Any

from control_plane import CommandEnvelope, ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import (
    ChapterContextItem,
    ChapterContextRevisionRecord,
    ChapterWorkspaceRecord,
)


class ChapterWorkspaceService:
    """Read/materialize/archive chapter workspaces bound to promoted Blueprint."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def _require_blueprint(self) -> dict[str, Any]:
        active = self.store.v3_active_artifact("ChapterBlueprint")
        if active is None:
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED",
                "尚未晋级 ChapterBlueprint，无法物化章节 Workspace。",
                status_code=409,
            )
        payload = active.get("payload")
        if not isinstance(payload, dict):
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED",
                "ChapterBlueprint payload 无效。",
                status_code=503,
            )
        nodes = payload.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED",
                "ChapterBlueprint 不包含章节节点。",
                status_code=409,
            )
        return {
            "artifact_id": str(active.get("artifact_id") or ""),
            "revision": int(active.get("revision") or 0),
            "artifact_hash": str(active.get("artifact_hash") or ""),
            "payload": payload,
            "nodes": [item for item in nodes if isinstance(item, dict)],
        }

    def _node_by_id(self, chapter_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        blueprint = self._require_blueprint()
        normalized = ControlStore._normalize_chapter_id(chapter_id)
        for node in blueprint["nodes"]:
            if str(node.get("chapter_id") or "").strip() == normalized:
                return blueprint, node
        raise ControlPlaneError(
            "CHAPTER_NOT_IN_BLUEPRINT",
            f"ChapterBlueprint 中不存在章节: {normalized}",
            status_code=404,
        )

    @staticmethod
    def _expected_chapter_revision(payload: dict[str, Any]) -> int:
        raw = payload.get("expected_chapter_revision", 0)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                "CHAPTER_REVISION_INVALID",
                "expected_chapter_revision 必须是整数。",
                status_code=400,
            ) from exc

    def list_chapters(self, *, include_archived: bool = True) -> dict[str, Any]:
        """Project Blueprint nodes with optional materialized workspace state."""
        try:
            blueprint = self._require_blueprint()
            nodes = blueprint["nodes"]
            blueprint_revision = int(blueprint["revision"])
            blueprint_hash = str(blueprint["artifact_hash"])
        except ControlPlaneError as exc:
            if exc.code == "CHAPTER_BLUEPRINT_REQUIRED":
                materializations = {
                    item["chapter_id"]: item
                    for item in self.store.chapter_workspaces(include_archived=include_archived)
                }
                items = list(materializations.values()) if include_archived else []
                return {
                    "blueprint_revision": 0,
                    "blueprint_hash": "",
                    "total": len(items),
                    "materialized": len(items),
                    "active": sum(1 for item in items if item.get("status") == "active"),
                    "archived": sum(1 for item in items if item.get("status") == "archived"),
                    "items": items,
                }
            raise

        materializations = {
            item["chapter_id"]: item
            for item in self.store.chapter_workspaces(include_archived=True)
        }
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in nodes:
            chapter_id = str(node.get("chapter_id") or "").strip()
            if not chapter_id:
                continue
            try:
                chapter_id = ControlStore._normalize_chapter_id(chapter_id)
            except ControlPlaneError:
                continue
            seen.add(chapter_id)
            row = materializations.get(chapter_id)
            if row is None:
                items.append(
                    {
                        "chapter_id": chapter_id,
                        "title": str(node.get("title") or chapter_id),
                        "parent_chapter_id": (
                            str(node["parent_chapter_id"])
                            if node.get("parent_chapter_id") is not None
                            else None
                        ),
                        "order": int(node.get("order") or 0),
                        "materialized": False,
                        "status": "projected",
                        "approval_status": "not_started",
                        "chapter_revision": 0,
                        "head_content_revision": 0,
                        "formal_content_revision": 0,
                        "head_context_revision": 0,
                        "state_hash": "",
                        "updated_at": "",
                        "blueprint_revision": blueprint_revision,
                        "blueprint_hash": blueprint_hash,
                        "metadata": {},
                    }
                )
                continue
            if not include_archived and str(row.get("status") or "") == "archived":
                continue
            items.append({**row, "materialized": True})

        if include_archived:
            for chapter_id, row in materializations.items():
                if chapter_id in seen:
                    continue
                if str(row.get("status") or "") != "archived":
                    # Orphan active rows (blueprint removed node) still surface.
                    items.append({**row, "materialized": True, "orphan": True})
                else:
                    items.append({**row, "materialized": True, "orphan": True})

        items.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("chapter_id") or "")))
        return {
            "blueprint_revision": blueprint_revision,
            "blueprint_hash": blueprint_hash,
            "total": len(items),
            "materialized": sum(1 for item in items if item.get("materialized")),
            "active": sum(1 for item in items if item.get("status") == "active"),
            "archived": sum(1 for item in items if item.get("status") == "archived"),
            "items": items,
        }

    def get_chapter(self, chapter_id: str) -> dict[str, Any]:
        blueprint, node = self._node_by_id(chapter_id)
        row = self.store.chapter_workspace(chapter_id)
        if row is None:
            return {
                "chapter_id": ControlStore._normalize_chapter_id(chapter_id),
                "title": str(node.get("title") or chapter_id),
                "parent_chapter_id": (
                    str(node["parent_chapter_id"])
                    if node.get("parent_chapter_id") is not None
                    else None
                ),
                "order": int(node.get("order") or 0),
                "materialized": False,
                "status": "projected",
                "approval_status": "not_started",
                "chapter_revision": 0,
                "head_content_revision": 0,
                "formal_content_revision": 0,
                "head_context_revision": 0,
                "state_hash": "",
                "updated_at": "",
                "blueprint_revision": int(blueprint["revision"]),
                "blueprint_hash": str(blueprint["artifact_hash"]),
                "blueprint_node": node,
                "metadata": {},
                "context": None,
            }
        context = self.store.chapter_context_head(chapter_id)
        content = self.store.chapter_content_head(chapter_id)
        formal = self.store.chapter_formal_content(chapter_id)
        return {
            **row,
            "materialized": True,
            "blueprint_node": node,
            "blueprint_revision_active": int(blueprint["revision"]),
            "blueprint_hash_active": str(blueprint["artifact_hash"]),
            "context": context,
            "content": content,
            "formal_content": formal,
        }

    @staticmethod
    def seed_items_from_blueprint_node(node: dict[str, Any]) -> list[dict[str, Any]]:
        """Deterministic first-time seed. Never reapplied over existing context."""
        items: list[dict[str, Any]] = []
        order = 0
        purpose = str(node.get("purpose") or "").strip()
        if purpose:
            items.append(
                ChapterContextItem(
                    item_id="seed:goal:purpose",
                    kind="GOAL",
                    title="章节目的",
                    body=purpose,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref="blueprint.purpose",
                ).model_dump(mode="json")
            )
            order += 1
        for index, objective in enumerate(node.get("writing_objectives") or []):
            text = str(objective or "").strip()
            if not text:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:goal:objective:{index}",
                    kind="GOAL",
                    title=f"写作目标 {index + 1}",
                    body=text,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.writing_objectives[{index}]",
                ).model_dump(mode="json")
            )
            order += 1
        for score_id in node.get("score_point_ids") or []:
            value = str(score_id or "").strip()
            if not value:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:scoring:point:{value}",
                    kind="SCORING_REQUIREMENT",
                    title=f"评分点 {value}",
                    body=value,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.score_point_ids:{value}",
                ).model_dump(mode="json")
            )
            order += 1
        for condition_id in node.get("score_condition_ids") or []:
            value = str(condition_id or "").strip()
            if not value:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:scoring:condition:{value}",
                    kind="SCORING_REQUIREMENT",
                    title=f"评分条件 {value}",
                    body=value,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.score_condition_ids:{value}",
                ).model_dump(mode="json")
            )
            order += 1
        content_policy = str(node.get("content_policy") or "").strip()
        if content_policy and content_policy != "full":
            deferred = str(node.get("deferred_reason") or "").strip()
            body = f"content_policy={content_policy}"
            if deferred:
                body = f"{body}; deferred_reason={deferred}"
            items.append(
                ChapterContextItem(
                    item_id="seed:constraint:content_policy",
                    kind="TECHNICAL_CONSTRAINT",
                    title="内容策略约束",
                    body=body,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref="blueprint.content_policy",
                ).model_dump(mode="json")
            )
            order += 1
        for topic_id in node.get("forbidden_topic_ids") or []:
            value = str(topic_id or "").strip()
            if not value:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:constraint:forbidden:{value}",
                    kind="TECHNICAL_CONSTRAINT",
                    title=f"禁止主题 {value}",
                    body=value,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.forbidden_topic_ids:{value}",
                ).model_dump(mode="json")
            )
            order += 1
        for requirement_id in node.get("requirement_ids") or []:
            value = str(requirement_id or "").strip()
            if not value:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:fact:requirement:{value}",
                    kind="KEY_FACT",
                    title=f"需求 {value}",
                    body=value,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.requirement_ids:{value}",
                ).model_dump(mode="json")
            )
            order += 1
        for index, mention in enumerate(node.get("required_mentions") or []):
            text = str(mention or "").strip()
            if not text:
                continue
            items.append(
                ChapterContextItem(
                    item_id=f"seed:fact:mention:{index}",
                    kind="KEY_FACT",
                    title=f"必提要点 {index + 1}",
                    body=text,
                    order=order,
                    source="BLUEPRINT_SEED",
                    origin_ref=f"blueprint.required_mentions[{index}]",
                ).model_dump(mode="json")
            )
            order += 1
        return items

    def create(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int = 0,
        metadata: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blueprint, node = self._node_by_id(chapter_id)
        record = self.store.materialize_chapter_workspace(
            chapter_id=chapter_id,
            blueprint_revision=int(blueprint["revision"]),
            blueprint_hash=str(blueprint["artifact_hash"]),
            title=str(node.get("title") or chapter_id),
            parent_chapter_id=(
                str(node["parent_chapter_id"])
                if node.get("parent_chapter_id") is not None
                else None
            ),
            order_index=int(node.get("order") or 0),
            expected_chapter_revision=expected_chapter_revision,
            metadata=metadata,
            actor=actor,
        )
        # Blueprint seed only when context head is still empty.
        if int(record.get("head_context_revision") or 0) == 0:
            seed_items = self.seed_items_from_blueprint_node(node)
            if seed_items:
                saved = self.store.append_chapter_context_revision(
                    chapter_id=chapter_id,
                    expected_chapter_revision=int(record["chapter_revision"]),
                    items=seed_items,
                    seeded_from_blueprint=True,
                    actor=actor,
                )
                record = saved["chapter"]
            # Empty seed still leaves head at 0; first user save becomes rev 1.
        return ChapterWorkspaceRecord.model_validate(record).model_dump(mode="json")

    def archive(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Archive is allowed even if the node left the latest blueprint (soft delete).
        try:
            ControlStore._normalize_chapter_id(chapter_id)
        except ControlPlaneError:
            raise
        record = self.store.archive_chapter_workspace(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            actor=actor,
        )
        return ChapterWorkspaceRecord.model_validate(record).model_dump(mode="json")

    def save_metadata(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        metadata: dict[str, Any],
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.store.update_chapter_workspace_metadata(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            metadata=metadata,
            actor=actor,
        )
        return ChapterWorkspaceRecord.model_validate(record).model_dump(mode="json")

    def save_context(
        self,
        *,
        chapter_id: str,
        expected_chapter_revision: int,
        items: list[dict[str, Any]],
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # User saves never mark seeded_from_blueprint; seed is first materialize only.
        result = self.store.append_chapter_context_revision(
            chapter_id=chapter_id,
            expected_chapter_revision=expected_chapter_revision,
            items=items,
            seeded_from_blueprint=False,
            actor=actor,
        )
        chapter = ChapterWorkspaceRecord.model_validate(result["chapter"]).model_dump(
            mode="json"
        )
        context = ChapterContextRevisionRecord.model_validate(result["context"]).model_dump(
            mode="json"
        )
        return {
            "chapter": chapter,
            "context": context,
            "unchanged": bool(result.get("unchanged")),
        }

    def list_context_revisions(
        self,
        chapter_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        ControlStore._normalize_chapter_id(chapter_id)
        workspace = self.store.chapter_workspace(chapter_id)
        if workspace is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND",
                f"章节 Workspace 不存在: {chapter_id}",
                status_code=404,
            )
        revisions = self.store.chapter_context_revisions(chapter_id, limit=limit)
        return {
            "chapter_id": workspace["chapter_id"],
            "head_context_revision": int(workspace.get("head_context_revision") or 0),
            "chapter_revision": int(workspace.get("chapter_revision") or 0),
            "revisions": revisions,
        }

    def get_context_revision(
        self,
        chapter_id: str,
        context_revision: int,
    ) -> dict[str, Any]:
        record = self.store.chapter_context_revision(chapter_id, context_revision)
        if record is None:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_NOT_FOUND",
                f"Context revision 不存在: {chapter_id}@{context_revision}",
                status_code=404,
            )
        return ChapterContextRevisionRecord.model_validate(record).model_dump(mode="json")

    def handle_create(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id  # context matches self; operation tracked by gateway
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ControlPlaneError(
                "CHAPTER_METADATA_INVALID",
                "metadata 必须是对象。",
                status_code=400,
            )
        chapter = self.create(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            metadata=metadata if isinstance(metadata, dict) else None,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节 Workspace 已就绪: {chapter['chapter_id']}",
            "chapter": chapter,
        }

    def handle_archive(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        chapter = self.archive(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节 Workspace 已归档: {chapter['chapter_id']}",
            "chapter": chapter,
        }

    def handle_save_metadata(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ControlPlaneError(
                "CHAPTER_METADATA_INVALID",
                "metadata 必须是对象。",
                status_code=400,
            )
        chapter = self.save_metadata(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            metadata=metadata,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": f"章节元数据已保存: {chapter['chapter_id']}",
            "chapter": chapter,
        }

    def handle_save_context(
        self,
        context: WorkspaceContext,
        envelope: CommandEnvelope,
        operation_id: str,
    ) -> dict[str, Any]:
        del context, operation_id
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        chapter_id = str(payload.get("chapter_id") or "").strip()
        if not chapter_id:
            raise ControlPlaneError("CHAPTER_ID_REQUIRED", "缺少 chapter_id。", status_code=400)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID",
                "items 必须是数组。",
                status_code=400,
            )
        result = self.save_context(
            chapter_id=chapter_id,
            expected_chapter_revision=self._expected_chapter_revision(payload),
            items=items,
            actor=envelope.actor if isinstance(envelope.actor, dict) else {},
        )
        message = (
            f"章节 Context 未变化: {chapter_id}"
            if result.get("unchanged")
            else f"章节 Context 已保存: {chapter_id}@{result['context']['context_revision']}"
        )
        return {
            "accepted": True,
            "operation_status": "succeeded",
            "message": message,
            "chapter": result["chapter"],
            "context": result["context"],
            "unchanged": result.get("unchanged"),
        }
