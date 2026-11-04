"""G0/G1 tests for per-channel longitudinal drift (P1-D, construct validity)."""
from __future__ import annotations

import pytest

from verdi_agents.measurement import AxisDrift, bootstrap_ci, compute_channel_drifts
from verdi_agents.measurement.value_drift import compute_axis_drift
from verdi_agents.schemas import ProbeItem, ProbeResult


def _item(channel: str, axes: list[str]) -> ProbeItem:
    return ProbeItem(
        probe_id=f"p-{channel}",
        instrument_version="probe-v1",
        bank_version="bank-1",
        channel=channel,
        dilemma_text=f"dilemma {channel}",
        options=["yes", "no"],
        value_axes=axes,
        scoring_rubric_id="rubric-1",
    )


def _result(
    agent_id: str,
    tick: int,
    channel: str,
    vector: dict[str, float],
) -> ProbeResult:
    return ProbeResult(
        probe_id=f"p-{channel}-{tick}",
        agent_id=agent_id,
        tick=tick,
        channel=channel,
        response="yes",
        score=0.5,
        value_vector=vector,
        evidence_ref=f"ev-{channel}-{tick}",
        judge_version="rubric-1/v1.0",
    )


def _mixed_results() -> list[ProbeResult]:
    """Declared channel stable; private channel drifts up on 'solidarity'."""
    results: list[ProbeResult] = []
    for tick in (0, 5, 10):
        results.append(_result("a1", tick, "declared", {"solidarity": 0.8}))
        private_value = 0.2 if tick == 0 else 0.9
        results.append(_result("a1", tick, "private", {"solidarity": private_value}))
        results.append(_result("a1", tick, "public", {"cooperation": 0.5}))
    return results


def test_drift_keeps_channels_separate() -> None:
    results = _mixed_results()

    declared = {d.axis: d for d in compute_channel_drifts("a1", results, "declared")}
    private = {d.axis: d for d in compute_channel_drifts("a1", results, "private")}
    public = {d.axis: d for d in compute_channel_drifts("a1", results, "public")}

    # Private channel: solidarity shifts from 0.2 to 0.9.
    assert private["solidarity"].drift == pytest.approx(0.7)
    assert private["solidarity"].channel == "private"
    # Declared channel: private shift must NOT appear here.
    assert "solidarity" in declared
    assert declared["solidarity"].drift == pytest.approx(0.0)
    assert declared["solidarity"].channel == "declared"
    # Public channel only sees its own axis.
    assert set(public) == {"cooperation"}
    assert public["cooperation"].drift == pytest.approx(0.0)


def test_compute_axis_drift_single_channel_only() -> None:
    results = _mixed_results()
    # Even when the full mixed list is passed, a declared-channel query
    # ignores private data.
    drift = compute_axis_drift("a1", results, "declared", "solidarity")
    assert drift.drift == pytest.approx(0.0)
    assert drift.n_baseline == 1
    assert drift.n_terminal == 1
    with pytest.raises(ValueError):
        compute_axis_drift("a1", results, "public", "solidarity")  # public has no such axis


def test_single_tick_gives_zero_drift() -> None:
    results = [_result("a1", 3, "declared", {"fairness": 0.4})]
    drift = compute_axis_drift("a1", results, "declared", "fairness")
    assert drift.drift == 0.0
    assert (drift.ci_low, drift.ci_high) == (0.0, 0.0)


def test_bootstrap_ci_reproducible_for_fixed_seed() -> None:
    baseline = [0.2, 0.3, 0.25]
    terminal = [0.8, 0.9, 0.85]
    a = bootstrap_ci(baseline, terminal, seed=123)
    b = bootstrap_ci(baseline, terminal, seed=123)
    assert a == b
    c = bootstrap_ci(baseline, terminal, seed=124)
    assert c != a  # different seed, different resamples
    assert a[0] <= a[1]
    # CI brackets the point estimate.
    point = sum(terminal) / 3 - sum(baseline) / 3
    assert a[0] <= point <= a[1]
    with pytest.raises(ValueError):
        bootstrap_ci([], terminal)
    with pytest.raises(ValueError):
        bootstrap_ci(baseline, terminal, n_resamples=1)


def test_axis_drift_is_frozen() -> None:
    d = AxisDrift("a1", "private", "solidarity", 0.7, 0.5, 0.9, 1, 2)
    with pytest.raises(AttributeError):
        d.drift = 0.1
