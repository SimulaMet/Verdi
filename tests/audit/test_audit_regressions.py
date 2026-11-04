"""Regression tests for previously fixed bugs."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import verdi_agents.audit.interview_audit as ia
from verdi_agents.audit.judge import _assert_no_key_material


@pytest.mark.parametrize("text", [
    "Bearer of bad news, I told Erin.",
    "Authorization: the assembly approved my request",
    "my api_key: none",
    "as standard-bearer counter-proposals matter",
    "Bearer responsibilities-and-obligations",
])
def test_key_guard_ignores_ordinary_agent_speech(text):
    _assert_no_key_material(text, "interviews.jsonl")  # must not raise


@pytest.mark.parametrize("secret", [
    "Bearer eyJhbGciOiJIUzI1NiJ9abcdef",
    "api_key=abc12345xyz",
    "authorization: tok_9f8e7d6c5b",
    "sk-abcdef1234567890xyz",
])
def test_key_guard_still_flags_tokens_without_echoing_them(secret):
    with pytest.raises(AssertionError) as exc:
        _assert_no_key_material(f"x {secret} y", "f")
    assert secret not in str(exc.value)


def test_simpleaudit_error_severity_counts_as_error(monkeypatch):
    class _Auditor:
        def set_target(self, target):
            pass

        async def run_async(self, scenarios, **kwargs):
            res = SimpleNamespace(judgment={"error": "judge returned no JSON"},
                                  severity="ERROR", issues_found=[], summary=None,
                                  positive_behaviors=[], recommendations=[], conversation=[])
            return SimpleNamespace(results=[res])

    monkeypatch.setattr(ia, "_build_auditor", lambda **kw: _Auditor())
    out = asyncio.run(ia.audit_interview(
        record={"agent_id": "a1", "tick": 1, "raw_answer": "{}", "parsed": {}},
        axis_focus="integrity", constitution_rules=None,
        base_url="http://x/v1", api_key="k", model="m",
    ))
    assert out["error"] and "judge returned no JSON" in out["error"]


@pytest.mark.parametrize("secret", [
    "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    "api_key=abcdefghijklmnopqrstuv",
    '"api_key": "abcdefghijklmnopqrstuvwx"',
    "x-api-key: abcdef12345678",
])
def test_key_guard_flags_other_real_key_shapes(secret):
    with pytest.raises(AssertionError):
        _assert_no_key_material(secret, "f")
