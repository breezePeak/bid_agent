"""Document outline awareness with progressive chapter inspection.

Default agent view is title-level only:

    outline titles + position + content status

Body / purpose details of other chapters are loaded only when the chapter
agent explicitly selects chapter_ids to inspect.  Human UI may still open a
read-only peer view on click; that path is separate from the default prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .canonicalization import canonical_hash
from .sibling_chapter_context import (
    _blocks_to_text,
    _chapter_role,
    _title_of,
    _truncate,
)

MAX_OUTLINE_NODES = 200
MAX_INSPECT = 4
READONLY_SUMMARY_CHARS = 1800
READONLY_CONTEXT_ITEMS = 12


class DocumentOutlineContextService:
    """Title-first outline + on-demand read-only peer inspection."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build_for_chapter(self, chapter: dict[str, Any]) -> dict[str, Any]:
        """Build outline skeleton without preloading other chapter bodies."""
        nodes = self._blueprint_nodes()
        if not nodes:
            return {
                "schema_version": "v3.document-outline-context.v2",
                "disclosure": "titles_first",
                "current_chapter_id": str(chapter.get("chapter_id") or ""),
                "outline": [],
                "outline_tree": [],
                "position": {},
                "related_summaries": [],
                "access": {
                    "mode": "read_only_outline",
                    "can_edit_other_chapters": False,
                    "detail_policy": "on_demand",
                },
                "writing_policy": {
                    "rules": ["目录尚未就绪，仅可使用本章与公共项目事实。"],
                    "guidance": "请先完成目录规划。",
                },
            }

        current_id = ControlStore._normalize_chapter_id(
            str(
                chapter.get("chapter_id")
                or (chapter.get("blueprint_node") or {}).get("chapter_id")
                or ""
            )
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
        for kids in children_map.values():
            kids.sort(
                key=lambda item: (
                    int(item.get("order") or 0),
                    str(item.get("chapter_id") or ""),
                )
            )

        leaf_ids = self._leaf_ids(nodes)
        flat: list[dict[str, Any]] = []

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
        current_row = next((item for item in flat if item["chapter_id"] == current_id), None)
        current_role = str((current_row or {}).get("role") or "general")
        writing_policy = self._writing_policy(
            current_id=current_id,
            current_title=str((current_row or {}).get("title") or current_id),
            current_role=current_role,
            position=position,
        )
        payload = {
            "schema_version": "v3.document-outline-context.v2",
            "disclosure": "titles_first",
            "current_chapter_id": current_id,
            "current_chapter_title": str((current_row or {}).get("title") or ""),
            "current_role": current_role,
            "position": position,
            "outline": flat,
            "outline_tree": tree,
            # Progressive: never preload peer bodies into the default projection.
            "related_summaries": [],
            "access": {
                "mode": "read_only_outline",
                "can_edit_other_chapters": False,
                "detail_policy": "on_demand",
                "note": (
                    "默认仅暴露目录标题与状态；需要他章细节时由章节 Agent 选择"
                    " chapter_id 再加载只读详情。不得修改其他章节。"
                ),
            },
            "writing_policy": writing_policy,
        }
        payload["context_hash"] = canonical_hash(
            {
                "current_chapter_id": current_id,
                "disclosure": "titles_first",
                "outline": [
                    {
                        "chapter_id": item["chapter_id"],
                        "title": item["title"],
                        "content_status": item["content_status"],
                        "content_revision": item["content_revision"],
                    }
                    for item in flat
                ],
            }
        )
        return payload

    def plan_inspections(
        self,
        *,
        viewer_chapter_id: str,
        outline_context: dict[str, Any] | None,
        task: str,
        max_inspect: int = MAX_INSPECT,
    ) -> dict[str, Any]:
        """Model selects which outline chapters need detail (if any)."""
        outline = compact_outline_for_prompt(outline_context or {})
        known_ids = {
            str(item.get("id") or "").strip()
            for item in (outline.get("outline") or [])
            if str(item.get("id") or "").strip()
        }
        viewer = str(viewer_chapter_id or outline.get("current_chapter_id") or "").strip()
        known_ids.discard(viewer)
        if not known_ids:
            return {
                "inspect_ids": [],
                "reason": "目录中没有其他可检查章节。",
                "decision_source": "empty_outline",
            }

        system = (
            "你是标书章节 Agent 的目录阅读规划器。"
            "默认你只能看到目录标题树与各章状态，看不到他章正文。"
            "请判断完成本任务是否必须打开其他章节的只读详情。"
            "原则：能只靠标题/状态/本章信息完成就不要 inspect；"
            "只选择真正需要的 chapter_id；最多 "
            f"{max(1, int(max_inspect))} 个；不要选择当前章。"
            "只输出 JSON："
            '{"inspect_ids":["chapter_id",...],"reason":"简短中文理由"}'
        )
        user = {
            "task": _truncate(task, 400),
            "current_chapter_id": viewer,
            "position": outline.get("position") or {},
            "outline_titles_only": outline.get("outline") or [],
        }
        try:
            from llm_client import chat

            try:
                raw = chat(
                    [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": json.dumps(user, ensure_ascii=False),
                        },
                    ],
                    temperature=0.0,
                )
            except TypeError:
                raw = chat(
                    [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": json.dumps(user, ensure_ascii=False),
                        },
                    ]
                )
        except Exception:
            return {
                "inspect_ids": [],
                "reason": "目录检查规划不可用，仅使用标题树继续。",
                "decision_source": "agent_unavailable",
            }

        payload = _parse_json_object(str(raw or ""))
        if not isinstance(payload, dict):
            return {
                "inspect_ids": [],
                "reason": "目录检查规划输出无效，仅使用标题树继续。",
                "decision_source": "invalid_plan",
            }
        selected: list[str] = []
        for item in payload.get("inspect_ids") or []:
            chapter_id = str(item or "").strip()
            if not chapter_id or chapter_id == viewer or chapter_id not in known_ids:
                continue
            if chapter_id not in selected:
                selected.append(chapter_id)
            if len(selected) >= max(1, int(max_inspect)):
                break
        return {
            "inspect_ids": selected,
            "reason": _truncate(payload.get("reason") or "", 200)
            or ("需要打开相关章节详情" if selected else "标题树已足够"),
            "decision_source": "chapter_agent",
        }

    def load_inspections(
        self,
        inspect_ids: list[str],
        *,
        viewer_chapter_id: str,
    ) -> list[dict[str, Any]]:
        """Load bounded read-only details for selected chapter ids only."""
        views: list[dict[str, Any]] = []
        for chapter_id in inspect_ids[:MAX_INSPECT]:
            try:
                view = self.readonly_chapter_view(
                    chapter_id,
                    viewer_chapter_id=viewer_chapter_id,
                )
            except ControlPlaneError:
                continue
            views.append(
                {
                    "chapter_id": view.get("chapter_id"),
                    "title": view.get("title"),
                    "relation_hint": "inspected",
                    "role": view.get("role"),
                    "purpose": view.get("purpose"),
                    "writing_objectives": view.get("writing_objectives") or [],
                    "content_status": view.get("content_status"),
                    "content_revision": view.get("content_revision"),
                    "summary": view.get("summary") or "",
                    "context_items": view.get("context_items") or [],
                    "access": {"mode": "read_only", "can_edit": False},
                }
            )
        return views

    def plan_and_load_inspections(
        self,
        *,
        viewer_chapter_id: str,
        outline_context: dict[str, Any] | None,
        task: str,
        max_inspect: int = MAX_INSPECT,
    ) -> dict[str, Any]:
        plan = self.plan_inspections(
            viewer_chapter_id=viewer_chapter_id,
            outline_context=outline_context,
            task=task,
            max_inspect=max_inspect,
        )
        views = self.load_inspections(
            list(plan.get("inspect_ids") or []),
            viewer_chapter_id=viewer_chapter_id,
        )
        return {
            **plan,
            "views": views,
        }

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
        # Status only — do not load peer body text into the default outline.
        formal_rev = int((row or {}).get("formal_content_revision") or 0)
        head_rev = int((row or {}).get("head_content_revision") or 0)
        content_status = (
            "formal"
            if formal_rev > 0
            else "draft"
            if head_rev > 0
            else "empty"
        )
        return {
            "chapter_id": chapter_id,
            "title": title,
            "parent_chapter_id": parent_id,
            "order": int(node.get("order") or 0),
            "depth": depth,
            "is_leaf": is_leaf,
            "is_current": is_current,
            "role": _chapter_role(title, purpose),
            # Kept on full projection for UI; stripped from agent title view.
            "purpose": purpose,
            "materialized": row is not None,
            "status": str((row or {}).get("status") or ("projected" if row is None else "")),
            "content_status": content_status,
            "has_content": formal_rev > 0 or head_rev > 0,
            "content_revision": formal_rev or head_rev,
            "content_hash": "",
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

    @staticmethod
    def _writing_policy(
        *,
        current_id: str,
        current_title: str,
        current_role: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        path_label = str(position.get("path_label") or current_title)
        rules = [
            f"你当前负责章节「{current_title}」（{current_id}），目录位置：{path_label}。",
            "默认只能看到整份目录的标题、层级与状态，看不到其他章节正文。",
            "只有当你明确选择 inspect 的章节，才会得到对应只读详情。",
            "不得修改、覆盖或整段搬用其他章节主责正文；只能完成本章职责。",
            "父节点通常只是结构标题；正文写在叶子章节。",
        ]
        if current_role == "visual":
            rules.append(
                "图示/路线图章：若需要阶段骨架，先 inspect 上游「总体技术路线」等章节，"
                "不要假设已预加载其正文。"
            )
        return {
            "chapter_role": current_role,
            "rules": rules,
            "guidance": "先看标题树定位；确有必要时再打开他章只读详情。",
            "recommended_read": [],
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
    """Title-first outline for LLM prompts: no peer bodies, no purpose dump."""
    outline = outline_context.get("outline") if isinstance(outline_context, dict) else []
    compact_nodes = []
    for item in outline or []:
        if not isinstance(item, dict):
            continue
        compact_nodes.append(
            {
                "id": item.get("chapter_id") or item.get("id"),
                "title": item.get("title"),
                "depth": item.get("depth"),
                "parent": item.get("parent_chapter_id") or item.get("parent"),
                "leaf": item.get("is_leaf") if "is_leaf" in item else item.get("leaf"),
                "current": item.get("is_current") if "is_current" in item else item.get("current"),
                "role": item.get("role"),
                "status": item.get("content_status") or item.get("status"),
            }
        )
    return {
        "disclosure": "titles_first",
        "current_chapter_id": outline_context.get("current_chapter_id")
        if isinstance(outline_context, dict)
        else None,
        "current_role": outline_context.get("current_role")
        if isinstance(outline_context, dict)
        else None,
        "position": (outline_context or {}).get("position") or {},
        "access": (outline_context or {}).get("access")
        or {"detail_policy": "on_demand", "can_edit_other_chapters": False},
        "writing_policy": (outline_context or {}).get("writing_policy") or {},
        "outline": compact_nodes,
        # Explicit empty: details arrive only via inspected_chapters.
        "related_summaries": [],
        "inspected_chapters": [],
    }


def compact_sibling_for_prompt(sibling_context: dict[str, Any] | None) -> dict[str, Any]:
    """Sibling metadata without body summaries (progressive disclosure)."""
    payload = sibling_context if isinstance(sibling_context, dict) else {}
    siblings = []
    for item in payload.get("siblings") or []:
        if not isinstance(item, dict):
            continue
        siblings.append(
            {
                "chapter_id": item.get("chapter_id"),
                "title": item.get("title"),
                "relation": item.get("relation"),
                "role": item.get("role"),
                "content_status": item.get("content_status"),
                "has_content": bool(item.get("has_content")),
            }
        )
    return {
        "chapter_role": payload.get("chapter_role"),
        "parent_chapter_id": payload.get("parent_chapter_id"),
        "parent_title": payload.get("parent_title"),
        "siblings": siblings,
        "missing_upstream": list(payload.get("missing_upstream") or []),
        "writing_policy": payload.get("writing_policy") or {},
        "disclosure": "titles_first",
    }


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
