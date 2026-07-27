"""Requirement Agent for V3 trusted extraction, amendment reconciliation and proposal generation."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from control_plane import WorkspaceContext

from .contracts import (
    InputManifest,
    InputRole,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    SourceAnchor,
    SourceBlock,
)
from .artifact_promotion import build_declared_dependency_fingerprint
from .proposals import ProposalEnvelope


class RequirementAgent:
    """Agent that produces candidate RequirementLedger proposals from frozen SourceIndex."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def extract_requirements(
        self,
        source_blocks: list[SourceBlock],
        input_manifest: InputManifest,
    ) -> list[RequirementItem]:
        """Extract atomic requirements from frozen source blocks with structured semantics."""
        items: list[RequirementItem] = []
        clause_stack: list[tuple[int, str]] = []

        for block in source_blocks:
            if block.input_role not in (InputRole.TENDER, InputRole.AMENDMENT, InputRole.SCORE):
                continue

            content = block.content.strip()
            if not content:
                continue

            # Track clause hierarchy from headings or heading_path
            if block.block_kind == "heading":
                current_depth = len(block.heading_path) if block.heading_path else 1
                while clause_stack and clause_stack[-1][0] >= current_depth:
                    clause_stack.pop()
                clause_stack.append((current_depth, content))
                continue

            parent_clause = clause_stack[-1][1] if clause_stack else (block.heading_path[-1] if block.heading_path else None)

            # Atomic statement extraction
            statements = self._atomic_statements(content)
            for ordinal, stmt in enumerate(statements):
                kind = RequirementKind.SCORE if block.input_role == InputRole.SCORE else self._classify(stmt)
                req_id = self._generate_requirement_id(block.input_id, block.block_id, ordinal)
                clause_id = self._extract_clause_id(stmt) or f"block-{block.ordinal}.{ordinal}"
                metrics = self._extract_metrics(stmt)
                subj, act, obj = self._parse_semantics(stmt)

                items.append(
                    RequirementItem(
                        requirement_id=req_id,
                        kind=kind,
                        source_anchor=block.source_anchor,
                        original_text=stmt,
                        normalized_requirement=stmt,
                        severity="blocking" if kind in (RequirementKind.MANDATORY, RequirementKind.QUALIFICATION) else "normal",
                        response_type=self._response_type(kind),
                        evidence_policy="tender_or_company" if kind == RequirementKind.QUALIFICATION else "tender_traceable",
                        clause_id=clause_id,
                        parent_clause_id=parent_clause,
                        subject=subj,
                        action=act,
                        target_object=obj,
                        conditions=self._extract_conditions(stmt),
                        exceptions=self._extract_exceptions(stmt),
                        quantitative_metrics=metrics,
                    )
                )

        return self.reconcile_amendments(items, input_manifest)

    def reconcile_amendments(
        self,
        items: list[RequirementItem],
        input_manifest: InputManifest,
    ) -> list[RequirementItem]:
        """Apply amendment supersedes rules based on input manifest issued_at and relationships."""
        active_amendments = [inp for inp in input_manifest.inputs if inp.role == InputRole.AMENDMENT and inp.active]
        if not active_amendments:
            return items

        # Sort amendments chronologically by issued_at date and version
        sorted_amendments = sorted(
            active_amendments,
            key=lambda item: (item.issued_at or "", item.version),
        )

        reconciled: list[RequirementItem] = []
        for item in items:
            src_input_id = item.source_anchor.source_input_id
            for amd in sorted_amendments:
                if src_input_id in amd.supersedes_input_ids:
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
        """Wrap extracted requirements into a valid ProposalEnvelope for G0/G1 gate validation."""
        cited_source_ids = sorted({item.source_anchor.source_input_id for item in items})
        ledger = RequirementLedger(
            revision=base_revision + 1,
            source_hashes=source_hashes or {},
            requirements=items,
            coverage_audit=coverage_audit or {},
        )
        prompt_version = "v3_requirement_agent_v1.0"
        model_fingerprint = "deterministic_v3_agent"
        # RequirementLedger has no promoted Artifact deps until PR-16.1 Source promotion.
        dep_fp = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot={},
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
            declared_dependencies=[],
            dependency_fingerprint=dep_fp,
            payload=ledger.model_dump(mode="json"),
            cited_source_ids=cited_source_ids,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )

    @staticmethod
    def _atomic_statements(content: str) -> list[str]:
        parts = [part.strip(" -•\t") for part in content.replace("；", "。\n").splitlines()]
        return [part for part in parts if len(part) >= 2]

    @staticmethod
    def _generate_requirement_id(input_id: str, block_id: str, ordinal: int) -> str:
        token = hashlib.sha256(f"{input_id}:{block_id}:{ordinal}".encode("utf-8")).hexdigest()[:12]
        return f"R-{token}"

    @staticmethod
    def _extract_clause_id(text: str) -> str | None:
        match = re.match(r"^([一二三四五六七八九十0-9]+[\.、质§\(（][0-9\.\)）]*)", text)
        return match.group(1) if match else None

    @staticmethod
    def _classify(statement: str) -> RequirementKind:
        if any(word in statement for word in ("资格", "资质", "证书", "业绩", "人员")):
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
        digits = re.findall(r"(\d+(?:\.\d+)?)\s*(个|天|月|年|%|分|元|万元|人|次)", text)
        for val, unit in digits:
            metrics[unit] = float(val) if "." in val else int(val)
        return metrics

    @staticmethod
    def _parse_semantics(text: str) -> tuple[str | None, str | None, str | None]:
        subj = "投标人" if "投标人" in text else ("系统" if "系统" in text else None)
        act = "提供" if "提供" in text else ("具备" if "具备" in text else ("满足" if "满足" in text else None))
        obj = text[:30] if len(text) > 30 else text
        return subj, act, obj

    @staticmethod
    def _extract_conditions(text: str) -> list[str]:
        conds = []
        if "在" in text and "前提下" in text:
            conds.append(text[text.find("在") : text.find("前提下") + 3])
        return conds

    @staticmethod
    def _extract_exceptions(text: str) -> list[str]:
        exps = []
        if "除" in text and "外" in text:
            exps.append(text[text.find("除") : text.find("外") + 1])
        return exps
