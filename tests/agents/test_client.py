import asyncio
import json

import pytest

from verdi.agents import ModelClient
from verdi.agents.client import ModelError

from .fakes import chat_transport, completion_body, make_config


def test_generate_structured_success_returns_result_with_trace_id():
    records: list[dict] = []
    transport = chat_transport(
        [completion_body('{"action_kind": "speak", "arguments": {}}')], records=records
    )
    client = ModelClient(make_config(max_retries=1), transport=transport)

    async def run():
        try:
            return await client.generate_structured(
                [{"role": "user", "content": "hi"}], response_format_json=True
            )
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.content == '{"action_kind": "speak", "arguments": {}}'
    assert result.request_id == "req-1"
    assert result.model == "qwen-test"
    assert len(result.trace_id) == 36  # uuid4
    assert result.usage["completion_tokens"] == 7
    (req,) = records
    assert req["path"] == "/v1/chat/completions"
    assert req["json"]["response_format"] == {"type": "json_object"}
    # api key rides only in the Authorization header, never in the payload
    assert req["headers"]["authorization"] == "Bearer SECRET-DO-NOT-LOG"
    assert "SECRET-DO-NOT-LOG" not in json.dumps(req["json"])


def test_retry_once_then_fail_on_503():
    records: list[dict] = []
    # first attempt 503 (retryable), second 503 again -> exhausted after 1 retry
    transport = chat_transport([503, 503], records=records)
    client = ModelClient(make_config(max_retries=1), transport=transport)

    async def run():
        try:
            await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    with pytest.raises(ModelError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.status_code == 503
    assert len(records) == 2  # original + exactly one retry
    # no secret in the error message
    assert "SECRET-DO-NOT-LOG" not in str(excinfo.value)


def test_retry_then_success():
    records: list[dict] = []
    transport = chat_transport([429, completion_body("ok")], records=records)
    client = ModelClient(make_config(max_retries=2), transport=transport)

    async def run():
        try:
            return await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.content == "ok"
    assert len(records) == 2


def test_non_retryable_400_raises_immediately():
    records: list[dict] = []
    transport = chat_transport([400], records=records)
    client = ModelClient(make_config(max_retries=3), transport=transport)

    async def run():
        try:
            await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    with pytest.raises(ModelError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.status_code == 400
    assert len(records) == 1  # no retry on 400


def test_transport_error_retries_then_succeeds():
    """Regression (review finding 5): connection-level transport errors are
    transient and must be retried with the capped backoff, then succeed."""
    import httpx

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=completion_body('{"action_kind": "speak", "arguments": {}}'))

    client = ModelClient(make_config(max_retries=2, timeout_seconds=1.0), transport=httpx.MockTransport(handler))

    async def run():
        try:
            return await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert calls["n"] == 2  # one failure + one retry
    assert result.content == '{"action_kind": "speak", "arguments": {}}'


def test_transport_error_exhausts_retries():
    """Transport errors raise ModelError after max_retries attempts."""
    import httpx

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    client = ModelClient(make_config(max_retries=1, timeout_seconds=1.0), transport=httpx.MockTransport(handler))

    async def run():
        try:
            await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    with pytest.raises(ModelError):
        asyncio.run(run())
    assert calls["n"] == 2  # original + exactly one retry


def test_reasoning_content_captured_when_present_and_empty_by_default():
    """reasoning_content is populated from the message when the endpoint
    sends it (reasoning models) and stays empty otherwise (additive)."""
    body = completion_body('{"action_kind": "speak", "arguments": {}}')
    body["choices"][0]["message"]["reasoning_content"] = "step by step"
    transport = chat_transport([body, completion_body("ok")], records=[])
    client = ModelClient(make_config(), transport=transport)

    async def run():
        try:
            with_reasoning = await client.generate_structured(
                [{"role": "user", "content": "hi"}]
            )
            without = await client.generate_structured(
                [{"role": "user", "content": "hi"}]
            )
            return with_reasoning, without
        finally:
            await client.aclose()

    results = asyncio.run(run())
    assert results[0].reasoning_content == "step by step"
    assert results[1].reasoning_content == ""


def test_malformed_200_body_raises_typed_error():
    transport = chat_transport([{"id": "x", "choices": []}], records=[])
    client = ModelClient(make_config(), transport=transport)

    async def run():
        try:
            await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    with pytest.raises(ModelError):
        asyncio.run(run())
