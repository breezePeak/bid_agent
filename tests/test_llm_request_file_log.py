from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import llm_client


def test_chapter_agent_request_is_written_with_full_payload(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "chapter-requests.jsonl"
    monkeypatch.setenv("BID_AGENT_LLM_REQUEST_LOG", str(log_path))
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "章节写作规则"},
            {"role": "user", "content": "生成第一章"},
        ],
        "temperature": 0.3,
        "stream": True,
    }

    with mock.patch.object(
        llm_client,
        "_request_callsite",
        return_value=(
            {
                "module": "document_pipeline.chapter_chat",
                "function": "stream",
                "file": "src/document_pipeline/chapter_chat.py",
                "line": 1,
            },
            True,
        ),
    ):
        llm_client._log_llm_request(
            provider="openai",
            endpoint="https://example.test/v1/chat/completions",
            payload=payload,
            timeout=60,
            verify_ssl=True,
            transport_attempt=2,
            transport_max_retries=3,
        )

    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["agent"] == "chapter_agent"
    assert record["callsite"]["module"] == "document_pipeline.chapter_chat"
    assert record["parameters"]["messages"] == payload["messages"]
    assert record["parameters"]["model"] == "test-model"
    assert record["parameters"]["temperature"] == 0.3
    assert record["parameters"]["stream"] is True
    assert record["parameters"]["transport_attempt"] == 2
    assert "api_key" not in record["parameters"]


def test_non_chapter_request_is_not_written(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "chapter-requests.jsonl"
    monkeypatch.setenv("BID_AGENT_LLM_REQUEST_LOG", str(log_path))

    with mock.patch.object(
        llm_client,
        "_request_callsite",
        return_value=({"module": "fact_extractor"}, False),
    ):
        llm_client._log_llm_request(
            provider="openai",
            endpoint="https://example.test/v1/chat/completions",
            payload={"model": "test-model", "messages": []},
            timeout=60,
            verify_ssl=True,
            transport_attempt=1,
            transport_max_retries=1,
        )

    assert not log_path.exists()
