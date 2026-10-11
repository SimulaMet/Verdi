"""Tranche 12d (review F1) regression tests for the live stall watchdog.

F1 (HIGH, independent review of feat/society-v2-stage-c): a full assembly
tick appends NOTHING to events.jsonl until its very end, so an
events-only watchdog threshold false-fires on legitimately long ticks. The
live C-1 attempt was killed at t15 (its first assembly) on exactly this
path. The fix adds per-model-call liveness beats
(live._LiveProgressCaller -> progress.jsonl) that reset the watchdog
deadline on every COMPLETED call, so the watchdog only trips when no call
finishes at all (a genuinely stuck socket).

These tests pin:
  * calibration: worst-case assembly tick duration (measured lower bound
    x worst observed latency) is safely below the default timeout;
  * behavior: the beat wrapper writes one monotonic beat per completed call
    and the combined-progress watchdog does NOT fire during a long
    sequential tick while beats keep flowing;
  * behavior: with the inner caller hanging, the watchdog still fires
    (the Tranche 12a bug stays covered).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from verdi.agents.client import ModelResult
from verdi.society import live as live_module

SCEN = Path(__file__).resolve().parents[2] / "experiments/scenarios/society_v2_stageC1.json"

# Observed SimulaChat Qwen3.8-27B latencies from Stage B / C-1 attempts
# (runs/society-v2/stageB-* health.json): mean 3.36-4.65 s/call,
# p95 8.9-11.5 s. Used by the F1 false-fire documentation test.
P95_CALL_SECONDS = 8.9


def _assembly_tick_lower_bound_calls(n: int) -> int:
    """Lower bound on SEQUENTIAL model calls in one n-agent assembly tick.

    Verified against campaign._run_tick order on an assembly tick:
    1. probe phases run first: n agents x 3 channels, sequential = 3n.
    2. run_deliberation: one call per agent, sequential = n.
    3. run_voting: one call per (proposal, voter) with >= 1 proposal,
       all n agents hold voting rights in these scenarios = n.
    Floor = 5n (n agents, 3 probe channels, >= 1 proposal).
    """
    return 5 * n


def test_watchdog_threshold_covers_worst_case_stuck_call(monkeypatch: pytest.MonkeyPatch):
    """With per-call beats, the watchdog deadline resets on every COMPLETED
    call, so the stall window only needs to outlast a single call that never
    finishes: timeout_seconds x (max_retries+1) is the worst-case stuck-call
    duration the client will hold before giving up. The default must exceed
    that plus one heartbeat interval, or it could trip while the client is
    still legitimately retrying one dead call."""
    monkeypatch.setenv("VERDI_VLLM_BASE_URL", "http://calibration.invalid/v1")
    monkeypatch.setenv("VERDI_VLLM_MODEL", "Qwen3.8-27B")
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("VERDI_REQUEST_TIMEOUT", "180")
    monkeypatch.setenv("VERDI_MAX_RETRIES", "5")
    from verdi.config import model_endpoint_from_env
    cfg = model_endpoint_from_env()
    # httpx total timeout bounds a normally-timed-out stuck call, but the
    # Tranche 12a bug is a held-open-never-read socket that httpx's timeout
    # does NOT fire on — only the watchdog catches that. So the window must
    # at least cover a full stuck-call retry sequence plus one heartbeat
    # interval (and in practice well beyond it, to catch the
    # timeout-immune dead socket).
    worst_stuck_call = cfg.timeout_seconds * (cfg.max_retries + 1)
    assert worst_stuck_call + live_module._HEARTBEAT_INTERVAL_SECONDS <= live_module.DEFAULT_STALL_TIMEOUT_SECONDS, (
        f"default stall timeout {live_module.DEFAULT_STALL_TIMEOUT_SECONDS}s must at least "
        f"exceed a stuck call's retry window (~{worst_stuck_call:.0f}s = "
        f"{cfg.timeout_seconds}s x {cfg.max_retries + 1} attempts) + heartbeat interval; "
        f"otherwise it could trip while the client is still legitimately retrying."
    )


def test_events_only_design_would_false_fire_on_assembly_tick():
    """Documents WHY the events-only threshold was wrong (F1): the worst-case
    duration of one n-agent assembly tick at p95 call latency exceeds the
    old 600s events-only window. This is the arithmetic that killed the live
    C-1 run at t15. (The current design avoids it because per-call beats
    reset the deadline mid-tick; this test pins the unsafe-arithmetic guard.)"""
    n = len(json.loads(SCEN.read_text())["agents"])  # 24 for C-1
    floor_calls = _assembly_tick_lower_bound_calls(n)  # 5n = 120
    p95_tick_seconds = floor_calls * P95_CALL_SECONDS
    assert p95_tick_seconds > 600, (
        f"expected the p95 {n}-agent assembly tick (~{p95_tick_seconds:.0f}s) to "
        f"exceed the old 600s events-only window; if this flips, the F1 "
        f"justification for per-call beats must be re-examined"
    )


class _SlowCaller:
    """Deterministic caller that sleeps per call (time-injected latency)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def generate_structured(self, messages, **kwargs: Any) -> ModelResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ModelResult(content="{}", request_id=f"r{self.calls}",
                           model="fake", trace_id=f"t{self.calls}")


class _HangCaller:
    """Caller whose FIRST call hangs forever (dead-socket simulation)."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, messages, **kwargs: Any) -> ModelResult:
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(3600)  # never completes
        return ModelResult(content="{}", request_id=f"r{self.calls}",
                           model="fake", trace_id=f"t{self.calls}")


def test_progress_beats_written_per_completed_call(tmp_path: Path):
    inner = _SlowCaller(delay=0.01)
    wrapper = live_module._LiveProgressCaller(inner, tmp_path)

    async def go():
        for _ in range(5):
            await wrapper.generate_structured([{"role": "user", "content": "x"}])

    asyncio.run(go())
    beats = [json.loads(line) for line in (tmp_path / live_module._PROGRESS_FILE).read_text().splitlines()]
    assert [b["seq"] for b in beats] == [1, 2, 3, 4, 5], "beats must be monotonic per call"
    assert all("ts_utc" in b for b in beats)


def test_watchdog_does_not_fire_during_long_tick_with_beats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A long sequential assembly-style tick (zero event writes) must NOT
    trip the watchdog while per-call beats keep flowing."""
    monkeypatch.setattr(live_module, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    (tmp_path / "events.jsonl").write_text("")
    inner = _SlowCaller(delay=0.01)  # 50 calls x 0.01s = 0.5s 'long tick'
    wrapper = live_module._LiveProgressCaller(inner, tmp_path)

    async def long_tick():
        for _ in range(50):
            await wrapper.generate_structured([{"role": "user", "content": "assembly work"}])

    async def go():
        watchdog = asyncio.create_task(live_module._stall_watchdog(tmp_path, 0.2))
        try:
            await long_tick()
            await asyncio.sleep(0.12)  # let a final heartbeat read the last beat
        finally:
            watchdog.cancel()

    asyncio.run(go())  # must NOT SystemExit(3)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta.get("status") != "stalled", "watchdog false-fired on a live long tick"
    # heartbeat reads may lag the final beat by one interval; the point is the
    # beats kept flowing (>=45 of 50) so the deadline kept resetting.
    assert meta.get("model_calls_beat", 0) >= 45, f"beats did not keep flowing: {meta.get('model_calls_beat')}"


def test_watchdog_still_fires_when_call_hangs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tranche 12a coverage stays: a hung in-flight call (no beats, no
    events) still trips the watchdog with exit code 3."""
    monkeypatch.setattr(live_module, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    (tmp_path / "events.jsonl").write_text("")
    inner = _HangCaller()
    wrapper = live_module._LiveProgressCaller(inner, tmp_path)
    exited: dict[str, int] = {}

    def fake_exit(code: int) -> None:
        exited["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr("os._exit", fake_exit)

    async def go():
        watchdog = asyncio.create_task(live_module._stall_watchdog(tmp_path, 0.2))
        try:
            await asyncio.gather(wrapper.generate_structured([{"role": "user", "content": "x"}]),
                                 asyncio.sleep(0.6))
        finally:
            watchdog.cancel()

    with pytest.raises(SystemExit):
        asyncio.run(go())
    assert exited.get("code") == 3
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["status"] == "stalled"
    assert meta["events_logged"] == 0
