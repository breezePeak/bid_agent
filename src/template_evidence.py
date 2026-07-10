from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from utils import project_root, read_json, stringify, write_json


SOURCE_HINTS: dict[str, list[str]] = {
    "tender.requirements": ["招标要求", "采购需求", "技术要求", "服务要求", "工作内容"],
    "tender.work_content": ["工作内容", "实施内容", "服务内容", "任务"],
    "tender.service_period": ["服务期限", "履约期限", "交付期限", "工期"],
    "tender.service_requirements": ["服务要求", "响应要求", "售后", "保障"],
    "tender.acceptance": ["验收", "成果", "检查", "质量"],
    "tender.contract": ["合同", "保密", "档案", "付款"],
    "tender.qualifications": ["资格", "资质", "信用", "声明", "承诺"],
    "score_points": ["评分", "评审", "分值", "得分", "响应策略"],
    "company.facts": ["公司", "供应商", "我公司", "事实"],
    "company.technical_capability": ["技术能力", "技术方案", "方法", "平台"],
    "company.delivery_experience": ["实施经验", "交付经验", "类似项目", "案例"],
    "company.experience": ["经验", "案例", "业绩"],
    "company.team_roles": ["团队", "人员", "职责", "项目组"],
    "company.qualifications": ["资质", "证书", "资格", "能力"],
    "company.equipment": ["设备", "工具", "投入"],
    "company.resources": ["资源", "保障", "投入"],
    "company.quality_system": ["质量体系", "质量控制", "检查"],
    "company.compliance": ["合规", "保密", "制度", "信用"],
    "company.after_sales": ["售后", "服务保障", "响应"],
}

SEMANTIC_HINTS: dict[str, list[str]] = {
    "project_name": ["项目名称", "采购项目名称"],
    "project_no": ["项目编号", "招标编号", "采购编号"],
    "package_no": ["包号", "标包"],
    "purchaser": ["采购人", "采购单位", "招标人"],
    "bidder_name": ["投标人", "供应商", "公司名称"],
    "deadline": ["服务期限", "履约期限", "交付期限"],
    "location": ["服务地点", "实施地点", "交付地点"],
    "budget": ["预算", "最高限价"],
    "payment": ["付款", "支付"],
    "acceptance": ["验收", "验收标准"],
    "requirement": ["招标要求", "文件要求", "采购需求"],
    "supplier": ["供应商响应", "投标响应", "我方指标"],
    "response": ["响应程度", "偏离", "完全响应"],
}

FACT_KEYS_BY_SOURCE: dict[str, list[str]] = {
    "tender.requirements": ["project_name", "project_location", "service_period", "warranty_period"],
    "tender.work_content": ["project_name", "core_products"],
    "tender.service_period": ["service_period"],
    "tender.acceptance": ["warranty_period"],
    "tender.qualifications": ["bidder_name"],
    "company.facts": ["bidder_name", "core_products", "company_advantages", "similar_cases", "team_roles"],
    "company.technical_capability": ["core_products", "company_advantages"],
    "company.delivery_experience": ["similar_cases", "company_advantages"],
    "company.experience": ["similar_cases", "company_advantages"],
    "company.team_roles": ["team_roles"],
    "company.qualifications": ["bidder_name", "company_advantages"],
    "company.equipment": ["core_products", "company_advantages"],
    "company.resources": ["company_advantages", "team_roles"],
    "company.quality_system": ["company_advantages"],
    "company.compliance": ["company_advantages"],
    "company.after_sales": ["company_advantages", "team_roles"],
}

PUNCT_RE = re.compile(r"[\s，。、；：？！“”‘’（）【】《》…—～\u3000,.;:?!()\[\]{}<>/\\|@#$%^&*+=~`_-]+")
STOPWORDS = {
    "项目", "方案", "服务", "要求", "进行", "提供", "包括", "相关", "内容", "根据",
    "投标", "招标", "采购", "建设", "管理", "技术", "工作", "实施", "保障", "响应",
    "满足", "符合", "确保", "完成", "主要", "具体", "明确", "具有", "关于", "以及",
    "我方", "我公司", "供应商", "文件", "模板", "章节", "标题", "资料", "依据",
}


def _safe_read_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        return read_json(path)
    except Exception:
        return default


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF)


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for part in PUNCT_RE.split(stringify(text).lower()):
        if len(part) < 2:
            continue
        if part in STOPWORDS:
            continue
        if any(_is_cjk(char) for char in part) and len(part) > 6:
            for size in (4, 3, 2):
                for start in range(0, len(part) - size + 1, 2):
                    token = part[start : start + size]
                    if token not in STOPWORDS:
                        terms.append(token)
        else:
            terms.append(part)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result


def _source_hints(sources: list[str]) -> list[str]:
    hints: list[str] = []
    for source in sources:
        for hint in SOURCE_HINTS.get(source, []):
            if hint not in hints:
                hints.append(hint)
    return hints


def _query_terms(item: dict[str, Any]) -> list[str]:
    sources = [stringify(source) for source in item.get("evidence_sources", []) if stringify(source)]
    semantic_key = stringify(item.get("semantic_key"))
    pieces = [
        item.get("title"),
        item.get("label"),
        item.get("heading_id"),
        item.get("heading_title"),
        _text_of(item.get("section_path", [])),
        item.get("writing_focus"),
        semantic_key,
        " ".join(sources),
        " ".join(_source_hints(sources)),
        " ".join(SEMANTIC_HINTS.get(semantic_key, [])),
    ]
    terms: list[str] = []
    for piece in pieces:
        for term in _terms(stringify(piece)):
            if term not in terms:
                terms.append(term)
    return terms


def _text_of(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(stringify(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {value}" for key, value in value.items())
    return stringify(value)


def _score_text(query_terms: list[str], title: str, body: str, keywords: Any = None) -> tuple[float, list[str]]:
    if not query_terms:
        return 0.0, []
    title_text = stringify(title).lower()
    body_text = stringify(body).lower()
    keyword_text = _text_of(keywords).lower()
    score = 0.0
    reasons: list[str] = []
    for term in query_terms:
        term_score = 0.0
        if term in title_text:
            term_score += 5.0
        if term in keyword_text:
            term_score += 3.0
        hits = body_text.count(term)
        if hits:
            term_score += min(hits, 4) * 1.2
        if term_score:
            score += term_score
            if len(reasons) < 5:
                reasons.append(term)
    return score, reasons


def _rank_chunks(item: dict[str, Any], chunks: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    query_terms = _query_terms(item)
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        title = _text_of(chunk.get("title_path"))
        content = stringify(chunk.get("content"))
        score, reasons = _score_text(query_terms, title, content, chunk.get("keywords"))
        if score <= 0:
            continue
        ranked.append(
            {
                "id": stringify(chunk.get("id")),
                "source": stringify(chunk.get("source")),
                "title_path": chunk.get("title_path", []),
                "score": round(score, 2),
                "matched_terms": reasons,
                "preview": content[:240],
            }
        )
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[:top_k]


def _rank_score_points(item: dict[str, Any], score_points: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    query_terms = _query_terms(item)
    ranked: list[dict[str, Any]] = []
    for point in score_points:
        text = " ".join(
            stringify(point.get(key))
            for key in ["category", "title", "requirement", "response_strategy"]
        )
        score, reasons = _score_text(query_terms, stringify(point.get("title")), text, point.get("keywords"))
        if score <= 0:
            continue
        ranked.append(
            {
                "id": stringify(point.get("id")),
                "title": stringify(point.get("title")),
                "score_value": point.get("score"),
                "match_score": round(score, 2),
                "matched_terms": reasons,
            }
        )
    ranked.sort(key=lambda row: row["match_score"], reverse=True)
    return ranked[:top_k]


def _relevant_global_facts(item: dict[str, Any], global_facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(global_facts, dict):
        return {}
    keys: list[str] = []
    semantic_key = stringify(item.get("semantic_key"))
    if semantic_key in global_facts:
        keys.append(semantic_key)
    for source in item.get("evidence_sources", []):
        for key in FACT_KEYS_BY_SOURCE.get(stringify(source), []):
            if key not in keys:
                keys.append(key)
    facts: dict[str, Any] = {}
    for key in keys:
        value = global_facts.get(key)
        if value in ("", None, [], {}):
            continue
        facts[key] = value
    return facts


def _relevant_tender_requirements(item: dict[str, Any], tender_requirements: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tender_requirements, dict):
        return {}
    wanted: list[str] = []
    for source in item.get("evidence_sources", []):
        source = stringify(source)
        if source in {"tender.requirements", "tender.work_content"}:
            wanted.extend(["procurement_scope", "functional_requirements", "implementation_requirements"])
        elif source == "tender.service_requirements":
            wanted.extend(["service_requirements", "delivery_requirements"])
        elif source == "tender.service_period":
            wanted.extend(["service_period"])
        elif source == "tender.acceptance":
            wanted.extend(["acceptance_requirements", "warranty_period"])
        elif source == "tender.qualifications":
            wanted.extend(["qualification_requirements"])
    result: dict[str, Any] = {}
    for key in wanted:
        if key in result:
            continue
        value = tender_requirements.get(key)
        if value in ("", None, [], {}):
            continue
        result[key] = value[:6] if isinstance(value, list) else value
    return result


def _source_types(sources: list[str]) -> list[str]:
    types: list[str] = []
    for source in sources:
        source = stringify(source)
        if source.startswith("tender.") and "tender" not in types:
            types.append("tender")
        elif source.startswith("company.") and "company" not in types:
            types.append("company")
        elif source == "score_points" and "score_points" not in types:
            types.append("score_points")
    return types


def _source_availability(
    tender_chunks: list[dict[str, Any]],
    company_chunks: list[dict[str, Any]],
    score_points: list[dict[str, Any]],
    global_facts: dict[str, Any],
    tender_requirements: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tender_chunks": len(tender_chunks),
        "company_chunks": len(company_chunks),
        "score_points": len(score_points),
        "global_fact_keys": sorted(key for key, value in global_facts.items() if value not in ("", None, [], {})),
        "tender_requirement_keys": sorted(key for key, value in tender_requirements.items() if value not in ("", None, [], {})),
    }


def _source_balance(evidence: dict[str, Any]) -> dict[str, int]:
    return {
        "tender_chunks": len(evidence.get("tender_chunks", [])),
        "company_chunks": len(evidence.get("company_chunks", [])),
        "score_points": len(evidence.get("score_points", [])),
        "global_facts": len(evidence.get("global_facts", {})),
        "tender_requirements": len(evidence.get("tender_requirements", {})),
    }


def _item_risk(item: dict[str, Any], evidence: dict[str, Any], status: str) -> dict[str, Any]:
    balance = _source_balance(evidence)
    required_types = _source_types(item.get("evidence_sources", []))
    gaps: list[str] = []
    if "tender" in required_types and not (balance["tender_chunks"] or balance["tender_requirements"]):
        gaps.append("缺少招标依据")
    if "company" in required_types and not (balance["company_chunks"] or balance["global_facts"]):
        gaps.append("缺少公司依据")
    if "score_points" in required_types and not balance["score_points"]:
        gaps.append("缺少评分点依据")

    if status == "missing" or gaps:
        risk_level = "high"
        action = "停止后续生成，补充资料或调整模板语义规则后重新执行 build-template-evidence"
    elif status == "weak":
        risk_level = "medium"
        action = "允许继续前需人工确认弱证据，写作时只能谨慎表述"
    else:
        risk_level = "low"
        action = "可作为后续大纲、上下文选择和写作依据"

    return {
        "risk_level": risk_level,
        "required_source_types": required_types,
        "source_balance": balance,
        "evidence_gaps": gaps,
        "needs_manual_review": risk_level in {"high", "medium"},
        "recommended_action": action,
    }


def _normalize_item(item: dict[str, Any], item_type: str) -> dict[str, Any]:
    return {
        "id": stringify(item.get("id")),
        "type": item_type,
        "location": stringify(item.get("location")),
        "heading_id": stringify(item.get("heading_id")),
        "heading_title": stringify(item.get("heading_title")),
        "section_path": item.get("section_path", []) if isinstance(item.get("section_path"), list) else [],
        "parent_id": stringify(item.get("parent_id")),
        "title": stringify(item.get("title")),
        "label": stringify(item.get("label")),
        "semantic_key": stringify(item.get("semantic_key")),
        "required": bool(item.get("required", True)),
        "evidence_sources": [stringify(source) for source in item.get("evidence_sources", []) if stringify(source)],
        "writing_focus": stringify(item.get("writing_focus")),
        "fill_strategy": stringify(item.get("fill_strategy")),
    }


def _template_contract(schema: dict[str, Any]) -> dict[str, Any]:
    headings = schema.get("headings", []) if isinstance(schema.get("headings"), list) else []
    tables = schema.get("tables", []) if isinstance(schema.get("tables"), list) else []
    fill_slots = schema.get("fill_slots", []) if isinstance(schema.get("fill_slots"), list) else []
    writing_tasks = schema.get("writing_tasks", []) if isinstance(schema.get("writing_tasks"), list) else []
    return {
        "required_headings": [
            {
                "id": stringify(item.get("id")),
                "title": stringify(item.get("title")),
                "level": item.get("level"),
                "style": stringify(item.get("style")),
            }
            for item in headings
            if isinstance(item, dict)
        ],
        "table_contracts": [
            {
                "index": table.get("index"),
                "type": stringify(table.get("type")),
                "rows": table.get("rows"),
                "columns": table.get("columns"),
                "semantic_columns": table.get("semantic_columns", {}),
                "row_labels": table.get("row_labels", [])[:20],
                "must_preserve": True,
                "fill_required": stringify(table.get("type")) in {"requirement_response", "labeled_fields"},
            }
            for table in tables
            if isinstance(table, dict)
        ],
        "required_fill_slot_ids": [
            stringify(item.get("id"))
            for item in fill_slots
            if isinstance(item, dict) and item.get("required", True)
        ],
        "required_writing_task_ids": [
            stringify(item.get("id"))
            for item in writing_tasks
            if isinstance(item, dict) and item.get("required", True)
        ],
    }


def _section_evidence_matrix(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        heading_id = stringify(item.get("heading_id")) or "__document_slots__"
        row = grouped.setdefault(
            heading_id,
            {
                "heading_id": heading_id,
                "item_count": 0,
                "mapped_count": 0,
                "weak_count": 0,
                "missing_count": 0,
                "high_risk_count": 0,
                "medium_risk_count": 0,
                "fill_slot_ids": [],
                "writing_task_ids": [],
                "tender_chunk_ids": [],
                "company_chunk_ids": [],
                "score_point_ids": [],
            },
        )
        row["item_count"] += 1
        status = stringify(item.get("status"))
        if status == "mapped":
            row["mapped_count"] += 1
        elif status == "weak":
            row["weak_count"] += 1
        elif status == "missing":
            row["missing_count"] += 1
        risk_level = stringify((item.get("analysis") or {}).get("risk_level"))
        if risk_level == "high":
            row["high_risk_count"] += 1
        elif risk_level == "medium":
            row["medium_risk_count"] += 1
        target = row["fill_slot_ids"] if item.get("type") == "fill_slot" else row["writing_task_ids"]
        target.append(stringify(item.get("id")))
        evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        for chunk in evidence.get("tender_chunks", []):
            chunk_id = stringify(chunk.get("id")) if isinstance(chunk, dict) else ""
            if chunk_id and chunk_id not in row["tender_chunk_ids"]:
                row["tender_chunk_ids"].append(chunk_id)
        for chunk in evidence.get("company_chunks", []):
            chunk_id = stringify(chunk.get("id")) if isinstance(chunk, dict) else ""
            if chunk_id and chunk_id not in row["company_chunk_ids"]:
                row["company_chunk_ids"].append(chunk_id)
        for point in evidence.get("score_points", []):
            point_id = stringify(point.get("id")) if isinstance(point, dict) else ""
            if point_id and point_id not in row["score_point_ids"]:
                row["score_point_ids"].append(point_id)

    matrix = list(grouped.values())
    matrix.sort(key=lambda row: (row["heading_id"] == "__document_slots__", row["heading_id"]))
    return matrix


def _item_status(
    item: dict[str, Any],
    tender_chunks: list[dict[str, Any]],
    company_chunks: list[dict[str, Any]],
    score_points: list[dict[str, Any]],
    global_facts: dict[str, Any],
    tender_requirements: dict[str, Any],
) -> tuple[str, float, list[str]]:
    score = 0.0
    notes: list[str] = []
    sources = item.get("evidence_sources", [])
    if any(stringify(source).startswith("tender.") for source in sources) and tender_chunks:
        score += 0.25
    if any(stringify(source).startswith("company.") for source in sources) and company_chunks:
        score += 0.25
    if "score_points" in sources and score_points:
        score += 0.2
    if global_facts:
        score += 0.15
    if tender_requirements:
        score += 0.15

    if not tender_chunks and any(stringify(source).startswith("tender.") for source in sources):
        notes.append("缺少招标文件相关片段")
    if not company_chunks and any(stringify(source).startswith("company.") for source in sources):
        notes.append("缺少公司资料相关片段")
    if not score_points and "score_points" in sources:
        notes.append("缺少评分点匹配")

    confidence = round(min(score, 1.0), 2)
    if confidence >= 0.45:
        return "mapped", confidence, notes
    if confidence >= 0.2:
        return "weak", confidence, notes
    return "missing", confidence, notes or ["未找到可用事实依据"]


def build_template_evidence(root: Path | None = None) -> tuple[Path, Path]:
    root = root or project_root()
    workspace = root / "workspace"
    schema_path = workspace / "template_schema.json"
    evidence_path = workspace / "template_evidence_map.json"
    quality_path = workspace / "template_quality_report.json"

    schema = _safe_read_json(schema_path, {})
    if not isinstance(schema, dict) or not schema.get("exists", False):
        evidence = {
            "template_fingerprint": {},
            "summary": {"item_count": 0, "mapped_count": 0, "weak_count": 0, "missing_count": 0},
            "items": [],
        }
        quality = {
            "ok": True,
            "ok_count": 1,
            "warn_count": 1,
            "fail_count": 0,
            "results": [
                {
                    "name": "template evidence",
                    "level": "warn",
                    "message": "未发现有效模板 schema，已跳过模板依据映射",
                    "suggestion": "如需严格套模板，请先上传 template.docx 并执行 prepare-inputs",
                }
            ],
        }
        write_json(evidence_path, evidence)
        write_json(quality_path, quality)
        print(f"[警告] 未发现有效模板 schema，已跳过模板依据映射: {evidence_path}")
        return evidence_path, quality_path

    tender_chunks = _safe_read_json(workspace / "chunks" / "tender_chunks.json", [])
    company_chunks = _safe_read_json(workspace / "chunks" / "company_chunks.json", [])
    score_points = _safe_read_json(workspace / "score_points.json", [])
    global_facts = _safe_read_json(workspace / "global_facts.json", {})
    tender_requirements = _safe_read_json(workspace / "tender_requirements.json", {})

    tender_chunks = tender_chunks if isinstance(tender_chunks, list) else []
    company_chunks = company_chunks if isinstance(company_chunks, list) else []
    score_points = score_points if isinstance(score_points, list) else []
    global_facts = global_facts if isinstance(global_facts, dict) else {}
    tender_requirements = tender_requirements if isinstance(tender_requirements, dict) else {}
    availability = _source_availability(tender_chunks, company_chunks, score_points, global_facts, tender_requirements)

    raw_items = [
        *[
            _normalize_item(item, "fill_slot")
            for item in schema.get("fill_slots", [])
            if isinstance(item, dict)
        ],
        *[
            _normalize_item(item, "writing_task")
            for item in schema.get("writing_tasks", [])
            if isinstance(item, dict)
        ],
    ]

    items: list[dict[str, Any]] = []
    for item in raw_items:
        evidence = {
            "tender_chunks": _rank_chunks(item, tender_chunks),
            "company_chunks": _rank_chunks(item, company_chunks),
            "score_points": _rank_score_points(item, score_points),
            "global_facts": _relevant_global_facts(item, global_facts),
            "tender_requirements": _relevant_tender_requirements(item, tender_requirements),
        }
        status, confidence, notes = _item_status(
            item,
            evidence["tender_chunks"],
            evidence["company_chunks"],
            evidence["score_points"],
            evidence["global_facts"],
            evidence["tender_requirements"],
        )
        analysis = _item_risk(item, evidence, status)
        items.append(
            {
                **item,
                "query_terms": _query_terms(item)[:20],
                "evidence": evidence,
                "status": status,
                "confidence": confidence,
                "analysis": analysis,
                "notes": notes,
            }
        )

    mapped_count = sum(1 for item in items if item["status"] == "mapped")
    weak_count = sum(1 for item in items if item["status"] == "weak")
    missing_count = sum(1 for item in items if item["status"] == "missing")
    summary = {
        "item_count": len(items),
        "fill_slot_count": sum(1 for item in items if item["type"] == "fill_slot"),
        "writing_task_count": sum(1 for item in items if item["type"] == "writing_task"),
        "mapped_count": mapped_count,
        "weak_count": weak_count,
        "missing_count": missing_count,
    }
    evidence_map = {
        "generated_from": "workspace/template_schema.json",
        "template_fingerprint": schema.get("fingerprint", {}),
        "summary": summary,
        "source_availability": availability,
        "template_contract": _template_contract(schema),
        "section_evidence_matrix": _section_evidence_matrix(items),
        "items": items,
    }

    results: list[dict[str, str]] = []

    def add(name: str, level: str, message: str, suggestion: str = "") -> None:
        results.append({"name": name, "level": level, "message": message, "suggestion": suggestion})

    if items:
        add("template evidence items", "ok", f"已为模板任务生成依据映射 {len(items)} 项")
    else:
        add("template evidence items", "fail", "模板 schema 中没有可执行的填充槽位或写作任务", "请检查模板是否使用了可识别标题、表头或占位符")
    if missing_count:
        add("template missing evidence", "fail", f"{missing_count} 个模板任务缺少可靠事实依据", "请补充招标文件/公司资料，或扩展模板语义规则")
    else:
        add("template missing evidence", "ok", "模板任务均已找到可用依据")
    if weak_count:
        add("template weak evidence", "warn", f"{weak_count} 个模板任务证据较弱", "建议人工确认这些模板任务，写作时避免写成已具备/已提供")
    else:
        add("template weak evidence", "ok", "模板任务未发现弱证据项")

    schema_summary = schema.get("summary", {}) if isinstance(schema.get("summary"), dict) else {}
    unknown_slots = [
        item for item in schema.get("fill_slots", [])
        if isinstance(item, dict) and stringify(item.get("semantic_key")) == "unknown"
    ]
    if unknown_slots:
        add("template unknown slots", "warn", f"{len(unknown_slots)} 个模板槽位语义未识别", "请补充字段标签或人工确认槽位含义")
    else:
        add("template unknown slots", "ok", "模板填充槽位语义已识别")

    unknown_table_count = int(schema_summary.get("unknown_table_count", 0) or 0)
    if unknown_table_count:
        add("template unknown tables", "warn", f"{unknown_table_count} 个模板表格暂未识别为可自动填充结构", "如这些表格必填，请补充表头语义规则")
    else:
        add("template unknown tables", "ok", "模板表格结构已完成基础识别")

    fail_count = sum(1 for item in results if item["level"] == "fail")
    warn_count = sum(1 for item in results if item["level"] == "warn")
    ok_count = sum(1 for item in results if item["level"] == "ok")
    quality_report = {
        "ok": fail_count == 0,
        "ok_count": ok_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "summary": summary,
        "source_availability": availability,
        "section_evidence_matrix": evidence_map["section_evidence_matrix"],
        "results": results,
    }

    write_json(evidence_path, evidence_map)
    write_json(quality_path, quality_report)
    print(
        f"[完成] 模板依据映射: {summary['mapped_count']} mapped, "
        f"{summary['weak_count']} weak, {summary['missing_count']} missing -> {evidence_path}"
    )
    if fail_count:
        raise RuntimeError(f"模板依据分析失败: {fail_count} 项 fail，请查看 {quality_path}")
    return evidence_path, quality_path


build_template_evidence_map = build_template_evidence
