from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import RequirementItem, RequirementKind, RequirementLedger, SourceAnchor
from .input_manifest import V3_ROOT
from .source_normalizer import SOURCE_INDEX_PATH


LEDGER_PATH = V3_ROOT / "requirement_ledger.json"


class RequirementLedgerBuilder:
    """Deterministically atomize tender and score fragments into V3 requirements.

    Later LLM extraction may split a fragment further, but it must preserve this
    source anchor and never replace the ledger with untraceable prose.
    """

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> RequirementLedger:
        index = read_json(self.root / SOURCE_INDEX_PATH)
        if not isinstance(index, dict):
            raise ValueError("V3 source_index 无效")
        roles = index.get("by_role") if isinstance(index.get("by_role"), dict) else {}
        rows: list[RequirementItem] = []
        for role_name in ("tender", "score"):
            chunks = roles.get(role_name, [])
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                content = str(chunk.get("content") or "").strip()
                if not content:
                    continue
                chunk_id = str(chunk.get("chunk_id") or "")
                input_id = str(chunk.get("input_id") or "")
                anchor_data = chunk.get("source_anchor") if isinstance(chunk.get("source_anchor"), dict) else {}
                anchor = SourceAnchor(
                    source_input_id=input_id,
                    chunk_id=chunk_id,
                    page=anchor_data.get("page"),
                    location=str(anchor_data.get("location") or "paragraph:unknown"),
                )
                for ordinal, statement in enumerate(self._atomic_statements(content)):
                    kind = RequirementKind.SCORE if role_name == "score" else self._classify(statement)
                    requirement_id = self._requirement_id(input_id, chunk_id, ordinal)
                    rows.append(
                        RequirementItem(
                            requirement_id=requirement_id,
                            kind=kind,
                            source_anchor=anchor,
                            original_text=statement,
                            normalized_requirement=statement,
                            severity="blocking" if kind in {RequirementKind.MANDATORY, RequirementKind.QUALIFICATION} else "normal",
                            response_type=self._response_type(kind),
                            evidence_policy="tender_or_company" if kind is RequirementKind.QUALIFICATION else "tender_traceable",
                        )
                    )
        source_hashes = index.get("source_hashes") if isinstance(index.get("source_hashes"), dict) else {}
        ledger = RequirementLedger(revision=int(index.get("revision") or 1), source_hashes=source_hashes, requirements=rows)
        write_json(self.root / LEDGER_PATH, ledger.model_dump(mode="json"))
        return ledger

    @staticmethod
    def _atomic_statements(content: str) -> list[str]:
        # Keep punctuation-bearing legal and scoring clauses intact where possible.
        parts = [part.strip(" -•\t") for part in content.replace("；", "。\n").splitlines()]
        return [part for part in parts if len(part) >= 2]

    @staticmethod
    def _requirement_id(input_id: str, chunk_id: str, ordinal: int) -> str:
        token = hashlib.sha256(f"{input_id}:{chunk_id}:{ordinal}".encode("utf-8")).hexdigest()[:12]
        return f"R-{token}"

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
