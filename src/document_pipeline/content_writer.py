from __future__ import annotations

import json
import re

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import extract_json_text, write_json

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
        writable_targets = [
            target
            for target in bundle.document_target_constraints
            if str(target.get("content_policy") or "full") == "full"
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
                try:
                    from .chapter_editing import ChapterEditingService

                    workspace = self.store.chapter_workspace(target_id) or {}
                    ChapterEditingService(self.context).generate_draft(
                        chapter_id=target_id,
                        expected_chapter_revision=int(
                            workspace.get("chapter_revision") or 0
                        ),
                        text=content,
                        overwrite_locked=False,
                        actor={"type": "system", "id": "writer", "role": "writer"},
                    )
                except Exception:
                    # Content unit path remains authoritative for pipeline G4; chapter
                    # workspace draft is best-effort overlay until Phase 7 hard-wires.
                    pass
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
    def _strip_writer_fences(raw: str) -> str:
        cleaned = str(raw or "").strip().lstrip("\ufeff")
        if cleaned.startswith("```"):
            fence = re.match(
                r"^```[a-zA-Z0-9_-]*\s*(.*?)\s*```$",
                cleaned,
                re.DOTALL,
            )
            if fence:
                return fence.group(1).strip()
        return cleaned

    @staticmethod
    def _escape_raw_control_chars_in_json_strings(text: str) -> str:
        """Escape bare control characters that models often leave inside strings."""

        out: list[str] = []
        in_string = False
        escaped = False
        for char in text:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\" and in_string:
                out.append(char)
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                out.append(char)
                continue
            if in_string and ord(char) < 0x20:
                if char == "\n":
                    out.append("\\n")
                elif char == "\r":
                    out.append("\\r")
                elif char == "\t":
                    out.append("\\t")
                else:
                    out.append(f"\\u{ord(char):04x}")
                continue
            out.append(char)
        return "".join(out)

    @staticmethod
    def _repair_common_json_noise(text: str) -> str:
        repaired = str(text or "")
        repaired = repaired.replace("“", '"').replace("”", '"')
        repaired = repaired.replace("‘", "'").replace("’", "'")
        # Trailing commas before object/array close.
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        return repaired

    @classmethod
    def _object_candidates(cls, raw: str) -> list[str]:
        cleaned = cls._strip_writer_fences(raw)
        candidates: list[str] = []
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            candidates.append(cleaned[start : end + 1])
        try:
            extracted = extract_json_text(raw)
        except Exception:
            extracted = ""
        if str(extracted).lstrip().startswith("{"):
            candidates.append(str(extracted).strip())
        # Deduplicate while preserving order.
        unique: list[str] = []
        for item in candidates:
            if item and item not in unique:
                unique.append(item)
        return unique

    @classmethod
    def _salvage_writer_payload(cls, raw: str) -> dict | None:
        """Best-effort recovery when the model emits almost-JSON chapter bodies."""

        text = cls._escape_raw_control_chars_in_json_strings(
            cls._repair_common_json_noise(cls._strip_writer_fences(raw))
        )
        content_match = re.search(
            r'"content"\s*:\s*"((?:\\.|[^"\\])*)"',
            text,
            re.DOTALL,
        )
        if not content_match:
            # Content with residual bare newlines after partial repair: take the
            # span until the next top-level field or object close.
            loose = re.search(
                r'"content"\s*:\s*"(.*)"\s*,\s*"used_evidence_ids"',
                text,
                re.DOTALL,
            )
            if not loose:
                loose = re.search(
                    r'"content"\s*:\s*"(.*)"\s*}',
                    text,
                    re.DOTALL,
                )
            if not loose:
                return None
            content_raw = loose.group(1)
        else:
            content_raw = content_match.group(1)
        try:
            content = json.loads(f'"{content_raw}"')
        except json.JSONDecodeError:
            content = (
                content_raw.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        content = str(content or "").strip()
        if not content:
            return None
        evidence_ids: list[str] = []
        evidence_match = re.search(
            r'"used_evidence_ids"\s*:\s*(\[[^\]]*\])',
            text,
            re.DOTALL,
        )
        if evidence_match:
            try:
                parsed_ids = json.loads(
                    cls._repair_common_json_noise(evidence_match.group(1))
                )
            except json.JSONDecodeError:
                parsed_ids = []
            if isinstance(parsed_ids, list):
                evidence_ids = [
                    str(item).strip()
                    for item in parsed_ids
                    if str(item).strip()
                ]
        return {
            "content": content,
            "used_evidence_ids": evidence_ids,
        }

    @classmethod
    def _parse_writer_json(cls, raw: str) -> dict:
        """Parse writer model output as a JSON object (never a bare array/scalar)."""

        last_error: Exception | None = None
        for candidate in cls._object_candidates(raw):
            variants = [
                candidate,
                cls._repair_common_json_noise(candidate),
                cls._escape_raw_control_chars_in_json_strings(candidate),
                cls._escape_raw_control_chars_in_json_strings(
                    cls._repair_common_json_noise(candidate)
                ),
            ]
            for payload in variants:
                try:
                    decoded = json.loads(payload)
                except Exception as exc:  # noqa: BLE001 - try next repair
                    last_error = exc
                    continue
                if isinstance(decoded, dict) and str(
                    decoded.get("content") or ""
                ).strip():
                    return decoded
                if isinstance(decoded, dict):
                    last_error = ValueError("Writer 输出正文为空")
                else:
                    last_error = ValueError("Writer 输出必须是 JSON 对象")
        salvaged = cls._salvage_writer_payload(raw)
        if salvaged is not None:
            return salvaged
        if last_error is not None:
            raise last_error
        raise ValueError("模型输出中未找到 JSON 对象")

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
        # Internal planning vocabulary must not leak into body text.
        rubric_trace = re.compile(
            r"满分条件|得分任务|得分点|评分要求|评分标准|本节用于|"
            r"按已确认的章节边界|章节边界组织响应内容|展开具体响应内容"
        )

        def _without_rubric_trace(values: list[str]) -> list[str]:
            cleaned: list[str] = []
            for item in values:
                text = rubric_trace.sub("", item).strip(" ：:；;，,。")
                if text and text not in cleaned:
                    cleaned.append(text)
            return cleaned

        objectives = _without_rubric_trace(objectives)
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
        intents = _without_rubric_trace(
            ContentWriter._short_items(
                [
                    item.get("response_intent")
                    for item in conditions
                ],
                5,
            )
        )
        scoring_obligations = ContentWriter._short_items(
            [
                item.get("response_intent")
                or item.get("normalized_condition")
                or item.get("text")
                for item in conditions
            ],
            6,
        )
        expectations = _without_rubric_trace(
            ContentWriter._short_items(
                [item.get("response_expectation") for item in response_units],
                4,
            )
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
        available_evidence_ids = sorted(
            {
                str(evidence_id)
                for item in (research_evidence or [])
                for evidence_id in (item.get("evidence_ids") or [])
                if str(evidence_id)
            }
        )
        if not self.deterministic_test:
            prompt_bundle = {
                "chapter_title": title,
                "target_size": target_size,
                "project_context": project,
                "chapter_objectives": objectives,
                "tender_requirement_excerpts": requirement_points,
                "internal_coverage_intents": intents,
                "scoring_obligations": scoring_obligations,
                "response_expectations": expectations,
                "required_evidence_types": evidence_types,
                "allowed_research_evidence": research_evidence or [],
                "writing_rule": (
                    "以章节标题和评分义务为主线，只覆盖与本章相关的内容；"
                    "不要求每个章节机械包含背景、步骤、质量、成果、验收或风险等全部维度。"
                ),
                "prohibited_visible_text": [
                    "满分条件",
                    "得分任务",
                    "本节用于",
                    "按已确认的章节边界",
                    "展开具体响应内容",
                    "评分要求",
                    "评分标准",
                    "得分点",
                ],
                "output_schema": {
                    "content": "完整正文，不含 Markdown 标题",
                    "used_evidence_ids": "正文实际使用的 allowed evidence id 列表",
                },
            }
            try:
                from llm_client import chat

                messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是技术标书正文写作器。以章节标题和评分义务为主线输出项目化正文，"
                            "不得复述评分条件、招标写作指令或生成占位话术。"
                            "需求与评分信息只用于内部覆盖检查，不能写成“本节用于、"
                            "围绕要求、满分条件、得分任务”等正文。"
                            "公开研究只可支持背景、现行依据、专业方法和风险控制；"
                            "禁止用公开资料虚构企业资质、业绩、人员、报价或承诺。"
                            "只展开与本章相关、能够支撑评分义务的内容；不要为了凑模板"
                            "强行补写不相关的方法、步骤、质量控制、成果、验收或风险。"
                            "并严格输出 JSON 对象，不要 Markdown；"
                            "content 字段内不要插入未转义的换行控制字符。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            prompt_bundle,
                            ensure_ascii=False,
                        ),
                    },
                ]
                last_error: Exception | None = None
                # Automatic retries absorb malformed model JSON without falling
                # back to template prose or pausing the whole pipeline early.
                attempt_messages = list(messages)
                for attempt in range(3):
                    try:
                        raw = chat(
                            attempt_messages,
                            temperature=0.25 if attempt == 0 else 0.1,
                        ).strip()
                        decoded = ContentWriter._parse_writer_json(raw)
                        content = str(decoded.get("content") or "").strip()
                        used_evidence_ids = sorted(
                            {
                                str(item)
                                for item in (
                                    decoded.get("used_evidence_ids") or []
                                )
                                if str(item)
                            }
                        )
                        if not content:
                            raise ValueError("Writer 输出正文为空")
                        return content, used_evidence_ids
                    except Exception as attempt_exc:
                        last_error = attempt_exc
                        attempt_messages = [
                            *messages,
                            {
                                "role": "user",
                                "content": (
                                    "上次输出无法解析为合法 JSON 对象。"
                                    "请只输出一个 JSON 对象，字段为 content 与 "
                                    "used_evidence_ids；content 使用 \\n 表示换行，"
                                    "不要 Markdown、不要前后说明文字。"
                                    f"错误：{type(attempt_exc).__name__}: "
                                    f"{attempt_exc}"[:500]
                                ),
                            },
                        ]
                        continue
                assert last_error is not None
                raise last_error
            except ControlPlaneError:
                raise
            except Exception as exc:
                raise ControlPlaneError(
                    "WRITER_MODEL_ACTION_REQUIRED",
                    (
                        "写作模型多次未返回可解析的项目化正文，生成已在当前章节暂停；"
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
        return (
            "\n\n".join(sections[:desired]),
            available_evidence_ids,
        )

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
