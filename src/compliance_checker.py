from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from file_loader import load_global_facts, load_outline, load_score_points, load_tender_requirements
from utils import project_root, read_json, read_text, stringify, write_json

SEVERITY_RANK = {
    "fatal": 5,
    "critical": 4,
    "major": 3,
    "minor": 2,
    "info": 1,
}

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARN = "warn"
STATUS_SKIP = "skip"

FUZZY_RESPONSE_RE = re.compile(
    r"(原则上|基本满足|尽量|视情况|大致|争取|尽可能|酌情|视具体)",
    re.IGNORECASE,
)
STAR_MARK_RE = re.compile(r"[★▲＊*]|【必】|【强制】")
DISQUALIFY_LINE_RE = re.compile(
    r"(废标|否决投标|无效投标|作废|取消投标资格|不予受理|视为无效)",
)
SIGNATURE_REQ_RE = re.compile(
    r"(签字|盖章|签章|公章|骑缝章|电子签章|法定代表人|授权委托|CA\s*证书)",
)
BOND_REQ_RE = re.compile(
    r"(投标保证金|保证金|保函|投标担保)",
)
VALIDITY_REQ_RE = re.compile(
    r"(投标有效期|有效期\s*[不少于不少于不低于]?\s*\d+\s*天|有效期\s*\d+\s*日)",
)
MANDATORY_DOC_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("法定代表人身份证明", re.compile(r"法定代表人(身份)?证明"), "fatal"),
    ("授权委托书", re.compile(r"授权委托书|授权书"), "fatal"),
    ("营业执照", re.compile(r"营业执照"), "critical"),
    ("投标函", re.compile(r"投标函|投标书"), "fatal"),
    ("开标一览表", re.compile(r"开标一览表"), "major"),
    ("分项报价表", re.compile(r"分项报价表|报价明细"), "major"),
    ("技术偏离表", re.compile(r"技术偏离表"), "major"),
    ("商务偏离表", re.compile(r"商务偏离表"), "major"),
    ("资格审查表", re.compile(r"资格审查(资料)?表|资格证明文件"), "critical"),
    ("业绩表", re.compile(r"业绩(一览)?表|类似项目业绩"), "major"),
    ("人员表", re.compile(r"人员(配置)?表|项目组成员"), "major"),
    ("保证金凭证", re.compile(r"保证金(缴纳)?凭证|保函"), "fatal"),
]
PLACEHOLDER_RE = re.compile(r"(XXX+|待填写|请填写|请输入|TODO|TBD|【待补充】)", re.IGNORECASE)
AMOUNT_RE = re.compile(
    r"(?:人民币|￥|¥|RMB)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*(?:元|万元)?"
)
VALIDITY_DAYS_RE = re.compile(r"(?:投标)?有效期[^。；;\n]{0,20}?(\d{2,3})\s*(?:天|日|个日历日)")
PROJECT_ID_RE = re.compile(r"(项目编号|招标编号|采购编号)[：:\s]*([A-Za-z0-9\-_/]+)")


def make_check_item(
    *,
    check_id: str,
    check_type: str,
    check_name: str,
    status: str,
    severity: str,
    requirement: str = "",
    requirement_source: dict[str, Any] | None = None,
    bid_evidence: list[Any] | None = None,
    confidence: float = 0.8,
    auto_fixable: bool = False,
    suggestion: str = "",
    need_manual_review: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "check_id": check_id,
        "check_type": check_type,
        "check_name": check_name,
        "status": status,
        "severity": severity,
        "requirement": requirement,
        "requirement_source": requirement_source or {},
        "bid_evidence": bid_evidence or [],
        "confidence": max(0.0, min(1.0, float(confidence))),
        "auto_fixable": bool(auto_fixable),
        "suggestion": suggestion,
        "need_manual_review": bool(need_manual_review),
    }
    if extra:
        item.update(extra)
    return item


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def _load_text_corpus(root: Path) -> dict[str, str]:
    parts: dict[str, str] = {}
    for name in ("tender.md", "score.md", "company.md"):
        path = root / "inputs" / name
        if path.exists():
            parts[name] = read_text(path)
    final_md = root / "outputs" / "final.md"
    if final_md.exists():
        parts["final.md"] = read_text(final_md)
    chapters_dir = root / "workspace" / "chapters"
    if chapters_dir.exists():
        chapter_texts: list[str] = []
        for path in sorted(chapters_dir.glob("*.md")):
            chapter_texts.append(read_text(path))
        if chapter_texts:
            parts["chapters"] = "\n\n".join(chapter_texts)
    return parts


def _combined_bid_text(corpus: dict[str, str]) -> str:
    if corpus.get("final.md"):
        return corpus["final.md"]
    return corpus.get("chapters", "")


def _combined_tender_text(corpus: dict[str, str]) -> str:
    return "\n\n".join(
        text for key, text in corpus.items() if key in {"tender.md", "score.md"} and text
    )


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


def _find_any(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw and kw in text]


def check_qualification(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    requirements = tender_req.get("qualification_requirements") if isinstance(tender_req, dict) else []
    if not isinstance(requirements, list):
        requirements = []
    bid_text = _combined_bid_text(corpus)
    company_text = corpus.get("company.md", "")
    search_text = f"{bid_text}\n{company_text}"
    evidence_phrases = ("附后", "复印件", "扫描件", "证书编号", "已提供", "满足", "响应", "承诺")

    if not requirements:
        items.append(
            make_check_item(
                check_id="QUAL-000",
                check_type="qualification",
                check_name="资格条件清单",
                status=STATUS_SKIP,
                severity="info",
                requirement="招标文件中未抽取到明确资格条件",
                suggestion="确认 tender_requirements.qualification_requirements 是否完整",
                confidence=0.5,
                need_manual_review=True,
            )
        )
        return items

    for index, req in enumerate(requirements, start=1):
        req_text = stringify(req).strip()
        if not req_text:
            continue
        keywords = [token for token in re.split(r"[，,；;、/\s]+", req_text) if len(token) >= 2][:6]
        hits = _find_any(search_text, keywords) if keywords else []
        evidence = [_snippet(search_text, hit) for hit in hits[:3] if _snippet(search_text, hit)]
        strong = bool(hits) and any(phrase in search_text for phrase in evidence_phrases)
        if not hits:
            status, severity = STATUS_FAIL, "critical"
            suggestion = f"补充资格响应材料或正文说明，覆盖：{req_text[:80]}"
            need_review = True
            confidence = 0.72
        elif strong:
            # 关键词+材料措辞仍不足以证明证书/社保真实存在，只降到 warn
            status, severity = STATUS_WARN, "major"
            suggestion = "正文疑似响应资格要求，请人工核对证书/社保/业绩原件是否齐全"
            need_review = True
            confidence = 0.55
        else:
            status, severity = STATUS_WARN, "critical"
            suggestion = "仅命中资格关键词，缺少证明材料表述，请补证据并人工确认"
            need_review = True
            confidence = 0.5
        items.append(
            make_check_item(
                check_id=f"QUAL-{index:03d}",
                check_type="qualification",
                check_name="资格条件检查",
                status=status,
                severity=severity,
                requirement=req_text,
                requirement_source={"file": "workspace/tender_requirements.json", "field": "qualification_requirements"},
                bid_evidence=evidence,
                confidence=confidence,
                suggestion=suggestion,
                need_manual_review=need_review,
            )
        )
    return items


def check_disqualification_clauses(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    clauses = _lines_matching(tender_text, DISQUALIFY_LINE_RE, limit=30)
    if not clauses:
        items.append(
            make_check_item(
                check_id="DQ-000",
                check_type="disqualification",
                check_name="废标条款专项扫描",
                status=STATUS_SKIP,
                severity="info",
                requirement="未在招标/评分文本中扫描到明确废标语句",
                suggestion="人工确认是否存在否决投标条款未入库",
                confidence=0.55,
                need_manual_review=True,
            )
        )
        return items

    for index, clause in enumerate(clauses, start=1):
        fuzzy_hits = FUZZY_RESPONSE_RE.findall(bid_text) if bid_text else []
        risk_keywords = [kw for kw in ("不满足", "无法提供", "无此", "不具备", "负偏离") if kw in bid_text]
        if not bid_text:
            status, severity = STATUS_FAIL, "fatal"
            suggestion = "标书正文缺失，无法核对废标条款"
            evidence: list[Any] = []
            confidence = 0.9
        elif risk_keywords:
            status, severity = STATUS_FAIL, "critical"
            suggestion = "正文出现负向表述，请逐条对照废标条款并消除负偏离"
            evidence = risk_keywords[:3]
            confidence = 0.75
        elif fuzzy_hits:
            status, severity = STATUS_WARN, "major"
            suggestion = "存在模糊响应表述，废标条款需人工逐条确认"
            evidence = fuzzy_hits[:3]
            confidence = 0.6
        else:
            # 规则无法证明“已满足”，禁止自动 pass
            status, severity = STATUS_WARN, "major"
            suggestion = "已提取废标条款，请人工逐条核对并保留满足证据"
            evidence = []
            confidence = 0.45
        items.append(
            make_check_item(
                check_id=f"DQ-{index:03d}",
                check_type="disqualification",
                check_name="废标条款检查",
                status=status,
                severity=severity,
                requirement=clause,
                requirement_source={"file": "inputs/tender.md|score.md", "match": "废标/否决投标"},
                bid_evidence=evidence,
                confidence=confidence,
                suggestion=suggestion,
                need_manual_review=True,
            )
        )
    return items


def check_mandatory_params(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    mandatory_lines = [
        line.strip()
        for line in tender_text.splitlines()
        if STAR_MARK_RE.search(line) and len(line.strip()) >= 6
    ][:40]

    if not mandatory_lines:
        items.append(
            make_check_item(
                check_id="STAR-000",
                check_type="mandatory_param",
                check_name="★▲强制参数扫描",
                status=STATUS_SKIP,
                severity="info",
                requirement="未扫描到★/▲/强制标记参数",
                suggestion="若招标文件使用其他强制标记，请扩展识别规则",
                confidence=0.6,
            )
        )
        return items

    for index, line in enumerate(mandatory_lines, start=1):
        tokens = [tok for tok in re.split(r"[，,；;：:\s]+", re.sub(r"[★▲＊*【】\[\]]", " ", line)) if len(tok) >= 2][:5]
        hits = _find_any(bid_text, tokens) if bid_text and tokens else []
        fuzzy = bool(FUZZY_RESPONSE_RE.search(bid_text)) if bid_text else False
        explicit = any(token in bid_text for token in ("完全响应", "无偏离", "正偏离", "满足★", "响应★", "逐条响应"))
        if not bid_text:
            status, severity = STATUS_FAIL, "fatal"
            suggestion = "标书正文缺失，无法响应强制参数"
            evidence: list[Any] = []
            confidence = 0.9
        elif not hits:
            status, severity = STATUS_FAIL, "fatal"
            suggestion = f"补充对强制要求的逐条响应：{line[:100]}"
            evidence = []
            confidence = 0.8
        elif fuzzy:
            status, severity = STATUS_WARN, "major"
            suggestion = "存在强制参数相关内容，但出现模糊响应表述，需改为明确响应"
            evidence = [_snippet(bid_text, hit) for hit in hits[:2]]
            confidence = 0.65
        elif explicit:
            status, severity = STATUS_WARN, "major"
            suggestion = "疑似已响应强制参数，请人工核对参数值/证明材料是否真实对应"
            evidence = [_snippet(bid_text, hit) for hit in hits[:2]]
            confidence = 0.6
        else:
            status, severity = STATUS_WARN, "critical"
            suggestion = "仅命中强制条款关键词，缺少明确响应表述，请逐条补响应"
            evidence = [_snippet(bid_text, hit) for hit in hits[:2]]
            confidence = 0.55
        items.append(
            make_check_item(
                check_id=f"STAR-{index:03d}",
                check_type="mandatory_param",
                check_name="★▲强制参数检查",
                status=status,
                severity=severity,
                requirement=line[:300],
                requirement_source={"file": "inputs/tender.md|score.md", "marker": "★/▲"},
                bid_evidence=evidence,
                confidence=confidence,
                suggestion=suggestion,
                need_manual_review=True,
            )
        )
    return items


def check_signature_seal(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    req_lines = _lines_matching(tender_text, SIGNATURE_REQ_RE, limit=20)
    checks = [
        ("SIGN-001", "投标函签字盖章", ["投标函", "签字", "盖章"], "fatal"),
        ("SIGN-002", "授权委托书完整性", ["授权委托", "签字", "盖章"], "fatal"),
        ("SIGN-003", "法定代表人签章", ["法定代表人", "签字"], "critical"),
        ("SIGN-004", "公章/电子签章", ["公章", "电子签章", "签章"], "critical"),
    ]
    for check_id, name, keywords, severity in checks:
        req_hit = next((line for line in req_lines if any(k in line for k in keywords)), "")
        requirement = req_hit or f"招标文件通常要求：{name}"
        bid_hits = _find_any(bid_text, keywords)
        # 文本出现“签字/盖章”不能证明真实签章，禁止自动 pass
        if not bid_text:
            status = STATUS_FAIL
            suggestion = f"补充{name}相关内容或附件说明，并在终稿人工核验真实签章"
            evidence: list[Any] = []
            conf = 0.9
        elif bid_hits:
            status = STATUS_WARN
            suggestion = f"正文提及{name}相关措辞，但文本无法验证真实签字/盖章/电子签，必须人工核验原件或 PDF 签章"
            evidence = bid_hits
            conf = 0.4
            severity = "critical"
        else:
            status = STATUS_FAIL
            suggestion = f"未检出{name}相关说明，请补充并人工核验签章"
            evidence = []
            conf = 0.75
        items.append(
            make_check_item(
                check_id=check_id,
                check_type="signature",
                check_name=name,
                status=status,
                severity=severity if status == STATUS_FAIL else "critical",
                requirement=requirement[:300],
                requirement_source={"file": "inputs/tender.md", "topic": "签章"},
                bid_evidence=evidence,
                confidence=conf,
                suggestion=suggestion,
                need_manual_review=True,
            )
        )
    return items


def check_bid_bond(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    evidence_notes = tender_req.get("evidence_notes") if isinstance(tender_req, dict) else []
    if not isinstance(evidence_notes, list):
        evidence_notes = []
    req_lines = _lines_matching(tender_text, BOND_REQ_RE, limit=15)
    for note in evidence_notes:
        note_text = stringify(note)
        if BOND_REQ_RE.search(note_text):
            req_lines.append(note_text)

    if not req_lines:
        items.append(
            make_check_item(
                check_id="BOND-000",
                check_type="bid_bond",
                check_name="投标保证金检查",
                status=STATUS_SKIP,
                severity="info",
                requirement="未识别到保证金相关要求",
                confidence=0.55,
            )
        )
        return items

    amount_in_tender = AMOUNT_RE.findall("\n".join(req_lines))
    bid_hits = _find_any(bid_text, ["保证金", "保函", "投标担保", "缴纳凭证"])
    amounts_in_bid = AMOUNT_RE.findall(bid_text) if bid_text else []

    if not bid_hits:
        status, severity = STATUS_FAIL, "fatal"
        suggestion = "补充投标保证金缴纳说明/保函/凭证，并核对金额与到账要求"
        conf = 0.85
    elif amount_in_tender and not amounts_in_bid:
        status, severity = STATUS_WARN, "critical"
        suggestion = "已提及保证金，但未检出金额，请核对是否与招标要求一致"
        conf = 0.6
    else:
        status, severity = STATUS_WARN, "critical"
        suggestion = "已检出保证金相关表述，请人工核验金额、到账时间、收款账户/保函原件"
        conf = 0.5

    items.append(
        make_check_item(
            check_id="BOND-001",
            check_type="bid_bond",
            check_name="投标保证金检查",
            status=status,
            severity=severity,
            requirement=req_lines[0][:300],
            requirement_source={"file": "inputs/tender.md", "topic": "保证金"},
            bid_evidence=bid_hits + amounts_in_bid[:3],
            confidence=conf,
            suggestion=suggestion,
            need_manual_review=True,
            extra={"tender_amount_candidates": amount_in_tender[:5], "bid_amount_candidates": amounts_in_bid[:5]},
        )
    )
    return items


def check_bid_validity(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    tender_days = [int(x) for x in VALIDITY_DAYS_RE.findall(tender_text)]
    bid_days = [int(x) for x in VALIDITY_DAYS_RE.findall(bid_text)] if bid_text else []
    service_period = stringify(facts.get("service_period") if isinstance(facts, dict) else "")
    warranty_period = stringify(facts.get("warranty_period") if isinstance(facts, dict) else "")

    if not tender_days and not _lines_matching(tender_text, VALIDITY_REQ_RE, limit=5):
        items.append(
            make_check_item(
                check_id="VAL-000",
                check_type="bid_validity",
                check_name="投标有效期检查",
                status=STATUS_SKIP,
                severity="info",
                requirement="未识别到投标有效期天数要求",
                confidence=0.55,
            )
        )
    else:
        required = max(tender_days) if tender_days else None
        offered = max(bid_days) if bid_days else None
        if required is None:
            status, severity = STATUS_WARN, "major"
            suggestion = "招标侧有效期表述不清晰，请人工确认"
            need_review = True
        elif offered is None:
            status, severity = STATUS_FAIL, "fatal"
            suggestion = f"标书未写明投标有效期，招标要求不少于 {required} 天"
            need_review = True
        elif offered < required:
            status, severity = STATUS_FAIL, "fatal"
            suggestion = f"投标有效期 {offered} 天低于招标要求 {required} 天"
            need_review = True
        else:
            status, severity = STATUS_PASS, "info"
            suggestion = ""
            need_review = False
        items.append(
            make_check_item(
                check_id="VAL-001",
                check_type="bid_validity",
                check_name="投标有效期是否满足",
                status=status,
                severity=severity,
                requirement=f"招标有效期要求天数: {required}" if required else "见招标有效期条款",
                requirement_source={"file": "inputs/tender.md", "topic": "投标有效期"},
                bid_evidence=[{"bid_days": offered, "tender_days": required}],
                confidence=0.85 if required and offered is not None else 0.65,
                suggestion=suggestion,
                need_manual_review=need_review,
            )
        )

    # 服务期/质保期在全文中的一致性（与 global_review 互补，规则侧再兜一层）
    if service_period and bid_text and service_period not in bid_text:
        partial = service_period[:12]
        if partial and partial not in bid_text:
            items.append(
                make_check_item(
                    check_id="VAL-002",
                    check_type="bid_validity",
                    check_name="服务期表述一致性",
                    status=STATUS_WARN,
                    severity="major",
                    requirement=service_period,
                    requirement_source={"file": "workspace/global_facts.json", "field": "service_period"},
                    bid_evidence=[],
                    confidence=0.7,
                    suggestion="全文服务期表述与全局事实不一致或缺失，请统一",
                    need_manual_review=True,
                )
            )
        else:
            items.append(
                make_check_item(
                    check_id="VAL-002",
                    check_type="bid_validity",
                    check_name="服务期表述一致性",
                    status=STATUS_PASS,
                    severity="info",
                    requirement=service_period,
                    confidence=0.7,
                )
            )
    if warranty_period and bid_text and warranty_period not in bid_text:
        partial = warranty_period[:12]
        if partial and partial not in bid_text:
            items.append(
                make_check_item(
                    check_id="VAL-003",
                    check_type="bid_validity",
                    check_name="质保期表述一致性",
                    status=STATUS_WARN,
                    severity="major",
                    requirement=warranty_period,
                    requirement_source={"file": "workspace/global_facts.json", "field": "warranty_period"},
                    confidence=0.7,
                    suggestion="全文质保期表述与全局事实不一致或缺失，请统一",
                    need_manual_review=True,
                )
            )
    return items


def check_document_completeness(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)
    outline = {}
    try:
        outline = load_outline(root)
    except Exception:
        outline = _safe_read_json(root / "workspace" / "outline.json") or {}

    chapters = outline.get("chapters") if isinstance(outline, dict) else []
    if isinstance(chapters, list) and chapters:
        missing_chapter_files: list[str] = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = stringify(chapter.get("id"))
            if not chapter_id:
                continue
            path = root / "workspace" / "chapters" / f"{chapter_id}.md"
            if not path.exists() or path.stat().st_size == 0:
                missing_chapter_files.append(chapter_id)
        if missing_chapter_files:
            items.append(
                make_check_item(
                    check_id="DOC-001",
                    check_type="completeness",
                    check_name="目录与章节完整性",
                    status=STATUS_FAIL,
                    severity="critical",
                    requirement="大纲章节均应生成正文",
                    bid_evidence=missing_chapter_files,
                    confidence=0.95,
                    suggestion=f"补齐缺失章节: {', '.join(missing_chapter_files)}",
                    need_manual_review=True,
                )
            )
        else:
            items.append(
                make_check_item(
                    check_id="DOC-001",
                    check_type="completeness",
                    check_name="目录与章节完整性",
                    status=STATUS_PASS,
                    severity="info",
                    requirement="大纲章节均应生成正文",
                    confidence=0.95,
                )
            )

    for index, (name, pattern, severity) in enumerate(MANDATORY_DOC_PATTERNS, start=2):
        required = bool(pattern.search(tender_text))
        if not required:
            continue
        found = bool(pattern.search(bid_text)) if bid_text else False
        items.append(
            make_check_item(
                check_id=f"DOC-{index:03d}",
                check_type="completeness",
                check_name=f"附件/表格完整性-{name}",
                status=STATUS_PASS if found else STATUS_FAIL,
                severity="info" if found else severity,
                requirement=f"招标文件要求包含：{name}",
                requirement_source={"file": "inputs/tender.md", "item": name},
                bid_evidence=[name] if found else [],
                confidence=0.8,
                suggestion="" if found else f"补充{name}",
                need_manual_review=not found,
                extra={"item": name, "required": True, "found": found},
            )
        )

    placeholders = PLACEHOLDER_RE.findall(bid_text) if bid_text else []
    if placeholders:
        items.append(
            make_check_item(
                check_id="DOC-090",
                check_type="completeness",
                check_name="残留占位符检查",
                status=STATUS_FAIL,
                severity="major",
                requirement="最终标书不应残留占位符",
                bid_evidence=list(dict.fromkeys(placeholders))[:10],
                confidence=0.95,
                suggestion="清除 XXX/待填写/TODO 等占位内容",
                need_manual_review=True,
                auto_fixable=False,
            )
        )
    else:
        items.append(
            make_check_item(
                check_id="DOC-090",
                check_type="completeness",
                check_name="残留占位符检查",
                status=STATUS_PASS if bid_text else STATUS_SKIP,
                severity="info",
                requirement="最终标书不应残留占位符",
                confidence=0.9,
            )
        )
    return items


def check_data_consistency(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    bid_text = _combined_bid_text(corpus)
    project_name = stringify(facts.get("project_name") if isinstance(facts, dict) else "")
    bidder_name = stringify(facts.get("bidder_name") if isinstance(facts, dict) else "")

    def _name_check(check_id: str, label: str, expected: str) -> dict[str, Any]:
        if not expected:
            return make_check_item(
                check_id=check_id,
                check_type="consistency",
                check_name=label,
                status=STATUS_SKIP,
                severity="info",
                requirement="全局事实缺失，无法比对",
                confidence=0.4,
                need_manual_review=True,
            )
        if not bid_text:
            return make_check_item(
                check_id=check_id,
                check_type="consistency",
                check_name=label,
                status=STATUS_FAIL,
                severity="critical",
                requirement=expected,
                confidence=0.9,
                suggestion=f"标书缺失，无法核验{label}",
                need_manual_review=True,
            )
        if expected in bid_text:
            return make_check_item(
                check_id=check_id,
                check_type="consistency",
                check_name=label,
                status=STATUS_PASS,
                severity="info",
                requirement=expected,
                bid_evidence=[expected],
                confidence=0.9,
            )
        return make_check_item(
            check_id=check_id,
            check_type="consistency",
            check_name=label,
            status=STATUS_FAIL,
            severity="critical",
            requirement=expected,
            confidence=0.85,
            suggestion=f"全文统一为：{expected}",
            need_manual_review=True,
        )

    items.append(_name_check("CONS-001", "项目名称一致性", project_name))
    items.append(_name_check("CONS-002", "投标人名称一致性", bidder_name))

    tender_ids = PROJECT_ID_RE.findall(_combined_tender_text(corpus))
    bid_ids = PROJECT_ID_RE.findall(bid_text) if bid_text else []
    if tender_ids:
        tender_id_values = {item[1] for item in tender_ids}
        bid_id_values = {item[1] for item in bid_ids}
        missing = sorted(tender_id_values - bid_id_values)
        if missing:
            items.append(
                make_check_item(
                    check_id="CONS-003",
                    check_type="consistency",
                    check_name="项目编号一致性",
                    status=STATUS_FAIL,
                    severity="major",
                    requirement=", ".join(sorted(tender_id_values)),
                    bid_evidence=sorted(bid_id_values),
                    confidence=0.8,
                    suggestion=f"补齐/统一项目编号: {', '.join(missing)}",
                    need_manual_review=True,
                )
            )
        else:
            items.append(
                make_check_item(
                    check_id="CONS-003",
                    check_type="consistency",
                    check_name="项目编号一致性",
                    status=STATUS_PASS,
                    severity="info",
                    requirement=", ".join(sorted(tender_id_values)),
                    bid_evidence=sorted(bid_id_values),
                    confidence=0.8,
                )
            )

    # 人员：摘要中的 personnel 与章节交叉
    personnel_values: list[str] = []
    summaries_dir = root / "workspace" / "summaries"
    if summaries_dir.exists():
        for path in sorted(summaries_dir.glob("*_summary.json")):
            data = _safe_read_json(path)
            if isinstance(data, dict) and isinstance(data.get("personnel"), list):
                for person in data["personnel"]:
                    text = stringify(person).strip()
                    if text and text not in personnel_values:
                        personnel_values.append(text)
    if personnel_values:
        inconsistent = [p for p in personnel_values if bid_text and p not in bid_text]
        # 若人员出现在摘要却不在全文，通常摘要来自全文，故仅提示重复命名冲突
        name_counts: dict[str, int] = {}
        for person in personnel_values:
            key = person[:20]
            name_counts[key] = name_counts.get(key, 0) + 1
        items.append(
            make_check_item(
                check_id="CONS-004",
                check_type="consistency",
                check_name="人员信息汇总",
                status=STATUS_PASS,
                severity="info",
                requirement="汇总章节摘要中的人员信息供人工复核",
                bid_evidence=personnel_values[:20],
                confidence=0.65,
                need_manual_review=len(personnel_values) > 0,
                suggestion="核对不同章节项目经理/成员姓名与证书编号是否一致",
            )
        )

    # 金额大小写粗检：同时出现大小写金额关键词时提示人工核对
    if bid_text and ("大写" in bid_text or "人民币" in bid_text):
        amounts = AMOUNT_RE.findall(bid_text)
        items.append(
            make_check_item(
                check_id="CONS-005",
                check_type="consistency",
                check_name="金额一致性粗检",
                status=STATUS_WARN if len(set(amounts)) > 3 else STATUS_PASS,
                severity="major" if len(set(amounts)) > 3 else "info",
                requirement="投标总价、分项合计、大小写金额应一致",
                bid_evidence=list(dict.fromkeys(amounts))[:12],
                confidence=0.55,
                suggestion="金额种类较多，请用确定性计算核对报价表与投标函",
                need_manual_review=len(set(amounts)) > 3,
            )
        )

    global_review = _safe_read_json(root / "workspace" / "global_review.json")
    if isinstance(global_review, dict):
        conflicts = global_review.get("chapter_conflicts") if isinstance(global_review.get("chapter_conflicts"), list) else []
        if conflicts:
            items.append(
                make_check_item(
                    check_id="CONS-006",
                    check_type="consistency",
                    check_name="承接全文审核冲突",
                    status=STATUS_FAIL,
                    severity="major",
                    requirement="global_review.chapter_conflicts 应清空",
                    bid_evidence=conflicts[:10],
                    confidence=0.9,
                    suggestion="先处理全文一致性审核中的章节冲突",
                    need_manual_review=True,
                )
            )
        flags = []
        for key in (
            "project_name_consistent",
            "bidder_name_consistent",
            "service_period_consistent",
            "warranty_period_consistent",
        ):
            if key in global_review and global_review.get(key) is False:
                flags.append(key)
        if flags:
            items.append(
                make_check_item(
                    check_id="CONS-007",
                    check_type="consistency",
                    check_name="承接全文审核布尔一致性",
                    status=STATUS_FAIL,
                    severity="critical",
                    requirement="global_review 一致性标志均为 true",
                    bid_evidence=flags,
                    confidence=0.9,
                    suggestion="修复项目名称/投标人/服务期/质保期不一致问题",
                    need_manual_review=True,
                )
            )
    return items


def _parse_amount_token(token: str) -> float | None:
    text = stringify(token).replace(",", "").replace("，", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def check_commercial_pricing(root: Path, corpus: dict[str, str], facts: dict[str, Any], tender_req: dict[str, Any]) -> list[dict[str, Any]]:
    """报价/商务最小检查：限价、金额异常、报价表存在性、大小写提示。"""
    items: list[dict[str, Any]] = []
    tender_text = _combined_tender_text(corpus)
    bid_text = _combined_bid_text(corpus)

    # 报价相关表格/章节是否出现
    price_docs = [
        ("PRICE-001", "开标一览表", re.compile(r"开标一览表"), "major"),
        ("PRICE-002", "分项报价表", re.compile(r"分项报价表|报价明细|报价一览"), "major"),
        ("PRICE-003", "商务偏离表", re.compile(r"商务偏离表"), "major"),
    ]
    for check_id, name, pattern, severity in price_docs:
        required = bool(pattern.search(tender_text))
        if not required:
            continue
        found = bool(pattern.search(bid_text)) if bid_text else False
        items.append(
            make_check_item(
                check_id=check_id,
                check_type="commercial",
                check_name=f"报价/商务表-{name}",
                status=STATUS_PASS if found else STATUS_FAIL,
                severity="info" if found else severity,
                requirement=f"招标文件要求包含：{name}",
                requirement_source={"file": "inputs/tender.md", "item": name},
                bid_evidence=[name] if found else [],
                confidence=0.8,
                suggestion="" if found else f"补充{name}",
                need_manual_review=not found,
                extra={"item": name, "required": True, "found": found},
            )
        )

    # 最高限价/预算
    ceiling_lines = [
        line.strip()
        for line in tender_text.splitlines()
        if any(k in line for k in ("最高限价", "预算金额", "采购预算", "控制价", "最高投标限价"))
    ][:10]
    ceiling_amounts: list[float] = []
    for line in ceiling_lines:
        for token in AMOUNT_RE.findall(line):
            value = _parse_amount_token(token)
            if value is not None:
                # 粗略：带“万”按万元
                if "万" in line:
                    value *= 10000
                ceiling_amounts.append(value)
    bid_amounts: list[float] = []
    if bid_text:
        for token in AMOUNT_RE.findall(bid_text):
            value = _parse_amount_token(token)
            if value is not None and value > 0:
                bid_amounts.append(value)

    if ceiling_amounts:
        ceiling = max(ceiling_amounts)
        suspicious = [amt for amt in bid_amounts if amt > ceiling * 1.001]
        if not bid_text:
            items.append(
                make_check_item(
                    check_id="PRICE-010",
                    check_type="commercial",
                    check_name="最高限价检查",
                    status=STATUS_FAIL,
                    severity="fatal",
                    requirement=f"最高限价/预算约 {ceiling}",
                    suggestion="标书缺失，无法核对是否超限价",
                    need_manual_review=True,
                    confidence=0.85,
                )
            )
        elif suspicious:
            items.append(
                make_check_item(
                    check_id="PRICE-010",
                    check_type="commercial",
                    check_name="最高限价检查",
                    status=STATUS_FAIL,
                    severity="fatal",
                    requirement=f"最高限价/预算约 {ceiling}",
                    bid_evidence=[str(x) for x in suspicious[:8]],
                    suggestion="检出可能超过最高限价/预算的金额，请核对报价表与投标函",
                    need_manual_review=True,
                    confidence=0.7,
                    extra={"ceiling": ceiling, "over_limit_candidates": suspicious[:8]},
                )
            )
        else:
            items.append(
                make_check_item(
                    check_id="PRICE-010",
                    check_type="commercial",
                    check_name="最高限价检查",
                    status=STATUS_WARN,
                    severity="major",
                    requirement=f"最高限价/预算约 {ceiling}",
                    bid_evidence=[str(x) for x in sorted(set(bid_amounts), reverse=True)[:8]],
                    suggestion="未检出明显超限价金额，请用确定性计算复核总价/分项",
                    need_manual_review=True,
                    confidence=0.55,
                    extra={"ceiling": ceiling},
                )
            )

    # 异常报价：0 / 负数 / 金额种类过多
    if bid_text:
        zero_or_neg = re.findall(r"(?:单价|合[计价]|总价)[^。；;\n]{0,12}?(?:为)?\s*(?:0|0\.0+|零)(?:\s*元)?", bid_text)
        if zero_or_neg:
            items.append(
                make_check_item(
                    check_id="PRICE-020",
                    check_type="commercial",
                    check_name="异常报价检查",
                    status=STATUS_FAIL,
                    severity="critical",
                    requirement="报价不得为零/明显异常",
                    bid_evidence=zero_or_neg[:5],
                    suggestion="存在疑似零报价表述，请核对分项报价",
                    need_manual_review=True,
                    confidence=0.7,
                )
            )
        unique_amounts = sorted({round(x, 2) for x in bid_amounts if x >= 1})
        if len(unique_amounts) >= 8:
            items.append(
                make_check_item(
                    check_id="PRICE-021",
                    check_type="commercial",
                    check_name="金额一致性粗检",
                    status=STATUS_WARN,
                    severity="major",
                    requirement="投标总价、分项合计、大小写金额应一致",
                    bid_evidence=[str(x) for x in unique_amounts[:12]],
                    suggestion="金额种类较多，请用程序核对数量×单价、分项合计与投标函总价",
                    need_manual_review=True,
                    confidence=0.55,
                )
            )
        if ("大写" in bid_text or "人民币" in bid_text) and unique_amounts:
            items.append(
                make_check_item(
                    check_id="PRICE-022",
                    check_type="commercial",
                    check_name="大小写金额人工复核",
                    status=STATUS_WARN,
                    severity="major",
                    requirement="大写金额与小写金额必须一致",
                    bid_evidence=[str(x) for x in unique_amounts[:6]],
                    suggestion="请人工或确定性程序核对大小写金额",
                    need_manual_review=True,
                    confidence=0.5,
                )
            )

    if not items:
        items.append(
            make_check_item(
                check_id="PRICE-000",
                check_type="commercial",
                check_name="报价/商务检查",
                status=STATUS_SKIP,
                severity="info",
                requirement="未识别到明确报价/限价条款",
                confidence=0.45,
                need_manual_review=True,
                suggestion="若本项目有报价要求，请确认招标文本是否已正确导入",
            )
        )
    return items


CHECKERS: list[tuple[str, Callable[[Path, dict[str, str], dict[str, Any], dict[str, Any]], list[dict[str, Any]]]]] = [
    ("qualification", check_qualification),
    ("disqualification", check_disqualification_clauses),
    ("mandatory_param", check_mandatory_params),
    ("signature", check_signature_seal),
    ("bid_bond", check_bid_bond),
    ("bid_validity", check_bid_validity),
    ("completeness", check_document_completeness),
    ("consistency", check_data_consistency),
    ("commercial", check_commercial_pricing),
]


def _max_severity(items: list[dict[str, Any]]) -> str:
    best = "info"
    best_rank = 0
    for item in items:
        severity = stringify(item.get("severity")) or "info"
        rank = SEVERITY_RANK.get(severity, 0)
        if rank > best_rank:
            best = severity
            best_rank = rank
    return best


def summarize_compliance_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "total": len(items),
        "pass": 0,
        "fail": 0,
        "warn": 0,
        "skip": 0,
        "fatal": 0,
        "critical": 0,
        "major": 0,
        "minor": 0,
        "info": 0,
        "need_manual_review": 0,
    }
    by_type: dict[str, int] = {}
    for item in items:
        status = stringify(item.get("status")) or STATUS_SKIP
        severity = stringify(item.get("severity")) or "info"
        check_type = stringify(item.get("check_type")) or "unknown"
        if status in counts:
            counts[status] += 1
        if severity in counts:
            counts[severity] += 1
        by_type[check_type] = by_type.get(check_type, 0) + 1
        if item.get("need_manual_review"):
            counts["need_manual_review"] += 1

    blocking = any(
        stringify(item.get("status")) == STATUS_FAIL
        and stringify(item.get("severity")) in {"fatal", "critical"}
        for item in items
    )
    need_manual = any(bool(item.get("need_manual_review")) for item in items) or blocking
    ok = not blocking and counts["fail"] == 0
    return {
        "ok": ok,
        "blocking": blocking,
        "need_manual_review": need_manual,
        "max_severity": _max_severity([i for i in items if stringify(i.get("status")) in {STATUS_FAIL, STATUS_WARN}] or items),
        "counts": counts,
        "by_type": by_type,
    }


def normalize_compliance_report(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("合规检查结果必须是 JSON 对象。")
    items = data.get("items") if isinstance(data.get("items"), list) else []
    normalized_items: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        normalized_items.append(
            make_check_item(
                check_id=stringify(raw.get("check_id")) or "UNKNOWN",
                check_type=stringify(raw.get("check_type")) or "unknown",
                check_name=stringify(raw.get("check_name")) or "",
                status=stringify(raw.get("status")) or STATUS_SKIP,
                severity=stringify(raw.get("severity")) or "info",
                requirement=stringify(raw.get("requirement")),
                requirement_source=raw.get("requirement_source") if isinstance(raw.get("requirement_source"), dict) else {},
                bid_evidence=raw.get("bid_evidence") if isinstance(raw.get("bid_evidence"), list) else [],
                confidence=float(raw.get("confidence") or 0.5),
                auto_fixable=bool(raw.get("auto_fixable", False)),
                suggestion=stringify(raw.get("suggestion")),
                need_manual_review=bool(raw.get("need_manual_review", False)),
                extra={
                    key: value
                    for key, value in raw.items()
                    if key
                    not in {
                        "check_id",
                        "check_type",
                        "check_name",
                        "status",
                        "severity",
                        "requirement",
                        "requirement_source",
                        "bid_evidence",
                        "confidence",
                        "auto_fixable",
                        "suggestion",
                        "need_manual_review",
                    }
                },
            )
        )
    summary = summarize_compliance_items(normalized_items)
    return {
        "version": stringify(data.get("version")) or "1.0.0",
        "check_set": data.get("check_set")
        if isinstance(data.get("check_set"), list)
        else [name for name, _ in CHECKERS],
        "summary": summary,
        "items": normalized_items,
        "ok": bool(summary["ok"]),
        "blocking": bool(summary["blocking"]),
        "need_manual_review": bool(summary["need_manual_review"]),
        "max_severity": summary["max_severity"],
    }


def run_compliance_check(
    root: Path | None = None,
    *,
    raise_on_blocking: bool = False,
    phase: str = "pre_build",
) -> Path:
    """
    phase:
      - pre_build: 写稿后、拼接前（可基于 chapters）
      - final: 终稿复检（优先 final.md，用于硬门禁）
    """
    root = root or project_root()

    from quality_gates import validate_global_review_blocking
    # 全文审核未通过则不应继续专项合规/出稿链路
    validate_global_review_blocking(root, required=False)
    corpus = _load_text_corpus(root)
    if phase == "final" and not corpus.get("final.md"):
        # 终稿阶段若无 final.md，强制记一条 fatal，避免空过
        items = [
            make_check_item(
                check_id="FINAL-000",
                check_type="final_gate",
                check_name="终稿文本存在性",
                status=STATUS_FAIL,
                severity="fatal",
                requirement="outputs/final.md 必须存在且非空",
                suggestion="请先执行 build-md",
                need_manual_review=True,
                confidence=0.99,
            )
        ]
        report = normalize_compliance_report(
            {"version": "1.1.0", "check_set": [name for name, _ in CHECKERS] + ["final_gate"], "items": items, "phase": phase}
        )
        report["phase"] = phase
        output_path = root / "workspace" / "compliance_report.json"
        write_json(output_path, report)
        if raise_on_blocking and report.get("blocking"):
            raise RuntimeError(f"终稿合规检查失败（缺少 final.md），请查看 {output_path}")
        return output_path

    try:
        facts = load_global_facts(root)
    except Exception:
        facts = _safe_read_json(root / "workspace" / "global_facts.json") or {}
    if not isinstance(facts, dict):
        facts = {}

    try:
        tender_req = load_tender_requirements(root)
    except Exception:
        tender_req = _safe_read_json(root / "workspace" / "tender_requirements.json") or {}
    if not isinstance(tender_req, dict):
        tender_req = {}

    try:
        _ = load_score_points(root)
    except Exception:
        pass

    items: list[dict[str, Any]] = []
    for _, checker in CHECKERS:
        try:
            items.extend(checker(root, corpus, facts, tender_req))
        except Exception as exc:
            items.append(
                make_check_item(
                    check_id="SYS-ERR",
                    check_type="system",
                    check_name="检查器执行异常",
                    status=STATUS_WARN,
                    severity="major",
                    requirement=checker.__name__,
                    suggestion=str(exc),
                    need_manual_review=True,
                    confidence=0.5,
                )
            )

    # 确定性报价验算 + 偏离表逐行
    try:
        from price_table_parser import price_table_compliance_items

        items.extend(price_table_compliance_items(root))
    except Exception as exc:
        items.append(
            make_check_item(
                check_id="PRICE-CALC-ERR",
                check_type="commercial",
                check_name="报价表验算异常",
                status=STATUS_WARN,
                severity="major",
                suggestion=str(exc),
                need_manual_review=True,
                confidence=0.5,
            )
        )
    try:
        from deviation_table_checker import deviation_compliance_items

        items.extend(deviation_compliance_items(root))
    except Exception as exc:
        items.append(
            make_check_item(
                check_id="DEV-ERR",
                check_type="responsiveness",
                check_name="偏离表检查异常",
                status=STATUS_WARN,
                severity="major",
                suggestion=str(exc),
                need_manual_review=True,
                confidence=0.5,
            )
        )

    if phase == "final":
        items.append(
            make_check_item(
                check_id="FINAL-001",
                check_type="final_gate",
                check_name="终稿复检标记",
                status=STATUS_PASS,
                severity="info",
                requirement="基于 outputs/final.md 复检",
                bid_evidence=["outputs/final.md"],
                confidence=0.9,
            )
        )

    report = normalize_compliance_report(
        {
            "version": "1.1.0",
            "check_set": [name for name, _ in CHECKERS],
            "items": items,
        }
    )
    report["phase"] = phase
    report["source_text"] = "final.md" if corpus.get("final.md") else ("chapters" if corpus.get("chapters") else "none")
    output_path = root / "workspace" / "compliance_report.json"
    write_json(output_path, report)
    summary = report["summary"]
    print(
        f"[完成] 专项合规检查完成({phase}): "
        f"OK={summary['counts']['pass']}, WARN={summary['counts']['warn']}, "
        f"FAIL={summary['counts']['fail']}, SKIP={summary['counts']['skip']}, "
        f"blocking={report['blocking']} -> {output_path}"
    )
    # 回灌人工复核与改稿线索（不因回灌失败阻断主流程）
    try:
        from claim_validator import validate_all_chapter_claims
        from compliance_feedback import sync_compliance_findings

        validate_all_chapter_claims(root)
        sync_compliance_findings(root)
    except Exception as exc:
        print(f"[警告] 合规/claim 回灌失败: {exc}")

    if raise_on_blocking and report.get("blocking"):
        fatal_or_critical = [
            f"{item.get('check_id')}:{item.get('check_name')}"
            for item in report.get("items", [])
            if isinstance(item, dict)
            and item.get("status") == STATUS_FAIL
            and item.get("severity") in {"fatal", "critical"}
        ][:8]
        detail = "；".join(fatal_or_critical) if fatal_or_critical else "存在阻断项"
        raise RuntimeError(f"专项合规检查阻断出稿：{detail}。详见 {output_path}")
    return output_path


def compliance_gate_status(report: dict[str, Any]) -> str:
    if not isinstance(report, dict):
        return "ok"
    if report.get("blocking") or (
        isinstance(report.get("summary"), dict) and report["summary"].get("blocking")
    ):
        return "error"
    if report.get("need_manual_review") or (
        isinstance(report.get("summary"), dict) and report["summary"].get("need_manual_review")
    ):
        return "warn"
    return "ok"


def validate_compliance_report(root: Path, *, raise_on_blocking: bool = True) -> dict[str, Any]:
    report_path = root / "workspace" / "compliance_report.json"
    if not report_path.exists():
        raise ValueError(f"合规检查报告不存在: {report_path}")
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise ValueError("compliance_report.json 必须是 JSON 对象")
    normalized = normalize_compliance_report(report)
    if raise_on_blocking and normalized.get("blocking"):
        raise RuntimeError(
            f"专项合规检查存在 fatal/critical 失败项，请查看 {report_path}"
        )
    return normalized


def enforce_final_compliance_gate(root: Path | None = None) -> Path:
    """终稿硬门禁：基于 final.md 复检，blocking 则抛错阻止成功完成。"""
    return run_compliance_check(root, raise_on_blocking=True, phase="final")
