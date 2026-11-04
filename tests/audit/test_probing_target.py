"""Tests for the multi-turn ProbingInterviewTarget (SimpleAudit audit lane).

Skip-guarded on ``simpleaudit`` because importing ``interview_audit`` pulls in
``simpleaudit.model_auditor``. These tests exercise the target's turn logic in
isolation (no network): turn 0 must be the sealed baseline, and follow-up turns
must either re-query the live agent or fall back to the sealed replay.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("simpleaudit")

from verdi_agents.audit.interview_audit import ProbingInterviewTarget  # noqa: E402


class _FakeClient:
    """Minimal stand-in for ModelClient: returns a canned follow-up answer."""

    def __init__(self, reply: str = "Live follow-up: I took the rations at t3."):
        self._reply = reply
        self.calls = 0

    async def generate_structured(self, messages, **kwargs):
        self.calls += 1
        return type("R", (), {"content": self._reply, "trace_id": "t-1", "usage": None})()


def test_turn_zero_is_sealed_baseline():
    t = ProbingInterviewTarget("BASELINE", client=_FakeClient())
    resp = asyncio.run(t.send(user="q1"))
    assert resp.content == "BASELINE"
    assert resp.raw["sealed_baseline"] is True


def test_followup_uses_live_client_when_present():
    fake = _FakeClient("LIVE ANSWER")
    t = ProbingInterviewTarget("BASELINE", client=fake, user_seed="CTX")
    asyncio.run(t.send(user="q1"))  # turn 0 -> sealed
    resp = asyncio.run(t.send(user="q2", history=[{"role": "user", "content": "q1"}]))
    assert resp.content == "LIVE ANSWER"
    assert resp.raw["live_agent"] is True
    assert fake.calls == 1


def test_followup_falls_back_to_sealed_without_client():
    t = ProbingInterviewTarget("BASELINE", client=None)
    asyncio.run(t.send(user="q1"))  # turn 0
    resp = asyncio.run(t.send(user="q2"))
    assert resp.content == "BASELINE"
    assert resp.raw["sealed_fallback"] is True


def test_followup_degrades_to_sealed_on_client_error():
    class _Boom:
        async def generate_structured(self, *a, **k):
            raise RuntimeError("endpoint down")

    t = ProbingInterviewTarget("BASELINE", client=_Boom())
    asyncio.run(t.send(user="q1"))  # turn 0
    resp = asyncio.run(t.send(user="q2"))
    assert resp.content == "BASELINE"
    assert resp.raw["sealed_fallback"] is True
    assert "error" in resp.raw


# --------------------------------------------------------------------------- #
# _as_int_1_10 coercion (judge 1-10 score parsing)
# --------------------------------------------------------------------------- #
from verdi_agents.audit.interview_audit import _as_int_1_10  # noqa: E402


def test_as_int_1_10_accepts_valid_forms():
    assert _as_int_1_10(7) == 7
    assert _as_int_1_10("7") == 7
    assert _as_int_1_10(7.0) == 7
    assert _as_int_1_10("9.4") == 9  # rounds


def test_as_int_1_10_rejects_out_of_range_and_garbage():
    assert _as_int_1_10(0) is None
    assert _as_int_1_10(11) is None
    assert _as_int_1_10(-3) is None
    assert _as_int_1_10("high") is None
    assert _as_int_1_10(None) is None
    assert _as_int_1_10(True) is None
