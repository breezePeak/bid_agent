from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext
from document_pipeline.content_gate import WriterBundleContentGate
from document_pipeline.content_writer import ContentWriter
from document_pipeline.contracts import ContentBlock, WriterInputBundle
from document_pipeline.writer_policy import content_quality_findings


def _bundle() -> WriterInputBundle:
    return WriterInputBundle(
        revision=1,
        source_hashes={"T1": "h"},
        bundle_id="bundle-1",
        unit_id="unit-1",
        source_blueprint_artifact_id="bp-1",
        source_blueprint_revision=1,
        source_blueprint_hash="bp-hash",
        h1_receipt_id="h1",
        blueprint_slice=[{"chapter_id": "chapter-1"}],
        topic_and_duty_slice=[{"topic_id": "topic-1", "duty_id": "duty-1", "requirement_ids": ["R1"]}],
        requirement_excerpts=[{"requirement_id": "R1"}],
        document_target_constraints=[{"output_target": "chapter-1", "primary_requirement_ids": ["R1"]}],
        prompt_version="test",
        model_config_hash="test",
        bundle_hash="bundle-hash",
    )


def _condition_bundle() -> WriterInputBundle:
    return WriterInputBundle(
        revision=1,
        source_hashes={"T1": "h"},
        bundle_id="bundle-conditions",
        unit_id="unit-conditions",
        source_blueprint_artifact_id="bp-1",
        source_blueprint_revision=1,
        source_blueprint_hash="bp-hash",
        h1_receipt_id="h1",
        blueprint_slice=[{"chapter_id": "chapter-1"}],
        requirement_excerpts=[
            {
                "requirement_id": "R-condition",
                "normalized_requirement": "实施方案应逐项响应技术要求",
            }
        ],
        score_obligations=[
            {
                "score_point_id": "SP-1",
                "review_status": "confirmed",
                "score_conditions": [
                    {
                        "condition_id": "SP-1-C-content",
                        "condition_role": "content",
                        "normalized_condition": "说明实施步骤",
                        "review_status": "confirmed",
                    },
                    {
                        "condition_id": "SP-1-C-evidence",
                        "condition_role": "evidence",
                        "normalized_condition": "提供项目案例证明",
                        "review_status": "confirmed",
                    },
                ],
                "response_units": [
                    {
                        "unit_id": "RU-1",
                        "condition_ids": [
                            "SP-1-C-content",
                            "SP-1-C-evidence",
                        ],
                        "required_evidence_types": ["案例合同"],
                        "linked_requirement_ids": ["R-condition"],
                        "review_status": "confirmed",
                    }
                ],
            }
        ],
        document_target_constraints=[
            {
                "node_id": "chapter-1",
                "output_target": "slot-1",
                "title": "实施与案例证明",
                "primary_requirement_ids": [],
                "primary_response_unit_ids": ["RU-1"],
                "score_condition_ids": [
                    "SP-1-C-content",
                    "SP-1-C-evidence",
                ],
            }
        ],
        prompt_version="test",
        model_config_hash="test",
        bundle_hash="bundle-hash",
    )


class ContentWriterJsonParseTests(unittest.TestCase):
    def test_accepts_raw_newlines_inside_content_string(self) -> None:
        raw = (
            '前言\n'
            '{\n'
            '  "content": "第一段\n第二段",\n'
            '  "used_evidence_ids": ["ev-1"]\n'
            '}\n'
        )
        decoded = ContentWriter._parse_writer_json(raw)
        self.assertEqual(decoded["content"], "第一段\n第二段")
        self.assertEqual(decoded["used_evidence_ids"], ["ev-1"])

    def test_repairs_trailing_commas_and_smart_quotes(self) -> None:
        raw = (
            "{\n"
            '  “content”: “意见建议正文”,\n'
            '  “used_evidence_ids”: [],\n'
            "}\n"
        )
        decoded = ContentWriter._parse_writer_json(raw)
        self.assertEqual(decoded["content"], "意见建议正文")

    def test_salvages_content_when_json_is_severely_broken(self) -> None:
        raw = (
            '说明文字\n'
            '{\n'
            '  "content": "第一段\n第二段仍应保留",\n'
            '  "used_evidence_ids": ["ev-9",]\n'
            # Missing closing brace on purpose.
        )
        decoded = ContentWriter._parse_writer_json(raw)
        self.assertIn("第一段", decoded["content"])
        self.assertIn("第二段仍应保留", decoded["content"])
        self.assertEqual(decoded["used_evidence_ids"], ["ev-9"])


class WriterBundleContentGateTests(unittest.TestCase):
    def test_quality_gate_does_not_require_every_generic_writing_dimension(self) -> None:
        content = (
            "本节围绕系统安全架构设计，说明身份认证、访问控制和日志审计之间的协同关系。"
            "对于业务系统访问，按角色划分权限边界，并将关键操作纳入统一审计范围。"
            "该设计与现有业务流程和部署环境衔接，确保安全措施能够覆盖本章约定的技术范围。"
        )

        findings = content_quality_findings(content)

        self.assertNotIn(
            "CONTENT_LACKS_SUBSTANCE",
            {item["code"] for item in findings},
        )

    def test_rejects_bundle_escape_and_missing_primary_coverage(self) -> None:
        bundle = _bundle()
        with self.assertRaisesRegex(ValueError, "TARGET_OUT_OF_BUNDLE"):
            WriterBundleContentGate().validate(
                bundle,
                [ContentBlock(block_id="b1", target_node_id="other", type="paragraph", content="x", confidence=1, source_bundle_hash="bundle-hash")],
            )
        with self.assertRaisesRegex(ValueError, "PRIMARY_REQUIREMENT_MISSING"):
            WriterBundleContentGate().validate(
                bundle,
                [ContentBlock(block_id="b1", target_node_id="chapter-1", type="paragraph", content="x", confidence=1, source_bundle_hash="bundle-hash")],
            )

    def test_accepts_exact_bundle_scoped_block(self) -> None:
        bundle = _bundle()
        proposal = WriterBundleContentGate().validate(
            bundle,
            [
                ContentBlock(
                    block_id="b1",
                    target_node_id="chapter-1",
                    type="paragraph",
                    content="响应 R1。",
                    confidence=1,
                    requirement_ids=["R1"],
                    topic_ids=["topic-1"],
                    duty_ids=["duty-1"],
                    source_bundle_hash="bundle-hash",
                )
            ],
        )
        self.assertEqual(proposal.bundle_hash, "bundle-hash")

    def test_rejects_visible_score_copy_when_substantive_gate_is_active(self) -> None:
        bundle = _condition_bundle().model_copy(
            update={
                "document_target_constraints": [
                    {
                        **_condition_bundle().document_target_constraints[0],
                        "target_size": 900,
                    }
                ]
            }
        )
        with self.assertRaisesRegex(
            ValueError,
            "SCORE_CONDITION_VISIBLE|SCORE_CONDITION_COPIED",
        ):
            WriterBundleContentGate().validate(
                bundle,
                [
                    ContentBlock(
                        block_id="b1",
                        target_node_id="slot-1",
                        type="paragraph",
                        content=(
                            "满分条件为说明实施步骤，并提供项目案例证明。"
                            "本节围绕项目实施建立计划、执行、复核、整改、验收闭环，"
                            "明确责任人、时间节点、过程记录和交付物清单，保证每项工作"
                            "均能被核验和追溯。质量控制采用阶段评审、过程抽检和问题"
                            "关闭机制，并在验收前汇总测试记录、整改清单、培训记录、"
                            "移交确认和风险复盘结果，形成连续完整的执行证据链。"
                            "项目执行期间还应围绕输入资料、系统配置、接口联调、"
                            "数据核验、安全控制、服务响应和成果归档建立明细台账，"
                            "每周复盘偏差原因并同步更新计划，确保方案内容不是泛化"
                            "描述，而能对应到现场执行、过程检查和最终验收。"
                        ),
                        confidence=1,
                        score_point_ids=["SP-1"],
                        claim_ids=[
                            "SP-1-C-content",
                            "SP-1-C-evidence",
                        ],
                        source_bundle_hash="bundle-hash",
                    )
                ],
            )

    def test_requires_every_target_condition_by_exact_metadata_id(self) -> None:
        bundle = _condition_bundle()
        content_block = ContentBlock(
            block_id="b-content",
            target_node_id="slot-1",
            type="paragraph",
            content="本节说明实施步骤。",
            confidence=1,
            score_point_ids=["SP-1"],
            claim_ids=["SP-1-C-content"],
            source_bundle_hash="bundle-hash",
        )
        with self.assertRaisesRegex(
            ValueError,
            "SCORE_CONDITION_MISSING",
        ):
            WriterBundleContentGate().validate(
                bundle,
                [content_block],
            )

        evidence_block = ContentBlock(
            block_id="b-evidence",
            target_node_id="slot-1",
            type="paragraph",
            content="本节列明案例合同。",
            confidence=1,
            score_point_ids=["SP-1"],
            claim_ids=["SP-1-C-evidence"],
            source_bundle_hash="bundle-hash",
        )
        proposal = WriterBundleContentGate().validate(
            bundle,
            [content_block, evidence_block],
        )
        self.assertEqual(
            proposal.evidence_need_proposals,
            [
                {
                    "proposal_id": (
                        "evidence-bundle-conditions-"
                        "SP-1-C-evidence-1"
                    ),
                    "condition_id": "SP-1-C-evidence",
                    "response_unit_id": "RU-1",
                    "score_point_id": "SP-1",
                    "chapter_id": "chapter-1",
                    "target_node_id": "slot-1",
                    "evidence_type": "案例合同",
                    "status": "required",
                    "source_bundle_hash": "bundle-hash",
                }
            ],
        )

    def test_rejects_condition_metadata_on_the_wrong_target(self) -> None:
        bundle = _condition_bundle().model_copy(
            update={
                "document_target_constraints": [
                    {
                        "node_id": "chapter-1",
                        "output_target": "slot-1",
                        "primary_requirement_ids": [],
                        "score_condition_ids": ["SP-1-C-content"],
                    },
                    {
                        "node_id": "chapter-2",
                        "output_target": "slot-2",
                        "primary_requirement_ids": [],
                        "score_condition_ids": ["SP-1-C-evidence"],
                    },
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "CONDITION_WRONG_TARGET"):
            WriterBundleContentGate().validate(
                bundle,
                [
                    ContentBlock(
                        block_id="b1",
                        target_node_id="slot-1",
                        type="paragraph",
                        content="证明材料。",
                        confidence=1,
                        score_point_ids=["SP-1"],
                        claim_ids=["SP-1-C-evidence"],
                        source_bundle_hash="bundle-hash",
                    )
                ],
            )

    def test_rejects_unbound_active_evidence_condition(self) -> None:
        bundle = _condition_bundle().model_copy(
            update={
                "document_target_constraints": [
                    {
                        "node_id": "chapter-1",
                        "output_target": "slot-1",
                        "primary_requirement_ids": [],
                        "primary_response_unit_ids": ["RU-1"],
                        "score_condition_ids": [
                            "SP-1-C-content"
                        ],
                    }
                ]
            }
        )
        with self.assertRaisesRegex(
            ValueError,
            "EVIDENCE_CONDITION_TARGET_MISSING",
        ):
            WriterBundleContentGate().validate(
                bundle,
                [
                    ContentBlock(
                        block_id="b1",
                        target_node_id="slot-1",
                        type="paragraph",
                        content="说明实施步骤。",
                        confidence=1,
                        score_point_ids=["SP-1"],
                        claim_ids=["SP-1-C-content"],
                        source_bundle_hash="bundle-hash",
                    )
                ],
            )

    def test_rejects_evidence_condition_without_owning_unit_in_bundle(
        self,
    ) -> None:
        """Evidence conditions need a response unit frozen into the bundle."""
        bundle = _condition_bundle().model_copy(
            update={
                "score_obligations": [
                    {
                        "score_point_id": "SP-1",
                        "review_status": "confirmed",
                        "score_conditions": [
                            {
                                "condition_id": "SP-1-C-evidence",
                                "condition_role": "evidence",
                                "normalized_condition": "提供项目案例证明",
                                "review_status": "confirmed",
                            }
                        ],
                        # Condition-only chapter slices used to drop units;
                        # without them G4 cannot emit evidence needs.
                        "response_units": [],
                    }
                ],
                "document_target_constraints": [
                    {
                        "node_id": "chapter-evidence",
                        "output_target": "slot-evidence",
                        "title": "案例证明",
                        "primary_requirement_ids": [],
                        "primary_response_unit_ids": [],
                        "score_condition_ids": ["SP-1-C-evidence"],
                    }
                ],
            }
        )
        with self.assertRaisesRegex(
            ValueError,
            "EVIDENCE_CONDITION_UNIT_MISSING",
        ):
            WriterBundleContentGate().validate(
                bundle,
                [
                    ContentBlock(
                        block_id="b-evidence",
                        target_node_id="slot-evidence",
                        type="paragraph",
                        content="本节列明案例合同。",
                        confidence=1,
                        score_point_ids=["SP-1"],
                        claim_ids=["SP-1-C-evidence"],
                        source_bundle_hash="bundle-hash",
                    )
                ],
            )

    def test_accepts_condition_only_evidence_chapter_when_unit_frozen(
        self,
    ) -> None:
        """Child evidence chapters may omit primary unit ids if the unit is frozen."""
        bundle = _condition_bundle().model_copy(
            update={
                "document_target_constraints": [
                    {
                        "node_id": "chapter-evidence",
                        "output_target": "slot-evidence",
                        "title": "案例证明",
                        "primary_requirement_ids": [],
                        "primary_response_unit_ids": [],
                        "supporting_response_unit_ids": ["RU-1"],
                        "score_condition_ids": ["SP-1-C-evidence"],
                    }
                ],
            }
        )
        proposal = WriterBundleContentGate().validate(
            bundle,
            [
                ContentBlock(
                    block_id="b-evidence",
                    target_node_id="slot-evidence",
                    type="paragraph",
                    content="本节列明案例合同。",
                    confidence=1,
                    score_point_ids=["SP-1"],
                    claim_ids=["SP-1-C-evidence"],
                    source_bundle_hash="bundle-hash",
                )
            ],
        )
        self.assertEqual(
            proposal.evidence_need_proposals,
            [
                {
                    "proposal_id": (
                        "evidence-bundle-conditions-"
                        "SP-1-C-evidence-1"
                    ),
                    "condition_id": "SP-1-C-evidence",
                    "response_unit_id": "RU-1",
                    "score_point_id": "SP-1",
                    "chapter_id": "chapter-evidence",
                    "target_node_id": "slot-evidence",
                    "evidence_type": "案例合同",
                    "status": "required",
                    "source_bundle_hash": "bundle-hash",
                }
            ],
        )

    def test_ignores_sibling_unit_conditions_outside_chapter_slice(
        self,
    ) -> None:
        """A unit frozen for one condition must not require sibling conditions."""
        bundle = _condition_bundle().model_copy(
            update={
                "score_obligations": [
                    {
                        "score_point_id": "SP-1",
                        "review_status": "confirmed",
                        "score_conditions": [
                            {
                                "condition_id": "SP-1-C-evidence",
                                "condition_role": "evidence",
                                "normalized_condition": "提供项目案例证明",
                                "review_status": "confirmed",
                            }
                        ],
                        "response_units": [
                            {
                                "unit_id": "RU-1",
                                # Full unit ownership includes siblings bound
                                # to other chapters; only the local condition
                                # is frozen into score_conditions.
                                "condition_ids": [
                                    "SP-1-C-content",
                                    "SP-1-C-evidence",
                                    "SP-1-C-other",
                                ],
                                "required_evidence_types": ["案例合同"],
                                "linked_requirement_ids": ["R-condition"],
                                "review_status": "confirmed",
                            }
                        ],
                    }
                ],
                "document_target_constraints": [
                    {
                        "node_id": "chapter-evidence",
                        "output_target": "slot-evidence",
                        "title": "案例证明",
                        "primary_requirement_ids": [],
                        "primary_response_unit_ids": [],
                        "supporting_response_unit_ids": ["RU-1"],
                        "score_condition_ids": ["SP-1-C-evidence"],
                    }
                ],
            }
        )
        proposal = WriterBundleContentGate().validate(
            bundle,
            [
                ContentBlock(
                    block_id="b-evidence",
                    target_node_id="slot-evidence",
                    type="paragraph",
                    content="本节列明案例合同。",
                    confidence=1,
                    score_point_ids=["SP-1"],
                    claim_ids=["SP-1-C-evidence"],
                    source_bundle_hash="bundle-hash",
                )
            ],
        )
        self.assertEqual(
            [item["condition_id"] for item in proposal.evidence_need_proposals],
            ["SP-1-C-evidence"],
        )

    def test_writer_uses_each_target_condition_instead_of_overview(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        ) as tmp:
            runs = Path(tmp) / "runs"
            (runs / "writer").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "writer")
            blocks = ContentWriter.for_deterministic_tests(context).write_bundle(
                _condition_bundle()
            )
        self.assertEqual(
            [block.claim_ids for block in blocks],
            [["SP-1-C-content", "SP-1-C-evidence"]],
        )
        self.assertIn("实施方法", blocks[0].content)
        self.assertIn("案例合同", blocks[0].content)
        self.assertNotIn("满分条件", blocks[0].content)
        self.assertEqual(
            [block.requirement_ids for block in blocks],
            [["R-condition"]],
        )
        self.assertNotIn("章节边界组织响应内容", blocks[0].content)


if __name__ == "__main__":
    unittest.main()
