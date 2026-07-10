from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import llm_client
import web_app
from config import Settings, get_settings


def _write_env(path: Path, *, key: str, model: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_BASE_URL=https://new.example/v1",
                f"OPENAI_API_KEY={key}",
                f"OPENAI_MODEL={model}",
                "OPENAI_TIMEOUT=30",
                "OPENAI_MAX_RETRIES=2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class _Request:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class LiveLlmConfigTests(unittest.TestCase):
    def test_managed_workspaces_reload_central_config_over_stale_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_root = base / "config"
            run_a = base / "run-a"
            run_b = base / "run-b"
            run_a.mkdir()
            run_b.mkdir()
            _write_env(config_root, key="new-key-1", model="new-model-1")
            stale_env = {
                "BID_AGENT_CONFIG_ROOT": str(config_root),
                "OPENAI_BASE_URL": "https://old.example/v1",
                "OPENAI_API_KEY": "old-key",
                "OPENAI_MODEL": "old-model",
            }
            with patch.dict(os.environ, stale_env, clear=False):
                self.assertEqual(get_settings(run_a).api_key, "new-key-1")
                self.assertEqual(get_settings(run_b).model, "new-model-1")
                _write_env(config_root, key="new-key-2", model="new-model-2")
                self.assertEqual(get_settings(run_a).api_key, "new-key-2")
                self.assertEqual(get_settings(run_b).model, "new-model-2")

    def test_saving_active_model_applies_it_live_without_set_active_flag(self) -> None:
        store = {
            "active_id": "active",
            "models": [
                {
                    "id": "active",
                    "name": "主模型",
                    "base_url": "https://old.example/v1",
                    "api_key": "old",
                    "model": "old-model",
                }
            ],
        }
        payload = {
            "model": {
                "id": "active",
                "name": "主模型",
                "base_url": "https://new.example/v1",
                "api_key": "new",
                "model": "new-model",
            },
            "set_active": False,
        }
        with (
            patch("web_app._read_models_store", return_value=store),
            patch("web_app._write_models_store"),
            patch("web_app._sync_model_to_env") as sync,
            patch("web_app._llm_config_revision", return_value="rev-2"),
        ):
            response = asyncio.run(web_app.api_set_llm_settings(_Request(payload)))

        body = json.loads(response.body)
        self.assertTrue(body["applied_live"])
        self.assertEqual(body["config_revision"], "rev-2")
        self.assertEqual(sync.call_args.args[0]["api_key"], "new")

    def test_retry_reloads_model_and_key(self) -> None:
        old = Settings("https://old.example/v1", "old-key", "old-model", max_retries=2)
        new = Settings("https://new.example/v1", "new-key", "new-model", max_retries=2)
        response = MagicMock()
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "ok"}}]}
        ).encode("utf-8")
        context = MagicMock()
        context.__enter__.return_value = response
        context.__exit__.return_value = False
        with (
            patch("llm_client.get_settings", side_effect=[old, old, new]),
            patch("llm_client.urllib.request.urlopen", side_effect=[urllib.error.URLError("temporary"), context]) as urlopen,
            patch("llm_client.time.sleep"),
        ):
            result = llm_client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "ok")
        second_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(second_request.get_header("Authorization"), "Bearer new-key")
        self.assertEqual(json.loads(second_request.data)["model"], "new-model")


if __name__ == "__main__":
    unittest.main()
