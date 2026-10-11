"""Deterministic soak for ModelClient retry/backoff (P3 prep, no network).

The client uses ``asyncio.sleep`` directly for backoff; we monkeypatch it with a
no-op recorder so every case runs in zero wall-clock time while still exposing
the exact delay sequence the client asked for. All fakes (mock transport,
config factory, completion bodies) are reused from :mod:`tests.agents.fakes`.
"""
from __future__ import annotations

import asyncio
import random
import uuid as uuid_module

import httpx
import pytest

from verdi.agents import ModelClient
from verdi.agents.client import _BACKOFF_BASE, _BACKOFF_CAP, ModelError, ModelResult

from .fakes import chat_transport, completion_body, make_config

BACKOFF_BASE = _BACKOFF_BASE  # constants are private in the frozen client module
BACKOFF_CAP = _BACKOFF_CAP

FAKE_API_KEY = "SECRET-DO-NOT-LOG"  # matches fakes.make_config
SEED = 1337


def recording_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch asyncio.sleep (referenced inside client.py) to a no-op recorder."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


def recording_uuid4(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch uuid.uuid4 so we can prove one logical trace id per request."""
    generated: list[str] = []
    counter = 0

    def fake_uuid4() -> uuid_module.UUID:
        nonlocal counter
        value = str(uuid_module.uuid5(uuid_module.NAMESPACE_DNS, f"soak-{counter}"))
        generated.append(value)
        counter += 1
        return uuid_module.UUID(value)

    monkeypatch.setattr("verdi.agents.client.uuid.uuid4", fake_uuid4)
    return generated


def run_generate(
    transport: httpx.AsyncBaseTransport,
    *,
    max_retries: int,
    seed: int = SEED,
) -> ModelResult | ModelError:
    """Run one logical request; return the ModelResult or the raised ModelError."""
    rng = random.Random(seed)
    client = ModelClient(make_config(max_retries=max_retries), transport=transport, rng=rng)

    async def run():
        try:
            return await client.generate_structured([{"role": "user", "content": "hi"}])
        except ModelError as exc:
            return exc
        finally:
            await client.aclose()

    return asyncio.run(run())


def test_retry_then_success_trace_chain_single_trace_id(monkeypatch: pytest.MonkeyPatch):
    sleeps = recording_sleep(monkeypatch)
    generated_ids = recording_uuid4(monkeypatch)
    records: list[dict] = []
    transport = chat_transport([429, 429, completion_body("soak-ok")], records=records)

    result = run_generate(transport, max_retries=3)

    assert isinstance(result, ModelResult)
    assert result.content == "soak-ok"
    assert len(records) == 3  # exactly 3 attempts: 429, 429, 200
    assert len(sleeps) == 2  # slept after attempts 0 and 1 only
    # Capped exponential growth: each base doubles, jitter in [0.5, 1.0) keeps
    # delay_a <= base_a <= 0.5*base_{a+1} <= delay_{a+1} until the cap.
    assert sleeps == sorted(sleeps)
    assert all(0.0 <= d <= BACKOFF_CAP for d in sleeps)
    # Exactly one trace id for the whole logical request chain.
    assert generated_ids == [result.trace_id]
    # The api key stays in the Authorization header, never in the payload.
    assert all(rec["json"] for rec in records)
    assert all(FAKE_API_KEY not in repr(rec["json"]) for rec in records)


@pytest.mark.parametrize("status", [429, 502, 503, 524])
def test_budget_exhaustion(monkeypatch: pytest.MonkeyPatch, status: int):
    sleeps = recording_sleep(monkeypatch)
    records: list[dict] = []
    transport = chat_transport([status] * 4, records=records)

    err = run_generate(transport, max_retries=3)

    assert isinstance(err, ModelError)
    assert err.status_code == status
    assert len(records) == 4  # original + exactly 3 retries
    assert len(sleeps) == 3
    assert all(d <= BACKOFF_CAP for d in sleeps)
    assert "3 retries" in str(err)
    assert err.trace_id is not None and len(err.trace_id) == 36
    assert FAKE_API_KEY not in str(err)


@pytest.mark.parametrize("status", [400, 500])
def test_non_retryable_no_backoff(monkeypatch: pytest.MonkeyPatch, status: int):
    sleeps = recording_sleep(monkeypatch)
    records: list[dict] = []
    transport = chat_transport([status], records=records)

    err = run_generate(transport, max_retries=3)

    assert isinstance(err, ModelError)
    assert err.status_code == status
    assert len(records) == 1  # exactly one attempt
    assert sleeps == []  # zero sleeps: no backoff on non-retryable status
    assert FAKE_API_KEY not in str(err)


def test_transport_errors_then_success(monkeypatch: pytest.MonkeyPatch):
    sleeps = recording_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=completion_body("transport-ok"))

    result = run_generate(httpx.MockTransport(handler), max_retries=3)

    assert isinstance(result, ModelResult)
    assert result.content == "transport-ok"
    assert calls["n"] == 3  # two failures + success = two retries
    assert len(sleeps) == 2


def test_persistent_transport_error_no_secret_leakage(monkeypatch: pytest.MonkeyPatch):
    sleeps = recording_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    err = run_generate(httpx.MockTransport(handler), max_retries=3)

    assert isinstance(err, ModelError)
    assert err.status_code is None
    assert calls["n"] == 4  # original + 3 retries
    assert len(sleeps) == 3
    assert "ConnectError" in str(err)  # exception type is named
    assert FAKE_API_KEY not in str(err)
    assert FAKE_API_KEY not in (err.trace_id or "")


def test_backoff_deterministic_under_fixed_seed(monkeypatch: pytest.MonkeyPatch):
    def schedule() -> list[float]:
        sleeps = recording_sleep(monkeypatch)
        transport = chat_transport([429, 429, 429, completion_body("ok")], records=[])
        run_generate(transport, max_retries=3, seed=SEED)
        return sleeps

    first = schedule()
    second = schedule()

    assert len(first) == 3
    assert first == second


def test_backoff_cap_boundaries(monkeypatch: pytest.MonkeyPatch):
    """Run a full schedule deep past the cap and check the cap arithmetic.

    base = min(cap, 0.5 * 2**attempt); jitter in [0.5, 1.0), so every delay
    satisfies 0.5*base <= delay < cap and in particular delay <= cap * 1.0.
    The 15.0 bound (cap * (0.5 + 1.0)) is an outer safety fence.
    """
    sleeps = recording_sleep(monkeypatch)
    # attempts 0..8: bases 0.5,1,2,4,8,10(capped),10,10,10 -> 8 sleeps
    transport = chat_transport([429] * 9, records=[])
    err = run_generate(transport, max_retries=8)

    assert isinstance(err, ModelError)
    assert len(sleeps) == 8
    # Once 0.5 * 2**attempt reaches the cap (attempt >= 5), pre-jitter base is
    # exactly the cap, so post-jitter delay stays within [cap/2, cap].
    capped = sleeps[5:]
    assert len(capped) == 3
    assert all(d >= BACKOFF_CAP / 2 for d in capped)
    assert all(d <= BACKOFF_CAP * 1.0 for d in capped)
    # Nothing ever exceeds the jitter upper boundary cap * (0.5 + 1.0).
    assert all(d <= BACKOFF_CAP * (0.5 + 1.0) for d in sleeps)
    assert max(sleeps) <= 15.0
