"""Async OpenAI-compatible client for Qwen on a vLLM endpoint.

Design rules (docs/ARCHITECTURE.md, GPU execution section):
- bounded concurrency is enforced by the pool, not here;
- every request carries a uuid trace id, returned on the result;
- retries ONLY on transient 429/502/503 with capped exponential backoff and jitter;
- secrets (api key) never appear in logs or error messages.
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from verdi.config import ModelEndpointConfig

# 524 is Cloudflare's origin-timeout (equivalent to 502/503: the upstream
# origin — here a 27B reasoning model under load — took too long to answer).
# Stage C-1 attempt 6 died on an un-retried 524 (LESSONS Tranche 12g).
RETRYABLE_STATUS = (429, 502, 503, 524)
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 10.0
_CONNECT_TIMEOUT = 15.0
_POOL_TIMEOUT = 30.0


class ModelError(RuntimeError):
    """Typed failure after the retry budget is exhausted (or a non-retryable error)."""

    def __init__(self, message: str, *, status_code: int | None = None, trace_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.trace_id = trace_id


@dataclass(frozen=True)
class ModelResult:
    """Small, inspectable outcome of one completion request."""

    content: str
    request_id: str
    model: str
    trace_id: str
    usage: dict[str, Any] = field(default_factory=dict)
    #: Endpoint-supplied reasoning/thinking text (may be empty).
    reasoning_content: str = ""


def _backoff_delay(attempt: int, rng: random.Random) -> float:
    delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2**attempt))
    return delay * (0.5 + rng.random() / 2)


class ModelClient:
    """httpx-based client constructed from :class:`verdi.config.ModelEndpointConfig`."""

    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._rng = rng or random.Random()
        #: Successful completions so far. A liveness signal for the stall
        #: watchdog: batched phases (reflection, consolidation) only write
        #: model_calls.jsonl after the whole batch, so file lines alone can
        #: stay flat for a long, healthy batch.
        self.completed_calls = 0
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            # Bounded pool + FINITE pool_timeout. Without an explicit
            # pool_timeout, httpx waits FOREVER for a free connection when the
            # pool is exhausted (the request `timeout` only covers the actual
            # request, not pool acquisition). Under sustained concurrent load
            # against a slow inference endpoint this deadlocked whole campaign
            # ticks. A finite pool_timeout turns that into a retryable
            # TransportError instead.
            # Explicit read/write/connect bounds so a no-bytes hang (dead
            # socket) always trips the read timeout and becomes retryable,
            # rather than blocking indefinitely.
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT, read=config.timeout_seconds,
                write=config.timeout_seconds, pool=_POOL_TIMEOUT,
            ),
            # Bounded pool sized to the concurrency we actually run at. Keepalive
            # REUSE is allowed (capped) so sockets are recycled rather than opening
            # a fresh TCP connection per call and piling up in CLOSE_WAIT — that
            # pileup is what exhausted the pool under sustained multi-turn audit
            # load and made "more concurrency" counterproductive. A dead reused
            # socket is safe: the finite read/write/pool timeouts below turn it
            # into a retryable TransportError instead of an infinite hang.
            limits=httpx.Limits(
                max_connections=max(20, config.max_concurrency * 2),
                max_keepalive_connections=max(10, config.max_concurrency),
                keepalive_expiry=30.0,
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ModelClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        """POST /chat/completions; retry only on 429/502/503 with capped backoff.

        One uuid trace id per logical request, echoed on every result so the
        full retry chain can be correlated in traces.
        """
        trace_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                # Connection failures/timeouts are transient: retry with the
                # same capped backoff as 429/502/503.
                if attempt < self._config.max_retries:
                    await asyncio.sleep(_backoff_delay(attempt, self._rng))
                    continue
                raise ModelError(
                    f"request {trace_id} failed to reach endpoint "
                    f"(after {self._config.max_retries} retries): {type(exc).__name__}",
                    trace_id=trace_id,
                ) from exc
            except httpx.HTTPError as exc:  # non-transport HTTPError: not retryable
                raise ModelError(
                    f"request {trace_id} failed to reach endpoint: {type(exc).__name__}",
                    trace_id=trace_id,
                ) from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self._config.max_retries:
                await asyncio.sleep(_backoff_delay(attempt, self._rng))
                continue

            if response.status_code == 200:
                result = self._parse(response, trace_id)
                self.completed_calls += 1
                return result

            raise ModelError(
                f"request {trace_id} failed with HTTP {response.status_code}"
                + (f" after {self._config.max_retries} retries" if attempt else ""),
                status_code=response.status_code,
                trace_id=trace_id,
            )

        raise AssertionError("unreachable: retry loop must return or raise")

    @staticmethod
    def _parse(response: httpx.Response, trace_id: str) -> ModelResult:
        try:
            body = json.loads(response.text)
            message = body["choices"][0]["message"]
            content = message["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # ValueError covers JSONDecodeError: a proxy/CDN can answer 200 with
            # an HTML page. Surface it as the typed ModelError callers handle.
            raise ModelError(
                f"request {trace_id} returned a structurally invalid completion body",
                trace_id=trace_id,
            ) from exc
        # Reasoning models may return ``"content": null`` (e.g. max_tokens spent
        # on thinking). Normalise to "" so callers can rely on a str.
        if content is None:
            content = ""
        elif not isinstance(content, str):
            raise ModelError(
                f"request {trace_id} returned non-string completion content",
                trace_id=trace_id,
            )
        return ModelResult(
            content=content,
            request_id=str(body.get("id", "")),
            model=str(body.get("model", "")),
            trace_id=trace_id,
            usage=body.get("usage") or {},
            reasoning_content=str(message.get("reasoning_content") or ""),
        )
