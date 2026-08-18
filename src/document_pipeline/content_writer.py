from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import write_json

from .contracts import ContentBlock, WriterInputBundle
from .content_gate import WriterBundleContentGate
from .canonicalization import canonical_hash
from .input_manifest import V3_ROOT
from .writer_policy import (
    WRITER_IMPLEMENTATION_VERSION,
    WRITER_PROMPT_VERSION,
    evidence_bindings,
    require_content_quality,
    require_writer_model,
    writer_base_fingerprint,
    writer_fingerprint,
)
from .writer_research import WriterResearchCoordinator


CONTENT_OUTPUT_DIR = V3_ROOT / "content_units"
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
        self.store = ControlStore(context)
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

    def write(self, unit_id: str, node_ids: list[str]) -> list[ContentBlock]:
        raise ValueError("WRITER_BUNDLE_REQUIRED: Writer 只能接收由确认 Blueprint 编译的 WriterInputBundle")

    def write_bundle(
        self,
        bundle: WriterInputBundle,
        *,
        operation_id: str = "",
        enable_writer_research: bool = False,
    ) -> list[ContentBlock]:
        """Generate only from a frozen Bundle; this method never reads workspace facts."""
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
        if writable_targets:
            first_target = writable_targets[0]
            self.store.update_content_unit_progress(
                bundle.unit_id,
                chapter_id=str(first_target.get("output_target") or ""),
                chapter_title=str(first_target.get("title") or ""),
                phase="preparing_research",
            )
        researcher = WriterResearchCoordinator(
            self.context,
            operation_id=operation_id,
            deterministic_test=self.deterministic_test,
        )
        research_decision: dict[str, object] = {}
        if enable_writer_research:
            research_decision, dynamic_evidence = researcher.resolve_for_bundle(
                bundle
            )
            if dynamic_evidence:
                bundle = self._bundle_with_research(
                    bundle,
                    evidence=[*bundle.evidence_snapshot, *dynamic_evidence],
                    decisions=[
                        *bundle.research_decisions,
                        research_decision,
                    ],
                )
        for target in bundle.document_target_constraints:
            if str(target.get("content_policy") or "full") != "full":
                continue
            if not _is_leaf_target(target):
                continue
            target_id = str(target["output_target"])
            title = str(target["title"])
            self.store.update_content_unit_progress(
                bundle.unit_id,
                chapter_id=target_id,
                chapter_title=title,
                phase="drafting",
            )
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

                grounding_report = ContentGroundingGate.evaluate(
                    global_context=dict(bundle.global_project_context or {}),
                    chapter={
                        "chapter_id": target_id,
                        "title": title,
                        "blueprint_node": next(
                            (
                                item
                                for item in bundle.blueprint_slice
                                if isinstance(item, dict)
                                and str(item.get("chapter_id") or "") == target_id
                            ),
                            {},
                        ),
                    },
                    content=content,
                    requirement_texts=[
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
                    chapter_grounding_context=local_grounding_context,
                    evidence_sources=[
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
                    require_evidence_use=bool(used_evidence_ids),
                )
            if not self.deterministic_test:
                self._validate_generated_chapter(
                    content,
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
            # Phase 7: if chapter workspace exists, also append a draft content revision
            # (never formal). Locked blocks are preserved by the merge service.
            if self.store.chapter_workspace(target_id) is not None:
                from .chapter_editing import ChapterEditingService

                workspace = self.store.chapter_workspace(target_id) or {}
                ChapterEditingService(self.context).generate_draft(
                    chapter_id=target_id,
                    expected_chapter_revision=int(
                        workspace.get("chapter_revision") or 0
                    ),
                    text=content,
                    overwrite_locked=False,
                    grounding_report=grounding_report,
                    actor={"type": "system", "id": "writer", "role": "writer"},
                )
            # This is an execution checkpoint only.  It lets the workspace show
            # the durable part of a running draft without promoting it to the
            # final Word artifact before the unit-level quality gate passes.
            self.store.update_content_unit_progress(
                bundle.unit_id,
                chapter_id=target_id,
                chapter_title=title,
                phase="drafted_checkpoint",
                draft_preview="\n\n".join(block.content for block in blocks),
            )
            researcher.mark_used(
                research_decision,
                str(target.get("node_id") or ""),
                used_evidence_ids,
            )
        if not blocks:
            raise ValueError("CONTENT_BLOCKED: WriterBundle 不包含可生成的章节目标")
        proposal = WriterBundleContentGate().validate(bundle, blocks)
        base_fingerprint = writer_base_fingerprint(
            self.context,
            unit_id=bundle.unit_id,
            contract_revision=bundle.revision,
            node_ids=[
                str(item.get("chapter_id") or item.get("node_id") or "")
                for item in bundle.blueprint_slice
                if str(item.get("chapter_id") or item.get("node_id") or "")
            ],
            deterministic_test=self.deterministic_test,
        )
        bindings = evidence_bindings(bundle.evidence_snapshot)
        final_fingerprint = writer_fingerprint(base_fingerprint, bindings)
        content_hash = canonical_hash(
            [block.model_dump(mode="json") for block in blocks]
        )
        output = (
            self.root
            / CONTENT_OUTPUT_DIR
            / (
                f"{bundle.unit_id}--{final_fingerprint[:12]}"
                f"--{content_hash[:12]}.json"
            )
        )
        write_json(
            output,
            {
                "schema_version": "v3",
                "writer_version": WRITER_IMPLEMENTATION_VERSION,
                "writer_prompt_version": WRITER_PROMPT_VERSION,
                "writer_mode": (
                    "deterministic_test"
                    if self.deterministic_test
                    else "production"
                ),
                "writer_base_fingerprint": base_fingerprint,
                "writer_fingerprint": final_fingerprint,
                "evidence_batches": bindings,
                "research_decision_id": str(
                    research_decision.get("decision_id") or ""
                ),
                "research_operation_id": operation_id,
                "unit_id": bundle.unit_id,
                "bundle_id": bundle.bundle_id,
                "content_proposal": proposal.model_dump(mode="json"),
                "blocks": [
                    block.model_dump(mode="json")
                    for block in blocks
                ],
            },
        )
        self.store.upsert_content_unit_state(
            {
                "unit_id": bundle.unit_id,
                "contract_revision": bundle.revision,
                "state": "completed",
                "evidence_snapshot_hash": bundle.bundle_hash,
                "writer_fingerprint": final_fingerprint,
                "stale_reason": "",
                "output_artifact_id": output.relative_to(self.root).as_posix(),
                "current_chapter_id": "",
                "current_chapter_title": "",
                "progress_phase": "",
            }
        )
        return blocks

    def _bundle_with_research(
        self,
        bundle: WriterInputBundle,
        *,
        evidence: list[dict],
        decisions: list[dict],
    ) -> WriterInputBundle:
        """Freeze writer-time evidence into a new immutable Bundle revision."""
        body = bundle.model_dump(
            mode="json",
            exclude={"revision", "source_hashes", "bundle_id", "bundle_hash"},
        )
        body["evidence_snapshot"] = evidence
        body["research_decisions"] = decisions
        source_hashes = dict(bundle.source_hashes)
        for item in evidence:
            if isinstance(item, dict) and item.get("batch_id"):
                source_hashes[f"evidence:{item['batch_id']}"] = canonical_hash(item)
        bundle_hash = canonical_hash(body)
        frozen = WriterInputBundle(
            revision=bundle.revision,
            source_hashes=source_hashes,
            bundle_id=f"{bundle.bundle_id}-r{bundle_hash[:8]}",
            bundle_hash=bundle_hash,
            **body,
        )
        path = self.root / V3_ROOT / "writer_bundles" / f"{frozen.bundle_id}.json"
        write_json(path, frozen.model_dump(mode="json"))
        return frozen

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
                operation="create",
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
