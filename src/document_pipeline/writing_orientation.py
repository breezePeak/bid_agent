"""Deterministic writing orientation for a materialized chapter.

Compiled before any inspect/search/draft step so the chapter agent first
knows: purpose, position in the bid, and relations to other chapters.
This is a Service projection from promoted Blueprint + outline + sibling
context + chapter-local materials. It is not a canonical Artifact.
"""

from __future__ import annotations

from typing import Any

from control_plane import ControlStore, WorkspaceContext

from .canonicalization import canonical_hash
from .sibling_chapter_context import _chapter_role, _title_of


MAX_OBJECTIVES = 6
MAX_RELATIONS = 10
MAX_MATERIAL_NOTES = 8
MAX_CROSS_REFS = 6


def _clean(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _role_label(role: str) -> str:
    return {
        "visual": "图示/路线图",
        "method": "方法细则",
        "overview": "总体骨架",
        "general": "正文",
    }.get(str(role or ""), str(role or "正文") or "正文")


def _relation_label(relation: str) -> str:
    return {
        "parent": "父章节",
        "child": "下级章节",
        "upstream": "上游同级",
        "downstream": "下游同级",
        "peer": "同级",
        "shared_score": "共享评分",
        "shared_requirement": "共享需求",
        "cross_reference": "交叉引用",
        "supporting": "支撑本章",
        "primary_elsewhere": "他章主责",
    }.get(str(relation or ""), str(relation or "相关") or "相关")


class WritingOrientationService:
    """Compile a frozen orientation packet for one chapter write."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build_for_chapter(
        self,
        chapter: dict[str, Any],
        *,
        outline_context: dict[str, Any] | None = None,
        sibling_context: dict[str, Any] | None = None,
        tender_requirements: list[dict[str, Any]] | None = None,
        scoring_requirements: list[dict[str, Any]] | None = None,
        inspected_chapters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        node = chapter.get("blueprint_node")
        node = node if isinstance(node, dict) else {}
        chapter_id = ControlStore._normalize_chapter_id(
            str(chapter.get("chapter_id") or node.get("chapter_id") or "")
        )
        if chapter_id and not str(node.get("purpose") or "").strip():
            for item in self._blueprint_nodes():
                if str(item.get("chapter_id") or "").strip() == chapter_id:
                    node = item
                    break
        title = str(chapter.get("title") or node.get("title") or chapter_id)
        purpose = str(node.get("purpose") or node.get("response_purpose") or "").strip()
        objectives = [
            str(item).strip()
            for item in (node.get("writing_objectives") or [])
            if str(item).strip()
        ][:MAX_OBJECTIVES]
        outline = outline_context if isinstance(outline_context, dict) else {}
        sibling = sibling_context if isinstance(sibling_context, dict) else {}
        if not outline:
            try:
                from .document_outline_context import DocumentOutlineContextService

                outline = DocumentOutlineContextService(self.context).build_for_chapter(
                    chapter
                )
            except Exception:
                outline = {}
        if not sibling:
            try:
                from .sibling_chapter_context import SiblingChapterContextService

                sibling = SiblingChapterContextService(self.context).build_for_chapter(
                    chapter
                )
            except Exception:
                sibling = {}

        position = dict(outline.get("position") or {})
        role = str(
            outline.get("current_role")
            or sibling.get("chapter_role")
            or _chapter_role(title, purpose)
        )
        is_leaf = bool(
            chapter.get("is_leaf")
            if "is_leaf" in chapter
            else position.get("is_leaf", True)
        )
        writing_purpose = {
            "chapter_id": chapter_id,
            "title": title,
            "purpose": purpose,
            "writing_objectives": objectives,
            "role": role,
            "role_label": _role_label(role),
            "section_domain": str(node.get("section_domain") or "technical"),
            "content_policy": str(node.get("content_policy") or "full"),
            "is_leaf": is_leaf,
            "primary_response_unit_ids": [
                str(item) for item in (node.get("primary_response_unit_ids") or []) if str(item)
            ][:8],
            "supporting_response_unit_ids": [
                str(item)
                for item in (node.get("supporting_response_unit_ids") or [])
                if str(item)
            ][:8],
            "required_mentions": [
                str(item).strip()
                for item in (node.get("required_mentions") or [])
                if str(item).strip()
            ][:6],
            "forbidden_topic_ids": [
                str(item)
                for item in (node.get("forbidden_topic_ids") or [])
                if str(item)
            ][:6],
        }
        document_position = {
            "chapter_id": chapter_id,
            "path": list(position.get("path") or []),
            "path_label": str(position.get("path_label") or title),
            "depth": int(position.get("depth") or 0),
            "order": int(position.get("order") or chapter.get("order") or node.get("order") or 0),
            "parent_chapter_id": position.get("parent_chapter_id")
            or node.get("parent_chapter_id"),
            "is_leaf": is_leaf,
            "sibling_ids": list(position.get("sibling_ids") or []),
        }
        relations = self._relations(
            chapter_id=chapter_id,
            node=node,
            outline=outline,
            sibling=sibling,
        )
        materials = self._materials(
            chapter=chapter,
            tender_requirements=tender_requirements or [],
            scoring_requirements=scoring_requirements or [],
            inspected_chapters=inspected_chapters or [],
        )
        payload = {
            "schema_version": "v3.writing-orientation.v1",
            "chapter_id": chapter_id,
            "writing_purpose": writing_purpose,
            "document_position": document_position,
            "chapter_relations": relations,
            "existing_materials": materials,
            "summary_text": _render_orientation_text(
                writing_purpose, document_position, relations, materials
            ),
            "confirmed": True,
            "confirmation_source": "deterministic_blueprint",
        }
        payload["orientation_hash"] = canonical_hash(
            {
                "chapter_id": chapter_id,
                "purpose": purpose,
                "path": document_position["path_label"],
                "relations": [
                    {
                        "chapter_id": item.get("chapter_id"),
                        "relation": item.get("relation"),
                    }
                    for item in relations.get("items") or []
                ],
                "materials": {
                    "chapter_context_item_count": materials.get(
                        "chapter_context_item_count"
                    ),
                    "tender_requirement_count": materials.get(
                        "tender_requirement_count"
                    ),
                    "scoring_requirement_count": materials.get(
                        "scoring_requirement_count"
                    ),
                },
            }
        )
        return payload

    def _relations(
        self,
        *,
        chapter_id: str,
        node: dict[str, Any],
        outline: dict[str, Any],
        sibling: dict[str, Any],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(
            *,
            related_id: str,
            title: str,
            relation: str,
            role: str = "",
            purpose: str = "",
            content_status: str = "",
            note: str = "",
        ) -> None:
            rid = str(related_id or "").strip()
            if not rid or rid == chapter_id:
                return
            if rid in seen:
                existing = next(
                    (item for item in items if item["chapter_id"] == rid),
                    None,
                )
                extra = _clean(note, 80)
                if existing is not None and extra and extra not in str(existing.get("note") or ""):
                    previous = str(existing.get("note") or "").strip()
                    existing["note"] = _clean(
                        f"{previous}；{extra}" if previous else extra,
                        120,
                    )
                return
            seen.add(rid)
            items.append(
                {
                    "chapter_id": rid,
                    "title": title or rid,
                    "relation": relation,
                    "relation_label": _relation_label(relation),
                    "role": role or "general",
                    "purpose": _clean(purpose, 80),
                    "content_status": content_status or "unknown",
                    "note": _clean(note, 80),
                }
            )

        parent_id = str(
            sibling.get("parent_chapter_id")
            or node.get("parent_chapter_id")
            or ""
        ).strip()
        if parent_id:
            add(
                related_id=parent_id,
                title=str(sibling.get("parent_title") or parent_id),
                relation="parent",
                purpose=str(sibling.get("parent_purpose") or ""),
                note="父节点通常只作结构标题，正文写在叶子章。",
            )
        for row in sibling.get("siblings") or []:
            if not isinstance(row, dict):
                continue
            add(
                related_id=str(row.get("chapter_id") or ""),
                title=str(row.get("title") or ""),
                relation=str(row.get("relation") or "peer"),
                role=str(row.get("role") or ""),
                purpose=str(row.get("purpose") or ""),
                content_status=str(row.get("content_status") or ""),
            )
        for row in outline.get("outline") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("parent_chapter_id") or "") != chapter_id:
                continue
            add(
                related_id=str(row.get("chapter_id") or ""),
                title=str(row.get("title") or ""),
                relation="child",
                role=str(row.get("role") or ""),
                purpose=str(row.get("purpose") or ""),
                content_status=str(row.get("content_status") or ""),
                note="下级叶子才写正文。",
            )

        current_req = {str(item) for item in (node.get("requirement_ids") or []) if item}
        current_scores = {str(item) for item in (node.get("score_point_ids") or []) if item}
        current_primary = {
            str(item) for item in (node.get("primary_response_unit_ids") or []) if item
        }
        for other in self._blueprint_nodes():
            oid = str(other.get("chapter_id") or "").strip()
            if not oid or oid == chapter_id:
                continue
            other_req = {str(item) for item in (other.get("requirement_ids") or []) if item}
            other_scores = {
                str(item) for item in (other.get("score_point_ids") or []) if item
            }
            other_primary = {
                str(item)
                for item in (other.get("primary_response_unit_ids") or [])
                if item
            }
            shared_req = current_req & other_req
            shared_score = current_scores & other_scores
            if shared_score:
                add(
                    related_id=oid,
                    title=_title_of(other, oid),
                    relation="shared_score",
                    role=_chapter_role(
                        _title_of(other, oid),
                        str(other.get("purpose") or ""),
                    ),
                    purpose=str(other.get("purpose") or ""),
                    note="共享评分点：" + "、".join(sorted(shared_score)[:3]),
                )
            elif shared_req:
                relation = (
                    "primary_elsewhere"
                    if other_primary and not current_primary
                    else "shared_requirement"
                )
                add(
                    related_id=oid,
                    title=_title_of(other, oid),
                    relation=relation,
                    role=_chapter_role(
                        _title_of(other, oid),
                        str(other.get("purpose") or ""),
                    ),
                    purpose=str(other.get("purpose") or ""),
                    note="共享需求：" + "、".join(sorted(shared_req)[:3]),
                )
            if len(items) >= MAX_RELATIONS:
                break

        for ref in (node.get("cross_references") or [])[:MAX_CROSS_REFS]:
            text = str(ref or "").strip()
            if not text:
                continue
            add(
                related_id=text,
                title=text,
                relation="cross_reference",
                note="Blueprint 声明的交叉引用。",
            )
            if len(items) >= MAX_RELATIONS:
                break

        recommended_inspect = [
            item["chapter_id"]
            for item in items
            if item.get("relation") in {"upstream", "parent", "shared_score", "primary_elsewhere"}
            and item.get("content_status") in {"formal", "draft"}
        ][:4]
        return {
            "parent_chapter_id": parent_id or None,
            "parent_title": str(sibling.get("parent_title") or "") or None,
            "items": items[:MAX_RELATIONS],
            "missing_upstream": list(sibling.get("missing_upstream") or []),
            "recommended_inspect_ids": recommended_inspect,
        }

    def _materials(
        self,
        *,
        chapter: dict[str, Any],
        tender_requirements: list[dict[str, Any]],
        scoring_requirements: list[dict[str, Any]],
        inspected_chapters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        context = chapter.get("context")
        context = context if isinstance(context, dict) else {}
        items = [
            item for item in (context.get("items") or []) if isinstance(item, dict)
        ]
        by_kind: dict[str, int] = {}
        notes: list[str] = []
        for item in items:
            kind = str(item.get("kind") or "KEY_FACT").strip() or "KEY_FACT"
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if by_kind.get("GOAL"):
            notes.append(f"已物化章节目的/写作目标 {by_kind['GOAL']} 条")
        if by_kind.get("SCORING_REQUIREMENT"):
            notes.append(f"已物化评分约束 {by_kind['SCORING_REQUIREMENT']} 条")
        if by_kind.get("TECHNICAL_CONSTRAINT"):
            notes.append(f"已物化技术约束 {by_kind['TECHNICAL_CONSTRAINT']} 条")
        if by_kind.get("KEY_FACT"):
            notes.append(f"已物化关键事实 {by_kind['KEY_FACT']} 条")
        tender_n = len(tender_requirements)
        score_n = len(scoring_requirements)
        if tender_n:
            notes.append(f"本章招标要求 {tender_n} 条")
        if score_n:
            notes.append(f"本章评分要求 {score_n} 条")
        inspected_n = len(
            [item for item in inspected_chapters if isinstance(item, dict)]
        )
        if inspected_n:
            notes.append(f"已打开他章只读详情 {inspected_n} 个")
        if not notes:
            notes.append("本章尚无额外资料，仅有目录与章节目的")
        return {
            "chapter_context_revision": int(context.get("context_revision") or 0),
            "chapter_context_item_count": len(items),
            "by_kind": by_kind,
            "tender_requirement_count": tender_n,
            "scoring_requirement_count": score_n,
            "inspected_peer_count": inspected_n,
            "has_purpose": bool(str((chapter.get("blueprint_node") or {}).get("purpose") or "")),
            "has_local_materials": bool(items or tender_n or score_n),
            "notes": notes[:MAX_MATERIAL_NOTES],
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


def compact_orientation_for_prompt(orientation: dict[str, Any] | None) -> dict[str, Any]:
    """Bounded orientation for LLM prompts — no peer bodies."""
    payload = orientation if isinstance(orientation, dict) else {}
    purpose = payload.get("writing_purpose") if isinstance(payload.get("writing_purpose"), dict) else {}
    position = (
        payload.get("document_position")
        if isinstance(payload.get("document_position"), dict)
        else {}
    )
    relations = (
        payload.get("chapter_relations")
        if isinstance(payload.get("chapter_relations"), dict)
        else {}
    )
    materials = (
        payload.get("existing_materials")
        if isinstance(payload.get("existing_materials"), dict)
        else {}
    )
    related = []
    for item in relations.get("items") or []:
        if not isinstance(item, dict):
            continue
        related.append(
            {
                "chapter_id": item.get("chapter_id"),
                "title": item.get("title"),
                "relation": item.get("relation"),
                "relation_label": item.get("relation_label"),
                "role": item.get("role"),
                "content_status": item.get("content_status"),
                "purpose": item.get("purpose"),
                "note": item.get("note"),
            }
        )
    return {
        "schema_version": payload.get("schema_version") or "v3.writing-orientation.v1",
        "chapter_id": payload.get("chapter_id"),
        "writing_purpose": {
            "title": purpose.get("title"),
            "purpose": purpose.get("purpose"),
            "writing_objectives": list(purpose.get("writing_objectives") or [])[:MAX_OBJECTIVES],
            "role": purpose.get("role"),
            "role_label": purpose.get("role_label"),
            "section_domain": purpose.get("section_domain"),
            "is_leaf": purpose.get("is_leaf"),
            "required_mentions": list(purpose.get("required_mentions") or [])[:6],
        },
        "document_position": {
            "path_label": position.get("path_label"),
            "depth": position.get("depth"),
            "order": position.get("order"),
            "parent_chapter_id": position.get("parent_chapter_id"),
            "is_leaf": position.get("is_leaf"),
        },
        "chapter_relations": {
            "parent_title": relations.get("parent_title"),
            "items": related[:MAX_RELATIONS],
            "missing_upstream": list(relations.get("missing_upstream") or [])[:4],
        },
        "existing_materials": {
            "chapter_context_item_count": materials.get("chapter_context_item_count") or 0,
            "by_kind": dict(materials.get("by_kind") or {}),
            "tender_requirement_count": materials.get("tender_requirement_count") or 0,
            "scoring_requirement_count": materials.get("scoring_requirement_count") or 0,
            "inspected_peer_count": materials.get("inspected_peer_count") or 0,
            "has_local_materials": bool(materials.get("has_local_materials")),
            "notes": list(materials.get("notes") or [])[:MAX_MATERIAL_NOTES],
        },
        "summary_text": payload.get("summary_text") or "",
        "confirmed": True,
    }


def public_orientation_view(orientation: dict[str, Any] | None) -> dict[str, Any]:
    """Small UI/stream projection."""
    compact = compact_orientation_for_prompt(orientation)
    purpose = compact.get("writing_purpose") or {}
    position = compact.get("document_position") or {}
    relations = compact.get("chapter_relations") or {}
    materials = compact.get("existing_materials") or {}
    related = []
    for item in relations.get("items") or []:
        related.append(
            {
                "title": item.get("title"),
                "relation_label": item.get("relation_label") or item.get("relation"),
                "role": item.get("role"),
                "content_status": item.get("content_status"),
            }
        )
    return {
        "title": purpose.get("title"),
        "purpose": purpose.get("purpose"),
        "objectives": list(purpose.get("writing_objectives") or []),
        "role_label": purpose.get("role_label"),
        "path_label": position.get("path_label"),
        "related": related[:8],
        "materials_notes": list(materials.get("notes") or []),
        "summary_text": compact.get("summary_text") or "",
    }


def _render_orientation_text(
    purpose: dict[str, Any],
    position: dict[str, Any],
    relations: dict[str, Any],
    materials: dict[str, Any],
) -> str:
    title = str(purpose.get("title") or "")
    path = str(position.get("path_label") or title)
    lines = [
        f"写作目的：本章《{title}》负责"
        + (str(purpose.get("purpose") or "完成本节响应职责") or "完成本节响应职责")
        + f"（角色={purpose.get('role_label') or '正文'}）。",
        f"全书位置：{path}"
        + ("，叶子章节，写正文。" if purpose.get("is_leaf") else "，目录父节点，不写正文。"),
    ]
    objectives = list(purpose.get("writing_objectives") or [])
    if objectives:
        lines.append("写作目标：" + "；".join(objectives[:4]))
    related_bits = []
    for item in (relations.get("items") or [])[:6]:
        if not isinstance(item, dict):
            continue
        related_bits.append(
            f"{item.get('relation_label') or item.get('relation')}「"
            f"{item.get('title') or item.get('chapter_id')}」"
        )
    if related_bits:
        lines.append("与其他章节关系：" + "；".join(related_bits))
    else:
        lines.append("与其他章节关系：目录中暂无需要同步的兄弟/交叉章节。")
    notes = list(materials.get("notes") or [])
    if notes:
        lines.append("已有资料：" + "；".join(notes[:5]))
    return "\n".join(lines)
