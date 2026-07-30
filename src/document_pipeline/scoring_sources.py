"""Shared classification for source blocks that contain scoring rules."""

from __future__ import annotations

import re
from collections import defaultdict

from .contracts import InputRole, SourceBlock


_SCORING_CONTEXT = re.compile(
    r"评分|评审|评标|打分|得分|分值|满分|计分|评分表|评分办法|评分标准|评分细则"
)
_TENDER_DIRECT_SCORING = re.compile(
    r"评分项|评分标准|评分细则|评审标准|评标办法|综合评分|技术评分|商务评分|价格评分"
)
_SPECIFIC_SCORING_HEADING = re.compile(
    r"评分项|评分标准|评分细则|评分表|技术评分|商务评分|价格评分|服务评分"
)
_POINT_VALUE = re.compile(r"\d+(?:\.\d+)?\s*分(?!钟)")
_SCORING_FACTOR_HEADER = re.compile(r"评分因素|评标因素|评审因素|评分项目|评分项")
_SCORING_STANDARD_HEADER = re.compile(r"评分标准|评审标准|评分细则|分值")
_SCORING_HEADER_ONLY = re.compile(
    r"^(?:序号|评分因素|评标因素|评审因素|评分项目|评分项|评分标准|评审标准|评分细则|分值)$"
)
ScoringTableKey = tuple[str, int | None, int]


def _table_key(block: SourceBlock) -> ScoringTableKey | None:
    if block.table_index is None or block.row_index is None or block.column_index is None:
        return None
    return (block.input_id, block.page, block.table_index)


def scoring_table_headers(source_blocks: list[SourceBlock]) -> dict[ScoringTableKey, int]:
    """Return tables whose frozen header explicitly declares factors and standards.

    A broad heading such as ``评标方法和标准`` can contain qualification,
    conformity and scoring tables.  Only the latter have both a scoring-factor
    header and a scoring-standard/points header.  This structural test prevents
    qualification rows and ordinary evaluation prose from becoming ScorePoints.
    """

    rows_by_table: dict[ScoringTableKey, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for block in source_blocks:
        key = _table_key(block)
        if key is None:
            continue
        rows_by_table[key][int(block.row_index)].append(block.content.strip())

    result: dict[ScoringTableKey, int] = {}
    for key, rows in rows_by_table.items():
        for row_index, contents in sorted(rows.items()):
            row_text = " | ".join(dict.fromkeys(value for value in contents if value))
            if _SCORING_FACTOR_HEADER.search(row_text) and _SCORING_STANDARD_HEADER.search(row_text):
                result[key] = row_index
                break
    return result


def scoring_table_data_block_ids(source_blocks: list[SourceBlock]) -> set[str]:
    """Return non-header cells belonging to structurally identified scoring tables."""

    headers = scoring_table_headers(source_blocks)
    return {
        block.block_id
        for block in source_blocks
        if (key := _table_key(block)) in headers
        and block.row_index is not None
        and int(block.row_index) > headers[key]
    }


def scoring_source_anchor_keys(source_blocks: list[SourceBlock]) -> set[tuple[str, str]]:
    """Return every legal ScorePoint anchor, including structural scoring rows."""

    table_data_ids = scoring_table_data_block_ids(source_blocks)
    return {
        (block.source_anchor.source_input_id, block.source_anchor.chunk_id)
        for block in source_blocks
        if block.block_id in table_data_ids or is_scoring_source_block(block)
    }


def is_scoring_source_block(block: SourceBlock) -> bool:
    """Return whether a frozen block is an admissible scoring source.

    A dedicated score input is scoring by role.  Amendments may carry scoring
    changes inline.  Tender inputs are narrower: a block must either sit below a
    scoring heading, be a scoring heading itself, or contain a direct scoring
    label together with a point value.  This prevents ordinary tender prose from
    becoming ScorePoints merely because it mentions an evaluation committee.
    """

    if block.input_role is InputRole.SCORE:
        return True

    heading_context = " / ".join(block.heading_path)
    if block.input_role is InputRole.AMENDMENT:
        return bool(_SCORING_CONTEXT.search(f"{heading_context} {block.content}"))

    if block.input_role is not InputRole.TENDER:
        return False
    content = block.content.strip()
    if _SCORING_HEADER_ONLY.fullmatch(content):
        return False
    if block.block_kind == "heading":
        return bool(_TENDER_DIRECT_SCORING.search(content))
    if _SPECIFIC_SCORING_HEADING.search(heading_context):
        return True
    return bool(
        (_TENDER_DIRECT_SCORING.search(content) and _POINT_VALUE.search(content))
        or (_SCORING_CONTEXT.search(heading_context) and _POINT_VALUE.search(content))
        or (_SCORING_CONTEXT.search(content) and _POINT_VALUE.search(content))
    )
