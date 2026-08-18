from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError  # noqa: E402
from document_pipeline.content_grounding import ContentGroundingGate  # noqa: E402


PROJECT_NAME = "2026年度全国国土变更调查监测数据核实处理项目"


def _global_context() -> dict:
    return {
        "global_context_id": "ProjectModel@4",
        "global_context_revision": 4,
        "global_context_hash": "a" * 64,
        "project_id": "land-change-2026",
        "identity": {
            "project_name": PROJECT_NAME,
            "purchaser": "中国国土勘测规划院",
        },
        "background": [
            "面向年度全国国土变更调查监测数据开展国家级核实处理。"
        ],
        "goals": ["形成可复核、可验收的国家级核查成果。"],
        "scope": ["核查范围覆盖全国31个省级区域。"],
        "work_packages": [
            "完成数据接收、任务分发、国家级内外业核查、质量控制及成果复核。"
        ],
        "processing": ["开展国家级内业核查和外业核查。"],
        "outputs": ["形成质量控制记录和成果复核结果。"],
        "deliverables": ["提交国家级核查成果。"],
        "acceptance_conditions": ["成果通过采购人组织的复核验收。"],
        "constraints": ["全过程执行质量、安全和保密要求。"],
        "confirmed_facts": [
            {
                "fact_id": "PF-1",
                "statement": "核查范围覆盖全国31个省级区域。",
            },
            {
                "fact_id": "PF-2",
                "statement": "项目包括数据接收、任务分发、国家级内外业核查、质量控制及成果复核。",
            },
        ],
    }


def _chapter_context() -> dict:
    return {
        "chapter_id": "background",
        "global_context_id": "ProjectModel@4",
        "global_context_revision": 4,
        "global_context_hash": "a" * 64,
        "chapter_context_id": "chapter-context:background",
        "chapter_context_revision": 2,
        "chapter_context_hash": "b" * 64,
    }


class ContentGroundingGateTests(unittest.TestCase):
    def test_goal_gate_rejects_real_but_off_goal_procurement_process(self) -> None:
        chapter = {
            "chapter_id": "background",
            "title": "项目任务背景",
            "blueprint_node": {
                "purpose": "交代项目任务所处背景、现实情境及任务由来，帮助评审理解项目实施基础。",
                "writing_objectives": ["清楚说明项目任务背景及任务由来。"],
            },
        }
        with (
            mock.patch(
                "document_pipeline.content_grounding._goal_alignment_review",
                return_value={
                    "verdict": "drifted",
                    "confidence": 0.96,
                    "off_goal_paragraphs": [0, 1],
                    "reason": "正文主要写采购人安排、资料接收和任务分发。",
                },
            ),
            self.assertRaises(ControlPlaneError) as caught,
        ):
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter=chapter,
                chapter_grounding_context=_chapter_context(),
                content=(
                    f"{PROJECT_NAME}面向年度全国国土变更调查监测数据开展国家级核实处理，"
                    "并由采购人形成本次任务。\n\n"
                    "项目组依据采购人安排接收资料并完成任务分发。"
                ),
            )
        self.assertEqual(caught.exception.code, "CHAPTER_GOAL_MISALIGNED")

    def test_fact_binding_does_not_bind_many_facts_on_weak_bigram_overlap(self) -> None:
        context = _global_context()
        context["confirmed_facts"] = [
            {"fact_id": f"PF-{index}", "statement": f"采购人安排第{index}批项目任务并组织人员流程。"}
            for index in range(35)
        ]
        report = ContentGroundingGate.evaluate(
            global_context=context,
            chapter={"chapter_id": "quality", "title": "质量控制"},
            chapter_grounding_context=_chapter_context(),
            content="完成数据接收、任务分发、国家级内外业核查、质量控制及成果复核。",
        )
        self.assertLessEqual(len(report["paragraph_fact_bindings"]["0"]), 6)
        self.assertLessEqual(len(report["used_fact_ids"]), 6)

    def test_project_background_must_use_actual_tender_facts(self) -> None:
        report = ContentGroundingGate.evaluate(
            global_context=_global_context(),
            chapter={"chapter_id": "background", "title": "项目任务背景"},
            chapter_grounding_context=_chapter_context(),
            content=(
                f"{PROJECT_NAME}面向年度全国国土变更调查监测数据开展国家级核实处理，"
                "核查范围覆盖全国31个省级区域。\n\n"
                "本项目将完成数据接收、任务分发、国家级内外业核查、质量控制及成果复核。"
            ),
        )
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["global_context_revision"], 4)
        self.assertIn("PF-1", report["used_fact_ids"])

    def test_real_but_generic_policy_text_is_blocked(self) -> None:
        with self.assertRaises(ControlPlaneError) as caught:
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "background", "title": "项目任务背景"},
                chapter_grounding_context=_chapter_context(),
                content=(
                    "招标投标制度是市场配置资源的重要机制，应坚持公开、公平、公正和诚实信用，"
                    "完善交易规则并强化全过程监管。"
                ),
            )
        self.assertEqual(caught.exception.code, "PROJECT_SPECIFICITY_MISSING")

    def test_grounding_does_not_require_title_specific_project_identity(self) -> None:
        report = ContentGroundingGate.evaluate(
            global_context=_global_context(),
            chapter={"chapter_id": "quality", "title": "质量控制"},
            chapter_grounding_context=_chapter_context(),
            content=(
                "完成数据接收、任务分发、国家级内外业核查、质量控制及成果复核。"
            ),
        )

        self.assertEqual(report["verdict"], "pass")

    def test_semantic_relevance_accepts_synonymous_project_facts(self) -> None:
        with mock.patch(
            "document_pipeline.content_grounding._semantic_relevance_review",
            return_value={
                "verdict": "relevant",
                "confidence": 0.9,
                "matched_fact_ids": ["scope:0"],
                "matched_requirement_ids": [],
                "matched_evidence_ids": [],
                "paragraph_fact_bindings": {"0": ["scope:0"]},
            },
        ):
            report = ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=_chapter_context(),
                content="建立分层任务矩阵并按阶段输出检查记录。",
            )

        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["relevance_method"], "semantic")
        self.assertEqual(report["semantic_review"]["confidence"], 0.9)

    def test_semantic_conflict_remains_blocking(self) -> None:
        with (
            mock.patch(
                "document_pipeline.content_grounding._semantic_relevance_review",
                return_value={
                    "verdict": "conflict",
                    "confidence": 0.95,
                    "matched_fact_ids": [],
                    "matched_requirement_ids": [],
                    "matched_evidence_ids": [],
                    "reason": "正文声称了项目未提供的范围。",
                },
            ),
            self.assertRaises(ControlPlaneError) as caught,
        ):
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=_chapter_context(),
                content="本项目仅覆盖单一城市，与当前项目范围不一致。",
            )

        self.assertEqual(caught.exception.code, "PROJECT_SEMANTIC_CONFLICT")

    def test_semantic_review_unavailability_is_not_reported_as_bad_content(self) -> None:
        with (
            mock.patch("llm_client.chat", side_effect=RuntimeError("provider down")),
            self.assertRaises(ControlPlaneError) as caught,
        ):
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=_chapter_context(),
                content="建立分层任务矩阵并按阶段输出检查记录。",
            )

        self.assertEqual(caught.exception.code, "PROJECT_RELEVANCE_REVIEW_UNAVAILABLE")
        self.assertEqual(caught.exception.status_code, 503)

    def test_prepending_project_name_to_generic_prose_is_still_blocked(self) -> None:
        with (
            mock.patch(
                "document_pipeline.content_grounding._semantic_relevance_review",
                return_value={
                    "verdict": "irrelevant",
                    "confidence": 0.1,
                    "matched_fact_ids": [],
                    "matched_requirement_ids": [],
                    "matched_evidence_ids": [],
                },
            ),
            self.assertRaises(ControlPlaneError) as caught,
        ):
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "background", "title": "项目任务背景"},
                chapter_grounding_context=_chapter_context(),
                content=(
                    f"{PROJECT_NAME}。\n\n"
                    "招标投标制度应发挥市场竞争作用，并坚持公开、公平、公正和诚实信用。"
                ),
            )
        self.assertEqual(caught.exception.code, "PROJECT_SPECIFICITY_MISSING")

    def test_non_background_chapter_can_link_project_after_its_opening(self) -> None:
        report = ContentGroundingGate.evaluate(
            global_context=_global_context(),
            chapter={"chapter_id": "goal", "title": "工作目标"},
            chapter_grounding_context={
                **_chapter_context(),
                "chapter_id": "goal",
                "chapter_context_id": "chapter-context:goal",
            },
            content=(
                "工作目标聚焦形成可复核、可验收的国家级核查成果，建立成果质量、"
                "问题闭环和交付完整性相互衔接的目标体系。\n\n"
                "具体目标按照成果完整、过程可追溯、问题可闭环三个层次组织，避免以项目概况"
                "代替目标描述。\n\n"
                f"上述目标服务于{PROJECT_NAME}，并落实数据接收、任务分发、国家级内外业"
                "核查、质量控制及成果复核等具体任务。"
            ),
        )

        self.assertEqual(report["verdict"], "pass")

    def test_global_or_chapter_version_mismatch_is_blocked(self) -> None:
        local = _chapter_context()
        local["global_context_revision"] = 3
        with self.assertRaises(ControlPlaneError) as caught:
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=local,
                content=f"{PROJECT_NAME}执行国家级核查和成果复核。",
            )
        self.assertEqual(caught.exception.code, "CHAPTER_CONTEXT_CONFLICT")

    def test_other_project_identity_is_blocked_even_when_facts_are_present(self) -> None:
        with self.assertRaises(ControlPlaneError) as caught:
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=_chapter_context(),
                content=(
                    f"{PROJECT_NAME}将完成质量控制及成果复核。\n\n"
                    "项目名称：另一城市测绘项目。"
                ),
            )
        self.assertEqual(caught.exception.code, "PROJECT_FACT_CONFLICT")

    def test_fact_requirement_and_public_evidence_are_bound_to_real_paragraphs(self) -> None:
        report = ContentGroundingGate.evaluate(
            global_context=_global_context(),
            chapter={"chapter_id": "quality", "title": "质量控制"},
            chapter_grounding_context=_chapter_context(),
            requirement_texts=["建立全过程质量控制并形成成果复核记录。"],
            evidence_sources=[{
                "batch_id": "EB-1234567890abcdef",
                "evidence_id": "E-STD-1",
                "content": "相关规范要求建立全过程质量控制并保留成果复核记录。",
            }],
            require_evidence_use=True,
            content=(
                f"{PROJECT_NAME}将完成质量控制及成果复核。\n\n"
                "实施中建立全过程质量控制并形成成果复核记录。"
            ),
        )
        self.assertIn("E-STD-1", report["used_evidence_ids"])
        self.assertIn("E-STD-1", report["paragraph_evidence_bindings"]["1"])
        self.assertTrue(report["paragraph_requirement_bindings"]["1"])

    def test_unrelated_public_source_cannot_be_reported_as_used(self) -> None:
        with (
            mock.patch(
                "document_pipeline.content_grounding._semantic_relevance_review",
                return_value={
                    "verdict": "relevant",
                    "confidence": 0.9,
                    "matched_fact_ids": ["work_packages:0"],
                    "matched_requirement_ids": [],
                    "matched_evidence_ids": [],
                },
            ),
            self.assertRaises(ControlPlaneError) as caught,
        ):
            ContentGroundingGate.evaluate(
                global_context=_global_context(),
                chapter={"chapter_id": "quality", "title": "质量控制"},
                chapter_grounding_context=_chapter_context(),
                evidence_sources=[{
                    "batch_id": "EB-fedcba0987654321",
                    "evidence_id": "E-GENERIC",
                    "content": "招标投标市场应当坚持公开公平公正。",
                }],
                require_evidence_use=True,
                content=(
                    f"{PROJECT_NAME}将完成国家级内外业核查、质量控制及成果复核。"
                ),
            )
        self.assertEqual(caught.exception.code, "PUBLIC_EVIDENCE_NOT_USED")


if __name__ == "__main__":
    unittest.main()
