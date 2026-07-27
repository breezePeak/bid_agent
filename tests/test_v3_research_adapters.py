from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.contracts import EvidenceSourceType  # noqa: E402
from document_pipeline.research_adapters import (  # noqa: E402
    DeepSeekWebAdapter,
    _extract_sources,
    _validate_attachment_paths,
)


class DeepSeekResearchAdapterTests(unittest.TestCase):
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
            ):
                candidates = adapter.search("适用政策", limit=1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://www.gov.cn/zhengce/example")
        self.assertEqual(candidates[0].source_type, EvidenceSourceType.OFFICIAL)

    def test_search_rejects_answer_without_citable_sources(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            adapter = DeepSeekWebAdapter(Path(tmp))
            with mock.patch.object(adapter, "_ask_deepseek", return_value=("没有来源", [])):
                with self.assertRaisesRegex(RuntimeError, "没有可核验"):
                    adapter.search("未知问题", limit=2)

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


if __name__ == "__main__":
    unittest.main()
