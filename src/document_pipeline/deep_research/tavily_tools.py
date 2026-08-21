from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, Callable

import certifi

from .config import DeepResearchConfig
from .contracts import ExtractedWebSource, WebExtractResult, WebSearchHit


SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
_CHAT_HOSTS = {
    "chat.deepseek.com",
    "www.doubao.com",
    "doubao.com",
    "chat.openai.com",
    "chatgpt.com",
    "claude.ai",
}


def normalize_public_url(value: str, *, max_length: int = 2048) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > max_length:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".").lower()
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            return ""
        if host in _CHAT_HOSTS:
            return ""
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return ""
        port = parsed.port
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = host if port is None or default_port else f"{host}:{port}"
        path = parsed.path or "/"
        return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))
    except (TypeError, ValueError, UnicodeError):
        return ""


def _publisher(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "公开网页"


class TavilyWebTools:
    provider_id = "tavily"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config: DeepResearchConfig | None = None,
        transport: Callable[[str, dict[str, Any], int, str], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config or DeepResearchConfig.from_env()
        self.api_key = (api_key if api_key is not None else os.environ.get("BID_AGENT_TAVILY_API_KEY", "")).strip()
        self.search_url = os.environ.get("BID_AGENT_TAVILY_SEARCH_URL", SEARCH_URL).strip() or SEARCH_URL
        self.extract_url = os.environ.get("BID_AGENT_TAVILY_EXTRACT_URL", EXTRACT_URL).strip() or EXTRACT_URL
        self.search_depth = os.environ.get("BID_AGENT_TAVILY_SEARCH_DEPTH", "basic").strip().lower()
        if self.search_depth not in {"basic", "advanced", "fast", "ultra-fast"}:
            raise ValueError("BID_AGENT_TAVILY_SEARCH_DEPTH 配置无效")
        self._transport = transport or self._post_json

    @staticmethod
    def _request_attempts() -> int:
        raw = str(os.environ.get("BID_AGENT_TAVILY_REQUEST_MAX_ATTEMPTS", "3")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 3
        return max(1, min(value, 5))

    def _request(self, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        attempts = self._request_attempts()
        for attempt in range(1, attempts + 1):
            try:
                return self._transport(url, payload, timeout, self.api_key)
            except RuntimeError as exc:
                reason = str(exc or "")
                transient = (
                    reason == "TAVILY_HTTP_429"
                    or reason.startswith("TAVILY_HTTP_5")
                    or reason.startswith("TAVILY_REQUEST_FAILED:")
                )
                if not transient or attempt >= attempts:
                    raise
                time.sleep(0.4 * attempt)
        raise RuntimeError("TAVILY_REQUEST_RETRY_EXHAUSTED")

    def runtime_status(self) -> dict[str, object]:
        return {
            "ready": bool(self.api_key),
            "provider_id": self.provider_id,
            "reason": "" if self.api_key else "TAVILY_API_KEY_MISSING",
        }

    def web_search(self, query: str, *, limit: int | None = None) -> list[WebSearchHit]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("TAVILY_SEARCH_QUERY_EMPTY")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY_MISSING")
        maximum = min(limit or self.config.max_search_results, self.config.max_search_results)
        payload = {
            "query": query,
            "topic": "general",
            "search_depth": self.search_depth,
            "max_results": maximum,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": False,
            "include_usage": True,
        }
        parsed = self._request(
            self.search_url,
            payload,
            self.config.extract_timeout_seconds,
        )
        hits: list[WebSearchHit] = []
        seen: set[str] = set()
        for index, result in enumerate(parsed.get("results") or []):
            if not isinstance(result, dict):
                continue
            url = normalize_public_url(str(result.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            publisher = _publisher(url)
            score = result.get("score")
            try:
                normalized_score = float(score) if score is not None else None
            except (TypeError, ValueError):
                normalized_score = None
            hits.append(
                WebSearchHit(
                    hit_id=f"WH-{hashlib.sha256(f'{query}:{url}'.encode()).hexdigest()[:16]}",
                    query=query,
                    title=str(result.get("title") or publisher).strip()[:300] or publisher,
                    url=url,
                    # Search content is discovery metadata only.  It is never copied
                    # to ExtractedWebSource or ResearchCandidate.
                    snippet=str(result.get("content") or "").strip()[:2_000],
                    score=normalized_score,
                    publisher=publisher,
                    provider_id=self.provider_id,
                )
            )
            if len(hits) >= maximum:
                break
        return hits

    def web_extract(self, urls: list[str]) -> WebExtractResult:
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY_MISSING")
        normalized: list[str] = []
        rejected: list[dict] = []
        for value in urls:
            url = normalize_public_url(value)
            if not url:
                rejected.append({"url": str(value or "")[:500], "reason": "URL_SAFETY_REJECTED"})
            elif url not in normalized:
                normalized.append(url)
        normalized = normalized[: self.config.max_extract_urls_per_round]
        if not normalized:
            return WebExtractResult(sources=[], rejected_urls=rejected)
        payload = {
            "urls": normalized,
            "extract_depth": self.config.extract_depth,
            "include_images": False,
            "include_favicon": False,
            "format": "markdown",
            "timeout": self.config.extract_timeout_seconds,
            "include_usage": True,
        }
        parsed = self._request(
            self.extract_url,
            payload,
            self.config.extract_timeout_seconds + 5,
        )
        sources: list[ExtractedWebSource] = []
        returned: set[str] = set()
        for result in parsed.get("results") or []:
            if not isinstance(result, dict):
                continue
            requested = normalize_public_url(str(result.get("url") or ""))
            final_url = normalize_public_url(str(result.get("final_url") or requested))
            if not requested or requested not in normalized or not final_url:
                continue
            returned.add(requested)
            raw_content = str(result.get("raw_content") or "").strip()
            if len(raw_content) < 80:
                rejected.append({"url": requested, "reason": "EXTRACT_CONTENT_TOO_SHORT"})
                continue
            raw_content = raw_content[: self.config.max_source_chars]
            digest = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            publisher = _publisher(final_url)
            sources.append(
                ExtractedWebSource(
                    source_id=f"WS-{digest[:16]}",
                    requested_url=requested,
                    final_url=final_url,
                    title=str(result.get("title") or publisher).strip()[:300] or publisher,
                    publisher=publisher,
                    raw_content=raw_content,
                    content_hash=digest,
                    content_type=str(result.get("content_type") or "text/markdown"),
                    extraction_provider=self.provider_id,
                    extracted_at=datetime.now(UTC).isoformat(),
                )
            )
        for failed in parsed.get("failed_results") or []:
            if not isinstance(failed, dict):
                continue
            url = normalize_public_url(str(failed.get("url") or "")) or str(failed.get("url") or "")[:500]
            returned.add(url)
            rejected.append({"url": url, "reason": str(failed.get("error") or "EXTRACT_FAILED")[:500]})
        for url in normalized:
            if url not in returned:
                rejected.append({"url": url, "reason": "EXTRACT_RESULT_MISSING"})
        return WebExtractResult(sources=sources, rejected_urls=rejected)

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], timeout: int, api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            # The backend Conda runtime can encounter malformed entries in the
            # Windows certificate store (ASN1 NOT_ENOUGH_DATA) before a request
            # is even sent.  Use the maintained certifi CA bundle so Tavily TLS
            # verification is deterministic across Python runtimes.
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                status = int(getattr(response, "status", 200) or 200)
                body = response.read(4_000_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status in {401, 429, 432, 433} or status >= 500:
                raise RuntimeError(f"TAVILY_HTTP_{status}") from exc
            raise RuntimeError(f"TAVILY_HTTP_{status}") from exc
        except Exception as exc:
            raise RuntimeError(f"TAVILY_REQUEST_FAILED:{type(exc).__name__}") from exc
        if status >= 400:
            raise RuntimeError(f"TAVILY_HTTP_{status}")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("TAVILY_INVALID_JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("TAVILY_INVALID_RESPONSE")
        return parsed
