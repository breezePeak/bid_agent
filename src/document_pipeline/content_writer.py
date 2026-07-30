from __future__ import annotations

from pathlib import Path
import os
import json
import re

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import ContentBlock, DOCUMENT_CONTRACT_ADAPTER, DocumentPlan, TemplateContract
from .document_contract import DOCUMENT_CONTRACT_PATH
from .document_planner import DOCUMENT_PLAN_PATH
from .requirement_ledger import load_promoted_requirement_ledger
from .contracts import RequirementLedger
from .input_manifest import V3_ROOT
from .contracts import WriterInputBundle
from .content_gate import WriterBundleContentGate


CONTENT_OUTPUT_DIR = V3_ROOT / "content_units"


class ContentWriter:
    """A constrained V3 writer that can only populate existing contract targets."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def write(self, unit_id: str, node_ids: list[str]) -> list[ContentBlock]:
        raise ValueError("WRITER_BUNDLE_REQUIRED: Writer 只能接收由确认 Blueprint 编译的 WriterInputBundle")

    def write_bundle(self, bundle: WriterInputBundle) -> list[ContentBlock]:
        """Generate only from a frozen Bundle; this method never reads workspace facts."""
        requirements = {
            str(item["requirement_id"]): item
            for item in bundle.requirement_excerpts
            if isinstance(item, dict) and item.get("requirement_id")
        }
        scores = {
            str(item["score_point_id"]): item
            for item in bundle.score_obligations
            if isinstance(item, dict) and item.get("score_point_id")
        }
        conditions: dict[str, dict] = {}
        condition_score_ids: dict[str, str] = {}
        condition_units: dict[str, dict] = {}
        for score_id, score in scores.items():
            for condition in score.get("score_conditions", []):
                if not isinstance(condition, dict) or not condition.get(
                    "condition_id"
                ):
                    continue
                condition_id = str(condition["condition_id"])
                conditions[condition_id] = condition
                condition_score_ids[condition_id] = score_id
            for unit in score.get("response_units", []):
                if not isinstance(unit, dict):
                    continue
                for condition_id_value in unit.get(
                    "condition_ids",
                    [],
                ):
                    condition_units[str(condition_id_value)] = unit
        blocks: list[ContentBlock] = []
        for target in bundle.document_target_constraints:
            if str(target.get("content_policy") or "full") != "full":
                continue
            target_id = str(target["output_target"])
            title = str(target["title"])
            research_evidence = self._research_evidence_for_target(
                bundle,
                target,
            )
            used_evidence_ids = sorted(
                {
                    str(evidence_id)
                    for item in research_evidence
                    for evidence_id in item.get("evidence_ids", [])
                    if str(evidence_id).strip()
                }
            )
            requirement_ids = [
                str(item)
                for item in target.get("primary_requirement_ids", [])
                if str(item) in requirements
            ]
            target_condition_ids = [
                str(item)
                for item in target.get("score_condition_ids", [])
            ]
            missing_conditions = [
                condition_id
                for condition_id in target_condition_ids
                if condition_id not in conditions
                or not condition_score_ids.get(condition_id)
            ]
            if missing_conditions:
                raise ValueError(
                    "CONTENT_BLOCKED: 章节绑定的满分条件未包含在 "
                    f"WriterBundle 中: {missing_conditions}"
                )
            condition_requirement_ids: set[str] = set()
            topic_ids: set[str] = set()
            duty_ids: set[str] = set()
            score_ids: set[str] = set()
            for condition_id in target_condition_ids:
                unit = condition_units.get(condition_id, {})
                if condition_score_ids.get(condition_id):
                    score_ids.add(condition_score_ids[condition_id])
                for requirement_id in unit.get("linked_requirement_ids", []):
                    requirement_id = str(requirement_id)
                    if requirement_id in requirements:
                        condition_requirement_ids.add(requirement_id)
            all_requirement_ids = list(
                dict.fromkeys(
                    [
                        *requirement_ids,
                        *sorted(condition_requirement_ids),
                    ]
                )
            )
            for requirement_id in all_requirement_ids:
                for item in bundle.topic_and_duty_slice:
                    if requirement_id not in item.get("requirement_ids", []):
                        continue
                    if item.get("topic_id"):
                        topic_ids.add(str(item.get("topic_id")))
                    if item.get("duty_id"):
                        duty_ids.add(str(item.get("duty_id")))
                for score_id, score in scores.items():
                    if requirement_id in score.get("linked_requirement_ids", []):
                        score_ids.add(score_id)
            content = self._draft_chapter_content(
                bundle=bundle,
                target=target,
                requirements=[
                    requirements[requirement_id]
                    for requirement_id in all_requirement_ids
                    if requirement_id in requirements
                ],
                conditions=[
                    conditions[condition_id]
                    for condition_id in target_condition_ids
                    if condition_id in conditions
                ],
                response_units=[
                    condition_units[condition_id]
                    for condition_id in target_condition_ids
                    if condition_id in condition_units
                ],
                research_evidence=research_evidence,
            )
            self._validate_generated_chapter(
                content,
                target=target,
                conditions=[
                    conditions[condition_id]
                    for condition_id in target_condition_ids
                    if condition_id in conditions
                ],
            )
            blocks.append(
                ContentBlock(
                    block_id=f"{bundle.bundle_id}-{target['node_id']}-chapter",
                    target_node_id=target_id,
                    type="paragraph",
                    content=content,
                    topic_ids=sorted(topic_ids),
                    duty_ids=sorted(duty_ids),
                    requirement_ids=all_requirement_ids,
                    score_point_ids=sorted(score_ids),
                    evidence_ids=used_evidence_ids,
                    claim_ids=target_condition_ids,
                    confidence=0.82,
                    source_bundle_hash=bundle.bundle_hash,
                )
            )
        if not blocks:
            raise ValueError("CONTENT_BLOCKED: WriterBundle 不包含可生成的章节目标")
        proposal = WriterBundleContentGate().validate(bundle, blocks)
        output = self.root / CONTENT_OUTPUT_DIR / f"{bundle.unit_id}.json"
        write_json(output, {"schema_version": "v3", "unit_id": bundle.unit_id, "bundle_id": bundle.bundle_id, "content_proposal": proposal.model_dump(mode="json"), "blocks": [block.model_dump(mode="json") for block in blocks]})
        self.store.upsert_content_unit_state(
            {
                "unit_id": bundle.unit_id,
                "contract_revision": bundle.revision,
                "state": "completed",
                "evidence_snapshot_hash": bundle.bundle_hash,
                "output_artifact_id": output.relative_to(self.root).as_posix(),
            }
        )
        return blocks

    @staticmethod
    def _writer_llm_enabled() -> bool:
        return str(
            os.environ.get("V3_WRITER_LLM_ENABLED", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _research_evidence_for_target(
        bundle: WriterInputBundle,
        target: dict,
    ) -> list[dict]:
        topics = {
            f"chapter:{target.get('node_id')}",
            *(
                f"score:{item}"
                for item in target.get("score_point_ids", [])
            ),
            *(
                f"requirement:{item}"
                for item in target.get("primary_requirement_ids", [])
            ),
        }
        return [
            item
            for item in bundle.evidence_snapshot
            if isinstance(item, dict)
            and str(item.get("topic_id") or "") in topics
        ]

    @staticmethod
    def _research_clause(research_evidence: list[dict] | None) -> str:
        """Create a short traceable method reference for deterministic writing."""

        items = research_evidence or []
        if not items:
            return ""
        first = items[0]
        content = re.sub(
            r"\s+",
            " ",
            str(first.get("content") or ""),
        ).strip()
        content = re.sub(r"[#*`]+", "", content)
        if len(content) > 260:
            content = content[:260].rstrip("，,；;。 ") + "…"
        sources = [
            source
            for source in first.get("sources", [])
            if isinstance(source, dict) and source.get("source_url")
        ]
        if not content or not sources:
            return ""
        source = sources[0]
        title = re.sub(
            r"\s+",
            " ",
            str(source.get("title") or source.get("publisher") or "公开资料"),
        ).strip()
        return (
            f" 结合内部固化的公开研究资料《{title}》，本节采用的"
            f"可核验方法参考为：{content}。"
        )

    @staticmethod
    def _short_items(values: list[object], limit: int = 4) -> list[str]:
        result: list[str] = []
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if not text:
                continue
            if len(text) > 120:
                text = text[:120].rstrip("，,；;。 ") + "…"
            if text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _draft_chapter_content(
        *,
        bundle: WriterInputBundle,
        target: dict,
        requirements: list[dict],
        conditions: list[dict],
        response_units: list[dict],
        research_evidence: list[dict] | None = None,
    ) -> str:
        title = str(target.get("title") or "本章")
        target_size = int(target.get("target_size") or 900)
        objectives = []
        for item in bundle.blueprint_slice:
            if (
                isinstance(item, dict)
                and str(item.get("chapter_id") or "")
                == str(target.get("node_id") or "")
            ):
                objectives.extend(ContentWriter._short_items([item.get("purpose")], 1))
                objectives.extend(ContentWriter._short_items(item.get("writing_objectives") or [], 3))
        requirement_points = ContentWriter._short_items(
            [item.get("normalized_requirement") or item.get("statement") for item in requirements],
            5,
        )
        requirement_points = [
            cleaned
            for item in requirement_points
            if not re.search(r"满分条件|得分点", item)
            for cleaned in [
                re.sub(r"评分(?:要求|标准)?[:：]?", "", item).strip()
            ]
            if cleaned
        ]
        intents = ContentWriter._short_items(
            [
                item.get("response_intent")
                for item in conditions
            ],
            5,
        )
        expectations = ContentWriter._short_items(
            [item.get("response_expectation") for item in response_units],
            4,
        )
        evidence_types = ContentWriter._short_items(
            [
                evidence_type
                for item in response_units
                for evidence_type in (item.get("required_evidence_types") or [])
            ],
            5,
        )
        project = bundle.project_context or {}
        project_name = str(
            (project.get("identity") or {}).get("project_name")
            or (project.get("identity") or {}).get("项目名称")
            or (project.get("identity") or {}).get("project")
            or "本项目"
        )
        scope = ContentWriter._short_items(project.get("scope") or [], 3)
        background = ContentWriter._short_items(project.get("background") or [], 2)
        research_clause = ContentWriter._research_clause(research_evidence)
        if ContentWriter._writer_llm_enabled():
            from llm_client import chat

            prompt_bundle = {
                "chapter_title": title,
                "target_size": target_size,
                "project_context": project,
                "chapter_objectives": objectives,
                "tender_requirement_excerpts": requirement_points,
                "internal_coverage_intents": intents,
                "response_expectations": expectations,
                "required_evidence_types": evidence_types,
                "allowed_research_evidence": research_evidence or [],
                "prohibited_visible_text": [
                    "满分条件",
                    "评分要求",
                    "评分标准",
                    "得分点",
                    "full_score",
                ],
            }
            content = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是技术标书正文写作器。按用户给定的章节目标、项目上下文、"
                            "招标摘录和内部覆盖意图，输出完整项目化正文。"
                            "评分条件只能作为内部验收规则，不得直接或改写后写入正文；"
                            "不得出现“满分条件”“评分要求”“评分标准”等表述。"
                            "公开研究资料只用于项目背景、标准、方法和风险控制，不在正文展示 URL。"
                            "禁止虚构企业资质、人员、业绩、财务、报价或承诺。"
                            "正文应包含项目理解、设计依据、总体思路、实施方法、关键流程、"
                            "保障措施、风险控制和验收方法中与本章语义相符的内容。"
                            "仅输出正文，不要 Markdown 标题。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt_bundle, ensure_ascii=False)},
                ],
                temperature=0.25,
            ).strip()
            if not content:
                raise ValueError("CONTENT_WRITER_EMPTY_RESPONSE")
            return content

        sections = [
            (
                f"本章围绕{project_name}的“{title}”展开。"
                f"{'项目背景显示，' + '；'.join(background) + '。' if background else ''}"
                f"{'本节响应的建设范围包括' + '、'.join(scope) + '。' if scope else ''}"
                "写作边界以已确认招标文件、项目模型和内部覆盖规则为准，不外推企业能力、报价或未提供承诺。"
            ),
            (
                "总体思路上，本章把需求拆解为目标确认、资源准备、过程控制、成果校核和验收闭环五类任务。"
                f"{'重点落实：' + '；'.join(requirement_points) + '。' if requirement_points else ''}"
                f"{'章节目标包括：' + '；'.join(objectives) + '。' if objectives else ''}"
            ),
            (
                "实施方法上，先建立需求台账和接口清单，明确输入资料、责任边界、交付节奏和评审节点；"
                "再按阶段形成方案设计、配置实施、联调验证、问题整改和移交培训记录，保证每项工作都有负责人、时间点、产物和复核依据。"
                f"{'本章内部覆盖重点为：' + '；'.join(intents) + '。' if intents else ''}"
            ),
            (
                "质量保障上，采用计划评审、过程抽检、成果复核和问题闭环四道控制线；"
                "对关键配置、接口、数据、安全、服务响应等内容建立可追溯记录，发现偏差时先定位影响范围，再制定整改措施并复测确认。"
                f"{'执行期需满足：' + '；'.join(expectations) + '。' if expectations else ''}"
                f"{'需形成或核验的材料包括：' + '、'.join(evidence_types) + '。' if evidence_types else ''}"
                f"{research_clause}"
            ),
            (
                "风险控制上，对资料缺口、现场条件变化、跨单位协同、系统联调失败和验收口径不一致等风险设置预警规则。"
                "验收时以招标要求、确认后的实施记录、测试结果、培训签到、问题关闭单和交付清单共同作为依据，确保正文承诺能够落到可检查的执行证据。"
            ),
        ]
        desired = max(5, min(9, target_size // 260))
        return "\n\n".join(sections[:desired])

    @staticmethod
    def _validate_generated_chapter(
        content: str,
        *,
        target: dict,
        conditions: list[dict],
    ) -> None:
        compact = re.sub(r"\s+", "", content)
        if len(compact) < 180:
            raise ValueError("CONTENT_BLOCKED: Writer 正文异常短，未形成完整章节")
        forbidden = [
            "满分条件",
            "评分要求",
            "评分标准规定",
            "得分点",
        ]
        if any(token in content for token in forbidden):
            raise ValueError("CONTENT_BLOCKED: Writer 正文复述了评分条件")
        paragraphs = [
            re.sub(r"\s+", "", item)
            for item in re.split(r"\n{2,}", content)
            if item.strip()
        ]
        if len(paragraphs) != len(set(paragraphs)):
            raise ValueError("CONTENT_BLOCKED: Writer 正文存在重复段落")
        generic_tokens = ("本节用于", "章节边界组织响应内容", "待补充", "根据实际情况")
        if any(token in content for token in generic_tokens):
            raise ValueError("CONTENT_BLOCKED: Writer 正文仍为空洞占位")
        for condition in conditions:
            source = str(
                condition.get("normalized_condition")
                or condition.get("text")
                or ""
            ).strip()
            if source and re.sub(r"\s+", "", source) in compact:
                raise ValueError("CONTENT_BLOCKED: Writer 正文机械抄写评分条件")

    @staticmethod
    def _draft_requirement_content(
        bundle: WriterInputBundle,
        title: str,
        requirement: dict,
        *,
        research_evidence: list[dict] | None = None,
    ) -> str:
        """Use an optional model only with the frozen Bundle; otherwise emit a traceable draft."""
        statement = str(requirement["normalized_requirement"])
        if not ContentWriter._writer_llm_enabled():
            return (
                f"{title}：围绕“{statement}”，本节按招标文件明确的响应范围、实施动作与验收要求组织方案，"
                "并在执行过程中保留可核验的过程记录和交付依据。"
                + ContentWriter._research_clause(research_evidence)
            )
        from llm_client import chat

        prompt_bundle = {
            "chapter_title": title,
            "requirement": requirement,
            "allowed_constraints": bundle.project_constraints,
            "terminology": bundle.terminology,
            "allowed_score_obligations": bundle.score_obligations,
            "allowed_targets": bundle.document_target_constraints,
            "allowed_research_evidence": research_evidence or [],
        }
        content = chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是投标章节写作器。只能使用用户消息提供的 WriterInputBundle 内容；"
                        "不得新增标题、项目事实、企业资质、人员、案例、金额、工期或未给出的承诺。"
                        "公开研究资料只能用于方案方法、标准和风险控制，并应保留来源 URL；"
                        "不得用其证明本企业能力。"
                        "缺少事实时写成待补证据的条件性表述。仅输出一个完整正文段落，不要 Markdown 标题或解释。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_bundle, ensure_ascii=False)},
            ],
            temperature=0.2,
        ).strip()
        if not content:
            raise ValueError("CONTENT_WRITER_EMPTY_RESPONSE")
        return content

    @staticmethod
    def _draft_condition_content(
        *,
        bundle: WriterInputBundle,
        title: str,
        condition: dict,
        response_unit: dict,
        related_requirements: list[dict],
        research_evidence: list[dict] | None = None,
    ) -> str:
        """Draft one source-bound full-score condition, never an overview."""

        normalized = str(
            condition.get("normalized_condition")
            or condition.get("text")
            or ""
        ).strip()
        if not normalized:
            raise ValueError(
                "CONTENT_BLOCKED: ScoreCondition 缺少可写条件内容"
            )
        role = str(condition.get("condition_role") or "content")
        intent = str(
            condition.get("response_intent") or normalized
        ).strip()
        expectation = str(
            response_unit.get("response_expectation") or ""
        ).strip()
        evidence_types = [
            str(item)
            for item in response_unit.get(
                "required_evidence_types",
                [],
            )
            if str(item).strip()
        ]
        if not ContentWriter._writer_llm_enabled():
            role_instruction = {
                "content": (
                    f"围绕“{intent}”展开具体响应内容、实施动作与交付结果"
                ),
                "evidence": (
                    "列明证明材料的名称、对应事项和核验位置"
                ),
                "constraint": (
                    "将该条件作为本节方案范围、参数和适用对象的边界"
                ),
                "quality": (
                    "将该条件作为本节完整性、可行性和针对性的写作目标"
                ),
            }.get(
                role,
                f"围绕“{intent}”组织可检查的响应内容",
            )
            evidence_clause = (
                f"，所需证明材料包括：{'、'.join(evidence_types)}"
                if evidence_types
                else ""
            )
            expectation_clause = (
                f"，并落实得分任务“{expectation}”"
                if expectation
                else ""
            )
            return (
                f"{title}：满分条件为“{normalized}”。本节将"
                f"{role_instruction}{expectation_clause}"
                f"{evidence_clause}。"
                + ContentWriter._research_clause(research_evidence)
            )

        from llm_client import chat

        prompt_bundle = {
            "chapter_title": title,
            "score_condition": condition,
            "response_unit": response_unit,
            "related_requirement_excerpts": related_requirements,
            "allowed_constraints": bundle.project_constraints,
            "terminology": bundle.terminology,
            "allowed_research_evidence": research_evidence or [],
        }
        content = chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是投标章节写作器。只能使用用户消息提供的"
                        "满分条件、得分任务和需求原文；必须实质响应"
                        "score_condition.normalized_condition，并落实"
                        "required_evidence_types。不得新增项目事实、企业"
                        "资质、人员、案例、金额、工期或未给出的承诺。"
                        "公开研究资料只能用于方案方法、标准和风险控制，"
                        "不得用其证明本企业能力。"
                        "缺少企业事实时写成待补证据的条件性表述。仅输出"
                        "一个完整正文段落，不要 Markdown 标题或解释。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        prompt_bundle,
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.2,
        ).strip()
        if not content:
            raise ValueError("CONTENT_WRITER_EMPTY_RESPONSE")
        # Keep the exact, source-bound condition visible without relying on
        # character-similarity checks against free-form model prose.
        return f"满分条件“{normalized}”。{content}"
