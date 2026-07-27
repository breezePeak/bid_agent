from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import EvidenceNeed, ProjectFact, ProjectModel, RequirementKind, RequirementLedger, SourceAnchor
from .input_manifest import V3_ROOT
from .requirement_ledger import load_promoted_requirement_ledger
from .source_normalizer import SOURCE_INDEX_PATH


PROJECT_MODEL_PATH = V3_ROOT / "project_model.json"
UNDERSTANDING_REPORT_PATH = V3_ROOT / "reports" / "understanding_gate.json"


class ProjectModelBuilder:
    """Build the minimum sufficient project understanding from frozen evidence."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    def build(self) -> ProjectModel:
        ledger = load_promoted_requirement_ledger(self.context)
        index = read_json(self.root / SOURCE_INDEX_PATH)
        roles = index.get("by_role") if isinstance(index, dict) and isinstance(index.get("by_role"), dict) else {}
        tender_chunks = roles.get("tender", []) if isinstance(roles.get("tender", []), list) else []
        company_chunks = roles.get("company", []) if isinstance(roles.get("company", []), list) else []
        mandatory = [item for item in ledger.requirements if item.kind is RequirementKind.MANDATORY]
        deliverables = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.DELIVERABLE]
        acceptance = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.ACCEPTANCE]
        constraints = [item.normalized_requirement for item in ledger.requirements if item.kind in {RequirementKind.CONTRACT, RequirementKind.QUALIFICATION}]
        milestones = [
            item.normalized_requirement
            for item in ledger.requirements
            if any(token in item.normalized_requirement for token in ("工期", "期限", "工作日", "个月", "年度"))
        ]
        roles = [item.normalized_requirement for item in ledger.requirements if item.kind is RequirementKind.QUALIFICATION]
        first_tender = str(tender_chunks[0].get("content") or "") if tender_chunks else ""
        facts = [self._company_fact(chunk) for chunk in company_chunks if isinstance(chunk, dict) and str(chunk.get("content") or "").strip()]
        evidence_needs: list[EvidenceNeed] = []
        unknowns: list[str] = []
        if not facts and any(item.kind is RequirementKind.QUALIFICATION for item in ledger.requirements):
            unknowns.append("尚未提供可核验的企业资质、人员或业绩材料")
            evidence_needs.append(
                EvidenceNeed(
                    need_id="EN-company-qualification",
                    question="请补充与资格要求对应的企业资质、人员和业绩材料。",
                    topic_id="company_qualification",
                    priority="blocking",
                    blocking_scope="content_unit",
                    deadline_stage="execute_content_plan",
                    query_budget=0,
                )
            )
        if not deliverables:
            unknowns.append("招标来源未识别出明确交付物")
        if not acceptance:
            unknowns.append("招标来源未识别出明确验收条件")
        model = ProjectModel(
            revision=ledger.revision,
            source_hashes=ledger.source_hashes,
            project_id=self._project_id(ledger),
            identity={"project_name": self._project_name(first_tender)},
            background=[first_tender] if first_tender else [],
            goals=[item.normalized_requirement for item in mandatory[:3]],
            scope=[item.normalized_requirement for item in mandatory],
            work_packages=[item.normalized_requirement for item in mandatory],
            deliverables=deliverables,
            acceptance_conditions=acceptance,
            milestones=milestones,
            roles=roles,
            constraints=constraints,
            confirmed_facts=facts,
            unknowns=unknowns,
            requirement_ids=[item.requirement_id for item in ledger.requirements],
            evidence_needs=evidence_needs,
        )
        write_json(self.root / PROJECT_MODEL_PATH, model.model_dump(mode="json"))
        write_json(self.root / UNDERSTANDING_REPORT_PATH, self.evaluate(model))
        return model

    @staticmethod
    def evaluate(model: ProjectModel) -> dict[str, object]:
        missing = [
            label
            for label, value in (
                ("project_goal", model.goals),
                ("scope", model.scope),
                ("work_packages", model.work_packages),
                ("deliverables", model.deliverables),
                ("acceptance_conditions", model.acceptance_conditions),
                ("milestones", model.milestones),
            )
            if not value
        ]
        return {
            "schema_version": "v3",
            "revision": model.revision,
            "status": "blocked" if missing else "passed",
            "missing": missing,
            "unknowns": model.unknowns,
            "evidence_need_ids": [item.need_id for item in model.evidence_needs],
        }

    @staticmethod
    def _project_id(ledger: RequirementLedger) -> str:
        return f"project-{hashlib.sha256('|'.join(item.requirement_id for item in ledger.requirements).encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _project_name(text: str) -> str:
        first_line = text.splitlines()[0].strip() if text else ""
        return first_line[:80] or "未命名项目"

    @staticmethod
    def _company_fact(chunk: dict[str, object]) -> ProjectFact:
        input_id = str(chunk.get("input_id") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        anchor_data = chunk.get("source_anchor") if isinstance(chunk.get("source_anchor"), dict) else {}
        return ProjectFact(
            fact_id=f"F-{hashlib.sha256(chunk_id.encode('utf-8')).hexdigest()[:12]}",
            statement=str(chunk.get("content") or "").strip(),
            source_anchor=SourceAnchor(
                source_input_id=input_id,
                chunk_id=chunk_id,
                page=anchor_data.get("page"),
                location=str(anchor_data.get("location") or "paragraph:unknown"),
            ),
        )
