"""Shared project-grounding policy for every newly generated chapter.

The writer prompt is not a trust boundary.  This module validates the final
text against the one promoted, workspace-wide project context before an AI
draft may be persisted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from control_plane import ControlPlaneError

from .canonicalization import canonical_hash


GROUNDING_POLICY_VERSION = "v3.global-project-context.v2"
_GROUNDING_FACT_FIELDS = (
    "background", "goals", "scope", "work_packages", "inputs",
    "processing", "outputs", "deliverables", "acceptance_conditions",
    "constraints", "risks", "roles",
)
_CLEARLY_GENERIC_POLICY = re.compile(
    r"(?:招标投标|政府采购|市场配置资源|交易规则|全过程监管).{0,80}"
    r"(?:公开|公平|公正|诚实信用|监管|竞争)",
    re.DOTALL,
)


def _compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _bigrams(value: str) -> set[str]:
    normalized = _compact(value)
    return {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
    }


def _supported(source: str, content: str) -> bool:
    """Conservative lexical support check suitable for deterministic gating."""
    source_text = _compact(source)
    content_text = _compact(content)
    if not source_text or not content_text:
        return False
    if source_text in content_text:
        return True
    grams = _bigrams(source_text)
    if not grams:
        return False
    overlap = len(grams & _bigrams(content_text))
    return overlap >= 6 and overlap / len(grams) >= 0.16


def _binding_supported(source: str, content: str) -> bool:
    """Require strong textual evidence before binding one fact to a paragraph."""
    source_text = _compact(source)
    content_text = _compact(content)
    if not source_text or not content_text:
        return False
    if source_text in content_text:
        return True
    grams = _bigrams(source_text)
    overlap = len(grams & _bigrams(content_text))
    return overlap >= 8 and overlap / max(1, len(grams)) >= 0.45


def _as_texts(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values] if values.strip() else []
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _identity_value(context: dict[str, Any], *keys: str) -> str:
    identity = context.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    lowered = {str(key).casefold(): str(value) for key, value in identity.items()}
    for key in keys:
        value = identity.get(key)
        if value:
            return str(value).strip()
        value = lowered.get(key.casefold())
        if value:
            return value.strip()
    return ""


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _semantic_relevance_review(
    *,
    chapter: dict[str, Any],
    content: str,
    fact_candidates: list[dict[str, str]],
    requirements: list[dict[str, str]],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use a bounded JSON-only model check after deterministic matching misses."""
    from llm_client import chat

    node = chapter.get("blueprint_node")
    node = node if isinstance(node, dict) else {}
    evidence_candidates = [
        {
            "evidence_id": str(item.get("evidence_id") or ""),
            "text": str(
                item.get("supporting_excerpt")
                or item.get("snippet")
                or item.get("content")
                or ""
            ).strip(),
        }
        for item in evidence_rows
        if str(item.get("evidence_id") or "").strip()
    ]
    payload = {
        "chapter_title": str(chapter.get("title") or node.get("title") or ""),
        "chapter_purpose": str(node.get("purpose") or ""),
        "writing_objectives": [str(item) for item in node.get("writing_objectives") or []],
        "content": str(content),
        "project_facts": fact_candidates[:32],
        "requirements": requirements[:24],
        "evidence": evidence_candidates[:16],
        "output_schema": {
            "verdict": "relevant|irrelevant|conflict",
            "confidence": "number between 0 and 1",
            "matched_fact_ids": "array of supplied fact ids",
            "matched_requirement_ids": "array of supplied requirement ids",
            "matched_evidence_ids": "array of supplied evidence ids",
            "paragraph_fact_bindings": "object mapping paragraph index to fact ids",
            "paragraph_requirement_bindings": "object mapping paragraph index to requirement ids",
            "paragraph_evidence_bindings": "object mapping paragraph index to evidence ids",
            "reason": "short Chinese explanation",
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是投标技术正文的项目相关性审核器。只判断正文是否真正响应当前章节的"
                "项目事实、任务或招标要求，不要求普通技术章节重复项目名称。"
                "只能使用输入提供的事实，不得补造项目事实。必须只返回一个 JSON 对象，"
                "不得输出 Markdown。若正文与事实相冲突，verdict 返回 conflict；"
                "只是通用套话且没有章节相关事实，返回 irrelevant。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        parsed = _parse_json_object(chat(messages, temperature=0.0))
    except Exception as exc:
        raise ControlPlaneError(
            "PROJECT_RELEVANCE_REVIEW_UNAVAILABLE",
            "项目相关性语义审核暂不可用，请稍后重试。",
            status_code=503,
            details={"error": f"{type(exc).__name__}: {exc}"[:500]},
        ) from exc
    if not parsed:
        raise ControlPlaneError(
            "PROJECT_RELEVANCE_REVIEW_UNAVAILABLE",
            "项目相关性语义审核返回格式无效，请稍后重试。",
            status_code=503,
        )
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in {"relevant", "irrelevant", "conflict"}:
        raise ControlPlaneError(
            "PROJECT_RELEVANCE_REVIEW_UNAVAILABLE",
            "项目相关性语义审核返回了无效结论，请稍后重试。",
            status_code=503,
        )
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    valid_fact_ids = {str(item.get("id")) for item in fact_candidates}
    valid_requirement_ids = {str(item.get("id")) for item in requirements}
    valid_evidence_ids = {str(item.get("evidence_id")) for item in evidence_candidates}

    def _valid_ids(value: Any, allowed: set[str], *, limit: int | None = None) -> list[str]:
        values = value if isinstance(value, list) else []
        clean = sorted({str(item) for item in values if str(item) in allowed})
        return clean[:limit] if limit is not None else clean

    def _valid_bindings(value: Any, allowed: set[str]) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, list[str]] = {}
        for key, ids in value.items():
            clean = _valid_ids(ids, allowed, limit=6)
            if clean:
                result[str(key)] = clean
        return result

    return {
        "verdict": verdict,
        "confidence": confidence,
        "matched_fact_ids": _valid_ids(
            parsed.get("matched_fact_ids"), valid_fact_ids, limit=12
        ),
        "matched_requirement_ids": _valid_ids(
            parsed.get("matched_requirement_ids"), valid_requirement_ids
        ),
        "matched_evidence_ids": _valid_ids(
            parsed.get("matched_evidence_ids"), valid_evidence_ids
        ),
        "paragraph_fact_bindings": _valid_bindings(
            parsed.get("paragraph_fact_bindings"), valid_fact_ids
        ),
        "paragraph_requirement_bindings": _valid_bindings(
            parsed.get("paragraph_requirement_bindings"), valid_requirement_ids
        ),
        "paragraph_evidence_bindings": _valid_bindings(
            parsed.get("paragraph_evidence_bindings"), valid_evidence_ids
        ),
        "reason": str(parsed.get("reason") or "").strip()[:500],
    }


def _chapter_profile(chapter: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Return one title-independent profile for every chapter."""
    return "goal_driven", _GROUNDING_FACT_FIELDS


@dataclass
class GroundingFinding:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class ContentGroundingGate:
    """Fail closed when generated text is not about the promoted project."""

    @classmethod
    def evaluate(
        cls,
        *,
        global_context: dict[str, Any],
        chapter: dict[str, Any],
        content: str,
        requirement_texts: Iterable[str] = (),
        chapter_grounding_context: dict[str, Any] | None = None,
        evidence_sources: Iterable[dict[str, Any]] = (),
        require_evidence_use: bool = False,
        effective_generation_mode: str = "new_write",
    ) -> dict[str, Any]:
        body = str(content or "").strip()
        if not global_context or not body:
            raise ControlPlaneError(
                "CHAPTER_GROUNDING_INSUFFICIENT",
                "缺少全局项目事实或待校验正文，已阻止 AI 生成。",
                status_code=409,
            )

        context_id = str(
            global_context.get("global_context_id")
            or global_context.get("project_id")
            or ""
        ).strip()
        context_hash = str(global_context.get("global_context_hash") or "").strip()
        context_revision = int(global_context.get("global_context_revision") or 0)
        project_name = _identity_value(
            global_context, "project_name", "项目名称", "project", "项目"
        )
        if not context_id or not context_hash or context_revision < 1 or not project_name:
            raise ControlPlaneError(
                "CHAPTER_GROUNDING_INSUFFICIENT",
                "全局项目上下文缺少有效标识、版本、哈希或项目名称。",
                status_code=409,
                details={"global_context_id": context_id},
            )
        substantive_body = body.replace(project_name, " ")

        local_context = dict(chapter_grounding_context or {})
        local_ref = (
            str(local_context.get("global_context_id") or ""),
            int(local_context.get("global_context_revision") or 0),
            str(local_context.get("global_context_hash") or ""),
        )
        if local_ref != (context_id, context_revision, context_hash):
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_CONFLICT",
                "本章上下文未绑定当前全局项目事实版本，已阻止生成。",
                status_code=409,
                details={
                    "chapter_global_ref": local_ref,
                    "current_global_ref": (
                        context_id,
                        context_revision,
                        context_hash,
                    ),
                },
            )
        chapter_context_id = str(
            local_context.get("chapter_context_id") or ""
        ).strip()
        chapter_context_hash = str(
            local_context.get("chapter_context_hash") or ""
        ).strip()
        chapter_context_revision = int(
            local_context.get("chapter_context_revision") or 0
        )
        if not chapter_context_id or not chapter_context_hash:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_REQUIRED",
                "章节生成必须绑定本章上下文版本和哈希。",
                status_code=409,
            )

        profile, relevant_fields = _chapter_profile(chapter)
        paragraphs = [
            item.strip()
            for item in re.split(r"\n\s*\n", body)
            if item.strip()
        ]
        matched_fields: dict[str, list[str]] = {}
        for field_name in relevant_fields:
            matches = [
                item
                for item in _as_texts(global_context.get(field_name))
                if _supported(item, substantive_body)
            ]
            if matches:
                matched_fields[field_name] = matches

        fact_candidates: list[dict[str, str]] = []
        for field_name in relevant_fields:
            for index, item in enumerate(_as_texts(global_context.get(field_name))):
                fact_candidates.append(
                    {"id": f"{field_name}:{index}", "field": field_name, "text": item}
                )
        confirmed = global_context.get("confirmed_facts")
        confirmed = confirmed if isinstance(confirmed, list) else []
        for item in confirmed:
            if isinstance(item, dict) and str(item.get("fact_id") or "").strip():
                fact_candidates.append(
                    {
                        "id": str(item.get("fact_id")),
                        "field": "confirmed_facts",
                        "text": str(item.get("statement") or "").strip(),
                    }
                )

        requirements = [str(item).strip() for item in requirement_texts if str(item).strip()]
        requirement_rows = [
            {"id": f"REQ-{index}", "text": item}
            for index, item in enumerate(requirements)
        ]
        matched_requirements = [
            item for item in requirements if _supported(item, substantive_body)
        ]
        evidence_rows = [
            dict(item) for item in evidence_sources if isinstance(item, dict)
        ]
        used_evidence_ids: list[str] = []
        paragraph_evidence_bindings: dict[str, list[str]] = {
            str(index): [] for index in range(len(paragraphs))
        }
        for source in evidence_rows:
            evidence_id = str(source.get("evidence_id") or "").strip()
            source_text = str(
                source.get("supporting_excerpt")
                or source.get("snippet")
                or source.get("content")
                or ""
            ).strip()
            if not evidence_id or not source_text:
                continue
            bound_paragraphs = [
                str(index)
                for index, paragraph in enumerate(paragraphs)
                if _supported(paragraph, source_text)
                or _supported(source_text, paragraph)
            ]
            if bound_paragraphs:
                used_evidence_ids.append(evidence_id)
                for index in bound_paragraphs:
                    paragraph_evidence_bindings[index].append(evidence_id)

        semantic_review: dict[str, Any] = {}
        specificity_missing_before_semantic = not matched_fields and not matched_requirements
        legacy_mode = effective_generation_mode in {
            "copy", "light_edit", "restructure"
        }
        require_project_specificity = not legacy_mode
        require_requirement_coverage = effective_generation_mode in {
            "new_write", "restructure"
        }
        semantic_needed = bool(
            (require_project_specificity and not matched_fields and not matched_requirements)
            or (require_requirement_coverage and requirements and not matched_requirements)
            or (require_evidence_use and evidence_rows and not used_evidence_ids)
        )
        # Procurement-policy boilerplate with no lexical project binding is a
        # deterministic specificity failure, not a provider-availability error.
        clearly_generic = bool(
            specificity_missing_before_semantic
            and _CLEARLY_GENERIC_POLICY.search(body)
        )
        if semantic_needed and not clearly_generic:
            semantic_review = _semantic_relevance_review(
                chapter=chapter,
                content=body,
                fact_candidates=fact_candidates,
                requirements=requirement_rows,
                evidence_rows=evidence_rows,
            )
            matched_fact_ids = set(semantic_review.get("matched_fact_ids") or [])
            for candidate in fact_candidates:
                if candidate["id"] in matched_fact_ids:
                    matched_fields.setdefault(candidate["field"], []).append(candidate["text"])
            matched_requirement_ids = set(
                semantic_review.get("matched_requirement_ids") or []
            )
            matched_requirements.extend(
                row["text"]
                for row in requirement_rows
                if row["id"] in matched_requirement_ids and row["text"] not in matched_requirements
            )
            semantic_evidence_ids = set(semantic_review.get("matched_evidence_ids") or [])
            used_evidence_ids.extend(
                evidence_id
                for evidence_id in semantic_evidence_ids
                if evidence_id not in used_evidence_ids
            )

        findings: list[GroundingFinding] = []
        semantic_conflict = semantic_review.get("verdict") == "conflict"
        semantic_relevance_failed = bool(
            semantic_review
            and specificity_missing_before_semantic
            and (
                semantic_review.get("verdict") == "irrelevant"
                or float(semantic_review.get("confidence") or 0) < 0.75
            )
        )
        if semantic_conflict:
            findings.append(
                GroundingFinding(
                    "PROJECT_SEMANTIC_CONFLICT",
                    "正文与当前项目事实存在语义冲突。",
                    {"semantic_review": semantic_review},
                )
            )
        if require_project_specificity and not matched_fields and not matched_requirements:
            findings.append(
                GroundingFinding(
                    "PROJECT_SPECIFICITY_MISSING",
                    "正文未使用当前项目的范围、任务、成果、约束或本章招标要求，不能保存为项目化正文。",
                    {
                        "required_fact_groups": list(relevant_fields),
                        "semantic_review": semantic_review,
                    },
                )
            )
        elif semantic_relevance_failed:
            findings.append(
                GroundingFinding(
                    "PROJECT_SPECIFICITY_MISSING",
                    "正文与本章项目任务的语义关联不足，不能保存为项目化正文。",
                    {"semantic_review": semantic_review},
                )
            )
        if require_requirement_coverage and requirements and not matched_requirements:
            findings.append(
                GroundingFinding(
                    "CHAPTER_REQUIREMENT_MISSING",
                    "正文未响应本章绑定的实际招标或评分要求。",
                    {
                        "requirement_count": len(requirements),
                        "semantic_review": semantic_review,
                    },
                )
            )
        if require_evidence_use and evidence_rows and not used_evidence_ids:
            findings.append(
                GroundingFinding(
                    "PUBLIC_EVIDENCE_NOT_USED",
                    "已检索到相关公开资料，但正文没有形成可核验的段落对应关系。",
                    {"evidence_count": len(evidence_rows)},
                )
            )
        for label, expected in (
            ("项目名称", project_name),
            (
                "采购人",
                _identity_value(
                    global_context,
                    "purchaser",
                    "procurer",
                    "buyer",
                    "采购人",
                    "招标人",
                    "采购单位",
                ),
            ),
        ):
            if not expected:
                continue
            for match in re.finditer(
                rf"(?:^|[；;。\n])\s*{label}\s*[:：]\s*([^；;。\n]+)",
                body,
            ):
                claimed = match.group(1).strip()
                if _compact(expected) not in _compact(claimed):
                    findings.append(
                        GroundingFinding(
                            "PROJECT_FACT_CONFLICT",
                            f"正文中的{label}与全局项目事实冲突。",
                            {"expected": expected, "claimed": claimed},
                        )
                    )

        used_fact_ids = sorted(
            {
                str(item.get("fact_id"))
                for item in confirmed
                if isinstance(item, dict)
                and item.get("fact_id")
                and _binding_supported(
                    str(item.get("statement") or ""), substantive_body
                )
            }
        )
        paragraph_fact_bindings = {
            str(index): sorted(
                {
                    str(item.get("fact_id"))
                    for item in confirmed
                    if isinstance(item, dict)
                    and item.get("fact_id")
                    and _binding_supported(
                        str(item.get("statement") or ""),
                        paragraph.replace(project_name, " "),
                    )
                }
            )
            for index, paragraph in enumerate(paragraphs)
        }
        paragraph_requirement_bindings = {
            str(index): [
                item
                for item in requirements
                if _supported(item, paragraph)
                or _supported(paragraph, item)
            ]
            for index, paragraph in enumerate(paragraphs)
        }
        confirmed_ids = {
            str(item.get("fact_id"))
            for item in confirmed
            if isinstance(item, dict) and item.get("fact_id")
        }
        for index, fact_ids in (semantic_review.get("paragraph_fact_bindings") or {}).items():
            paragraph_fact_bindings.setdefault(str(index), [])
            paragraph_fact_bindings[str(index)] = sorted(
                set(paragraph_fact_bindings[str(index)])
                | {str(item) for item in fact_ids if str(item) in confirmed_ids}
            )[:6]
        requirement_text_by_id = {row["id"]: row["text"] for row in requirement_rows}
        for index, requirement_ids in (
            semantic_review.get("paragraph_requirement_bindings") or {}
        ).items():
            paragraph_requirement_bindings.setdefault(str(index), [])
            paragraph_requirement_bindings[str(index)] = sorted(
                set(paragraph_requirement_bindings[str(index)])
                | {
                    requirement_text_by_id[str(item)]
                    for item in requirement_ids
                    if str(item) in requirement_text_by_id
                }
            )
        for index, evidence_ids in (
            semantic_review.get("paragraph_evidence_bindings") or {}
        ).items():
            paragraph_evidence_bindings.setdefault(str(index), [])
            paragraph_evidence_bindings[str(index)] = sorted(
                set(paragraph_evidence_bindings[str(index)])
                | {str(item) for item in evidence_ids}
            )
        used_fact_ids = sorted(
            set(used_fact_ids)
            | {
                str(item)
                for item in semantic_review.get("matched_fact_ids") or []
                if str(item) in confirmed_ids
            }
        )
        report = {
            "policy_version": GROUNDING_POLICY_VERSION,
            "verdict": "fail" if findings else "pass",
            "global_context_id": context_id,
            "global_context_revision": context_revision,
            "global_context_hash": context_hash,
            "chapter_context_id": chapter_context_id,
            "chapter_context_revision": chapter_context_revision,
            "chapter_context_hash": chapter_context_hash,
            "chapter_id": str(chapter.get("chapter_id") or ""),
            "effective_generation_mode": effective_generation_mode,
            "evaluated_content_hash": canonical_hash(body),
            "chapter_profile": profile,
            "relevance_method": "semantic" if semantic_review else "lexical",
            "semantic_review": semantic_review,
            "repair_attempted": False,
            "repair_succeeded": False,
            "matched_fact_groups": sorted(matched_fields),
            "matched_fact_ids": sorted(
                set(semantic_review.get("matched_fact_ids") or [])
                | set(used_fact_ids)
            ),
            "matched_requirement_count": len(matched_requirements),
            "matched_requirement_ids": [
                row["id"]
                for row in requirement_rows
                if row["text"] in matched_requirements
            ],
            "used_fact_ids": used_fact_ids,
            "used_evidence_ids": sorted(set(used_evidence_ids)),
            "evidence_batch_ids": sorted(
                {
                    str(item.get("batch_id") or "")
                    for item in evidence_rows
                    if str(item.get("batch_id") or "")
                }
            ),
            "paragraph_fact_bindings": paragraph_fact_bindings,
            "paragraph_requirement_bindings": paragraph_requirement_bindings,
            "paragraph_evidence_bindings": {
                key: sorted(set(value))
                for key, value in paragraph_evidence_bindings.items()
            },
            "findings": [finding.as_dict() for finding in findings],
        }
        if findings:
            first = findings[0]
            raise ControlPlaneError(
                first.code,
                first.message,
                status_code=409,
                details=report,
            )
        return report


__all__ = [
    "ContentGroundingGate",
    "GROUNDING_POLICY_VERSION",
]
