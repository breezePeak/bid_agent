from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
import json
import os
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import EvidenceSourceType  # noqa: E402
from document_pipeline.research_adapters import (  # noqa: E402
    DeepSeekWebAdapter,
    DoubaoWebAdapter,
    TavilySearchAdapter,
    _WEB_RUNTIME_STATUS,
    _click_send_button,
    _extract_sources,
    _run_in_persistent_web_context,
    _validate_attachment_paths,
    _wait_for_composer,
    _wait_for_web_chat_authenticated,
    close_web_sessions,
    create_research_adapter,
)


class _FakeBrowser:
    def __init__(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


class _FakeContext:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()
        self.pages: list[object] = []
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.browser.connected = False


class _FakeChromium:
    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []

    def launch_persistent_context(self, _profile: str, *, headless: bool) -> _FakeContext:
        del headless
        context = _FakeContext()
        self.contexts.append(context)
        return context


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _FakePlaywrightStarter:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self.playwright = playwright
        self.start_calls = 0

    def start(self) -> _FakePlaywright:
        self.start_calls += 1
        return self.playwright


class _FakeBody:
    def __init__(self, text: str) -> None:
        self.text = text

    def count(self) -> int:
        return 1

    def inner_text(self, *, timeout: int) -> str:
        del timeout
        return self.text


class _FakeProviderPage:
    def __init__(self, focus_log: list[str]) -> None:
        self.url = "https://chat.deepseek.com/a/chat/s/result"
        self.focus_log = focus_log

    def is_closed(self) -> bool:
        return False

    def bring_to_front(self) -> None:
        self.focus_log.append("provider")


class _FakeSourcePage:
    def __init__(
        self,
        text: str,
        *,
        focus_log: list[str] | None = None,
        goto_error: bool = False,
    ) -> None:
        self.url = "about:blank"
        self.body = _FakeBody(text)
        self.focus_log = focus_log if focus_log is not None else []
        self.goto_error = goto_error
        self.goto_calls: list[str] = []
        self.bring_to_front_calls = 0
        self.close_calls = 0
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def goto(self, url: str, **_kwargs: object) -> None:
        self.goto_calls.append(url)
        if self.goto_error:
            raise RuntimeError("navigation failed")
        self.url = url

    def bring_to_front(self) -> None:
        self.bring_to_front_calls += 1
        self.focus_log.append("source")

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def locator(self, selector: str) -> _FakeBody:
        if selector != "body":
            raise AssertionError(selector)
        return self.body

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class _FakeSourceContext:
    def __init__(
        self,
        text: str,
        *,
        reader_pages: list[_FakeSourcePage] | None = None,
    ) -> None:
        self.focus_log: list[str] = []
        self.provider_page = _FakeProviderPage(self.focus_log)
        self.reader_pages = reader_pages or [
            _FakeSourcePage(text, focus_log=self.focus_log)
        ]
        for page in self.reader_pages:
            page.focus_log = self.focus_log
        self.page = self.reader_pages[0]
        self.pages: list[object] = [self.provider_page]
        self.new_page_calls = 0

    def new_page(self) -> _FakeSourcePage:
        page = self.reader_pages[self.new_page_calls]
        self.new_page_calls += 1
        self.pages.append(page)
        # Chromium activates a newly-created about:blank tab.
        self.focus_log.append("source")
        return page


class _FakeElement:
    def __init__(
        self,
        *,
        placeholder: str = "",
        visible: bool = True,
        enabled: bool = True,
        inside_authentication_surface: bool = False,
    ) -> None:
        self.placeholder = placeholder
        self.visible = visible
        self.enabled = enabled
        self.inside_authentication_surface = inside_authentication_surface
        self.click_calls = 0
        self.fill_calls: list[str] = []

    def is_visible(self, *, timeout: int = 0) -> bool:
        del timeout
        return self.visible

    def is_enabled(self, *, timeout: int = 0) -> bool:
        del timeout
        return self.enabled

    def get_attribute(self, name: str) -> str | None:
        if name == "placeholder":
            return self.placeholder
        return None

    def evaluate(self, _script: str) -> bool:
        return self.inside_authentication_surface

    def click(self, **_kwargs: object) -> None:
        self.click_calls += 1

    def fill(self, value: str) -> None:
        self.fill_calls.append(value)


class _FakeElements:
    def __init__(self, elements: list[_FakeElement]) -> None:
        self.elements = elements

    def count(self) -> int:
        return len(self.elements)

    def nth(self, index: int) -> _FakeElement:
        return self.elements[index]


class _AuthenticationPage:
    def __init__(
        self,
        *,
        authenticated_after_ticks: int = 2,
        blocking_authentication: bool = True,
        chat_composer: bool = True,
        send_requires_text: bool = False,
    ) -> None:
        self.url = "https://www.doubao.com/chat"
        self.frames: list[object] = []
        self.ticks = 0
        self.authenticated_after_ticks = authenticated_after_ticks
        self.blocking_authentication = blocking_authentication
        self.chat_composer = chat_composer
        self.send_requires_text = send_requires_text
        self.queries: list[str] = []
        self.auth_input = _FakeElement(placeholder="请输入手机号")
        self.composer = _FakeElement(placeholder="发消息...")
        self.send = _FakeElement()
        self.login = _FakeElement()

    def locator(self, selector: str) -> _FakeElements:
        self.queries.append(selector)
        authenticated = self.ticks >= self.authenticated_after_ticks
        if selector == "button:has-text('登录')" and not authenticated:
            return _FakeElements([self.login])
        if (
            selector == "input[placeholder*='手机号']"
            and self.blocking_authentication
            and not authenticated
        ):
            return _FakeElements([self.auth_input])
        if selector == "textarea" and not self.chat_composer:
            return _FakeElements([self.auth_input])
        if selector == "textarea[placeholder*='发消息']" and self.chat_composer:
            return _FakeElements([self.composer])
        if (
            selector == "#flow-end-msg-send"
            and self.chat_composer
            and (not self.send_requires_text or self.composer.fill_calls)
        ):
            return _FakeElements([self.send])
        return _FakeElements([])

    def bring_to_front(self) -> None:
        return None

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.assert_one_second(milliseconds)
        self.ticks += 1

    @staticmethod
    def assert_one_second(milliseconds: int) -> None:
        if milliseconds != 1_000:
            raise AssertionError(milliseconds)


class DeepSeekResearchAdapterTests(unittest.TestCase):
    def tearDown(self) -> None:
        close_web_sessions()
        _WEB_RUNTIME_STATUS.clear()

    def test_extracts_and_deduplicates_only_public_source_urls(self) -> None:
        answer = (
            "结论一[国家标准](https://std.samr.gov.cn/example#part)。"
            "结论二 https://example.com/report。"
        )
        sources = _extract_sources(
            answer,
            [
                ("重复标准", "https://std.samr.gov.cn/example#part"),
                ("DeepSeek 会话", "https://chat.deepseek.com/a/chat/s/123"),
            ],
        )
        self.assertEqual(
            sources,
            [
                ("重复标准", "https://std.samr.gov.cn/example"),
                ("", "https://example.com/report"),
            ],
        )

    def test_search_returns_one_candidate_per_citable_source_and_honors_limit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            with mock.patch.object(
                adapter,
                "_ask_deepseek",
                return_value=(
                    "研究结论",
                    [
                        ("政府文件", "https://www.gov.cn/zhengce/example"),
                        ("行业文章", "https://example.com/article"),
                    ],
                ),
            ), mock.patch.object(
                adapter,
                "runtime_status",
                return_value={"ready": True},
            ), mock.patch.object(
                adapter,
                "_read_public_source",
                return_value="可核验的政府公开文件正文。" * 20,
            ):
                candidates = adapter.search("适用政策", limit=1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://www.gov.cn/zhengce/example")
        self.assertEqual(candidates[0].source_type, EvidenceSourceType.OFFICIAL)

    def test_search_rejects_answer_without_citable_sources(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            with mock.patch.object(
                adapter,
                "runtime_status",
                return_value={"ready": True},
            ), mock.patch.object(
                adapter,
                "_ask_deepseek",
                return_value=("没有来源", []),
            ):
                with self.assertRaisesRegex(RuntimeError, "没有可核验"):
                    adapter.search("未知问题", limit=2)

    def test_search_fails_instead_of_reporting_gap_when_sources_cannot_be_read(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            with mock.patch.object(
                adapter,
                "runtime_status",
                return_value={"ready": True},
            ), mock.patch.object(
                adapter,
                "_ask_deepseek",
                return_value=("研究结论", [("公开来源", "https://example.com/report")]),
            ), mock.patch.object(
                adapter,
                "_read_public_source_detailed",
                return_value=("", "HTTP 状态 403"),
            ):
                with self.assertRaisesRegex(RuntimeError, "引用页面原文均未能读取"):
                    adapter.search("检索问题", limit=2)
                try:
                    adapter.search("检索问题", limit=2)
                except RuntimeError as exc:
                    message = str(exc)
                    self.assertIn("https://example.com/report", message)
                    self.assertIn("HTTP 状态 403", message)
                    self.assertIn("豆包回答后会再打开链接", message)

    def test_authentication_field_is_never_used_as_chat_composer(self) -> None:
        page = _AuthenticationPage(
            authenticated_after_ticks=999,
            chat_composer=False,
        )

        composer = _wait_for_composer(
            page,
            timeout_ms=1_000,
            provider_id="doubao_web",
        )

        self.assertIsNone(composer)
        self.assertEqual(page.auth_input.click_calls, 0)
        self.assertEqual(page.auth_input.fill_calls, [])

    def test_same_authentication_wait_resumes_when_doubao_becomes_ready(self) -> None:
        page = _AuthenticationPage(authenticated_after_ticks=2)

        composer = _wait_for_web_chat_authenticated(
            page,
            provider_id="doubao_web",
            timeout_ms=5_000,
        )

        self.assertIs(composer, page.composer)
        self.assertEqual(page.ticks, 3)
        self.assertEqual(page.auth_input.fill_calls, [])

    def test_guest_login_button_does_not_block_prompt_entry(self) -> None:
        page = _AuthenticationPage(
            authenticated_after_ticks=999,
            blocking_authentication=False,
            send_requires_text=True,
        )

        composer = _wait_for_web_chat_authenticated(
            page,
            provider_id="doubao_web",
            timeout_ms=3_000,
        )
        pre_fill_queries = list(page.queries)
        composer.fill("需要检索的资料")
        _click_send_button(page)

        self.assertIs(composer, page.composer)
        self.assertEqual(page.ticks, 1)
        self.assertEqual(page.composer.fill_calls, ["需要检索的资料"])
        self.assertNotIn("#flow-end-msg-send", pre_fill_queries)
        self.assertEqual(page.send.click_calls, 1)

    def test_doubao_send_never_clicks_send_verification_fallback(self) -> None:
        page = _AuthenticationPage(
            authenticated_after_ticks=0,
            blocking_authentication=False,
        )

        _click_send_button(page)

        self.assertEqual(page.send.click_calls, 1)
        self.assertNotIn("button:has-text('发送')", page.queries)

    def test_persistent_session_reuses_one_owner_thread_until_shutdown(self) -> None:
        playwright = _FakePlaywright()
        starter = _FakePlaywrightStarter(playwright)
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = lambda: starter
        package = types.ModuleType("playwright")
        package.sync_api = sync_api
        results: list[tuple[int, int]] = []

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(
            sys.modules,
            {"playwright": package, "playwright.sync_api": sync_api},
        ):
            profile = Path(tmp) / "profile"

            def call_from_worker() -> None:
                results.append(
                    _run_in_persistent_web_context(
                        profile,
                        headless=False,
                        operation=lambda context, _state: (
                            id(context),
                            threading.get_ident(),
                        ),
                    )
                )

            first = threading.Thread(target=call_from_worker)
            first.start()
            first.join()
            second = threading.Thread(target=call_from_worker)
            second.start()
            second.join()

            self.assertEqual(starter.start_calls, 1)
            self.assertEqual(len(playwright.chromium.contexts), 1)
            self.assertEqual(results[0], results[1])
            self.assertEqual(playwright.chromium.contexts[0].close_calls, 0)
            self.assertEqual(playwright.stop_calls, 0)

            with self.assertRaisesRegex(RuntimeError, "operation failed"):
                _run_in_persistent_web_context(
                    profile,
                    headless=False,
                    operation=lambda _context, _state: (_ for _ in ()).throw(
                        RuntimeError("operation failed")
                    ),
                )
            self.assertEqual(
                _run_in_persistent_web_context(
                    profile,
                    headless=False,
                    operation=lambda _context, _state: "still-open",
                ),
                "still-open",
            )

            first_context = playwright.chromium.contexts[0]
            first_context.browser.connected = False
            replacement_context_id = _run_in_persistent_web_context(
                profile,
                headless=False,
                operation=lambda context, _state: id(context),
            )
            self.assertEqual(len(playwright.chromium.contexts), 2)
            self.assertEqual(replacement_context_id, id(playwright.chromium.contexts[1]))

            close_web_sessions()
            self.assertEqual(first_context.close_calls, 0)
            self.assertEqual(playwright.chromium.contexts[1].close_calls, 1)
            self.assertEqual(playwright.stop_calls, 1)

            self.assertEqual(
                _run_in_persistent_web_context(
                    profile,
                    headless=False,
                    operation=lambda _context, _state: "restarted",
                ),
                "restarted",
            )
            self.assertEqual(starter.start_calls, 2)
            self.assertEqual(len(playwright.chromium.contexts), 3)

    def test_public_source_reader_tab_stays_open_and_is_reused(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            context = _FakeSourceContext("可核验的公开来源正文。" * 20)
            state: dict[str, object] = {}
            with mock.patch(
                "document_pipeline.research_adapters._fetch_public_source_content_detailed",
                return_value=("", "HTTP blocked"),
            ), mock.patch(
                "document_pipeline.research_adapters._run_in_persistent_web_context",
                side_effect=lambda _profile, *, headless, operation: operation(context, state),
            ):
                first = adapter._read_public_source("https://example.com/first")
                repeated = adapter._read_public_source("https://example.com/first")
                second = adapter._read_public_source("https://example.com/second")

        self.assertTrue(first)
        self.assertEqual(repeated, first)
        self.assertEqual(second, first)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(
            context.page.goto_calls,
            ["https://example.com/first", "https://example.com/second"],
        )
        self.assertEqual(context.page.close_calls, 0)
        self.assertEqual(context.page.bring_to_front_calls, 0)
        self.assertIs(state["source_page"], context.page)
        self.assertEqual(context.focus_log[-1], "provider")

    def test_http_source_read_does_not_open_an_extra_browser_tab(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            verified = "后台直接读取的公开来源正文。" * 20
            with mock.patch(
                "document_pipeline.research_adapters._fetch_public_source_content_detailed",
                return_value=(verified, ""),
            ), mock.patch(
                "document_pipeline.research_adapters._run_in_persistent_web_context",
            ) as browser_read:
                content = adapter._read_public_source("https://example.com/report")

        self.assertEqual(content, verified)
        browser_read.assert_not_called()

    def test_provider_reuses_chromiums_initial_blank_tab(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            blank_page = mock.Mock()
            blank_page.url = "about:blank"

            def navigate(url: str, **_kwargs: object) -> None:
                blank_page.url = url

            blank_page.goto.side_effect = navigate
            context = mock.Mock()
            context.pages = [blank_page]
            state: dict[str, object] = {}
            with mock.patch(
                "document_pipeline.research_adapters._wait_for_web_chat_authenticated",
                side_effect=RuntimeError("stop after page selection"),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after page selection"):
                    adapter._ask_deepseek_in_context(
                        context,
                        state=state,
                        prompt="检索问题",
                        attachment_paths=(),
                    )

        context.new_page.assert_not_called()
        blank_page.goto.assert_called_once_with(
            adapter.chat_url,
            wait_until="domcontentloaded",
            timeout=adapter.timeout_ms,
        )
        self.assertIs(state["provider_page"], blank_page)

    def test_existing_provider_closes_only_stale_about_blank_tabs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            provider_page = mock.Mock()
            provider_page.url = "https://chat.deepseek.com/a/chat/s/result"
            stale_blank = mock.Mock()
            stale_blank.url = "about:blank"
            context = mock.Mock()
            context.pages = [provider_page, stale_blank]
            state: dict[str, object] = {}
            with mock.patch(
                "document_pipeline.research_adapters._wait_for_web_chat_authenticated",
                side_effect=RuntimeError("stop after cleanup"),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after cleanup"):
                    adapter._ask_deepseek_in_context(
                        context,
                        state=state,
                        prompt="检索问题",
                        attachment_paths=(),
                    )

        stale_blank.close.assert_called_once_with()
        provider_page.close.assert_not_called()
        context.new_page.assert_not_called()
        self.assertIs(state["provider_page"], provider_page)

    def test_failed_about_blank_reader_is_closed_and_provider_stays_in_front(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            failed = _FakeSourcePage("", goto_error=True)
            successful = _FakeSourcePage("可核验的公开来源正文。" * 20)
            context = _FakeSourceContext(
                "",
                reader_pages=[failed, successful],
            )
            state: dict[str, object] = {}

            missing = adapter._read_public_source_in_context(
                context,
                state,
                "https://example.com/fails",
            )
            recovered = adapter._read_public_source_in_context(
                context,
                state,
                "https://example.com/works",
            )

        self.assertEqual(missing, "")
        self.assertEqual(failed.close_calls, 1)
        self.assertTrue(failed.closed)
        self.assertEqual(recovered, "可核验的公开来源正文。" * 20)
        self.assertEqual(context.new_page_calls, 2)
        self.assertIs(state["source_page"], successful)
        self.assertEqual(successful.close_calls, 0)
        self.assertEqual(context.focus_log[-1], "provider")

    def test_validates_attachment_files_and_fingerprints_their_content(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            tender = root / "招标文件.pdf"
            tender.write_bytes(b"first")
            self.assertEqual(_validate_attachment_paths([tender, tender]), (tender.resolve(),))
            first = DeepSeekWebAdapter(root / "profile-a", attachment_paths=[tender])
            tender.write_bytes(b"second")
            second = DeepSeekWebAdapter(root / "profile-b", attachment_paths=[tender])
            self.assertNotEqual(first.cache_fingerprint, second.cache_fingerprint)

            executable = root / "unsafe.exe"
            executable.write_bytes(b"not allowed")
            with self.assertRaisesRegex(ValueError, "不允许上传"):
                DeepSeekWebAdapter(root / "profile-c", attachment_paths=[executable])

    def test_doubao_provider_uses_its_own_persistent_profile(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            profile = Path(tmp) / "doubao-profile"
            adapter = DoubaoWebAdapter(profile)
            self.assertEqual(adapter.provider_id, "doubao_web")
            self.assertEqual(adapter.chat_url, "https://www.doubao.com/chat")
            self.assertEqual(adapter.profile_dir, profile)
            self.assertIsInstance(create_research_adapter("doubao_web"), DoubaoWebAdapter)


class TavilyResearchAdapterTests(unittest.TestCase):
    def test_search_uses_documented_api_and_returns_source_content(self) -> None:
        response_body = json.dumps({
            "results": [{
                "title": "政策文件",
                "url": "https://www.gov.cn/zhengce/example#section",
                "raw_content": "公开政策正文。" * 30,
            }],
        }).encode("utf-8")

        class _Response:
            status = 200

            def read(self, _size: int) -> bytes:
                return response_body

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        with mock.patch.dict(os.environ, {"BID_AGENT_TAVILY_API_KEY": "tvly-test"}, clear=False), mock.patch(
            "document_pipeline.research_adapters.urllib.request.urlopen",
            return_value=_Response(),
        ) as urlopen:
            adapter = TavilySearchAdapter()
            candidates = adapter.search("适用政策", limit=1)

        self.assertEqual(urlopen.call_count, 2)
        search_request = urlopen.call_args_list[0].args[0]
        extract_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(search_request.full_url, "https://api.tavily.com/search")
        self.assertEqual(extract_request.full_url, "https://api.tavily.com/extract")
        self.assertEqual(search_request.get_header("Authorization"), "Bearer tvly-test")
        search_payload = json.loads(search_request.data.decode("utf-8"))
        self.assertFalse(search_payload["include_answer"])
        self.assertFalse(search_payload["include_raw_content"])
        extract_payload = json.loads(extract_request.data.decode("utf-8"))
        self.assertNotIn("query", extract_payload)
        self.assertNotIn("chunks_per_source", extract_payload)
        self.assertEqual(candidates[0].source_url, "https://www.gov.cn/zhengce/example")
        self.assertEqual(candidates[0].source_type, EvidenceSourceType.OFFICIAL)

    def test_factory_creates_tavily_and_requires_key_at_search_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"BID_AGENT_CONFIG_ROOT": tmp},
            clear=False,
        ):
            os.environ.pop("BID_AGENT_TAVILY_API_KEY", None)
            os.environ.pop("TAVILY_API_KEY", None)
            adapter = create_research_adapter("tavily")
            self.assertIsInstance(adapter, TavilySearchAdapter)
            self.assertEqual(adapter.runtime_status()["reason"], "TAVILY_API_KEY_MISSING")
            with self.assertRaisesRegex(RuntimeError, "TAVILY_API_KEY_MISSING"):
                adapter.search("适用政策", limit=1)

    def test_reads_tavily_key_from_authoritative_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text("BID_AGENT_TAVILY_API_KEY=tvly-from-dotenv\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"BID_AGENT_CONFIG_ROOT": tmp},
                clear=False,
            ):
                os.environ.pop("BID_AGENT_TAVILY_API_KEY", None)
                os.environ.pop("TAVILY_API_KEY", None)
                self.assertEqual(TavilySearchAdapter().api_key, "tvly-from-dotenv")


if __name__ == "__main__":
    unittest.main()
