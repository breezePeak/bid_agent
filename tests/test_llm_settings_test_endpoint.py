from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import web_app


class _Request:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class LlmSettingsTestEndpointTests(unittest.TestCase):
    def test_missing_fields(self) -> None:
        resp = asyncio.run(
            web_app.api_test_llm_settings(
                _Request({"model": {"name": "x", "base_url": "", "api_key": "", "model": ""}})
            )
        )
        body = json.loads(resp.body.decode("utf-8"))
        self.assertFalse(body.get("ok"))

    def test_success_hello(self) -> None:
        fake_response = {
            "choices": [{"message": {"content": "hello back"}}]
        }

        class _Resp:
            status = 200

            def read(self):
                return json.dumps(fake_response).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=_Resp()):
            resp = asyncio.run(
                web_app.api_test_llm_settings(
                    _Request(
                        {
                            "model": {
                                "name": "t",
                                "base_url": "https://example.com/v1",
                                "api_key": "k",
                                "model": "m1",
                                "timeout": 30,
                                "verify_ssl": True,
                            }
                        }
                    )
                )
            )
        body = json.loads(resp.body.decode("utf-8"))
        self.assertTrue(body.get("ok"), body)
        self.assertIn("hello", body.get("reply", "").lower())
        self.assertEqual(body.get("model"), "m1")


if __name__ == "__main__":
    unittest.main()
