from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .canonicalization import canonical_hash
from .chapter_rewrite_match import (
    ChapterRewriteMatchService,
    project_rewrite_coverage,
)
from .chapter_workspace import ChapterWorkspaceService
from .global_project_context import GlobalProjectContextService
from .input_manifest import V3_ROOT
from .research_tool import V3ResearchTool


_STRATEGIES = {"copy", "light_edit", "restructure", "new_write"}
_EDIT_OPS = {
    "select_legacy_block",
    "unselect_legacy_block",
    "change_block_usage",
    "update_instruction",
    "set_strategy",
    "add_new_content_item",
    "remove_new_content_item",
    "bind_evidence",
    "unbind_evidence",
    "resolve_pollution",
}
_FORBIDDEN_SEARCH = re.compile(
    r"企业|公司|业绩|案例|人员|项目经理|团队|证书|资质|承诺|保证|报价|投标人能力"
)
_POLLUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("旧项目名称", re.compile(r"(?:项目名称|项目名)\s*[:：]\s*([^，。；\n]{2,60})")),
    ("旧采购人", re.compile(r"(?:采购人|招标人|采购单位)\s*[:：]\s*([^，。；\n]{2,60})")),
    ("旧地区", re.compile(r"[\u4e00-\u9fff]{2,12}(?:省|市|区|县)")),
    ("旧年份/日期", re.compile(r"(?:19|20)\d{2}(?:年(?:\d{1,2}月(?:\d{1,2}日)?)?)?")),
    ("旧工期", re.compile(r"(?:工期|周期|期限)[^，。；\n]{0,8}\d+\s*(?:日|天|个月|月|年)")),
    ("旧人员", re.compile(r"(?:项目经理|负责人|工程师|联系人)\s*[:：]?\s*[\u4e00-\u9fff]{2,4}")),
    ("旧数量", re.compile(r"\d+(?:\.\d+)?\s*(?:套|台|人|项|个|处|公里|万元|万)")),
    ("旧系统名称", re.compile(r"[\u4e00-\u9fffA-Za-z0-9_-]{2,30}(?:系统|平台)")),
    ("旧产品/标准版本", re.compile(r"(?:GB/?T?|ISO|IEC|CJ|DB)\s*[A-Za-z0-9./_-]+|[vV]\d+(?:\.\d+)+")),
    ("旧合同承诺", re.compile(r"[^，。；\n]{0,24}(?:承诺|保证|确保)[^，。；\n]{0,36}")),
)


class ChapterRewritePlanService:
    """Editable, append-only rewrite plan; never writes chapter body content."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        research_tool: Any | None = None,
        global_context_override: dict[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.research_tool = research_tool
        self.global_context_override = deepcopy(global_context_override)

    def generate(
        self, chapter_id: str, *, actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._require_mode()
        if self.store.chapter_rewrite_plan_revision(chapter_id):
            return self.get(chapter_id)
        match_service = ChapterRewriteMatchService(self.context)
        try:
            match = match_service.latest(chapter_id)
        except ControlPlaneError as exc:
            if exc.code not in {
                "CHAPTER_REWRITE_MATCH_NOT_FOUND",
                "CHAPTER_REWRITE_MATCH_STALE",
            }:
                raise
            match = match_service.generate(chapter_id)
        dependencies = self._dependencies(chapter_id)
        selected = [
            {
                "section_id": str(item.get("section_id") or ""),
                "block_id": str(item.get("block_id") or ""),
                "content_hash": str(item.get("content_hash") or ""),
                "usage": self._default_usage(match),
                "instruction": "",
            }
            for item in match.get("matches") or []
            if isinstance(item, dict) and item.get("block_kind") != "heading"
        ]
        plan = {
            "schema_version": "v3.chapter-rewrite-plan.v1",
            "chapter_id": str(chapter_id),
            "match_revision": int(match.get("match_revision") or 0),
            "match_hash": str(match.get("result_hash") or ""),
            "strategy": str((match.get("recommendation") or {}).get("strategy") or "new_write"),
            "instruction": "；".join(filter(None, [
                str((match.get("recommendation") or {}).get("reason") or ""),
                *(str(item) for item in (match.get("recommendation") or {}).get("required_changes") or []),
            ])),
            "required_changes": list(
                (match.get("recommendation") or {}).get("required_changes")
                or []
            ),
            "selected_legacy_blocks": selected,
            "new_content_items": [
                {
                    "item_id": f"new:{item.get('writing_block_id')}",
                    "writing_block_id": str(item.get("writing_block_id") or ""),
                    "instruction": str(item.get("must_answer") or item.get("heading") or ""),
                    "evidence_ids": [],
                }
                for item in match.get("coverage") or []
                if isinstance(item, dict) and item.get("status") == "not_covered"
            ],
            "writing_plan": deepcopy(match.get("writing_plan") or {}),
            "target": deepcopy(match.get("target") or {}),
            "coverage": [],
            "pollution_findings": [],
            "dependencies": dependencies,
        }
        self._recompute(plan)
        row = self.store.append_chapter_rewrite_plan_revision(
            chapter_id=chapter_id,
            expected_plan_revision=0,
            plan=plan,
            plan_hash=canonical_hash(plan),
            actor=actor,
            event_type="plan.generated",
            event_payload={"match_revision": plan["match_revision"]},
        )
        return self._project(row)

    def get(self, chapter_id: str, revision: int | None = None) -> dict[str, Any]:
        self._require_mode()
        row = self.store.chapter_rewrite_plan_revision(chapter_id, revision)
        if not row:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_PLAN_NOT_FOUND",
                "当前章节尚未生成改写方案。",
                status_code=404,
            )
        return self._project(row)

    def history(self, chapter_id: str) -> list[dict[str, Any]]:
        self._require_mode()
        return [self._project(row) for row in self.store.chapter_rewrite_plan_revisions(chapter_id)]

    def update(
        self,
        chapter_id: str,
        *,
        expected_plan_revision: int,
        expected_plan_hash: str,
        operations: list[dict[str, Any]],
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_mode()
        if not operations or any(not isinstance(item, dict) for item in operations):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_EDIT_INVALID",
                "operations 必须包含至少一个结构化编辑操作。",
                status_code=400,
            )
        row = self._require_head(chapter_id, expected_plan_revision, expected_plan_hash)
        plan = deepcopy(row["plan"])
        before_covered = self._covered_count(plan)
        before_coverage_score = self._coverage_score(plan)
        for operation in operations:
            self._apply_operation(plan, operation)
        self._recompute(plan)
        after_covered = self._covered_count(plan)
        after_coverage_score = self._coverage_score(plan)
        warnings = []
        if after_covered < before_covered or after_coverage_score < before_coverage_score:
            warnings.append(
                {
                    "code": "CHAPTER_REWRITE_COVERAGE_DECREASED",
                    "message": "本次编辑降低了写作块覆盖度，请检查缺失项。",
                }
            )
        saved = self.store.append_chapter_rewrite_plan_revision(
            chapter_id=chapter_id,
            expected_plan_revision=expected_plan_revision,
            plan=plan,
            plan_hash=canonical_hash(plan),
            actor=actor,
            event_type="plan.updated",
            event_payload={
                "operations": [str(item.get("op") or "") for item in operations],
                "warnings": warnings,
            },
        )
        return {**self._project(saved), "warnings": warnings}

    def search(
        self,
        chapter_id: str,
        *,
        expected_plan_revision: int,
        expected_plan_hash: str,
        item_id: str,
        query: str,
        actor: dict[str, Any] | None = None,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_mode()
        text = str(query or "").strip()
        if not text:
            raise ControlPlaneError("CHAPTER_REWRITE_SEARCH_QUERY_REQUIRED", "查询目标不能为空。", status_code=400)
        if _FORBIDDEN_SEARCH.search(text):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_SEARCH_FORBIDDEN",
                "公开搜索不能用于证明企业事实、业绩、人员、资质或承诺。",
                status_code=409,
            )
        row = self._require_head(chapter_id, expected_plan_revision, expected_plan_hash)
        plan = deepcopy(row["plan"])
        item = self._new_item(plan, item_id)
        need_id = "rewrite-search:" + hashlib.sha256(
            f"{chapter_id}:{item_id}:{expected_plan_revision}:{text}".encode("utf-8")
        ).hexdigest()[:20]
        self.store.upsert_evidence_need(
            {
                "need_id": need_id,
                "question": text,
                "topic_id": f"rewrite:{chapter_id}:{item_id}",
                "priority": "normal",
                "blocking_scope": "none",
                "deadline_stage": "chapter_writing",
                "query_budget": 5,
                "status": "pending",
            }
        )
        tool = self.research_tool or V3ResearchTool(self.context)
        try:
            result = tool.invoke(need_id, provider_id=provider_id)
        except Exception as exc:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_SEARCH_FAILED",
                f"补充查询失败，原方案未变化：{exc}",
                status_code=502,
            ) from exc
        batch = result.get("batch") if isinstance(result, dict) else {}
        batch = batch if isinstance(batch, dict) else {}
        if batch.get("status") != "published" or not batch.get("items"):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_SEARCH_FAILED",
                "补充查询没有发布可用证据，原方案未变化。",
                status_code=409,
            )
        evidence_ids = [
            str(value.get("evidence_id") or "")
            for value in batch.get("items") or []
            if isinstance(value, dict) and value.get("evidence_id")
        ]
        item["evidence_ids"] = list(dict.fromkeys([*(item.get("evidence_ids") or []), *evidence_ids]))
        self._recompute(plan)
        saved = self.store.append_chapter_rewrite_plan_revision(
            chapter_id=chapter_id,
            expected_plan_revision=expected_plan_revision,
            plan=plan,
            plan_hash=canonical_hash(plan),
            actor=actor,
            event_type="plan.searched",
            event_payload={"item_id": item_id, "need_id": need_id, "evidence_ids": evidence_ids},
        )
        return {**self._project(saved), "research": result}

    def confirm(
        self,
        chapter_id: str,
        *,
        expected_chapter_revision: int,
        plan_revision: int,
        plan_hash: str,
        principal_id: str,
    ) -> dict[str, Any]:
        self._require_mode()
        if not principal_id:
            raise ControlPlaneError("AUTH_REQUIRED", "确认改写方案需要认证用户。", status_code=401)
        row = self._require_head(chapter_id, plan_revision, plan_hash)
        projected = self._project(row)
        if projected["stale"]:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_PLAN_STALE",
                "改写方案依赖已变化，请重新生成或保存后确认。",
                status_code=409,
                details={"stale_reasons": projected["stale_reasons"]},
            )
        chapter = ChapterWorkspaceService(self.context).get_chapter(chapter_id)
        if int(chapter.get("chapter_revision") or 0) != int(expected_chapter_revision):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_CHAPTER_CONFLICT",
                "章节版本已变化，请刷新后确认。",
                status_code=409,
            )
        existing_confirmation = self.store.chapter_rewrite_confirmation(chapter_id)
        if (
            existing_confirmation
            and int(existing_confirmation.get("plan_revision") or 0) == int(plan_revision)
            and str(existing_confirmation.get("plan_hash") or "") == str(plan_hash)
            and str(existing_confirmation.get("principal_id") or "") == principal_id
        ):
            return existing_confirmation
        unresolved = [
            item
            for item in row["plan"].get("pollution_findings") or []
            if isinstance(item, dict) and item.get("status") != "resolved"
        ]
        if unresolved:
            raise ControlPlaneError(
                "CHAPTER_REWRITE_POLLUTION_UNRESOLVED",
                "仍有旧项目污染风险未解决，不能确认改写方案。",
                status_code=409,
                details={"finding_ids": [item.get("finding_id") for item in unresolved]},
            )
        deps = row["plan"]["dependencies"]
        confirmation = {
            "confirmation_id": f"rewrite-confirmation:{uuid.uuid4()}",
            "chapter_id": chapter_id,
            "expected_chapter_revision": int(expected_chapter_revision),
            "plan_revision": int(plan_revision),
            "plan_hash": str(plan_hash),
            "blueprint_hash": str(deps.get("blueprint_hash") or ""),
            "legacy_bid_index_hash": str(deps.get("legacy_bid_index_hash") or ""),
            "global_context_hash": str(deps.get("global_context_hash") or ""),
            "chapter_context_hash": str(deps.get("chapter_context_hash") or ""),
            "principal_id": principal_id,
        }
        receipt_hash = canonical_hash(confirmation)
        return self.store.confirm_chapter_rewrite_plan(
            confirmation=confirmation,
            receipt_hash=receipt_hash,
        )

    def reopen(
        self,
        chapter_id: str,
        *,
        expected_plan_revision: int,
        expected_plan_hash: str,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self._require_head(chapter_id, expected_plan_revision, expected_plan_hash)
        saved = self.store.append_chapter_rewrite_plan_revision(
            chapter_id=chapter_id,
            expected_plan_revision=expected_plan_revision,
            plan=deepcopy(row["plan"]),
            plan_hash=canonical_hash(row["plan"]),
            actor=actor,
            event_type="plan.reopened",
        )
        return self._project(saved)

    def _apply_operation(self, plan: dict[str, Any], operation: dict[str, Any]) -> None:
        op = str(operation.get("op") or "")
        if op not in _EDIT_OPS:
            raise ControlPlaneError("CHAPTER_REWRITE_EDIT_INVALID", f"不支持的编辑操作：{op}", status_code=400)
        selected = plan.setdefault("selected_legacy_blocks", [])
        if op == "select_legacy_block":
            source = self._legacy_ref(operation)
            if any(item.get("block_id") == source["block_id"] for item in selected):
                return
            selected.append({**source, "usage": str(operation.get("usage") or "light_edit"), "instruction": ""})
        elif op == "unselect_legacy_block":
            block_id = self._required(operation, "block_id")
            plan["selected_legacy_blocks"] = [item for item in selected if item.get("block_id") != block_id]
        elif op == "change_block_usage":
            item = self._selected(plan, self._required(operation, "block_id"))
            usage = self._strategy(operation.get("usage"))
            item["usage"] = usage
        elif op == "update_instruction":
            instruction = str(operation.get("instruction") or "").strip()
            block_id = str(operation.get("block_id") or "").strip()
            if block_id:
                self._selected(plan, block_id)["instruction"] = instruction
            else:
                plan["instruction"] = instruction
        elif op == "set_strategy":
            plan["strategy"] = self._strategy(operation.get("strategy"))
        elif op == "add_new_content_item":
            instruction = self._required(operation, "instruction")
            item_id = str(operation.get("item_id") or f"new:{uuid.uuid4()}")
            if any(item.get("item_id") == item_id for item in plan.get("new_content_items") or []):
                raise ControlPlaneError("CHAPTER_REWRITE_ITEM_EXISTS", "补写项 ID 已存在。", status_code=409)
            plan.setdefault("new_content_items", []).append(
                {"item_id": item_id, "writing_block_id": str(operation.get("writing_block_id") or ""), "instruction": instruction, "evidence_ids": []}
            )
        elif op == "remove_new_content_item":
            item_id = self._required(operation, "item_id")
            before = len(plan.get("new_content_items") or [])
            plan["new_content_items"] = [item for item in plan.get("new_content_items") or [] if item.get("item_id") != item_id]
            if len(plan["new_content_items"]) == before:
                raise ControlPlaneError("CHAPTER_REWRITE_ITEM_NOT_FOUND", "补写项不存在。", status_code=404)
        elif op == "bind_evidence":
            item = self._new_item(plan, self._required(operation, "item_id"))
            evidence_id = self._required(operation, "evidence_id")
            self._require_evidence(evidence_id)
            item["evidence_ids"] = list(dict.fromkeys([*(item.get("evidence_ids") or []), evidence_id]))
        elif op == "unbind_evidence":
            item = self._new_item(plan, self._required(operation, "item_id"))
            evidence_id = self._required(operation, "evidence_id")
            item["evidence_ids"] = [value for value in item.get("evidence_ids") or [] if value != evidence_id]
        elif op == "resolve_pollution":
            finding_id = self._required(operation, "finding_id")
            finding = next((item for item in plan.get("pollution_findings") or [] if item.get("finding_id") == finding_id), None)
            if finding is None:
                raise ControlPlaneError("CHAPTER_REWRITE_FINDING_NOT_FOUND", "污染项不存在。", status_code=404)
            replacement = self._replacement(operation, plan)
            finding.update({"status": "resolved", **replacement})

    def _recompute(self, plan: dict[str, Any]) -> None:
        legacy = self._legacy_payload()
        blocks = {str(item.get("block_id") or ""): item for item in legacy.get("blocks") or [] if isinstance(item, dict)}
        sections_by_block: dict[str, str] = {}
        for section in legacy.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for block_id in [section.get("heading_block_id"), *(section.get("content_block_ids") or [])]:
                if block_id:
                    sections_by_block[str(block_id)] = str(section.get("section_id") or "")
        ranked = []
        for selected in plan.get("selected_legacy_blocks") or []:
            block_id = str(selected.get("block_id") or "")
            source = blocks.get(block_id)
            if (
                not source
                or str(selected.get("content_hash") or "") != str(source.get("content_hash") or "")
                or str(selected.get("section_id") or "") != sections_by_block.get(block_id)
            ):
                raise ControlPlaneError(
                    "CHAPTER_REWRITE_LEGACY_REFERENCE_INVALID",
                    "改写方案包含悬空或过期的旧文引用。",
                    status_code=409,
                    details={"block_id": block_id},
                )
            ranked.append({**source, "section_id": sections_by_block[block_id]})
        strategy = self._strategy(plan.get("strategy"))
        if strategy == "new_write":
            plan["selected_legacy_blocks"] = []
            ranked = []
        plan["coverage"] = project_rewrite_coverage(
            plan.get("writing_plan") or {},
            strategy,
            ranked,
            list(plan.get("required_changes") or []),
        )
        if strategy == "new_write":
            existing = {
                str(item.get("writing_block_id") or ""): item
                for item in plan.get("new_content_items") or []
                if isinstance(item, dict)
            }
            plan["new_content_items"] = [
                {
                    "item_id": str(
                        (existing.get(str(block.get("block_id") or "")) or {}).get(
                            "item_id"
                        )
                        or f"new:{block.get('block_id')}"
                    ),
                    "writing_block_id": str(block.get("block_id") or ""),
                    "instruction": str(
                        (existing.get(str(block.get("block_id") or "")) or {}).get(
                            "instruction"
                        )
                        or block.get("must_answer")
                        or block.get("heading")
                        or ""
                    ),
                    "evidence_ids": list(
                        (existing.get(str(block.get("block_id") or "")) or {}).get(
                            "evidence_ids"
                        )
                        or []
                    ),
                }
                for block in (plan.get("writing_plan") or {}).get("blocks") or []
                if isinstance(block, dict)
            ]
        else:
            plan["new_content_items"] = []
        plan["pollution_findings"] = self._scan_pollution(plan, blocks)

    def _scan_pollution(self, plan: dict[str, Any], blocks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        existing = {
            (str(item.get("type") or ""), str(item.get("block_id") or ""), str(item.get("source_text") or "")): item
            for item in plan.get("pollution_findings") or []
            if isinstance(item, dict)
        }
        allowed = self._allowed_corpus(plan)
        findings: list[dict[str, Any]] = []
        for selected in plan.get("selected_legacy_blocks") or []:
            block_id = str(selected.get("block_id") or "")
            content = str((blocks.get(block_id) or {}).get("content") or "")
            for finding_type, pattern in _POLLUTION_PATTERNS:
                for match in pattern.finditer(content):
                    source_text = str(match.group(0) or "").strip()
                    if not source_text or source_text in allowed:
                        continue
                    key = (finding_type, block_id, source_text)
                    prior = existing.get(key) or {}
                    finding_id = "pollution:" + hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:20]
                    findings.append(
                        {
                            "finding_id": finding_id,
                            "type": finding_type,
                            "block_id": block_id,
                            "source_text": source_text,
                            "suggested_action": "替换为已确认的新项目事实或新招标原文。",
                            "replacement_fact_id": str(prior.get("replacement_fact_id") or ""),
                            "replacement_requirement_id": str(prior.get("replacement_requirement_id") or ""),
                            "replacement_text": str(prior.get("replacement_text") or ""),
                            "status": "resolved" if prior.get("status") == "resolved" else "unresolved",
                        }
                    )
        return findings

    def _replacement(self, operation: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
        fact_id = str(operation.get("replacement_fact_id") or "").strip()
        requirement_id = str(operation.get("replacement_requirement_id") or "").strip()
        global_context = self._global_context()
        if fact_id:
            fact = next((item for item in global_context.get("confirmed_facts") or [] if isinstance(item, dict) and str(item.get("fact_id") or "") == fact_id), None)
            if not fact:
                raise ControlPlaneError("CHAPTER_REWRITE_REPLACEMENT_INVALID", "替换值只能引用已确认的新项目事实。", status_code=409)
            return {"replacement_fact_id": fact_id, "replacement_requirement_id": "", "replacement_text": str(fact.get("statement") or "")}
        if requirement_id:
            requirement = next((item for item in (plan.get("target") or {}).get("requirements") or [] if isinstance(item, dict) and str(item.get("requirement_id") or "") == requirement_id), None)
            if not requirement:
                raise ControlPlaneError("CHAPTER_REWRITE_REPLACEMENT_INVALID", "替换值只能引用当前新招标原文。", status_code=409)
            return {"replacement_fact_id": "", "replacement_requirement_id": requirement_id, "replacement_text": str(requirement.get("text") or requirement.get("normalized_requirement") or "")}
        raise ControlPlaneError("CHAPTER_REWRITE_REPLACEMENT_INVALID", "必须选择已确认事实或新招标原文作为替换值。", status_code=400)

    def _legacy_ref(self, operation: dict[str, Any]) -> dict[str, str]:
        block_id = self._required(operation, "block_id")
        section_id = self._required(operation, "section_id")
        content_hash = self._required(operation, "content_hash")
        legacy = self._legacy_payload()
        block = next((item for item in legacy.get("blocks") or [] if isinstance(item, dict) and str(item.get("block_id") or "") == block_id), None)
        section = next((item for item in legacy.get("sections") or [] if isinstance(item, dict) and str(item.get("section_id") or "") == section_id), None)
        section_blocks = [section.get("heading_block_id"), *(section.get("content_block_ids") or [])] if section else []
        if not block or not section or block_id not in section_blocks or str(block.get("content_hash") or "") != content_hash:
            raise ControlPlaneError("CHAPTER_REWRITE_LEGACY_REFERENCE_INVALID", "旧文引用不存在或内容哈希已变化。", status_code=409)
        return {"section_id": section_id, "block_id": block_id, "content_hash": content_hash}

    def _require_evidence(self, evidence_id: str) -> dict[str, Any]:
        directory = self.context.root / V3_ROOT / "evidence" / "batches"
        for path in directory.glob("*.json") if directory.is_dir() else []:
            try:
                batch = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if batch.get("status") != "published":
                continue
            for item in batch.get("items") or []:
                if isinstance(item, dict) and str(item.get("evidence_id") or "") == evidence_id:
                    return item
        raise ControlPlaneError("CHAPTER_REWRITE_EVIDENCE_INVALID", "Evidence ID 不存在或尚未发布。", status_code=409)

    def _dependencies(self, chapter_id: str) -> dict[str, Any]:
        chapter = ChapterWorkspaceService(self.context).get_chapter(chapter_id)
        blueprint = self.store.v3_active_artifact("ChapterBlueprint") or {}
        legacy = self.store.v3_active_artifact("LegacyBidIndex") or {}
        global_context = self._global_context()
        context = chapter.get("context") or {}
        return {
            "chapter_revision": int(chapter.get("chapter_revision") or 0),
            "blueprint_hash": str(blueprint.get("artifact_hash") or ""),
            "legacy_bid_index_hash": str(legacy.get("artifact_hash") or ""),
            "global_context_hash": str(global_context.get("global_context_hash") or ""),
            "chapter_context_hash": str(context.get("content_hash") or canonical_hash([])),
        }

    def _project(self, row: dict[str, Any]) -> dict[str, Any]:
        plan = deepcopy(row.get("plan") or {})
        current = self._dependencies(str(row.get("chapter_id") or plan.get("chapter_id") or ""))
        bound = plan.get("dependencies") or {}
        stale_reasons = [key for key in ("chapter_revision", "blueprint_hash", "legacy_bid_index_hash", "global_context_hash", "chapter_context_hash") if current.get(key) != bound.get(key)]
        state = self.store.chapter_rewrite_state(str(row.get("chapter_id") or "")) or {}
        confirmation = self.store.chapter_rewrite_confirmation(str(row.get("chapter_id") or ""))
        return {
            **plan,
            "plan_revision": int(row.get("plan_revision") or 0),
            "parent_plan_revision": row.get("parent_plan_revision"),
            "plan_hash": str(row.get("plan_hash") or ""),
            "created_at": str(row.get("created_at") or ""),
            "status": "stale" if stale_reasons else str(state.get("status") or "draft"),
            "stale": bool(stale_reasons),
            "stale_reasons": stale_reasons,
            "confirmation": confirmation,
        }

    def _require_head(self, chapter_id: str, revision: int, plan_hash: str) -> dict[str, Any]:
        row = self.store.chapter_rewrite_plan_revision(chapter_id)
        if not row:
            raise ControlPlaneError("CHAPTER_REWRITE_PLAN_NOT_FOUND", "改写方案不存在。", status_code=404)
        if int(row.get("plan_revision") or 0) != int(revision) or str(row.get("plan_hash") or "") != str(plan_hash or ""):
            raise ControlPlaneError(
                "CHAPTER_REWRITE_PLAN_CONFLICT",
                "改写方案已被其他操作更新，请刷新后重试。",
                status_code=409,
                details={"actual_plan_revision": int(row.get("plan_revision") or 0), "actual_plan_hash": str(row.get("plan_hash") or "")},
            )
        return row

    def _legacy_payload(self) -> dict[str, Any]:
        artifact = self.store.v3_active_artifact("LegacyBidIndex") or {}
        payload = artifact.get("payload") or {}
        if not payload:
            raise ControlPlaneError("LEGACY_BID_INDEX_REQUIRED", "旧投标书索引尚未就绪。", status_code=409)
        return payload

    def _allowed_corpus(self, plan: dict[str, Any]) -> str:
        return json.dumps(
            {
                "global": self._global_context(),
                "target": plan.get("target") or {},
            },
            ensure_ascii=False,
        )

    def _require_mode(self) -> None:
        if self.store.workspace_profile().get("project_mode") != "bid_rewrite":
            raise ControlPlaneError("REWRITE_MODE_REQUIRED", "当前工作空间不支持改写方案命令。", status_code=409)

    def _global_context(self) -> dict[str, Any]:
        if self.global_context_override is not None:
            return deepcopy(self.global_context_override)
        return GlobalProjectContextService(self.context).load()

    @staticmethod
    def _default_usage(match: dict[str, Any]) -> str:
        value = str((match.get("recommendation") or {}).get("strategy") or "light_edit")
        return value if value in _STRATEGIES else "light_edit"

    @staticmethod
    def _strategy(value: Any) -> str:
        normalized = str(value or "")
        if normalized not in _STRATEGIES:
            raise ControlPlaneError("CHAPTER_REWRITE_STRATEGY_INVALID", "改写策略无效。", status_code=400)
        return normalized

    @staticmethod
    def _required(value: dict[str, Any], key: str) -> str:
        normalized = str(value.get(key) or "").strip()
        if not normalized:
            raise ControlPlaneError("CHAPTER_REWRITE_EDIT_INVALID", f"编辑操作缺少 {key}。", status_code=400)
        return normalized

    @staticmethod
    def _selected(plan: dict[str, Any], block_id: str) -> dict[str, Any]:
        item = next((value for value in plan.get("selected_legacy_blocks") or [] if value.get("block_id") == block_id), None)
        if item is None:
            raise ControlPlaneError("CHAPTER_REWRITE_LEGACY_REFERENCE_INVALID", "旧文块未被选中。", status_code=404)
        return item

    @staticmethod
    def _new_item(plan: dict[str, Any], item_id: str) -> dict[str, Any]:
        item = next((value for value in plan.get("new_content_items") or [] if value.get("item_id") == item_id), None)
        if item is None:
            raise ControlPlaneError("CHAPTER_REWRITE_ITEM_NOT_FOUND", "补写项不存在。", status_code=404)
        return item

    @staticmethod
    def _covered_count(plan: dict[str, Any]) -> int:
        return sum(1 for item in plan.get("coverage") or [] if item.get("status") in {"fully_covered", "partially_covered"})

    @staticmethod
    def _coverage_score(plan: dict[str, Any]) -> float:
        return sum(
            float(item.get("best_score") or 0)
            for item in plan.get("coverage") or []
            if isinstance(item, dict)
        )


__all__ = ["ChapterRewritePlanService"]
