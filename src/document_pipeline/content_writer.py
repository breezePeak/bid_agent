from __future__ import annotations

import json
import re

from control_plane import ControlPlaneError, WorkspaceContext

from .contracts import ContentBlock, WriterInputBundle
from .writer_policy import (
    require_content_quality,
    require_writer_model,
)


_DETERMINISTIC_TEST_AUTHORITY = object()


class ContentWriter:
    """A constrained V3 writer that can only populate existing contract targets."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        _deterministic_test_authority: object | None = None,
    ) -> None:
        self.context = context
        self.root = context.root
        self.deterministic_test = (
            _deterministic_test_authority is _DETERMINISTIC_TEST_AUTHORITY
        )

    @classmethod
    def for_deterministic_tests(
        cls,
        context: WorkspaceContext,
    ) -> "ContentWriter":
        return cls(
            context,
            _deterministic_test_authority=_DETERMINISTIC_TEST_AUTHORITY,
        )

    def _execute_bundle(
        self,
        bundle: WriterInputBundle,
        *,
        operation_id: str = "",
    ) -> list[ContentBlock]:
        """Generate only from a frozen Bundle; this method never reads workspace facts."""
        del operation_id
        if not self.deterministic_test:
            require_writer_model(self.root)
            from .global_project_context import GlobalProjectContextService

            current_global = GlobalProjectContextService(self.context).load()
            bundle_global = dict(bundle.global_project_context or {})
            current_ref = (
                str(current_global.get("global_context_id") or ""),
                int(current_global.get("global_context_revision") or 0),
                str(current_global.get("global_context_hash") or ""),
            )
            bundle_ref = (
                str(bundle_global.get("global_context_id") or ""),
                int(bundle_global.get("global_context_revision") or 0),
                str(bundle_global.get("global_context_hash") or ""),
            )
            if bundle_ref != current_ref:
                raise ControlPlaneError(
                    "GLOBAL_PROJECT_CONTEXT_CONFLICT",
                    "WriterBundle 未绑定当前全局项目事实版本，请重新编译写作单元。",
                    status_code=409,
                    details={"bundle": bundle_ref, "current": current_ref},
                )
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
        parent_chapter_ids = {
            str(item.get("parent_chapter_id") or "")
            for item in bundle.blueprint_slice
            if isinstance(item, dict)
            and str(item.get("parent_chapter_id") or "")
        }

        def _is_leaf_target(target: dict) -> bool:
            if "is_leaf" in target:
                return bool(target.get("is_leaf"))
            target_id = str(
                target.get("node_id") or target.get("output_target") or ""
            )
            return target_id not in parent_chapter_ids

        writable_targets = [
            target
            for target in bundle.document_target_constraints
            if str(target.get("content_policy") or "full") == "full"
            and _is_leaf_target(target)
        ]
        for target in bundle.document_target_constraints:
            if str(target.get("content_policy") or "full") != "full":
                continue
            if not _is_leaf_target(target):
                continue
            target_id = str(target["output_target"])
            title = str(target["title"])
            research_evidence = self._research_evidence_for_target(
                bundle,
                target,
            )
            available_evidence_ids = sorted(
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
            content, used_evidence_ids = self._draft_chapter_content(
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
            grounding_report: dict[str, object] = {}
            if not self.deterministic_test:
                from .content_grounding import ContentGroundingGate

                local_grounding_context = dict(
                    (bundle.chapter_grounding_contexts or {}).get(
                        str(target.get("node_id") or "")
                    )
                    or bundle.chapter_grounding_context
                    or {}
                )

                grounding_blueprint = dict(
                    next(
                        (
                            item
                            for item in bundle.blueprint_slice
                            if isinstance(item, dict)
                            and str(item.get("chapter_id") or "") == target_id
                        ),
                        {},
                    )
                )
                grounding_args = {
                    "global_context": dict(bundle.global_project_context or {}),
                    "chapter": {
                        "chapter_id": target_id,
                        "title": title,
                        "blueprint_node": grounding_blueprint,
                        "chapter_writing_plan": dict(
                            bundle.chapter_writing_plan or {}
                        ),
                    },
                    "requirement_texts": [
                        *(
                            str(
                                item.get("normalized_requirement")
                                or item.get("statement")
                                or ""
                            )
                            for item in (
                                requirements[requirement_id]
                                for requirement_id in all_requirement_ids
                                if requirement_id in requirements
                            )
                        ),
                        *(
                            str(item.get("text") or "")
                            for item in (
                                conditions[condition_id]
                                for condition_id in target_condition_ids
                                if condition_id in conditions
                            )
                        ),
                    ],
                    "chapter_grounding_context": local_grounding_context,
                    "evidence_sources": [
                        {
                            **source,
                            "batch_id": str(item.get("batch_id") or ""),
                            "content": str(item.get("content") or ""),
                        }
                        for item in research_evidence
                        if isinstance(item, dict)
                        for source in item.get("sources") or []
                        if isinstance(source, dict)
                        and str(source.get("evidence_id") or "")
                        in set(used_evidence_ids)
                    ],
                    "require_evidence_use": bool(used_evidence_ids),
                }
                repair_attempted = False
                try:
                    grounding_report = ContentGroundingGate.evaluate(
                        **grounding_args,
                        content=content,
                    )
                except ControlPlaneError as exc:
                    if not self._is_soft_grounding_error(content, exc):
                        raise
                    repaired = self._repair_soft_grounding_failure(
                        content=content,
                        bundle=bundle,
                        target=target,
                        error=exc,
                    )
                    repair_attempted = True
                    grounding_report = ContentGroundingGate.evaluate(
                        **grounding_args,
                        content=repaired,
                    )
                    grounding_report = dict(grounding_report)
                    grounding_report["repair_attempted"] = True
                    grounding_report["repair_succeeded"] = True
                    content = repaired
            if not self.deterministic_test:
                target_requirements = [
                    requirements[requirement_id]
                    for requirement_id in all_requirement_ids
                    if requirement_id in requirements
                ]
                target_conditions = [
                    conditions[condition_id]
                    for condition_id in target_condition_ids
                    if condition_id in conditions
                ]
                try:
                    self._validate_generated_chapter(
                        content,
                        target=target,
                        requirements=target_requirements,
                        conditions=target_conditions,
                    )
                except ControlPlaneError as exc:
                    findings = (
                        exc.details.get("findings")
                        if isinstance(exc.details, dict)
                        else []
                    )
                    soft_too_short = (
                        exc.code == "CONTENT_QUALITY_BLOCKED"
                        and bool(findings)
                        and all(
                            str(item.get("code") or "") == "CONTENT_TOO_SHORT"
                            for item in findings
                            if isinstance(item, dict)
                        )
                    )
                    if repair_attempted or not soft_too_short:
                        raise
                    content = self._repair_soft_grounding_failure(
                        content=content,
                        bundle=bundle,
                        target=target,
                        error=exc,
                    )
                    repair_attempted = True
                    grounding_report = ContentGroundingGate.evaluate(
                        **grounding_args,
                        content=content,
                    )
                    grounding_report = dict(grounding_report)
                    grounding_report["repair_attempted"] = True
                    grounding_report["repair_succeeded"] = True
                    self._validate_generated_chapter(
                        content,
                        target=target,
                        requirements=target_requirements,
                        conditions=target_conditions,
                    )
            if not set(used_evidence_ids).issubset(
                set(available_evidence_ids)
            ):
                raise ControlPlaneError(
                    "CONTENT_EVIDENCE_BINDING_INVALID",
                    "写作模型声明使用了当前章节未授权的公开证据。",
                    details={
                        "unit_id": bundle.unit_id,
                        "chapter_id": str(target.get("node_id") or ""),
                    },
                )
            if research_evidence and not used_evidence_ids:
                raise ControlPlaneError(
                    "CONTENT_EVIDENCE_USE_REQUIRED",
                    "当前章节的必要公开检索已发布，但写作模型未声明使用任何证据。",
                    details={
                        "unit_id": bundle.unit_id,
                        "chapter_id": str(target.get("node_id") or ""),
                    },
                )
            from .chapter_editing import split_text_into_blocks

            # Phase 3: AI output is multiple blocks (paragraph/list/table), not one chapter string.
            split_payloads = split_text_into_blocks(
                content,
                chapter_id=target_id,
                actor_id="writer",
                source="AI_GENERATED",
                confidence=0.82,
                source_bundle_hash=bundle.bundle_hash,
            )
            if not split_payloads:
                split_payloads = [
                    {
                        "block_id": f"{bundle.bundle_id}-{target_id}-chapter",
                        "target_node_id": target_id,
                        "type": "paragraph",
                        "content": content,
                        "order": 0,
                        "source": "AI_GENERATED",
                        "confidence": 0.82,
                        "source_bundle_hash": bundle.bundle_hash,
                    }
                ]
            for payload in split_payloads:
                paragraph_index = int(payload.get("order") or 0)
                fact_bindings = (
                    grounding_report.get("paragraph_fact_bindings")
                    if isinstance(grounding_report, dict)
                    else {}
                )
                fact_bindings = (
                    fact_bindings if isinstance(fact_bindings, dict) else {}
                )
                blocks.append(
                    ContentBlock(
                        block_id=str(payload["block_id"]),
                        target_node_id=target_id,
                        type=payload.get("type") or "paragraph",
                        content=str(payload["content"]),
                        topic_ids=sorted(topic_ids),
                        duty_ids=sorted(duty_ids),
                        requirement_ids=all_requirement_ids,
                        score_point_ids=sorted(score_ids),
                        evidence_ids=used_evidence_ids,
                        fact_ids=list(
                            fact_bindings.get(str(paragraph_index)) or []
                        ),
                        claim_ids=target_condition_ids,
                        confidence=float(payload.get("confidence") or 0.82),
                        source_bundle_hash=bundle.bundle_hash,
                        source="AI_GENERATED",
                        order=int(payload.get("order") or 0),
                        created_by="writer",
                        updated_by="writer",
                        created_at=str(payload.get("created_at") or ""),
                        updated_at=str(payload.get("updated_at") or ""),
                        lock_state="UNLOCKED",
                        human_locked=False,
                    )
                )
        if not blocks:
            raise ValueError("CONTENT_BLOCKED: WriterBundle 不包含可生成的章节目标")
        return blocks

    def stream_bundle(
        self,
        bundle: WriterInputBundle,
        *,
        operation_id: str = "",
    ) -> list[ContentBlock]:
        """Run the sole content-model kernel on an already frozen Bundle.

        Research planning and chapter revision persistence belong to
        ``ChapterWritingService``.  This is the only public model boundary.
        """
        return self._execute_bundle(
            bundle,
            operation_id=operation_id,
        )

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
            and (
                str(target.get("node_id") or "")
                in {
                    str(target_id)
                    for target_id in (item.get("target_ids") or [])
                }
                or str(item.get("topic_id") or "") in topics
            )
        ]

    def _draft_chapter_content(
        self,
        *,
        bundle: WriterInputBundle,
        target: dict,
        requirements: list[dict],
        conditions: list[dict],
        response_units: list[dict],
        research_evidence: list[dict] | None = None,
    ) -> tuple[str, list[str]]:
        title = str(target.get("title") or "本章")
        chapter_grounding_context = dict(
            (bundle.chapter_grounding_contexts or {}).get(
                str(target.get("node_id") or "")
            )
            or bundle.chapter_grounding_context
            or {}
        )
        blueprint_node = next(
            (
                item
                for item in bundle.blueprint_slice
                if isinstance(item, dict)
                and str(item.get("chapter_id") or "")
                == str(target.get("node_id") or "")
            ),
            {},
        )
        project = bundle.global_project_context or bundle.project_context or {}
        if not self.deterministic_test:
            from .global_project_context import GlobalProjectContextService

            project = GlobalProjectContextService.prompt_projection(
                dict(project),
                chapter_grounding_context,
                purpose=str(blueprint_node.get("purpose") or ""),
                writing_objectives=list(blueprint_node.get("writing_objectives") or []),
                scoring_requirements=[
                    item for item in bundle.score_obligations if isinstance(item, dict)
                ],
            )
        available_evidence_ids = sorted(
            {
                str(evidence_id)
                for item in (research_evidence or [])
                for evidence_id in (item.get("evidence_ids") or [])
                if str(evidence_id)
            }
        )
        from .chapter_writing_kernel import (
            ChapterWritingRequest,
            compile_chapter_writing_messages,
            compile_chapter_writing_spec,
        )

        tender_requirements = tuple(
            {
                "requirement_id": item.get("requirement_id"),
                "text": item.get("normalized_requirement") or item.get("statement"),
            }
            for item in requirements
        )
        scoring_requirements = tuple(
            {
                "score_point_id": item.get("score_point_id"),
                "title": item.get("title"),
                "response_expectation": item.get("response_expectation"),
                "conditions": list(item.get("score_conditions") or []),
                "response_units": list(item.get("response_units") or []),
            }
            for item in bundle.score_obligations
            if isinstance(item, dict)
        )
        spec = compile_chapter_writing_spec(
            ChapterWritingRequest(
                chapter_id=str(target.get("node_id") or ""),
                operation=bundle.operation,
                user_instruction=bundle.user_instruction,
                existing_content=bundle.existing_content,
                chapter={
                    "chapter_id": str(target.get("node_id") or ""),
                    "title": title,
                    "blueprint_node": blueprint_node,
                },
                tender_requirements=tender_requirements,
                scoring_requirements=scoring_requirements,
                binding_requirements=tuple(
                    [*tender_requirements, *scoring_requirements]
                ),
                project_context=project,
                history=tuple(bundle.chapter_dialogue or []),
                writing_plan=dict(bundle.chapter_writing_plan or {}),
                chapter_context={
                    **chapter_grounding_context,
                    "chapter_context_items": list(bundle.chapter_context_items or []),
                    "response_units": list(response_units),
                    "research_evidence": list(research_evidence or []),
                },
            )
        )
        if not self.deterministic_test:
            try:
                from llm_client import chat

                content = chat(
                    compile_chapter_writing_messages(spec),
                    temperature=0.25,
                ).strip()
                if not content:
                    raise ValueError("Writer 输出正文为空")
                return content, available_evidence_ids
            except ControlPlaneError:
                raise
            except Exception as exc:
                raise ControlPlaneError(
                    "WRITER_MODEL_ACTION_REQUIRED",
                    (
                        "写作模型未返回项目化正文，生成已在当前章节暂停；"
                        "请检查模型配置后重试该章节，不会使用模板回退。"
                    ),
                    retryable=True,
                    details={
                        "unit_id": bundle.unit_id,
                        "chapter_id": str(target.get("node_id") or ""),
                        "chapter_title": title,
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                    },
                ) from exc

        blocks = list(spec.writing_outline.get("blocks") or [])
        deterministic_content = "；".join(
            str(block.get("must_answer") or "").strip()
            for block in blocks
            if str(block.get("must_answer") or "").strip()
        )
        if not deterministic_content:
            deterministic_content = spec.purpose or "；".join(spec.writing_objectives)
        deterministic_content += (
            "。正文仅依据本章绑定的招标要求、写作块、章节上下文和已确认项目事实展开；"
            "每项陈述说明适用对象、前提条件、执行边界、判断依据和可核验结果，并保持"
            "与原始要求的对应关系。未在写作块中明确要求的采购人职责、实施步骤、人员安排、"
            "交付物、验收条件及承诺不作扩展；资料不足之处保留为待确认项，不以通用模板、"
            "其他章节内容或历史回复补齐。所有内容按照当前目标组织，避免复述内部规则，"
            "避免引入与本章目的无关的工作，并确保最终文字能够追溯到已提供的依据。"
        )
        return deterministic_content, available_evidence_ids

    @staticmethod
    def _is_soft_grounding_error(
        content: str,
        error: ControlPlaneError,
    ) -> bool:
        if error.code == "PROJECT_SPECIFICITY_MISSING":
            return True
        if error.code == "WRITING_PLAN_COVERAGE_INCOMPLETE":
            return True
        if error.code != "CHAPTER_GOAL_MISALIGNED":
            return False
        details = error.details if isinstance(error.details, dict) else {}
        alignment = details.get("goal_alignment")
        alignment = alignment if isinstance(alignment, dict) else {}
        off_goal = {
            int(item)
            for item in alignment.get("off_goal_paragraphs") or []
            if isinstance(item, int) and item >= 0
        }
        paragraph_count = len(
            [item for item in re.split(r"\n\s*\n", content) if item.strip()]
        )
        # One isolated drifting paragraph is repairable. Predominant or
        # multi-paragraph cross-chapter content remains a hard block.
        return bool(off_goal) and len(off_goal) == 1 and paragraph_count >= 2

    @staticmethod
    def _repair_soft_grounding_failure(
        *,
        content: str,
        bundle: WriterInputBundle,
        target: dict,
        error: ControlPlaneError,
    ) -> str:
        """Repair one soft coverage/specificity failure inside ContentWriter."""
        from llm_client import chat

        blueprint_node = next(
            (
                item
                for item in bundle.blueprint_slice
                if isinstance(item, dict)
                and str(item.get("chapter_id") or "")
                == str(target.get("node_id") or "")
            ),
            {},
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是同一 ContentWriter 内部的一次性正文修复步骤。只修复内容太空、"
                    "目标不具体、WritingPlan 覆盖不完整或轻微偏题。删除超出章节目的的展开，"
                    "补齐 WritingPlan 未回答内容；保持已授权项目事实，不得新增事实、企业能力、"
                    "指标、任务或承诺。只输出修复后的正文。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "chapter_goal": {
                            "purpose": blueprint_node.get("purpose"),
                            "writing_objectives": blueprint_node.get("writing_objectives") or [],
                        },
                        "chapter_writing_plan": bundle.chapter_writing_plan,
                        "gate_error": {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details,
                        },
                        "current_content": content,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        repaired = str(chat(messages, temperature=0.15) or "").strip()
        if not repaired:
            raise error
        alignment = (
            error.details.get("goal_alignment")
            if isinstance(error.details, dict)
            else {}
        )
        off_goal = {
            int(index)
            for index in (alignment or {}).get("off_goal_paragraphs") or []
            if str(index).isdigit()
        }
        if off_goal:
            paragraphs = [
                item.strip()
                for item in re.split(r"\n\s*\n", repaired)
                if item.strip()
            ]
            retained = [
                paragraph
                for index, paragraph in enumerate(paragraphs)
                if index not in off_goal
            ]
            if retained:
                repaired = "\n\n".join(retained)
        return repaired

    @staticmethod
    def _validate_generated_chapter(
        content: str,
        *,
        target: dict,
        requirements: list[dict],
        conditions: list[dict],
    ) -> None:
        source_texts = [
            str(
                item.get("normalized_requirement")
                or item.get("statement")
                or ""
            )
            for item in requirements
        ]
        source_texts.extend(
            str(
                item.get("normalized_condition")
                or item.get("text")
                or ""
            )
            for item in conditions
        )
        require_content_quality(content, source_texts=source_texts)
