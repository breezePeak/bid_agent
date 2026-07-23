from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from compliance_checker import DISQUALIFY_LINE_RE, MANDATORY_DOC_PATTERNS
from file_loader import load_tender_requirements
from utils import project_root, read_json, read_text, stringify, write_json

CHECKLIST_VERSION = "1.0.0"
CHECKLIST_PATH = "workspace/materials_checklist.json"
OVERRIDES_PATH = "workspace/manual_review/materials_checklist_overrides.json"

MATERIAL_GAP_START = "<!-- MATERIAL_GAP:"
MATERIAL_GAP_END = "<!-- /MATERIAL_GAP -->"

_CHAPTER_HINTS = {
    "qualification": ("资格", "资质", "审查", "商务", "投标人须知", "响应"),
    "disqualification": ("资格", "商务", "偏离", "承诺", "合规"),
    "mandatory_doc": ("附件", "证明", "资格", "商务", "投标文件组成", "目录"),
    "evidence": ("证明", "材料", "资格", "业绩", "人员", "商务"),
}

_EVIDENCE_PHRASES = ("附后", "复印件", "扫描件", "证书编号", "已提供", "满足", "响应", "承诺", "具备")


def checklist_path(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / CHECKLIST_PATH


def load_materials_checklist(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    path = checklist_path(root)
    if not path.exists():
        return empty_checklist()
    try:
        data = read_json(path)
    except Exception:
        return empty_checklist()
    if not isinstance(data, dict):
        return empty_checklist()
    data.setdefault("version", CHECKLIST_VERSION)
    data.setdefault("items", [])
    data.setdefault("summary", {})
    return data


def empty_checklist() -> dict[str, Any]:
    return {
        "version": CHECKLIST_VERSION,
        "summary": {
            "total": 0,
            "ready": 0,
            "deferred": 0,
            "waived": 0,
            "missing": 0,
            "weak": 0,
            "satisfied": 0,
        },
        "items": [],
    }


def _load_overrides(root: Path) -> dict[str, dict[str, Any]]:
    path = root / OVERRIDES_PATH
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        rows = data["items"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [{"item_id": k, **(v if isinstance(v, dict) else {"response_status": v})} for k, v in data.items()]
    else:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = stringify(row.get("item_id"))
        if not item_id:
            continue
        out[item_id] = row
    return out


def _safe_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return read_text(path)
    except Exception:
        return ""


def _keywords(text: str, limit: int = 6) -> list[str]:
    return [token for token in re.split(r"[，,；;、/\s]+", text) if len(token) >= 2][:limit]


def _find_any(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw and kw in text]


def _snippet(text: str, keyword: str, radius: int = 60) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    return text[start:end].replace("\n", " ").strip()


def _lines_matching(text: str, pattern: re.Pattern[str], limit: int = 40) -> list[str]:
    hits: list[str] = []
    for line in text.splitlines():
        compact = line.strip()
        if not compact:
            continue
        if pattern.search(compact):
            hits.append(compact[:240])
            if len(hits) >= limit:
                break
    return hits


def _evidence_status(requirement: str, search_text: str) -> tuple[str, list[str], float]:
    keywords = _keywords(requirement)
    hits = _find_any(search_text, keywords) if keywords else []
    evidence = [_snippet(search_text, hit) for hit in hits[:3] if _snippet(search_text, hit)]
    strong = bool(hits) and any(phrase in search_text for phrase in _EVIDENCE_PHRASES)
    if not hits:
        return "missing", evidence, 0.72
    if strong:
        return "weak", evidence, 0.55
    return "weak", evidence, 0.5


def _default_response_status(evidence_status: str) -> str:
    if evidence_status == "satisfied":
        return "ready"
    return "deferred"


def _suggested_attachment(requirement: str, category: str) -> str:
    text = requirement
    if "营业执照" in text:
        return "营业执照复印件/扫描件"
    if "授权委托" in text or "授权书" in text:
        return "授权委托书原件或扫描件"
    if "法定代表人" in text:
        return "法定代表人身份证明"
    if "保证金" in text or "保函" in text:
        return "投标保证金凭证/保函"
    if "业绩" in text:
        return "类似项目业绩证明及合同关键页"
    if "人员" in text or "社保" in text:
        return "人员简历/证书/社保缴纳证明"
    if category == "qualification":
        return "资格证明文件及证书扫描件"
    if category == "mandatory_doc":
        return f"{requirement}（按招标文件格式）"
    return "对应证明材料扫描件"


def _placeholder_language(item: dict[str, Any]) -> str:
    req = stringify(item.get("requirement"))[:80]
    att = stringify(item.get("suggested_attachment"))
    return (
        f"拟按招标要求响应：{req}。"
        f"相关证明材料（{att or '对应附件'}）将随投标文件附后提交；"
        "当前正文仅作响应占位，不宣称已具备或已提交。"
    )


def _lifecycle_from_response(response_status: str, evidence_status: str) -> str:
    """PR-13 lifecycle: missing→requested→uploaded→verified→injected→resolved (+ waived/rejected/n/a)."""
    rs = stringify(response_status).lower()
    es = stringify(evidence_status).lower()
    if rs == "waived":
        return "waived"
    if rs == "rejected":
        return "rejected"
    if rs == "not_applicable":
        return "not_applicable"
    if rs == "ready" and es == "satisfied":
        return "resolved"
    if rs == "ready":
        return "uploaded"
    if rs == "deferred":
        return "requested" if es in {"missing", "weak", ""} else "missing"
    if es == "missing":
        return "missing"
    if es == "weak":
        return "requested"
    return "missing"


def _make_item(
    *,
    item_id: str,
    category: str,
    requirement: str,
    requirement_source: dict[str, Any],
    evidence_status: str,
    company_evidence: list[str],
    severity: str,
    check_id_compat: str = "",
    confidence: float = 0.7,
) -> dict[str, Any]:
    response_status = _default_response_status(evidence_status)
    item = {
        "item_id": item_id,
        "category": category,
        "requirement": requirement,
        "requirement_source": requirement_source,
        "evidence_status": evidence_status,
        "response_status": response_status,
        "lifecycle_status": _lifecycle_from_response(response_status, evidence_status),
        "severity": severity,
        "company_evidence": company_evidence,
        "suggested_attachment": _suggested_attachment(requirement, category),
        "suggested_placeholder_language": "",
        "need_manual_review": evidence_status != "satisfied",
        "target_chapter_hints": list(_CHAPTER_HINTS.get(category, ("资格", "商务"))),
        "check_id_compat": check_id_compat or item_id,
        "confidence": confidence,
        "reason": "",
        "affected_chapters": [],
    }
    if evidence_status == "missing":
        item["reason"] = "公司资料中未匹配到对应证据，写作阶段应留白待补"
    elif evidence_status == "weak":
        item["reason"] = "仅命中关键词，证据不足，建议补材料或保守表述"
    else:
        item["reason"] = "资料中已有响应线索"
    item["suggested_placeholder_language"] = _placeholder_language(item)
    return item


def _apply_override(item: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    status = stringify(override.get("response_status") or override.get("status")).lower()
    if status in {"ready", "deferred", "waived", "rejected", "not_applicable"}:
        item["response_status"] = status if status in {"ready", "deferred", "waived"} else item.get("response_status")
        if status in {"waived", "rejected", "not_applicable"}:
            item["response_status"] = "waived" if status == "waived" else item.get("response_status") or "deferred"
            item["lifecycle_status"] = status
    lifecycle = stringify(override.get("lifecycle_status")).lower()
    if lifecycle in {
        "missing",
        "requested",
        "uploaded",
        "verified",
        "injected",
        "resolved",
        "waived",
        "rejected",
        "not_applicable",
    }:
        item["lifecycle_status"] = lifecycle
        if lifecycle in {"uploaded", "verified", "injected", "resolved"}:
            item["response_status"] = "ready"
        elif lifecycle in {"missing", "requested"}:
            item["response_status"] = "deferred"
    elif not item.get("lifecycle_status"):
        item["lifecycle_status"] = _lifecycle_from_response(
            stringify(item.get("response_status")),
            stringify(item.get("evidence_status")),
        )
    note = stringify(override.get("reason") or override.get("note") or override.get("operator_note"))
    if note:
        item["reason"] = note
        item["user_note"] = note
    if stringify(override.get("suggested_attachment")):
        item["suggested_attachment"] = stringify(override.get("suggested_attachment"))
        item["suggested_placeholder_language"] = _placeholder_language(item)
    if stringify(override.get("uploaded_path")):
        item["uploaded_path"] = stringify(override.get("uploaded_path"))
    item["user_override"] = True
    return item


def _summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(items),
        "ready": 0,
        "deferred": 0,
        "waived": 0,
        "missing": 0,
        "weak": 0,
        "satisfied": 0,
    }
    for item in items:
        rs = stringify(item.get("response_status"))
        es = stringify(item.get("evidence_status"))
        if rs in summary:
            summary[rs] += 1
        if es in summary:
            summary[es] += 1
    return summary


def derive_materials_checklist(root: Path | None = None) -> dict[str, Any]:
    """Derive material requirements without creating a state projection.

    V2 command handlers use this pure derivation and persist the resulting
    authority in ``control.db``.  The file-writing wrapper below remains for
    retired V1 worker code only.
    """
    root = root or project_root()
    tender_req = load_tender_requirements(root)
    if not isinstance(tender_req, dict):
        tender_req = {}

    tender_text = _safe_text(root / "inputs" / "tender.md")
    score_text = _safe_text(root / "inputs" / "score.md")
    company_text = _safe_text(root / "inputs" / "company.md")
    company_facts = {}
    facts_path = root / "workspace" / "company_facts.json"
    if facts_path.exists():
        try:
            loaded = read_json(facts_path)
            if isinstance(loaded, dict):
                company_facts = loaded
        except Exception:
            company_facts = {}

    search_text = "\n".join(
        [
            company_text,
            stringify(company_facts.get("bidder_name")),
            "\n".join(stringify(x) for x in company_facts.get("core_products", []) or []),
            "\n".join(stringify(x) for x in company_facts.get("company_advantages", []) or []),
            "\n".join(stringify(x) for x in company_facts.get("similar_cases", []) or []),
            "\n".join(stringify(x) for x in company_facts.get("team_roles", []) or []),
        ]
    )
    tender_all = f"{tender_text}\n{score_text}"
    overrides = _load_overrides(root)
    items: list[dict[str, Any]] = []
    seen_req: set[str] = set()

    # 1) qualification requirements from facts
    quals = tender_req.get("qualification_requirements") if isinstance(tender_req, dict) else []
    if not isinstance(quals, list):
        quals = []
    for index, req in enumerate(quals, start=1):
        req_text = stringify(req).strip()
        if not req_text or req_text in seen_req:
            continue
        seen_req.add(req_text)
        evidence_status, evidence, confidence = _evidence_status(req_text, search_text)
        # never auto-satisfied for qualification certificates
        if evidence_status == "satisfied":
            evidence_status = "weak"
        item = _make_item(
            item_id=f"MAT-QUAL-{index:03d}",
            category="qualification",
            requirement=req_text,
            requirement_source={
                "file": "workspace/tender_requirements.json",
                "field": "qualification_requirements",
            },
            evidence_status=evidence_status,
            company_evidence=evidence,
            severity="critical",
            check_id_compat=f"QUAL-{index:03d}",
            confidence=confidence,
        )
        if item["item_id"] in overrides:
            _apply_override(item, overrides[item["item_id"]])
        items.append(item)

    # 2) evidence notes that look like material requirements
    notes = tender_req.get("evidence_notes") if isinstance(tender_req, dict) else []
    if not isinstance(notes, list):
        notes = []
    note_idx = 0
    material_note_re = re.compile(r"(提供|提交|附|复印件|扫描件|证明|证书|原件|材料|清单|表)")
    for note in notes:
        note_text = stringify(note).strip()
        if not note_text or note_text in seen_req:
            continue
        if not material_note_re.search(note_text):
            continue
        if len(note_text) < 6:
            continue
        seen_req.add(note_text)
        note_idx += 1
        evidence_status, evidence, confidence = _evidence_status(note_text, search_text)
        item = _make_item(
            item_id=f"MAT-EVD-{note_idx:03d}",
            category="evidence",
            requirement=note_text,
            requirement_source={
                "file": "workspace/tender_requirements.json",
                "field": "evidence_notes",
            },
            evidence_status=evidence_status if evidence_status != "satisfied" else "weak",
            company_evidence=evidence,
            severity="major",
            check_id_compat=f"EVD-{note_idx:03d}",
            confidence=confidence,
        )
        if item["item_id"] in overrides:
            _apply_override(item, overrides[item["item_id"]])
        items.append(item)

    # 3) mandatory docs mentioned in tender
    doc_idx = 0
    for name, pattern, severity in MANDATORY_DOC_PATTERNS:
        if not tender_all or not pattern.search(tender_all):
            continue
        doc_idx += 1
        req_text = f"招标文件要求包含：{name}"
        if req_text in seen_req:
            continue
        seen_req.add(req_text)
        found_in_company = bool(pattern.search(search_text)) if search_text else False
        evidence_status = "weak" if found_in_company else "missing"
        evidence = [name] if found_in_company else []
        item = _make_item(
            item_id=f"MAT-DOC-{doc_idx:03d}",
            category="mandatory_doc",
            requirement=req_text,
            requirement_source={"file": "inputs/tender.md", "item": name},
            evidence_status=evidence_status,
            company_evidence=evidence,
            severity=severity,
            check_id_compat=f"DOC-{doc_idx:03d}",
            confidence=0.8,
        )
        if item["item_id"] in overrides:
            _apply_override(item, overrides[item["item_id"]])
        items.append(item)

    # 4) disqualification clauses — awareness, usually deferred for manual confirm
    clauses = _lines_matching(tender_all, DISQUALIFY_LINE_RE, limit=20)
    for index, clause in enumerate(clauses, start=1):
        if clause in seen_req:
            continue
        seen_req.add(clause)
        item = _make_item(
            item_id=f"MAT-DQ-{index:03d}",
            category="disqualification",
            requirement=clause,
            requirement_source={"file": "inputs/tender.md|score.md", "match": "废标/否决投标"},
            evidence_status="missing",
            company_evidence=[],
            severity="critical",
            check_id_compat=f"DQ-{index:03d}",
            confidence=0.55,
        )
        item["reason"] = "废标条款需人工逐条核对；无对应材料时正文不得编造满足证明"
        item["suggested_placeholder_language"] = (
            f"针对废标/否决条款「{clause[:60]}」，我方将按招标文件要求逐项响应并保留证明材料；"
            "当前若材料未齐，仅作合规响应占位，不宣称已通过资格审查。"
        )
        if item["item_id"] in overrides:
            _apply_override(item, overrides[item["item_id"]])
        items.append(item)

    payload = {
        "version": CHECKLIST_VERSION,
        "summary": _summarize(items),
        "items": items,
    }
    return payload


def build_materials_checklist(root: Path | None = None) -> Path:
    """Write the retired V1 checklist projection for legacy worker code."""
    root = root or project_root()
    payload = derive_materials_checklist(root)
    out = checklist_path(root)
    write_json(out, payload)
    print(
        f"[完成] 材料/资格清单: total={payload['summary']['total']} "
        f"deferred={payload['summary']['deferred']} "
        f"ready={payload['summary']['ready']} "
        f"waived={payload['summary']['waived']} → {out}"
    )
    return out


def items_for_chapter(
    root: Path | None,
    chapter: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    *,
    material_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Pick checklist items relevant to a chapter/job for writing constraints."""
    root = root or project_root()
    data = load_materials_checklist(root) if material_items is None else {}
    items = material_items if material_items is not None else data.get("items") if isinstance(data.get("items"), list) else []
    if not items:
        return []

    title = ""
    description = ""
    requirements: list[str] = []
    if isinstance(job, dict):
        title = stringify(job.get("chapter_title"))
        description = stringify(job.get("description"))
        requirements = [stringify(x) for x in job.get("writing_requirements", []) if stringify(x)]
    if isinstance(chapter, dict):
        title = title or stringify(chapter.get("title"))
        description = description or stringify(chapter.get("description"))
        requirements = requirements or [stringify(x) for x in chapter.get("writing_requirements", []) if stringify(x)]

    hay = " ".join([title, description, *requirements]).lower()
    matched: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if stringify(item.get("response_status")) == "ready" and stringify(item.get("evidence_status")) == "satisfied":
            continue
        hints = item.get("target_chapter_hints") if isinstance(item.get("target_chapter_hints"), list) else []
        req = stringify(item.get("requirement"))
        hit = False
        for hint in hints:
            h = stringify(hint)
            if h and h in hay:
                hit = True
                break
        if not hit:
            # keyword overlap with chapter text
            for token in _keywords(req, limit=4):
                if token and token in hay:
                    hit = True
                    break
        if hit:
            matched.append(item)

    # fallback: attach open deferred/waived items to 资格/商务/附件-like chapters only
    if not matched and any(k in hay for k in ("资格", "商务", "附件", "证明", "偏离", "承诺", "投标文件组成")):
        matched = [
            item
            for item in items
            if isinstance(item, dict) and stringify(item.get("response_status")) in {"deferred", "waived", ""}
        ][:12]
    return matched


def render_placeholder_block(item: dict[str, Any]) -> str:
    """Structured placeholder allowed in chapter body (not XXX/TODO)."""
    item_id = stringify(item.get("item_id")) or "MAT-UNKNOWN"
    status = stringify(item.get("response_status")) or "deferred"
    requirement = stringify(item.get("requirement")) or "（未命名材料要求）"
    reason = stringify(item.get("reason")) or "公司资料暂未提供对应证据"
    attachment = stringify(item.get("suggested_attachment")) or "对应证明材料"
    language = stringify(item.get("suggested_placeholder_language")) or _placeholder_language(item)
    return (
        f"{MATERIAL_GAP_START}item_id={item_id} status={status} -->\n"
        f"> **【材料待补 · {item_id}】**  \n"
        f"> **要求**：{requirement}  \n"
        f"> **留白原因**：{reason}  \n"
        f"> **建议附件**：{attachment}  \n"
        f"> **响应口径**：{language}  \n"
        f"> **状态**：{status}（后续人工补充后可定向回填）\n"
        f"{MATERIAL_GAP_END}"
    )


def ensure_placeholders_in_content(content: str, items: list[dict[str, Any]]) -> str:
    """Append missing structured placeholders for deferred items."""
    text = content or ""
    deferred = [
        item
        for item in items
        if isinstance(item, dict) and stringify(item.get("response_status")) == "deferred"
    ]
    if not deferred:
        return text
    missing_blocks: list[str] = []
    for item in deferred:
        item_id = stringify(item.get("item_id"))
        if not item_id:
            continue
        if item_id in text:
            continue
        missing_blocks.append(render_placeholder_block(item))
    if not missing_blocks:
        return text
    suffix = "\n\n### 材料待补清单（系统占位）\n\n" + "\n\n".join(missing_blocks) + "\n"
    return text.rstrip() + "\n" + suffix


def strip_material_gap_blocks(text: str) -> str:
    """Remove structured MATERIAL_GAP blocks for residual-placeholder scanning."""
    if not text:
        return text
    pattern = re.compile(
        r"<!--\s*MATERIAL_GAP:.*?-->.*?<!--\s*/MATERIAL_GAP\s*-->",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub("", text)


def open_deferred_items(root: Path | None = None) -> list[dict[str, Any]]:
    data = load_materials_checklist(root)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return [
        item
        for item in items
        if isinstance(item, dict) and stringify(item.get("response_status")) == "deferred"
    ]


def writing_requirement_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = stringify(item.get("item_id"))
        req = stringify(item.get("requirement"))[:100]
        status = stringify(item.get("response_status")) or "deferred"
        if status == "deferred":
            lines.append(
                f"材料清单[{item_id}/deferred]：{req}；"
                "必须输出结构化 MATERIAL_GAP 占位块，写明留白原因与建议附件；"
                "禁止写已具备/已提供/已完成；禁止使用 XXX/TODO/待填写。"
            )
        elif status == "waived":
            lines.append(
                f"材料清单[{item_id}/waived]：{req}；"
                "用户接受风险，可用保守响应表述，勿编造证明材料，勿假装已提交附件。"
            )
        elif status == "ready":
            lines.append(
                f"材料清单[{item_id}/ready]：{req}；"
                "仅在资料有证据时写成已响应/附后说明，不得夸大。"
            )
    return lines


def load_override_rows(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or project_root()
    overrides = _load_overrides(root)
    return [{"item_id": k, **v} for k, v in overrides.items()]


def save_override_rows(root: Path | None, rows: list[dict[str, Any]]) -> Path:
    root = root or project_root()
    path = root / OVERRIDES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = stringify(row.get("item_id"))
        if not item_id:
            continue
        status = stringify(row.get("response_status") or row.get("status")).lower()
        if status not in {"ready", "deferred", "waived"}:
            continue
        entry: dict[str, Any] = {"item_id": item_id, "response_status": status}
        note = stringify(row.get("reason") or row.get("note") or row.get("operator_note"))
        if note:
            entry["reason"] = note[:500]
        att = stringify(row.get("suggested_attachment"))
        if att:
            entry["suggested_attachment"] = att[:200]
        clean.append(entry)
    write_json(path, {"version": 1, "items": clean})
    return path


def update_item_response(
    root: Path | None,
    item_id: str,
    *,
    response_status: str,
    reason: str = "",
    suggested_attachment: str = "",
    rebuild: bool = True,
) -> dict[str, Any]:
    """Persist user triage for one checklist item and optionally rebuild checklist."""
    root = root or project_root()
    item_id = stringify(item_id)
    status = stringify(response_status).lower()
    if not item_id:
        return {"ok": False, "message": "缺少 item_id"}
    lifecycle_aliases = {
        "missing",
        "requested",
        "uploaded",
        "verified",
        "injected",
        "resolved",
        "rejected",
        "not_applicable",
    }
    if status in lifecycle_aliases:
        # allow lifecycle verbs as response updates
        lifecycle = status
        if status in {"uploaded", "verified", "injected", "resolved"}:
            status = "ready"
        elif status in {"rejected", "not_applicable"}:
            status = "waived" if status == "not_applicable" else "deferred"
        else:
            status = "deferred"
    else:
        lifecycle = ""
    if status not in {"ready", "deferred", "waived"}:
        return {"ok": False, "message": "response_status 必须是 ready/deferred/waived 或 lifecycle 状态"}

    data = load_materials_checklist(root)
    known = {stringify(i.get("item_id")) for i in data.get("items", []) if isinstance(i, dict)}
    if known and item_id not in known:
        return {"ok": False, "message": f"未找到清单项: {item_id}"}

    overrides = _load_overrides(root)
    row = dict(overrides.get(item_id) or {})
    row["item_id"] = item_id
    row["response_status"] = status
    if lifecycle:
        row["lifecycle_status"] = lifecycle
    elif status == "ready":
        row["lifecycle_status"] = "uploaded"
    elif status == "waived":
        row["lifecycle_status"] = "waived"
    else:
        row["lifecycle_status"] = "requested"
    if reason is not None and str(reason).strip():
        row["reason"] = str(reason).strip()[:500]
    if suggested_attachment is not None and str(suggested_attachment).strip():
        row["suggested_attachment"] = str(suggested_attachment).strip()[:200]
    overrides[item_id] = row
    save_override_rows(root, [{"item_id": k, **v} for k, v in overrides.items()])

    checklist = None
    if rebuild:
        build_materials_checklist(root)
        checklist = load_materials_checklist(root)
    return {
        "ok": True,
        "item_id": item_id,
        "response_status": status,
        "lifecycle_status": row.get("lifecycle_status"),
        "override": row,
        "checklist": checklist,
        "message": f"已将 {item_id} 标记为 {status}",
    }


def batch_update_item_responses(
    root: Path | None,
    updates: list[dict[str, Any]],
    *,
    rebuild: bool = True,
) -> dict[str, Any]:
    root = root or project_root()
    if not isinstance(updates, list) or not updates:
        return {"ok": False, "message": "updates 不能为空"}
    overrides = _load_overrides(root)
    data = load_materials_checklist(root)
    known = {stringify(i.get("item_id")) for i in data.get("items", []) if isinstance(i, dict)}
    applied: list[str] = []
    errors: list[str] = []
    for row in updates:
        if not isinstance(row, dict):
            continue
        item_id = stringify(row.get("item_id"))
        status = stringify(row.get("response_status") or row.get("status")).lower()
        if not item_id or status not in {"ready", "deferred", "waived"}:
            errors.append(f"无效更新: {row}")
            continue
        if known and item_id not in known:
            errors.append(f"未找到: {item_id}")
            continue
        entry = dict(overrides.get(item_id) or {})
        entry["item_id"] = item_id
        entry["response_status"] = status
        note = stringify(row.get("reason") or row.get("note"))
        if note:
            entry["reason"] = note[:500]
        att = stringify(row.get("suggested_attachment"))
        if att:
            entry["suggested_attachment"] = att[:200]
        overrides[item_id] = entry
        applied.append(item_id)
    save_override_rows(root, [{"item_id": k, **v} for k, v in overrides.items()])
    checklist = None
    if rebuild:
        build_materials_checklist(root)
        checklist = load_materials_checklist(root)
    return {
        "ok": True,
        "applied": applied,
        "errors": errors,
        "checklist": checklist,
        "message": f"已更新 {len(applied)} 项",
    }


_GAP_ID_RE = re.compile(r"MATERIAL_GAP:item_id=([A-Za-z0-9_\-]+)", re.IGNORECASE)


def material_gap_ids_in_text(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(_GAP_ID_RE.findall(text)))


def chapters_with_material_gaps(root: Path | None = None) -> dict[str, list[str]]:
    """Map chapter_id -> MATERIAL_GAP item_ids found in chapter markdown."""
    root = root or project_root()
    chapters_dir = root / "workspace" / "chapters"
    result: dict[str, list[str]] = {}
    if not chapters_dir.exists():
        return result
    for path in sorted(chapters_dir.glob("*.md")):
        try:
            text = read_text(path)
        except Exception:
            continue
        ids = material_gap_ids_in_text(text)
        if ids:
            result[path.stem] = ids
    return result


def chapters_ready_for_refill(
    root: Path | None = None,
    *,
    material_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Chapters that still contain MATERIAL_GAP for items now marked ready."""
    root = root or project_root()
    data = load_materials_checklist(root) if material_items is None else {}
    status_by_id = {
        stringify(i.get("item_id")): stringify(i.get("response_status"))
        for i in (material_items if material_items is not None else data.get("items", []))
        if isinstance(i, dict)
    }
    plans: list[dict[str, Any]] = []
    for chapter_id, gap_ids in chapters_with_material_gaps(root).items():
        ready_ids = [gid for gid in gap_ids if status_by_id.get(gid) == "ready"]
        if ready_ids:
            plans.append(
                {
                    "chapter_id": chapter_id,
                    "ready_item_ids": ready_ids,
                    "all_gap_ids": gap_ids,
                }
            )
    return plans


def refill_material_gaps(
    root: Path | None = None,
    *,
    chapter_ids: list[str] | None = None,
    replan_jobs: bool = True,
    max_chapters: int = 20,
    material_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    After user marks materials ready (and preferably re-uploads company docs),
    replan jobs and rewrite affected chapters so MATERIAL_GAP slots can be filled.
    """
    root = root or project_root()
    # Retired V1 callers still refresh their file projection. V2 supplies the
    # SQLite material snapshot and must not recreate that projection.
    if material_items is None:
        build_materials_checklist(root)

    plans = chapters_ready_for_refill(root, material_items=material_items)
    if chapter_ids is not None:
        wanted = {str(x) for x in chapter_ids}
        plans = [p for p in plans if p["chapter_id"] in wanted]

    if not plans:
        return {
            "ok": True,
            "rewritten": [],
            "failed": [],
            "plans": [],
            "message": "没有可回填的章节（正文中无已 ready 的 MATERIAL_GAP）。",
        }

    if replan_jobs:
        try:
            from job_planner import plan_chapter_jobs

            plan_chapter_jobs(root, material_items=material_items)
        except Exception as exc:
            return {"ok": False, "message": f"重规划章节任务失败: {exc}", "plans": plans}

    # ensure contexts exist for targets
    try:
        from context_selector import select_context_for_job
        from utils import read_json as _read_json

        for plan in plans[: max(1, max_chapters)]:
            cid = plan["chapter_id"]
            ctx = root / "workspace" / "contexts" / f"{cid}_context.json"
            job_path = root / "workspace" / "jobs" / f"{cid}.json"
            if ctx.exists() or not job_path.exists():
                continue
            job = _read_json(job_path)
            select_context_for_job(job, root)
    except Exception:
        pass

    from chapter_writer import write_chapter

    rewritten: list[str] = []
    failed: list[dict[str, str]] = []
    for plan in plans[: max(1, max_chapters)]:
        cid = plan["chapter_id"]
        try:
            write_chapter(cid, root)
            rewritten.append(cid)
        except Exception as exc:
            failed.append({"chapter_id": cid, "error": str(exc)})

    # invalidate only affected artifacts
    if rewritten:
        try:
            from agent.invalidation import mark_invalidated

            mark_invalidated(
                root,
                reason="material_refill",
                chapter_ids=rewritten,
                source_stage="write_chapters",
            )
        except Exception:
            pass

    # mark lifecycle injected/resolved for ready items that no longer appear as gaps
    try:
        remaining_gaps = chapters_with_material_gaps(root)
        data = load_materials_checklist(root) if material_items is None else {}
        items = material_items if material_items is not None else data.get("items") if isinstance(data.get("items"), list) else []
        gap_item_ids = {gid for ids in remaining_gaps.values() for gid in ids}
        for item in items:
            if not isinstance(item, dict):
                continue
            if stringify(item.get("response_status")) != "ready":
                continue
            iid = stringify(item.get("item_id"))
            if iid and iid not in gap_item_ids:
                item["lifecycle_status"] = "resolved"
            else:
                item["lifecycle_status"] = "injected"
        if material_items is None:
            data["items"] = items
            data["summary"] = _summarize(items)
            write_json(checklist_path(root), data)
    except Exception:
        pass

    # resume goal if blocked on materials
    try:
        from agent.goal import load_goal, resume_goal_after_materials

        goal = load_goal(root) if material_items is None else None
        if goal and str(goal.get("status")) == "blocked_human":
            resume_goal_after_materials(root, note="material_refill")
    except Exception:
        pass

    return {
        "ok": not failed,
        "rewritten": rewritten,
        "failed": failed,
        "plans": plans,
        "resolved_item_ids": [
            stringify(item.get("item_id"))
            for item in items
            if isinstance(item, dict)
            and stringify(item.get("response_status")) == "ready"
            and stringify(item.get("item_id")) not in gap_item_ids
        ],
        "injected_item_ids": [
            stringify(item.get("item_id"))
            for item in items
            if isinstance(item, dict)
            and stringify(item.get("response_status")) == "ready"
            and stringify(item.get("item_id")) in gap_item_ids
        ],
        "recovery_plan": {
            "chapter_ids": rewritten,
            "invalidate": ["reviews", "summaries", "coverage", "export"],
            "full_rerun": False,
        },
        "message": (
            f"材料回填完成：成功 {len(rewritten)} 章"
            + (f"，失败 {len(failed)} 章" if failed else "")
        ),
    }


def mark_material_uploaded(
    root: Path | None,
    item_id: str,
    *,
    uploaded_path: str = "",
    note: str = "",
    rebuild: bool = True,
    auto_verify: bool = True,
) -> dict[str, Any]:
    """Mark material as uploaded; optionally run authenticity verify (PR-A5).

    uploaded ≠ verified. Only verified (or human-confirmed) may close qualification gaps.
    """
    root = root or project_root()
    item_id = stringify(item_id)
    if not item_id:
        return {"ok": False, "message": "缺少 item_id"}

    result = update_item_response(
        root,
        item_id,
        response_status="ready",
        reason=note or "材料已上传",
        rebuild=rebuild,
    )
    if not result.get("ok"):
        return result

    # attach lifecycle + path — start as uploaded only
    overrides = _load_overrides(root)
    row = dict(overrides.get(item_id) or {})
    row["item_id"] = item_id
    row["response_status"] = "ready"
    row["lifecycle_status"] = "uploaded"
    if uploaded_path:
        row["uploaded_path"] = str(uploaded_path)[:500]
    if note:
        row["reason"] = str(note)[:500]
    overrides[item_id] = row
    save_override_rows(root, [{"item_id": k, **v} for k, v in overrides.items()])

    verification: dict[str, Any] = {}
    lifecycle = "uploaded"
    if auto_verify and uploaded_path:
        try:
            from agent.material_verifier import verify_material

            verification = verify_material(
                root,
                item_id,
                uploaded_path=uploaded_path,
                note=note,
            )
            lifecycle = str(verification.get("lifecycle_status") or "uploaded")
            # never auto-resolve from keyword alone
            if lifecycle == "verified":
                row["lifecycle_status"] = "verified"
                row["verification_confidence"] = verification.get("confidence")
                row["verified_at"] = verification.get("message")
            elif lifecycle == "rejected":
                row["lifecycle_status"] = "rejected"
                row["response_status"] = "missing"
                row["reason"] = str(verification.get("message") or "材料验证未通过")[:500]
            else:
                row["lifecycle_status"] = "uploaded"
                row["needs_human_verify"] = bool(verification.get("needs_human", True))
            overrides[item_id] = row
            save_override_rows(root, [{"item_id": k, **v} for k, v in overrides.items()])
        except Exception as exc:
            verification = {"ok": False, "message": f"verify_error: {exc}", "lifecycle_status": "uploaded"}

    if rebuild:
        build_materials_checklist(root)

    affected = affected_chapters_for_items(root, [item_id])
    recovery = build_material_recovery_plan(root, item_ids=[item_id], chapter_ids=affected)

    # Only resume goal when verified (not merely uploaded)
    if lifecycle == "verified":
        try:
            from agent.goal import load_goal, resume_goal_after_materials

            goal = load_goal(root)
            if goal and str(goal.get("status")) == "blocked_human":
                resume_goal_after_materials(root, note=f"material_verified:{item_id}")
        except Exception:
            pass

    return {
        "ok": True,
        "item_id": item_id,
        "lifecycle_status": lifecycle,
        "verification": verification,
        "affected_chapters": affected,
        "recovery_plan": recovery,
        "message": (
            f"材料 {item_id} 状态={lifecycle}，影响章节 {affected or '待识别'}"
            + ("" if lifecycle == "verified" else "（uploaded 不等于 verified，需验证通过后才关闭缺口）")
        ),
    }


def affected_chapters_for_items(root: Path | None, item_ids: list[str]) -> list[str]:
    root = root or project_root()
    wanted = {str(x) for x in item_ids if str(x).strip()}
    if not wanted:
        return []
    chapters: list[str] = []
    # from MATERIAL_GAP in chapter text
    for cid, gaps in chapters_with_material_gaps(root).items():
        if any(g in wanted for g in gaps):
            chapters.append(cid)
    # from checklist hints + jobs
    data = load_materials_checklist(root)
    hint_tokens: list[str] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        if stringify(item.get("item_id")) not in wanted:
            continue
        for h in item.get("target_chapter_hints") or []:
            if stringify(h):
                hint_tokens.append(stringify(h))
    jobs_dir = root / "workspace" / "jobs"
    if jobs_dir.exists() and hint_tokens:
        for path in sorted(jobs_dir.glob("*.json")):
            try:
                job = read_json(path)
            except Exception:
                continue
            blob = " ".join(
                [
                    stringify(job.get("chapter_id")),
                    stringify(job.get("chapter_title")),
                    stringify(job.get("description")),
                ]
            )
            if any(tok in blob for tok in hint_tokens):
                cid = stringify(job.get("chapter_id")) or path.stem
                if cid not in chapters:
                    chapters.append(cid)
    return chapters[:50]


def build_material_recovery_plan(
    root: Path | None,
    *,
    item_ids: list[str] | None = None,
    chapter_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Minimal recovery plan after materials update — never full pipeline rerun."""
    root = root or project_root()
    chapters = list(chapter_ids or [])
    if not chapters and item_ids:
        chapters = affected_chapters_for_items(root, item_ids)
    steps = [
        {"step_id": "rebuild_checklist", "tool": "run_stage", "args": {"command": "build-materials-checklist"}},
    ]
    if chapters:
        steps.append(
            {
                "step_id": "rewrite_affected",
                "tool": "rewrite_chapters",
                "args": {"chapter_ids": chapters[:20]},
                "depends_on": ["rebuild_checklist"],
            }
        )
        steps.append(
            {
                "step_id": "review_affected",
                "tool": "review_chapters",
                "args": {"chapter_ids": chapters[:20]},
                "depends_on": ["rewrite_affected"],
            }
        )
        steps.append(
            {
                "step_id": "recheck_coverage",
                "tool": "analyze_coverage",
                "args": {"rebuild": True, "max_chapters": 5},
                "depends_on": ["review_affected"],
            }
        )
    return {
        "full_rerun": False,
        "chapter_ids": chapters,
        "item_ids": list(item_ids or []),
        "steps": steps,
        "invalidate": [
            "workspace/reviews",
            "workspace/summaries",
            "workspace/score_coverage_matrix.json",
            "outputs/final.md",
            "outputs/final.docx",
        ],
    }


def revalidate_issues_after_materials(root: Path | None = None) -> dict[str, Any]:
    """Re-sync issues after materials filled; close NEED_EVIDENCE when resolved."""
    root = root or project_root()
    try:
        from agent.issues import load_open_issues, save_open_issues
        from agent.root_cause import sync_issues_from_review_fix

        # re-collect evidence status from reviews
        need_evidence: list[str] = []
        stuck: list[str] = []
        need_rewrite: list[str] = []
        reviews_dir = root / "workspace" / "reviews"
        if reviews_dir.exists():
            for rf in reviews_dir.glob("*_review.json"):
                try:
                    review = read_json(rf)
                except Exception:
                    continue
                if not isinstance(review, dict):
                    continue
                cid = stringify(review.get("chapter_id")) or rf.stem.replace("_review", "")
                status = stringify(review.get("rewrite_status"))
                if status == "stuck" or review.get("stuck"):
                    stuck.append(cid)
                elif status == "need_evidence" or (
                    review.get("need_evidence") and not review.get("has_writing_fixes", True)
                ):
                    need_evidence.append(cid)
                elif review.get("need_rewrite"):
                    need_rewrite.append(cid)
        sync_issues_from_review_fix(
            root,
            need_rewrite_ids=need_rewrite,
            need_evidence_ids=need_evidence,
            stuck_ids=stuck,
        )
        return {
            "ok": True,
            "need_evidence": need_evidence,
            "stuck": stuck,
            "need_rewrite": need_rewrite,
            "open_count": len(load_open_issues(root)),
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
