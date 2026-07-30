"""Requirement Agent for V3 trusted extraction, amendment reconciliation and proposal generation.

PR-17.1: batch extraction, stable clause IDs, scoped amendment override, abstain/needs_human,
and exact SourceIndex/prompt/model fingerprints on proposals. Deterministic rules remain the
default controlled inference provider; optional LLM providers may only emit candidates.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from control_plane import ControlStore, WorkspaceContext

from .artifact_promotion import build_declared_dependency_fingerprint
from .contracts import (
    InputManifest,
    InputRole,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    SourceAnchor,
    SourceBlock,
)
from .proposals import DependencyRef, ProposalEnvelope
from .scoring_sources import is_scoring_source_block, scoring_table_data_block_ids

PROMPT_EXTRACT_VERSION = "v3_requirement_agent_extract_v1.2"
PROMPT_RECONCILE_VERSION = "v3_requirement_agent_reconcile_v1.0"
REQUIREMENT_SCHEMA_VERSION = "v3"
REQUIREMENT_POLICY_VERSION = "v3-requirement-policy-3"
DEFAULT_BATCH_SIZE = 32

_OBLIGATION_MARKERS = (
    "应当", "必须", "须", "应", "不得", "禁止", "需要", "要求", "提供", "具备", "保证", "确保", "提交",
)
_DECLARED_REQUIREMENT_MARKERS = (
    "项目目标",
    "服务目标",
    "工作目标",
    "服务范围",
    "工作范围",
    "项目范围",
    "交付成果",
    "交付物",
    "验收条件",
    "验收标准",
    "服务期限",
    "项目工期",
    "工期",
)
_NEGATION_MARKERS = ("不得", "禁止", "不可", "不能", "严禁")
_EXCEPTION_MARKERS = ("除外", "除", "以外", "不适用于")
_SCORING_TABLE_HEADER = re.compile(
    r"^(?:序号|评分因素|评标因素|评审因素|评分项目|评分项|评分标准|评审标准|评分细则|分值)$"
)
_STANDALONE_DATE = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?$")
_TOC_PAGE_LINE = re.compile(r"^.+\t+\s*\d+\s*$")
_QUANTIFIED_OBLIGATION = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个?工作日|天|日|个?月|%|分(?!钟)|人(?:次|员)?)"
)


@dataclass
class ExtractionBatchResult:
    batch_id: str
    block_ids: list[str]
    items: list[RequirementItem] = field(default_factory=list)
    abstained: list[dict[str, Any]] = field(default_factory=list)
    needs_human: list[dict[str, Any]] = field(default_factory=list)


class RequirementInferenceProvider(Protocol):
    """Controlled inference surface. Implementations only emit candidates, never facts."""

    prompt_version: str
    model_fingerprint: str

    def extract_batch(
        self,
        *,
        batch_id: str,
        blocks: list[SourceBlock],
        clause_context: list[dict[str, str]],
        scoring_block_ids: set[str] | None = None,
    ) -> ExtractionBatchResult: ...


class DeterministicRequirementExtractor:
    """Rule-based controlled extractor; versioned as a model fingerprint for audit."""

    prompt_version = PROMPT_EXTRACT_VERSION
    model_fingerprint = "deterministic_requirement_extractor_v4"

    def extract_batch(
        self,
        *,
        batch_id: str,
        blocks: list[SourceBlock],
        clause_context: list[dict[str, str]],
        scoring_block_ids: set[str] | None = None,
    ) -> ExtractionBatchResult:
        items: list[RequirementItem] = []
        abstained: list[dict[str, Any]] = []
        needs_human: list[dict[str, Any]] = []
        # Local stack starts from frozen upstream clause context.
        clause_stack: list[tuple[int, str, str]] = [
            (int(item["depth"]), item["clause_id"], item["title"]) for item in clause_context
        ]

        for block in blocks:
            if block.input_role not in (InputRole.TENDER, InputRole.AMENDMENT, InputRole.SCORE):
                continue
            content = block.content.strip()
            if not content:
                continue
            scoring_source = block.block_id in (scoring_block_ids or set()) or is_scoring_source_block(block)

            if block.block_kind == "heading":
                depth = len(block.heading_path) if block.heading_path else 1
                while clause_stack and clause_stack[-1][0] >= depth:
                    clause_stack.pop()
                clause_id = _stable_clause_id(block.input_id, block.block_id, content, depth)
                clause_stack.append((depth, clause_id, content))
                continue
            if _SCORING_TABLE_HEADER.fullmatch(content):
                abstained.append(
                    {
                        "block_id": block.block_id,
                        "reason": "scoring_table_header",
                        "content_preview": content[:80],
                    }
                )
                continue

            parent_clause_id = clause_stack[-1][1] if clause_stack else None
            statements = self._atomic_statements(content)
            if not statements:
                abstained.append(
                    {
                        "block_id": block.block_id,
                        "reason": "no_atomic_statement",
                        "content_preview": content[:80],
                    }
                )
                continue

            for ordinal, stmt in enumerate(statements):
                if not scoring_source and self._is_non_obligation_prose(stmt):
                    abstained.append(
                        {
                            "block_id": block.block_id,
                            "ordinal": ordinal,
                            "reason": "non_obligation_prose",
                            "content_preview": stmt[:80],
                        }
                    )
                    continue

                kind = RequirementKind.SCORE if scoring_source else self._classify(stmt)
                confidence = self._confidence(stmt, kind)
                review_status = "confirmed"
                if confidence < 0.45:
                    review_status = "needs_human"
                    needs_human.append(
                        {
                            "block_id": block.block_id,
                            "ordinal": ordinal,
                            "reason": "low_confidence",
                            "confidence": confidence,
                            "content_preview": stmt[:80],
                        }
                    )

                clause_id = self._extract_clause_id(stmt) or f"{parent_clause_id or 'root'}.{ordinal + 1}"
                if not self._extract_clause_id(stmt):
                    clause_id = _stable_clause_id(block.input_id, block.block_id, stmt, ordinal + 1)

                metrics = self._extract_metrics(stmt)
                subj, act, obj = self._parse_semantics(stmt)
                severity = "blocking" if kind in (RequirementKind.MANDATORY, RequirementKind.QUALIFICATION) else "normal"
                if any(marker in stmt for marker in _NEGATION_MARKERS) and kind is RequirementKind.MANDATORY:
                    severity = "blocking"

                items.append(
                    RequirementItem(
                        requirement_id=self._generate_requirement_id(block.input_id, block.block_id, ordinal),
                        kind=kind,
                        source_anchor=block.source_anchor,
                        original_text=stmt,
                        normalized_requirement=stmt,
                        severity=severity,  # type: ignore[arg-type]
                        response_type=self._response_type(kind),
                        evidence_policy="tender_or_company" if kind == RequirementKind.QUALIFICATION else "tender_traceable",
                        status="open" if review_status == "confirmed" else "blocked",
                        clause_id=clause_id,
                        parent_clause_id=parent_clause_id,
                        subject=subj,
                        action=act,
                        target_object=obj,
                        conditions=self._extract_conditions(stmt),
                        exceptions=self._extract_exceptions(stmt),
                        quantitative_metrics={**metrics, "_confidence": confidence, "_review": review_status},
                    )
                )
        return ExtractionBatchResult(
            batch_id=batch_id,
            block_ids=[block.block_id for block in blocks],
            items=items,
            abstained=abstained,
            needs_human=needs_human,
        )

    @staticmethod
    def _atomic_statements(content: str) -> list[str]:
        normalized = content.replace("；", "。").replace(";", "。")
        parts: list[str] = []
        for line in normalized.splitlines():
            for piece in re.split(r"[。！？]", line):
                text = piece.strip(" -•\t")
                if len(text) >= 2:
                    parts.append(text)
        return parts

    @staticmethod
    def _is_non_obligation_prose(statement: str) -> bool:
        text = statement.strip()
        # Publication dates and table-of-contents/page-number lines are source
        # structure, not bidder obligations.  Check these before generic marker
        # matching because words such as “须知” contain the single-character
        # marker “须”.
        if _STANDALONE_DATE.fullmatch(text) or _TOC_PAGE_LINE.fullmatch(text):
            return True
        if any(marker in statement for marker in _OBLIGATION_MARKERS):
            return False
        if any(
            marker in statement
            for marker in _DECLARED_REQUIREMENT_MARKERS
        ):
            return False
        if _QUANTIFIED_OBLIGATION.search(statement):
            return False
        # Pure narrative without obligation markers is abstained rather than guessed.
        return True

    @staticmethod
    def _confidence(statement: str, kind: RequirementKind) -> float:
        score = 0.35
        if any(marker in statement for marker in _OBLIGATION_MARKERS):
            score += 0.25
        if any(
            marker in statement
            for marker in _DECLARED_REQUIREMENT_MARKERS
        ):
            score += 0.25
        if kind is RequirementKind.SCORE:
            score += 0.15
        if kind is not RequirementKind.MANDATORY:
            score += 0.1
        if re.search(r"\d", statement):
            score += 0.1
        if any(marker in statement for marker in _NEGATION_MARKERS):
            score += 0.1
        if any(marker in statement for marker in _EXCEPTION_MARKERS):
            score += 0.05
        return min(score, 0.99)

    @staticmethod
    def _generate_requirement_id(input_id: str, block_id: str, ordinal: int) -> str:
        token = hashlib.sha256(f"{input_id}:{block_id}:{ordinal}".encode("utf-8")).hexdigest()[:12]
        return f"R-{token}"

    @staticmethod
    def _extract_clause_id(text: str) -> str | None:
        match = re.match(r"^([一二三四五六七八九十0-9]+(?:[\.、．][0-9]+)*)", text)
        return match.group(1) if match else None

    @staticmethod
    def _classify(statement: str) -> RequirementKind:
        if any(word in statement for word in ("资格", "资质", "证书", "业绩", "人员", "ISO")):
            return RequirementKind.QUALIFICATION
        if any(word in statement for word in ("验收", "验收标准", "验收条件")):
            return RequirementKind.ACCEPTANCE
        if any(word in statement for word in ("交付", "成果", "提交", "报告")):
            return RequirementKind.DELIVERABLE
        if any(word in statement for word in ("合同", "付款", "违约")):
            return RequirementKind.CONTRACT
        return RequirementKind.MANDATORY

    @staticmethod
    def _response_type(kind: RequirementKind) -> str:
        return {
            RequirementKind.SCORE: "score_response",
            RequirementKind.QUALIFICATION: "evidence_response",
            RequirementKind.DELIVERABLE: "deliverable_response",
            RequirementKind.ACCEPTANCE: "acceptance_response",
            RequirementKind.CONTRACT: "commitment_response",
            RequirementKind.MANDATORY: "mandatory_response",
        }[kind]

    @staticmethod
    def _extract_metrics(text: str) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        digits = re.findall(r"(\d+(?:\.\d+)?)\s*(个|天|日|月|年|%|分|元|万元|人|次)", text)
        for val, unit in digits:
            unit_key = "天" if unit == "日" else unit
            metrics[unit_key] = float(val) if "." in val else int(val)
        return metrics

    @staticmethod
    def _parse_semantics(text: str) -> tuple[str | None, str | None, str | None]:
        subj = "投标人" if "投标人" in text else ("乙方" if "乙方" in text else ("系统" if "系统" in text else None))
        act = None
        for candidate in ("提供", "具备", "满足", "保证", "确保", "提交", "完成", "不得", "禁止"):
            if candidate in text:
                act = candidate
                break
        obj = text[:40]
        return subj, act, obj

    @staticmethod
    def _extract_conditions(text: str) -> list[str]:
        conds: list[str] = []
        if "在" in text and "前提下" in text:
            conds.append(text[text.find("在") : text.find("前提下") + 3])
        for match in re.finditer(r"(合同签署后\d+天内|验收前|实施期间)", text):
            conds.append(match.group(1))
        return conds

    @staticmethod
    def _extract_exceptions(text: str) -> list[str]:
        exps: list[str] = []
        if "除" in text and "外" in text and text.find("除") < text.find("外"):
            exps.append(text[text.find("除") : text.find("外") + 1])
        for marker in ("不可抗力除外", "法律另有规定除外"):
            if marker in text:
                exps.append(marker)
        return exps


def _stable_clause_id(*parts: Any) -> str:
    token = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]
    return f"C-{token}"


def _normalize_for_overlap(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text.lower())
    # Character bigrams for Chinese-friendly overlap without external tokenizers.
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class RequirementAgent:
    """Agent that produces candidate RequirementLedger proposals from frozen SourceIndex."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        provider: RequirementInferenceProvider | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.context = context
        self.root = context.root
        self.provider = provider or DeterministicRequirementExtractor()
        self.batch_size = max(1, int(batch_size))

    def extract_requirements(
        self,
        source_blocks: list[SourceBlock],
        input_manifest: InputManifest,
    ) -> list[RequirementItem]:
        batches = self.extract_batches(source_blocks)
        items = self.reconcile_batches(batches)
        return self.reconcile_amendments(items, input_manifest, source_blocks)

    def extract_batches(self, source_blocks: list[SourceBlock]) -> list[ExtractionBatchResult]:
        """Process frozen SourceBlocks in batches; headings update shared clause context."""
        results: list[ExtractionBatchResult] = []
        clause_context: list[dict[str, str]] = []
        scoring_block_ids = scoring_table_data_block_ids(source_blocks)
        batch: list[SourceBlock] = []
        batch_index = 0

        def flush() -> None:
            nonlocal batch_index, batch
            if not batch:
                return
            batch_id = f"batch-{batch_index:04d}"
            result = self.provider.extract_batch(
                batch_id=batch_id,
                blocks=list(batch),
                clause_context=list(clause_context),
                scoring_block_ids=scoring_block_ids,
            )
            results.append(result)
            # Advance clause context using headings inside this batch for next batches.
            for block in batch:
                if block.block_kind != "heading":
                    continue
                depth = len(block.heading_path) if block.heading_path else 1
                while clause_context and int(clause_context[-1]["depth"]) >= depth:
                    clause_context.pop()
                clause_context.append(
                    {
                        "depth": str(depth),
                        "clause_id": _stable_clause_id(block.input_id, block.block_id, block.content, depth),
                        "title": block.content,
                    }
                )
            batch_index += 1
            batch = []

        for block in source_blocks:
            batch.append(block)
            if len(batch) >= self.batch_size:
                flush()
        flush()
        return results

    def reconcile_batches(self, batches: list[ExtractionBatchResult]) -> list[RequirementItem]:
        """Cross-batch dedupe/merge and conflict detection."""
        merged: list[RequirementItem] = []
        seen_text: dict[str, RequirementItem] = {}
        conflicts: list[dict[str, Any]] = []

        for batch in batches:
            for item in batch.items:
                key = re.sub(r"\s+", "", item.normalized_requirement)
                existing = seen_text.get(key)
                if existing is None:
                    seen_text[key] = item
                    merged.append(item)
                    continue
                # Same text, keep first anchor; record merge.
                conflicts.append(
                    {
                        "type": "duplicate",
                        "kept_requirement_id": existing.requirement_id,
                        "dropped_requirement_id": item.requirement_id,
                        "text": item.normalized_requirement[:120],
                    }
                )

        # Semantic conflicts: same metric unit with different values and overlapping subjects.
        by_metric: dict[str, list[RequirementItem]] = {}
        for item in merged:
            for unit, value in item.quantitative_metrics.items():
                if unit.startswith("_"):
                    continue
                by_metric.setdefault(unit, []).append(item)
        for unit, group in by_metric.items():
            values = {str(item.quantitative_metrics.get(unit)) for item in group}
            if len(values) > 1 and len(group) > 1:
                conflicts.append(
                    {
                        "type": "metric_conflict",
                        "unit": unit,
                        "requirement_ids": [item.requirement_id for item in group],
                        "values": sorted(values),
                    }
                )
                for item in group:
                    item.status = "blocked"

        # Stash conflicts into quantitative_metrics audit side-channel for proposal coverage_audit merge.
        self._last_conflicts = conflicts
        self._last_batch_audit = {
            "batch_count": len(batches),
            "abstained": [row for batch in batches for row in batch.abstained],
            "needs_human": [row for batch in batches for row in batch.needs_human],
            "conflicts": conflicts,
            "prompt_version": getattr(self.provider, "prompt_version", PROMPT_EXTRACT_VERSION),
            "reconcile_prompt_version": PROMPT_RECONCILE_VERSION,
            "model_fingerprint": getattr(self.provider, "model_fingerprint", "unknown"),
            "policy_version": REQUIREMENT_POLICY_VERSION,
            "schema_version": REQUIREMENT_SCHEMA_VERSION,
        }
        return merged

    def reconcile_amendments(
        self,
        items: list[RequirementItem],
        input_manifest: InputManifest,
        source_blocks: list[SourceBlock] | None = None,
    ) -> list[RequirementItem]:
        """Scoped amendment override: waive only overlapping obligations, not whole superseded files."""
        active_amendments = [inp for inp in input_manifest.inputs if inp.role == InputRole.AMENDMENT and inp.active]
        if not active_amendments:
            return items

        blocks_by_input: dict[str, list[SourceBlock]] = {}
        for block in source_blocks or []:
            blocks_by_input.setdefault(block.input_id, []).append(block)

        sorted_amendments = sorted(active_amendments, key=lambda item: (item.issued_at or "", item.version))
        amendment_texts: dict[str, set[str]] = {}
        for amd in sorted_amendments:
            texts: set[str] = set()
            for block in blocks_by_input.get(amd.input_id, []):
                if block.block_kind == "heading":
                    continue
                texts |= _normalize_for_overlap(block.content)
            amendment_texts[amd.input_id] = texts

        reconciled: list[RequirementItem] = []
        for item in items:
            src_input_id = item.source_anchor.source_input_id
            item_tokens = _normalize_for_overlap(item.normalized_requirement)
            for amd in sorted_amendments:
                if src_input_id not in amd.supersedes_input_ids:
                    continue
                amd_tokens = amendment_texts.get(amd.input_id) or set()
                # Scope: only waive when amendment content overlaps this requirement, or same clause id appears.
                overlap = _jaccard(item_tokens, amd_tokens)
                clause_hit = bool(item.clause_id and any(item.clause_id in block.content for block in blocks_by_input.get(amd.input_id, [])))
                if overlap >= 0.18 or clause_hit:
                    item.status = "waived"
                    item.superseded_by_input_id = amd.input_id
            reconciled.append(item)
        return reconciled

    def create_extraction_proposal(
        self,
        items: list[RequirementItem],
        base_revision: int,
        operation_id: str,
        *,
        source_hashes: dict[str, str] | None = None,
        coverage_audit: dict[str, Any] | None = None,
    ) -> ProposalEnvelope:
        cited_source_ids = sorted({item.source_anchor.source_input_id for item in items})
        store = ControlStore(self.context)
        active_source = store.v3_active_artifact("SourceIndex")
        resolved: dict[str, Any] = {}
        declared: list[DependencyRef] = []
        if active_source is not None:
            resolved["SourceIndex"] = {
                "artifact_kind": "SourceIndex",
                "artifact_id": str(active_source["artifact_id"]),
                "revision": int(active_source["revision"]),
                "artifact_hash": str(active_source["artifact_hash"]),
            }
            declared.append(
                DependencyRef(
                    artifact_kind="SourceIndex",
                    expected_revision=int(active_source["revision"]),
                    expected_hash=str(active_source["artifact_hash"]),
                )
            )

        audit = dict(coverage_audit or {})
        batch_audit = getattr(self, "_last_batch_audit", {})
        audit.update(
            {
                "source_index_revision": int(active_source["revision"]) if active_source else None,
                "source_index_hash": str(active_source["artifact_hash"]) if active_source else None,
                "batch_audit": batch_audit,
                "prompt_version": getattr(self.provider, "prompt_version", PROMPT_EXTRACT_VERSION),
                "reconcile_prompt_version": PROMPT_RECONCILE_VERSION,
                "model_fingerprint": getattr(self.provider, "model_fingerprint", "unknown"),
                "policy_version": REQUIREMENT_POLICY_VERSION,
                "schema_version": REQUIREMENT_SCHEMA_VERSION,
            }
        )

        ledger = RequirementLedger(
            revision=base_revision + 1,
            source_hashes=source_hashes or {},
            requirements=items,
            coverage_audit=audit,
        )
        prompt_version = getattr(self.provider, "prompt_version", PROMPT_EXTRACT_VERSION)
        model_fingerprint = getattr(self.provider, "model_fingerprint", "deterministic_v3_agent")
        dep_fp = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=resolved,
            artifact_kind="RequirementLedger",
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )

        return ProposalEnvelope(
            workspace_id=self.context.workspace_id,
            artifact_kind="RequirementLedger",
            producer_role="requirement_agent",
            operation_id=operation_id,
            base_revision=base_revision,
            declared_dependencies=declared,
            dependency_fingerprint=dep_fp,
            payload=ledger.model_dump(mode="json"),
            cited_source_ids=cited_source_ids,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )
