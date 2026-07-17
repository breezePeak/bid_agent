from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import (
    _anthropic_messages_endpoint,
    _extract_anthropic_content,
    _extract_openai_message,
    _extract_stream_delta,
    _normalize_provider,
    _openai_chat_endpoint,
    _split_system_messages,
)


class LlmProviderTests(unittest.TestCase):
    def test_normalize_provider(self) -> None:
        self.assertEqual(_normalize_provider("OpenAI"), "openai")
        self.assertEqual(_normalize_provider("anthropic"), "anthropic")
        self.assertEqual(_normalize_provider("claude"), "anthropic")
        self.assertEqual(_normalize_provider("xxx"), "openai")

    def test_endpoints(self) -> None:
        self.assertTrue(_openai_chat_endpoint("https://api.openai.com/v1").endswith("/chat/completions"))
        self.assertTrue(_anthropic_messages_endpoint("https://api.anthropic.com").endswith("/v1/messages"))
        self.assertTrue(_anthropic_messages_endpoint("https://api.anthropic.com/v1").endswith("/messages"))

    def test_split_system(self) -> None:
        system, msgs = _split_system_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
            ]
        )
        self.assertEqual(system, "sys")
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["role"], "assistant")

    def test_extract_anthropic(self) -> None:
        text = _extract_anthropic_content({"content": [{"type": "text", "text": "hello"}]})
        self.assertEqual(text, "hello")

    def test_extract_openai_message_reasoning(self) -> None:
        content, reasoning = _extract_openai_message(
            {
                "choices": [
                    {
                        "message": {
                            "content": "final answer",
                            "reasoning_content": "step by step",
                        }
                    }
                ]
            }
        )
        self.assertEqual(content, "final answer")
        self.assertEqual(reasoning, "step by step")

    def test_extract_stream_delta_reasoning_aliases(self) -> None:
        reasoning, content = _extract_stream_delta(
            {"choices": [{"delta": {"reasoning": "think", "content": "hi"}}]}
        )
        self.assertEqual(reasoning, "think")
        self.assertEqual(content, "hi")


if __name__ == "__main__":
    unittest.main()
