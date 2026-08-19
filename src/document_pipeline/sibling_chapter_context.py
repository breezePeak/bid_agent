"""Controlled sibling-chapter context for leaf writers and chat.

Parent nodes are structural titles only. Many bid sections split one theme
across sibling leaves, e.g.:

    技术路线
    ├─ 总体技术路线   (narrative skeleton)
    ├─ 关键技术方法   (method detail)
    └─ 技术路线图     (visual presentation)

The leaf writer must not freely read the workspace. This service builds a
frozen, truncated projection of same-parent siblings only — purpose plus a
short draft summary bound to content revision/hash — so a diagram chapter can
follow the overall route without rewriting method chapters.
"""

from __future__ import annotations

import re
from typing import Any

from control_plane import ControlStore, WorkspaceContext

from .canonicalization import canonical_hash

SIBLING_SUMMARY_CHARS = 1600
MAX_SIBLINGS = 12

def _title_of(node: dict[str, Any], fallback: str = "") -> str:
    return str(node.get("title") or fallback or "").strip()


def _chapter_role(title: str, purpose: str = "") -> str:
    """Do not infer execution behavior from chapter naming conventions."""
    return "general"


def _blocks_to_text(content: dict[str, Any] | None) -> str:
    if not isinstance(content, dict):
        return ""
    blocks = content.get("blocks")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = str(
            block.get("content")
            or block.get("text")
            or block.get("body")
            or ""
        ).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _truncate(text: str, limit: int = SIBLING_SUMMARY_CHARS) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


class SiblingChapterContextService:
    """Project same-parent sibling leaves for the active chapter."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build_for_chapter(
        self,
        chapter: dict[str, Any],
        *,
        include_bodies: bool = False,
    ) -> dict[str, Any]:
        node = chapter.get("blueprint_node")
        node = node if isinstance(node, dict) else {}
        chapter_id = ControlStore._normalize_chapter_id(
            str(chapter.get("chapter_id") or node.get("chapter_id") or "")
        )
        parent_id = node.get("parent_chapter_id")
        parent_id = str(parent_id).strip() if parent_id is not None else ""
        title = _title_of(node, str(chapter.get("title") or chapter_id))
        purpose = str(node.get("purpose") or "").strip()
        current_role = _chapter_role(title, purpose)
        current_order = int(chapter.get("order") or node.get("order") or 0)

        blueprint_nodes = self._blueprint_nodes()
        siblings_nodes = self._same_parent_siblings(
            blueprint_nodes,
            chapter_id=chapter_id,
            parent_id=parent_id or None,
        )
        parent_node = next(
            (
                item
                for item in blueprint_nodes
                if str(item.get("chapter_id") or "").strip() == parent_id
            ),
            None,
        ) if parent_id else None

        sibling_rows: list[dict[str, Any]] = []
        for sibling_node in siblings_nodes[:MAX_SIBLINGS]:
            sibling_rows.append(
                self._project_sibling(
                    sibling_node,
                    current_order=current_order,
                    include_bodies=include_bodies,
                )
            )

        missing_upstream = [
            {
                "chapter_id": item["chapter_id"],
                "title": item["title"],
                "role": item["role"],
                "reason": "recommended_upstream_empty",
            }
            for item in sibling_rows
            if item.get("relation") == "upstream"
            and item.get("role") in {"overview", "general"}
            and not item.get("has_content")
            and current_role == "visual"
        ]
        # For visual chapters, also surface empty overview siblings even if order
        # ties put them "downstream" in blueprint numbering.
        if current_role == "visual":
            seen = {item["chapter_id"] for item in missing_upstream}
            for item in sibling_rows:
                if item["chapter_id"] in seen:
                    continue
                if item.get("role") == "overview" and not item.get("has_content"):
                    missing_upstream.append(
                        {
                            "chapter_id": item["chapter_id"],
                            "title": item["title"],
                            "role": item["role"],
                            "reason": "overview_sibling_empty",
                        }
                    )

        writing_policy = self._writing_policy(
            current_role=current_role,
            title=title,
            siblings=sibling_rows,
            missing_upstream=missing_upstream,
        )
        payload = {
            "schema_version": "v3.sibling-chapter-context.v1",
            "chapter_id": chapter_id,
            "chapter_title": title,
            "chapter_role": current_role,
            "parent_chapter_id": parent_id or None,
            "parent_title": _title_of(parent_node or {}, parent_id),
            "parent_purpose": str((parent_node or {}).get("purpose") or "").strip(),
            "siblings": sibling_rows,
            "missing_upstream": missing_upstream,
            "ready_for_dependent_writing": not bool(missing_upstream)
            if current_role == "visual"
            else True,
            "writing_policy": writing_policy,
        }
        payload["context_hash"] = canonical_hash(
            {
                "chapter_id": chapter_id,
                "parent_chapter_id": parent_id,
                "siblings": [
                    {
                        "chapter_id": item["chapter_id"],
                        "content_revision": item["content_revision"],
                        "content_hash": item["content_hash"],
                        "purpose": item["purpose"],
                    }
                    for item in sibling_rows
                ],
                "writing_policy": writing_policy,
            }
        )
        return payload

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
    def _same_parent_siblings(
        nodes: list[dict[str, Any]],
        *,
        chapter_id: str,
        parent_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return ordered sibling leaves under the same parent (exclude self)."""
        siblings: list[dict[str, Any]] = []
        for node in nodes:
            sid = str(node.get("chapter_id") or "").strip()
            if not sid or sid == chapter_id:
                continue
            node_parent = node.get("parent_chapter_id")
            node_parent = str(node_parent).strip() if node_parent is not None else ""
            # Root-level siblings share empty parent.
            if (parent_id or "") != node_parent:
                continue
            siblings.append(node)
        siblings.sort(
            key=lambda item: (
                int(item.get("order") or 0),
                str(item.get("chapter_id") or ""),
            )
        )
        return siblings

    def _project_sibling(
        self,
        node: dict[str, Any],
        *,
        current_order: int,
        include_bodies: bool = False,
    ) -> dict[str, Any]:
        chapter_id = ControlStore._normalize_chapter_id(
            str(node.get("chapter_id") or "")
        )
        title = _title_of(node, chapter_id)
        purpose = str(node.get("purpose") or "").strip()
        objectives = [
            str(item).strip()
            for item in (node.get("writing_objectives") or [])
            if str(item).strip()
        ]
        order = int(node.get("order") or 0)
        role = _chapter_role(title, purpose)
        relation = (
            "upstream"
            if order < current_order
            else "downstream"
            if order > current_order
            else "peer"
        )

        row = self.store.chapter_workspace(chapter_id)
        formal_rev = int((row or {}).get("formal_content_revision") or 0)
        head_rev = int((row or {}).get("head_content_revision") or 0)
        content_status = (
            "formal"
            if formal_rev > 0
            else "draft"
            if head_rev > 0
            else "empty"
        )
        content_revision = formal_rev or head_rev
        content_hash = ""
        summary = ""
        if include_bodies and row:
            formal = self.store.chapter_formal_content(chapter_id)
            head = self.store.chapter_content_head(chapter_id)
            source = formal if isinstance(formal, dict) else head if isinstance(head, dict) else None
            content_revision = int((source or {}).get("content_revision") or content_revision)
            content_hash = str((source or {}).get("content_hash") or "")
            full_text = _blocks_to_text(source)
            summary = _truncate(full_text) if full_text else ""
            content_status = (
                "formal"
                if formal and _blocks_to_text(formal)
                else "draft"
                if head and _blocks_to_text(head)
                else "empty"
            )
        has_content = bool(summary) if include_bodies else (formal_rev > 0 or head_rev > 0)

        return {
            "chapter_id": chapter_id,
            "title": title,
            "order": order,
            "relation": relation,
            "role": role,
            "purpose": purpose,
            "writing_objectives": objectives[:6],
            "has_content": has_content,
            "content_status": content_status,
            "content_revision": content_revision,
            "content_hash": content_hash,
            "summary": summary,
            "summary_chars": len(summary),
        }

    @staticmethod
    def _writing_policy(
        *,
        current_role: str,
        title: str,
        siblings: list[dict[str, Any]],
        missing_upstream: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rules = [
            "只能把同级兄弟章摘要当作边界与骨架参考，不得整段复制兄弟章正文。",
            "不要越权改写其他兄弟章的主责内容；本章只完成自己的响应职责。",
        ]
        if current_role == "visual":
            rules.extend(
                [
                    f"「{title}」是可视化/图示类章节：只呈现阶段、节点、顺序、依赖和输入输出，"
                    "不要展开关键技术方法细则。",
                    "阶段划分与节点命名必须优先对齐已有内容的上游兄弟章"
                    "（尤其是总体技术路线/总体方案）；若上游尚无正文，只依据其 purpose 搭骨架并"
                    "明确标注待上游确认。",
                    "关键技术方法类兄弟章仅用于确认哪些细节不该写进本图，避免与方法章重复。",
                ]
            )
        elif current_role == "method":
            rules.append(
                "方法章应承接总体路线中的阶段/节点，写可执行方法与质量控制，"
                "不要重写完整技术路线图。"
            )
        elif current_role == "overview":
            rules.append(
                "总述/总体路线章应给出完整阶段框架与主线逻辑，供同级图示与方法章引用；"
                "不要把全部方法细节塞进总述。"
            )

        recommended_read_order = [
            item["chapter_id"]
            for item in siblings
            if item.get("relation") == "upstream" or item.get("role") == "overview"
        ]
        return {
            "chapter_role": current_role,
            "rules": rules,
            "recommended_read_order": recommended_read_order,
            "blocked_by_empty_upstream": [
                item["chapter_id"] for item in missing_upstream
            ],
            "guidance": (
                "建议先完成："
                + "、".join(item["title"] for item in missing_upstream)
                + "，再写本章图示。"
                if missing_upstream
                else "同级兄弟章上下文已可用于约束本章边界。"
            ),
        }
