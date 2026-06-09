from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from utils import project_root


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    max_retries: int = 3


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f".env 第 {line_number} 行格式错误，应为 KEY=VALUE。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def get_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    env_path = root / ".env"
    file_values = _parse_env_file(env_path)
    values = {**file_values, **os.environ}

    required_keys = ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]
    missing = [key for key in required_keys if not str(values.get(key, "")).strip()]
    if missing:
        missing_text = ", ".join(missing)
        raise ConfigError(
            f"缺少必要配置: {missing_text}。请在 {env_path} 中配置，"
            "可参考 .env.example，且不要把 API Key 写入代码。"
        )

    timeout = int(values.get("OPENAI_TIMEOUT", 120))
    max_retries = int(values.get("OPENAI_MAX_RETRIES", 3))

    return Settings(
        base_url=str(values["OPENAI_BASE_URL"]).strip().rstrip("/"),
        api_key=str(values["OPENAI_API_KEY"]).strip(),
        model=str(values["OPENAI_MODEL"]).strip(),
        timeout=timeout,
        max_retries=max_retries,
    )
