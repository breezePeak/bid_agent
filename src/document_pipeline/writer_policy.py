from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from config import get_settings
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from utils import read_json

from .canonicalization import canonical_hash
from .document_planner import CONTENT_UNITS_PATH
from .input_manifest import V3_ROOT


WRITER_IMPLEMENTATION_VERSION = "v3.writer.leaf-chapters-only.v7"
WRITER_PROMPT_VERSION = "v3.writer.leaf-chapters-only.prompt.v7"
RESEARCH_DECISION_POLICY_VERSION = "v3.writer.research.project-relevance.v5"
CONTENT_QUALITY_POLICY_VERSION = "v3.writer.quality.no-rubric.v3"
GLOBAL_GROUNDING_POLICY_VERSION = "v3.global-project-context.v1"
RESEARCH_RELEVANCE_POLICY_VERSION = "v3.project-relevance.v1"

_FORBIDDEN_TEMPLATE_MARKERS = (
    "满分条件",
    "得分任务",
    "本节用于",
    "按已确认的章节边界",
    "章节边界组织响应内容",
    "展开具体响应内容",
    "围绕要求组织方案",
    "招标文件明确的响应范围、实施动作与验收要求组织方案",
    "本节将围绕",
    "评分要求",
    "评分标准",
    "得分点",
)
def writer_model_identity(root: Path, *, deterministic_test: bool = False) -> dict[str, Any]:
    if deterministic_test:
        return {
            "mode": "deterministic_test",
            "provider": "test",
            "base_url": "",
            "model": "deterministic-test-writer",
            "available": True,
        }
    try:
        settings = get_settings(root)
    except Exception as exc:
        return {
            "mode": "production",
            "provider": "",
            "base_url": "",
            "model": "",
            "available": False,
            "error": f"{type(exc).__name__}: {exc}"[:1000],
        }
    return {
        "mode": "production",
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "available": True,
    }


def require_writer_model(root: Path) -> dict[str, Any]:
    identity = writer_model_identity(root)
    if not identity["available"]:
        raise ControlPlaneError(
            "WRITER_MODEL_ACTION_REQUIRED",
            "写作模型未配置或不可用，生成已暂停；不会回退为模板正文。",
            details={"writer_model": identity},
        )
    return identity


def writer_base_fingerprint(
    context: WorkspaceContext,
    *,
    unit_id: str,
    contract_revision: int,
    node_ids: Iterable[str],
    deterministic_test: bool = False,
) -> str:
    store = ControlStore(context)
    dependencies: dict[str, str] = {}
    for kind in (
        "RequirementLedger",
        "ScoreModel",
        "ChapterBlueprint",
        "ProjectModel",
        "TemplateStructureContract",
    ):
        item = store.v3_active_artifact(kind)
        if item is not None:
            dependencies[kind] = str(item.get("artifact_hash") or "")
    return canonical_hash(
        {
            "writer_version": WRITER_IMPLEMENTATION_VERSION,
            "prompt_version": WRITER_PROMPT_VERSION,
            "research_policy_version": RESEARCH_DECISION_POLICY_VERSION,
            "quality_policy_version": CONTENT_QUALITY_POLICY_VERSION,
            "global_grounding_policy_version": GLOBAL_GROUNDING_POLICY_VERSION,
            "research_relevance_policy_version": RESEARCH_RELEVANCE_POLICY_VERSION,
            "model": writer_model_identity(
                context.root,
                deterministic_test=deterministic_test,
            ),
            "unit_id": str(unit_id),
            "contract_revision": int(contract_revision),
            "node_ids": sorted(str(item) for item in node_ids),
            "dependencies": dependencies,
        }
    )


def evidence_bindings(evidence_snapshot: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in evidence_snapshot:
        if not isinstance(item, dict):
            continue
        batch_id = str(item.get("batch_id") or "").strip()
        if not batch_id or batch_id in seen:
            continue
        seen.add(batch_id)
        result.append({"batch_id": batch_id, "snapshot_hash": canonical_hash(item)})
    return sorted(result, key=lambda item: item["batch_id"])


def writer_fingerprint(base_fingerprint: str, bindings: Iterable[dict[str, str]]) -> str:
    return canonical_hash(
        {
            "base_fingerprint": str(base_fingerprint),
            "evidence_batches": list(bindings),
        }
    )


def registered_content_path(
    context: WorkspaceContext,
    unit_id: str,
    state: dict[str, Any],
) -> Path | None:
    registered = str(state.get("output_artifact_id") or "").strip()
    if not registered:
        return None
    content_dir = (context.root / V3_ROOT / "content_units").resolve()
    candidate = (context.root / registered).resolve()
    try:
        relative = candidate.relative_to(content_dir)
    except ValueError as exc:
        raise ControlPlaneError(
            "CONTENT_UNIT_PATH_INVALID",
            "章节正文登记路径不在当前工作区内容目录内。",
            status_code=409,
        ) from exc
    valid_name = (
        candidate.name == f"{unit_id}.json"
        or (
            candidate.name.startswith(f"{unit_id}--")
            and candidate.suffix.lower() == ".json"
        )
    )
    if len(relative.parts) != 1 or not valid_name:
        raise ControlPlaneError(
            "CONTENT_UNIT_PATH_INVALID",
            "章节正文登记路径与章节标识不一致。",
            status_code=409,
        )
    return candidate


def assess_content_unit(
    context: WorkspaceContext,
    unit: dict[str, Any],
    state: dict[str, Any],
    *,
    deterministic_test: bool = False,
) -> dict[str, Any]:
    unit_id = str(unit.get("unit_id") or "")
    expected_base = writer_base_fingerprint(
        context,
        unit_id=unit_id,
        contract_revision=int(unit.get("contract_revision") or 0),
        node_ids=unit.get("node_ids") or [],
        deterministic_test=deterministic_test,
    )
    result: dict[str, Any] = {
        "fresh": False,
        "stale_reason": "",
        "expected_base_fingerprint": expected_base,
        "path": None,
        "payload": {},
    }
    if str(state.get("state") or "") != "completed":
        result["stale_reason"] = str(state.get("stale_reason") or "")
        return result
    try:
        path = registered_content_path(context, unit_id, state)
    except ControlPlaneError as exc:
        result["stale_reason"] = exc.message
        return result
    result["path"] = path
    if path is None or not path.is_file():
        result["stale_reason"] = "章节正文文件不存在，必须重新生成。"
        return result
    try:
        payload = read_json(path)
    except Exception:
        result["stale_reason"] = "章节正文文件结构无效，必须重新生成。"
        return result
    if not isinstance(payload, dict):
        result["stale_reason"] = "章节正文文件结构无效，必须重新生成。"
        return result
    result["payload"] = payload
    stored_fingerprint = str(state.get("writer_fingerprint") or "")
    output_base = str(payload.get("writer_base_fingerprint") or "")
    output_fingerprint = str(payload.get("writer_fingerprint") or "")
    if not stored_fingerprint or not output_base or not output_fingerprint:
        result["stale_reason"] = "旧正文缺少当前写作器指纹，已强制标记为过期。"
        return result
    if output_base != expected_base:
        result["stale_reason"] = "写作器、提示词、模型、研究策略或上游资料已变化，正文必须重写。"
        return result
    if stored_fingerprint != output_fingerprint:
        result["stale_reason"] = "章节状态与正文写作指纹不一致，已拒绝复用。"
        return result
    bindings = [
        item
        for item in (payload.get("evidence_batches") or [])
        if isinstance(item, dict)
    ]
    if writer_fingerprint(output_base, bindings) != output_fingerprint:
        result["stale_reason"] = "正文绑定的证据批次与写作指纹不一致，已拒绝复用。"
        return result
    result["fresh"] = True
    return result


def require_all_content_units_fresh(
    context: WorkspaceContext,
    *,
    deterministic_test: bool = False,
    code: str = "CONTENT_UNITS_STALE",
) -> list[dict[str, Any]]:
    index_path = context.root / CONTENT_UNITS_PATH
    index = read_json(index_path) if index_path.is_file() else {}
    units = [
        item
        for item in (index.get("units") or [])
        if isinstance(item, dict)
    ]
    store = ControlStore(context)
    checked: list[dict[str, Any]] = []
    stale: list[dict[str, str]] = []
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        state = store.content_unit_state(unit_id) or {}
        assessment = assess_content_unit(
            context,
            unit,
            state,
            deterministic_test=deterministic_test,
        )
        checked.append({"unit": unit, "state": state, **assessment})
        if not assessment["fresh"]:
            stale.append(
                {
                    "unit_id": unit_id,
                    "reason": str(
                        assessment.get("stale_reason")
                        or "章节尚未使用当前写作器生成。"
                    ),
                }
            )
    if stale:
        raise ControlPlaneError(
            code,
            "存在未完成或已过期的章节正文，已阻止预览、整合或下载。",
            status_code=409,
            details={"stale_units": stale},
        )
    return checked


def content_quality_findings(
    content: str,
    *,
    source_texts: Iterable[str] = (),
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    compact = re.sub(r"\s+", "", str(content or ""))
    if len(compact) < 160:
        findings.append({"code": "CONTENT_TOO_SHORT", "message": "正文过短，未形成实质方案。"})
    for marker in _FORBIDDEN_TEMPLATE_MARKERS:
        if marker in content:
            findings.append(
                {
                    "code": "TEMPLATE_OR_RUBRIC_TRACE",
                    "message": f"正文包含模板或评分复述痕迹：{marker}",
                }
            )
    paragraphs = [
        re.sub(r"\s+", "", item)
        for item in re.split(r"\n{2,}", content)
        if item.strip()
    ]
    for index, paragraph in enumerate(paragraphs):
        for previous in paragraphs[:index]:
            if paragraph == previous or (
                min(len(paragraph), len(previous)) >= 60
                and SequenceMatcher(None, paragraph, previous).ratio() >= 0.92
            ):
                findings.append(
                    {
                        "code": "DUPLICATE_PARAGRAPH",
                        "message": "正文存在重复或近似重复段落。",
                    }
                )
                break
    normalized_sources = [
        re.sub(r"\s+", "", str(item or ""))
        for item in source_texts
        if str(item or "").strip()
    ]
    for source in normalized_sources:
        if len(source) < 30:
            continue
        if source in compact or SequenceMatcher(None, source, compact).ratio() >= 0.86:
            findings.append(
                {
                    "code": "SOURCE_REQUIREMENT_PARAPHRASE",
                    "message": "正文机械抄写或近似复述了需求/评分原文。",
                }
            )
            break
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in findings:
        key = (item["code"], item["message"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def require_content_quality(
    content: str,
    *,
    source_texts: Iterable[str] = (),
) -> None:
    findings = content_quality_findings(content, source_texts=source_texts)
    if findings:
        raise ControlPlaneError(
            "CONTENT_QUALITY_BLOCKED",
            findings[0]["message"],
            status_code=409,
            details={"findings": findings},
        )
