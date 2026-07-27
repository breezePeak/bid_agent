from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from utils import project_root, read_json, read_text, stringify, write_json

# 高确定性既成事实措辞：用于判定“硬编造风险”
CERTAINTY_RE = re.compile(
    r"(已具备|已取得|已获得|已通过|已完成|已提供|完全满足|均已落实|拥有|持有|具备)"
)
AMOUNT_CLAIM_RE = re.compile(
    r"(?:合同金额|项目金额|业绩金额|总投资|中标金额|成交金额|金额为|金额达|金额约)?"
    r"[^。；;\n]{0,8}?"
    r"(?:人民币|￥|¥|RMB)?\s*"
    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
    r"(万元|亿元|元)"
)
CERT_CLAIM_RE = re.compile(
    r"((?:ISO\s*\d+[A-Za-z0-9:\-]*)|"
    r"(?:CMMI\s*[1-5级]*)|"
    r"(?:高新(?:技术)?企业)|"
    r"(?:系统集成|信息安全|涉密|安防|建筑|测绘|监理|设计|施工)[^\s，,。；;]{0,12}(?:资质|证书|认证)|"
    r"(?:软件著作权|软著)|"
    r"(?:发明专利|实用新型|外观专利)|"
    r"(?:等保[二三]级)|"
    r"(?:AAA\s*信用)|"
    r"[A-Za-z0-9\u4e00-\u9fff]{2,20}(?:资质|认证|证书))"
)
CASE_CLAIM_RE = re.compile(
    r"((?:为|服务|承接|实施|中标|完成了?)?"
    r"[\u4e00-\u9fffA-Za-z0-9（）()]{4,40}?"
    r"(?:有限公司|股份有限公司|集团|局|厅|中心|医院|大学|学校|银行)"
    r"[^。；;\n]{0,20}?"
    r"(?:项目|工程|系统|平台)?)"
)
YEAR_EXP_RE = re.compile(r"(拥有|具备|深耕|从事)[^。；;\n]{0,12}?(\d{1,2})\s*年")
FORBIDDEN_FABRICATED_MARKERS = (
    "全国领先",
    "行业第一",
    "唯一供应商",
    "垄断",
    "100%通过",
    "零事故",
    "零投诉",
)


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def _safe_read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return read_text(path)
    except Exception:
        return ""


def build_evidence_corpus(root: Path) -> dict[str, Any]:
    """公司/招标可引用证据语料（允许事实来源）。"""
    company_md = _safe_read_text(root / "inputs" / "company.md")
    tender_md = _safe_read_text(root / "inputs" / "tender.md")
    company_facts = _safe_read_json(root / "workspace" / "company_facts.json") or {}
    global_facts = _safe_read_json(root / "workspace" / "global_facts.json") or {}
    tender_req = _safe_read_json(root / "workspace" / "tender_requirements.json") or {}

    fact_bits: list[str] = []
    for blob in (company_facts, global_facts, tender_req):
        if not isinstance(blob, dict):
            continue
        for key, value in blob.items():
            if isinstance(value, list):
                fact_bits.extend(stringify(item) for item in value if stringify(item))
            else:
                text = stringify(value)
                if text:
                    fact_bits.append(text)

    company_text = "\n".join([company_md, *fact_bits])
    # 金额允许出现在公司资料与招标文件（预算/限价）
    amount_text = "\n".join([company_md, tender_md, *fact_bits])
    return {
        "company_text": company_text,
        "amount_text": amount_text,
        "tender_text": tender_md,
        "company_facts": company_facts if isinstance(company_facts, dict) else {},
        "global_facts": global_facts if isinstance(global_facts, dict) else {},
    }


def _amount_key(num: str, unit: str) -> str:
    raw = num.replace(",", "")
    try:
        value = float(raw)
    except Exception:
        return f"{num}{unit}"
    if unit == "万元":
        value *= 10000
    elif unit == "亿元":
        value *= 100000000
    # 归一到整数元，避免 1000.0 / 1000 差异
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}"


def _extract_amount_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for num, unit in AMOUNT_CLAIM_RE.findall(text or ""):
        keys.add(_amount_key(num, unit))
    # 宽松补充：纯数字+元/万元
    for match in re.finditer(r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.[0-9]+)?\s*(万元|亿元|元)", text or ""):
        keys.add(_amount_key(match.group(1), match.group(2)))
    return keys


def _normalize_cert(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def extract_claims(chapter_markdown: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    text = chapter_markdown or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        if any(marker in line for marker in FORBIDDEN_FABRICATED_MARKERS):
            claims.append(
                {
                    "type": "hype",
                    "text": line[:160],
                    "value": next((m for m in FORBIDDEN_FABRICATED_MARKERS if m in line), ""),
                    "certainty": True,
                }
            )

        for num, unit in AMOUNT_CLAIM_RE.findall(line):
            claims.append(
                {
                    "type": "amount",
                    "text": line[:160],
                    "value": f"{num}{unit}",
                    "amount_key": _amount_key(num, unit),
                    "certainty": bool(CERTAINTY_RE.search(line)) or ("合同" in line) or ("中标" in line),
                }
            )

        for cert in CERT_CLAIM_RE.findall(line):
            cert = cert.strip()
            if len(cert) < 3:
                continue
            # 过滤过泛
            if cert in {"资质", "证书", "认证", "相关资质", "相应证书"}:
                continue
            claims.append(
                {
                    "type": "certification",
                    "text": line[:160],
                    "value": cert,
                    "certainty": bool(CERTAINTY_RE.search(line)),
                }
            )

        for case in CASE_CLAIM_RE.findall(line):
            case = case.strip(" ：:，,")
            if len(case) < 6:
                continue
            if not any(token in case for token in ("公司", "局", "厅", "中心", "医院", "大学", "银行", "集团")):
                continue
            claims.append(
                {
                    "type": "case",
                    "text": line[:160],
                    "value": case[:80],
                    "certainty": bool(CERTAINTY_RE.search(line)) or ("中标" in line) or ("完成" in line),
                }
            )

        for match in YEAR_EXP_RE.finditer(line):
            years = match.group(2)
            claims.append(
                {
                    "type": "experience_years",
                    "text": line[:160],
                    "value": f"{years}年",
                    "certainty": True,
                }
            )
    return claims


def _load_chapter_chunks(root: Path, chapter_id: str) -> list[dict[str, Any]]:
    """优先 source_trace 选中 chunk，回退到 context + chunks 全量内容。"""
    chunks: list[dict[str, Any]] = []
    trace_path = root / "workspace" / "source_traces" / f"{chapter_id}_sources.json"
    if trace_path.exists():
        try:
            trace = read_json(trace_path)
        except Exception:
            trace = {}
        if isinstance(trace, dict):
            for key, source in (
                ("selected_company_chunks", "company"),
                ("selected_tender_chunks", "tender"),
                ("selected_reference_chunks", "reference"),
            ):
                for item in trace.get(key) or []:
                    if not isinstance(item, dict):
                        continue
                    chunk_id = stringify(item.get("id"))
                    content = stringify(item.get("content")) or stringify(item.get("content_preview"))
                    if not chunk_id:
                        continue
                    # 尝试补全全文
                    if len(content) < 80:
                        full = _chunk_full_content(root, chunk_id, source)
                        if full:
                            content = full
                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "source": source,
                            "content": content,
                            "selected_reason": stringify(item.get("selected_reason")),
                        }
                    )
    if chunks:
        return chunks

    # 回退 contexts
    context_path = root / "workspace" / "contexts" / f"{chapter_id}_context.json"
    if not context_path.exists():
        return chunks
    try:
        context = read_json(context_path)
    except Exception:
        return chunks
    if not isinstance(context, dict):
        return chunks
    for key, source, filename in (
        ("selected_company_chunks", "company", "company_chunks.json"),
        ("selected_tender_chunks", "tender", "tender_chunks.json"),
        ("selected_reference_chunks", "reference", "reference_chunks.json"),
    ):
        index = _chunk_index_map(root, filename)
        for item in context.get(key) or []:
            if isinstance(item, dict):
                chunk_id = stringify(item.get("id"))
            else:
                chunk_id = stringify(item)
            if not chunk_id or chunk_id not in index:
                continue
            content = stringify(index[chunk_id].get("content"))
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "source": source,
                    "content": content,
                    "selected_reason": stringify(item.get("reason")) if isinstance(item, dict) else "",
                }
            )
    return chunks


def _chunk_index_map(root: Path, filename: str) -> dict[str, dict[str, Any]]:
    path = root / "workspace" / "chunks" / filename
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    return {
        stringify(item.get("id")): item
        for item in data
        if isinstance(item, dict) and stringify(item.get("id"))
    }


def _chunk_full_content(root: Path, chunk_id: str, source: str) -> str:
    filename = {
        "company": "company_chunks.json",
        "reference": "reference_chunks.json",
    }.get(source, "tender_chunks.json")
    index = _chunk_index_map(root, filename)
    item = index.get(chunk_id) or {}
    return stringify(item.get("content"))


def align_claim_to_chunks(claim: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """claim 句子/值 → 选中 chunk 的 span 对齐。"""
    alignments: list[dict[str, Any]] = []
    ctype = claim.get("type")
    value = stringify(claim.get("value"))
    line = stringify(claim.get("text"))
    amount_key = stringify(claim.get("amount_key"))

    for chunk in chunks:
        content = stringify(chunk.get("content"))
        if not content:
            continue
        source = stringify(chunk.get("source"))
        # 外部参考资料可以支撑技术和行业事实，但不能证明投标人的
        # 资质、案例或从业年限。
        if source == "reference" and ctype in {
            "certification",
            "case",
            "experience_years",
        }:
            continue
        score = 0.0
        method = ""
        matched_span = ""

        if ctype == "amount" and amount_key:
            chunk_keys = _extract_amount_keys(content)
            if amount_key in chunk_keys:
                score = 0.95
                method = "amount_key"
                matched_span = value or amount_key
        elif ctype == "certification" and value:
            if _normalize_cert(value) in _normalize_cert(content) or value in content:
                score = 0.9
                method = "substring"
                matched_span = value
        elif ctype == "case" and value:
            core = re.split(r"(有限公司|股份有限公司|集团|局|厅|中心|医院|大学|银行)", value)[0]
            core = core[-12:] if len(core) > 12 else core
            if core and core in content:
                score = 0.88
                method = "entity_core"
                matched_span = core
        elif ctype == "experience_years" and value:
            if value in content:
                score = 0.85
                method = "substring"
                matched_span = value
        elif line and line[:20] in content:
            score = 0.7
            method = "line_substring"
            matched_span = line[:80]

        # 补充：claim 行与 chunk 的 token 重叠
        if score < 0.7 and line:
            line_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", line.lower()))
            content_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", content.lower()))
            if line_tokens:
                overlap = len(line_tokens & content_tokens) / max(len(line_tokens), 1)
                if overlap >= 0.45:
                    score = max(score, min(0.84, 0.5 + overlap))
                    method = method or "token_overlap"
                    matched_span = matched_span or line[:80]

        if score >= 0.7:
            alignments.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "chunk_source": chunk.get("source"),
                    "matched_span": matched_span[:160],
                    "score": round(score, 3),
                    "method": method,
                }
            )

    alignments.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return alignments[:5]


def validate_claims_against_evidence(
    chapter_markdown: str,
    evidence: dict[str, Any],
    *,
    chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claims = extract_claims(chapter_markdown)
    company_text = stringify(evidence.get("company_text"))
    amount_text = stringify(evidence.get("amount_text"))
    company_norm = _normalize_cert(company_text)
    amount_keys = _extract_amount_keys(amount_text)
    chunks = chunks or []

    findings: list[dict[str, Any]] = []
    claim_alignments: list[dict[str, Any]] = []
    grounded_count = 0

    for claim in claims:
        ctype = claim.get("type")
        value = stringify(claim.get("value"))
        certainty = bool(claim.get("certainty"))
        grounded = False
        severity = "major"
        alignments = align_claim_to_chunks(claim, chunks) if chunks else []

        if ctype == "hype":
            grounded = False
            severity = "blocker"
        elif ctype == "amount":
            key = stringify(claim.get("amount_key"))
            grounded = key in amount_keys or any(a.get("method") == "amount_key" for a in alignments)
            severity = "blocker" if certainty and not grounded else ("major" if not grounded else "info")
        elif ctype == "certification":
            grounded = _normalize_cert(value) in company_norm or value in company_text or bool(alignments)
            severity = "blocker" if certainty and not grounded else ("major" if not grounded else "info")
        elif ctype == "case":
            core = re.split(r"(有限公司|股份有限公司|集团|局|厅|中心|医院|大学|银行)", value)[0]
            core = core[-12:] if len(core) > 12 else core
            grounded = bool(core) and ((core in company_text) or bool(alignments))
            severity = "blocker" if certainty and not grounded else ("major" if not grounded else "info")
        elif ctype == "experience_years":
            grounded = value in company_text or bool(alignments)
            severity = "blocker" if not grounded else "info"
        else:
            grounded = True
            severity = "info"

        best = alignments[0] if alignments else None
        claim_alignments.append(
            {
                "claim": {
                    "type": ctype,
                    "value": value,
                    "source_line": claim.get("text", ""),
                    "certainty": certainty,
                },
                "alignments": alignments,
                "grounded": grounded,
                "best_chunk_id": best.get("chunk_id") if isinstance(best, dict) else "",
                "best_score": best.get("score") if isinstance(best, dict) else 0,
            }
        )
        if grounded:
            grounded_count += 1
            continue
        findings.append(
            {
                "type": "fabricated_claim" if certainty else "ungrounded_claim",
                "claim_type": ctype,
                "severity": severity,
                "value": value,
                "description": f"疑似无证据支撑的表述：{claim.get('text', '')}",
                "suggestion": "删除该表述，或改为‘拟/将/按要求附后’，并补充公司资料中的真实证据",
                "source_line": claim.get("text", ""),
                "auto_fixable": certainty is False,
                "alignments": alignments,
                "best_chunk_id": "",
            }
        )

    blockers = [item for item in findings if item.get("severity") == "blocker"]
    majors = [item for item in findings if item.get("severity") == "major"]
    return {
        "claim_count": len(claims),
        "finding_count": len(findings),
        "blocker_count": len(blockers),
        "major_count": len(majors),
        "grounded_count": grounded_count,
        "aligned_count": sum(1 for item in claim_alignments if item.get("alignments")),
        "ok": len(blockers) == 0,
        "need_manual_review": bool(findings),
        "findings": findings[:40],
        "claims_sample": claims[:20],
        "claim_alignments": claim_alignments[:40],
    }


def validate_chapter_claims(root: Path | None, chapter_id: str, chapter_markdown: str) -> dict[str, Any]:
    root = root or project_root()
    evidence = build_evidence_corpus(root)
    chunks = _load_chapter_chunks(root, chapter_id)
    result = validate_claims_against_evidence(chapter_markdown, evidence, chunks=chunks)
    result["chapter_id"] = stringify(chapter_id)
    result["chunk_count"] = len(chunks)
    return result


def validate_all_chapter_claims(root: Path | None = None) -> Path:
    root = root or project_root()
    chapters_dir = root / "workspace" / "chapters"
    evidence = build_evidence_corpus(root)
    items: list[dict[str, Any]] = []
    if chapters_dir.exists():
        for path in sorted(chapters_dir.glob("*.md")):
            chapter_id = path.stem
            markdown = read_text(path)
            chunks = _load_chapter_chunks(root, chapter_id)
            result = validate_claims_against_evidence(markdown, evidence, chunks=chunks)
            result["chapter_id"] = chapter_id
            result["chunk_count"] = len(chunks)
            items.append(result)
            # 回写 source_trace 对齐
            _attach_alignments_to_source_trace(root, chapter_id, result)

    total_findings = sum(int(item.get("finding_count") or 0) for item in items)
    total_blockers = sum(int(item.get("blocker_count") or 0) for item in items)
    total_aligned = sum(int(item.get("aligned_count") or 0) for item in items)
    report = {
        "version": "1.1.0",
        "ok": total_blockers == 0,
        "need_manual_review": total_findings > 0,
        "summary": {
            "chapter_count": len(items),
            "finding_count": total_findings,
            "blocker_count": total_blockers,
            "aligned_count": total_aligned,
        },
        "items": items,
    }
    output = root / "workspace" / "claim_validation_report.json"
    write_json(output, report)
    print(
        f"[完成] claim 防编造检查: chapters={len(items)}, findings={total_findings}, "
        f"blockers={total_blockers}, aligned={total_aligned} -> {output}"
    )
    return output


def _attach_alignments_to_source_trace(root: Path, chapter_id: str, result: dict[str, Any]) -> None:
    trace_path = root / "workspace" / "source_traces" / f"{chapter_id}_sources.json"
    if not trace_path.exists():
        return
    try:
        trace = read_json(trace_path)
    except Exception:
        return
    if not isinstance(trace, dict):
        return
    alignments = result.get("claim_alignments") if isinstance(result.get("claim_alignments"), list) else []
    trace["claim_alignments"] = alignments[:40]
    trace["claim_alignment_summary"] = {
        "claim_count": result.get("claim_count", 0),
        "aligned_count": result.get("aligned_count", 0),
        "finding_count": result.get("finding_count", 0),
        "blocker_count": result.get("blocker_count", 0),
        "grounded_count": result.get("grounded_count", 0),
    }
    write_json(trace_path, trace)


def claim_findings_as_review_problems(result: dict[str, Any]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for item in result.get("findings") or []:
        if not isinstance(item, dict):
            continue
        problems.append(
            {
                "type": stringify(item.get("type")) or "fabricated_claim",
                "severity": stringify(item.get("severity")) or "major",
                "description": stringify(item.get("description")),
                "suggestion": stringify(item.get("suggestion")),
            }
        )
    return problems


def claim_findings_as_priority_fixes(result: dict[str, Any]) -> list[dict[str, Any]]:
    fixes: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("findings") or [], start=1):
        if not isinstance(item, dict):
            continue
        severity = stringify(item.get("severity")) or "major"
        if severity not in {"blocker", "major"}:
            continue
        fixes.append(
            {
                "id": f"CLAIM-{index:03d}",
                "severity": severity,
                "problem_type": stringify(item.get("type")) or "fabricated_claim",
                "target": stringify(item.get("value")) or "无证据表述",
                "action": stringify(item.get("suggestion"))
                or "删除无证据既成事实，或改为待证/拟提供表述",
                "acceptance": "正文不再包含无公司资料支撑的金额/资质/业绩既成事实",
            }
        )
    return fixes[:8]
