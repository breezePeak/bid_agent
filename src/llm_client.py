from __future__ import annotations

import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import certifi

from config import get_settings
from runtime_context import record_llm_call
from utils import project_root, strip_code_fences


def _create_ssl_context(verify_ssl: bool = True) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context(cafile=certifi.where())
    print("[LLM] 警告: OPENAI_VERIFY_SSL=false，当前请求不会校验 TLS 证书。", file=sys.stderr)
    return ssl._create_unverified_context()


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


def _extract_stream_delta(event_data: dict[str, Any]) -> str:
    choices = event_data.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if content is None:
        message = choice.get("message") or {}
        content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return "" if content is None else str(content)


def _read_streaming_response(response: Any) -> str:
    parts: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            event_data = json.loads(data)
        except json.JSONDecodeError:
            continue
        parts.append(_extract_stream_delta(event_data))
    return "".join(parts)


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


def _build_connection_error_hint(endpoint: str, error: Exception) -> str:
    text = str(error).lower()
    if "remote end closed connection without response" in text or "remotedisconnected" in text:
        return (
            "远端在返回响应前主动断开连接，通常是上游网关/模型服务临时不稳定、限流或 endpoint 不匹配。"
            "如多次出现，请增大 OPENAI_MAX_RETRIES，或检查 OPENAI_BASE_URL 是否应切换为服务商提供的非 go endpoint。"
        )
    if "timed out" in text or "timeout" in text:
        return "请求超时。可适当增大 OPENAI_TIMEOUT，或降低并发/稍后重试。"
    if "/zen/go/" in endpoint:
        return "当前使用 OpenCode Go endpoint。如持续连接失败，请确认该 endpoint、模型和 API Key 组合仍可用。"
    return ""


def _retry_delay(attempt: int, initial_delay: float, max_delay: float) -> float:
    base_delay = min(initial_delay * (2 ** (attempt - 1)), max_delay)
    jitter = random.uniform(0, min(1.0, base_delay * 0.25))
    return base_delay + jitter


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    settings = get_settings(project_root())
    endpoint = _chat_endpoint(settings.base_url)
    ssl_context = _create_ssl_context(settings.verify_ssl)
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    if settings.stream:
        payload["stream"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
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
                if settings.stream:
                    content = _read_streaming_response(response)
                    if not content.strip():
                        raise ValueError("LLM 流式响应为空。")
                    cleaned = strip_code_fences(content)
                    record_llm_call(messages, cleaned, settings.model, temperature)
                    return cleaned
                response_body = response.read().decode("utf-8")
                response_data = json.loads(response_body)
                cleaned = strip_code_fences(_extract_content(response_data))
                record_llm_call(messages, cleaned, settings.model, temperature)
                return cleaned
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
            hint = _build_connection_error_hint(endpoint, exc)
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"endpoint={endpoint} model={settings.model} {exc}",
                file=sys.stderr,
            )
            if hint:
                print(f"[LLM] 提示: {hint}", file=sys.stderr)

        if attempt < settings.max_retries:
            delay = _retry_delay(attempt, settings.retry_initial_delay, settings.retry_max_delay)
            print(f"[LLM] {delay:.1f} 秒后重试...", file=sys.stderr)
            time.sleep(delay)

    raise RuntimeError(
        f"LLM 请求失败，已重试 {settings.max_retries} 次。"
        "请检查 OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY 是否匹配，"
        "或临时增大 OPENAI_MAX_RETRIES 后重试。"
    ) from last_error
