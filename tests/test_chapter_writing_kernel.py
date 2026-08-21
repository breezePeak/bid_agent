from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from document_pipeline.chapter_writing_kernel import (
    ChapterWritingRequest,
    compile_chapter_scope_contract,
    compile_chapter_writing_messages,
    compile_chapter_writing_spec,
    project_chapter_facts,
)


def _request(*, title: str, operation: str = "create") -> ChapterWritingRequest:
    return ChapterWritingRequest(
        chapter_id="CH-1",
        operation=operation,
        existing_content="旧正文" if operation != "create" else "",
        validation_errors=("缺少项目事实",) if operation == "repair" else (),
        chapter={
            "chapter_id": "CH-1",
            "title": title,
            "blueprint_node": {
                "chapter_id": "CH-1",
                "title": title,
                "purpose": "说明现状约束及开展工作的客观依据",
                "writing_objectives": ["论证实际需求与实施条件"],
                "target_size": 500,
            },
        },
        project_context={
            "identity": {"project_name": "示例项目", "purchaser": "某单位"},
            "confirmed_facts": [
                {"fact_id": "F-1", "statement": "现状存在数据分散约束"},
                {"fact_id": "F-2", "statement": "采购人负责组织年度考核"},
            ],
        },
    )


def test_title_does_not_select_rules_or_project_facts() -> None:
    first = compile_chapter_writing_spec(_request(title="工作必要性与可行性"))
    renamed = compile_chapter_writing_spec(_request(title="任意名称"))

    assert first.project_context == renamed.project_context
    assert compile_chapter_writing_messages(first)[0] == compile_chapter_writing_messages(renamed)[0]
    assert "purchaser" not in first.project_context.get("identity", {})


def test_create_rewrite_and_repair_share_one_system_contract() -> None:
    messages = [
        compile_chapter_writing_messages(
            compile_chapter_writing_spec(_request(title="章节", operation=operation))
        )
        for operation in ("create", "rewrite", "repair")
    ]

    assert len({item[0]["content"] for item in messages}) == 1
    assert [item[1]["content"] for item in messages]


def test_writer_receives_target_size_and_substantive_body_rules() -> None:
    spec = compile_chapter_writing_spec(_request(title="章节"))
    messages = compile_chapter_writing_messages(spec)

    assert spec.target_size == 500
    assert '"target_size":500' in messages[1]["content"]
    assert "submission-ready body text" in messages[0]["content"]
    assert 'assert that an objective is "clear"' in messages[0]["content"]


def test_scope_contract_is_the_same_boundary_without_runtime_chat_state() -> None:
    spec = compile_chapter_writing_spec(_request(title="工作必要性与可行性"))
    contract = compile_chapter_scope_contract(spec)
    payload = contract.payload()

    assert contract == spec.scope_contract()
    assert payload["purpose"] == spec.purpose
    assert payload["writing_objectives"] == list(spec.writing_objectives)
    assert payload["writing_outline"] == spec.writing_outline
    assert payload["project_context"] == spec.project_context
    assert "history" not in payload
    assert "user_instruction" not in payload
    assert "existing_content" not in payload

    repeated = compile_chapter_writing_spec(
        _request(title="工作必要性与可行性")
    ).scope_contract()
    assert repeated.scope_hash == contract.scope_hash


def test_fact_projection_never_falls_back_to_unrelated_project_facts() -> None:
    projected = project_chapter_facts(
        {
            "identity": {"project_name": "示例项目", "purchaser": "某单位"},
            "background": ["采购人负责组织年度考核"],
            "outputs": [{"name": "部署记录", "description": "上线后提交"}],
            "confirmed_facts": [
                {"fact_id": "F-1", "statement": "采购人负责组织年度考核"},
            ],
        },
        purpose="解释质量核查结果复核的客观必要性",
        writing_objectives=["论证核查与复核为什么不可替代"],
        writing_outline={
            "blocks": [
                {"must_answer": "为什么必须开展质量核查与结果复核", "write_as": "因果论证"},
            ]
        },
    )

    assert projected.get("identity") == {"project_name": "示例项目"}
    assert "background" not in projected
    assert "outputs" not in projected
    assert "confirmed_facts" not in projected
    assert projected["selected_fact_ids"] == []


def test_active_writing_callers_use_the_shared_kernel() -> None:
    root = Path(__file__).resolve().parents[1]
    api_source = (root / "src/api/v3_app.py").read_text(encoding="utf-8")
    batch_source = (root / "src/document_pipeline/content_writer.py").read_text(
        encoding="utf-8"
    )
    chat_source = (root / "src/document_pipeline/chapter_chat.py").read_text(
        encoding="utf-8"
    )

    assert "ChapterWritingService" in api_source
    assert "compile_chapter_writing_messages" in batch_source
    assert chat_source.count("    def answer(") == 1
    assert "你是技术标书正文写作器" not in api_source
    assert "你是技术标书正文写作器" not in batch_source
