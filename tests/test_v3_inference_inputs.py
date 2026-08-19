from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import (  # noqa: E402
    InputRole,
    RequirementItem,
    RequirementKind,
    RequirementLedger,
    ScoreGroup,
    ScoreModel,
    ScorePoint,
    SourceAnchor,
    SourceBlock,
    SourceIndex,
)
from document_pipeline.inference_inputs import (  # noqa: E402
    build_project_understanding_input,
    build_score_semantic_input,
    build_score_semantic_input_batches,
)
from document_pipeline.score_agent import ScoreAgent  # noqa: E402


def test_project_input_omits_audit_transcript_and_layout_noise() -> None:
    ledger = RequirementLedger(
        requirements=[
            RequirementItem(
                requirement_id="R-1",
                kind=RequirementKind.MANDATORY,
                source_anchor=SourceAnchor(
                    source_input_id="tender",
                    chunk_id="C-1",
                    location="paragraph:1",
                ),
                original_text="项目范围包括完成数据处理",
                normalized_requirement="项目范围包括完成数据处理",
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="open",
            )
        ],
        coverage_audit={"batch_audit": {"raw_transcript": "x" * 100_000}},
    )
    source = SourceIndex(
        input_manifest_revision=1,
        blocks=[
            SourceBlock(
                block_id="B-1",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=0,
                content="项目名称：数据治理项目；项目范围包括数据处理。",
                source_anchor=SourceAnchor(
                    source_input_id="tender",
                    chunk_id="C-1",
                    location="paragraph:1",
                ),
                content_hash="hash",
            )
        ]
    )

    request = build_project_understanding_input(
        ledger,
        source,
    )

    assert request.requirement_ledger == {
        "projection_version": "v3.project_input.v3",
        "revision": 1,
    }
    assert request.scanned_source_block_count == 1
    assert request.source_context == [
        {
            "block_id": "B-1",
            "input_id": "tender",
            "input_role": "tender",
            "block_kind": "paragraph",
            "ordinal": 0,
            "content": "项目名称：数据治理项目；项目范围包括数据处理。",
            "heading_path": [],
            "source_anchor": {
                "source_input_id": "tender",
                "chunk_id": "C-1",
                "page": None,
                "location": "paragraph:1",
            },
        }
    ]
    assert "score_model" not in request.model_dump(mode="json")
    assert len(request.model_dump_json()) <= 16_000


def test_score_semantic_input_contains_only_directionally_linked_context() -> None:
    score_anchor = SourceAnchor(
        source_input_id="score-doc",
        chunk_id="S-1",
        location="table[0]/row[1]",
    )
    linked_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="R-1",
        location="paragraph:8",
    )
    unlinked_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="R-2",
        location="paragraph:9",
    )
    source = SourceIndex(
        input_manifest_revision=1,
        source_hashes={"score-doc": "score-hash", "tender": "tender-hash"},
        blocks=[
            SourceBlock(
                block_id="SB-1",
                input_id="score-doc",
                input_role=InputRole.SCORE,
                block_kind="table_cell",
                ordinal=0,
                content="方案内容完整、方法科学，得2分",
                heading_path=["评分办法", "技术评分"],
                source_anchor=score_anchor,
                content_hash="score-block-hash",
            ),
            SourceBlock(
                block_id="RB-1",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=1,
                content="须提交实施方案",
                heading_path=["采购需求", "实施要求"],
                source_anchor=linked_anchor,
                content_hash="linked-block-hash",
            ),
            SourceBlock(
                block_id="RB-2",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=2,
                content="须提交无关材料",
                heading_path=["采购需求", "其他要求"],
                source_anchor=unlinked_anchor,
                content_hash="unlinked-block-hash",
            ),
        ],
    )
    ledger = RequirementLedger(
        requirements=[
            RequirementItem(
                requirement_id="REQ-LINKED",
                kind=RequirementKind.MANDATORY,
                source_anchor=linked_anchor,
                original_text="须提交实施方案",
                normalized_requirement="提交实施方案",
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="confirmed",
            ),
            RequirementItem(
                requirement_id="REQ-UNLINKED",
                kind=RequirementKind.MANDATORY,
                source_anchor=unlinked_anchor,
                original_text="须提交无关材料",
                normalized_requirement="提交无关材料",
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="open",
            ),
        ]
    )
    scores = ScoreModel(
        model_id="SCORE-1",
        source_input_ids=["score-doc"],
        total_points=2,
        groups=[
            ScoreGroup(
                group_id="SG-1",
                title="技术评分",
                declared_points=2,
            )
        ],
        points=[
            ScorePoint(
                score_point_id="SP-1",
                group_id="SG-1",
                title="实施方案",
                criterion="方案内容完整、方法科学，得2分",
                max_points=2,
                response_expectation="完整响应实施方案",
                context_requirement_ids=["REQ-LINKED"],
                source_anchors=[score_anchor],
                confidence=1,
            )
        ],
    )

    request = build_score_semantic_input(scores, source, ledger)
    batches = build_score_semantic_input_batches(
        scores,
        source,
        ledger,
        max_input_chars=100_000,
    )

    assert request.rules[0].linked_requirement_ids == []
    assert request.rules[0].context_requirement_ids == ["REQ-LINKED"]
    assert [
        item.requirement_id for item in request.linked_requirements
    ] == ["REQ-LINKED"]
    assert request.linked_requirements[0].original_text == "须提交实施方案"
    assert {
        tuple(item.heading_path) for item in request.document_map
    } == {
        ("评分办法", "技术评分"),
        ("采购需求", "实施要求"),
        ("采购需求", "其他要求"),
    }
    assert {
        block_id
        for item in request.document_map
        for block_id in item.block_ids
    } == {"SB-1", "RB-1", "RB-2"}
    assert sum(item.block_count for item in request.document_map) == 3
    assert all(
        item.title == item.heading_path[-1]
        for item in request.document_map
    )
    assert len({item.heading_id for item in request.document_map}) == len(
        request.document_map
    )
    score_entries = [
        item for item in request.document_map if item.score_rule_ids
    ]
    assert len(score_entries) == 1
    assert score_entries[0].score_rule_ids == ["SP-1"]
    assert {
        item.content_type for item in request.document_map
    } == {"table_cell", "paragraph"}
    assert len(batches) == 1
    assert batches[0].semantic_input.linked_requirements == request.linked_requirements
    assert batches[0].fingerprint


def test_document_map_compresses_1480_blocks_to_heading_level_topology() -> None:
    blocks: list[SourceBlock] = []
    for index in range(1480):
        heading_index = index // 370
        anchor = SourceAnchor(
            source_input_id="tender",
            chunk_id=f"C-{index:04d}",
            location=f"paragraph:{index + 1}",
        )
        blocks.append(
            SourceBlock(
                block_id=f"SB-{index:04d}",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind=(
                    "heading" if index % 370 == 0 else "paragraph"
                ),
                ordinal=index,
                content=(
                    f"第{heading_index + 1}章"
                    if index % 370 == 0
                    else f"不应进入地图的正文-{index:04d}"
                ),
                heading_path=["采购需求", f"子章节{heading_index + 1}"],
                source_anchor=anchor,
                content_hash=f"hash-{index:04d}",
            )
        )
    source = SourceIndex(
        input_manifest_revision=1,
        source_hashes={"tender": "source-hash"},
        blocks=blocks,
    )
    scores = ScoreModel(
        model_id="SCORE-LARGE",
        source_input_ids=["tender"],
        total_points=10,
        groups=[
            ScoreGroup(
                group_id="SG-TECH",
                title="技术部分",
                declared_points=10,
            )
        ],
        points=[
            ScorePoint(
                score_point_id="SP-LARGE",
                group_id="SG-TECH",
                title="技术方案",
                criterion="第1章",
                max_points=10,
                response_expectation="响应技术方案",
                source_anchors=[blocks[0].source_anchor],
                confidence=1,
            )
        ],
    )

    request = build_score_semantic_input(scores, source)
    payload = request.model_dump(mode="json")

    assert len(request.document_map) == 4
    assert sum(item.block_count for item in request.document_map) == 1480
    assert sum(len(item.block_ids) for item in request.document_map) == 8
    assert all(len(item.block_ids) <= 2 for item in request.document_map)
    assert all("chunk_ids" not in item for item in payload["document_map"])
    assert "不应进入地图的正文-1479" not in request.model_dump_json()


def test_score_context_prefers_real_procurement_requirements_and_excludes_score_fragments() -> None:
    def anchor(chunk_id: str, location: str) -> SourceAnchor:
        return SourceAnchor(
            source_input_id="tender",
            chunk_id=chunk_id,
            location=location,
        )

    score_anchor = anchor("S-1", "table:8:row:3:cell:2")
    requirement_specs = [
        (
            "R-EXPLICIT",
            RequirementKind.MANDATORY,
            "E-1",
            "满足5.2条规定的成果处理要求",
            ["第五章 采购需求", "特殊条款"],
            "5.2",
        ),
        (
            "R-DIRECT",
            RequirementKind.MANDATORY,
            "D-1",
            "配置项目实施人员",
            ["第五章 采购需求", "人员安排"],
            None,
        ),
        (
            "R-SAME",
            RequirementKind.DELIVERABLE,
            "H-1",
            "形成路线图",
            ["第五章 采购需求", "技术路线"],
            None,
        ),
        (
            "R-KEYWORD",
            RequirementKind.MANDATORY,
            "K-1",
            "设计技术方法和工作流程，保证路线合理高效",
            ["第五章 采购需求", "具体服务要求"],
            None,
        ),
        (
            "R-NOISE",
            RequirementKind.MANDATORY,
            "N-1",
            "技术路线和工作流程必须完整",
            ["第二章 投标人须知", "符合性审查"],
            None,
        ),
    ]
    blocks = [
        SourceBlock(
            block_id="SB-SCORE",
            input_id="tender",
            input_role=InputRole.TENDER,
            block_kind="table_cell",
            ordinal=0,
            content="技术路线应满足5.2条，方法科学、流程合理，得6分",
            heading_path=["第三章 评标方法和标准", "技术评分"],
            source_anchor=score_anchor,
            content_hash="score-hash",
        )
    ]
    requirements = [
        RequirementItem(
            requirement_id="R-SCORE-FRAGMENT",
            kind=RequirementKind.SCORE,
            source_anchor=score_anchor,
            original_text="方法科学、流程合理，得6分",
            normalized_requirement="方法科学、流程合理，得6分",
            response_type="score_response",
            evidence_policy="tender_traceable",
            status="confirmed",
        )
    ]
    for order, (
        requirement_id,
        kind,
        chunk_id,
        text,
        heading_path,
        clause_id,
    ) in enumerate(requirement_specs, start=1):
        requirement_anchor = anchor(chunk_id, f"paragraph:{order}")
        blocks.append(
            SourceBlock(
                block_id=f"SB-{chunk_id}",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=order,
                content=text,
                heading_path=heading_path,
                source_anchor=requirement_anchor,
                content_hash=f"hash-{chunk_id}",
            )
        )
        requirements.append(
            RequirementItem(
                requirement_id=requirement_id,
                kind=kind,
                source_anchor=requirement_anchor,
                original_text=text,
                normalized_requirement=text,
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="confirmed",
                clause_id=clause_id,
            )
        )
    source = SourceIndex(
        input_manifest_revision=1,
        source_hashes={"tender": "source-hash"},
        blocks=blocks,
    )
    ledger = RequirementLedger(requirements=requirements)
    scores = ScoreModel(
        model_id="SCORE-CONTEXT",
        source_input_ids=["tender"],
        total_points=6,
        groups=[
            ScoreGroup(
                group_id="SG-TECH",
                title="技术部分",
                declared_points=6,
            )
        ],
        points=[
            ScorePoint(
                score_point_id="SP-TECH",
                group_id="SG-TECH",
                title="技术路线",
                criterion="技术路线应满足5.2条，方法科学、流程合理，得6分",
                max_points=6,
                outline_path=["技术路线"],
                linked_requirement_ids=["R-SCORE-FRAGMENT"],
                context_requirement_ids=["R-DIRECT"],
                response_expectation="完整响应技术路线",
                source_anchors=[score_anchor],
                confidence=1,
            )
        ],
    )

    request = build_score_semantic_input(scores, source, ledger)

    assert request.rules[0].linked_requirement_ids == []
    assert request.rules[0].context_requirement_ids[:4] == [
        "R-EXPLICIT",
        "R-DIRECT",
        "R-SAME",
        "R-KEYWORD",
    ]
    assert "R-SCORE-FRAGMENT" not in request.rules[0].context_requirement_ids
    assert "R-NOISE" not in request.rules[0].context_requirement_ids
    assert "R-SCORE-FRAGMENT" not in {
        item.requirement_id for item in request.linked_requirements
    }


def test_formula_only_price_point_does_not_retrieve_procurement_context() -> None:
    score_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="S-PRICE",
        location="table:1:row:1:cell:1",
    )
    requirement_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="R-PRICE",
        location="paragraph:2",
    )
    source = SourceIndex(
        input_manifest_revision=1,
        source_hashes={"tender": "source-hash"},
        blocks=[
            SourceBlock(
                block_id="SB-PRICE",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="table_cell",
                ordinal=0,
                content=(
                    "满足招标文件要求且投标价格最低的报价为评标基准价，"
                    "报价得分按公式计算"
                ),
                heading_path=["第三章 评标方法和标准", "价格部分"],
                source_anchor=score_anchor,
                content_hash="score-price-hash",
            ),
            SourceBlock(
                block_id="SB-PRICE-FORM",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=1,
                content="投标总价应和投标分项报价表中的总价相一致",
                heading_path=["第六章 投标文件格式", "开标一览表"],
                source_anchor=requirement_anchor,
                content_hash="price-form-hash",
            ),
        ],
    )
    ledger = RequirementLedger(
        requirements=[
            RequirementItem(
                requirement_id="R-PRICE-FORM",
                kind=RequirementKind.MANDATORY,
                source_anchor=requirement_anchor,
                original_text="投标总价应和投标分项报价表中的总价相一致",
                normalized_requirement="投标总价应和投标分项报价表中的总价相一致",
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="confirmed",
            )
        ]
    )
    scores = ScoreModel(
        model_id="SCORE-PRICE-CONTEXT",
        source_input_ids=["tender"],
        total_points=10,
        groups=[
            ScoreGroup(
                group_id="SG-PRICE",
                title="价格部分",
                declared_points=10,
            )
        ],
        points=[
            ScorePoint(
                score_point_id="SP-PRICE",
                group_id="SG-PRICE",
                title="投标报价",
                criterion=(
                    "满足招标文件要求且投标价格最低的报价为评标基准价，"
                    "报价得分按公式计算"
                ),
                max_points=10,
                response_expectation="响应投标报价",
                source_anchors=[score_anchor],
                confidence=1,
            )
        ],
    )

    request = build_score_semantic_input(scores, source, ledger)

    assert request.rules[0].context_requirement_ids == []
    assert request.linked_requirements == []


def test_explicit_table_reference_allows_targeted_price_context() -> None:
    score_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="S-PRICE-TABLE",
        location="table:1:row:1:cell:1",
    )
    requirement_anchor = SourceAnchor(
        source_input_id="tender",
        chunk_id="R-PRICE-TABLE",
        location="table:7:row:1:cell:2",
    )
    score_block = SourceBlock(
        block_id="SB-PRICE-TABLE",
        input_id="tender",
        input_role=InputRole.TENDER,
        block_kind="table_cell",
        ordinal=0,
        content="报价得分按附件1开标一览表的投标总价计算",
        heading_path=["第三章 评标方法和标准", "价格部分"],
        source_anchor=score_anchor,
        content_hash="score-price-table-hash",
    )
    requirement_block = SourceBlock(
        block_id="SB-PRICE-TABLE-REQUIREMENT",
        input_id="tender",
        input_role=InputRole.TENDER,
        block_kind="table_cell",
        ordinal=1,
        content="投标总价应与投标分项报价表中的总价一致",
        heading_path=["第六章 投标文件格式", "附件1 开标一览表"],
        source_anchor=requirement_anchor,
        content_hash="price-table-requirement-hash",
    )
    ledger = RequirementLedger(
        requirements=[
            RequirementItem(
                requirement_id="R-PRICE-TABLE",
                kind=RequirementKind.MANDATORY,
                source_anchor=requirement_anchor,
                original_text="投标总价应与投标分项报价表中的总价一致",
                normalized_requirement="投标总价应与投标分项报价表中的总价一致",
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="confirmed",
            )
        ]
    )
    point = ScorePoint(
        score_point_id="SP-PRICE-TABLE",
        group_id="SG-PRICE",
        title="投标报价",
        criterion="报价得分按附件1开标一览表的投标总价计算",
        max_points=10,
        response_expectation="响应投标报价",
        source_anchors=[score_anchor],
        confidence=1,
    )

    selected = ScoreAgent._context_requirement_ids_for_point(
        point,
        {
            ("tender", "S-PRICE-TABLE"): score_block,
            ("tender", "R-PRICE-TABLE"): requirement_block,
        },
        ledger,
    )

    assert selected == ["R-PRICE-TABLE"]


def test_context_retrieval_excludes_generic_contract_noise_unless_explicit() -> None:
    def anchor(chunk_id: str, location: str) -> SourceAnchor:
        return SourceAnchor(
            source_input_id="tender",
            chunk_id=chunk_id,
            location=location,
        )

    score_anchor = anchor("S-ROUTE", "table:2:row:1:cell:1")
    requirement_specs = [
        (
            "R-ROUTE",
            RequirementKind.CONTRACT,
            "R-ROUTE-CHUNK",
            "工作路线与方法应包括数据准备、内业核查和成果复核流程",
            ["第四章 合同条款", "二、工作路线与方法"],
            None,
        ),
        (
            "R-GUARANTEE",
            RequirementKind.MANDATORY,
            "R-GUARANTEE-CHUNK",
            "工作路线相关履约保证金（本项目不适用）",
            ["第五章 采购需求", "工作路线与方法"],
            "31",
        ),
        (
            "R-SECURITY",
            RequirementKind.CONTRACT,
            "R-SECURITY-CHUNK",
            "技术路线和工作流程涉及安全保密及失泄密责任",
            ["第四章 合同条款", "一般条款"],
            None,
        ),
        (
            "R-EXPLICIT-GUARANTEE",
            RequirementKind.CONTRACT,
            "R-EXPLICIT-GUARANTEE-CHUNK",
            "31.1条规定履约保证金提交要求",
            ["第四章 合同条款", "履约保证金"],
            "31.1",
        ),
    ]
    blocks = [
        SourceBlock(
            block_id="SB-ROUTE-SCORE",
            input_id="tender",
            input_role=InputRole.TENDER,
            block_kind="table_cell",
            ordinal=0,
            content="技术路线与工作方法应满足31.1条，流程完整合理，得6分",
            heading_path=["第三章 评标方法和标准", "技术部分"],
            source_anchor=score_anchor,
            content_hash="route-score-hash",
        )
    ]
    requirements = []
    for ordinal, (
        requirement_id,
        kind,
        chunk_id,
        text,
        heading_path,
        clause_id,
    ) in enumerate(requirement_specs, start=1):
        requirement_anchor = anchor(chunk_id, f"paragraph:{ordinal}")
        blocks.append(
            SourceBlock(
                block_id=f"SB-{chunk_id}",
                input_id="tender",
                input_role=InputRole.TENDER,
                block_kind="paragraph",
                ordinal=ordinal,
                content=text,
                heading_path=heading_path,
                source_anchor=requirement_anchor,
                content_hash=f"hash-{chunk_id}",
            )
        )
        requirements.append(
            RequirementItem(
                requirement_id=requirement_id,
                kind=kind,
                source_anchor=requirement_anchor,
                original_text=text,
                normalized_requirement=text,
                response_type="mandatory_response",
                evidence_policy="tender_traceable",
                status="confirmed",
                clause_id=clause_id,
            )
        )
    source = SourceIndex(
        input_manifest_revision=1,
        source_hashes={"tender": "source-hash"},
        blocks=blocks,
    )
    ledger = RequirementLedger(requirements=requirements)
    scores = ScoreModel(
        model_id="SCORE-CONTRACT-NOISE",
        source_input_ids=["tender"],
        total_points=6,
        groups=[
            ScoreGroup(
                group_id="SG-TECH",
                title="技术部分",
                declared_points=6,
            )
        ],
        points=[
            ScorePoint(
                score_point_id="SP-ROUTE",
                group_id="SG-TECH",
                title="技术路线",
                criterion=(
                    "技术路线与工作方法应满足31.1条，流程完整合理，得6分"
                ),
                max_points=6,
                outline_path=[
                    "工作路线与方法",
                    "年度核查质量控制检查和成果复核（31分）",
                ],
                response_expectation="完整响应技术路线",
                source_anchors=[score_anchor],
                confidence=1,
            )
        ],
    )

    request = build_score_semantic_input(scores, source, ledger)
    selected_ids = request.rules[0].context_requirement_ids

    assert "R-ROUTE" in selected_ids
    assert "R-EXPLICIT-GUARANTEE" in selected_ids
    assert "R-GUARANTEE" not in selected_ids
    assert "R-SECURITY" not in selected_ids
