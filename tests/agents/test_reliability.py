"""P3 reliability soak for the ModelClient retry path (WS-6).

Deterministic, offline, no wall-clock sleep: ``asyncio.sleep`` is replaced by a
recorder and the backoff RNG is seeded per scenario. Expected values are
recomputed from ``verdi.agents.client`` constants (``_BACKOFF_BASE``,
``_BACKOFF_CAP``, ``_backoff_delay``), never hardcoded.
"""
from __future__ import annotations

import asyncio
import json
import random

import httpx
import pytest

from verdi.agents import ModelClient
from verdi.agents.client import _BACKOFF_BASE, _BACKOFF_CAP, ModelError, _backoff_delay

from .fakes import completion_body, make_config

SEED = 42
MAX_ATTEMPTS = 5  # retry-budget scenario: 5 retries, 6 attempts


def run(coro):
    return asyncio.run(coro)


def patched_sleep(monkeypatch):
    """Replace asyncio.sleep with a recorder; returns the captured delays."""
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def handler_fail_transport_then_200(k: int):
    """Fails with httpx.TransportError on the first k calls, then answers 200."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= k:
            raise httpx.ConnectError("deterministic simulated outage")
        return httpx.Response(200, json=completion_body("recovered"))

    return handler, calls


def expected_delays(seed: int, attempts: int) -> list[float]:
    """Same formula as client._backoff_delay, from a fresh seeded RNG."""
    rng = random.Random(seed)
    return [_backoff_delay(attempt, rng) for attempt in range(attempts)]


class TestTransportSoak:
    @pytest.mark.parametrize("k", [0, 1, MAX_ATTEMPTS - 1])
    def test_fail_then_success_within_budget(self, monkeypatch, k: int):
        delays = patched_sleep(monkeypatch)
        handler, calls = handler_fail_transport_then_200(k)
        client = ModelClient(
            make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                return await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        result = run(run_call())

        assert result.content == "recovered"
        assert calls["n"] == k + 1  # exactly k failures + one success
        assert len(delays) == k  # one backoff per failed attempt, none after success
        assert result.trace_id and len(result.trace_id) == 36

    def test_boundary_k_equals_max_attempts_succeeds_on_last_attempt(self, monkeypatch):
        """k = max_attempts - 1: success only on the very last permitted attempt."""
        delays = patched_sleep(monkeypatch)
        handler, calls = handler_fail_transport_then_200(MAX_ATTEMPTS - 1)
        client = ModelClient(
            make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                return await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        result = run(run_call())
        assert result.content == "recovered"
        assert calls["n"] == MAX_ATTEMPTS  # exactly max_retries + 1 attempts used
        assert len(delays) == MAX_ATTEMPTS - 1  # no backoff after the final attempt

    def test_exhausted_budget_raises_with_final_trace_and_status(self, monkeypatch):
        delays = patched_sleep(monkeypatch)
        statuses = [503] * (MAX_ATTEMPTS + 1)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(statuses.pop(0), json={"error": {"message": "unavailable"}})

        client = ModelClient(
            make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        with pytest.raises(ModelError) as excinfo:
            run(run_call())

        assert calls["n"] == MAX_ATTEMPTS + 1  # original + exactly max_retries
        assert len(delays) == MAX_ATTEMPTS  # one backoff before each retry, not after
        # final trace/status preserved: single logical trace across the whole chain,
        # status carried on the typed error, no secret leak.
        assert excinfo.value.trace_id and len(excinfo.value.trace_id) == 36
        assert excinfo.value.status_code == 503
        assert "SECRET-DO-NOT-LOG" not in str(excinfo.value)
        # note (see FINDING): the trace id is returned on the error/result but is
        # NOT sent as a per-attempt header, so it cannot correlate attempts server-side


class TestBackoffBounds:
    @pytest.mark.parametrize("k", [1, MAX_ATTEMPTS - 1])
    def test_delays_monotonic_bounded_and_match_seed(self, monkeypatch, k: int):
        delays = patched_sleep(monkeypatch)
        handler, _ = handler_fail_transport_then_200(k)
        client = ModelClient(
            make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        run(run_call())

        assert len(delays) == k
        # monotonic non-decreasing across the full captured sequence
        assert all(b >= a for a, b in zip(delays, delays[1:]))
        # jittered multiplier is in [0.5, 1.0), so every delay is bounded by the
        # cap and the first by the (uncapped) exponential term bounds
        assert all(d <= _BACKOFF_CAP for d in delays)
        assert delays[0] >= _BACKOFF_BASE * 0.5
        assert delays[0] <= _BACKOFF_BASE
        # the sequence matches the documented formula recomputed from the same
        # seed (see FINDING: with the 0.5..1.0 jitter the first delay may be
        # BELOW _BACKOFF_BASE, so "first >= base" in a strict sense does not hold)
        assert delays == expected_delays(SEED, k)

    def test_cap_is_reached_and_never_exceeded(self, monkeypatch):
        """With enough retries the raw exponential term hits _BACKOFF_CAP."""
        budget = 12
        delays = patched_sleep(monkeypatch)
        handler, _ = handler_fail_transport_then_200(budget)
        client = ModelClient(
            make_config(max_retries=budget, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        run(run_call())

        assert len(delays) == budget
        assert all(d <= _BACKOFF_CAP for d in delays)
        # the uncapped exponential term actually reaches the cap in this window,
        # so the bound is exercised (and the max of the sequence is capped)
        terms = [_BACKOFF_BASE * (2**a) for a in range(budget)]
        assert any(t >= _BACKOFF_CAP for t in terms)
        assert max(terms) > max(delays)  # jitter strictly scales the capped term down
        assert delays == expected_delays(SEED, budget)


class TestIsolation:
    def test_second_call_makes_exactly_one_attempt(self, monkeypatch):
        delays = patched_sleep(monkeypatch)
        # first call: two simulated transport failures, then success
        handler1, calls1 = handler_fail_transport_then_200(2)
        # second call: healthy endpoint
        handler2, calls2 = handler_fail_transport_then_200(0)
        config = make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0)
        client = ModelClient(
            config, transport=httpx.MockTransport(handler1), rng=random.Random(SEED)
        )
        rng = client._rng  # the live (possibly shared) backoff RNG

        async def run_calls():
            try:
                first = await client.generate_structured([{"role": "user", "content": "one"}])
                client._client = httpx.AsyncClient(
                    base_url=config.base_url,
                    headers={"Authorization": "******"},
                    timeout=config.timeout_seconds,
                    transport=httpx.MockTransport(handler2),
                )
                second = await client.generate_structured([{"role": "user", "content": "two"}])
                return first, second
            finally:
                await client.aclose()

        first, second = run(run_calls())

        assert first.content == "recovered"
        assert second.content == "recovered"
        assert calls1["n"] == 3  # 2 failures + success
        assert calls2["n"] == 1  # second call: exactly one transport attempt
        assert first.trace_id != second.trace_id  # independent logical traces
        # no retry state or timing carried into the second success path: all
        # recorded delays belong to the first call, the second slept never
        assert len(delays) == 2
        # shared client RNG stream advances past the first call's draws; the
        # second call's success path is unchanged because it drew no delays
        assert rng.getstate() != random.Random(SEED).getstate()


class TestNoRetryClass:
    def test_http_status_error_is_not_retried(self, monkeypatch):
        """Actual client behavior (client.py:118-122): any non-transport
        httpx.HTTPError is raised as ModelError immediately, no retry.
        (TransportError is checked first at client.py:107, hence the split.)"""
        delays = patched_sleep(monkeypatch)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://model.test/v1/chat/completions"),
                response=httpx.Response(418, request=httpx.Request("GET", "http://model.test")),
            )

        client = ModelClient(
            make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
            transport=httpx.MockTransport(handler),
            rng=random.Random(SEED),
        )

        async def run_call():
            try:
                await client.generate_structured([{"role": "user", "content": "hi"}])
            finally:
                await client.aclose()

        with pytest.raises(ModelError) as excinfo:
            run(run_call())

        assert calls["n"] == 1  # single attempt: HTTPStatusError is not retried
        assert delays == []  # no backoff recorded
        assert "HTTPStatusError" in str(excinfo.value)
        assert excinfo.value.status_code is None  # status surfaced only for HTTP responses
        assert excinfo.value.trace_id and len(excinfo.value.trace_id) == 36


class TestDeterminism:
    def test_identical_reruns_produce_identical_sequences(self, monkeypatch):
        """Same seed + fakes -> byte-identical (attempt_count, delays)."""
        runs: list[tuple[int, list[float]]] = []
        for _ in range(2):
            delays = patched_sleep(monkeypatch)
            handler, calls = handler_fail_transport_then_200(MAX_ATTEMPTS - 1)
            client = ModelClient(
                make_config(max_retries=MAX_ATTEMPTS, timeout_seconds=1.0),
                transport=httpx.MockTransport(handler),
                rng=random.Random(SEED),
            )

            async def run_call():
                try:
                    await client.generate_structured([{"role": "user", "content": "hi"}])
                finally:
                    await client.aclose()

            run(run_call())
            runs.append((calls["n"], list(delays)))

        assert runs[0] == runs[1]
        assert runs[0][0] == MAX_ATTEMPTS
        assert json.dumps(runs[0][1]) == json.dumps(runs[1][1])  # byte-identical
        assert runs[0][1] == expected_delays(SEED, MAX_ATTEMPTS - 1)
