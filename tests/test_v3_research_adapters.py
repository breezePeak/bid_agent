from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import EvidenceSourceType  # noqa: E402
from document_pipeline.research_adapters import (  # noqa: E402
    DisabledResearchAdapter,
    TavilySearchAdapter,
    create_research_adapter,
)


class TavilyResearchAdapterTests(unittest.TestCase):
    def test_search_uses_tavily_search_and_extract(self) -> None:
        response_body = json.dumps(
            {
                "results": [
                    {
                        "title": "政策文件",
                        "url": "https://www.gov.cn/zhengce/example#section",
                        "raw_content": "公开政策正文。" * 30,
                    }
                ]
            }
        ).encode("utf-8")

        class _Response:
            status = 200

            def read(self, _size: int) -> bytes:
                return response_body

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        with (
            mock.patch.dict(
                os.environ,
                {"BID_AGENT_TAVILY_API_KEY": "tvly-test"},
                clear=False,
            ),
            mock.patch(
                "document_pipeline.deep_research.tavily_tools.urllib.request.urlopen",
                return_value=_Response(),
            ) as urlopen,
        ):
            candidates = TavilySearchAdapter().search("适用政策", limit=1)

        self.assertEqual(urlopen.call_count, 2)
        search_request = urlopen.call_args_list[0].args[0]
        extract_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(search_request.full_url, "https://api.tavily.com/search")
        self.assertEqual(extract_request.full_url, "https://api.tavily.com/extract")
        self.assertEqual(search_request.get_header("Authorization"), "Bearer tvly-test")
        self.assertEqual(candidates[0].source_url, "https://www.gov.cn/zhengce/example")
        self.assertEqual(candidates[0].source_type, EvidenceSourceType.OFFICIAL)

    def test_factory_defaults_to_tavily(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BID_AGENT_RESEARCH_PROVIDER", None)
            self.assertIsInstance(create_research_adapter(), TavilySearchAdapter)

    def test_factory_rejects_all_browser_providers(self) -> None:
        for provider_id in ("doubao_web", "deepseek_web", "bing", "google"):
            with self.subTest(provider_id=provider_id):
                with self.assertRaisesRegex(
                    ValueError,
                    "V3_RESEARCH_PROVIDER_UNSUPPORTED",
                ):
                    create_research_adapter(provider_id)

    def test_tavily_rejects_attachments(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "V3_RESEARCH_ATTACHMENTS_PROVIDER_UNSUPPORTED",
        ):
            create_research_adapter("tavily", attachment_paths=[Path("source.pdf")])

    def test_disabled_provider_remains_an_explicit_kill_switch(self) -> None:
        self.assertIsInstance(create_research_adapter("disabled"), DisabledResearchAdapter)

    def test_missing_key_is_reported_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"BID_AGENT_CONFIG_ROOT": temporary},
            clear=False,
        ):
            os.environ.pop("BID_AGENT_TAVILY_API_KEY", None)
            os.environ.pop("TAVILY_API_KEY", None)
            adapter = create_research_adapter("tavily")
            self.assertEqual(adapter.runtime_status()["reason"], "TAVILY_API_KEY_MISSING")
            with self.assertRaisesRegex(RuntimeError, "TAVILY_API_KEY_MISSING"):
                adapter.search("适用政策", limit=1)

    def test_reads_tavily_key_from_authoritative_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, ".env").write_text(
                "BID_AGENT_TAVILY_API_KEY=tvly-from-dotenv\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"BID_AGENT_CONFIG_ROOT": temporary},
                clear=False,
            ):
                os.environ.pop("BID_AGENT_TAVILY_API_KEY", None)
                os.environ.pop("TAVILY_API_KEY", None)
                self.assertEqual(TavilySearchAdapter().api_key, "tvly-from-dotenv")


if __name__ == "__main__":
    unittest.main()
