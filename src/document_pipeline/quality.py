from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import IntegratedDocument, QualityReport, RequirementLedger
from .input_manifest import V3_ROOT
from .integrator import INTEGRATED_DOCUMENT_PATH
from .requirement_ledger import load_promoted_requirement_ledger
from .score_model import load_promoted_score_model
from .writer_policy import content_quality_findings


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
        active_requirements = [
            requirement
            for requirement in ledger.requirements
            if requirement.status not in {"blocked", "waived"}
        ]
        blueprint_artifact = self.store.v3_active_artifact(
            "ChapterBlueprint"
        )
        if (
            blueprint_artifact is not None
            and str(
                (blueprint_artifact.get("payload") or {}).get(
                    "planning_model"
                )
                or "topic_graph"
            )
            == "score_direct"
        ):
            scores = load_promoted_score_model(self.context)
            blueprint_payload = blueprint_artifact.get("payload") or {}
            deferred_requirement_ids = {
                requirement_id
                for node in (blueprint_payload.get("nodes") or [])
                if isinstance(node, dict)
                and str(node.get("content_policy") or "full") != "full"
                for requirement_id in (node.get("requirement_ids") or [])
            }
            deferred_response_unit_ids = {
                unit_id
                for node in (blueprint_payload.get("nodes") or [])
                if isinstance(node, dict)
                and str(node.get("content_policy") or "full") != "full"
                for unit_id in [
                    *(node.get("primary_response_unit_ids") or []),
                    *(node.get("supporting_response_unit_ids") or []),
                ]
            }
            score_linked_requirement_ids = {
                requirement_id
                for point in scores.points
                if point.review_status != "blocked"
                for unit in point.response_units
                if unit.review_status != "blocked"
                and unit.response_scope == "section"
                and unit.unit_id not in deferred_response_unit_ids
                for requirement_id in unit.linked_requirement_ids
            }
            active_requirements = [
                requirement
                for requirement in active_requirements
                if requirement.requirement_id in score_linked_requirement_ids
                and requirement.requirement_id not in deferred_requirement_ids
            ]
        findings: list[dict[str, object]] = []
        for requirement in active_requirements:
            if requirement.requirement_id not in covered:
                findings.append({"code": "REQUIREMENT_UNCOVERED", "severity": "blocking" if requirement.severity == "blocking" else "major", "requirement_id": requirement.requirement_id})
        for block in document.blocks:
            if block.critical_claims and not (block.evidence_ids or block.fact_ids):
                findings.append({"code": "CLAIM_UNSOURCED", "severity": "blocking", "block_id": block.block_id})
            bound_sources = [
                str(
                    requirement.normalized_requirement
                    or ""
                )
                for requirement in ledger.requirements
                if requirement.requirement_id in block.requirement_ids
            ]
            for issue in content_quality_findings(
                block.content,
                source_texts=bound_sources,
            ):
                findings.append(
                    {
                        **issue,
                        "severity": "blocking",
                        "block_id": block.block_id,
                    }
                )
        if len(document.blocks) > 1:
            for issue in content_quality_findings(
                "\n\n".join(block.content for block in document.blocks)
            ):
                if issue["code"] != "DUPLICATE_PARAGRAPH":
                    continue
                findings.append(
                    {
                        **issue,
                        "severity": "blocking",
                        "block_id": "document",
                    }
                )
        verdict = "fail" if any(item["severity"] == "blocking" for item in findings) else ("warn" if findings else "pass")
        report = QualityReport(
            revision=document.revision,
            source_hashes={"integrated_document": hashlib.sha256("|".join(block.block_id for block in document.blocks).encode("utf-8")).hexdigest()},
            report_id=f"quality-{document.revision}",
            verdict=verdict,
            findings=findings,
        )
        coverage = {"schema_version": "v3", "revision": document.revision, "covered_requirement_ids": sorted(covered), "uncovered_requirement_ids": sorted({item.requirement_id for item in active_requirements} - covered)}
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
