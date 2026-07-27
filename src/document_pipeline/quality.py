from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import IntegratedDocument, QualityReport, RequirementLedger
from .input_manifest import V3_ROOT
from .integrator import INTEGRATED_DOCUMENT_PATH
from .requirement_ledger import load_promoted_requirement_ledger


FINAL_COVERAGE_PATH = V3_ROOT / "reports" / "final_coverage.json"
CONTENT_QUALITY_PATH = V3_ROOT / "reports" / "content_quality.json"


class QualityGate:
    """Read-only final audit. Failed checks must route work back to integration."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def verify(self) -> QualityReport:
        ledger = load_promoted_requirement_ledger(self.context)
        document = IntegratedDocument.model_validate(read_json(self.root / INTEGRATED_DOCUMENT_PATH))
        covered = {requirement_id for block in document.blocks for requirement_id in block.requirement_ids}
        findings: list[dict[str, object]] = []
        for requirement in ledger.requirements:
            if requirement.requirement_id not in covered:
                findings.append({"code": "REQUIREMENT_UNCOVERED", "severity": "blocking" if requirement.severity == "blocking" else "major", "requirement_id": requirement.requirement_id})
        for block in document.blocks:
            if block.critical_claims and not (block.evidence_ids or block.fact_ids):
                findings.append({"code": "CLAIM_UNSOURCED", "severity": "blocking", "block_id": block.block_id})
        verdict = "fail" if any(item["severity"] == "blocking" for item in findings) else ("warn" if findings else "pass")
        report = QualityReport(
            revision=document.revision,
            source_hashes={"integrated_document": hashlib.sha256("|".join(block.block_id for block in document.blocks).encode("utf-8")).hexdigest()},
            report_id=f"quality-{document.revision}",
            verdict=verdict,
            findings=findings,
        )
        coverage = {"schema_version": "v3", "revision": document.revision, "covered_requirement_ids": sorted(covered), "uncovered_requirement_ids": sorted({item.requirement_id for item in ledger.requirements} - covered)}
        write_json(self.root / FINAL_COVERAGE_PATH, coverage)
        write_json(self.root / CONTENT_QUALITY_PATH, report.model_dump(mode="json"))
        self.store.record_gate_evaluation(
            command="verify_document",
            verdict="pass" if verdict == "pass" else "block",
            input_fingerprint=report.source_hashes["integrated_document"],
            findings=findings,
            source="v3.quality",
        )
        return report
