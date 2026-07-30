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

from concurrency import llm_slot, note_rate_limit_429
from config import Settings, get_settings
from runtime_context import record_llm_call
from utils import project_root, strip_code_fences


def _create_ssl_context(verify_ssl: bool = True) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context(cafile=certifi.where())
    print("[LLM] 警告: OPENAI_VERIFY_SSL=false，当前请求不会校验 TLS 证书。", file=sys.stderr)
    return ssl._create_unverified_context()


def _normalize_provider(value: str | None) -> str:
    p = str(value or "openai").strip().lower()
    if p in {"anthropic", "claude"}:
        return "anthropic"
    return "openai"


def _openai_chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _anthropic_messages_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _split_system_messages(messages: list[dict]) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    converted: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or part.get("content") or "") if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = str(content or "")
        if role == "system":
            if text.strip():
                system_parts.append(text)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        converted.append({"role": role, "content": text})
    return "\n\n".join(system_parts).strip(), converted


def _extract_openai_reasoning(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    for key in ("reasoning_content", "reasoning", "thinking", "thought"):
        value = message.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            text = "".join(parts).strip()
        else:
            text = str(value).strip()
        if text:
            return text
    return ""


def _extract_openai_content(response_data: dict[str, Any]) -> str:
    content, _ = _extract_openai_message(response_data)
    return content


def _extract_openai_message(response_data: dict[str, Any]) -> tuple[str, str]:
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
        content_text = "".join(parts)
    else:
        content_text = str(content) if content is not None else ""
    return content_text, _extract_openai_reasoning(message)


def _extract_anthropic_content(response_data: dict[str, Any]) -> str:
    content = response_data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {None, "text"} or "text" in item:
                    parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        text = "".join(parts).strip()
        if text:
            return text
    raise ValueError(f"Anthropic 响应缺少 content: {response_data}")


def _extract_stream_delta(event_data: dict[str, Any]) -> tuple[str, str]:
    choices = event_data.get("choices") or []
    if not choices:
        return "", ""
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    content = delta.get("content")
    if content is None:
        content = message.get("content")
    reasoning = (
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or delta.get("thinking")
        or _extract_openai_reasoning(message)
        or ""
    )
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return str(reasoning) if reasoning else "", "".join(parts)
    return str(reasoning) if reasoning else "", "" if content is None else str(content)


def _read_openai_streaming_response(response: Any) -> tuple[str, str, str]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = ""
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
        reasoning, content = _extract_stream_delta(event_data)
        choices = event_data.get("choices") or []
        if choices and choices[0].get("finish_reason"):
            finish_reason = str(choices[0]["finish_reason"])
        if reasoning:
            reasoning_parts.append(reasoning)
        if content:
            content_parts.append(content)
    return "".join(content_parts), "".join(reasoning_parts), finish_reason


def _build_http_error_hint(endpoint: str, status_code: int, error_body: str, provider: str) -> str:
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
        if provider == "anthropic":
            return "Anthropic 请求参数不合法，请检查 Base URL（通常 …/v1）、model 与 API Key。"
        return "请求参数不合法，请检查 endpoint、model 和请求体。"
    if status_code == 404 and provider == "anthropic":
        return "Anthropic endpoint 可能不正确，请使用 https://api.anthropic.com 或带 /v1 的网关地址。"
    return ""


def _build_connection_error_hint(endpoint: str, error: Exception, provider: str) -> str:
    text = str(error).lower()
    if "remote end closed connection without response" in text or "remotedisconnected" in text:
        return (
            "远端在返回响应前主动断开连接，通常是上游网关/模型服务临时不稳定、限流或 endpoint 不匹配。"
            "如多次出现，请增大 OPENAI_MAX_RETRIES，或检查 Base URL / API 格式是否匹配。"
        )
    if "timed out" in text or "timeout" in text:
        return "请求超时。可适当增大超时时间，或降低并发/稍后重试。"
    if provider == "anthropic":
        return "Anthropic 连接失败，请确认 API 格式选 Anthropic，且 Base URL 与 Key 匹配。"
    if "/zen/go/" in endpoint:
        return "当前使用 OpenCode Go endpoint。如持续连接失败，请确认该 endpoint、模型和 API Key 组合仍可用。"
    return ""


def _retry_delay(attempt: int, initial_delay: float, max_delay: float) -> float:
    base_delay = min(initial_delay * (2 ** (attempt - 1)), max_delay)
    jitter = random.uniform(0, min(1.0, base_delay * 0.25))
    return base_delay + jitter


def _openai_request(settings: Settings, messages: list[dict], temperature: float) -> tuple[str, str, str]:
    endpoint = _openai_chat_endpoint(settings.base_url)
    ssl_context = _create_ssl_context(settings.verify_ssl)
    payload: dict[str, Any] = {
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=settings.timeout, context=ssl_context) as response:
        if settings.stream:
            content, reasoning, finish_reason = _read_openai_streaming_response(response)
            if not content.strip():
                raise ValueError("LLM 流式响应为空。")
            return (
                strip_code_fences(content),
                str(reasoning or "").strip(),
                finish_reason,
            )
        response_body = response.read().decode("utf-8")
        response_data = json.loads(response_body)
        content, reasoning = _extract_openai_message(response_data)
        choices = response_data.get("choices") or []
        finish_reason = (
            str(choices[0].get("finish_reason") or "") if choices else ""
        )
        return (
            strip_code_fences(content),
            str(reasoning or "").strip(),
            finish_reason,
        )


def _anthropic_request(settings: Settings, messages: list[dict], temperature: float) -> tuple[str, str, str]:
    endpoint = _anthropic_messages_endpoint(settings.base_url)
    ssl_context = _create_ssl_context(settings.verify_ssl)
    system_text, converted = _split_system_messages(messages)
    if not converted:
        converted = [{"role": "user", "content": "hello"}]
    payload: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": converted,
    }
    if system_text:
        payload["system"] = system_text
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "x-api-key": settings.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
    }
    # Some OpenAI-compatible gateways still want bearer for anthropic-shaped routes.
    if settings.api_key and not settings.api_key.startswith("sk-ant"):
        headers["Authorization"] = f"Bearer {settings.api_key}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=settings.timeout, context=ssl_context) as response:
        response_body = response.read().decode("utf-8")
        response_data = json.loads(response_body)
        return (
            strip_code_fences(_extract_anthropic_content(response_data)),
            "",
            str(response_data.get("stop_reason") or ""),
        )


def chat_with_meta(messages: list[dict], temperature: float = 0.2) -> dict[str, str]:
    """Call LLM and return both answer content and model reasoning (if any)."""
    initial_settings = get_settings(project_root())
    last_error: Exception | None = None
    last_error_detail = ""
    for attempt in range(1, initial_settings.max_retries + 1):
        settings = get_settings(project_root())
        provider = _normalize_provider(getattr(settings, "provider", "openai"))
        endpoint = (
            _anthropic_messages_endpoint(settings.base_url)
            if provider == "anthropic"
            else _openai_chat_endpoint(settings.base_url)
        )
        try:
            with llm_slot():
                from document_pipeline.llm_telemetry import record_llm_request

                request_parameters = {
                    "provider": provider,
                    "endpoint": endpoint,
                    "model": settings.model,
                    "temperature": temperature,
                    "timeout": settings.timeout,
                    "stream": settings.stream if provider == "openai" else False,
                    "verify_ssl": settings.verify_ssl,
                    "transport_attempt": attempt,
                    "transport_max_retries": initial_settings.max_retries,
                }
                if provider == "anthropic":
                    request_parameters["max_tokens"] = 4096
                    system_text, converted = _split_system_messages(messages)
                    request_parameters["messages"] = converted or [
                        {"role": "user", "content": "hello"}
                    ]
                    if system_text:
                        request_parameters["system"] = system_text
                with record_llm_request(
                    messages,
                    parameters=request_parameters,
                    ):
                        if provider == "anthropic":
                            transport_result = _anthropic_request(
                                settings,
                                messages,
                                temperature,
                            )
                        else:
                            transport_result = _openai_request(
                                settings,
                                messages,
                                temperature,
                            )
                        if len(transport_result) == 2:
                            cleaned, reasoning = transport_result
                            finish_reason = ""
                        else:
                            cleaned, reasoning, finish_reason = transport_result
            record_llm_call(messages, cleaned, settings.model, temperature)
            return {
                "content": cleaned,
                "reasoning": str(reasoning or "").strip(),
                "finish_reason": str(finish_reason or "").strip(),
            }
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            last_error = exc
            last_error_detail = f"HTTP {exc.code}: {error_body[:2000].strip()}"
            should_retry = exc.code not in {400, 401, 403}
            if exc.code == 429:
                note_rate_limit_429()
                should_retry = True
            hint = _build_http_error_hint(endpoint, exc.code, error_body, provider)
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"HTTP {exc.code} provider={provider} endpoint={endpoint} model={settings.model} {error_body}",
                file=sys.stderr,
            )
            if hint:
                print(f"[LLM] 提示: {hint}", file=sys.stderr)
            if not should_retry:
                break
        except Exception as exc:
            last_error = exc
            last_error_detail = str(exc)[:2000]
            hint = _build_connection_error_hint(endpoint, exc, provider)
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"provider={provider} endpoint={endpoint} model={settings.model} {exc}",
                file=sys.stderr,
            )
            if hint:
                print(f"[LLM] 提示: {hint}", file=sys.stderr)

        if attempt < initial_settings.max_retries:
            delay = _retry_delay(attempt, settings.retry_initial_delay, settings.retry_max_delay)
            # 429 storms: longer backoff on top of exponential retry
            if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
                delay = max(delay, min(settings.retry_max_delay, delay * 2))
            print(f"[LLM] {delay:.1f} 秒后重试...", file=sys.stderr)
            time.sleep(delay)

    raise RuntimeError(
        f"LLM 请求失败，已重试 {initial_settings.max_retries} 次。"
        "请检查 API 格式（OpenAI/Anthropic）、Base URL、模型 ID 与 API Key 是否匹配，"
        "或临时增大 OPENAI_MAX_RETRIES 后重试。"
        + (f" 最后错误：{last_error_detail}" if last_error_detail else "")
    ) from last_error


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    return chat_with_meta(messages, temperature=temperature)["content"]


def chat_stream_chunks(messages: list[dict], temperature: float = 0.2):
    settings = get_settings(project_root())
    provider = _normalize_provider(getattr(settings, "provider", "openai"))
    if provider == "anthropic":
        # Anthropic 流式协议不同；这里降级为一次性返回，保证前端可消费
        text = chat(messages, temperature=temperature)
        if text:
            yield ("content", text)
        return

    endpoint = _openai_chat_endpoint(settings.base_url)
    ssl_context = _create_ssl_context(settings.verify_ssl)
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    with llm_slot():
        with urllib.request.urlopen(request, timeout=settings.timeout, context=ssl_context) as response:
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
                reasoning, content = _extract_stream_delta(event_data)
                if reasoning:
                    yield ("reasoning", reasoning)
                if content:
                    yield ("content", content)
