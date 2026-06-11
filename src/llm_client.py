from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import certifi

from config import get_settings
from utils import project_root, strip_code_fences


def _chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _extract_content(response_data: dict[str, Any]) -> str:
    choices = response_data.get("choices") or []
    if not choices:
        raise ValueError(f"LLM 响应缺少 choices: {response_data}")

    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _build_http_error_hint(endpoint: str, status_code: int, error_body: str) -> str:
    body = (error_body or "").lower()
    if status_code == 403 and "error code: 1010" in body and "/zen/go/" in endpoint:
        return (
            "OpenCode Go 访问被拒绝。请检查：1) 当前 API Key 是否来自 OpenCode Go；"
            "2) 当前工作空间是否已有订阅成员；3) Go 额度是否已用完且未开启 Use balance；"
            "4) 如无需 Go，可改用 https://opencode.ai/zen/v1/chat/completions 和可用的 Zen 模型。"
        )
    if status_code == 403:
        return "服务端拒绝了当前 key / endpoint / model 组合，请检查模型权限和账号额度。"
    if status_code == 401:
        return "API Key 无效或未授权。"
    if status_code == 400:
        return "请求参数不合法，请检查 endpoint、model 和请求体。"
    return ""


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    settings = get_settings(project_root())
    endpoint = _chat_endpoint(settings.base_url)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # OpenCode's edge can flag Python's default urllib fingerprint on Windows.
        # A standard browser-like UA avoids Cloudflare 1010 false positives.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
    }

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout, context=ssl_context) as response:
                response_body = response.read().decode("utf-8")
            response_data = json.loads(response_body)
            return strip_code_fences(_extract_content(response_data))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            last_error = exc
            should_retry = exc.code not in {400, 401, 403}
            hint = _build_http_error_hint(endpoint, exc.code, error_body)
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"HTTP {exc.code} endpoint={endpoint} model={settings.model} {error_body}",
                file=sys.stderr,
            )
            if hint:
                print(f"[LLM] 提示: {hint}", file=sys.stderr)
            if not should_retry:
                break
        except Exception as exc:
            last_error = exc
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"endpoint={endpoint} model={settings.model} {exc}",
                file=sys.stderr,
            )

        if attempt < settings.max_retries:
            time.sleep(min(2 ** (attempt - 1), 8))

    raise RuntimeError(f"LLM 请求失败，已重试 {settings.max_retries} 次。") from last_error
