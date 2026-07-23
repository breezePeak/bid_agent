from __future__ import annotations

"""Material authenticity verification (PR-A5).

uploaded → verified requires evidence extraction + tender match.
Keyword-only hits never auto-resolve qualification gaps.
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from utils import project_root, stringify

_CERT_NO_PATTERNS = [
    re.compile(r"(?:证书编号|证号|编号|No\.?|NO\.?)[:：\s]*([A-Za-z0-9\-_/]{6,40})", re.I),
    re.compile(r"\b([A-Z]{1,4}\d{6,}[A-Z0-9\-]*)\b"),
]
_DATE_PATTERNS = [
    re.compile(r"(?:有效期至|有效期|截止日期|届满)[:：\s]*(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})"),
    re.compile(r"(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})\s*(?:前有效|止)"),
]
_ISSUER_PATTERNS = [
    re.compile(r"(?:签发单位|发证机关|颁发单位|发证机构)[:：\s]*([^\n，,。]{2,40})"),
]
_COMPANY_PATTERNS = [
    re.compile(r"(?:单位名称|企业名称|公司名称|持证人|申请人)[:：\s]*([^\n，,。]{2,60})"),
    re.compile(r"([\u4e00-\u9fff]{2,40}(?:有限公司|股份有限公司|集团有限公司|有限责任公司))"),
]

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "business_license": ["营业执照", "统一社会信用代码", "市场主体"],
    "qualification_cert": ["资质证书", "资质等级", "承装", "承修", "承试", "建筑业企业资质"],
    "iso_cert": ["ISO", "质量管理体系", "环境管理体系", "职业健康"],
    "safety_cert": ["安全生产许可证", "安全许可证"],
    "performance": ["中标通知书", "合同", "业绩", "验收报告"],
    "personnel": ["身份证", "社保证明", "职称证书", "注册证书", "毕业证"],
    "financial": ["审计报告", "财务报表", "银行资信"],
    "other": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verification_dir(root: Path | None = None) -> Path:
    root = root or project_root()
    path = root / "workspace" / "materials" / "verifications"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_text_file(path: Path, max_chars: int = 80_000) -> str:
    try:
        if path.suffix.lower() in {".txt", ".md", ".csv", ".json"}:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        if path.suffix.lower() == ".pdf":
            try:
                # optional dependency path
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(str(path))
                parts: list[str] = []
                for i, page in enumerate(reader.pages[:40]):
                    try:
                        parts.append(f"\n--- page {i + 1} ---\n{page.extract_text() or ''}")
                    except Exception:
                        continue
                return "".join(parts)[:max_chars]
            except Exception:
                return path.read_bytes()[:2000].decode("utf-8", errors="ignore")
        # binary / docx: best-effort decode
        raw = path.read_bytes()[: max_chars * 2]
        return raw.decode("utf-8", errors="ignore")[:max_chars]
    except Exception as exc:
        return f"[read_error] {exc}"


def classify_material_type(text: str, filename: str = "") -> tuple[str, float]:
    blob = f"{filename}\n{text}"
    best = "other"
    best_hits = 0
    for mtype, kws in _TYPE_KEYWORDS.items():
        if mtype == "other":
            continue
        hits = sum(1 for k in kws if k.lower() in blob.lower() or k in blob)
        if hits > best_hits:
            best_hits = hits
            best = mtype
    conf = min(0.95, 0.35 + 0.2 * best_hits) if best_hits else 0.2
    return best, conf


def extract_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "company_name": "",
        "cert_no": "",
        "issuer": "",
        "valid_until": "",
        "scope": "",
    }
    for pat in _COMPANY_PATTERNS:
        m = pat.search(text)
        if m:
            fields["company_name"] = m.group(1).strip()[:80]
            break
    for pat in _CERT_NO_PATTERNS:
        m = pat.search(text)
        if m:
            fields["cert_no"] = m.group(1).strip()[:60]
            break
    for pat in _ISSUER_PATTERNS:
        m = pat.search(text)
        if m:
            fields["issuer"] = m.group(1).strip()[:80]
            break
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                fields["valid_until"] = date(y, mo, d).isoformat()
            except ValueError:
                fields["valid_until"] = f"{y:04d}-{mo:02d}-{d:02d}"
            break
    # scope: line with 范围/业务
    m = re.search(r"(?:许可范围|业务范围|适用范围)[:：\s]*([^\n]{4,120})", text)
    if m:
        fields["scope"] = m.group(1).strip()[:120]
    return fields


def _page_snippets(text: str, keywords: list[str], limit: int = 3) -> list[dict[str, Any]]:
    pages = re.split(r"--- page (\d+) ---", text)
    snippets: list[dict[str, Any]] = []
    if len(pages) >= 3:
        # split gives ['', '1', content, '2', content, ...]
        for i in range(1, len(pages) - 1, 2):
            page_no = pages[i]
            content = pages[i + 1]
            for kw in keywords:
                if kw and kw in content:
                    idx = content.find(kw)
                    start = max(0, idx - 40)
                    end = min(len(content), idx + 80)
                    snippets.append(
                        {
                            "page": int(page_no) if str(page_no).isdigit() else page_no,
                            "keyword": kw,
                            "excerpt": content[start:end].strip()[:200],
                        }
                    )
                    if len(snippets) >= limit:
                        return snippets
    else:
        for kw in keywords:
            if kw and kw in text:
                idx = text.find(kw)
                start = max(0, idx - 40)
                end = min(len(text), idx + 80)
                snippets.append(
                    {
                        "page": 1,
                        "keyword": kw,
                        "excerpt": text[start:end].strip()[:200],
                    }
                )
                if len(snippets) >= limit:
                    break
    return snippets


def _load_expected_company(root: Path) -> str:
    for rel in (
        "workspace/company_facts.json",
        "workspace/global_facts.json",
        "inputs/company.md",
    ):
        path = root / rel
        if not path.exists():
            continue
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("company_name", "bidder_name", "name", "单位名称"):
                        if data.get(key):
                            return str(data[key]).strip()
                    facts = data.get("facts") or data.get("company") or {}
                    if isinstance(facts, dict):
                        for key in ("company_name", "name", "单位名称"):
                            if facts.get(key):
                                return str(facts[key]).strip()
            else:
                text = path.read_text(encoding="utf-8", errors="replace")[:4000]
                m = re.search(r"(?:公司名称|单位名称|投标人)[:：\s]*([^\n]{2,60})", text)
                if m:
                    return m.group(1).strip()
                m = re.search(r"([\u4e00-\u9fff]{2,40}(?:有限公司|股份有限公司))", text)
                if m:
                    return m.group(1).strip()
        except Exception:
            continue
    return ""


def _load_checklist_item(root: Path, item_id: str) -> dict[str, Any] | None:
    path = root / "workspace" / "materials_checklist.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for item in data.get("items") or []:
        if isinstance(item, dict) and stringify(item.get("item_id")) == item_id:
            return item
    return None


def match_tender_requirement(
    *,
    item: dict[str, Any] | None,
    fields: dict[str, Any],
    text: str,
    expected_company: str,
) -> dict[str, Any]:
    issues: list[str] = []
    score = 0.0
    max_score = 0.0

    title = stringify((item or {}).get("title") or (item or {}).get("requirement") or "")
    keywords = [k for k in re.split(r"[\s,，/、]+", title) if len(k) >= 2][:8]
    if not keywords and item:
        keywords = [stringify(item.get("category")), stringify(item.get("item_id"))]

    # keyword presence
    max_score += 1.0
    hits = [k for k in keywords if k and k in text]
    if hits:
        score += min(1.0, len(hits) / max(1, min(3, len(keywords))))
    else:
        issues.append("content_keyword_mismatch")

    # company match
    max_score += 1.0
    company = fields.get("company_name") or ""
    if expected_company and company:
        if expected_company in company or company in expected_company:
            score += 1.0
        else:
            # partial char overlap
            overlap = len(set(expected_company) & set(company))
            if overlap >= 4:
                score += 0.5
                issues.append("company_partial_match")
            else:
                issues.append("company_mismatch")
    elif expected_company and not company:
        issues.append("company_not_extracted")
        score += 0.2
    else:
        score += 0.5  # unknown company baseline

    # expiry
    max_score += 1.0
    valid_until = fields.get("valid_until") or ""
    if valid_until:
        try:
            exp = date.fromisoformat(valid_until[:10])
            if exp < date.today():
                issues.append("expired")
            else:
                score += 1.0
        except ValueError:
            issues.append("invalid_expiry_format")
            score += 0.3
    else:
        # not all materials have expiry
        score += 0.6

    # cert number present for cert-like types
    max_score += 0.5
    if fields.get("cert_no"):
        score += 0.5
    else:
        score += 0.15

    ratio = score / max_score if max_score else 0.0
    return {
        "match_score": round(ratio, 3),
        "keyword_hits": hits,
        "issues": issues,
        "expected_company": expected_company,
        "extracted_company": company,
    }


def decide_verification_status(match: dict[str, Any], type_conf: float) -> tuple[str, float, bool]:
    """Return (lifecycle_status, confidence, needs_human)."""
    issues = set(match.get("issues") or [])
    score = float(match.get("match_score") or 0)
    conf = round(min(0.99, 0.4 * type_conf + 0.6 * score), 3)

    if "expired" in issues or "company_mismatch" in issues:
        return "rejected", conf, True
    if "content_keyword_mismatch" in issues and score < 0.35:
        return "uploaded", conf, True  # stay uploaded, not verified
    if conf >= 0.72 and score >= 0.55 and "expired" not in issues:
        return "verified", conf, conf < 0.85
    if conf >= 0.5:
        return "uploaded", conf, True  # needs human confirm to verify
    return "uploaded", conf, True


def verify_material(
    root: Path | None,
    item_id: str,
    *,
    uploaded_path: str = "",
    note: str = "",
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify an uploaded material file against checklist item + company identity."""
    root = root or project_root()
    item_id = stringify(item_id)
    item = dict(item) if isinstance(item, dict) else _load_checklist_item(root, item_id)
    path = Path(uploaded_path) if uploaded_path else None
    if path is None or not path.is_file():
        # try overrides path
        overrides_path = root / "workspace" / "materials_overrides.json"
        if overrides_path.exists():
            try:
                data = json.loads(overrides_path.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else (data.get("items") or data.get("overrides") or [])
                for row in rows:
                    if isinstance(row, dict) and stringify(row.get("item_id")) == item_id:
                        p = stringify(row.get("uploaded_path"))
                        if p:
                            path = Path(p)
                        break
            except Exception:
                pass

    text = ""
    filename = ""
    if path and path.is_file():
        filename = path.name
        text = _read_text_file(path)
    elif note:
        text = note
        filename = "note.txt"
    else:
        return {
            "ok": False,
            "item_id": item_id,
            "lifecycle_status": "uploaded",
            "message": "缺少可解析的上传文件",
            "confidence": 0.0,
            "needs_human": True,
        }

    material_type, type_conf = classify_material_type(text, filename)
    fields = extract_fields(text)
    expected_company = _load_expected_company(root)
    title = stringify((item or {}).get("title") or (item or {}).get("requirement") or item_id)
    keywords = [k for k in re.split(r"[\s,，/、]+", title) if len(k) >= 2][:8]
    snippets = _page_snippets(text, keywords + [fields.get("company_name") or "", fields.get("cert_no") or ""])
    match = match_tender_requirement(
        item=item,
        fields=fields,
        text=text,
        expected_company=expected_company,
    )
    lifecycle, confidence, needs_human = decide_verification_status(match, type_conf)

    evidence = {
        "item_id": item_id,
        "uploaded_path": str(path) if path else "",
        "filename": filename,
        "material_type": material_type,
        "type_confidence": type_conf,
        "fields": fields,
        "match": match,
        "snippets": snippets,
        "lifecycle_status": lifecycle,
        "confidence": confidence,
        "needs_human": needs_human,
        "verified_at": _now() if lifecycle == "verified" else "",
        "note": note[:300],
    }

    out_path = verification_dir(root) / f"{item_id}.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": lifecycle in {"verified", "uploaded"},
        "item_id": item_id,
        "lifecycle_status": lifecycle,
        "confidence": confidence,
        "needs_human": needs_human,
        "fields": fields,
        "match": match,
        "snippets": snippets,
        "material_type": material_type,
        "evidence_path": str(out_path.relative_to(root)) if out_path.is_relative_to(root) else str(out_path),
        "message": (
            f"材料验证完成: status={lifecycle}, confidence={confidence}, issues={match.get('issues')}"
        ),
    }


def human_confirm_verification(
    root: Path | None,
    item_id: str,
    *,
    operator: str = "operator",
    accept: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    root = root or project_root()
    path = verification_dir(root) / f"{stringify(item_id)}.json"
    if not path.exists():
        return {"ok": False, "message": "无验证记录"}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["human_confirmation"] = {
        "operator": operator[:80],
        "accept": bool(accept),
        "reason": reason[:500],
        "at": _now(),
    }
    if accept:
        data["lifecycle_status"] = "verified"
        data["verified_at"] = _now()
        data["needs_human"] = False
        data["confidence"] = max(float(data.get("confidence") or 0), 0.9)
    else:
        data["lifecycle_status"] = "rejected"
        data["needs_human"] = True
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item_id": item_id, "lifecycle_status": data["lifecycle_status"], "evidence": data}
