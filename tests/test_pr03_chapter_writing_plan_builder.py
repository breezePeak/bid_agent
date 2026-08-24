from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from document_pipeline.chapter_writing_plan_builder import ChapterWritingPlanBuilder


def _chapter(title: str = "政策与标准依据") -> dict:
    return {
        "chapter_id": "ch-1",
        "title": title,
        "blueprint_node": {
            "chapter_id": "ch-1",
            "title": title,
            "purpose": f"说明{title}",
        },
    }


def test_builder_has_stable_sources_and_complete_bindings() -> None:
    plan = {
        "schema_version": "v3.chapter-writing-plan.v1",
        "chapter_id": "ch-1",
        "chapter_title": "工作内容",
        "purpose": "说明项目工作内容",
        "blocks": [
            {
                "block_id": "WO-1",
                "heading": "数据治理",
                "must_answer": "说明数据治理工作内容",
                "write_as": "按项目事实说明",
                "requirement_ids": ["REQ-1"],
                "score_point_id": "S-1",
                "condition_id": "C-1",
                "project_fact_refs": ["work_packages[0]"],
            }
        ],
    }
    kwargs = {
        "chapter": _chapter("工作内容"),
        "writing_plan": plan,
        "tender_requirements": [{"requirement_id": "REQ-1", "text": "完成数据治理"}],
        "scoring_requirements": [{"score_point_id": "S-1", "title": "方案完整性"}],
        "project_context": {"work_packages": ["完成数据治理与质量检查"]},
    }

    first = ChapterWritingPlanBuilder().build(**kwargs)
    second = ChapterWritingPlanBuilder().build(**kwargs)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert {item.source_type.value for item in first.sources} == {
        "TENDER_REQUIREMENT",
        "SCORE_OBLIGATION",
        "GLOBAL_PROJECT_FACT",
    }
    assert first.content_units[0].source_refs == sorted(
        item.source_id for item in first.sources
    )
    assert first.research_decisions[0].needs_research is False
    assert first.metadata["shadow_diff"]["project_fact_coverage"] == 1.0


def test_builder_prohibits_enterprise_fact_search() -> None:
    candidate = ChapterWritingPlanBuilder().build(
        chapter=_chapter("企业资质"),
        writing_plan={
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-1",
            "chapter_title": "企业资质",
            "purpose": "说明企业资质",
            "blocks": [{
                "block_id": "WO-1",
                "heading": "企业资质证书",
                "must_answer": "列明本公司资质和人员证书",
                "write_as": "基于企业材料",
                "needs_public_research": True,
            }],
        },
    )

    decision = candidate.research_decisions[0]
    assert decision.prohibited is True
    assert decision.needs_research is False
    assert decision.query == ""
    assert candidate.metadata["shadow_status"] == "ready"


class _FakeExecutor:
    def execute(self, subject, decision):
        assert subject.unit_id == "WO-1"
        return (
            {**decision.model_dump(mode="json"), "decision_status": "published"},
            [{
                "batch_id": "EB-1",
                "evidence_ids": ["EV-1"],
                "sources": [{
                    "evidence_id": "EV-1",
                    "title": "国家标准原文",
                    "publisher": "国家标准平台",
                    "source_url": "https://example.test/standard",
                    "source_type": "official",
                }],
            }],
        )


def test_builder_executes_public_gap_and_binds_web_evidence() -> None:
    candidate = ChapterWritingPlanBuilder(_FakeExecutor()).build(
        chapter=_chapter(),
        writing_plan={
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-1",
            "chapter_title": "政策与标准依据",
            "purpose": "说明政策与标准依据",
            "blocks": [{
                "block_id": "WO-1",
                "heading": "现行标准",
                "must_answer": "说明现行国家标准和规范依据",
                "write_as": "引用可核验公开来源",
            }],
        },
    )

    decision = candidate.research_decisions[0]
    assert decision.status == "published"
    assert decision.evidence_ids == ["EV-1"]
    web = [item for item in candidate.sources if item.source_type.value == "WEB_EVIDENCE"]
    assert len(web) == 1
    assert web[0].snapshot_ref == "evidence:EB-1:EV-1"
    assert any(
        item.source_id == web[0].source_id and item.content_unit_id == "WO-1"
        for item in candidate.source_bindings
    )


def test_builder_binds_relevant_user_material_and_sibling_reference() -> None:
    candidate = ChapterWritingPlanBuilder().build(
        chapter=_chapter("数据治理方案"),
        writing_plan={
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-1",
            "chapter_title": "数据治理方案",
            "purpose": "说明数据治理方案",
            "blocks": [{
                "block_id": "WO-1",
                "heading": "数据治理流程",
                "must_answer": "说明数据治理流程和质量检查",
                "write_as": "按材料和兄弟章边界展开",
            }],
        },
        user_material_blocks=[{
            "block_id": "MB-1",
            "input_role": "company",
            "title": "数据治理能力材料",
            "content": "数据治理流程包括质量检查和问题闭环",
        }],
        sibling_references=[{
            "chapter_id": "ch-overview",
            "title": "总体数据治理路线",
            "purpose": "形成数据治理流程骨架",
            "summary": "数据治理流程分为接收、检查和闭环",
            "has_content": True,
            "content_revision": 2,
            "content_hash": "sibling-hash",
        }],
    )

    assert {item.source_type.value for item in candidate.sources} == {
        "USER_MATERIAL_BLOCK",
        "SIBLING_REFERENCE",
    }
    assert {item.usage_type.value for item in candidate.source_bindings} == {
        "support",
        "cross_reference",
    }


def test_builder_records_search_failure_without_confirming_plan() -> None:
    class FailingExecutor:
        def execute(self, subject, decision):
            raise RuntimeError("provider unavailable")

    candidate = ChapterWritingPlanBuilder(FailingExecutor()).build(
        chapter=_chapter(),
        writing_plan={
            "schema_version": "v3.chapter-writing-plan.v1",
            "chapter_id": "ch-1",
            "chapter_title": "政策与标准依据",
            "purpose": "说明政策与标准依据",
            "blocks": [{
                "block_id": "WO-1",
                "heading": "现行标准",
                "must_answer": "补充政策标准规范",
                "write_as": "引用公开依据",
            }],
        },
    )

    assert candidate.research_decisions[0].status == "failed"
    assert candidate.metadata["shadow_status"] == "failed"


def test_concurrent_chapters_do_not_share_sources() -> None:
    def build(chapter_id: str, fact: str):
        return ChapterWritingPlanBuilder().build(
            chapter={
                "chapter_id": chapter_id,
                "title": fact,
                "blueprint_node": {
                    "chapter_id": chapter_id,
                    "title": fact,
                    "purpose": fact,
                },
            },
            writing_plan={
                "schema_version": "v3.chapter-writing-plan.v1",
                "chapter_id": chapter_id,
                "chapter_title": fact,
                "purpose": fact,
                "blocks": [{
                    "block_id": f"{chapter_id}-WO-1",
                    "heading": fact,
                    "must_answer": fact,
                    "write_as": "按项目事实说明",
                    "project_fact_refs": ["work_packages[0]"],
                }],
            },
            project_context={"work_packages": [fact]},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(build, "ch-a", "数据治理任务")
        second_future = pool.submit(build, "ch-b", "系统运维任务")
        first = first_future.result()
        second = second_future.result()

    first_ids = {item.source_id for item in first.sources}
    second_ids = {item.source_id for item in second.sources}
    assert first_ids.isdisjoint(second_ids)
    assert all(
        binding.source_id in first_ids for binding in first.source_bindings
    )
    assert all(
        binding.source_id in second_ids for binding in second.source_bindings
    )
