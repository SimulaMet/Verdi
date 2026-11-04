"""Offline fakes shared by agent tests: no live network calls anywhere."""
from __future__ import annotations

import json

import httpx

from verdi_agents.config import ModelEndpointConfig


def make_config(**overrides) -> ModelEndpointConfig:
    base = dict(
        base_url="http://model.test/v1",
        model="qwen-test",
        api_key="SECRET-DO-NOT-LOG",
        max_concurrency=2,
        timeout_seconds=5.0,
        max_retries=1,
    )
    base.update(overrides)
    return ModelEndpointConfig(**base)


def completion_body(content: str, *, request_id: str = "req-1") -> dict:
    return {
        "id": request_id,
        "model": "qwen-test",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 7},
    }


def chat_transport(statuses: list[int] | list[dict], *, records: list[dict] | None = None) -> httpx.MockTransport:
    """MockTransport that plays `statuses` (int or body-dict) in order.

    Every request is appended to `records` as {"path", "json", "headers"} for
    assertions (e.g. that the api key is not leaked into payloads).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        records.append(
            {
                "path": request.url.path,
                "json": json.loads(request.content),
                "headers": dict(request.headers),
            }
        )
        item = statuses.pop(0)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": f"HTTP {item}"}})
        return httpx.Response(200, json=item)

    return httpx.MockTransport(handler)
