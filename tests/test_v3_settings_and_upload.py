from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import api.v3_app as v3_app
from api.settings_service import SettingsService


class _Request:
    def __init__(self, body: object) -> None:
        self.body = body

    async def json(self) -> object:
        return self.body


def _payload(response) -> dict[str, object]:
    return json.loads(response.body)


class V3SettingsAndUploadTests(unittest.TestCase):
    def test_settings_routes_are_owned_by_the_standalone_v3_app(self) -> None:
        paths = {getattr(route, "path", "") for route in v3_app.app.routes}
        self.assertIn("/api/llm-settings", paths)
        self.assertIn("/api/llm-settings/activate", paths)
        self.assertIn("/api/llm-settings/delete", paths)
        self.assertIn("/api/llm-settings/test", paths)
        self.assertIn("/api/flow-settings", paths)

    def test_runtime_settings_are_scoped_to_the_application_lifespan(self) -> None:
        async def exercise_lifespan() -> None:
            async with v3_app._runtime_settings_lifespan(v3_app.app):
                self.assertEqual(os.environ["OPENAI_MODEL"], "file-model")
                self.assertEqual(
                    os.environ["BID_AGENT_CONFIG_ROOT"],
                    str(v3_app.ROOT),
                )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "OPENAI_BASE_URL=https://example.test/v1",
                        "OPENAI_API_KEY=test-key",
                        "OPENAI_MODEL=file-model",
                        "OPENAI_PROVIDER=openai",
                        "CHAPTER_REVIEW_GATE=0",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            settings = SettingsService(root)
            sentinel = {
                "OPENAI_MODEL": "parent-model",
                "CHAPTER_REVIEW_GATE": "1",
                "BID_AGENT_CONFIG_ROOT": "parent-root",
            }
            with (
                mock.patch.dict(os.environ, sentinel, clear=False),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                asyncio.run(exercise_lifespan())
                self.assertEqual(os.environ["OPENAI_MODEL"], "parent-model")
                self.assertEqual(os.environ["CHAPTER_REVIEW_GATE"], "1")
                self.assertEqual(
                    os.environ["BID_AGENT_CONFIG_ROOT"],
                    "parent-root",
                )

    def test_auth_prefers_process_environment_and_sets_secure_cookies(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "BID_AGENT_AUTH_USER=file-user",
                        "BID_AGENT_AUTH_PASSWORD=file-password",
                        "BID_AGENT_AUTH_SECURE_COOKIE=0",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            settings = SettingsService(root)
            environment = {
                "BID_AGENT_AUTH_USER": "env-user",
                "BID_AGENT_AUTH_PASSWORD": "env-password",
                "BID_AGENT_AUTH_SECURE_COOKIE": "1",
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                response = asyncio.run(
                    v3_app.login(
                        _Request(
                            {
                                "username": "env-user",
                                "password": "env-password",
                            }
                        )
                    )
                )

            self.assertEqual(response.status_code, 200)
            body = response.body.decode("utf-8")
            self.assertNotIn("env-password", body)
            self.assertNotIn("file-password", body)
            cookies = "\n".join(
                value.decode("latin-1")
                for key, value in response.raw_headers
                if key.lower() == b"set-cookie"
            )
            self.assertIn("bid_agent_session=", cookies)
            self.assertIn("bid_agent_csrf=", cookies)
            self.assertIn("HttpOnly", cookies)
            self.assertEqual(cookies.count("Secure"), 2)
            with v3_app._SESSION_LOCK:
                v3_app._SESSIONS.clear()

    def test_auth_fails_closed_when_password_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            settings = SettingsService(Path(temporary))
            clean_auth = {
                "BID_AGENT_AUTH_USER": "admin",
                "BID_AGENT_AUTH_PASSWORD": "",
            }
            with (
                mock.patch.dict(os.environ, clean_auth, clear=False),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                response = asyncio.run(
                    v3_app.login(
                        _Request({"username": "admin", "password": "anything"})
                    )
                )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(_payload(response)["ok"])
        self.assertNotIn("anything", response.body.decode("utf-8"))

    def test_llm_and_flow_settings_are_persisted_and_applied_live(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "OPENAI_BASE_URL=https://from-file.example/v1",
                        "OPENAI_API_KEY=file-key",
                        "OPENAI_MODEL=file-model",
                        "OPENAI_PROVIDER=openai",
                        "BID_AGENT_WORKERS_DEFAULT=3",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            settings = SettingsService(root)
            stale_environment = {
                "OPENAI_BASE_URL": "https://stale-process.example/v1",
                "OPENAI_API_KEY": "stale-key",
                "OPENAI_MODEL": "stale-model",
                "BID_AGENT_WORKERS_DEFAULT": "9",
            }
            with (
                mock.patch.dict(os.environ, stale_environment, clear=False),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                initial_response = v3_app.get_llm_settings()
                initial = _payload(initial_response)
                self.assertEqual(
                    initial["models"][0]["base_url"],
                    "https://from-file.example/v1",
                )
                self.assertEqual(initial["models"][0]["model"], "file-model")
                self.assertTrue(initial["models"][0]["has_api_key"])
                self.assertEqual(
                    initial["models"][0]["api_key_masked"],
                    "••••••••",
                )
                self.assertNotIn("api_key", initial["models"][0])
                self.assertNotIn("file-key", initial_response.body.decode("utf-8"))
                model_id = initial["models"][0]["id"]

                saved_response = asyncio.run(
                    v3_app.set_llm_settings(
                        _Request(
                            {
                                "model": {
                                    "id": model_id,
                                    "name": "主模型",
                                    "provider": "openai",
                                    "base_url": "https://new.example/v1",
                                    "api_key": "new-key",
                                    "model": "new-model",
                                    "timeout": 60,
                                    "max_retries": 2,
                                    "retry_initial_delay": 1,
                                    "retry_max_delay": 5,
                                    "stream": False,
                                    "verify_ssl": True,
                                },
                                "set_active": True,
                            }
                        )
                    )
                )
                saved = _payload(saved_response)
                self.assertTrue(saved["ok"])
                self.assertTrue(saved["applied_live"])
                self.assertEqual(saved["active_id"], model_id)
                self.assertEqual(os.environ["OPENAI_MODEL"], "new-model")
                self.assertNotIn("new-key", saved_response.body.decode("utf-8"))
                self.assertNotIn("api_key", saved["models"][0])

                retained_response = asyncio.run(
                    v3_app.set_llm_settings(
                        _Request(
                            {
                                "model": {
                                    **saved["models"][0],
                                    "api_key": "",
                                    "model": "new-model-revised",
                                },
                                "set_active": True,
                            }
                        )
                    )
                )
                retained = _payload(retained_response)
                self.assertTrue(retained["ok"])
                self.assertNotIn("new-key", retained_response.body.decode("utf-8"))
                self.assertEqual(settings.active_model()["api_key"], "new-key")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "new-key")
                self.assertEqual(os.environ["OPENAI_MODEL"], "new-model-revised")

                flow = _payload(
                    asyncio.run(
                        v3_app.set_flow_settings(
                            _Request(
                                {
                                    "settings": {
                                        "workers": 99,
                                        "chapter_review_enabled": False,
                                    }
                                }
                            )
                        )
                    )
                )
                self.assertTrue(flow["ok"])
                self.assertEqual(flow["settings"]["workers"], 10)
                self.assertFalse(flow["settings"]["chapter_review_gate"])
                self.assertFalse(flow["settings"]["global_review_gate"])
                self.assertFalse(flow["settings"]["anti_fabrication_gate"])
                self.assertEqual(os.environ["BID_AGENT_WORKERS_DEFAULT"], "10")

            persisted = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("OPENAI_BASE_URL=https://new.example/v1", persisted)
            self.assertIn("OPENAI_MODEL=new-model-revised", persisted)
            self.assertIn("BID_AGENT_WORKERS_DEFAULT=10", persisted)

    def test_all_model_mutation_responses_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            settings = SettingsService(Path(temporary))
            with mock.patch.object(v3_app, "SETTINGS", settings):
                created_response = asyncio.run(
                    v3_app.set_llm_settings(
                        _Request(
                            {
                                "model": {
                                    "name": "模型 A",
                                    "base_url": "https://a.example/v1",
                                    "api_key": "secret-a",
                                    "model": "model-a",
                                },
                                "set_active": True,
                            }
                        )
                    )
                )
                created = _payload(created_response)
                model_a_id = created["saved_id"]

                second_response = asyncio.run(
                    v3_app.set_llm_settings(
                        _Request(
                            {
                                "model": {
                                    "name": "模型 B",
                                    "base_url": "https://b.example/v1",
                                    "api_key": "secret-b",
                                    "model": "model-b",
                                },
                                "set_active": False,
                            }
                        )
                    )
                )
                second = _payload(second_response)
                model_b_id = second["saved_id"]
                activated_response = asyncio.run(
                    v3_app.activate_llm_model(_Request({"id": model_b_id}))
                )
                deleted_response = asyncio.run(
                    v3_app.delete_llm_model(_Request({"id": model_b_id}))
                )

            for response in (
                created_response,
                second_response,
                activated_response,
                deleted_response,
            ):
                body = response.body.decode("utf-8")
                self.assertNotIn("secret-a", body)
                self.assertNotIn("secret-b", body)
                payload = _payload(response)
                for model in payload["models"]:
                    self.assertNotIn("api_key", model)
                    self.assertIn("has_api_key", model)
                    self.assertIn("api_key_masked", model)
            self.assertNotEqual(_payload(deleted_response)["active_id"], model_b_id)
            self.assertTrue(
                any(
                    model["id"] == model_a_id
                    for model in _payload(deleted_response)["models"]
                )
            )

    def test_new_model_requires_key_and_probe_reuses_stored_key_by_id_or_active(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            settings = SettingsService(Path(temporary))
            created = settings.save_model(
                {
                    "name": "probe",
                    "base_url": "https://probe.example/v1",
                    "api_key": "stored-probe-secret",
                    "model": "probe-model",
                },
                set_active=True,
            )
            model_id = created["saved_id"]
            with self.assertRaises(ValueError):
                settings.save_model(
                    {
                        "name": "missing",
                        "base_url": "https://missing.example/v1",
                        "api_key": "",
                        "model": "missing-model",
                    },
                    set_active=False,
                )

            observed: list[dict[str, object]] = []

            def probe(model: dict[str, object]) -> dict[str, object]:
                observed.append(model)
                return {
                    "ok": True,
                    "message": "连接成功",
                    "model": model["model"],
                }

            with (
                mock.patch.object(v3_app, "SETTINGS", settings),
                mock.patch.object(settings, "probe_model", side_effect=probe),
            ):
                by_id = asyncio.run(
                    v3_app.test_llm_settings(
                        _Request(
                            {
                                "model": {
                                    "id": model_id,
                                    "name": "probe",
                                    "base_url": "https://probe.example/v1",
                                    "api_key": "",
                                    "model": "probe-model-edited",
                                },
                                "use_active": False,
                            }
                        )
                    )
                )
                by_active = asyncio.run(
                    v3_app.test_llm_settings(
                        _Request({"use_active": True})
                    )
                )

            self.assertTrue(_payload(by_id)["ok"])
            self.assertTrue(_payload(by_active)["ok"])
            self.assertEqual(observed[0]["api_key"], "stored-probe-secret")
            self.assertEqual(observed[0]["model"], "probe-model-edited")
            self.assertEqual(observed[1]["api_key"], "stored-probe-secret")
            self.assertNotIn(
                "stored-probe-secret",
                by_id.body.decode("utf-8") + by_active.body.decode("utf-8"),
            )

    def test_probe_rejects_non_http_and_credential_bearing_urls(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            settings = SettingsService(Path(temporary))
            model = {
                "name": "probe",
                "api_key": "secret",
                "model": "test",
            }
            for base_url in (
                "file:///tmp/probe",
                "https://user:password@example.com/v1",
            ):
                with self.subTest(base_url=base_url):
                    with self.assertRaises(ValueError):
                        settings.probe_model({**model, "base_url": base_url})

    def test_upload_rejects_invalid_role_or_type_before_registration(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            settings = SettingsService(root)
            with (
                mock.patch.object(v3_app, "RUNS_DIR", runs),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                cases = (
                    ("unknown", "tender.md", "INPUT_ROLE_INVALID"),
                    ("tender", "tender.xlsx", "UPLOAD_TYPE_UNSUPPORTED"),
                    ("template", "template.pdf", "UPLOAD_TYPE_UNSUPPORTED"),
                )
                for role, filename, code in cases:
                    with self.subTest(role=role, filename=filename):
                        upload_file = UploadFile(
                            filename=filename,
                            file=io.BytesIO(b"not registered"),
                        )
                        response = asyncio.run(
                            v3_app.upload("alpha", role, upload_file, "")
                        )
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(_payload(response)["error"]["code"], code)

            self.assertFalse(
                (runs / "alpha" / "workspace" / "v3" / "input_manifest.json").exists()
            )

    def test_upload_limit_uses_bid_agent_source_upload_max_mb(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            settings = SettingsService(root)
            with (
                mock.patch.dict(
                    os.environ,
                    {"BID_AGENT_SOURCE_UPLOAD_MAX_MB": "1"},
                    clear=False,
                ),
                mock.patch.object(v3_app, "RUNS_DIR", runs),
                mock.patch.object(v3_app, "SETTINGS", settings),
            ):
                too_large = UploadFile(
                    filename="tender.pdf",
                    file=io.BytesIO(b"x" * (1024 * 1024 + 1)),
                )
                rejected = asyncio.run(
                    v3_app.upload("alpha", "tender", too_large, "")
                )
                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(
                    _payload(rejected)["error"]["code"],
                    "UPLOAD_INVALID",
                )
                self.assertIn("1 MB", _payload(rejected)["message"])

                accepted_file = UploadFile(
                    filename="tender.md",
                    file=io.BytesIO("项目目标".encode("utf-8")),
                )
                accepted = asyncio.run(
                    v3_app.upload("alpha", "tender", accepted_file, "")
                )
                self.assertEqual(accepted.status_code, 201)
                self.assertEqual(_payload(accepted)["input"]["role"], "tender")


if __name__ == "__main__":
    unittest.main()
