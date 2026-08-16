"""Workspace-wide project facts inherited by every chapter."""

from __future__ import annotations

import re
import json
from typing import Any, Iterable

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import ChapterGroundingContext, GlobalProjectContext
from .canonicalization import canonical_hash
from .project_model import load_promoted_project_model


_IDENTITY_LABELS = {
    "项目名称": ("project_name", "项目名称", "project", "项目"),
    "采购人": ("purchaser", "procurer", "buyer", "采购人", "招标人", "采购单位"),
    "项目编号": ("project_no", "project_number", "采购编号", "招标编号", "项目编号"),
    "标包": ("package", "lot", "标包", "包号", "包件", "标段"),
}

_SHARED_FACT_LABELS = {
    "项目背景": ("background",),
    "建设目标": ("goals",),
    "工作目标": ("goals",),
    "采购范围": ("scope", "boundaries"),
    "项目范围": ("scope", "boundaries"),
    "任务范围": ("scope", "work_packages"),
    "核心任务": ("work_packages", "processing"),
    "工作任务": ("work_packages", "processing"),
    "工作内容": ("work_packages", "processing"),
    "输入数据": ("inputs",),
    "输出成果": ("outputs", "deliverables"),
    "交付物": ("deliverables", "outputs"),
    "验收条件": ("acceptance_conditions",),
    "服务期限": ("milestones", "constraints"),
}


def _identity_lookup(identity: dict[str, str], aliases: Iterable[str]) -> str:
    lowered = {str(key).casefold(): str(value) for key, value in identity.items()}
    for alias in aliases:
        if alias in identity and str(identity[alias]).strip():
            return str(identity[alias]).strip()
        value = lowered.get(str(alias).casefold(), "").strip()
        if value:
            return value
    return ""


def _compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _bigrams(value: Any) -> set[str]:
    text = _compact(value)
    return {text[index : index + 2] for index in range(max(0, len(text) - 1))}


def _fact_value_supported(value: str, expected_values: list[str]) -> bool:
    compact_value = _compact(value)
    if not compact_value:
        return True
    value_numbers = set(re.findall(r"\d+(?:\.\d+)?", value))
    expected_numbers = {
        number
        for expected in expected_values
        for number in re.findall(r"\d+(?:\.\d+)?", expected)
    }
    if value_numbers and expected_numbers and not value_numbers <= expected_numbers:
        return False
    for expected in expected_values:
        compact_expected = _compact(expected)
        if not compact_expected:
            continue
        if compact_value in compact_expected or compact_expected in compact_value:
            return True
        grams = _bigrams(compact_value)
        if grams and len(grams & _bigrams(compact_expected)) / len(grams) >= 0.28:
            return True
    return False


class GlobalProjectContextService:
    """Resolve the only shared ProjectModel revision and chapter references."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def load_model(self) -> GlobalProjectContext:
        artifact = self.store.v3_active_artifact("ProjectModel")
        if artifact is None:
            raise ControlPlaneError(
                "GLOBAL_PROJECT_CONTEXT_REQUIRED",
                "全局项目事实尚未晋级，已阻止章节生成。",
                status_code=409,
            )
        project = load_promoted_project_model(self.context)
        try:
            return GlobalProjectContext(
                global_context_id=str(
                    artifact.get("artifact_id") or project.project_id
                ),
                global_context_revision=int(
                    artifact.get("revision") or project.revision
                ),
                global_context_hash=str(artifact.get("artifact_hash") or ""),
                project_id=project.project_id,
                identity=dict(project.identity),
                background=list(project.background),
                goals=list(project.goals),
                scope=list(project.scope),
                boundaries=list(project.boundaries),
                work_packages=list(project.work_packages),
                dependencies=list(project.dependencies),
                inputs=list(project.inputs),
                processing=list(project.processing),
                outputs=list(project.outputs),
                deliverables=list(project.deliverables),
                acceptance_conditions=list(project.acceptance_conditions),
                milestones=list(project.milestones),
                roles=list(project.roles),
                risks=list(project.risks),
                constraints=list(project.constraints),
                terminology=dict(project.terminology),
                confirmed_facts=list(project.confirmed_facts),
                unknowns=list(project.unknowns),
            )
        except Exception as exc:
            raise ControlPlaneError(
                "GLOBAL_PROJECT_CONTEXT_INVALID",
                "已晋级的全局项目事实不完整，已阻止章节生成。",
                status_code=409,
                details={"error": f"{type(exc).__name__}: {exc}"[:1000]},
            ) from exc

    def load(self) -> dict[str, Any]:
        return self.load_model().model_dump(mode="json")

    def load_for_deterministic_tests(self) -> dict[str, Any]:
        """Explicit non-production fixture path used by deterministic writers."""
        artifact = self.store.v3_active_artifact("ProjectModel")
        if artifact is None:
            body: dict[str, Any] = {
                "project_id": f"deterministic-test:{self.context.workspace_id}",
                "identity": {"project_name": "确定性测试项目"},
                "background": [],
                "goals": [],
                "scope": [],
                "boundaries": [],
                "work_packages": [],
                "dependencies": [],
                "inputs": [],
                "processing": [],
                "outputs": [],
                "deliverables": [],
                "acceptance_conditions": [],
                "milestones": [],
                "roles": [],
                "risks": [],
                "constraints": [],
                "terminology": {},
                "confirmed_facts": [],
                "unknowns": [],
            }
            fixture_hash = canonical_hash(body)
            return {
                "global_context_id": "deterministic-test-context",
                "global_context_revision": 1,
                "global_context_hash": fixture_hash,
                **body,
            }
        project = load_promoted_project_model(self.context)
        identity = dict(project.identity)
        if not _identity_lookup(
            identity,
            ("project_name", "项目名称", "project", "项目"),
        ):
            identity["project_name"] = project.project_id
        return {
            "global_context_id": str(artifact.get("artifact_id") or project.project_id),
            "global_context_revision": int(artifact.get("revision") or project.revision),
            "global_context_hash": str(artifact.get("artifact_hash") or canonical_hash(project.model_dump(mode="json"))),
            "project_id": project.project_id,
            "identity": identity,
            "background": list(project.background),
            "goals": list(project.goals),
            "scope": list(project.scope),
            "boundaries": list(project.boundaries),
            "work_packages": list(project.work_packages),
            "dependencies": list(project.dependencies),
            "inputs": list(project.inputs),
            "processing": list(project.processing),
            "outputs": list(project.outputs),
            "deliverables": list(project.deliverables),
            "acceptance_conditions": list(project.acceptance_conditions),
            "milestones": list(project.milestones),
            "roles": list(project.roles),
            "risks": list(project.risks),
            "constraints": list(project.constraints),
            "terminology": dict(project.terminology),
            "confirmed_facts": [
                item.model_dump(mode="json") for item in project.confirmed_facts
            ],
            "unknowns": list(project.unknowns),
        }

    def build_chapter_context(
        self,
        chapter_id: str,
        *,
        requirement_excerpts: list[dict[str, Any]] | None = None,
        score_obligations: list[dict[str, Any]] | None = None,
        chapter_context_items: list[dict[str, Any]] | None = None,
        highlighted_fact_ids: list[str] | None = None,
        chapter_context_revision: int = 0,
        chapter_context_hash: str = "",
        global_context_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        global_context = (
            GlobalProjectContext.model_validate(global_context_override)
            if global_context_override is not None
            else self.load_model()
        )
        items = [
            dict(item)
            for item in (chapter_context_items or [])
            if isinstance(item, dict)
        ]
        self.assert_no_conflicts(items, global_context=global_context)
        known_fact_ids = {fact.fact_id for fact in global_context.confirmed_facts}
        selected = list(
            dict.fromkeys(
                str(item)
                for item in (highlighted_fact_ids or [])
                if str(item)
            )
        )
        if highlighted_fact_ids is None:
            selected = self._select_fact_ids(
                global_context,
                requirement_excerpts=requirement_excerpts or [],
                score_obligations=score_obligations or [],
                chapter_context_items=items,
            )
        unknown = set(selected) - known_fact_ids
        if unknown:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_INVALID_FACT_REFERENCE",
                "章节引用了不存在的全局项目事实。",
                status_code=409,
                details={"fact_ids": sorted(unknown)},
            )
        return ChapterGroundingContext(
            chapter_id=str(chapter_id),
            global_context_id=global_context.global_context_id,
            global_context_revision=global_context.global_context_revision,
            global_context_hash=global_context.global_context_hash,
            chapter_context_id=f"chapter-context:{chapter_id}",
            chapter_context_revision=int(chapter_context_revision or 0),
            chapter_context_hash=(
                str(chapter_context_hash).strip()
                or canonical_hash(
                    {
                        "chapter_id": str(chapter_id),
                        "chapter_context_revision": int(
                            chapter_context_revision or 0
                        ),
                        "items": items,
                    }
                )
            ),
            requirement_excerpts=list(requirement_excerpts or []),
            score_obligations=list(score_obligations or []),
            chapter_context_items=items,
            highlighted_fact_ids=selected,
        ).model_dump(mode="json")

    @staticmethod
    def _select_fact_ids(
        global_context: GlobalProjectContext,
        *,
        requirement_excerpts: list[dict[str, Any]],
        score_obligations: list[dict[str, Any]],
        chapter_context_items: list[dict[str, Any]],
        limit: int = 24,
    ) -> list[str]:
        """Select fact references; the facts themselves remain in the global store."""
        focus = json.dumps(
            {
                "requirements": requirement_excerpts,
                "scores": score_obligations,
                "chapter": chapter_context_items,
            },
            ensure_ascii=False,
        )
        focus_grams = _bigrams(focus)
        ranked: list[tuple[float, str]] = []
        for fact in global_context.confirmed_facts:
            grams = _bigrams(fact.statement)
            if not grams:
                continue
            score = len(grams & focus_grams) / len(grams)
            if score >= 0.12:
                ranked.append((score, fact.fact_id))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [fact_id for _score, fact_id in ranked[:limit]]

    @staticmethod
    def prompt_projection(
        global_context: dict[str, Any],
        chapter_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return common core plus chapter-relevant references from the same source."""
        projected = dict(global_context)
        fact_ids = {
            str(item)
            for item in chapter_context.get("highlighted_fact_ids") or []
        }
        facts = global_context.get("confirmed_facts")
        facts = facts if isinstance(facts, list) else []
        projected["confirmed_facts"] = [
            dict(item)
            for item in facts
            if isinstance(item, dict) and str(item.get("fact_id") or "") in fact_ids
        ]
        projected["confirmed_fact_count"] = len(facts)
        projected["selected_fact_ids"] = sorted(fact_ids)
        return projected

    @staticmethod
    def research_anchors(
        global_context: GlobalProjectContext | dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        context = (
            global_context.model_dump(mode="json")
            if isinstance(global_context, GlobalProjectContext)
            else dict(global_context)
        )
        identity = context.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        project_anchors: list[str] = []
        for aliases in (
            ("project_name", "项目名称", "project", "项目"),
            ("project_no", "project_number", "项目编号", "采购编号", "招标编号"),
            ("package", "lot", "标包", "包号", "包件", "标段"),
        ):
            value = _identity_lookup(identity, aliases)
            if len(value) >= 3:
                project_anchors.append(value)
        task_values = [
            *list(context.get("scope") or []),
            *list(context.get("work_packages") or []),
            *list(context.get("processing") or []),
            *list(context.get("outputs") or []),
            *list(context.get("deliverables") or []),
        ]
        task_anchors: list[str] = []
        domain_cues = re.compile(
            r"国土|调查|监测|核查|复核|成果|数据|图斑|内业|外业|质量|验收|云建设|运维"
        )
        generic_anchors = {
            "项目覆盖全国",
            "不含港澳台",
            "服务包括成果接收",
            "任务分发",
            "工作协调",
            "问题讨论",
            "意见反馈",
            "承担成果接收",
            "协调讨论",
        }
        for value in task_values:
            text = re.sub(r"\s+", "", str(value or "")).strip()
            if not text:
                continue
            for part in re.split(r"[，,；;、。：（）()]+", text):
                part = part.strip()
                if part in generic_anchors:
                    continue
                if (
                    4 <= len(part) <= 32
                    and (len(part) >= 8 or domain_cues.search(part))
                ):
                    task_anchors.append(part)
        return (
            list(dict.fromkeys(project_anchors))[:8],
            list(dict.fromkeys(task_anchors))[:24],
        )

    @staticmethod
    def assert_no_conflicts(
        chapter_context_items: list[dict[str, Any]],
        *,
        global_context: GlobalProjectContext | dict[str, Any],
    ) -> None:
        context = (
            global_context.model_dump(mode="json")
            if isinstance(global_context, GlobalProjectContext)
            else dict(global_context)
        )
        identity = context.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        conflicts: list[dict[str, str]] = []
        for item in chapter_context_items:
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            for label, aliases in _IDENTITY_LABELS.items():
                expected = _identity_lookup(identity, aliases)
                if not expected:
                    continue
                match = re.search(
                    rf"(?:^|[；;。\n])\s*{re.escape(label)}\s*[:：]\s*([^；;。\n]+)",
                    body,
                )
                if match and expected not in match.group(1).strip():
                    conflicts.append(
                        {
                            "label": label,
                            "expected": expected,
                            "chapter_value": match.group(1).strip(),
                        }
                    )
            for label, fields in _SHARED_FACT_LABELS.items():
                match = re.search(
                    rf"(?:^|[；;。\n])\s*{re.escape(label)}\s*[:：]\s*([^；;。\n]+)",
                    body,
                )
                if not match:
                    continue
                expected_values = [
                    str(value)
                    for field in fields
                    for value in (context.get(field) or [])
                    if str(value).strip()
                ]
                chapter_value = match.group(1).strip()
                if expected_values and not _fact_value_supported(
                    chapter_value, expected_values
                ):
                    conflicts.append(
                        {
                            "label": label,
                            "expected": "；".join(expected_values[:6]),
                            "chapter_value": chapter_value,
                        }
                    )
        if conflicts:
            raise ControlPlaneError(
                "CHAPTER_CONTEXT_CONFLICT",
                "本章补充内容试图覆盖公共项目事实，请在公共项目事实中统一修改。",
                status_code=409,
                details={"conflicts": conflicts},
            )


__all__ = ["GlobalProjectContextService"]
