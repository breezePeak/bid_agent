"""Score Agent for V3 source-traceable scoring-model proposals."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass

from control_plane import WorkspaceContext

from .contracts import (
    RequirementLedger,
    ScoreEvidenceNeedCandidate,
    ScoreGroup,
    ScoreModel,
    ScoreCondition,
    ScorePoint,
    ScoreResponseUnit,
    ScoringLevel,
    SourceAnchor,
    SourceBlock,
)
from .artifact_promotion import build_declared_dependency_fingerprint
from control_plane import ControlStore
from .canonicalization import canonical_hash
from .proposals import DependencyRef, InferenceReceiptRef, ProposalEnvelope
from .score_semantic import (
    DeterministicScoreGroupInput,
    DeterministicScoreLevelInput,
    DeterministicScoreRuleInput,
    ScoreDocumentMapEntry,
    ScoreLinkedRequirementInput,
    ScoreSemanticCandidate,
    ScoreSemanticInput,
    ScoreSourceAnchorInput,
)
from .scoring_sources import is_scoring_source_block, scoring_table_headers
from .scoring_outline_policy import (
    SCORING_OUTLINE_POLICY_VERSION,
    full_score_condition_heading,
    highest_score_conditions,
    is_document_quality_score,
    is_evaluative_sentence_heading,
    outline_structure_key,
)


_SCORE_SIGNAL = re.compile(r"评分|评审|得分|分值|满分|废标|否决|资格")
_POINTS = re.compile(r"(?:(?:满分|最高|得|计)?\s*)(\d+(?:\.\d+)?)\s*分(?!钟)")
_TOTAL_POINTS = re.compile(r"(?:总分|满分合计|合计)\D{0,8}(\d+(?:\.\d+)?)\s*分(?!钟)")
_EXPLICIT_MAX_POINTS = re.compile(
    r"(?:本项(?:最高(?:得)?)?|最高(?:得)?|满分(?:为)?)\D{0,6}(\d+(?:\.\d+)?)\s*分(?!钟)"
)
_AWARDED_POINTS = re.compile(
    r"(?:(?:得|计)\s*(?:每(?:人|项|个|份)\s*)?"
    r"|每(?:人|项|个|份)\s*(?:得|计)\s*)"
    r"(\d+(?:\.\d+)?)\s*分(?!钟)"
)
_ZERO_AWARD = re.compile(r"(?:没有|无)\s*不得分")
_LEADING_SCORE_MECHANICS_CLAUSE = re.compile(
    r"^\s*(?:[，,；;。.]\s*)*"
    r"(?:累计计分"
    r"|(?:本项|该项)?"
    r"(?:最高(?:得)?|最多|总得分不超过|满分(?:为)?)"
    r"\s*\d+(?:\.\d+)?\s*分(?!钟))"
    r"\s*(?:[，,；;。.]\s*)*"
)
_LEVEL = re.compile(
    r"(优秀|良好|一般|合格|不合格|差|[一二三四五六七八九十]档)"
    r"\s*[：:，,]?\s*(\d+(?:\.\d+)?)?\s*分?(?!钟)\s*([^；;。]*)"
)
_LEVEL_LABEL = re.compile(r"(优秀|良好|一般|合格|不合格|差|[一二三四五六七八九十]档)")
_GROUP_LABEL = re.compile(r"^.{1,80}[（(][^（）()]{0,30}\d+(?:\.\d+)?\s*分\s*[）)]$")
_SCORE_METADATA = frozenset({"独立评分文件", "评分办法", "评分标准", "评分表", "评分细则", "综合评分法"})
_SCORING_TABLE_HEADER = re.compile(
    r"^(?:序号|评分因素|评标因素|评审因素|评分项目|评分项|评分标准|评审标准|评分细则|分值)$"
)
_EXPLICIT_TABLE_REFERENCE = re.compile(
    r"(?:附件|附表|表)\s*[一二三四五六七八九十百\d]+"
    r"(?:[.-]\d+)*"
)


@dataclass(frozen=True)
class _RequirementCandidate:
    requirement_id: str
    text: str
    source_anchor: SourceAnchor


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
        requirements_by_anchor: dict[tuple[str, str], list[_RequirementCandidate]] = defaultdict(list)
        for item in requirement_ledger.requirements:
            anchor = item.source_anchor
            requirements_by_anchor[(anchor.source_input_id, anchor.chunk_id)].append(
                _RequirementCandidate(
                    requirement_id=item.requirement_id,
                    text=item.normalized_requirement,
                    source_anchor=anchor,
                )
            )

        table_headers = scoring_table_headers(source_blocks)
        scoring_table_block_ids: set[str] = set()
        row_blocks_by_leader: dict[str, list[SourceBlock]] = {}
        rows: dict[tuple[str, int | None, int, int], list[SourceBlock]] = defaultdict(list)
        for block in source_blocks:
            if block.table_index is None or block.row_index is None or block.column_index is None:
                continue
            table_key = (block.input_id, block.page, block.table_index)
            if table_key not in table_headers:
                continue
            scoring_table_block_ids.add(block.block_id)
            if int(block.row_index) <= table_headers[table_key]:
                continue
            rows[(block.input_id, block.page, block.table_index, block.row_index)].append(block)
        for row_blocks in rows.values():
            canonical = self._canonical_row_blocks(row_blocks)
            if canonical:
                leader = min(canonical, key=lambda item: (item.ordinal, item.column_index or 0))
                row_blocks_by_leader[leader.block_id] = canonical

        # Bind each scoring table to the nearest preceding scored-group caption.
        # This is a source-position/table-role relation: the caption text is
        # never classified as an applicability label by wording.
        group_caption_by_table: dict[
            tuple[str, int | None, int], SourceBlock
        ] = {}
        for table_key in table_headers:
            table_blocks = [
                block
                for block in source_blocks
                if (
                    block.input_id,
                    block.page,
                    block.table_index,
                )
                == table_key
            ]
            if not table_blocks:
                continue
            first_table_ordinal = min(block.ordinal for block in table_blocks)
            captions = [
                block
                for block in source_blocks
                if block.input_id == table_key[0]
                and block.table_index is None
                and block.ordinal < first_table_ordinal
                and block.block_kind in {"heading", "paragraph"}
                and self._is_group_label(block.content.strip())
            ]
            if captions:
                group_caption_by_table[table_key] = max(
                    captions,
                    key=lambda item: item.ordinal,
                )

        groups: list[ScoreGroup] = []
        points: list[ScorePoint] = []
        group_by_input: dict[str, ScoreGroup] = {}
        group_by_table: dict[tuple[str, int | None, int], ScoreGroup] = {}
        current_group: ScoreGroup | None = None
        total_declared: float | None = None

        for block in source_blocks:
            if block.block_id in scoring_table_block_ids:
                row_blocks = row_blocks_by_leader.get(block.block_id)
                if row_blocks is None:
                    continue
                table_key = (
                    block.input_id,
                    block.page,
                    int(block.table_index or 0),
                )
                current_group = group_by_table.get(table_key)
                if current_group is None:
                    caption = group_caption_by_table.get(table_key)
                    if caption is not None:
                        current_group = self._group_for_heading(caption)
                        group_by_input[block.input_id] = current_group
                        if not any(
                            group.group_id == current_group.group_id
                            for group in groups
                        ):
                            groups.append(current_group)
                    else:
                        current_group = group_by_input.get(block.input_id)
                if current_group is None:
                    current_group = self._default_group(block)
                    group_by_input[block.input_id] = current_group
                    groups.append(current_group)
                group_by_table[table_key] = current_group
                point = self._point_from_table_row(
                    row_blocks,
                    current_group,
                    requirements_by_anchor,
                )
                if point is not None:
                    points.append(point)
                continue
            if not self._is_scoring_block(block):
                continue
            content = block.content.strip()
            if block.block_kind == "heading" and self._is_group_label(content):
                current_group = self._group_for_heading(block)
                group_by_input[block.input_id] = current_group
                groups.append(current_group)
                continue
            if match := _TOTAL_POINTS.search(content):
                total_declared = float(match.group(1))
            if self._is_group_label(content):
                current_group = self._group_for_heading(block)
                group_by_input[block.input_id] = current_group
                groups.append(current_group)
                continue
            current_group = group_by_input.get(block.input_id)
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
        points = self._disambiguate_titles(points)
        groups = self._reconcile_groups(groups, points)
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

    @staticmethod
    def semantic_input(
        score_model: ScoreModel,
        source_blocks: list[SourceBlock],
        requirement_ledger: RequirementLedger | None = None,
    ) -> ScoreSemanticInput:
        """Freeze deterministic table/points parsing for the semantic provider."""

        source_blocks_by_anchor = {
            (
                block.source_anchor.source_input_id,
                block.source_anchor.chunk_id,
            ): block
            for block in source_blocks
        }
        requirements_by_id = (
            {
                item.requirement_id: item
                for item in requirement_ledger.requirements
            }
            if requirement_ledger is not None
            else {}
        )
        context_requirement_ids_by_point = (
            {
                point.score_point_id: ScoreAgent._context_requirement_ids_for_point(
                    point,
                    source_blocks_by_anchor,
                    requirement_ledger,
                )
                for point in score_model.points
            }
            if requirement_ledger is not None
            else {
                point.score_point_id: []
                for point in score_model.points
            }
        )
        projected_linked_requirement_ids_by_point = {
            point.score_point_id: [
                requirement_id
                for requirement_id in point.linked_requirement_ids
                for requirement in [requirements_by_id.get(requirement_id)]
                if (
                    requirement is not None
                    and requirement.status not in {"blocked", "waived"}
                    and getattr(
                        requirement.kind,
                        "value",
                        requirement.kind,
                    )
                    != "score"
                )
            ]
            for point in score_model.points
        }
        if requirement_ledger is not None:
            unknown_direct_requirements = {
                requirement_id
                for point in score_model.points
                for requirement_id in point.linked_requirement_ids
                if requirement_id not in requirements_by_id
            }
            if unknown_direct_requirements:
                raise ValueError(
                    "评分结构引用了 RequirementLedger 中不存在的 linked "
                    f"requirement_id: {sorted(unknown_direct_requirements)}"
                )
        linked_requirement_ids = [
            requirement_id
            for point in score_model.points
            for requirement_id in (
                *projected_linked_requirement_ids_by_point[
                    point.score_point_id
                ],
                *context_requirement_ids_by_point[point.score_point_id],
            )
        ]
        linked_requirement_ids = list(dict.fromkeys(linked_requirement_ids))
        if requirement_ledger is not None:
            unknown_requirements = (
                set(linked_requirement_ids) - set(requirements_by_id)
            )
            if unknown_requirements:
                raise ValueError(
                    "评分结构引用了 RequirementLedger 中不存在的 requirement_id: "
                    f"{sorted(unknown_requirements)}"
                )
        else:
            # Historical callers did not pass the ledger.  Keep that call
            # shape valid, while refusing to emit dangling context references.
            linked_requirement_ids = []
        linked_requirements = [
            ScoreLinkedRequirementInput(
                requirement_id=item.requirement_id,
                kind=item.kind.value,
                normalized_requirement=item.normalized_requirement,
                status=item.status,
                severity=item.severity,
                original_text=item.original_text,
                source_input_id=item.source_anchor.source_input_id,
                chunk_id=item.source_anchor.chunk_id,
                location=item.source_anchor.location,
            )
            for requirement_id in linked_requirement_ids
            for item in [requirements_by_id[requirement_id]]
        ]
        groups = [
            DeterministicScoreGroupInput(
                group_id=group.group_id,
                title=group.title,
                source_order=index,
                declared_points=group.declared_points,
            )
            for index, group in enumerate(score_model.groups)
        ]
        rules = []
        for point_index, point in enumerate(score_model.points):
            normalized_levels = ScoreAgent._scoring_levels(point.criterion)
            normalized_disqualifying = (
                ScoreAgent._is_wholly_disqualifying_criterion(
                    point.criterion,
                    max_points=point.max_points,
                )
            )
            common_criterion = ScoreAgent._common_score_requirements(
                point.criterion
            )
            source_anchor_inputs = [
                ScoreSourceAnchorInput(
                    source_input_id=anchor.source_input_id,
                    chunk_id=anchor.chunk_id,
                    page=anchor.page,
                    location=anchor.location,
                    source_text=ScoreAgent._source_text_for_anchor(
                        source_blocks_by_anchor,
                        anchor,
                    ),
                )
                for anchor in point.source_anchors
            ]
            (
                level_ranges,
                common_range,
            ) = ScoreAgent._semantic_source_ranges(
                point.criterion,
                normalized_levels,
                common_criterion,
                source_anchor_inputs,
            )
            rules.append(
                DeterministicScoreRuleInput(
                    rule_id=point.score_point_id,
                    group_id=point.group_id,
                    source_order=point_index,
                    title=point.title,
                    raw_criterion=point.criterion,
                    common_criterion=common_criterion,
                    common_source_anchor_index=(
                        common_range[0]
                        if common_range is not None
                        else None
                    ),
                    common_source_span_start=(
                        common_range[1]
                        if common_range is not None
                        else None
                    ),
                    common_source_span_end=(
                        common_range[2]
                        if common_range is not None
                        else None
                    ),
                    max_points=point.max_points,
                    disqualifying=normalized_disqualifying,
                    source_hierarchy=point.outline_path,
                    linked_requirement_ids=(
                        projected_linked_requirement_ids_by_point[
                            point.score_point_id
                        ]
                        if requirement_ledger is not None
                        else []
                    ),
                    context_requirement_ids=(
                        context_requirement_ids_by_point[point.score_point_id]
                        if requirement_ledger is not None
                        else []
                    ),
                    levels=[
                        DeterministicScoreLevelInput(
                            level_id=f"{point.score_point_id}-L{level_index:02d}",
                            label=level.label,
                            points=level.points,
                            criterion=level.criterion,
                            source_order=level_index - 1,
                            source_anchor_index=level_ranges[
                                level_index - 1
                            ][0],
                            source_span_start=level_ranges[
                                level_index - 1
                            ][1],
                            source_span_end=level_ranges[
                                level_index - 1
                            ][2],
                        )
                        for level_index, level in enumerate(
                            normalized_levels,
                            start=1,
                        )
                    ],
                    source_anchors=source_anchor_inputs,
                )
            )
        score_rule_ids_by_anchor: dict[tuple[str, str], list[str]] = defaultdict(list)
        for point in score_model.points:
            for anchor in point.source_anchors:
                anchor_key = (anchor.source_input_id, anchor.chunk_id)
                if point.score_point_id not in score_rule_ids_by_anchor[anchor_key]:
                    score_rule_ids_by_anchor[anchor_key].append(point.score_point_id)
        document_map_values: dict[
            tuple[str, str, tuple[str, ...]],
            dict[str, object],
        ] = {}
        for block in source_blocks:
            if block.input_role.value not in {
                "tender",
                "score",
                "amendment",
                "template",
            }:
                continue
            key = (
                block.input_id,
                block.input_role.value,
                tuple(block.heading_path),
            )
            value = document_map_values.setdefault(
                key,
                {
                    "blocks": [],
                    "content_types": [],
                    "score_rule_ids": [],
                },
            )
            blocks = value["blocks"]
            assert isinstance(blocks, list)
            blocks.append(block)
            content_types = value["content_types"]
            assert isinstance(content_types, list)
            if block.block_kind not in content_types:
                content_types.append(block.block_kind)
            score_rule_ids = value["score_rule_ids"]
            assert isinstance(score_rule_ids, list)
            for rule_id in score_rule_ids_by_anchor.get(
                (
                    block.source_anchor.source_input_id,
                    block.source_anchor.chunk_id,
                ),
                [],
            ):
                if rule_id not in score_rule_ids:
                    score_rule_ids.append(rule_id)
        document_map: list[ScoreDocumentMapEntry] = []
        for (
            input_id,
            input_role,
            heading_path,
        ), value in document_map_values.items():
            blocks = value["blocks"]
            content_types = value["content_types"]
            score_rule_ids = value["score_rule_ids"]
            assert isinstance(blocks, list)
            assert isinstance(content_types, list)
            assert isinstance(score_rule_ids, list)
            ordered_blocks = sorted(
                blocks,
                key=lambda item: (item.ordinal, item.block_id),
            )
            # The global map is topology, not a second SourceIndex.  Keep only
            # the boundary block IDs for traceability instead of repeating every
            # paragraph/table-cell and chunk identifier.
            boundary_block_ids = [ordered_blocks[0].block_id]
            if ordered_blocks[-1].block_id != boundary_block_ids[0]:
                boundary_block_ids.append(ordered_blocks[-1].block_id)
            heading_token = canonical_hash(
                [input_id, input_role, list(heading_path)]
            )[:16]
            document_map.append(
                ScoreDocumentMapEntry(
                    map_id=f"SDM-{heading_token}",
                    heading_id=f"HDG-{heading_token}",
                    source_input_id=input_id,
                    input_role=input_role,
                    heading_path=list(heading_path),
                    title=(
                        heading_path[-1]
                        if heading_path
                        else "文档根节点"
                    ),
                    block_ids=boundary_block_ids,
                    block_count=len(ordered_blocks),
                    content_type=(
                        content_types[0]
                        if len(content_types) == 1
                        else "mixed:" + ",".join(sorted(content_types))
                    ),
                    score_rule_ids=list(score_rule_ids),
                )
            )
        structure = {
            "groups": [item.model_dump(mode="json") for item in groups],
            "rules": [item.model_dump(mode="json") for item in rules],
            "total_points": score_model.total_points,
        }
        return ScoreSemanticInput(
            source_snapshot_hash=canonical_hash(score_model.source_hashes),
            deterministic_structure_hash=canonical_hash(structure),
            total_points=float(
                score_model.total_points
                if score_model.total_points is not None
                else sum(point.max_points or 0 for point in score_model.points)
            ),
            groups=groups,
            rules=rules,
            document_map=document_map,
            linked_requirements=linked_requirements,
        )

    @staticmethod
    def _source_text_for_anchor(
        source_blocks_by_anchor: dict[tuple[str, str], SourceBlock],
        anchor: SourceAnchor,
    ) -> str:
        block = source_blocks_by_anchor.get(
            (anchor.source_input_id, anchor.chunk_id)
        )
        if block is None:
            raise ValueError(
                "确定性评分结构引用了 SourceIndex 中不存在的 source anchor: "
                f"{anchor.source_input_id}/{anchor.chunk_id}"
            )
        if block.source_anchor != anchor:
            raise ValueError(
                "确定性评分结构的 source anchor 与 SourceIndex 冻结来源不一致: "
                f"{anchor.source_input_id}/{anchor.chunk_id}"
            )
        if not block.content:
            raise ValueError(
                "确定性评分结构引用的 SourceBlock 文本为空: "
                f"{anchor.source_input_id}/{anchor.chunk_id}"
            )
        return block.content

    @staticmethod
    def _semantic_source_ranges(
        raw_criterion: str,
        levels: list[ScoringLevel],
        common_criterion: str | None,
        source_anchors: list[ScoreSourceAnchorInput],
    ) -> tuple[
        list[tuple[int, int, int]],
        tuple[int, int, int] | None,
    ]:
        """Freeze score-level/common source spans before any LLM request."""

        raw_matches = [
            (anchor_index, match.start(), match.end())
            for anchor_index, anchor in enumerate(source_anchors)
            for match in re.finditer(
                re.escape(raw_criterion),
                anchor.source_text,
            )
        ]
        if len(raw_matches) != 1:
            raise ValueError(
                "评分原文无法在确定性 SourceBlock 中唯一定位；"
                f"matches={len(raw_matches)}"
            )
        anchor_index, raw_start, _ = raw_matches[0]
        cursor = 0
        level_ranges: list[tuple[int, int, int]] = []
        for level_index, level in enumerate(levels, start=1):
            relative_start = raw_criterion.find(level.criterion, cursor)
            if relative_start < 0:
                raise ValueError(
                    "确定性评分档无法按来源顺序定位到评分原文: "
                    f"level={level_index}, criterion={level.criterion[:160]}"
                )
            relative_end = relative_start + len(level.criterion)
            level_ranges.append(
                (
                    anchor_index,
                    raw_start + relative_start,
                    raw_start + relative_end,
                )
            )
            cursor = relative_end
        common_range: tuple[int, int, int] | None = None
        if common_criterion is not None:
            relative_start = raw_criterion.rfind(common_criterion)
            if relative_start < cursor:
                raise ValueError(
                    "评分档后的共同资格或证明要求无法定位到评分原文"
                )
            relative_end = relative_start + len(common_criterion)
            common_range = (
                anchor_index,
                raw_start + relative_start,
                raw_start + relative_end,
            )
        return level_ranges, common_range

    @staticmethod
    def _context_requirement_ids_for_point(
        point: ScorePoint,
        source_blocks_by_anchor: dict[tuple[str, str], SourceBlock],
        requirement_ledger: RequirementLedger,
        *,
        limit: int = 6,
    ) -> list[str]:
        """Select bounded procurement context without changing score-source links."""

        if is_document_quality_score(point.title, point.criterion):
            return []
        requirements_by_id = {
            item.requirement_id: item
            for item in requirement_ledger.requirements
        }
        point_anchor_keys = {
            (anchor.source_input_id, anchor.chunk_id)
            for anchor in point.source_anchors
        }
        direct_context_ids: set[str] = set()
        for requirement_id in point.context_requirement_ids:
            requirement = requirements_by_id.get(requirement_id)
            if requirement is None:
                raise ValueError(
                    f"评分点 {point.score_point_id} 引用未知 context requirement: "
                    f"{requirement_id}"
                )
            if requirement.status in {"blocked", "waived"}:
                raise ValueError(
                    f"评分点 {point.score_point_id} 引用非活动 context requirement: "
                    f"{requirement_id}"
                )
            requirement_anchor_key = (
                requirement.source_anchor.source_input_id,
                requirement.source_anchor.chunk_id,
            )
            requirement_kind = getattr(
                requirement.kind,
                "value",
                requirement.kind,
            )
            # Historical ledgers frequently classified each scoring band as a
            # ``kind=score`` requirement.  Those fragments are already present
            # in the rule source and must not crowd out procurement requirements.
            if (
                requirement_anchor_key in point_anchor_keys
                or requirement_kind == "score"
            ):
                continue
            direct_context_ids.add(requirement_id)

        point_blocks = [
            source_blocks_by_anchor.get(
                (anchor.source_input_id, anchor.chunk_id)
            )
            for anchor in point.source_anchors
        ]
        point_blocks = [block for block in point_blocks if block is not None]
        generic_headings = {
            "采购需求",
            "技术要求",
            "评分办法",
            "评分标准",
            "评审办法",
            "评标办法",
            "商务部分",
            "技术部分",
            "价格部分",
        }

        def specific_headings(values: list[str]) -> set[str]:
            return {
                compact
                for value in values
                for compact in [re.sub(r"\s+", "", value)]
                if (
                    len(compact) >= 3
                    and compact not in generic_headings
                    and "评标方法" not in compact
                    and "评分标准" not in compact
                    and re.fullmatch(
                        r"包\d+(?:到|至|-)\d+(?:采购需求)?[：:]?",
                        compact,
                    )
                    is None
                )
            }

        point_headings = specific_headings(
            [
                *point.outline_path,
                *(
                    heading
                    for block in point_blocks
                    for heading in block.heading_path
                ),
            ]
        )
        point_text = " ".join(
            (
                point.title,
                point.criterion,
                *point.outline_path,
            )
        )
        compact_rule_reference_text = re.sub(
            r"\s+",
            "",
            f"{point.title} {point.criterion}",
        )
        # A formula-only price point does not need procurement-context retrieval.
        # Price wording has very high accidental overlap with bid forms, deposits,
        # and commercial boilerplate; only a section/clause reference in the
        # scoring rule itself justifies opening that retrieval path.
        price_reference_text = re.sub(
            r"\s+",
            "",
            f"{point.title} {point.criterion}",
        )
        is_price_point = any(
            signal in price_reference_text
            for signal in (
                "投标报价",
                "报价得分",
                "价格得分",
                "价格分",
                "评标基准价",
                "最低评标价",
            )
        )
        has_explicit_price_reference = bool(
            re.search(
                r"第[一二三四五六七八九十百\d.]+(?:章|条)"
                r"|(?<!\d)\d+(?:\.\d+)+条",
                price_reference_text,
            )
            or _EXPLICIT_TABLE_REFERENCE.search(price_reference_text)
        )
        if is_price_point and not has_explicit_price_reference:
            return []

        ranked: list[tuple[int, float, int, str]] = []
        direct_ids = set(point.linked_requirement_ids)
        for ledger_order, requirement in enumerate(
            requirement_ledger.requirements
        ):
            requirement_id = requirement.requirement_id
            if (
                requirement_id in direct_ids
                or requirement.status in {"blocked", "waived"}
            ):
                continue
            requirement_anchor_key = (
                requirement.source_anchor.source_input_id,
                requirement.source_anchor.chunk_id,
            )
            requirement_kind = getattr(
                requirement.kind,
                "value",
                requirement.kind,
            )
            if (
                requirement_anchor_key in point_anchor_keys
                or requirement_kind == "score"
            ):
                continue
            requirement_block = source_blocks_by_anchor.get(
                requirement_anchor_key
            )
            if (
                requirement_block is not None
                and requirement_block.input_role.value
                not in {"tender", "amendment"}
            ):
                continue
            raw_requirement_headings = list(
                requirement_block.heading_path
                if requirement_block is not None
                else []
            )
            requirement_headings = specific_headings(
                raw_requirement_headings
            )
            compact_requirement_path = "".join(
                re.sub(r"\s+", "", heading)
                for heading in raw_requirement_headings
            )
            procurement_context = (
                any(
                    signal in compact_requirement_path
                    for signal in (
                        "采购需求",
                        "具体服务要求",
                        "服务要求",
                        "项目概况",
                        "项目主要任务",
                        "工作路线与方法",
                        "工作内容",
                    )
                )
            )
            clause_refs = [
                value
                for value in (
                    requirement.clause_id,
                    requirement.parent_clause_id,
                )
                if value and len(re.sub(r"\s+", "", value)) >= 2
            ]
            explicit_clause_reference = False
            for clause_ref in clause_refs:
                compact_clause_ref = re.sub(r"\s+", "", clause_ref)
                if re.fullmatch(r"\d+(?:\.\d+)*", compact_clause_ref):
                    if re.search(
                        rf"(?:第)?{re.escape(compact_clause_ref)}"
                        r"(?:章|条|款|项|节)",
                        compact_rule_reference_text,
                    ):
                        explicit_clause_reference = True
                        break
                elif compact_clause_ref in compact_rule_reference_text:
                    explicit_clause_reference = True
                    break
            explicit_heading_reference = any(
                chapter_match.group(0) in compact_rule_reference_text
                for heading in requirement_headings
                for chapter_match in [
                    re.search(
                        r"第[一二三四五六七八九十百\d.]+(?:章|条)",
                        heading,
                    )
                ]
                if chapter_match is not None
            )
            rule_table_references = {
                re.sub(r"\s+", "", match.group(0))
                for match in _EXPLICIT_TABLE_REFERENCE.finditer(
                    compact_rule_reference_text
                )
            }
            requirement_table_references = {
                re.sub(r"\s+", "", match.group(0))
                for heading in raw_requirement_headings
                for match in _EXPLICIT_TABLE_REFERENCE.finditer(heading)
            }
            explicit_table_reference = bool(
                rule_table_references & requirement_table_references
            )
            similarity = ScoreAgent._context_requirement_similarity(
                point_text,
                requirement.normalized_requirement,
                point_title=point.title,
            )
            excluded_context_heading = any(
                signal in compact_requirement_path
                for signal in (
                    "评标方法",
                    "评分标准",
                    "评分办法",
                    "符合性审查",
                    "资格审查",
                    "投标人须知",
                )
            )
            compact_requirement_text = re.sub(
                r"\s+",
                "",
                " ".join(
                    filter(
                        None,
                        (
                            requirement.original_text,
                            requirement.normalized_requirement,
                        ),
                    )
                ),
            )
            task_specific_contract_section = any(
                signal in compact_requirement_path
                for signal in (
                    "具体服务要求",
                    "工作路线与方法",
                    "工作内容",
                    "工作目标",
                    "项目主要任务",
                    "技术要求",
                    "成果要求",
                    "验收要求",
                )
            )
            generic_contract_noise = (
                any(
                    signal in compact_requirement_text
                    for signal in (
                        "履约保证金",
                        "投标保证金",
                        "保证金",
                        "中标服务费",
                        "合同价款",
                        "合同履行",
                        "经费使用",
                        "违约责任",
                        "赔偿责任",
                        "索赔",
                        "安全保密",
                        "保密协议",
                        "失泄密",
                        "商品包装",
                        "快递包装",
                        "企业划型",
                        "残疾人福利性单位",
                        "监狱企业",
                        "平台注册",
                        "购标",
                        "法定代表人授权书",
                        "身份证明书",
                    )
                )
                or (
                    (
                        requirement_kind == "contract"
                        or "合同条款" in compact_requirement_path
                    )
                    and not task_specific_contract_section
                )
            )
            if (
                requirement_id not in direct_context_ids
                and not explicit_clause_reference
                and not explicit_heading_reference
                and not explicit_table_reference
                and excluded_context_heading
            ):
                continue
            if (
                requirement_id not in direct_context_ids
                and not explicit_clause_reference
                and not explicit_heading_reference
                and not explicit_table_reference
                and generic_contract_noise
            ):
                continue
            if (
                requirement_id not in direct_context_ids
                and not procurement_context
                and not explicit_clause_reference
                and not explicit_heading_reference
                and not explicit_table_reference
                and similarity <= 0
            ):
                continue
            same_heading = bool(
                point_headings & requirement_headings
                or any(
                    left in right or right in left
                    for left in point_headings
                    for right in requirement_headings
                    if min(len(left), len(right)) >= 4
                )
            )
            has_strong_binding = bool(
                requirement_id in direct_context_ids
                or explicit_clause_reference
                or explicit_heading_reference
                or explicit_table_reference
                or same_heading
            )
            if (
                not has_strong_binding
                and (
                    similarity < 20.0
                    or not ScoreAgent._context_domain_overlap(
                        point.title,
                        point.criterion,
                        requirement.normalized_requirement,
                    )
                )
            ):
                continue
            if (
                explicit_clause_reference
                or explicit_heading_reference
                or explicit_table_reference
            ):
                priority = 0
            elif requirement_id in direct_context_ids:
                priority = 1
            elif same_heading:
                priority = 2
            elif similarity > 0:
                priority = 3
            else:
                continue
            ranked.append(
                (priority, -similarity, ledger_order, requirement_id)
            )
        selected: list[str] = []
        for _, _, _, requirement_id in sorted(ranked):
            if requirement_id not in selected:
                selected.append(requirement_id)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _context_domain_overlap(
        point_title: str,
        point_criterion: str,
        requirement_text: str,
    ) -> bool:
        """Reject lexical overlap that belongs to a different response domain."""

        point_text = re.sub(r"\s+", "", f"{point_title}{point_criterion}")
        requirement = re.sub(r"\s+", "", requirement_text)
        if "业绩" in point_title:
            return any(
                signal in requirement
                for signal in ("业绩", "项目合同", "任务书", "验收", "项目经验")
            )
        if "资质" in point_title:
            return any(
                signal in requirement
                for signal in (
                    "测绘",
                    "摄影测量",
                    "地理信息系统",
                    "遥感",
                    "资质证书",
                )
            )
        if any(
            signal in point_text
            for signal in ("驻场人员", "技术负责人", "人员结构", "职称")
        ):
            return any(
                signal in requirement
                for signal in (
                    "驻场",
                    "负责人",
                    "职称",
                    "社保",
                    "劳动合同",
                    "工作年限",
                    "项目经验",
                    "选派",
                )
            )
        return True

    @staticmethod
    def apply_semantic_candidate(
        structural_model: ScoreModel,
        candidate: ScoreSemanticCandidate,
    ) -> ScoreModel:
        """Compile model semantics without allowing it to alter source points or arithmetic."""

        interpretations = {item.rule_id: item for item in candidate.interpretations}
        if set(interpretations) != {
            point.score_point_id for point in structural_model.points
        }:
            raise ValueError("评分语义候选未完整覆盖确定性评分规则")
        compiled_points: list[ScorePoint] = []
        group_titles = {
            group.group_id: group.title for group in structural_model.groups
        }
        for point in structural_model.points:
            normalized_levels = ScoreAgent._scoring_levels(point.criterion)
            normalized_disqualifying = (
                ScoreAgent._is_wholly_disqualifying_criterion(
                    point.criterion,
                    max_points=point.max_points,
                )
            )
            interpretation = interpretations[point.score_point_id]
            condition_key_to_id: dict[str, str] = {}
            conditions: list[ScoreCondition] = []
            for unit in interpretation.units:
                for condition in unit.full_score_conditions:
                    if (
                        condition.source_anchor_index is None
                        or condition.source_span_start is None
                        or condition.source_span_end is None
                    ):
                        raise ValueError(
                            f"满分条件 {condition.condition_key} 尚未完成确定性来源定位"
                        )
                    if condition.source_anchor_index >= len(point.source_anchors):
                        raise ValueError(
                            f"满分条件 {condition.condition_key} 的 source_anchor_index "
                            f"超出评分点 {point.score_point_id} 的来源范围"
                        )
                    source_anchor = point.source_anchors[
                        condition.source_anchor_index
                    ]
                    condition_token = canonical_hash(
                        {
                            "score_point_id": point.score_point_id,
                            "source_level_id": condition.source_level_id,
                            "source_anchor": source_anchor.model_dump(
                                mode="json"
                            ),
                            "source_span_start": condition.source_span_start,
                            "source_span_end": condition.source_span_end,
                            "source_excerpt": "".join(
                                condition.source_excerpt.split()
                            ),
                        }
                    )[:12]
                    condition_id = (
                        f"{point.score_point_id}-C-{condition_token}"
                    )
                    if condition_id in condition_key_to_id.values():
                        raise ValueError(
                            f"评分语义候选为 {point.score_point_id} 重复声明同一来源满分条件"
                        )
                    condition_key_to_id[condition.condition_key] = condition_id
                    conditions.append(
                        ScoreCondition(
                            condition_id=condition_id,
                            text=condition.text,
                            normalized_condition=condition.normalized_condition,
                            condition_role=condition.condition_role,
                            source_excerpt=condition.source_excerpt,
                            source_level_id=condition.source_level_id,
                            subject=condition.semantic_subject,
                            response_intent=condition.response_intent,
                            source_anchor=source_anchor,
                            source_span_start=condition.source_span_start,
                            source_span_end=condition.source_span_end,
                            confidence=condition.confidence,
                            review_status=(
                                "needs_review"
                                if unit.review_status == "needs_human"
                                else "confirmed"
                            ),
                        )
                    )
            units: list[ScoreResponseUnit] = []
            context_requirement_ids = list(
                dict.fromkeys(
                    (
                        *point.context_requirement_ids,
                        *interpretation.context_requirement_ids,
                    )
                )
            )
            allowed_unit_requirement_ids = list(
                dict.fromkeys(
                    (
                        *point.linked_requirement_ids,
                        *context_requirement_ids,
                    )
                )
            )
            selected_unit_requirement_ids: set[str] = set()
            for unit_index, unit in enumerate(interpretation.units, start=1):
                unit_title = unit.title
                if is_evaluative_sentence_heading(unit_title):
                    unit_title = full_score_condition_heading(unit_title, 1)
                unit_requirement_ids = list(unit.linked_requirement_ids)
                if unknown_requirement_ids := (
                    set(unit_requirement_ids)
                    - set(allowed_unit_requirement_ids)
                ):
                    raise ValueError(
                        f"独立得分单元 {unit.unit_key} 引用了评分规则未提供的 "
                        f"requirement_id: {sorted(unknown_requirement_ids)}"
                    )
                selected_unit_requirement_ids.update(unit_requirement_ids)
                unit_evidence_types = list(unit.required_evidence_types)
                for condition in unit.full_score_conditions:
                    for evidence_type in condition.required_evidence_types:
                        if evidence_type not in unit_evidence_types:
                            unit_evidence_types.append(evidence_type)
                units.append(
                    ScoreResponseUnit(
                        unit_id=f"{point.score_point_id}-U{unit_index:02d}",
                        title=unit_title,
                        outline_path=ScoreAgent._canonical_semantic_outline_path(
                            structural_path=point.outline_path,
                            semantic_path=unit.outline_path,
                            group_title=group_titles.get(point.group_id, ""),
                        ),
                        source_level_ids=[
                            item.level_id for item in unit.band_semantics
                        ],
                        condition_ids=[
                            condition_key_to_id[condition.condition_key]
                            for condition in unit.full_score_conditions
                        ],
                        condition_join=unit.condition_join,
                        linked_requirement_ids=unit_requirement_ids,
                        response_scope=unit.response_scope,
                        response_expectation=unit.response_expectation,
                        required_evidence_types=unit_evidence_types,
                        confidence=unit.confidence,
                        review_status=(
                            "needs_review"
                            if unit.review_status == "needs_human"
                            else "confirmed"
                        ),
                    )
                )
            selected_context_requirement_ids = [
                requirement_id
                for requirement_id in context_requirement_ids
                if requirement_id in selected_unit_requirement_ids
            ]
            evidence_types = list(point.required_evidence_types)
            for unit in units:
                for evidence_type in unit.required_evidence_types:
                    if evidence_type not in evidence_types:
                        evidence_types.append(evidence_type)
            single_unit = interpretation.units[0] if len(interpretation.units) == 1 else None
            compiled_points.append(
                point.model_copy(
                    update={
                        # Table-derived titles and hierarchy remain authoritative.
                        # Semantic inference may enrich response units and
                        # conditions, but it must never rewrite the directory.
                        "title": point.title,
                        "outline_path": point.outline_path,
                        "response_scope": (
                            "document"
                            if interpretation.units
                            and all(
                                unit.response_scope == "document"
                                for unit in interpretation.units
                            )
                            else "section"
                        ),
                        "full_score_conditions": [item.text for item in conditions],
                        "score_conditions": conditions,
                        "response_units": units,
                        "scoring_levels": normalized_levels,
                        "disqualifying": normalized_disqualifying,
                        "response_depth": ScoreAgent._response_depth(
                            point.max_points,
                            normalized_disqualifying,
                        ),
                        "response_expectation": (
                            single_unit.response_expectation
                            if single_unit is not None
                            else interpretation.shared_context
                        ),
                        "required_evidence_types": evidence_types,
                        "context_requirement_ids": (
                            selected_context_requirement_ids
                        ),
                        "confidence": min(
                            point.confidence,
                            interpretation.confidence,
                            *(unit.confidence for unit in interpretation.units),
                        ),
                        "review_status": (
                            "needs_review"
                            if interpretation.review_status == "needs_human"
                            or any(unit.review_status == "needs_human" for unit in interpretation.units)
                            else point.review_status
                        ),
                    }
                )
            )
        return structural_model.model_copy(update={"points": compiled_points})

    @staticmethod
    def _canonical_semantic_outline_path(
        *,
        structural_path: list[str],
        semantic_path: list[str],
        group_title: str,
    ) -> list[str]:
        """Return only the deterministic scoring-table hierarchy."""

        def cleaned(values: list[str]) -> list[str]:
            return [
                text
                for value in values
                if (text := re.sub(r"\s+", " ", str(value)).strip())
            ]

        source = cleaned(structural_path)
        group_key = outline_structure_key(group_title)
        while (
            source
            and group_key
            and outline_structure_key(source[0]) == group_key
        ):
            source.pop(0)
        return source

    def create_score_model_proposal(
        self,
        score_model: ScoreModel,
        *,
        base_revision: int,
        operation_id: str,
        requirement_revision: int,
        prompt_version: str | None = None,
        model_fingerprint: str | None = None,
        inference_receipt_refs: list[InferenceReceiptRef] | None = None,
    ) -> ProposalEnvelope:
        cited_source_ids = score_model.source_input_ids
        prompt_version = prompt_version or "v3_score_agent_v1.3"
        model_fingerprint = model_fingerprint or (
            f"deterministic_score_agent_row_v3:{SCORING_OUTLINE_POLICY_VERSION}"
        )
        store = ControlStore(self.context)
        resolved: dict = {}
        declared: list[DependencyRef] = []
        for kind in ("RequirementLedger", "SourceIndex"):
            active = store.v3_active_artifact(kind)
            if active is None:
                continue
            resolved[kind] = {
                "artifact_kind": kind,
                "artifact_id": str(active["artifact_id"]),
                "revision": int(active["revision"]),
                "artifact_hash": str(active["artifact_hash"]),
            }
            declared.append(
                DependencyRef(
                    artifact_kind=kind,
                    expected_revision=int(active["revision"]),
                    expected_hash=str(active["artifact_hash"]),
                )
            )
        dep_fp = build_declared_dependency_fingerprint(
            resolved_dependency_snapshot=resolved,
            artifact_kind="ScoreModel",
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
        )
        return ProposalEnvelope(
            workspace_id=self.context.workspace_id,
            artifact_kind="ScoreModel",
            producer_role="score_agent",
            operation_id=operation_id,
            base_revision=base_revision,
            declared_dependencies=declared,
            dependency_fingerprint=dep_fp,
            payload=score_model.model_dump(mode="json"),
            cited_source_ids=cited_source_ids,
            prompt_version=prompt_version,
            model_fingerprint=model_fingerprint,
            inference_receipt_refs=inference_receipt_refs or [],
        )

    @staticmethod
    def _is_scoring_block(block: SourceBlock) -> bool:
        return is_scoring_source_block(block)

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
        text = content.strip()
        if not text or text in _SCORE_METADATA:
            return []
        if ScoreAgent._is_total_only_line(text):
            return []
        # One frozen paragraph/cell is one logical scoring rule.  Semicolon
        # fragments are usually performance bands and must become ScoringLevel,
        # never independent ScorePoints.
        if _TOTAL_POINTS.search(text) and not (
            _EXPLICIT_MAX_POINTS.search(text) or _AWARDED_POINTS.search(text)
        ):
            return []
        if _EXPLICIT_MAX_POINTS.search(text) or _AWARDED_POINTS.search(text):
            return [text]
        if _LEVEL_LABEL.search(text) and _POINTS.search(text):
            return [text]
        return []

    @staticmethod
    def _is_total_only_line(text: str) -> bool:
        stripped = text.strip().rstrip("。.")
        if re.fullmatch(r"[（(]?\s*\d+(?:\.\d+)?\s*分\s*[）)]?", stripped):
            return True
        if _TOTAL_POINTS.fullmatch(stripped):
            return True
        if re.fullmatch(
            r"(?:(?:采用)?(?:综合评分法|评分办法|评审办法)[，,:：\s]*)?"
            r"(?:总分|满分合计|合计)\D{0,8}\d+(?:\.\d+)?\s*分",
            stripped,
        ):
            return True
        return bool(re.fullmatch(r"(?:总分|满分合计|合计)?\D{0,8}\d+(?:\.\d+)?\s*分", stripped))

    @staticmethod
    def _is_group_label(text: str) -> bool:
        return bool(_GROUP_LABEL.fullmatch(text.strip().rstrip("。.")))

    @staticmethod
    def _reconcile_groups(groups: list[ScoreGroup], points: list[ScorePoint]) -> list[ScoreGroup]:
        """Drop empty/duplicate groups without rewriting source-declared subtotals."""
        referenced = {point.group_id for point in points}
        reconciled: list[ScoreGroup] = []
        seen: set[str] = set()
        for group in groups:
            if group.group_id not in referenced or group.group_id in seen:
                continue
            seen.add(group.group_id)
            reconciled.append(group)
        return reconciled

    @staticmethod
    def _canonical_row_blocks(row_blocks: list[SourceBlock]) -> list[SourceBlock]:
        """Collapse DOCX gridSpan/vMerge duplicates to one physical-content anchor."""

        result: list[SourceBlock] = []
        seen_content: set[str] = set()
        for block in sorted(row_blocks, key=lambda item: (item.column_index or 0, item.ordinal, item.block_id)):
            normalized = re.sub(r"\s+", "", block.content)
            if not normalized or normalized in seen_content:
                continue
            seen_content.add(normalized)
            result.append(block)
        return result

    def _point_from_table_row(
        self,
        row_blocks: list[SourceBlock],
        group: ScoreGroup,
        requirements_by_anchor: dict[tuple[str, str], list[_RequirementCandidate]],
    ) -> ScorePoint | None:
        criterion_block = self._criterion_block(row_blocks)
        if criterion_block is None:
            return None
        criterion = criterion_block.content.strip()
        row_text = "\n".join(block.content for block in row_blocks)
        if not _POINTS.search(row_text) and not (
            _EXPLICIT_MAX_POINTS.search(criterion) or _AWARDED_POINTS.search(criterion)
        ):
            return None

        title_block = self._row_title_block(row_blocks, criterion_block)
        title = self._row_title(title_block.content if title_block is not None else criterion)
        max_points = self._row_max_points(
            criterion,
            title_block.content if title_block is not None else "",
        )
        disqualifying = self._is_wholly_disqualifying_criterion(
            criterion,
            max_points=max_points,
        )
        row_candidates = [
            candidate
            for block in row_blocks
            for candidate in requirements_by_anchor.get(
                (block.source_anchor.source_input_id, block.source_anchor.chunk_id),
                [],
            )
        ]
        exact_candidates = requirements_by_anchor.get(
            (
                criterion_block.source_anchor.source_input_id,
                criterion_block.source_anchor.chunk_id,
            ),
            [],
        )
        linked = self._best_requirement_link(criterion, exact_candidates, minimum_similarity=0.72)
        if linked is None:
            linked = self._best_requirement_link(criterion, row_candidates, minimum_similarity=0.82)

        anchors: list[SourceAnchor] = []
        seen_anchors: set[tuple[str, str]] = set()
        for block in row_blocks:
            anchor = block.source_anchor
            key = (anchor.source_input_id, anchor.chunk_id)
            if key not in seen_anchors:
                seen_anchors.add(key)
                anchors.append(anchor)
        if linked is not None:
            key = (linked.source_anchor.source_input_id, linked.source_anchor.chunk_id)
            if key not in seen_anchors:
                anchors.append(linked.source_anchor)

        first = min(row_blocks, key=lambda item: (item.column_index or 0, item.ordinal))
        token = hashlib.sha256(
            f"{first.input_id}:{first.table_index}:{first.row_index}:{criterion}".encode("utf-8")
        ).hexdigest()[:12]
        evidence_types = self._evidence_types(f"{title}\n{criterion}")
        levels = self._scoring_levels(criterion)
        return ScorePoint(
            score_point_id=f"SP-{token}",
            group_id=group.group_id,
            title=title,
            criterion=criterion,
            max_points=max_points,
            scoring_levels=levels,
            disqualifying=disqualifying,
            response_scope="document" if is_document_quality_score(title, criterion) else "section",
            outline_path=self._row_outline_path(
                row_blocks,
                criterion_block,
                title,
                group_title=group.title,
            ),
            full_score_conditions=highest_score_conditions(criterion, levels, max_points),
            response_expectation=self._response_expectation(max_points, disqualifying),
            response_depth=self._response_depth(max_points, disqualifying),
            required_evidence_types=evidence_types,
            linked_requirement_ids=[linked.requirement_id] if linked is not None else [],
            source_anchors=anchors,
            confidence=1.0 if max_points is not None else 0.8,
            review_status="confirmed" if max_points is not None else "needs_review",
        )

    @staticmethod
    def _criterion_block(row_blocks: list[SourceBlock]) -> SourceBlock | None:
        candidates = [
            block
            for block in row_blocks
            if not _SCORING_TABLE_HEADER.fullmatch(block.content.strip())
            and not ScoreAgent._is_total_only_line(block.content)
        ]
        if not candidates:
            return None

        def rank(block: SourceBlock) -> tuple[int, int, int, int]:
            content = block.content.strip()
            return (
                len(_AWARDED_POINTS.findall(content)) + len(_EXPLICIT_MAX_POINTS.findall(content)),
                len(re.sub(r"\s+", "", content)),
                block.column_index or 0,
                -block.ordinal,
            )

        return max(candidates, key=rank)

    @staticmethod
    def _row_title_block(
        row_blocks: list[SourceBlock],
        criterion_block: SourceBlock,
    ) -> SourceBlock | None:
        candidates = [
            block
            for block in row_blocks
            if block.block_id != criterion_block.block_id
            and not _SCORING_TABLE_HEADER.fullmatch(block.content.strip())
            and not ScoreAgent._is_total_only_line(block.content)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.column_index or 0, item.ordinal))

    @staticmethod
    def _row_outline_path(
        row_blocks: list[SourceBlock],
        criterion_block: SourceBlock,
        fallback_title: str,
        *,
        group_title: str = "",
    ) -> list[str]:
        """Preserve scoring-factor hierarchy instead of flattening every row."""

        path: list[str] = []
        for block in sorted(row_blocks, key=lambda item: (item.column_index or 0, item.ordinal)):
            if block.block_id == criterion_block.block_id:
                continue
            content = block.content.strip()
            if (
                not content
                or _SCORING_TABLE_HEADER.fullmatch(content)
                or ScoreAgent._is_total_only_line(content)
            ):
                continue
            label = re.sub(r"\s+", " ", content).strip(" /-—")
            label = re.sub(r"\s+(?=[（(])", "", label)
            if label and label not in path:
                path.append(label[:80])
        if not path:
            path.append(fallback_title)
        if (
            path
            and group_title
            and outline_structure_key(path[0])
            == outline_structure_key(group_title)
        ):
            path.pop(0)
        return path

    @staticmethod
    def _row_title(text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        value = re.sub(r"[（(][^（）()]{0,40}\d+(?:\.\d+)?\s*分[）)]", "", value).strip(" /-—")
        value = re.sub(r"\d+(?:\.\d+)?\s*分$", "", value).strip(" /-—")
        return value[:80] or "未命名评分项"

    @staticmethod
    def _row_max_points(criterion: str, title_text: str) -> float | None:
        explicit = [float(value) for value in _EXPLICIT_MAX_POINTS.findall(criterion)]
        awarded = [float(value) for value in _AWARDED_POINTS.findall(criterion)]
        # A long row may contain a subcomponent's ``本项最高3分`` while the
        # row-level first rule is ``得4分``.  The row maximum is therefore the
        # largest explicit cap or awarded band, not the first number encountered.
        if explicit or awarded:
            return max([*explicit, *awarded])
        title_points = [float(value) for value in _POINTS.findall(title_text)]
        return max(title_points) if title_points else None

    def _point_from_criterion(
        self,
        block: SourceBlock,
        criterion: str,
        ordinal: int,
        group_id: str,
        requirements_by_anchor: dict[tuple[str, str], list[_RequirementCandidate]],
    ) -> ScorePoint:
        token = hashlib.sha256(f"{block.input_id}:{block.block_id}:{ordinal}:{criterion}".encode("utf-8")).hexdigest()[:12]
        max_points = self._first_points(criterion)
        levels = self._scoring_levels(criterion)
        title = self._title(criterion)
        disqualifying = self._is_wholly_disqualifying_criterion(
            criterion,
            max_points=max_points,
        )
        evidence_types = self._evidence_types(criterion)
        anchor = block.source_anchor
        linked = self._best_requirement_link(
            criterion,
            requirements_by_anchor.get((anchor.source_input_id, anchor.chunk_id), []),
            minimum_similarity=0.72,
        )
        return ScorePoint(
            score_point_id=f"SP-{token}",
            group_id=group_id,
            title=title,
            criterion=criterion,
            max_points=max_points,
            scoring_levels=levels,
            disqualifying=disqualifying,
            response_scope="document" if is_document_quality_score(title, criterion) else "section",
            outline_path=[title],
            full_score_conditions=highest_score_conditions(criterion, levels, max_points),
            response_expectation=self._response_expectation(max_points, disqualifying),
            response_depth=self._response_depth(max_points, disqualifying),
            required_evidence_types=evidence_types,
            linked_requirement_ids=[linked.requirement_id] if linked is not None else [],
            source_anchors=[anchor],
            confidence=1.0 if max_points is not None else 0.8,
            review_status="confirmed" if max_points is not None or disqualifying else "needs_review",
        )

    @staticmethod
    def _first_points(text: str) -> float | None:
        match = _POINTS.search(text)
        return float(match.group(1)) if match else None

    @staticmethod
    def _is_wholly_disqualifying_criterion(
        criterion: str,
        *,
        max_points: float | None,
    ) -> bool:
        """Only classify a pure veto rule as globally disqualifying.

        A scored rule may contain a clause-level rejection consequence (for
        example an abnormal-low-price review note).  Marking the entire score
        point as disqualifying would incorrectly bypass its full-score semantic
        conditions, so any rule with a positive deterministic score remains a
        normal scoring point.
        """

        if max_points is not None and max_points > 0:
            return False
        return any(
            signal in criterion
            for signal in (
                "废标",
                "否决",
                "不合格",
                "无效投标",
                "资格性审查",
            )
        )

    @staticmethod
    def _score_award_events(
        criterion: str,
    ) -> list[tuple[re.Match[str], float]]:
        """Return source-ordered primary score awards, excluding caps."""

        cap_spans = [
            (match.start(), match.end())
            for match in _EXPLICIT_MAX_POINTS.finditer(criterion)
        ]
        events: list[tuple[re.Match[str], float]] = []
        for match in _AWARDED_POINTS.finditer(criterion):
            if any(
                cap_start <= match.start() and match.end() <= cap_end
                for cap_start, cap_end in cap_spans
            ):
                continue
            events.append((match, float(match.group(1))))
        events.extend(
            (match, 0.0)
            for match in _ZERO_AWARD.finditer(criterion)
        )
        return sorted(events, key=lambda item: (item[0].start(), item[0].end()))

    @staticmethod
    def _scoring_levels(criterion: str) -> list[ScoringLevel]:
        levels: list[ScoringLevel] = []
        awarded = ScoreAgent._score_award_events(criterion)
        previous_end = 0
        for match, points in awarded:
            detail = ScoreAgent._strip_leading_score_mechanics(
                criterion[previous_end : match.end()]
            ).strip(" \t\r\n；;。.")
            previous_end = match.end()
            if not detail:
                detail = match.group(0)
            levels.append(
                ScoringLevel(
                    label=f"{points:g}分档",
                    points=points,
                    criterion=detail,
                )
            )
        if awarded:
            ScoreAgent._validate_award_level_partition(criterion, levels)
            return levels

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
    def _strip_leading_score_mechanics(value: str) -> str:
        """Remove carry-over caps independent of line-break formatting."""

        remainder = value
        while match := _LEADING_SCORE_MECHANICS_CLAUSE.match(remainder):
            if match.end() == 0:
                break
            remainder = remainder[match.end() :]
        return remainder.lstrip(" \t\r\n，,；;。.")

    @staticmethod
    def _is_score_mechanics_line(value: str) -> bool:
        return not ScoreAgent._strip_leading_score_mechanics(value).strip()

    @staticmethod
    def _validate_award_level_partition(
        criterion: str,
        levels: list[ScoringLevel],
    ) -> None:
        """Fail before inference when score awards were merged or dropped."""

        expected = ScoreAgent._score_award_events(criterion)
        if len(expected) != len(levels):
            raise ValueError(
                "确定性评分档切分不完整: "
                f"awards={len(expected)}, levels={len(levels)}"
            )
        for index, level in enumerate(levels, start=1):
            events = ScoreAgent._score_award_events(level.criterion)
            if len(events) != 1:
                raise ValueError(
                    "确定性评分档必须且只能包含一个主计分语句: "
                    f"level={index}, awards={len(events)}, "
                    f"criterion={level.criterion[:160]}"
                )
            if not math.isclose(events[0][1], float(level.points or 0.0)):
                raise ValueError(
                    "确定性评分档分值与主计分语句不一致: "
                    f"level={index}, parsed={events[0][1]}, "
                    f"level_points={level.points}"
                )

    @staticmethod
    def _common_score_requirements(criterion: str) -> str | None:
        """Return substantive requirements placed after the final score band."""

        awarded = ScoreAgent._score_award_events(criterion)
        if awarded:
            tail = criterion[awarded[-1][0].end() :]
        else:
            note = re.search(
                r"(?:^|[\r\n。；;])\s*(?:说明|备注|注)\s*[：:]",
                criterion,
            )
            if note is None:
                return None
            tail = criterion[note.start() :].lstrip("\r\n。；;")
        tail = ScoreAgent._strip_leading_score_mechanics(tail)
        tail = re.sub(
            r"^\s*(?:(?:说明|备注|注)|关于.{1,60}的规定)\s*[：:]?\s*",
            "",
            tail,
            count=1,
        )
        common = tail.strip(" \t\r\n，,；;。.")
        return common or None

    @staticmethod
    def _title(criterion: str) -> str:
        value = re.sub(r"^\s*(?:\d+[\.、])?", "", criterion)
        value = re.split(r"[：:（(]", value, maxsplit=1)[0].strip()
        return value[:80] or criterion[:80]

    @staticmethod
    def _same_requirement(criterion: str, requirement_text: str) -> bool:
        return ScoreAgent._requirement_similarity(criterion, requirement_text) >= 0.8

    @staticmethod
    def _requirement_similarity(criterion: str, requirement_text: str) -> float:
        normalize = lambda value: re.sub(r"[\s，,。；;：:（）()\-—•/]", "", value)
        left, right = normalize(criterion), normalize(requirement_text)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        shorter, longer = sorted((left, right), key=len)
        if shorter in longer:
            return 0.9 + 0.1 * (len(shorter) / len(longer))
        left_tokens = {left[index : index + 2] for index in range(max(len(left) - 1, 1))}
        right_tokens = {right[index : index + 2] for index in range(max(len(right) - 1, 1))}
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))

    @staticmethod
    def _context_requirement_similarity(
        point_text: str,
        requirement_text: str,
        *,
        point_title: str = "",
    ) -> float:
        """Rank procurement context by meaningful task terms, not adjectives.

        The generic bigram score used for same-row requirement binding strongly
        rewards short phrases such as “满足完整性要求”.  For cross-chapter
        retrieval that behavior fills Top-K with qualification/compliance
        fragments.  Require at least one shared task-bearing bigram first.
        """

        normalize = lambda value: re.sub(
            r"[\s，,。；;：:（）()【】\[\]\-—•/《》“”\"'、]",
            "",
            value,
        )
        left = normalize(point_text)
        right = normalize(requirement_text)
        if len(left) < 2 or len(right) < 2:
            return 0.0
        left_bigrams = {
            left[index : index + 2]
            for index in range(len(left) - 1)
        }
        right_bigrams = {
            right[index : index + 2]
            for index in range(len(right) - 1)
        }
        generic_bigrams = {
            "项目",
            "工作",
            "要求",
            "内容",
            "具体",
            "明确",
            "合理",
            "可行",
            "完整",
            "相关",
            "投标",
            "采购",
            "提供",
            "满足",
            "符合",
            "进行",
            "根据",
            "应当",
            "需要",
            "以及",
            "能够",
        }
        meaningful_overlap = (
            left_bigrams & right_bigrams
        ) - generic_bigrams
        normalized_title = normalize(point_title)
        title_bigrams = {
            normalized_title[index : index + 2]
            for index in range(max(len(normalized_title) - 1, 0))
        }
        # Titles such as “目标任务” and “技术路线” are the strongest query
        # terms.  Match them against the leading clause label, where procurement
        # headings/tasks are stated, rather than a late incidental word.
        leading_requirement = right[:80]
        title_overlap = {
            term
            for term in title_bigrams
            if term and term in leading_requirement
        }
        if not title_overlap and len(meaningful_overlap) < 2:
            return 0.0
        base_similarity = ScoreAgent._requirement_similarity(
            point_text,
            requirement_text,
        )
        # Count shared task terms directly so a long, substantive procurement
        # clause is not ranked below a short boilerplate sentence.
        return (
            (len(title_overlap) * 10.0)
            + float(len(meaningful_overlap))
            + base_similarity
        )

    @staticmethod
    def _best_requirement_link(
        criterion: str,
        candidates: list[_RequirementCandidate],
        *,
        minimum_similarity: float,
    ) -> _RequirementCandidate | None:
        """Bind one ScorePoint to one best same-anchor Requirement.

        Requirement extraction may split scoring levels into several statements
        while ScoreAgent intentionally keeps those levels inside one ScorePoint.
        Selecting the most complete matching statement prevents one scoring row
        from being bulk-bound to every level.
        """

        ranked = [
            (
                ScoreAgent._requirement_similarity(criterion, candidate.text),
                len(re.sub(r"\s+", "", candidate.text)),
                candidate.requirement_id,
                candidate,
            )
            for candidate in candidates
        ]
        ranked = [item for item in ranked if item[0] >= minimum_similarity]
        if not ranked:
            return None
        return max(ranked, key=lambda item: (item[0], item[1], item[2]))[3]

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
    def _disambiguate_titles(points: list[ScorePoint]) -> list[ScorePoint]:
        counts: dict[str, int] = defaultdict(int)
        for point in points:
            counts[point.title] += 1
        label_counts: dict[tuple[str, str], int] = defaultdict(int)
        result: list[ScorePoint] = []
        for point in points:
            if counts[point.title] <= 1:
                result.append(point)
                continue
            detail = ScoreAgent._criterion_outline_label(
                point.title,
                point.criterion,
                point.full_score_conditions,
            )
            label_key = (point.title, detail)
            label_counts[label_key] += 1
            if label_counts[label_key] > 1:
                detail = f"{detail}（{label_counts[label_key]}）"
            result.append(point.model_copy(update={"title": f"{point.title}—{detail}"[:80]}))
        return result

    @staticmethod
    def _criterion_outline_label(
        parent_title: str,
        criterion: str,
        full_score_conditions: list[str] | None = None,
    ) -> str:
        """Derive a short semantic leaf label from the highest scoring band.

        Vertically merged scoring factors commonly span several physical rows.
        The factor is the shared parent; copying the first 80 characters of each
        scoring rule produces unusable chapter names.  Prefer the first concrete
        noun phrase that differs from the parent and stop before evaluative
        language such as “描述清楚” or “方法科学”.
        """

        if full_score_conditions:
            parent = re.sub(r"\s+", "", parent_title)
            headings: list[str] = []
            for index, condition in enumerate(full_score_conditions, start=1):
                heading = full_score_condition_heading(condition, index)
                compact = re.sub(r"\s+", "", heading)
                if compact == parent or not compact:
                    continue
                heading = re.sub(r"检查分析$", "分析", heading)
                heading = re.sub(r"分类方法$", "分类", heading)
                if heading not in headings:
                    headings.append(heading)
            if len(headings) >= 2:
                return f"{headings[0]}与{headings[1]}"[:28]
            if headings:
                return headings[0][:28]

        highest_band = re.split(
            r"(?:得|计)\s*\d+(?:\.\d+)?\s*分",
            re.sub(r"\s+", " ", criterion).strip(),
            maxsplit=1,
        )[0]
        highest_band = re.sub(r"^[（(]?\d+(?:\.\d+)?[）).、．]?\s*", "", highest_band)
        parent = re.sub(r"\s+", "", parent_title)
        quality_cue = re.compile(
            r"描述|条理|逻辑|全面|清楚|明确|具体|细致|科学|合理|充分|可行|"
            r"突出|齐全|翔实|规范|准确|丰富|有效"
        )
        for raw_clause in re.split(r"[；;，,。]", highest_band):
            clause = raw_clause.strip(" ：:、-—")
            clause = re.sub(r"^(?:具体)?针对本项目的?", "", clause)
            if not clause or clause.startswith(("能够", "可以", "有利于", "符合", "满足")):
                continue
            candidate = quality_cue.split(clause, maxsplit=1)[0].strip(" 的：:、-—")
            compact = re.sub(r"\s+", "", candidate)
            if compact.startswith(parent):
                compact = compact[len(parent) :].lstrip("的")
            if len(compact) >= 2:
                return compact[:24]
        return "评分要求"

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
