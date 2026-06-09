from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

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


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    settings = get_settings(project_root())
    endpoint = _chat_endpoint(settings.base_url)
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout) as response:
                response_body = response.read().decode("utf-8")
            response_data = json.loads(response_body)
            return strip_code_fences(_extract_content(response_data))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            last_error = exc
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: "
                f"HTTP {exc.code} {error_body}",
                file=sys.stderr,
            )
        except Exception as exc:
            last_error = exc
            print(
                f"[LLM] 第 {attempt}/{settings.max_retries} 次请求失败: {exc}",
                file=sys.stderr,
            )

        if attempt < settings.max_retries:
            time.sleep(min(2 ** (attempt - 1), 8))

    raise RuntimeError(f"LLM 请求失败，已重试 {settings.max_retries} 次。") from last_error
