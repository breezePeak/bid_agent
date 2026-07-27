"""Score Agent for V3 source-traceable scoring-model proposals."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from control_plane import WorkspaceContext

from .contracts import (
    InputRole,
    RequirementLedger,
    ScoreEvidenceNeedCandidate,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    ScoringLevel,
    SourceAnchor,
    SourceBlock,
)
from .proposals import ProposalEnvelope, dependency_fingerprint


_SCORE_SIGNAL = re.compile(r"评分|评审|得分|分值|满分|废标|否决|资格")
_POINTS = re.compile(r"(?:(?:满分|最高|得)?\s*)(\d+(?:\.\d+)?)\s*分")
_TOTAL_POINTS = re.compile(r"(?:总分|满分合计|合计)\D{0,8}(\d+(?:\.\d+)?)\s*分")
_LEVEL = re.compile(r"(优秀|良好|一般|合格|不合格|差|[一二三四五六七八九十]档)\s*[：:，,]?\s*(\d+(?:\.\d+)?)?\s*分?\s*([^；;。]*)")


class ScoreAgent:
    """Extract scoring logic from frozen score blocks without modifying RequirementLedger."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def build_score_model(
        self,
        source_blocks: list[SourceBlock],
        requirement_ledger: RequirementLedger,
        *,
        revision: int,
        source_hashes: dict[str, str],
    ) -> ScoreModel:
        requirements_by_anchor: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for item in requirement_ledger.requirements:
            anchor = item.source_anchor
            requirements_by_anchor[(anchor.source_input_id, anchor.chunk_id)].append(
                (item.requirement_id, item.normalized_requirement)
            )

        groups: list[ScoreGroup] = []
        points: list[ScorePoint] = []
        group_by_input: dict[str, ScoreGroup] = {}
        current_group: ScoreGroup | None = None
        total_declared: float | None = None

        for block in source_blocks:
            if not self._is_scoring_block(block):
                continue
            content = block.content.strip()
            if block.block_kind == "heading":
                current_group = self._group_for_heading(block)
                group_by_input[block.input_id] = current_group
                groups.append(current_group)
                continue
            if match := _TOTAL_POINTS.search(content):
                total_declared = float(match.group(1))
            current_group = group_by_input.get(block.input_id) or current_group
            if current_group is None or current_group.group_id.startswith("score-default-"):
                current_group = self._default_group(block)
                group_by_input[block.input_id] = current_group
                if not any(group.group_id == current_group.group_id for group in groups):
                    groups.append(current_group)
            for ordinal, criterion in enumerate(self._atomic_criteria(content)):
                point = self._point_from_criterion(
                    block,
                    criterion,
                    ordinal,
                    current_group.group_id,
                    requirements_by_anchor,
                )
                points.append(point)

        points = self._deduplicate_points(points)
        known_total = sum(point.max_points or 0 for point in points)
        total_points = total_declared if total_declared is not None else known_total
        candidates = self._evidence_need_candidates(points)
        source_input_ids = sorted({anchor.source_input_id for point in points for anchor in point.source_anchors})
        model_id = f"SM-{hashlib.sha256('|'.join(source_input_ids).encode('utf-8')).hexdigest()[:12]}"
        return ScoreModel(
            revision=revision,
            source_hashes=source_hashes,
            model_id=model_id,
            source_input_ids=source_input_ids,
            total_points=total_points,
            groups=groups,
            points=points,
            evidence_need_candidates=candidates,
        )

    def create_score_model_proposal(
        self,
        score_model: ScoreModel,
        *,
        base_revision: int,
        operation_id: str,
        requirement_revision: int,
    ) -> ProposalEnvelope:
        cited_source_ids = score_model.source_input_ids
        return ProposalEnvelope(
            artifact_kind="ScoreModel",
            producer_role="score_agent",
            operation_id=operation_id,
            base_revision=base_revision,
            dependency_fingerprint=dependency_fingerprint(
                score_model.source_hashes,
                requirement_revision,
                cited_source_ids,
                "v3_score_agent_v1.0",
            ),
            payload=score_model.model_dump(mode="json"),
            cited_source_ids=cited_source_ids,
            prompt_version="v3_score_agent_v1.0",
            model_fingerprint="deterministic_v3_agent",
        )

    @staticmethod
    def _is_scoring_block(block: SourceBlock) -> bool:
        return block.input_role is InputRole.SCORE or (
            block.input_role is InputRole.AMENDMENT and bool(_SCORE_SIGNAL.search(block.content))
        )

    @staticmethod
    def _group_for_heading(block: SourceBlock) -> ScoreGroup:
        declared = ScoreAgent._first_points(block.content)
        token = hashlib.sha256(f"{block.input_id}:{block.block_id}".encode("utf-8")).hexdigest()[:12]
        return ScoreGroup(group_id=f"SG-{token}", title=block.content, declared_points=declared)

    @staticmethod
    def _default_group(block: SourceBlock) -> ScoreGroup:
        return ScoreGroup(group_id=f"score-default-{block.input_id}", title="未分组评分项")

    @staticmethod
    def _atomic_criteria(content: str) -> list[str]:
        parts = [part.strip(" -•\t") for part in re.split(r"[；;\n]", content)]
        return [part for part in parts if len(part) >= 2 and not _TOTAL_POINTS.fullmatch(part)]

    def _point_from_criterion(
        self,
        block: SourceBlock,
        criterion: str,
        ordinal: int,
        group_id: str,
        requirements_by_anchor: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> ScorePoint:
        token = hashlib.sha256(f"{block.input_id}:{block.block_id}:{ordinal}:{criterion}".encode("utf-8")).hexdigest()[:12]
        max_points = self._first_points(criterion)
        levels = self._scoring_levels(criterion)
        title = self._title(criterion)
        disqualifying = any(word in criterion for word in ("废标", "否决", "不合格", "资格性审查"))
        evidence_types = self._evidence_types(criterion)
        anchor = block.source_anchor
        linked_ids = sorted(
            requirement_id
            for requirement_id, requirement_text in requirements_by_anchor.get((anchor.source_input_id, anchor.chunk_id), [])
            if self._same_requirement(criterion, requirement_text)
        )
        return ScorePoint(
            score_point_id=f"SP-{token}",
            group_id=group_id,
            title=title,
            criterion=criterion,
            max_points=max_points,
            scoring_levels=levels,
            disqualifying=disqualifying,
            response_expectation=self._response_expectation(max_points, disqualifying),
            response_depth=self._response_depth(max_points, disqualifying),
            required_evidence_types=evidence_types,
            linked_requirement_ids=linked_ids,
            source_anchors=[anchor],
            confidence=1.0 if max_points is not None else 0.8,
            review_status="confirmed" if max_points is not None or disqualifying else "needs_review",
        )

    @staticmethod
    def _first_points(text: str) -> float | None:
        match = _POINTS.search(text)
        return float(match.group(1)) if match else None

    @staticmethod
    def _scoring_levels(criterion: str) -> list[ScoringLevel]:
        levels: list[ScoringLevel] = []
        for match in _LEVEL.finditer(criterion):
            label, points, detail = match.groups()
            levels.append(
                ScoringLevel(
                    label=label,
                    points=float(points) if points else None,
                    criterion=(detail or criterion).strip(),
                )
            )
        return levels

    @staticmethod
    def _title(criterion: str) -> str:
        value = re.sub(r"^\s*(?:\d+[\.、])?", "", criterion)
        value = re.split(r"[：:（(]", value, maxsplit=1)[0].strip()
        return value[:80] or criterion[:80]

    @staticmethod
    def _same_requirement(criterion: str, requirement_text: str) -> bool:
        normalize = lambda value: re.sub(r"[\s，,。；;：:（）()\-•]", "", value)
        left, right = normalize(criterion), normalize(requirement_text)
        if left == right:
            return True
        left_tokens, right_tokens = set(left), set(right)
        return bool(left_tokens and right_tokens) and len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.8

    @staticmethod
    def _evidence_types(criterion: str) -> list[str]:
        mapping = {
            "资质": "qualification_certificate",
            "证书": "qualification_certificate",
            "业绩": "project_reference",
            "案例": "project_reference",
            "人员": "personnel_record",
            "承诺": "commitment_letter",
            "证明": "supporting_document",
        }
        return sorted({evidence for term, evidence in mapping.items() if term in criterion})

    @staticmethod
    def _response_depth(max_points: float | None, disqualifying: bool) -> str:
        if disqualifying or (max_points is not None and max_points >= 10):
            return "detailed"
        if max_points is not None and max_points >= 3:
            return "substantive"
        return "basic"

    def _response_expectation(self, max_points: float | None, disqualifying: bool) -> str:
        depth = self._response_depth(max_points, disqualifying)
        if disqualifying:
            return "提供可核验证明并明确满足资格/否决条件。"
        return {"detailed": "提供可量化、可核验的完整响应与证明材料。", "substantive": "针对评分标准作出具体响应并提供对应证明。", "basic": "明确响应评分标准并保留来源依据。"}[depth]

    @staticmethod
    def _deduplicate_points(points: list[ScorePoint]) -> list[ScorePoint]:
        seen: set[tuple[str, str]] = set()
        result: list[ScorePoint] = []
        for point in points:
            key = (point.source_anchors[0].chunk_id, point.criterion)
            if key not in seen:
                seen.add(key)
                result.append(point)
        return result

    @staticmethod
    def _evidence_need_candidates(points: list[ScorePoint]) -> list[ScoreEvidenceNeedCandidate]:
        candidates: list[ScoreEvidenceNeedCandidate] = []
        for point in points:
            if not point.required_evidence_types:
                continue
            candidates.append(
                ScoreEvidenceNeedCandidate(
                    need_id=f"EN-{point.score_point_id}",
                    score_point_id=point.score_point_id,
                    question=f"请提供支撑“{point.title}”评分响应的证明材料。",
                    required_evidence_types=point.required_evidence_types,
                    priority="blocking" if point.disqualifying else "high",
                )
            )
        return candidates
