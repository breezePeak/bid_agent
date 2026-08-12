"""Pluggable external-research adapters for V3 EvidenceNeed resolution."""

from __future__ import annotations

import atexit
import html
import hashlib
import importlib.util
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from .contracts import EvidenceSourceType
from .research_service import ResearchCandidate


_T = TypeVar("_T")
_STOP_WEB_SESSION = object()
_WEB_SESSION_LOCK = threading.RLock()
_WEB_SEND_LOCK = threading.Lock()
_WEB_SESSIONS: dict[tuple[str, bool], "_PersistentWebSession"] = {}
_WEB_RUNTIME_STATUS: dict[tuple[str, bool], dict[str, object]] = {}
_WEB_LAST_SEND_AT = 0.0


class WebAuthenticationRequiredError(RuntimeError):
    """The visible provider page still requires user authentication."""


def _web_session_key(profile_dir: Path, *, headless: bool) -> tuple[str, bool]:
    return str(profile_dir.resolve()), bool(headless)


class _PersistentWebSession:
    """Own one persistent Playwright context on a dedicated long-lived thread.

    Starlette may resume separate search requests on different worker threads,
    while Playwright's synchronous API is bound to the thread that created it.
    Routing every page operation through this owner thread keeps the same
    visible browser, profile, tabs and login state reusable across requests.
    """

    def __init__(self, profile_dir: Path, *, headless: bool) -> None:
        self.profile_dir = profile_dir.resolve()
        self.headless = bool(headless)
        # One running request plus one queued request is enough for interactive
        # web research.  Rejecting deeper backlogs avoids exhausting Starlette's
        # worker pool while every caller waits synchronously for the browser.
        self._jobs: queue.Queue[object] = queue.Queue(maxsize=1)
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._state_lock = threading.Lock()
        self._startup_error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._serve,
            name=f"bid-agent-web-{self.profile_dir.name}",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    def run(self, operation: Callable[[object, dict[str, object]], _T]) -> _T:
        done = threading.Event()
        result: dict[str, object] = {}
        with self._state_lock:
            if self._closed:
                raise RuntimeError("网页浏览器会话已关闭。")
            try:
                self._jobs.put_nowait((operation, done, result))
            except queue.Full as exc:
                raise RuntimeError("WEB_RESEARCH_BUSY: 网页检索正在忙，请稍后重试。") from exc
        done.wait()
        error = result.get("error")
        if isinstance(error, BaseException):
            raise error
        return result["value"]  # type: ignore[return-value]

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._jobs.put(_STOP_WEB_SESSION)
        self._stopped.wait()
        self._thread.join()

    def _serve(self) -> None:
        playwright = None
        context = None
        try:
            from playwright.sync_api import sync_playwright

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless,
            )
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self._stopped.set()
            return

        self._ready.set()
        state: dict[str, object] = {}
        try:
            while True:
                job = self._jobs.get()
                if job is _STOP_WEB_SESSION:
                    break
                operation, done, result = job
                try:
                    browser = getattr(context, "browser", None)
                    connected = getattr(browser, "is_connected", None)
                    if callable(connected) and not connected():
                        context = playwright.chromium.launch_persistent_context(
                            str(self.profile_dir), headless=self.headless,
                        )
                        state.clear()
                    result["value"] = operation(context, state)
                except BaseException as exc:
                    result["error"] = exc
                finally:
                    done.set()
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                playwright.stop()
            except Exception:
                pass
            self._stopped.set()


def _persistent_web_session(profile_dir: Path, *, headless: bool) -> _PersistentWebSession:
    """Return the one process-wide browser owner for a profile."""
    key = _web_session_key(profile_dir, headless=headless)
    with _WEB_SESSION_LOCK:
        existing = _WEB_SESSIONS.get(key)
        if existing is not None and not existing.closed:
            return existing
        session = _PersistentWebSession(profile_dir, headless=headless)
        _WEB_SESSIONS[key] = session
        return session


def _run_in_persistent_web_context(
    profile_dir: Path,
    *,
    headless: bool,
    operation: Callable[[object, dict[str, object]], _T],
) -> _T:
    return _persistent_web_session(profile_dir, headless=headless).run(operation)


def close_web_sessions() -> None:
    """Close retained browser contexts only during orderly backend shutdown."""
    with _WEB_SESSION_LOCK:
        sessions = list(_WEB_SESSIONS.values())
        for session in sessions:
            session.close()
        _WEB_SESSIONS.clear()


atexit.register(close_web_sessions)


def _wait_for_web_send_slot() -> None:
    """Serialize all web-chat sends and keep a deliberate human-scale interval."""
    global _WEB_LAST_SEND_AT
    interval = max(
        30,
        min(int(os.environ.get("BID_AGENT_WEB_QUERY_INTERVAL_SECONDS", "90")), 600),
    )
    remaining = _WEB_LAST_SEND_AT + interval - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def _record_web_send() -> None:
    global _WEB_LAST_SEND_AT
    _WEB_LAST_SEND_AT = time.monotonic()


class ResearchProviderAdapter(Protocol):
    provider_id: str

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]: ...


class DisabledResearchAdapter:
    provider_id = "disabled"

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        return []

    def runtime_status(self) -> dict[str, object]:
        return {
            "ready": True,
            "provider_id": self.provider_id,
            "reason": "WEB_AUTOMATION_DISABLED",
        }


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
        self._runtime_status_cache: dict[str, object] | None = None

    def search(self, question: str, *, limit: int) -> list[ResearchCandidate]:
        if limit <= 0:
            return []
        runtime = self.runtime_status()
        if not runtime.get("ready"):
            reason = str(runtime.get("reason") or "PLAYWRIGHT_RUNTIME_UNAVAILABLE")
            if reason == "PLAYWRIGHT_PACKAGE_MISSING":
                message = "后端 Python 未安装 Playwright。"
            elif reason == "CHROMIUM_MISSING":
                message = "后端 Playwright 未安装 Chromium。"
            else:
                message = "后端 Playwright 运行环境不可用。"
            raise RuntimeError(
                f"{message} 当前解释器：{runtime.get('python_executable') or '未知'}；"
                f"请检查当前 {self.provider_id} Provider 的浏览器运行环境后重试。"
            )
        answer, links = self._ask_deepseek(
            question,
            attachment_paths=self.attachment_paths,
        )
        sources = _extract_sources(answer, links)
        if not sources:
            raise RuntimeError(
                f"{self.provider_id} 对话已返回文字，但没有可核验的公开来源链接"
                "（需带 http(s) 原文链接）。豆包回答本身不能当作证据；"
                "已拒绝写入证据库。"
            )
        candidates: list[ResearchCandidate] = []
        read_failures: list[str] = []
        for title, source_url in sources[:limit]:
            resolved_url = _unwrap_public_url(source_url)
            if not resolved_url:
                read_failures.append(f"{source_url or title or '（空链接）'}: 链接无效或被过滤")
                continue
            # The chat-provider answer is only a lead.  Evidence sent to the
            # writer must be text read from the cited public page itself, not
            # an LLM's unverified paraphrase of that page.
            source_content, read_reason = self._read_public_source_detailed(resolved_url)
            if not source_content:
                read_failures.append(
                    f"{resolved_url}: {read_reason or '页面正文读取失败'}"
                )
                continue
            host = urllib.parse.urlparse(resolved_url).hostname or "公开网页"
            candidates.append(
                ResearchCandidate(
                    title=title or host,
                    publisher=host,
                    content=source_content,
                    source_url=resolved_url,
                    source_type=_source_type(resolved_url),
                    claim_types=("project_context", "method"),
                )
            )
        if not candidates:
            detail = "；".join(read_failures[:6]) if read_failures else "无可用链接"
            raise RuntimeError(
                f"{self.provider_id} 对话与链接提取已完成（{len(sources)} 个公开链接），"
                "但引用页面原文均未能读取，不能把聊天转述当证据。"
                f" 失败详情：{detail}。"
                " 说明：系统在豆包回答后会再打开链接抓取公开网页原文；"
                "若目标站防爬、需登录、跳转失效或正文过短，会在此步失败。"
                "请检查浏览器中链接是否可直接打开，或换可公开访问的政府/标准站链接后重试。"
            )
        return candidates

    def _read_public_source(self, source_url: str) -> str:
        """Read the cited URL without disturbing the retained provider tab."""
        content, _reason = self._read_public_source_detailed(source_url)
        return content

    def _read_public_source_detailed(self, source_url: str) -> tuple[str, str]:
        """Return (content, empty_reason). Content empty means verification failed."""
        if not source_url:
            return "", "空 URL"
        # Most public sources can be verified directly without opening another
        # visible tab.  The retained browser is only a fallback for pages that
        # reject or require a real browser request.
        content, http_reason = _fetch_public_source_content_detailed(source_url)
        if content:
            return content, ""
        try:
            browser_content = _run_in_persistent_web_context(
                self.profile_dir,
                headless=self.headless,
                operation=lambda context, state: self._read_public_source_in_context(
                    context, state, source_url,
                ),
            )
        except Exception as exc:
            browser_reason = f"浏览器抓取异常: {type(exc).__name__}: {exc}"[:180]
            return "", (
                f"HTTP 失败（{http_reason or '未知'}）；{browser_reason}"
            )
        if browser_content:
            return browser_content, ""
        return "", (
            f"HTTP 失败（{http_reason or '未知'}）；"
            "浏览器打开后仍无足够正文（可能需登录、验证码、防爬或跳转失效）"
        )

    def _read_public_source_in_context(
        self,
        context: object,
        state: dict[str, object],
        source_url: str,
    ) -> str:
        """Reuse one background source tab while keeping the provider in front."""
        provider_page = state.get("provider_page")
        provider_closed = getattr(provider_page, "is_closed", None)
        if provider_page is None or (callable(provider_closed) and provider_closed()):
            provider_page = next(
                (
                    page
                    for page in reversed(list(getattr(context, "pages", ()) or ()))
                    if str(getattr(page, "url", "") or "").startswith(self.chat_url)
                ),
                None,
            )
            if provider_page is not None:
                state["provider_page"] = provider_page

        def restore_provider_focus() -> None:
            if provider_page is None:
                return
            try:
                provider_page.bring_to_front()
            except Exception:
                pass

        def discard_reader(page: object) -> None:
            try:
                page.close()
            except Exception:
                pass
            if state.get("source_page") is page:
                state.pop("source_page", None)
                state.pop("source_target_url", None)
                state.pop("source_final_url", None)

        source_page = state.get("source_page")
        is_closed = getattr(source_page, "is_closed", None)
        if source_page is None or (callable(is_closed) and is_closed()):
            source_page = context.new_page()
            created = True
        else:
            created = False

        # ``new_page()`` temporarily activates about:blank.  Restore the
        # already-complete Doubao/DeepSeek result immediately and keep source
        # verification in the background.
        restore_provider_focus()
        try:
            current_url = _normalize_public_url(str(source_page.url or ""))
            target_matches = (
                str(state.get("source_target_url") or "") == source_url
                and current_url == str(state.get("source_final_url") or "")
            )
            if not target_matches:
                source_page.goto(
                    source_url,
                    wait_until="domcontentloaded",
                    timeout=min(self.timeout_ms, 60_000),
                )
            # Give SPA / slow official sites a bit more time to render body text.
            source_page.wait_for_timeout(2_500)
            content = _extract_page_text(source_page)
            min_chars = 80 if _looks_like_official_host(source_url) else 120
            if len(content) < min_chars:
                if created:
                    discard_reader(source_page)
                return ""
            state["source_page"] = source_page
            state["source_target_url"] = source_url
            state["source_final_url"] = _normalize_public_url(
                str(source_page.url or "")
            )
            return content[:60_000]
        except Exception:
            current_url = str(getattr(source_page, "url", "") or "")
            if created or current_url in {"", "about:blank"} or current_url.startswith(
                "chrome-error:"
            ):
                discard_reader(source_page)
            return ""
        finally:
            restore_provider_focus()

    def runtime_status(self) -> dict[str, object]:
        """Return diagnostics for the exact interpreter serving the API."""
        key = _web_session_key(self.profile_dir, headless=self.headless)
        with _WEB_SESSION_LOCK:
            session = _WEB_SESSIONS.get(key)
            active = session is not None and not session.closed
            cached = _WEB_RUNTIME_STATUS.get(key)
        # A new adapter is created for many requests.  Reuse the process-level
        # diagnosis, especially while its persistent browser is already active;
        # starting a second temporary Playwright driver can fail on Windows.
        if cached is not None:
            self._runtime_status_cache = {
                **cached,
                "browser_session_active": active,
            }
            return dict(self._runtime_status_cache)
        if active:
            self._runtime_status_cache = {
                "ready": True,
                "python_executable": sys.executable,
                "profile_dir": str(self.profile_dir),
                "playwright_installed": True,
                "chromium_installed": True,
                "browser_session_active": True,
            }
            return dict(self._runtime_status_cache)
        status: dict[str, object] = {
            "python_executable": sys.executable,
            "profile_dir": str(self.profile_dir),
            "playwright_installed": importlib.util.find_spec("playwright") is not None,
            "browser_session_active": False,
        }
        if not status["playwright_installed"]:
            self._runtime_status_cache = {
                **status,
                "ready": False,
                "reason": "PLAYWRIGHT_PACKAGE_MISSING",
            }
            return dict(self._runtime_status_cache)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                executable = playwright.chromium.executable_path
            status["chromium_executable"] = executable
            status["chromium_installed"] = bool(executable and Path(executable).is_file())
        except Exception as exc:
            self._runtime_status_cache = {
                **status,
                "ready": False,
                "reason": "PLAYWRIGHT_RUNTIME_UNAVAILABLE",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
            return dict(self._runtime_status_cache)
        if not status["chromium_installed"]:
            self._runtime_status_cache = {**status, "ready": False, "reason": "CHROMIUM_MISSING"}
            return dict(self._runtime_status_cache)
        self._runtime_status_cache = {**status, "ready": True}
        with _WEB_SESSION_LOCK:
            _WEB_RUNTIME_STATUS[key] = dict(self._runtime_status_cache)
        return dict(self._runtime_status_cache)

    def _ask_deepseek(
        self,
        question: str,
        *,
        attachment_paths: tuple[Path, ...] = (),
    ) -> tuple[str, list[tuple[str, str]]]:
        prompt = (
            "你是招投标资料研究助手。仅回答以下明确问题，并给出可核验的公开来源链接。"
            "必须使用联网搜索；每项结论紧跟来源链接。不要编造企业资质、业绩、人员或能力；"
            "无法核验时明确说明。上传文件只用于理解检索上下文，不能把模型转述当成文件原文证据，"
            "结论仍必须引用公开来源 URL。请把来源内容归纳成可直接用于标书写作的具体结论、"
            "技术方法、数据或要求；不得只罗列 URL、文件名或搜索摘要。"
        )
        if attachment_paths:
            prompt += "\n\n已附加文件：" + "、".join(path.name for path in attachment_paths)
        prompt += "\n\n问题：" + question
        return _run_in_persistent_web_context(
            self.profile_dir,
            headless=self.headless,
            operation=lambda context, state: self._ask_deepseek_in_context(
                context,
                state=state,
                prompt=prompt,
                attachment_paths=attachment_paths,
            ),
        )

    def _ask_deepseek_in_context(
        self,
        context: object,
        *,
        state: dict[str, object],
        prompt: str,
        attachment_paths: tuple[Path, ...],
    ) -> tuple[str, list[tuple[str, str]]]:
        # Reuse the already-open provider tab when possible.  The dedicated
        # session owner serializes all access to this context.
        retained_source_page = state.get("source_page")
        pages = list(context.pages)
        page = next(
            (
                item
                for item in reversed(pages)
                if item is not retained_source_page and item.url.startswith(self.chat_url)
            ),
            None,
        )
        if page is None:
            # Persistent Chromium normally starts with one about:blank tab.
            # Reuse it instead of opening a second tab and leaving the first
            # empty beside the completed research conversation.
            page = next(
                (
                    item
                    for item in reversed(pages)
                    if item is not retained_source_page and item.url == "about:blank"
                ),
                None,
            ) or context.new_page()
            page.goto(self.chat_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        state["provider_page"] = page
        # Remove only empty automation leftovers.  Real provider/source pages
        # remain open for reuse, matching the persistent-session contract.
        for stale_page in pages:
            if stale_page is page or stale_page is retained_source_page:
                continue
            if str(getattr(stale_page, "url", "") or "") != "about:blank":
                continue
            try:
                stale_page.close()
            except Exception:
                pass
        page.bring_to_front()
        page.wait_for_timeout(1_500)
        login_wait_ms = _web_login_wait_ms(self.provider_id)
        composer = _wait_for_web_chat_authenticated(
            page,
            provider_id=self.provider_id,
            timeout_ms=login_wait_ms,
        )
        _enable_web_search(page)
        if attachment_paths:
            _upload_attachments(page, attachment_paths, timeout_ms=self.timeout_ms)
        response_selector = ".ds-markdown, .markdown-body, [data-testid='message-content'], main article"
        before_count = page.locator(response_selector).count()
        conversation_root = _conversation_root(page)
        before_main = conversation_root.inner_text(timeout=5_000).strip()
        # Keep the interaction on the visible web UI: focus the actual
        # composer first, then put the research question into that field.
        # This also makes the entered query observable in the retained tab.
        composer.click(timeout=5_000)
        composer.fill(prompt)
        with _WEB_SEND_LOCK:
            _wait_for_web_send_slot()
            _click_send_button(page)
            _record_web_send()
        previous = ""
        stable = 0
        resent_after_authentication = False
        remaining_ticks = max(1, self.timeout_ms // 1_000)
        while remaining_ticks > 0:
            page.wait_for_timeout(1_000)
            remaining_ticks -= 1
            if _web_authentication_pending(page, provider_id=self.provider_id):
                composer = _wait_for_web_chat_authenticated(
                    page,
                    provider_id=self.provider_id,
                    timeout_ms=login_wait_ms,
                )
                conversation_root = _conversation_root(page)
                current_main = conversation_root.inner_text(timeout=5_000).strip()
                responses = page.locator(response_selector)
                if (
                    not resent_after_authentication
                    and responses.count() <= before_count
                    and prompt not in current_main
                ):
                    # A login challenge can consume the original click without
                    # posting the prompt.  Once authentication is complete,
                    # submit that same in-memory query exactly once.
                    composer.click(timeout=5_000)
                    composer.fill(prompt)
                    with _WEB_SEND_LOCK:
                        _click_send_button(page)
                        _record_web_send()
                    resent_after_authentication = True
                previous = ""
                stable = 0
                remaining_ticks = max(1, self.timeout_ms // 1_000)
                continue
            current_main = conversation_root.inner_text(timeout=5_000).strip()
            text = current_main.rsplit(prompt, 1)[-1].strip() if prompt in current_main else current_main[len(before_main):].strip()
            stable = stable + 1 if text == previous else 0
            previous = text
            if stable >= 5 and len(text) >= 20:
                break
        answer = previous[-24_000:].strip()
        if not answer or answer == prompt:
            raise RuntimeError(f"{self.provider_id} 未返回可用回答。")
        responses = page.locator(response_selector)
        link_scope = responses.last if responses.count() > before_count else conversation_root
        links = _links_from_scope(link_scope)
        return answer, links


class DoubaoWebAdapter(DeepSeekWebAdapter):
    """Doubao web research provider using a retained local browser profile."""

    provider_id = "doubao_web"
    chat_url = "https://www.doubao.com/chat"

    def __init__(
        self,
        profile_dir: Path | None = None,
        *,
        attachment_paths: list[Path] | tuple[Path, ...] | None = None,
    ) -> None:
        super().__init__(profile_dir=profile_dir, attachment_paths=attachment_paths)
        configured = os.environ.get("BID_AGENT_DOUBAO_PROFILE_DIR", "").strip()
        self.profile_dir = profile_dir or Path(configured or ".runtime/doubao-playwright").resolve()
        self.timeout_ms = max(10_000, min(int(os.environ.get("BID_AGENT_DOUBAO_TIMEOUT_MS", "120000")), 300_000))
        self.headless = str(os.environ.get("BID_AGENT_DOUBAO_HEADLESS", "0")).lower() in {"1", "true", "yes"}


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
    is_doubao = "doubao.com" in str(page.url or "")
    configured = os.environ.get(
        "BID_AGENT_DOUBAO_SEARCH_SELECTOR" if is_doubao else "BID_AGENT_DEEPSEEK_SEARCH_SELECTOR",
        "",
    ).strip()
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
            "button:has-text('搜索')" if is_doubao else "",
            "[role='button'][aria-label*='搜索']" if is_doubao else "",
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
    if is_doubao:
        # Doubao's current chat page does not expose a separate web-search
        # switch.  The prompt explicitly requests online search, so continue
        # through its visible composer instead of blocking before it is sent.
        return
    raise RuntimeError(
        "未检测到网页“联网搜索”开关，已停止检索。"
        "如页面结构已更新，请配置对应 Provider 的搜索选择器。"
    )


def _web_login_wait_ms(provider_id: str) -> int:
    provider_key = (
        "BID_AGENT_DOUBAO_LOGIN_WAIT_MS"
        if provider_id == "doubao_web"
        else "BID_AGENT_DEEPSEEK_LOGIN_WAIT_MS"
    )
    raw = (
        os.environ.get(provider_key)
        or os.environ.get("BID_AGENT_WEB_LOGIN_WAIT_MS")
        or "300000"
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 300_000
    return max(30_000, min(value, 900_000))


def _visible_locator_exists(scope: object, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        if not selector:
            continue
        try:
            locator = scope.locator(selector)
            for index in range(locator.count() - 1, -1, -1):
                if locator.nth(index).is_visible(timeout=500):
                    return True
        except Exception:
            continue
    return False


def _web_authentication_pending(page: object, *, provider_id: str) -> bool:
    """Detect login, SMS-code and human-verification UI before chat controls."""
    if provider_id != "doubao_web" and "doubao.com" not in str(
        getattr(page, "url", "") or ""
    ):
        return False
    current_url = str(getattr(page, "url", "") or "").lower()
    if re.search(r"/(login|passport|captcha|verify)(?:[/?#]|$)", current_url):
        return True
    configured = os.environ.get("BID_AGENT_DOUBAO_AUTH_PENDING_SELECTOR", "").strip()
    selectors = tuple(
        item
        for item in (
            configured,
            # Doubao exposes a header “登录” button on its usable guest chat
            # landing page.  That button alone must not block prompt entry: the
            # real authentication boundary is a dialog, credential field or
            # verification surface raised after Send is clicked.
            "[role='dialog']:has-text('登录')",
            ".semi-modal-content:has-text('登录')",
            "input[placeholder*='手机号']",
            "input[placeholder*='验证码']",
            "[role='dialog']:has-text('安全验证')",
            "[role='dialog']:has-text('人机验证')",
            "[role='dialog']:has-text('完成验证')",
            ".semi-modal-content:has-text('验证')",
            "text=请完成验证",
            "text=安全验证",
            "text=人机验证",
            "iframe[src*='captcha']",
            "iframe[src*='verify']",
            "[class*='captcha']",
            "[class*='verify']",
        )
        if item
    )
    frames = tuple(getattr(page, "frames", ()) or ())
    return any(_visible_locator_exists(scope, selectors) for scope in (page, *frames))


def _wait_for_web_chat_authenticated(page, *, provider_id: str, timeout_ms: int):
    """Wait in the same request until auth is complete and chat is truly ready."""
    if provider_id != "doubao_web" and "doubao.com" not in str(page.url or ""):
        composer = _wait_for_composer(
            page,
            timeout_ms=timeout_ms,
            provider_id=provider_id,
        )
        if composer is None:
            raise RuntimeError("未检测到网页聊天输入框。")
        return composer

    elapsed = 0
    stable_ready_polls = 0
    composer = None
    while elapsed < timeout_ms:
        authentication_pending = _web_authentication_pending(
            page,
            provider_id=provider_id,
        )
        composer = None if authentication_pending else _find_visible_composer(
            page,
            provider_id=provider_id,
        )
        # Doubao creates ``#flow-end-msg-send`` only after the composer gets
        # non-empty text.  Requiring Send here deadlocks before ``fill(prompt)``
        # can run; the strict composer plus absence of blocking auth UI is the
        # correct pre-fill readiness boundary.
        chat_ready = not authentication_pending and composer is not None
        if chat_ready:
            stable_ready_polls += 1
            if stable_ready_polls >= 2:
                return composer
        else:
            stable_ready_polls = 0
        # Keep this browser and this search call alive while the user handles
        # SMS, QR-code or human verification.  Two consecutive ready polls keep
        # a disappearing modal from being mistaken for completed login.
        try:
            page.bring_to_front()
        except Exception:
            pass
        page.wait_for_timeout(1_000)
        elapsed += 1_000
    raise WebAuthenticationRequiredError(
        f"{provider_id} 登录或人工验证尚未完成（已等待 {timeout_ms // 1000} 秒）。"
        "请在保持打开的豆包窗口完成验证后重试；本次检索不会按“未找到资料”继续写作。"
    )


def _click_send_button(page) -> None:
    """Send only by clicking the visible page control; never submit via API/Enter."""
    is_doubao = "doubao.com" in str(page.url or "")
    configured = os.environ.get(
        "BID_AGENT_DOUBAO_SEND_SELECTOR" if is_doubao else "BID_AGENT_DEEPSEEK_SEND_SELECTOR",
        "",
    ).strip()
    if is_doubao:
        # The login dialog contains controls such as “发送验证码”.  Restrict
        # Doubao to its actual chat-send control so authentication fields are
        # never filled or submitted by the research prompt.
        selectors = tuple(
            item
            for item in (
                configured,
                "#flow-end-msg-send",
                "#input-engine-container #flow-end-msg-send",
                ".send-btn-wrapper button",
            )
            if item
        )
    else:
        selectors = tuple(
            item
            for item in (
                configured,
                "button[type='submit']",
                "button[data-testid*='send']",
                "[role='button'][aria-label*='发送']",
                "button:has-text('发送')",
                "[role='button'][aria-label*='Send']",
                "button:has-text('Send')",
            )
            if item
        )
    for selector in selectors:
        controls = page.locator(selector)
        for index in range(controls.count() - 1, -1, -1):
            control = controls.nth(index)
            try:
                if control.is_visible() and control.is_enabled():
                    control.click()
                    return
            except Exception:
                continue
    raise RuntimeError("未检测到可点击的网页发送按钮；为避免非页面发送，本次检索已停止。")


def _unwrap_public_url(value: str) -> str:
    """Prefer the cited public target over a chat-provider tracking redirect."""
    candidate = _normalize_public_url(value)
    parsed = urllib.parse.urlparse(candidate)
    if parsed.hostname == "link.wtturl.cn":
        target = urllib.parse.parse_qs(parsed.query).get("target", [""])[0]
        return _normalize_public_url(target) or candidate
    return candidate


def _looks_like_official_host(source_url: str) -> bool:
    host = (urllib.parse.urlparse(source_url).hostname or "").lower()
    return (
        host.endswith(".gov.cn")
        or host == "gov.cn"
        or "std.samr.gov.cn" in host
        or host.endswith(".edu.cn")
    )


def _browser_request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _ssl_context_for_public_fetch():
    try:
        import ssl

        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            import ssl

            return ssl.create_default_context()
        except Exception:
            return None


def _fetch_public_source_content(source_url: str) -> str:
    """Read the cited public source itself; never use an LLM summary as evidence."""
    content, _reason = _fetch_public_source_content_detailed(source_url)
    return content


def _fetch_public_source_content_detailed(source_url: str) -> tuple[str, str]:
    """Return (content, empty_reason) for diagnostics."""
    if not source_url:
        return "", "空 URL"
    try:
        request = urllib.request.Request(
            source_url,
            headers=_browser_request_headers(),
        )
        with urllib.request.urlopen(
            request,
            timeout=25,
            context=_ssl_context_for_public_fetch(),
        ) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read(2_000_000)
            final_url = str(getattr(response, "geturl", lambda: source_url)() or source_url)
    except Exception as exc:
        return "", f"HTTP 请求失败: {type(exc).__name__}: {exc}"[:160]
    if status >= 400:
        return "", f"HTTP 状态 {status}"
    if "pdf" in content_type or source_url.lower().split("?", 1)[0].endswith(".pdf") or final_url.lower().split("?", 1)[0].endswith(".pdf"):
        try:
            from pypdf import PdfReader
            from io import BytesIO

            reader = PdfReader(BytesIO(raw))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)[:60_000].strip()
            if len(text) >= 80:
                return text, ""
            return "", "PDF 可打开但正文过短/无法提取文字"
        except Exception as exc:
            return "", f"PDF 解析失败: {type(exc).__name__}"
    encoding = "utf-8"
    match = re.search(br"charset=([A-Za-z0-9._-]+)", raw[:2_000], re.I)
    if match:
        encoding = match.group(1).decode("ascii", errors="ignore") or encoding
    text = raw.decode(encoding, errors="ignore")
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    min_chars = 80 if _looks_like_official_host(final_url or source_url) else 120
    if len(text) < min_chars:
        return "", f"HTTP 正文过短（{len(text)} 字，阈值 {min_chars}）"
    return text[:60_000], ""


def _extract_page_text(page: object) -> str:
    """Best-effort visible text extraction from a Playwright page."""
    selectors = (
        "main",
        "article",
        "#content",
        ".content",
        "#main",
        "body",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = getattr(locator, "count", None)
            if callable(count) and not count():
                continue
            target = getattr(locator, "first", locator)
            text = str(target.inner_text(timeout=8_000) or "").strip()
            if len(text) >= 80:
                return text
        except Exception:
            continue
    try:
        text = str(
            page.evaluate("() => (document.body && document.body.innerText) || ''")
            or ""
        ).strip()
        return text
    except Exception:
        return ""


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
    if host in {"localhost", "127.0.0.1", "::1", "chat.deepseek.com", "www.doubao.com", "doubao.com"} or host.endswith(".local"):
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


def _find_visible_composer(page: object, *, provider_id: str):
    """Return one chat composer while excluding login and verification fields."""
    configured = os.environ.get(
        "BID_AGENT_DOUBAO_COMPOSER_SELECTOR"
        if provider_id == "doubao_web"
        else "BID_AGENT_DEEPSEEK_COMPOSER_SELECTOR",
        "",
    ).strip()
    selectors = tuple(
        item
        for item in (
            configured,
            "#input-engine-container textarea[placeholder*='发消息']",
            "textarea[placeholder*='发消息']",
            "input[placeholder*='发消息']",
            "[data-placeholder*='发消息']",
            "[aria-label*='发消息']",
            "[contenteditable='true'][role='textbox']",
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
        )
        if item
    )
    authentication_field = re.compile(
        r"手机号|验证码|密码|账号|邮箱|登录|认证|安全验证|人机验证",
        re.I,
    )
    frames = tuple(getattr(page, "frames", ()) or ())
    for scope in (page, *frames):
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(count - 1, -1, -1):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible(timeout=1_000) or not candidate.is_enabled(
                        timeout=1_000
                    ):
                        continue
                    field_description = " ".join(
                        str(candidate.get_attribute(attribute) or "")
                        for attribute in (
                            "placeholder",
                            "aria-label",
                            "name",
                            "autocomplete",
                        )
                    )
                    if authentication_field.search(field_description):
                        continue
                except Exception:
                    continue
                try:
                    inside_authentication_surface = candidate.evaluate(
                        """node => Boolean(node.closest(
                            '[role="dialog"], dialog, [class*="login" i], '
                            + '[class*="verify" i], [class*="captcha" i]'
                        ))"""
                    )
                except Exception:
                    inside_authentication_surface = False
                if inside_authentication_surface:
                    continue
                return candidate
    return None


def _wait_for_composer(
    page: object,
    *,
    timeout_ms: int,
    provider_id: str = "deepseek_web",
):
    """Wait for a real chat composer, including provider iframe DOMs."""
    elapsed = 0
    while elapsed < timeout_ms:
        candidate = _find_visible_composer(page, provider_id=provider_id)
        if candidate is not None:
            return candidate
        page.wait_for_timeout(1_000)
        elapsed += 1_000
    return None


def create_research_adapter(
    provider_id: str | None = None,
    *,
    attachment_paths: list[Path] | tuple[Path, ...] | None = None,
) -> ResearchProviderAdapter:
    selected = str(provider_id or os.environ.get("BID_AGENT_RESEARCH_PROVIDER", "doubao_web")).strip().lower()
    if selected == "deepseek_web":
        # Driving a personal chat account by Playwright can violate a provider's
        # usage rules. It is therefore never an implicit product behavior.
        allowed = str(os.environ.get("BID_AGENT_ALLOW_DEEPSEEK_WEB_AUTOMATION", "0")).lower()
        if allowed not in {"1", "true", "yes"}:
            return DisabledResearchAdapter()
        return DeepSeekWebAdapter(attachment_paths=attachment_paths)
    if selected == "doubao_web":
        return DoubaoWebAdapter(attachment_paths=attachment_paths)
    if selected in {"", "disabled", "manual"}:
        if attachment_paths:
            raise ValueError("V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED")
        return DisabledResearchAdapter()
    raise ValueError(f"未知 V3 研究 Provider: {selected}")
