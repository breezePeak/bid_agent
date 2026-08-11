"""Document-wide outline awareness for chapter agents and workbench UI.

Each leaf chapter needs to know its place in the full outline and may inspect
other chapters as read-only context.  This is a control-plane projection from
promoted ChapterBlueprint + chapter workspace heads — not a second authority
and not a write path to other chapters.
"""

from __future__ import annotations

from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .canonicalization import canonical_hash
from .sibling_chapter_context import (
    SIBLING_SUMMARY_CHARS,
    _blocks_to_text,
    _chapter_role,
    _title_of,
    _truncate,
)

MAX_OUTLINE_NODES = 200
MAX_RELATED_SUMMARIES = 8
READONLY_SUMMARY_CHARS = 1800
READONLY_CONTEXT_ITEMS = 12


class DocumentOutlineContextService:
    """Full-outline, read-only peer awareness for one active chapter."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build_for_chapter(self, chapter: dict[str, Any]) -> dict[str, Any]:
        nodes = self._blueprint_nodes()
        if not nodes:
            return {
                "schema_version": "v3.document-outline-context.v1",
                "current_chapter_id": str(chapter.get("chapter_id") or ""),
                "outline": [],
                "outline_tree": [],
                "position": {},
                "related_summaries": [],
                "access": {
                    "mode": "read_only_outline",
                    "can_edit_other_chapters": False,
                },
                "writing_policy": {
                    "rules": ["目录尚未就绪，仅可使用本章与公共项目事实。"],
                    "guidance": "请先完成目录规划。",
                },
            }

        current_id = ControlStore._normalize_chapter_id(
            str(chapter.get("chapter_id") or chapter.get("blueprint_node", {}).get("chapter_id") or "")
        )
        by_id = {
            str(node.get("chapter_id") or "").strip(): node
            for node in nodes
            if str(node.get("chapter_id") or "").strip()
        }
        children_map: dict[str | None, list[dict[str, Any]]] = {}
        for node in nodes:
            cid = str(node.get("chapter_id") or "").strip()
            if not cid:
                continue
            parent = node.get("parent_chapter_id")
            parent_key = str(parent).strip() if parent is not None else None
            children_map.setdefault(parent_key, []).append(node)
        for parent_key, kids in children_map.items():
            kids.sort(
                key=lambda item: (
                    int(item.get("order") or 0),
                    str(item.get("chapter_id") or ""),
                )
            )

        leaf_ids = self._leaf_ids(nodes)
        flat: list[dict[str, Any]] = []
        tree: list[dict[str, Any]] = []

        def walk(parent_key: str | None, depth: int) -> list[dict[str, Any]]:
            level_nodes: list[dict[str, Any]] = []
            for node in children_map.get(parent_key, []):
                cid = str(node.get("chapter_id") or "").strip()
                if not cid:
                    continue
                row = self._outline_row(
                    node,
                    depth=depth,
                    is_leaf=cid in leaf_ids,
                    is_current=(cid == current_id),
                )
                flat.append(row)
                child_nodes = walk(cid, depth + 1)
                level_nodes.append({**row, "children": child_nodes})
                if len(flat) >= MAX_OUTLINE_NODES:
                    break
            return level_nodes

        tree = walk(None, 0)
        position = self._position(current_id, by_id, flat)
        related = self._related_summaries(
            current_id=current_id,
            by_id=by_id,
            flat=flat,
            leaf_ids=leaf_ids,
        )
        current_row = next((item for item in flat if item["chapter_id"] == current_id), None)
        current_role = str((current_row or {}).get("role") or "general")
        writing_policy = self._writing_policy(
            current_id=current_id,
            current_title=str((current_row or {}).get("title") or current_id),
            current_role=current_role,
            position=position,
            related=related,
        )
        payload = {
            "schema_version": "v3.document-outline-context.v1",
            "current_chapter_id": current_id,
            "current_chapter_title": str((current_row or {}).get("title") or ""),
            "current_role": current_role,
            "position": position,
            "outline": flat,
            "outline_tree": tree,
            "related_summaries": related,
            "access": {
                "mode": "read_only_outline",
                "can_edit_other_chapters": False,
                "note": "可查看整份目录与他章只读摘要，不得修改其他章节正文或上下文。",
            },
            "writing_policy": writing_policy,
        }
        payload["context_hash"] = canonical_hash(
            {
                "current_chapter_id": current_id,
                "outline": [
                    {
                        "chapter_id": item["chapter_id"],
                        "content_status": item["content_status"],
                        "content_revision": item["content_revision"],
                        "purpose": item["purpose"],
                    }
                    for item in flat
                ],
                "related": [
                    {
                        "chapter_id": item["chapter_id"],
                        "content_revision": item.get("content_revision"),
                        "summary_chars": len(item.get("summary") or ""),
                    }
                    for item in related
                ],
            }
        )
        return payload

    def readonly_chapter_view(
        self,
        target_chapter_id: str,
        *,
        viewer_chapter_id: str = "",
    ) -> dict[str, Any]:
        """Return a read-only projection of another (or any) chapter."""
        target_id = ControlStore._normalize_chapter_id(target_chapter_id)
        nodes = self._blueprint_nodes()
        node = next(
            (
                item
                for item in nodes
                if str(item.get("chapter_id") or "").strip() == target_id
            ),
            None,
        )
        if node is None:
            # Allow archived/orphan workspace rows that still exist in control store.
            row = self.store.chapter_workspace(target_id)
            if row is None:
                raise ControlPlaneError(
                    "CHAPTER_NOT_IN_BLUEPRINT",
                    f"目录中不存在章节: {target_id}",
                    status_code=404,
                )
            title = str(row.get("title") or target_id)
            purpose = ""
            objectives: list[str] = []
            parent_id = row.get("parent_chapter_id")
            is_leaf = True
            order = int(row.get("order") or 0)
        else:
            title = _title_of(node, target_id)
            purpose = str(node.get("purpose") or "").strip()
            objectives = [
                str(item).strip()
                for item in (node.get("writing_objectives") or [])
                if str(item).strip()
            ][:8]
            parent_id = node.get("parent_chapter_id")
            parent_id = str(parent_id).strip() if parent_id is not None else None
            leaf_ids = self._leaf_ids(nodes)
            is_leaf = target_id in leaf_ids
            order = int(node.get("order") or 0)

        formal = self.store.chapter_formal_content(target_id)
        head = self.store.chapter_content_head(target_id)
        source = formal if isinstance(formal, dict) else head if isinstance(head, dict) else None
        summary = _truncate(_blocks_to_text(source), READONLY_SUMMARY_CHARS)
        content_revision = int((source or {}).get("content_revision") or 0)
        content_hash = str((source or {}).get("content_hash") or "")
        content_status = (
            "formal"
            if formal and _blocks_to_text(formal)
            else "draft"
            if head and _blocks_to_text(head)
            else "empty"
        )
        context_head = self.store.chapter_context_head(target_id)
        context_items = []
        if isinstance(context_head, dict):
            for item in (context_head.get("items") or [])[:READONLY_CONTEXT_ITEMS]:
                if not isinstance(item, dict):
                    continue
                context_items.append(
                    {
                        "kind": str(item.get("kind") or ""),
                        "title": str(item.get("title") or ""),
                        "body": _truncate(str(item.get("body") or ""), 400),
                        "source": str(item.get("source") or ""),
                    }
                )

        viewer = str(viewer_chapter_id or "").strip()
        return {
            "schema_version": "v3.chapter-readonly-view.v1",
            "access": {
                "mode": "read_only",
                "can_edit": False,
                "viewer_chapter_id": viewer or None,
                "is_self": bool(viewer and viewer == target_id),
            },
            "chapter_id": target_id,
            "title": title,
            "parent_chapter_id": parent_id,
            "order": order,
            "is_leaf": is_leaf,
            "role": _chapter_role(title, purpose),
            "purpose": purpose,
            "writing_objectives": objectives,
            "content_status": content_status,
            "content_revision": content_revision,
            "content_hash": content_hash,
            "summary": summary,
            "context_revision": int(
                (context_head or {}).get("context_revision") or 0
            )
            if isinstance(context_head, dict)
            else 0,
            "context_items": context_items,
        }

    def _outline_row(
        self,
        node: dict[str, Any],
        *,
        depth: int,
        is_leaf: bool,
        is_current: bool,
    ) -> dict[str, Any]:
        chapter_id = ControlStore._normalize_chapter_id(
            str(node.get("chapter_id") or "")
        )
        title = _title_of(node, chapter_id)
        purpose = str(node.get("purpose") or "").strip()
        parent = node.get("parent_chapter_id")
        parent_id = str(parent).strip() if parent is not None else None
        row = self.store.chapter_workspace(chapter_id)
        formal = self.store.chapter_formal_content(chapter_id) if row else None
        head = self.store.chapter_content_head(chapter_id) if row else None
        has_formal = bool(formal and _blocks_to_text(formal))
        has_draft = bool(head and _blocks_to_text(head))
        content_status = (
            "formal"
            if has_formal
            else "draft"
            if has_draft
            else "empty"
        )
        source = formal if has_formal else head if has_draft else None
        return {
            "chapter_id": chapter_id,
            "title": title,
            "parent_chapter_id": parent_id,
            "order": int(node.get("order") or 0),
            "depth": depth,
            "is_leaf": is_leaf,
            "is_current": is_current,
            "role": _chapter_role(title, purpose),
            "purpose": purpose,
            "materialized": row is not None,
            "status": str((row or {}).get("status") or ("projected" if row is None else "")),
            "content_status": content_status,
            "has_content": bool(has_formal or has_draft),
            "content_revision": int((source or {}).get("content_revision") or 0),
            "content_hash": str((source or {}).get("content_hash") or ""),
        }

    def _position(
        self,
        current_id: str,
        by_id: dict[str, dict[str, Any]],
        flat: list[dict[str, Any]],
    ) -> dict[str, Any]:
        current = next((item for item in flat if item["chapter_id"] == current_id), None)
        if current is None:
            return {
                "chapter_id": current_id,
                "path": [],
                "depth": 0,
                "parent_chapter_id": None,
                "sibling_ids": [],
            }
        path: list[dict[str, str]] = []
        cursor = current_id
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            node = by_id.get(cursor)
            if node is None:
                break
            path.append(
                {
                    "chapter_id": cursor,
                    "title": _title_of(node, cursor),
                }
            )
            parent = node.get("parent_chapter_id")
            cursor = str(parent).strip() if parent is not None else ""
        path.reverse()
        parent_id = current.get("parent_chapter_id")
        sibling_ids = [
            item["chapter_id"]
            for item in flat
            if item.get("parent_chapter_id") == parent_id
            and item["chapter_id"] != current_id
        ]
        return {
            "chapter_id": current_id,
            "title": current.get("title"),
            "path": path,
            "depth": int(current.get("depth") or 0),
            "parent_chapter_id": parent_id,
            "order": int(current.get("order") or 0),
            "is_leaf": bool(current.get("is_leaf")),
            "sibling_ids": sibling_ids,
            "path_label": " / ".join(item["title"] for item in path),
        }

    def _related_summaries(
        self,
        *,
        current_id: str,
        by_id: dict[str, dict[str, Any]],
        flat: list[dict[str, Any]],
        leaf_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Bounded read-only summaries: ancestors + same-parent siblings first."""
        current = next((item for item in flat if item["chapter_id"] == current_id), None)
        if current is None:
            return []
        parent_id = current.get("parent_chapter_id")
        selected_ids: list[str] = []

        # Ancestors (excluding self).
        cursor = parent_id
        while cursor and cursor in by_id and cursor not in selected_ids:
            selected_ids.append(str(cursor))
            parent = by_id[str(cursor)].get("parent_chapter_id")
            cursor = str(parent).strip() if parent is not None else ""

        # Same-parent siblings (prefer leaves with content, then all).
        siblings = [
            item
            for item in flat
            if item.get("parent_chapter_id") == parent_id
            and item["chapter_id"] != current_id
        ]
        siblings.sort(
            key=lambda item: (
                0 if item.get("has_content") else 1,
                int(item.get("order") or 0),
            )
        )
        for item in siblings:
            if item["chapter_id"] not in selected_ids:
                selected_ids.append(item["chapter_id"])

        # Nearby leaves earlier in the document (for cross-section awareness).
        earlier_leaves = [
            item
            for item in flat
            if item.get("is_leaf")
            and item["chapter_id"] != current_id
            and item["chapter_id"] not in selected_ids
            and int(item.get("order") or 0) < int(current.get("order") or 0)
            and item.get("has_content")
        ]
        earlier_leaves.sort(key=lambda item: int(item.get("order") or 0), reverse=True)
        for item in earlier_leaves[:3]:
            selected_ids.append(item["chapter_id"])

        rows: list[dict[str, Any]] = []
        for chapter_id in selected_ids[:MAX_RELATED_SUMMARIES]:
            node = by_id.get(chapter_id) or {}
            outline_row = next(
                (item for item in flat if item["chapter_id"] == chapter_id),
                None,
            )
            formal = self.store.chapter_formal_content(chapter_id)
            head = self.store.chapter_content_head(chapter_id)
            source = formal if isinstance(formal, dict) else head if isinstance(head, dict) else None
            summary = _truncate(_blocks_to_text(source), SIBLING_SUMMARY_CHARS)
            relation = "ancestor"
            if outline_row and outline_row.get("parent_chapter_id") == parent_id:
                relation = (
                    "upstream_sibling"
                    if int(outline_row.get("order") or 0) < int(current.get("order") or 0)
                    else "downstream_sibling"
                    if int(outline_row.get("order") or 0) > int(current.get("order") or 0)
                    else "sibling"
                )
            elif outline_row and outline_row.get("is_leaf"):
                relation = "earlier_section"
            rows.append(
                {
                    "chapter_id": chapter_id,
                    "title": _title_of(node, chapter_id)
                    if node
                    else str((outline_row or {}).get("title") or chapter_id),
                    "relation": relation,
                    "role": (outline_row or {}).get("role")
                    or _chapter_role(_title_of(node, chapter_id), str(node.get("purpose") or "")),
                    "purpose": str(node.get("purpose") or "").strip(),
                    "is_leaf": chapter_id in leaf_ids,
                    "has_content": bool(summary),
                    "content_status": (outline_row or {}).get("content_status") or "empty",
                    "content_revision": int((source or {}).get("content_revision") or 0),
                    "content_hash": str((source or {}).get("content_hash") or ""),
                    "summary": summary,
                }
            )
        return rows

    @staticmethod
    def _writing_policy(
        *,
        current_id: str,
        current_title: str,
        current_role: str,
        position: dict[str, Any],
        related: list[dict[str, Any]],
    ) -> dict[str, Any]:
        path_label = str(position.get("path_label") or current_title)
        rules = [
            f"你当前负责章节「{current_title}」（{current_id}），目录位置：{path_label}。",
            "你可以看到整份目录结构与其他章节的只读摘要，用于判断边界、顺序和交叉引用。",
            "不得修改、覆盖或整段搬用其他章节的主责正文；只能完成本章职责。",
            "父节点通常只是结构标题；正文写在叶子章节。",
        ]
        if current_role == "visual":
            rules.append(
                "图示/路线图章：用目录中上游总体/方法章的阶段骨架成图，不展开方法细则。"
            )
        missing = [
            item["title"]
            for item in related
            if item.get("relation") in {"upstream_sibling", "ancestor"}
            and item.get("role") in {"overview", "general"}
            and not item.get("has_content")
        ]
        guidance = (
            "建议先读/先完成：" + "、".join(missing[:4])
            if missing and current_role == "visual"
            else "可按目录位置参考相关章节只读摘要，保持本章边界清晰。"
        )
        return {
            "chapter_role": current_role,
            "rules": rules,
            "guidance": guidance,
            "recommended_read": [
                item["chapter_id"]
                for item in related
                if item.get("has_content")
            ][:6],
        }

    def _blueprint_nodes(self) -> list[dict[str, Any]]:
        active = self.store.v3_active_artifact("ChapterBlueprint")
        if active is None:
            return []
        payload = active.get("payload")
        if not isinstance(payload, dict):
            return []
        nodes = payload.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [item for item in nodes if isinstance(item, dict)]

    @staticmethod
    def _leaf_ids(nodes: list[dict[str, Any]]) -> set[str]:
        node_ids = {
            str(node.get("chapter_id") or "").strip()
            for node in nodes
            if str(node.get("chapter_id") or "").strip()
        }
        parent_ids = {
            str(node.get("parent_chapter_id") or "").strip()
            for node in nodes
            if str(node.get("parent_chapter_id") or "").strip()
        }
        return node_ids - parent_ids


def compact_outline_for_prompt(outline_context: dict[str, Any]) -> dict[str, Any]:
    """Shrink outline payload for LLM prompts."""
    outline = outline_context.get("outline") if isinstance(outline_context, dict) else []
    compact_nodes = []
    for item in outline or []:
        if not isinstance(item, dict):
            continue
        compact_nodes.append(
            {
                "id": item.get("chapter_id"),
                "title": item.get("title"),
                "depth": item.get("depth"),
                "parent": item.get("parent_chapter_id"),
                "leaf": item.get("is_leaf"),
                "current": item.get("is_current"),
                "role": item.get("role"),
                "status": item.get("content_status"),
                "purpose": _truncate(str(item.get("purpose") or ""), 80),
            }
        )
    related = []
    for item in outline_context.get("related_summaries") or []:
        if not isinstance(item, dict):
            continue
        related.append(
            {
                "id": item.get("chapter_id"),
                "title": item.get("title"),
                "relation": item.get("relation"),
                "role": item.get("role"),
                "purpose": _truncate(str(item.get("purpose") or ""), 100),
                "summary": _truncate(str(item.get("summary") or ""), 500),
                "status": item.get("content_status"),
            }
        )
    return {
        "current_chapter_id": outline_context.get("current_chapter_id"),
        "position": outline_context.get("position") or {},
        "access": outline_context.get("access") or {},
        "writing_policy": outline_context.get("writing_policy") or {},
        "outline": compact_nodes,
        "related_summaries": related,
    }
