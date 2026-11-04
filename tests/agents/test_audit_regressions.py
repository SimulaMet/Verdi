"""Regression tests for previously fixed bugs."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from verdi_agents import config as cfg_mod
from verdi_agents.agents import ModelClient
from verdi_agents.agents.client import ModelError

from .fakes import completion_body, make_config


def _client(handler) -> ModelClient:
    return ModelClient(make_config(max_retries=0), transport=httpx.MockTransport(handler))


def _call(client: ModelClient):
    async def run():
        try:
            return await client.generate_structured([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    return asyncio.run(run())


def test_non_json_200_body_raises_typed_model_error():
    # A proxy/CDN answering 200 with an HTML page used to leak a raw
    # JSONDecodeError past every `except ModelError` handler.
    client = _client(lambda req: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(ModelError, match="structurally invalid"):
        _call(client)


def test_null_content_is_normalised_to_empty_string():
    body = completion_body("x")
    body["choices"][0]["message"]["content"] = None
    result = _call(_client(lambda req: httpx.Response(200, json=body)))
    assert result.content == ""


def test_non_string_content_raises_model_error():
    body = completion_body("x")
    body["choices"][0]["message"]["content"] = [{"type": "text"}]
    with pytest.raises(ModelError):
        _call(_client(lambda req: httpx.Response(200, json=body)))


def test_config_repr_never_shows_api_key():
    assert "SECRET-DO-NOT-LOG" not in repr(make_config())


@pytest.mark.parametrize(
    "block, env, message",
    [
        ({"max_retries": -1}, {}, "max_retries"),
        ({"timeout_seconds": 0}, {}, "timeout_seconds"),
        ({}, {"VERDI_MAX_CONCURRENCY": "0"}, "VERDI_MAX_CONCURRENCY"),
    ],
)
def test_endpoint_config_rejects_out_of_range_values(monkeypatch, block, env, message):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match=message):
        cfg_mod.model_endpoint_from_env({"base_url": "http://x/v1", "name": "m", **block})


def test_api_key_values_include_keys_read_via_api_key_env(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("VERDI_VLLM_API_KEY", raising=False)
    monkeypatch.setattr(cfg_mod, "_RESOLVED_KEYS", set())
    monkeypatch.setenv("OTHER_PROVIDER_KEY", "custom-secret-value-123")
    cfg_mod.model_endpoint_from_env(
        {"base_url": "http://x/v1", "name": "m", "api_key_env": "OTHER_PROVIDER_KEY"}
    )
    assert "custom-secret-value-123" in cfg_mod.api_key_values()
