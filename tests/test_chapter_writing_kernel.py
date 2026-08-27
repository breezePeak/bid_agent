from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from unittest import mock

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
from control_plane import WorkspaceContext
from document_pipeline.content_writer import ContentWriter
from document_pipeline.contracts import WriterInputBundle


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


def test_rewrite_context_reaches_the_shared_writer_prompt() -> None:
    request = _request(title="章节", operation="rewrite")
    request = ChapterWritingRequest(
        **{
            **request.__dict__,
            "writing_plan": {
                "blocks": [{"must_answer": "说明迁移方案", "write_as": "说明"}],
                "rewrite_context": {
                    "rewrite_schema": "v1",
                    "rewrite_strategy": "light_edit",
                    "selected_legacy_sources": [{"content": "已确认旧文"}],
                },
            },
        }
    )

    spec = compile_chapter_writing_spec(request)
    message = compile_chapter_writing_messages(spec)[1]["content"]

    assert spec.writing_outline["rewrite_context"]["rewrite_strategy"] == "light_edit"
    assert '"selected_legacy_sources"' in message
    assert '"user_rewrite_instruction"' in message


def test_blueprint_legacy_sources_reach_shared_writer_with_current_fact_priority() -> None:
    request = _request(title="章节")
    request = ChapterWritingRequest(
        **{
            **request.__dict__,
            "chapter": {
                **request.chapter,
                "blueprint_node": {
                    **request.chapter["blueprint_node"],
                    "legacy_section_ids": ["OLD-1"],
                    "legacy_sources": [
                        {
                            "section_id": "OLD-1",
                            "block_id": "OLD-BLOCK-1",
                            "content_hash": "old-hash",
                            "content": "岳阳市，2008年形成的旧投标书正文。",
                        }
                    ],
                },
            },
            "chapter_context": {
                "legacy_section_ids": ["OLD-1"],
                "legacy_sources": [
                    {
                        "section_id": "OLD-1",
                        "block_id": "OLD-BLOCK-1",
                        "content_hash": "old-hash",
                        "content": "岳阳市，2008年形成的旧投标书正文。",
                    }
                ],
            },
            "project_context": {
                "identity": {"project_name": "长沙市项目"},
                "confirmed_facts": [
                    {"fact_id": "F-current", "statement": "项目位于长沙市，实施年份为2026年。"}
                ],
            },
        }
    )

    messages = compile_chapter_writing_messages(compile_chapter_writing_spec(request))

    assert "岳阳市，2008年形成的旧投标书正文" in messages[1]["content"]
    assert "当前项目事实优先" in messages[0]["content"]
    assert "旧项目专属事实不得直接继承" in messages[0]["content"]
    assert "有新值使用新值" in messages[0]["content"]
    assert "无新值时删除或泛化旧项目专属事实，不得编造" in messages[0]["content"]


def test_rewrite_chapter_without_legacy_sources_still_compiles_for_new_writing() -> None:
    spec = compile_chapter_writing_spec(_request(title="新增章节"))
    messages = compile_chapter_writing_messages(spec)

    assert spec.chapter_context == {}
    assert '"legacy_sources"' not in messages[1]["content"]
    assert messages[1]["content"]


def test_writer_input_bundle_legacy_content_reaches_actual_content_writer_model_call() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        runs = Path(temporary) / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        bundle = WriterInputBundle(
            revision=1,
            source_hashes={},
            bundle_id="bundle-legacy",
            bundle_hash="bundle-hash",
            unit_id="chapter-CH-1",
            source_blueprint_artifact_id="blueprint-1",
            source_blueprint_revision=1,
            source_blueprint_hash="blueprint-hash",
            h1_receipt_id="h1",
            blueprint_slice=[
                {
                    "chapter_id": "CH-1",
                    "title": "迁移方案",
                    "purpose": "说明迁移实施方法",
                    "writing_objectives": ["形成可执行的迁移步骤"],
                    "legacy_section_ids": ["OLD-1"],
                    "legacy_sources": [
                        {
                            "section_id": "OLD-1",
                            "block_id": "OLD-BLOCK-1",
                            "content_hash": "old-hash",
                            "content": "旧稿采用数据盘点、试迁移、正式迁移和验证。",
                        }
                    ],
                }
            ],
            requirement_excerpts=[],
            score_obligations=[],
            document_target_constraints=[
                {
                    "node_id": "CH-1",
                    "output_target": "CH-1",
                    "title": "迁移方案",
                    "purpose": "说明迁移实施方法",
                    "writing_objectives": ["形成可执行的迁移步骤"],
                    "primary_requirement_ids": [],
                }
            ],
            global_project_context={"identity": {"project_name": "当前项目"}},
            prompt_version="test",
            model_config_hash="test",
        )
        captured: list[dict[str, str]] = []

        def fake_chat(messages, *, temperature):
            del temperature
            captured.extend(messages)
            return "按照当前项目要求，先完成数据盘点，再依次实施试迁移、正式迁移和结果验证。"

        with mock.patch("llm_client.chat", side_effect=fake_chat):
            ContentWriter(context)._draft_chapter_content(
                bundle=bundle,
                target=bundle.document_target_constraints[0],
                requirements=[],
                conditions=[],
                response_units=[],
            )

        assert "旧稿采用数据盘点、试迁移、正式迁移和验证" in captured[1]["content"]
        assert "chapter_context.legacy_sources" in captured[0]["content"]


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
