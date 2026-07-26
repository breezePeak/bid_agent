from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from document_splitter import split_markdown_document
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import compact_json, parse_json_from_model, project_root, read_json, read_text, stringify, write_json, write_text


USER_AGENT = "Mozilla/5.0 (compatible; BidAgentResearch/1.0)"
MAX_PAGE_BYTES = 1_500_000
MAX_PAGE_CHARS = 12_000


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            text = re.sub(r"\s+", " ", data).strip()
            if len(text) >= 2:
                self.parts.append(text)


def _bool_env(name: str, default: bool = True) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _safe_public_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        for address in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM):
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


def _open_url(url: str, *, timeout: int = 12) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = str(response.headers.get("Content-Type") or "")
        return response.read(MAX_PAGE_BYTES + 1)[:MAX_PAGE_BYTES], content_type


def _search_bing_rss(query: str, top_k: int = 4) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "setlang": "zh-Hans"}
    )
    payload, _ = _open_url(url)
    root = ET.fromstring(payload)
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        link = stringify(item.findtext("link"))
        if not link or not _safe_public_url(link):
            continue
        results.append(
            {
                "title": html.unescape(stringify(item.findtext("title"))),
                "url": link,
                "snippet": re.sub(
                    r"<[^>]+>",
                    "",
                    html.unescape(stringify(item.findtext("description"))),
                ).strip(),
            }
        )
        if len(results) >= top_k:
            break
    return results


def _page_text(url: str) -> str:
    try:
        payload, content_type = _open_url(url)
    except Exception:
        return ""
    if "pdf" in content_type.lower() or url.lower().split("?", 1)[0].endswith(".pdf"):
        return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            decoded = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            decoded = ""
    parser = _VisibleTextParser()
    try:
        parser.feed(decoded)
    except Exception:
        return ""
    return "\n".join(parser.parts)[:MAX_PAGE_CHARS]


def _authority(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith(".gov.cn") or host == "gov.cn":
        return "government"
    if host.endswith(".edu.cn"):
        return "academic"
    if "std.samr.gov.cn" in host:
        return "standard"
    return "web"


def _collect_results(queries: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    max_queries = max(1, min(int(os.environ.get("BID_AGENT_RESEARCH_MAX_QUERIES", "8")), 12))
    top_k = max(1, min(int(os.environ.get("BID_AGENT_RESEARCH_RESULTS_PER_QUERY", "3")), 5))
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_urls: set[str] = set()
    for query in queries[:max_queries]:
        try:
            items = _search_bing_rss(query, top_k=top_k)
        except Exception as exc:
            warnings.append(f"检索失败：{query}：{exc}")
            continue
        for item in items:
            url = stringify(item.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                {
                    **item,
                    "query": query,
                    "authority": _authority(url),
                    "content": _page_text(url),
                }
            )
    return results, warnings


def _reference_markdown(report: dict[str, Any], raw_results: list[dict[str, Any]]) -> str:
    lines = ["# 自动联网资料研究报告", ""]
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        lines.extend(
            [
                f"## {stringify(section.get('title')) or '研究主题'}",
                "",
                stringify(section.get("summary")),
                "",
            ]
        )
        for point in section.get("key_points", []):
            if stringify(point):
                lines.append(f"- {stringify(point)}")
        lines.append("")
    lines.extend(["## 检索来源", ""])
    for index, item in enumerate(raw_results, start=1):
        lines.extend(
            [
                f"### R{index:03d} {stringify(item.get('title')) or item.get('url')}",
                "",
                f"- URL：{item.get('url')}",
                f"- 检索问题：{item.get('query')}",
                f"- 来源级别：{item.get('authority')}",
                f"- 摘要：{stringify(item.get('snippet'))}",
                "",
                stringify(item.get("content"))[:4000],
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def research_project_materials(root: Path | None = None) -> Path:
    """Search after whole-project understanding, then persist citable reference material."""
    root = root or project_root()
    understanding = read_json(root / "workspace" / "project_understanding.json")
    if not isinstance(understanding, dict):
        raise ValueError("workspace/project_understanding.json 必须是 JSON 对象。")
    queries = [
        stringify(item)
        for item in understanding.get("research_queries", [])
        if stringify(item)
    ]
    enabled = _bool_env("BID_AGENT_WEB_RESEARCH_ENABLED", default=True)
    raw_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    if enabled:
        raw_results, warnings = _collect_results(queries)
    else:
        warnings.append("BID_AGENT_WEB_RESEARCH_ENABLED 已关闭，未执行联网检索。")
    write_json(root / "workspace" / "research_raw_results.json", raw_results)

    existing_reference = read_text(root / "inputs" / "reference.md")
    if not raw_results and not existing_reference.strip():
        raise RuntimeError(
            "项目资料检索没有取得任何结果，且没有人工参考资料。"
            "请检查网络，或将政策/标准/类似项目资料放入 sources/reference/ 后继续。"
        )

    prompt = load_agent_prompt(root, "web_research")
    report: dict[str, Any] = {
        "status": "manual_only" if not raw_results else ("partial" if warnings else "complete"),
        "queries": queries,
        "warnings": warnings,
        "source_count": len(raw_results),
        "sections": [],
    }
    if raw_results:
        model_payload = [
            {
                "id": f"R{index:03d}",
                "title": item.get("title"),
                "url": item.get("url"),
                "query": item.get("query"),
                "authority": item.get("authority"),
                "snippet": item.get("snippet"),
                "content": stringify(item.get("content"))[:5000],
            }
            for index, item in enumerate(raw_results, start=1)
        ]
        with agent_run(
            root,
            "research_project_materials",
            "web_research",
            input_summary={"query_count": len(queries), "source_count": len(raw_results)},
            temperature=0.1,
        ):
            raw = chat(
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            "请围绕项目整体理解整理联网检索材料。只能使用给定来源，"
                            "每条结论必须列出 source_ids。\n\n"
                            "## 项目整体理解\n\n"
                            f"{compact_json(understanding)}\n\n"
                            "## 检索结果\n\n"
                            f"{compact_json(model_payload)}"
                        ),
                    },
                ],
                temperature=0.1,
            )
        normalized = parse_json_from_model(
            raw,
            root / "workspace" / "debug_web_research_raw.txt",
        )
        if isinstance(normalized, dict):
            report["sections"] = normalized.get("sections", [])
            report["research_gaps"] = normalized.get("research_gaps", [])

    report["sources"] = [
        {key: item.get(key) for key in ("title", "url", "query", "authority", "snippet")}
        for item in raw_results
    ]
    output = root / "workspace" / "research_report.json"
    write_json(output, report)

    auto_reference = _reference_markdown(report, raw_results) if raw_results else ""
    merged_reference = "\n\n---\n\n".join(
        part.strip() for part in (existing_reference, auto_reference) if part.strip()
    )
    if merged_reference:
        reference_path = root / "inputs" / "reference.md"
        write_text(reference_path, merged_reference)
        chunks = split_markdown_document(
            merged_reference,
            "reference.md",
            "REFERENCE",
        )
        write_json(root / "workspace" / "chunks" / "reference_chunks.json", chunks)
    print(
        f"[完成] 项目资料研究：queries={len(queries)} sources={len(raw_results)} "
        f"status={report['status']} -> {output}"
    )
    return output
