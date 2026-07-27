"""Pluggable external-research adapters for V3 EvidenceNeed resolution."""

from __future__ import annotations

import html
import hashlib
import os
import re
import urllib.parse
from pathlib import Path
from typing import Protocol

from .contracts import EvidenceSourceType
from .research_service import ResearchCandidate


class ResearchProviderAdapter(Protocol):
    provider_id: str

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]: ...


class DisabledResearchAdapter:
    provider_id = "disabled"

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        return []


class DeepSeekWebAdapter:
    """Use a local, persistent Playwright session; credentials never enter the app."""

    provider_id = "deepseek_web"
    chat_url = "https://chat.deepseek.com/"

    def __init__(
        self,
        profile_dir: Path | None = None,
        *,
        attachment_paths: list[Path] | tuple[Path, ...] | None = None,
    ) -> None:
        configured = os.environ.get("BID_AGENT_DEEPSEEK_PROFILE_DIR", "").strip()
        self.profile_dir = profile_dir or Path(configured or ".runtime/deepseek-playwright").resolve()
        self.timeout_ms = max(10_000, min(int(os.environ.get("BID_AGENT_DEEPSEEK_TIMEOUT_MS", "120000")), 300_000))
        self.headless = str(os.environ.get("BID_AGENT_DEEPSEEK_HEADLESS", "0")).lower() in {"1", "true", "yes"}
        self.attachment_paths = _validate_attachment_paths(attachment_paths or ())
        self.cache_fingerprint = _attachment_fingerprint(self.attachment_paths)

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        if limit <= 0:
            return []
        answer, links = self._ask_deepseek(
            question,
            attachment_paths=self.attachment_paths,
        )
        sources = _extract_sources(answer, links)
        if not sources:
            raise RuntimeError("DeepSeek 回答没有可核验的公开来源链接，已拒绝写入证据库。")
        candidates: list[ResearchCandidate] = []
        for title, source_url in sources[:limit]:
            host = urllib.parse.urlparse(source_url).hostname or "公开网页"
            candidates.append(
                ResearchCandidate(
                    title=title or host,
                    publisher=host,
                    content=answer,
                    source_url=source_url,
                    source_type=_source_type(source_url),
                    claim_types=("project_context", "method"),
                )
            )
        return candidates

    def _ask_deepseek(
        self,
        question: str,
        *,
        attachment_paths: tuple[Path, ...] = (),
    ) -> tuple[str, list[tuple[str, str]]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency configuration
            raise RuntimeError("未安装 Playwright；请安装 requirements.txt 并执行 playwright install chromium。") from exc
        prompt = (
            "你是招投标资料研究助手。仅回答以下明确问题，并给出可核验的公开来源链接。"
            "必须使用联网搜索；每项结论紧跟来源链接。不要编造企业资质、业绩、人员或能力；"
            "无法核验时明确说明。上传文件只用于理解检索上下文，不能把模型转述当成文件原文证据，"
            "结论仍必须引用公开来源 URL。"
        )
        if attachment_paths:
            prompt += "\n\n已附加文件：" + "、".join(path.name for path in attachment_paths)
        prompt += "\n\n问题：" + question
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.chat_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                composer = page.locator("textarea").last
                if composer.count() == 0:
                    composer = page.locator("[contenteditable='true']").last
                if composer.count() == 0:
                    raise RuntimeError("未检测到 DeepSeek 输入框；请先在弹出的浏览器中完成登录后重试。")
                _enable_web_search(page)
                if attachment_paths:
                    _upload_attachments(page, attachment_paths, timeout_ms=self.timeout_ms)
                response_selector = ".ds-markdown, [data-testid='message-content'], main article"
                before_count = page.locator(response_selector).count()
                conversation_root = _conversation_root(page)
                before_main = conversation_root.inner_text(timeout=5_000).strip()
                composer.fill(prompt)
                composer.press("Enter")
                previous = ""
                stable = 0
                elapsed_ms = 0
                while elapsed_ms < self.timeout_ms:
                    page.wait_for_timeout(1_000)
                    elapsed_ms += 1_000
                    responses = page.locator(response_selector)
                    text = ""
                    if responses.count() > before_count:
                        text = responses.last.inner_text(timeout=5_000).strip()
                    if not text:
                        current_main = conversation_root.inner_text(timeout=5_000).strip()
                        if prompt in current_main:
                            text = current_main.rsplit(prompt, 1)[-1].strip()
                        elif current_main.startswith(before_main):
                            text = current_main[len(before_main):].strip()
                        else:
                            text = current_main
                    stable = stable + 1 if text == previous else 0
                    previous = text
                    if stable >= 5 and len(text) >= 20:
                        break
                answer = previous[-24_000:].strip()
                if not answer or answer == prompt:
                    raise RuntimeError("DeepSeek 未返回可用回答。")
                links: list[tuple[str, str]] = []
                # DeepSeek may paint citation anchors a few seconds after the
                # answer text stops changing. Wait briefly so a valid sourced
                # answer is not rejected merely because citations were late.
                for attempt in range(16):
                    responses = page.locator(response_selector)
                    link_scope = (
                        responses.last
                        if responses.count() > before_count
                        else conversation_root
                    )
                    links = _links_from_scope(link_scope)
                    if _extract_sources(answer, links) or attempt == 15:
                        break
                    page.wait_for_timeout(1_000)
                    if responses.count() > before_count:
                        refreshed = responses.last.inner_text(timeout=5_000).strip()
                        if refreshed:
                            answer = refreshed[-24_000:].strip()
                return answer, links
            finally:
                context.close()


def _conversation_root(page):
    main = page.locator("main")
    if main.count() > 0:
        return main
    body = page.locator("body")
    if body.count() != 1:
        raise RuntimeError("未检测到 DeepSeek 对话区域。")
    return body


def _links_from_scope(scope) -> list[tuple[str, str]]:
    raw_links = scope.locator("a[href]").evaluate_all(
        """nodes => nodes.map(node => ({
            title: (node.innerText || node.textContent || "").trim(),
            url: node.href || node.getAttribute("href") || ""
        }))"""
    )
    return [
        (str(item.get("title") or "").strip(), str(item.get("url") or "").strip())
        for item in raw_links
        if isinstance(item, dict)
    ]


def _validate_attachment_paths(paths: list[Path] | tuple[Path, ...]) -> tuple[Path, ...]:
    max_count = max(1, min(int(os.environ.get("BID_AGENT_DEEPSEEK_MAX_ATTACHMENTS", "5")), 10))
    max_bytes = max(
        1,
        min(int(os.environ.get("BID_AGENT_DEEPSEEK_MAX_FILE_MB", "50")), 100),
    ) * 1024 * 1024
    allowed_extensions = {
        ".pdf", ".doc", ".docx", ".txt", ".md", ".csv",
        ".xls", ".xlsx", ".ppt", ".pptx",
        ".png", ".jpg", ".jpeg", ".webp",
    }
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if path in seen:
            continue
        if not path.is_file():
            raise FileNotFoundError(f"DeepSeek 附件不存在: {path}")
        if path.suffix.lower() not in allowed_extensions:
            raise ValueError(f"DeepSeek 不允许上传此文件类型: {path.suffix or '<无扩展名>'}")
        if path.stat().st_size <= 0:
            raise ValueError(f"DeepSeek 附件为空: {path.name}")
        if path.stat().st_size > max_bytes:
            raise ValueError(
                f"DeepSeek 附件超过 {max_bytes // (1024 * 1024)} MB: {path.name}"
            )
        seen.add(path)
        resolved.append(path)
    if len(resolved) > max_count:
        raise ValueError(f"DeepSeek 单次最多上传 {max_count} 个附件。")
    return tuple(resolved)


def _attachment_fingerprint(paths: tuple[Path, ...]) -> str:
    if not paths:
        return ""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _upload_attachments(page, paths: tuple[Path, ...], *, timeout_ms: int) -> None:
    file_inputs = page.locator("input[type='file']")
    count = file_inputs.count()
    if count == 0:
        raise RuntimeError("未检测到 DeepSeek 文件上传控件。")
    file_input = file_inputs.nth(count - 1)
    file_input.set_input_files([str(path) for path in paths])
    expected_names = {path.name for path in paths}
    conversation_root = _conversation_root(page)
    elapsed_ms = 0
    upload_timeout = min(timeout_ms, 60_000)
    while elapsed_ms < upload_timeout:
        visible_text = conversation_root.inner_text(timeout=5_000)
        if expected_names.issubset(set(visible_text.splitlines())) or all(
            name in visible_text for name in expected_names
        ):
            page.wait_for_timeout(1_000)
            return
        if "上传失败" in visible_text or "文件解析失败" in visible_text:
            raise RuntimeError("DeepSeek 文件上传或解析失败。")
        page.wait_for_timeout(500)
        elapsed_ms += 500
    raise RuntimeError(
        "DeepSeek 未在规定时间内确认附件上传完成: "
        + "、".join(sorted(expected_names))
    )


def _enable_web_search(page) -> None:
    configured = os.environ.get("BID_AGENT_DEEPSEEK_SEARCH_SELECTOR", "").strip()
    selectors = tuple(
        item
        for item in (
            configured,
            "[aria-pressed]:has-text('智能搜索')",
            "button:has-text('联网搜索')",
            "[aria-pressed]:has-text('联网搜索')",
            "[role='button'][aria-label*='联网搜索']",
            "button:has-text('Web Search')",
            "[role='button'][aria-label*='Web Search']",
        )
        if item
    )
    for selector in selectors:
        controls = page.locator(selector)
        for index in range(controls.count() - 1, -1, -1):
            control = controls.nth(index)
            if not control.is_visible():
                continue
            active = (
                str(control.get_attribute("aria-pressed") or "").lower() == "true"
                or str(control.get_attribute("data-state") or "").lower() in {"active", "checked", "on"}
                or bool(re.search(r"\b(active|selected|checked)\b", str(control.get_attribute("class") or ""), re.I))
            )
            if not active:
                control.click()
            return
    raise RuntimeError(
        "未检测到 DeepSeek“联网搜索”开关，已停止检索。"
        "如页面结构已更新，请通过 BID_AGENT_DEEPSEEK_SEARCH_SELECTOR 配置选择器。"
    )


def _normalize_public_url(value: str) -> str:
    candidate = html.unescape(str(value or "").strip()).rstrip(".,;:!?，。；：！？)]}>'\"")
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        parsed = urllib.parse.urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1", "chat.deepseek.com"} or host.endswith(".local"):
        return ""
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _extract_sources(answer: str, links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    discovered = list(links)
    discovered.extend(
        (title.strip(), url.strip())
        for title, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", answer)
    )
    discovered.extend(("", url) for url in re.findall(r"https?://[^\s<>\]\[\"']+", answer))
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, raw_url in discovered:
        url = _normalize_public_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        results.append((title.strip()[:200], url))
    return results


def _source_type(url: str) -> EvidenceSourceType:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith(".gov.cn") or host == "gov.cn":
        return EvidenceSourceType.OFFICIAL
    if host.endswith(".edu.cn") or host.endswith(".edu"):
        return EvidenceSourceType.ACADEMIC
    if "std.samr.gov.cn" in host or "standard" in host:
        return EvidenceSourceType.STANDARD
    return EvidenceSourceType.WEB


def create_research_adapter(
    provider_id: str | None = None,
    *,
    attachment_paths: list[Path] | tuple[Path, ...] | None = None,
) -> ResearchProviderAdapter:
    selected = str(provider_id or os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "deepseek_web")).strip().lower()
    if selected == "deepseek_web":
        return DeepSeekWebAdapter(attachment_paths=attachment_paths)
    if selected in {"", "disabled", "manual"}:
        if attachment_paths:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        return DisabledResearchAdapter()
    raise ValueError(f"未知 V3 研究 Provider: {selected}")
