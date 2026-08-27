"""The single, chapter-agnostic writing specification and prompt kernel.

This module intentionally contains no chapter-name or topic-specific rules.  A
chapter's scope is data: the promoted Blueprint, its compiled blocks, bound
requirements, and the facts which can be shown to support those blocks.  All
callers (chat, single chapter, batch, rewrite and repair) can therefore build
the same :class:`ChapterWritingSpec` and use the same messages.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Literal, Mapping

from .canonicalization import canonical_hash, canonical_json
from .chapter_writing_outline import compile_chapter_writing_plan


Operation = Literal["create", "rewrite", "repair"]


def _dump(value: Any) -> Any:
    """Convert pydantic/dataclass values to ordinary JSON-compatible values."""
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_dump(item) for item in value]
    return value


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", _text(value)).casefold()


def _ngrams(value: Any) -> set[str]:
    """Small language-neutral tokens, including CJK bigrams."""
    text = _compact(value)
    if not text:
        return set()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", text))
    tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return tokens


_GENERIC_TOKENS = {
    "project", "chapter", "section", "content", "response", "work",
    "item", "task", "method", "implementation", "项目", "章节", "内容",
    "工作", "任务", "相关", "说明", "要求", "本项目", "本章节",
}

_PROJECT_FACT_FIELD_CUES = {
    "background": "项目背景 现状 情境 由来 必要性 依据",
    "goals": "项目目标 工作目标 建设目标 目的 成效 效果",
    "scope": "项目范围 工作范围 任务范围 实施范围 实施边界 工作边界 边界 对象",
    "boundaries": "实施边界 工作边界 范围 边界",
    "work_packages": "项目目标 工作目标 工作内容 工作任务 核心任务 实施任务",
    "dependencies": "实施条件 依赖 前提 可行性",
    "inputs": "输入 数据 资料 前提",
    "processing": "实施 处理 方法 做法 工作内容",
    "outputs": "输出 成果 结果 可检验",
    "deliverables": "交付 成果 提交",
    "acceptance_conditions": "验收 检查 检验 判定 可检验",
    "milestones": "阶段 进度 周期 时间",
    "roles": "组织 人员 角色 职责 分工",
    "risks": "风险 难点 不确定性",
    "constraints": "约束 限制 条件 可行性",
}

_DIRECT_SEMANTIC_FACT_FIELDS = {"goals", "scope", "boundaries", "work_packages"}


def _relevance(target: str, value: Any) -> float:
    """Return a conservative relevance score for a candidate fact.

    Exact target containment is useful for identifiers and short requirements;
    otherwise overlap is calculated on meaningful tokens.  Generic words alone
    never make a fact eligible.
    """
    candidate = _text(value)
    target_compact = _compact(target)
    candidate_compact = _compact(candidate)
    if not candidate_compact or not target_compact:
        return 0.0
    if candidate_compact in target_compact or target_compact in candidate_compact:
        return 1.0
    target_tokens = {
        token for token in _ngrams(target)
        if token not in _GENERIC_TOKENS and len(token) >= 2
    }
    candidate_tokens = {
        token for token in _ngrams(candidate)
        if token not in _GENERIC_TOKENS and len(token) >= 2
    }
    if not target_tokens or not candidate_tokens:
        return 0.0
    overlap = target_tokens & candidate_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(1, len(candidate_tokens))


def _node_for(chapter: Any, blueprint: Any = None, chapter_id: str = "") -> dict[str, Any]:
    raw_chapter = _dump(chapter) or {}
    if not isinstance(raw_chapter, dict):
        raw_chapter = {}
    nested = raw_chapter.get("blueprint_node")
    if isinstance(nested, dict):
        return dict(nested)
    if isinstance(raw_chapter.get("node"), dict):
        return dict(raw_chapter["node"])
    raw_blueprint = _dump(blueprint) or {}
    if isinstance(raw_blueprint, dict):
        nodes = raw_blueprint.get("nodes") or raw_blueprint.get("blueprint_nodes") or []
        for item in nodes:
            item = _dump(item)
            if isinstance(item, dict) and (
                not chapter_id or str(item.get("chapter_id") or "") == str(chapter_id)
            ):
                return dict(item)
    return {
        key: raw_chapter[key]
        for key in (
            "chapter_id", "title", "purpose", "writing_objectives",
            "requirement_ids", "score_condition_ids", "score_point_ids",
            "primary_response_unit_ids", "supporting_response_unit_ids",
        )
        if key in raw_chapter
    }


def _chapter_payload(chapter: Any, node: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    raw = _dump(chapter) or {}
    if not isinstance(raw, dict):
        raw = {}
    result = dict(raw)
    result["chapter_id"] = str(result.get("chapter_id") or node.get("chapter_id") or chapter_id)
    result["title"] = str(result.get("title") or node.get("title") or "")
    # The outline compiler consumes the node through this conventional field.
    result["blueprint_node"] = dict(node)
    return result


def _bound_requirements(
    node: dict[str, Any],
    tender: list[dict[str, Any]],
    scoring: list[dict[str, Any]],
    explicit: list[dict[str, Any]] | None,
    outline: dict[str, Any],
) -> list[dict[str, Any]]:
    if explicit is not None:
        return [dict(_dump(item)) for item in explicit if isinstance(_dump(item), dict)]
    ids = {str(item) for item in node.get("requirement_ids") or [] if item}
    for block in outline.get("blocks") or []:
        ids.update(str(item) for item in block.get("requirement_ids") or [] if item)
    if ids:
        return [dict(item) for item in tender if str(item.get("requirement_id") or "") in ids]
    # Score points and conditions can be the binding source when no requirement
    # ids are present.  Do not leak unrelated tender material into the spec.
    score_ids = {str(item) for item in node.get("score_point_ids") or [] if item}
    if score_ids:
        return [dict(item) for item in scoring if str(item.get("score_point_id") or "") in score_ids]
    return []


def _candidate_text(item: Any) -> str:
    if isinstance(item, dict):
        return " ".join(
            _text(item.get(key))
            for key in ("statement", "title", "body", "text", "description", "name", "value")
            if item.get(key) is not None
        )
    return _text(item)


def _project_facts(project_context: Any, target: str) -> dict[str, Any]:
    """Project only facts which support the chapter target.

    Revision/hash metadata is retained for concurrency and audit.  Content
    categories and identity fields are retained only when relevant, except for
    the project identity name, which is harmless display context.  This is a
    deterministic projection and does not ask a model to decide what is in
    scope.
    """
    context = _dump(project_context) or {}
    if not isinstance(context, dict):
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema_version", "global_context_id", "global_context_revision",
        "global_context_hash", "project_id",
    ):
        if key in context:
            projected[key] = context[key]
    identity = context.get("identity")
    if isinstance(identity, dict):
        identity_out: dict[str, Any] = {}
        for key, value in identity.items():
            key_text = _text(key).casefold()
            if key_text in {"project_name", "project", "项目名称", "项目"}:
                identity_out[str(key)] = value
            elif _relevance(target, f"{key} {value}") >= 0.32:
                identity_out[str(key)] = value
        if identity_out:
            projected["identity"] = identity_out

    selected_ids: list[str] = []
    total_facts = 0
    target_compact = _compact(target)
    for key, values in context.items():
        if key in projected or key in {"identity", "unknowns", "terminology"}:
            continue
        if not isinstance(values, list):
            continue
        field_cues = _PROJECT_FACT_FIELD_CUES.get(str(key), "")
        field_matches_goal = str(key) in _DIRECT_SEMANTIC_FACT_FIELDS and any(
            _compact(cue) and _compact(cue) in target_compact
            for cue in field_cues.split()
        )
        kept: list[Any] = []
        for item in values:
            total_facts += 1
            candidate = " ".join(
                value
                for value in (
                    _PROJECT_FACT_FIELD_CUES.get(str(key), ""),
                    _candidate_text(item),
                )
                if value
            )
            score = 1.0 if field_matches_goal else _relevance(target, candidate)
            # A direct target phrase is strong; otherwise require meaningful
            # overlap.  No title-only signal is ever used here.
            if score < 0.32:
                continue
            kept.append(deepcopy(item))
            if isinstance(item, dict) and item.get("fact_id"):
                selected_ids.append(str(item["fact_id"]))
        if kept:
            projected[key] = kept

    terminology = context.get("terminology")
    if isinstance(terminology, dict):
        terms = {
            str(key): value
            for key, value in terminology.items()
            if _relevance(target, f"{key} {value}") >= 0.32
        }
        if terms:
            projected["terminology"] = terms
    projected["confirmed_fact_count"] = total_facts
    projected["selected_fact_ids"] = sorted(set(selected_ids))
    return projected


def project_chapter_facts(
    project_context: Any,
    *,
    purpose: str = "",
    writing_objectives: Any = (),
    writing_outline: Any = None,
    bound_requirements: Any = (),
) -> dict[str, Any]:
    """Return the project-fact projection shared by writing and chapter chat.

    The declared chapter goal is the only content-selection signal.  A fact
    that does not match that goal is omitted; an empty match never falls back
    to the full project context.
    """
    outline = _dump(writing_outline) or {}
    if not isinstance(outline, dict):
        outline = {}
    objectives = [_text(item) for item in (_dump(writing_objectives) or []) if _text(item)]
    requirements = [
        item
        for item in (_dump(bound_requirements) or [])
        if isinstance(item, dict)
    ]
    target_parts = [_text(purpose), *objectives]
    target_parts.extend(
        _text(block.get("must_answer"))
        for block in outline.get("blocks") or []
        if isinstance(block, dict)
    )
    target_parts.extend(
        _text(item.get("text") or item.get("normalized_requirement") or item.get("requirement"))
        for item in requirements
    )
    return _project_facts(
        project_context,
        " ".join(item for item in target_parts if item),
    )


def _normalize_history(history: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in history or []:
        item = _dump(raw)
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").lower()
        content = item.get("content", item.get("text", ""))
        if content is None:
            content = ""
        normalized = {
            "role": role,
            "content": content,
            # Previous model text can be discussed, but never promoted to fact.
            "authority": "non_authoritative" if role == "assistant" else "user_input",
            "is_fact_source": False,
        }
        if item.get("timestamp") is not None:
            normalized["timestamp"] = item["timestamp"]
        result.append(normalized)
    return result


@dataclass(frozen=True)
class ChapterWritingRequest:
    """Transport-neutral request shared by every writing entry point."""

    chapter_id: str
    operation: Operation = "create"
    user_instruction: str = ""
    existing_content: str = ""
    validation_errors: tuple[str, ...] = ()
    expected_workspace_revision: int | None = None
    expected_chapter_revision: int | None = None
    actor: str = "system"
    idempotency_key: str | None = None
    chapter: Any = None
    blueprint: Any = None
    tender_requirements: tuple[dict[str, Any], ...] = ()
    scoring_requirements: tuple[dict[str, Any], ...] = ()
    binding_requirements: tuple[dict[str, Any], ...] | None = None
    project_context: Any = None
    chapter_context: Any = None
    history: tuple[dict[str, Any], ...] = ()
    writing_orientation: Any = None
    writing_plan: Any = None


@dataclass(frozen=True)
class ChapterWritingSpec:
    """Immutable, content-addressed input to the one chapter writer."""

    chapter_id: str
    chapter_title: str
    operation: Operation
    purpose: str
    writing_objectives: tuple[str, ...]
    writing_outline: dict[str, Any]
    bound_requirements: tuple[dict[str, Any], ...]
    project_context: dict[str, Any]
    chapter_context: dict[str, Any]
    history: tuple[dict[str, Any], ...]
    user_instruction: str = ""
    existing_content: str = ""
    validation_errors: tuple[str, ...] = ()
    target_size: int = 0
    spec_hash: str = field(default="")

    def payload(self, *, include_hash: bool = False) -> dict[str, Any]:
        value = {
            "schema_version": "v3.chapter-writing-spec.v1",
            "chapter_id": self.chapter_id,
            # Title is display metadata; it is deliberately not used in rules.
            "display": {"chapter_title": self.chapter_title},
            "operation": self.operation,
            "purpose": self.purpose,
            "writing_objectives": list(self.writing_objectives),
            "writing_outline": deepcopy(self.writing_outline),
            "bound_requirements": deepcopy(list(self.bound_requirements)),
            "project_context": deepcopy(self.project_context),
            "chapter_context": deepcopy(self.chapter_context),
            "history": deepcopy(list(self.history)),
            "user_instruction": self.user_instruction,
            "existing_content": self.existing_content,
            "validation_errors": list(self.validation_errors),
            "target_size": self.target_size,
        }
        if include_hash:
            value["spec_hash"] = self.spec_hash
        return value

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        return self.payload(include_hash=True)

    def scope_contract(self) -> "ChapterScopeContract":
        """Expose the same immutable boundary to non-writing chapter turns."""
        return compile_chapter_scope_contract(self)


@dataclass(frozen=True)
class ChapterScopeContract:
    """The chapter boundary shared by document writing and conversational turns.

    Runtime state such as the current user message, draft and chat history is
    intentionally absent.  Those inputs may shape the response, but may not
    expand what the chapter is about.
    """

    chapter_id: str
    chapter_title: str
    purpose: str
    writing_objectives: tuple[str, ...]
    writing_outline: dict[str, Any]
    bound_requirements: tuple[dict[str, Any], ...]
    project_context: dict[str, Any]
    scope_hash: str = field(default="")

    def payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": "v3.chapter-scope-contract.v1",
            "chapter_id": self.chapter_id,
            # Display metadata is never a scope-selection input.
            "display": {"chapter_title": self.chapter_title},
            "purpose": self.purpose,
            "writing_objectives": list(self.writing_objectives),
            "writing_outline": deepcopy(self.writing_outline),
            "bound_requirements": deepcopy(list(self.bound_requirements)),
            "project_context": deepcopy(self.project_context),
        }
        if include_hash:
            value["scope_hash"] = self.scope_hash
        return value

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        return self.payload(include_hash=True)


def compile_chapter_scope_contract(spec: ChapterWritingSpec) -> ChapterScopeContract:
    """Derive the transport-neutral chapter boundary from the canonical spec."""
    if not isinstance(spec, ChapterWritingSpec):
        raise TypeError("spec must be ChapterWritingSpec")
    contract = ChapterScopeContract(
        chapter_id=spec.chapter_id,
        chapter_title=spec.chapter_title,
        purpose=spec.purpose,
        writing_objectives=tuple(spec.writing_objectives),
        writing_outline=deepcopy(spec.writing_outline),
        bound_requirements=tuple(deepcopy(list(spec.bound_requirements))),
        project_context=deepcopy(spec.project_context),
    )
    hash_payload = contract.payload(include_hash=False)
    hash_payload.pop("display", None)
    object.__setattr__(contract, "scope_hash", canonical_hash(hash_payload))
    return contract


def _request_from_values(request: Any, values: dict[str, Any]) -> ChapterWritingRequest:
    if request is None:
        return ChapterWritingRequest(**values)
    if isinstance(request, ChapterWritingRequest):
        return request
    raw = _dump(request)
    if isinstance(raw, dict):
        merged = dict(raw)
        merged.update({key: value for key, value in values.items() if value is not None})
        return ChapterWritingRequest(**merged)
    raise TypeError("request must be ChapterWritingRequest or a mapping")


def compile_chapter_writing_spec(
    request: ChapterWritingRequest | Mapping[str, Any] | None = None,
    **values: Any,
) -> ChapterWritingSpec:
    """Compile one canonical spec from any transport's request/context values."""
    values = {key: _dump(value) for key, value in values.items()}
    req = _request_from_values(request, values)
    if req.operation not in {"create", "rewrite", "repair"}:
        raise ValueError("operation must be create, rewrite, or repair")
    chapter_id = str(req.chapter_id or "")
    node = _node_for(req.chapter, req.blueprint, chapter_id)
    chapter = _chapter_payload(req.chapter, node, chapter_id)
    purpose = _text(node.get("purpose"))
    objectives = tuple(_text(item) for item in node.get("writing_objectives") or [] if _text(item))
    tender = [dict(item) for item in (_dump(req.tender_requirements) or []) if isinstance(item, dict)]
    scoring = [dict(item) for item in (_dump(req.scoring_requirements) or []) if isinstance(item, dict)]
    chapter_context = _dump(req.chapter_context) or {}
    if not isinstance(chapter_context, dict):
        chapter_context = {"items": chapter_context}
    context_items = chapter_context.get("chapter_context_items") or chapter_context.get("items") or []
    outline = _dump(
        compile_chapter_writing_plan(
            chapter,
            tender_requirements=tender,
            scoring_requirements=scoring,
            writing_orientation=_dump(req.writing_orientation),
            chapter_context_items=context_items if isinstance(context_items, list) else [],
            project_context=_dump(req.project_context),
        )
    )
    supplied_plan = _dump(req.writing_plan)
    if isinstance(supplied_plan, dict) and supplied_plan.get("blocks"):
        outline = {
            **outline,
            "blocks": deepcopy(list(supplied_plan.get("blocks") or [])),
            "block_count": len(list(supplied_plan.get("blocks") or [])),
        }
        if isinstance(supplied_plan.get("rewrite_context"), dict):
            outline["rewrite_context"] = deepcopy(supplied_plan["rewrite_context"])
    bound = _bound_requirements(
        node, tender, scoring, _dump(req.binding_requirements), outline
    )
    projected_context = project_chapter_facts(
        req.project_context,
        purpose=purpose,
        writing_objectives=objectives,
        writing_outline=outline,
        bound_requirements=bound,
    )
    normalized_history = tuple(_normalize_history(req.history))
    errors = tuple(_text(item) for item in req.validation_errors if _text(item))
    spec = ChapterWritingSpec(
        chapter_id=chapter_id,
        chapter_title=str(node.get("title") or _dump(req.chapter or {}).get("title") or ""),
        operation=req.operation,
        purpose=purpose,
        writing_objectives=objectives,
        writing_outline=outline,
        bound_requirements=tuple(bound),
        project_context=projected_context,
        chapter_context=deepcopy(chapter_context),
        history=normalized_history,
        user_instruction=_text(req.user_instruction),
        existing_content=str(req.existing_content or ""),
        validation_errors=errors,
        target_size=max(0, int(node.get("target_size") or 0)),
    )
    object.__setattr__(spec, "spec_hash", canonical_hash(spec.payload()))
    return spec


_SYSTEM_PROMPT = """You are the single chapter-writing engine for a Chinese technical bid.
Use only the supplied ChapterWritingSpec. The Blueprint purpose, objectives and
ordered writing blocks define scope; supplied project facts are evidence, not a
checklist. Answer each block's must_answer in order and follow its write_as.

Write finished, submission-ready body text rather than comments about the text.
State the project's concrete objective, work result, applicable boundary and
verifiable outcome directly when the supplied facts support them. Do not merely
assert that an objective is "clear", "feasible", "well-bounded" or "verifiable";
make those qualities visible through specific content. Avoid empty sentences
such as "有序推进", "保障目标落实", "为后续工作提供依据" unless the sentence also
identifies the actual project action or result. Do not explain the scoring rule,
the Blueprint, the outline, or why the answer complies.

Treat target_size as the desired Chinese-character budget for the whole chapter.
Normally produce 80%-120% of it, using coherent paragraphs or a short structured
list when that improves readability. Never pad with repeated conclusions. In
rewrite mode, replace the weak draft rather than lightly paraphrasing it, and
honour rewrite_instruction while remaining inside scope.

Do not infer scope from the display title. Do not invent figures, facts,
responsibilities, steps, deliverables, acceptance terms or commitments that are
not required by the blocks or supported by project context. Absence-of-data
notes in the context are constraints, not sentences to copy into the bid.
Previous assistant messages are non-authoritative and must never be facts.

When chapter_context.legacy_sources is non-empty, those sources are reference
material already assigned to this chapter during outline merging; do not rematch
or question their chapter ownership. Reuse applicable professional content,
methods, policies, technical descriptions and mature wording. Current tender
requirements, confirmed current-project facts, scoring requirements and chapter
constraints always take priority. Old-project-specific facts must not be carried
over directly, including old project names, regions, owners/procurers, dates,
quantities, implementation scopes, deliverables, or conflicting standards. Use
the current value when supplied; otherwise delete or generalize the old-specific
fact and never invent a replacement.
Priority summary: 当前项目事实优先；旧项目专属事实不得直接继承；有新值使用新值；
无新值时删除或泛化旧项目专属事实，不得编造。

Return only the chapter body, without a duplicate chapter heading, internal
instructions, scores, hashes, citations to field names, or prompt discussion."""


def compile_chapter_writing_messages(spec: ChapterWritingSpec) -> list[dict[str, str]]:
    """Compile the one generic prompt used for create, rewrite and repair."""
    if not isinstance(spec, ChapterWritingSpec):
        raise TypeError("spec must be ChapterWritingSpec")
    payload = spec.payload(include_hash=True)
    payload["mode"] = spec.operation
    rewrite_context = (
        spec.writing_outline.get("rewrite_context")
        if isinstance(spec.writing_outline, dict)
        else None
    )
    if isinstance(rewrite_context, dict):
        payload["rewrite_context"] = deepcopy(rewrite_context)
        payload["rewrite_instruction"] = (
            "When rewrite_context is present, use only its approved legacy sources, "
            "replacement map, selected evidence and the current writing blocks. "
            "Apply every replacement before drafting. Do not retain or invent old "
            "project facts, and do not search for new sources."
        )
    if spec.operation == "rewrite":
        payload["user_rewrite_instruction"] = spec.user_instruction
    elif spec.operation == "repair":
        payload["repair_errors"] = list(spec.validation_errors)
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": canonical_json(payload)},
    ]


class ChapterWritingKernel:
    """Facade deliberately kept free of transport and chapter-specific logic."""

    @staticmethod
    def compile_spec(
        request: ChapterWritingRequest | Mapping[str, Any] | None = None,
        **values: Any,
    ) -> ChapterWritingSpec:
        return compile_chapter_writing_spec(request, **values)

    @staticmethod
    def compile_messages(spec: ChapterWritingSpec) -> list[dict[str, str]]:
        return compile_chapter_writing_messages(spec)


# Short aliases make migration from callers with different naming conventions
# harmless while retaining one implementation.
compile_spec = compile_chapter_writing_spec
compile_messages = compile_chapter_writing_messages


__all__ = [
    "ChapterWritingKernel",
    "ChapterWritingRequest",
    "ChapterWritingSpec",
    "ChapterScopeContract",
    "compile_chapter_scope_contract",
    "compile_chapter_writing_messages",
    "compile_chapter_writing_spec",
    "compile_messages",
    "compile_spec",
    "project_chapter_facts",
]
