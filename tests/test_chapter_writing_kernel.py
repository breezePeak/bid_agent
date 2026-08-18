from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from document_pipeline.chapter_writing_kernel import (
    ChapterWritingRequest,
    compile_chapter_writing_messages,
    compile_chapter_writing_spec,
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


def test_active_writing_callers_use_the_shared_kernel() -> None:
    root = Path(__file__).resolve().parents[1]
    api_source = (root / "src/api/v3_app.py").read_text(encoding="utf-8")
    batch_source = (root / "src/document_pipeline/content_writer.py").read_text(
        encoding="utf-8"
    )
    chat_source = (root / "src/document_pipeline/chapter_chat.py").read_text(
        encoding="utf-8"
    )

    assert "compile_chapter_writing_messages" in api_source
    assert "compile_chapter_writing_messages" in batch_source
    assert chat_source.count("    def answer(") == 1
    assert "你是技术标书正文写作器" not in api_source
    assert "你是技术标书正文写作器" not in batch_source
