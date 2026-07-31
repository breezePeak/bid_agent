from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from config import _parse_env_file


LLM_ENV_KEYS: tuple[tuple[str, str], ...] = (
    ("OPENAI_BASE_URL", "base_url"),
    ("OPENAI_API_KEY", "api_key"),
    ("OPENAI_MODEL", "model"),
    ("OPENAI_TIMEOUT", "timeout"),
    ("OPENAI_MAX_RETRIES", "max_retries"),
    ("OPENAI_RETRY_INITIAL_DELAY", "retry_initial_delay"),
    ("OPENAI_RETRY_MAX_DELAY", "retry_max_delay"),
    ("OPENAI_STREAM", "stream"),
    ("OPENAI_VERIFY_SSL", "verify_ssl"),
    ("OPENAI_PROVIDER", "provider"),
)
_LLM_ALIAS_TO_KEY = {alias: key for key, alias in LLM_ENV_KEYS}
_LLM_KEY_TO_ALIAS = {key: alias for key, alias in LLM_ENV_KEYS}

FLOW_SETTING_SPECS: dict[str, tuple[str, int, int, int]] = {
    "workers": ("BID_AGENT_WORKERS_DEFAULT", 4, 1, 10),
    "llm_concurrency": ("BID_AGENT_LLM_CONCURRENCY", 8, 1, 32),
    "write_batch_retries": ("BID_AGENT_WRITE_BATCH_RETRIES", 5, 0, 20),
    "max_repair_rounds": ("AGENT_MAX_REPAIR_ROUNDS", 2, 0, 10),
}
FLOW_REVIEW_SPECS: dict[str, tuple[str, bool]] = {
    "chapter_review_enabled": ("BID_AGENT_CHAPTER_REVIEW_ENABLED", True),
    "chapter_review_gate": ("CHAPTER_REVIEW_GATE", True),
    "global_review_gate": ("GLOBAL_REVIEW_GATE", True),
    "allow_accept_risk": ("ISSUE_ACCEPT_RISK_ENABLED", False),
    "anti_fabrication_gate": ("BID_AGENT_ANTI_FABRICATION_GATE", True),
    "write_failure_fallback": ("BID_AGENT_WRITE_FAILURE_FALLBACK", True),
    "validation_failure_blocks_pipeline": (
        "BID_AGENT_VALIDATION_FAILURE_BLOCKS_PIPELINE",
        False,
    ),
    # Phase 5: chapter body H2 confirmation. Independent of chapter_review_enabled.
    "confirmation_required": ("BID_AGENT_CHAPTER_CONFIRMATION_REQUIRED", True),
}
FLOW_CHOICE_SPECS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "research_provider": (
        "BID_AGENT_RESEARCH_PROVIDER",
        "doubao_web",
        ("doubao_web", "deepseek_web", "disabled"),
    ),
}
RUNTIME_ENV_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(key for key, _alias in LLM_ENV_KEYS),
            *(key for key, _default, _low, _high in FLOW_SETTING_SPECS.values()),
            *(key for key, _default in FLOW_REVIEW_SPECS.values()),
            *(key for key, _default, _choices in FLOW_CHOICE_SPECS.values()),
        ]
    )
)

_SETTINGS_LOCK = threading.RLock()


def _invalidate_inference_runtime_metadata() -> None:
    """Force H1 to resolve the newly active process-wide model policy."""

    from document_pipeline.inference_runtime import (
        INFERENCE_RUNTIME_REGISTRY,
    )

    INFERENCE_RUNTIME_REGISTRY.clear()


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class SettingsService:
    """Own the small, allow-listed set of process-wide Web settings.

    Secrets remain in the ignored ``.env``/``models.json`` files.  Callers get
    structured results only; this service never logs credentials or includes
    authentication passwords in an API payload.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.env_path = self.root / ".env"
        self.models_path = self.root / "models.json"

    def _file_values(self) -> dict[str, str]:
        return _parse_env_file(self.env_path)

    def _value(self, key: str, default: str = "") -> str:
        if key in os.environ:
            return str(os.environ[key])
        return str(self._file_values().get(key, default))

    def auth_credentials(self) -> tuple[str, str]:
        return (
            self._value("BID_AGENT_AUTH_USER", "admin"),
            self._value("BID_AGENT_AUTH_PASSWORD", "123456"),
        )

    def auth_secure_cookie(self) -> bool:
        return _parse_bool(self._value("BID_AGENT_AUTH_SECURE_COOKIE", "0"), False)

    def source_upload_max_bytes(self) -> int:
        limit_mb = _to_int(self._value("BID_AGENT_SOURCE_UPLOAD_MAX_MB", "512"), 512)
        return max(1, min(limit_mb, 2048)) * 1024 * 1024

    def config_revision(self) -> str:
        if not self.env_path.exists():
            return ""
        stat = self.env_path.stat()
        return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"

    @staticmethod
    def normalize_model(raw: dict[str, Any]) -> dict[str, Any]:
        provider = str(raw.get("provider") or "openai").strip().lower()
        if provider == "claude":
            provider = "anthropic"
        if provider not in {"openai", "anthropic"}:
            provider = "openai"
        return {
            "id": str(raw.get("id", "")).strip(),
            "name": str(raw.get("name", "")).strip(),
            "provider": provider,
            "base_url": str(raw.get("base_url", "")).strip(),
            "api_key": str(raw.get("api_key", "")).strip(),
            "model": str(raw.get("model", "")).strip(),
            "timeout": max(5, min(_to_int(raw.get("timeout"), 300), 1800)),
            "max_retries": max(1, min(_to_int(raw.get("max_retries"), 3), 20)),
            "retry_initial_delay": max(0.1, _to_float(raw.get("retry_initial_delay"), 2)),
            "retry_max_delay": max(0.1, _to_float(raw.get("retry_max_delay"), 30)),
            "stream": _parse_bool(raw.get("stream"), False),
            "verify_ssl": _parse_bool(raw.get("verify_ssl"), True),
        }

    @staticmethod
    def public_model(model: dict[str, Any]) -> dict[str, Any]:
        """Return a model safe for HTTP responses.

        The API only tells the settings UI whether a credential exists.  It
        never sends the persisted key (including a partially revealed suffix)
        back to the browser.
        """
        public = {
            key: value
            for key, value in model.items()
            if key not in {"api_key", "has_api_key", "api_key_masked"}
        }
        has_api_key = bool(str(model.get("api_key") or "").strip())
        public["has_api_key"] = has_api_key
        public["api_key_masked"] = "••••••••" if has_api_key else ""
        return public

    @classmethod
    def public_result(cls, result: dict[str, Any]) -> dict[str, Any]:
        """Redact every model embedded in a settings endpoint result."""
        public = dict(result)
        models = public.get("models")
        if isinstance(models, list):
            public["models"] = [
                cls.public_model(model)
                for model in models
                if isinstance(model, dict)
            ]
        return public

    def _read_llm_env_values(self) -> dict[str, str]:
        file_values = self._file_values()
        return {
            alias: str(file_values[key] if key in file_values else os.environ.get(key, ""))
            for key, alias in LLM_ENV_KEYS
        }

    def read_models_store(self) -> dict[str, Any]:
        with _SETTINGS_LOCK:
            if self.models_path.exists():
                try:
                    data = json.loads(self.models_path.read_text(encoding="utf-8"))
                    models = data.get("models") if isinstance(data, dict) else None
                    if isinstance(models, list) and all(isinstance(item, dict) for item in models):
                        return {
                            "models": [self.normalize_model(item) for item in models],
                            "active_id": str(data.get("active_id") or ""),
                        }
                except (OSError, UnicodeError, json.JSONDecodeError):
                    pass

            env_values = self._read_llm_env_values()
            default_model = self.normalize_model(
                {
                    "id": "default",
                    "name": "默认模型",
                    **env_values,
                }
            )
            configured = bool(
                default_model["base_url"]
                and default_model["api_key"]
                and default_model["model"]
            )
            store = {
                "models": [default_model],
                "active_id": default_model["id"] if configured else "",
            }
            self._write_models_store_locked(store)
            return store

    def _write_models_store_locked(self, store: dict[str, Any]) -> None:
        self.models_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.models_path.with_name(
            f"{self.models_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(store, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.models_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_llm_env_locked(self, settings: dict[str, Any]) -> None:
        existing_lines = (
            self.env_path.read_text(encoding="utf-8").splitlines()
            if self.env_path.exists()
            else []
        )
        known_keys = {key for key, _alias in LLM_ENV_KEYS}
        updated_keys: set[str] = set()
        rendered: list[str] = []
        for raw_line in existing_lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                rendered.append(raw_line)
                continue
            key = stripped.split("=", 1)[0].strip()
            alias = _LLM_KEY_TO_ALIAS.get(key)
            if key in known_keys and alias in settings and settings[alias] is not None:
                rendered.append(f"{key}={settings[alias]}")
                updated_keys.add(key)
            else:
                rendered.append(raw_line)
        for alias, value in settings.items():
            key = _LLM_ALIAS_TO_KEY.get(alias)
            if key and key not in updated_keys and value is not None:
                rendered.append(f"{key}={value}")
                updated_keys.add(key)

        self._replace_env_locked(rendered)
        for alias, value in settings.items():
            key = _LLM_ALIAS_TO_KEY.get(alias)
            if key and value is not None:
                os.environ[key] = str(value)

    def _replace_env_locked(self, lines: list[str]) -> None:
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.env_path.with_name(
            f"{self.env_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temporary.replace(self.env_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _sync_model_to_env_locked(self, model: dict[str, Any]) -> None:
        self._write_llm_env_locked(
            {
                "base_url": model.get("base_url", ""),
                "api_key": model.get("api_key", ""),
                "model": model.get("model", ""),
                "provider": model.get("provider", "openai") or "openai",
                "timeout": model.get("timeout", 300),
                "max_retries": model.get("max_retries", 3),
                "retry_initial_delay": model.get("retry_initial_delay", 2),
                "retry_max_delay": model.get("retry_max_delay", 30),
                "stream": "true" if model.get("stream") else "false",
                "verify_ssl": "true" if model.get("verify_ssl") else "false",
            }
        )

    @staticmethod
    def _validate_model(model: dict[str, Any]) -> None:
        if not model["name"]:
            raise ValueError("模型别名（name）不能为空。")
        if not model["base_url"] or not model["api_key"] or not model["model"]:
            raise ValueError("Base URL、API Key、模型均为必填项。")

    def save_model(
        self,
        raw_model: dict[str, Any],
        *,
        set_active: bool,
    ) -> dict[str, Any]:
        model = self.normalize_model(raw_model)
        with _SETTINGS_LOCK:
            store = self.read_models_store()
            models = list(store.get("models", []))
            model_id = model["id"]
            if model_id:
                index = next(
                    (
                        i
                        for i, item in enumerate(models)
                        if str(item.get("id") or "") == model_id
                    ),
                    -1,
                )
                if index < 0:
                    raise LookupError("未找到要更新的模型。")
                if not model["api_key"]:
                    model["api_key"] = str(models[index].get("api_key") or "")
                models[index] = {**models[index], **model}
            else:
                model_id = uuid.uuid4().hex[:12]
                model["id"] = model_id
                models.append(model)
            self._validate_model(model)

            active_id = str(store.get("active_id") or "")
            applied_live = set_active or not active_id or active_id == model_id
            store = {
                "models": models,
                "active_id": model_id if set_active or not active_id else active_id,
            }
            if applied_live:
                selected = next(item for item in models if item["id"] == model_id)
                self._sync_model_to_env_locked(selected)
                _invalidate_inference_runtime_metadata()
            self._write_models_store_locked(store)
            return {
                **store,
                "saved_id": model_id,
                "applied_live": applied_live,
                "config_revision": self.config_revision(),
            }

    def activate_model(self, model_id: str) -> dict[str, Any]:
        target_id = str(model_id or "").strip()
        with _SETTINGS_LOCK:
            store = self.read_models_store()
            models = list(store.get("models", []))
            target = next(
                (item for item in models if str(item.get("id") or "") == target_id),
                None,
            )
            if target is None:
                raise LookupError("未找到该模型。")
            store = {"models": models, "active_id": target_id}
            self._sync_model_to_env_locked(target)
            _invalidate_inference_runtime_metadata()
            self._write_models_store_locked(store)
            return {
                **store,
                "applied_live": True,
                "config_revision": self.config_revision(),
            }

    def delete_model(self, model_id: str) -> dict[str, Any]:
        target_id = str(model_id or "").strip()
        with _SETTINGS_LOCK:
            store = self.read_models_store()
            models = list(store.get("models", []))
            remaining = [
                item for item in models if str(item.get("id") or "") != target_id
            ]
            if len(remaining) == len(models):
                raise LookupError("未找到该模型。")
            active_id = str(store.get("active_id") or "")
            if active_id == target_id:
                active_id = str(remaining[0].get("id") or "") if remaining else ""
                if remaining:
                    self._sync_model_to_env_locked(remaining[0])
                _invalidate_inference_runtime_metadata()
            next_store = {"models": remaining, "active_id": active_id}
            self._write_models_store_locked(next_store)
            return {
                **next_store,
                "applied_live": bool(active_id),
                "config_revision": self.config_revision(),
            }

    def active_model(self) -> dict[str, Any] | None:
        store = self.read_models_store()
        models = list(store.get("models", []))
        active_id = str(store.get("active_id") or "")
        active = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and str(item.get("id") or "") == active_id
            ),
            None,
        )
        if active is None and models:
            active = models[0]
        return dict(active) if isinstance(active, dict) else None

    def resolve_probe_model(
        self,
        raw_model: dict[str, Any] | None,
        *,
        use_active: bool,
    ) -> dict[str, Any] | None:
        """Resolve a probe request without requiring the browser to hold secrets.

        A persisted model is selected by the supplied model id, or by the active
        id when requested.  Non-secret form edits may overlay that stored model;
        an empty API key always means "reuse the stored credential".
        """
        candidate = dict(raw_model) if isinstance(raw_model, dict) else {}
        store = self.read_models_store()
        models = [
            item
            for item in store.get("models", [])
            if isinstance(item, dict)
        ]
        requested_id = str(candidate.get("id") or "").strip()
        active_id = str(store.get("active_id") or "").strip()
        lookup_id = requested_id or (active_id if use_active else "")
        stored = next(
            (
                item
                for item in models
                if str(item.get("id") or "").strip() == lookup_id
            ),
            None,
        )
        if stored is None and use_active and active_id:
            stored = next(
                (
                    item
                    for item in models
                    if str(item.get("id") or "").strip() == active_id
                ),
                None,
            )
        if not candidate and stored is None:
            return None

        merged = {**(stored or {}), **candidate}
        if stored is not None and not str(candidate.get("api_key") or "").strip():
            merged["api_key"] = stored.get("api_key", "")
        return self.normalize_model(merged)

    def apply_runtime_settings(self) -> None:
        """Make persisted Web settings authoritative for this server process."""
        with _SETTINGS_LOCK:
            active = self.active_model()
            if active:
                runtime_values = {
                    "base_url": active.get("base_url", ""),
                    "api_key": active.get("api_key", ""),
                    "model": active.get("model", ""),
                    "provider": active.get("provider", "openai"),
                    "timeout": active.get("timeout", 300),
                    "max_retries": active.get("max_retries", 3),
                    "retry_initial_delay": active.get("retry_initial_delay", 2),
                    "retry_max_delay": active.get("retry_max_delay", 30),
                    "stream": "true" if active.get("stream") else "false",
                    "verify_ssl": "true" if active.get("verify_ssl") else "false",
                }
                for alias, value in runtime_values.items():
                    key = _LLM_ALIAS_TO_KEY[alias]
                    os.environ[key] = str(value)
                _invalidate_inference_runtime_metadata()
            flow = self.flow_settings()
            os.environ.update(
                {
                    key: str(flow[alias])
                    for alias, (key, _default, _low, _high) in FLOW_SETTING_SPECS.items()
                }
            )
            os.environ.update(
                {
                    key: str(flow[alias])
                    for alias, (key, _default, _choices) in FLOW_CHOICE_SPECS.items()
                }
            )
            os.environ.update(
                {
                    key: "1" if flow[alias] else "0"
                    for alias, (key, _default) in FLOW_REVIEW_SPECS.items()
                }
            )

    def capture_runtime_environment(
        self,
        *extra_keys: str,
    ) -> dict[str, str | None]:
        """Capture only environment values this service may mutate.

        The V3 application uses this snapshot around its lifespan so embedded
        servers and ``TestClient`` instances cannot leak persisted Web settings
        into the parent process after shutdown.
        """
        keys = tuple(dict.fromkeys((*RUNTIME_ENV_KEYS, *extra_keys)))
        with _SETTINGS_LOCK:
            return {key: os.environ.get(key) for key in keys}

    @staticmethod
    def restore_runtime_environment(snapshot: dict[str, str | None]) -> None:
        """Restore a snapshot produced by :meth:`capture_runtime_environment`."""
        with _SETTINGS_LOCK:
            for key, value in snapshot.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def flow_settings(self) -> dict[str, Any]:
        file_values = self._file_values()
        result: dict[str, Any] = {}
        for alias, (key, default, low, high) in FLOW_SETTING_SPECS.items():
            raw = file_values[key] if key in file_values else os.environ.get(key, default)
            result[alias] = max(low, min(high, _to_int(raw, default)))
        for alias, (key, default) in FLOW_REVIEW_SPECS.items():
            raw = (
                file_values[key]
                if key in file_values
                else os.environ.get(key, "1" if default else "0")
            )
            result[alias] = _parse_bool(raw, default)
        for alias, (key, default, choices) in FLOW_CHOICE_SPECS.items():
            raw = str(file_values[key] if key in file_values else os.environ.get(key, default)).strip().lower()
            result[alias] = raw if raw in choices else default
        if not result["chapter_review_enabled"]:
            result["chapter_review_gate"] = False
            result["global_review_gate"] = False
            result["anti_fabrication_gate"] = False
        return result

    def write_flow_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with _SETTINGS_LOCK:
            current = self.flow_settings()
            for alias, (_key, _default, low, high) in FLOW_SETTING_SPECS.items():
                if alias not in updates:
                    continue
                try:
                    value = int(updates[alias])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{alias} 必须是 {low}-{high} 的整数"
                    ) from exc
                current[alias] = max(low, min(high, value))
            for alias, (_key, default) in FLOW_REVIEW_SPECS.items():
                if alias in updates:
                    current[alias] = _parse_bool(updates[alias], default)
            for alias, (_key, default, choices) in FLOW_CHOICE_SPECS.items():
                if alias in updates:
                    candidate = str(updates[alias] or "").strip().lower()
                    if candidate not in choices:
                        raise ValueError(f"{alias} 必须是: {', '.join(choices)}")
                    current[alias] = candidate
            if not current["chapter_review_enabled"]:
                current["chapter_review_gate"] = False
                current["global_review_gate"] = False
                current["anti_fabrication_gate"] = False

            env_updates = {
                key: str(current[alias])
                for alias, (key, _default, _low, _high) in FLOW_SETTING_SPECS.items()
            }
            env_updates.update(
                {
                    key: "1" if current[alias] else "0"
                    for alias, (key, _default) in FLOW_REVIEW_SPECS.items()
                }
            )
            env_updates.update(
                {
                    key: str(current[alias])
                    for alias, (key, _default, _choices) in FLOW_CHOICE_SPECS.items()
                }
            )
            existing_lines = (
                self.env_path.read_text(encoding="utf-8").splitlines()
                if self.env_path.exists()
                else []
            )
            seen: set[str] = set()
            rendered: list[str] = []
            for line in existing_lines:
                key = line.strip().split("=", 1)[0].strip() if "=" in line else ""
                if key in env_updates:
                    rendered.append(f"{key}={env_updates[key]}")
                    seen.add(key)
                else:
                    rendered.append(line)
            rendered.extend(
                f"{key}={value}"
                for key, value in env_updates.items()
                if key not in seen
            )
            self._replace_env_locked(rendered)
            os.environ.update(env_updates)
            return current

    # Keep in sync with llm_client so mid-tier proxies behind Cloudflare do not
    # reject the settings probe with browser-signature bans (e.g. CF 1010).
    _PROBE_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )

    def probe_model(self, raw_model: dict[str, Any]) -> dict[str, Any]:
        model = self.normalize_model(raw_model)
        self._validate_model(model)
        base_url = str(model["base_url"]).rstrip("/")
        model_id = str(model["model"])
        provider = str(model["provider"])
        timeout = max(5, min(int(model["timeout"]), 90))
        parsed_base = urllib.parse.urlparse(base_url)
        if (
            parsed_base.scheme not in {"http", "https"}
            or not parsed_base.hostname
            or parsed_base.username
            or parsed_base.password
        ):
            raise ValueError(
                "Base URL 必须是无用户名和密码的有效 http(s) 地址。"
            )

        common_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": self._PROBE_USER_AGENT,
        }
        if provider == "anthropic":
            if base_url.endswith("/messages"):
                endpoint = base_url
            elif base_url.endswith("/v1"):
                endpoint = f"{base_url}/messages"
            else:
                endpoint = f"{base_url}/v1/messages"
            payload = {
                "model": model_id,
                "max_tokens": 64,
                "temperature": 0,
                "messages": [{"role": "user", "content": "hello"}],
                "system": "You are a connectivity probe. Reply briefly.",
            }
            headers = {
                **common_headers,
                "x-api-key": str(model["api_key"]),
                "anthropic-version": "2023-06-01",
            }
            # Some OpenAI-compatible gateways still want bearer for anthropic-shaped routes.
            api_key = str(model["api_key"] or "")
            if api_key and not api_key.startswith("sk-ant"):
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            endpoint = (
                base_url
                if base_url.endswith("/chat/completions")
                else f"{base_url}/chat/completions"
            )
            payload = {
                "model": model_id,
                "temperature": 0,
                "max_tokens": 64,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a connectivity probe. Reply briefly.",
                    },
                    {"role": "user", "content": "hello"},
                ],
            }
            headers = {
                **common_headers,
                "Authorization": f"Bearer {model['api_key']}",
            }

        context = (
            ssl.create_default_context()
            if model["verify_ssl"]
            else ssl._create_unverified_context()
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=context,
            ) as response:
                raw_bytes = response.read(1_048_577)
                status_code = int(getattr(response, "status", 200))
            if len(raw_bytes) > 1_048_576:
                return {
                    "ok": False,
                    "message": "模型测试响应超过 1 MB，已停止读取。",
                    "model": model_id,
                    "provider": provider,
                    "base_url": base_url,
                }
            raw = raw_bytes.decode("utf-8", errors="replace")
            elapsed_ms = int((time.monotonic() - started) * 1000)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "message": f"HTTP {status_code} 但响应不是 JSON。",
                    "model": model_id,
                    "provider": provider,
                    "base_url": base_url,
                    "elapsed_ms": elapsed_ms,
                }
            text = self._probe_response_text(parsed, provider)
            if not text:
                return {
                    "ok": False,
                    "message": f"HTTP 成功但模型返回空内容（status={status_code}）。",
                    "model": model_id,
                    "provider": provider,
                    "base_url": base_url,
                    "elapsed_ms": elapsed_ms,
                }
            return {
                "ok": True,
                "message": "连接成功",
                "reply": text if len(text) <= 300 else text[:300] + "…",
                "model": model_id,
                "provider": provider,
                "name": model.get("name") or "",
                "base_url": base_url,
                "elapsed_ms": elapsed_ms,
            }
        except urllib.error.HTTPError as exc:
            detail = self._probe_http_error_detail(exc)
            return {
                "ok": False,
                "message": detail,
                "model": model_id,
                "provider": provider,
                "base_url": base_url,
            }
        except TimeoutError:
            return {
                "ok": False,
                "message": f"连接超时（{timeout}s）。请检查 Base URL、网络或增大超时。",
                "model": model_id,
                "provider": provider,
                "base_url": base_url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"连接失败: {type(exc).__name__}: {exc}",
                "model": model_id,
                "provider": provider,
                "base_url": base_url,
            }

    @classmethod
    def _probe_http_error_detail(cls, exc: urllib.error.HTTPError) -> str:
        body = ""
        try:
            body = exc.read(4_096).decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        snippet = body[:280].replace("\n", " ") if body else ""
        code = int(getattr(exc, "code", 0) or 0)
        reason = str(getattr(exc, "reason", "") or "")
        lower = snippet.lower()
        if code == 401:
            hint = "API Key 无效或未授权。"
        elif code == 403 and ("1010" in snippet or "cloudflare" in lower):
            hint = (
                "网关/Cloudflare 拒绝了探测请求（常见 error 1010）。"
                "已使用与正式调用相同的浏览器 UA；仍失败时请检查 Key、IP 白名单或中转站风控。"
            )
        elif code == 403:
            hint = "无权限访问该模型，请检查 API Key 与模型 ID。"
        elif code == 404:
            hint = "接口路径或模型 ID 不存在，请核对 Base URL（通常以 /v1 结尾）与模型名。"
        elif code == 429:
            hint = "请求过于频繁或额度不足。"
        elif code >= 500:
            hint = "上游服务异常，请稍后重试或更换中转。"
        else:
            hint = "请检查 Base URL、模型 ID 与 API Key。"
        if snippet:
            return f"连接失败: HTTP {code} {reason}。{hint} 上游返回: {snippet}"
        return f"连接失败: HTTP {code} {reason}。{hint}"

    @staticmethod
    def _probe_response_text(parsed: Any, provider: str) -> str:
        if not isinstance(parsed, dict):
            return ""
        if provider == "anthropic":
            content = parsed.get("content")
            if isinstance(content, list):
                return "".join(
                    str(item.get("text") or "")
                    if isinstance(item, dict)
                    else str(item)
                    for item in content
                ).strip()
            return str(content or "").strip()

        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        return str(content).strip()
