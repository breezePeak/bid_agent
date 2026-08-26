from __future__ import annotations

from collections import defaultdict
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .artifact_promotion import HumanGateService
from .canonicalization import canonical_hash
from .chapter_semantics import project_chapter_semantic_requirements
from .chapter_workspace import ChapterWorkspaceService
from .chapter_writing_outline import compile_chapter_writing_plan
_COVERAGE_STATES = {
    "fully_covered",
    "partially_covered",
    "not_covered",
}


def project_rewrite_coverage(
    writing_plan: dict[str, Any],
    rewrite_mode: str,
    legacy_sources: list[dict[str, Any]],
    required_changes: list[str],
) -> list[dict[str, Any]]:
    if rewrite_mode not in {"copy", "light_edit", "restructure", "new_write"}:
        raise ValueError(f"未知 rewrite_mode: {rewrite_mode}")
    matched_block_ids = list(dict.fromkeys(
        str(item.get("block_id") or "")
        for item in legacy_sources
        if isinstance(item, dict) and item.get("block_id")
    ))
    status = {
        "copy": "fully_covered",
        "light_edit": "partially_covered",
        "restructure": "partially_covered",
        "new_write": "not_covered",
    }[rewrite_mode]
    risk = (
        "；".join(str(item) for item in required_changes if str(item).strip())
        if rewrite_mode in {"light_edit", "restructure"}
        else ("未引用旧文，需要按当前写作计划新写。" if rewrite_mode == "new_write" else "")
    )
    return [
        {
            "writing_block_id": str(block.get("block_id") or ""),
            "heading": str(block.get("heading") or ""),
            "must_answer": str(block.get("must_answer") or ""),
            "status": status,
            "best_score": 1.0 if rewrite_mode == "copy" else 0.0,
            "matched_block_ids": (
                [] if rewrite_mode == "new_write" else matched_block_ids
            ),
            "risk": risk,
        }
        for block in writing_plan.get("blocks") or []
        if isinstance(block, dict)
    ]


class ChapterRewriteMatchService:
    """Read-only legacy paragraph matching for one confirmed new leaf chapter."""

    def __init__(
        self,
        context: WorkspaceContext,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)

    def generate(self, chapter_id: str) -> dict[str, Any]:
        self._require_rewrite_mode()
        HumanGateService(self.context).require_current_confirmation()
        blueprint, node = self._require_leaf(chapter_id)
        if str((blueprint.get("payload") or {}).get("planning_model") or "") != "rewrite_merge":
            raise ControlPlaneError(
                "REWRITE_OUTLINE_REGENERATE_REQUIRED",
                "旧版改写目录缺少目录融合决策，请重新生成目录。",
                status_code=409,
            )
        rewrite_mode = str(node.get("rewrite_mode") or "")
        if rewrite_mode not in {"copy", "light_edit", "restructure", "new_write"}:
            raise ControlPlaneError(
                "REWRITE_OUTLINE_REGENERATE_REQUIRED",
                "当前叶子章节缺少 rewrite_mode，请重新生成目录。",
                status_code=409,
            )
        chapter = ChapterWorkspaceService(self.context).get_chapter(chapter_id)
        legacy_artifact, legacy = self._require_current_legacy_index()
        requirements, scoring = project_chapter_semantic_requirements(
            self.context, chapter
        )
        writing_plan = compile_chapter_writing_plan(
            chapter,
            tender_requirements=requirements,
            scoring_requirements=scoring,
        )
        target = self._target(node, requirements, scoring, writing_plan)
        blocks = {
            str(item.get("block_id") or ""): item
            for item in legacy.get("blocks") or []
            if isinstance(item, dict)
        }
        selected = []
        for source in node.get("legacy_sources") or []:
            if not isinstance(source, dict):
                continue
            block = blocks.get(str(source.get("block_id") or ""))
            selected.append({
                **(block or {}),
                "section_id": str(source.get("section_id") or ""),
                "content_hash": str(source.get("content_hash") or ""),
                "semantic_score": 1.0,
            })
        self._validate_refs(selected, legacy)
        coverage = project_rewrite_coverage(
            writing_plan,
            rewrite_mode,
            selected,
            list(node.get("required_changes") or []),
        )
        matches = self._matches(selected, coverage, legacy)
        strategy = {
            "strategy": rewrite_mode,
            "reason": str(node.get("rewrite_reason") or ""),
            "required_changes": list(node.get("required_changes") or []),
        }
        result = {
            "schema_version": "v3.chapter-rewrite-match.v1",
            "chapter_id": str(chapter_id),
            "chapter_title": str(node.get("title") or chapter.get("title") or ""),
            "read_only": True,
            "target": {
                "title": target["title"],
                "purpose": target["purpose"],
                "writing_objectives": target["writing_objectives"],
                "requirements": requirements,
                "score_conditions": target["score_conditions"],
            },
            "writing_plan": writing_plan,
            "matches": matches,
            "coverage": coverage,
            "recommendation": strategy,
            "summary": self._summary(coverage, matches),
            "reranker": {"provider_id": "planning.rewrite_outline_merge"},
            "source": {
                "legacy_bid_id": str(legacy.get("legacy_bid_id") or ""),
                "legacy_index_revision": int(legacy.get("revision") or 0),
                "legacy_index_hash": str(legacy_artifact.get("artifact_hash") or ""),
                "file_hash": str(legacy.get("file_hash") or ""),
            },
        }
        stored = self.store.append_chapter_rewrite_match_revision(
            chapter_id=str(chapter_id),
            blueprint_revision=int(blueprint.get("revision") or 0),
            blueprint_hash=str(blueprint.get("artifact_hash") or ""),
            legacy_bid_id=str(legacy.get("legacy_bid_id") or ""),
            legacy_index_revision=int(legacy.get("revision") or 0),
            legacy_index_hash=str(legacy_artifact.get("artifact_hash") or ""),
            result=result,
            result_hash=canonical_hash(result),
        )
        return {
            **result,
            "match_revision": int(stored.get("match_revision") or 0),
            "result_hash": str(stored.get("result_hash") or ""),
            "created_at": str(stored.get("created_at") or ""),
        }
    def latest(self, chapter_id: str) -> dict[str, Any]:
        self._require_rewrite_mode()
        row = self.store.chapter_rewrite_match_revision(chapter_id)
        if not row:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_MATCH_NOT_FOUND",
                "当前章节尚未生成改写匹配。",
                status_code=404,
            )
        result = dict(row.get("result") or {})
        blueprint = self.store.v3_active_artifact("ChapterBlueprint") or {}
        legacy = self.store.v3_active_artifact("LegacyBidIndex") or {}
        if (
            int(row.get("blueprint_revision") or 0) != int(blueprint.get("revision") or 0)
            or str(row.get("blueprint_hash") or "") != str(blueprint.get("artifact_hash") or "")
            or int(row.get("legacy_index_revision") or 0) != int(legacy.get("revision") or 0)
            or str(row.get("legacy_index_hash") or "") != str(legacy.get("artifact_hash") or "")
        ):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_MATCH_STALE",
                "章节或旧投标书已变化，请重新生成改写匹配。",
                status_code=409,
            )
        return {
            **result,
            "match_revision": int(row.get("match_revision") or 0),
            "result_hash": str(row.get("result_hash") or ""),
            "created_at": str(row.get("created_at") or ""),
        }

    def _require_rewrite_mode(self) -> None:
        if self.store.workspace_profile().get("project_mode") != "bid_rewrite":
            raise ControlPlaneError(
                "REWRITE_MODE_REQUIRED",
                "仅标书改写工作空间提供章节改写逻辑。",
                status_code=409,
            )

    def _require_leaf(self, chapter_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        blueprint = self.store.v3_active_artifact("ChapterBlueprint")
        if not blueprint:
            raise ControlPlaneError(
                "CHAPTER_BLUEPRINT_REQUIRED", "目录尚未生成。", status_code=409
            )
        nodes = (blueprint.get("payload") or {}).get("nodes") or []
        node = next(
            (
                item
                for item in nodes
                if isinstance(item, dict)
                and str(item.get("chapter_id") or "") == str(chapter_id)
            ),
            None,
        )
        if node is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND", "章节不在当前目录中。", status_code=404
            )
        if any(
            isinstance(item, dict)
            and str(item.get("parent_chapter_id") or "") == str(chapter_id)
            for item in nodes
        ):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_MATCH_LEAF_REQUIRED",
                "父章节不生成改写匹配，请选择叶子章节。",
                status_code=409,
            )
        return blueprint, node

    def _require_current_legacy_index(self) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self.store.v3_active_artifact("LegacyBidSourceManifest")
        index = self.store.v3_active_artifact("LegacyBidIndex")
        if not manifest or not index:
            raise ControlPlaneError(
                "LEGACY_BID_INDEX_REQUIRED", "旧投标书索引尚未就绪。", status_code=409
            )
        payload = index.get("payload") or {}
        sources = (manifest.get("payload") or {}).get("sources") or []
        active = next(
            (item for item in sources if isinstance(item, dict) and item.get("active")),
            None,
        )
        if (
            not active
            or str(active.get("legacy_bid_id") or "")
            != str(payload.get("legacy_bid_id") or "")
            or int(payload.get("source_manifest_revision") or 0)
            != int(manifest.get("revision") or 0)
            or str(payload.get("source_manifest_artifact_hash") or "")
            != str(manifest.get("artifact_hash") or "")
        ):
            raise ControlPlaneError(
                "LEGACY_BID_INDEX_STALE",
                "旧投标书索引与当前来源不一致。",
                status_code=409,
            )
        return index, payload

    @staticmethod
    def _target(
        node: dict[str, Any],
        requirements: list[dict[str, Any]],
        scoring: list[dict[str, Any]],
        writing_plan: dict[str, Any],
    ) -> dict[str, Any]:
        conditions = [
            condition
            for point in scoring
            if isinstance(point, dict)
            for condition in (point.get("conditions") or point.get("score_conditions") or [])
            if isinstance(condition, dict)
        ]
        parts = [
            node.get("title"),
            node.get("purpose"),
            *(node.get("writing_objectives") or []),
            *(
                item.get("text") or item.get("normalized_requirement") or ""
                for item in requirements
                if isinstance(item, dict)
            ),
            *(
                item.get("normalized_condition") or item.get("text") or ""
                for item in conditions
            ),
            *(
                f"{item.get('heading') or ''} {item.get('must_answer') or ''}"
                for item in writing_plan.get("blocks") or []
                if isinstance(item, dict)
            ),
        ]
        return {
            "title": str(node.get("title") or ""),
            "purpose": str(node.get("purpose") or ""),
            "writing_objectives": list(node.get("writing_objectives") or []),
            "score_conditions": conditions,
            "query": "\n".join(str(item) for item in parts if str(item or "").strip()),
        }

    @staticmethod
    def _validate_refs(ranked: list[dict[str, Any]], legacy: dict[str, Any]) -> None:
        block_ids = {
            str(item.get("block_id") or "")
            for item in legacy.get("blocks") or []
            if isinstance(item, dict)
        }
        section_ids = {
            str(item.get("section_id") or "")
            for item in legacy.get("sections") or []
            if isinstance(item, dict)
        }
        content_hashes = {
            str(item.get("block_id") or ""): str(item.get("content_hash") or "")
            for item in legacy.get("blocks") or []
            if isinstance(item, dict)
        }
        contents = {
            str(item.get("block_id") or ""): str(item.get("content") or "")
            for item in legacy.get("blocks") or []
            if isinstance(item, dict)
        }
        for item in ranked:
            block_id = str(item.get("block_id") or "")
            section_id = str(item.get("section_id") or "")
            if (
                block_id not in block_ids
                or section_id not in section_ids
                or str(item.get("content_hash") or "") != content_hashes.get(block_id)
                or str(item.get("content") or "") != contents.get(block_id)
            ):
                raise ControlPlaneError(
                    "LEGACY_BID_RERANK_REFERENCE_INVALID",
                    "匹配结果引用了未知旧章节、段落或内容哈希。",
                    status_code=409,
                )

    @staticmethod
    def _matches(
        ranked: list[dict[str, Any]],
        coverage: list[dict[str, Any]],
        legacy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        coverage_by_block: dict[str, list[str]] = defaultdict(list)
        for item in coverage:
            for block_id in item.get("matched_block_ids") or []:
                coverage_by_block[str(block_id)].append(
                    str(item.get("writing_block_id") or "")
                )
        sections = {
            str(item.get("section_id") or ""): item
            for item in legacy.get("sections") or []
            if isinstance(item, dict)
        }
        return [
            {
                "section_id": str(item.get("section_id") or ""),
                "section_title": str(
                    (sections.get(str(item.get("section_id") or "")) or {}).get("title")
                    or item.get("section_title")
                    or ""
                ),
                "block_id": str(item.get("block_id") or ""),
                "content_hash": str(item.get("content_hash") or ""),
                "content": str(item.get("content") or ""),
                "heading_path": list(item.get("heading_path") or []),
                "block_kind": str(item.get("block_kind") or ""),
                "match_score": float(item.get("semantic_score") or 0),
                "reason": "标题/层级召回后，原始段落与当前章目标语义重合。",
                "covered_writing_block_ids": coverage_by_block.get(
                    str(item.get("block_id") or ""), []
                ),
                "risk": (
                    "仅作旧文参考，必须按新招标要求改写。"
                    if item.get("block_kind") != "heading"
                    else "标题仅用于定位，不等于内容已覆盖。"
                ),
            }
            for item in ranked
        ]

    @staticmethod
    def _summary(
        coverage: list[dict[str, Any]], matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        counts = {status: 0 for status in sorted(_COVERAGE_STATES)}
        for item in coverage:
            counts[str(item.get("status") or "not_covered")] += 1
        return {
            "match_count": len(matches),
            "writing_block_count": len(coverage),
            **counts,
            "covered_count": counts["fully_covered"] + counts["partially_covered"],
            "missing_count": counts["not_covered"],
        }
