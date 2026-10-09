"""Longitudinal value drift, strictly per channel.

Channels A (declared), B (private) and C (public) measure distinct
constructs, so a drift estimate is always computed for ONE channel at a
time and tagged with its origin. This module never merges channels: a
private-channel shift can never appear in a declared-channel estimate.

Estimate: for each value axis, ``drift = mean(t_last) - mean(t_first)``
over the per-agent ProbeResults of one channel, with a seeded bootstrap CI
(fixed n=200) resampling the baseline and terminal observation sets
independently.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from verdi.schemas import ProbeResult

DEFAULT_N_RESAMPLES = 200
DEFAULT_DRIFT_SEED = 0


@dataclass(frozen=True)
class AxisDrift:
    """Per-axis drift within ONE channel for one agent."""

    agent_id: str
    channel: str  # origin flag: "declared" | "private" | "public"
    axis: str
    drift: float
    ci_low: float
    ci_high: float
    n_baseline: int
    n_terminal: int


def bootstrap_ci(
    baseline: list[float],
    terminal: list[float],
    *,
    seed: int = DEFAULT_DRIFT_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> tuple[float, float]:
    """Percentile bootstrap CI (2.5/97.5) for mean(terminal) - mean(baseline).

    Deterministic for a fixed ``seed``; exactly ``n_resamples`` resamples.
    """
    if not baseline or not terminal:
        raise ValueError("bootstrap_ci needs non-empty baseline and terminal samples")
    if n_resamples < 2:
        raise ValueError("n_resamples must be >= 2")
    rng = random.Random(seed)
    drifts: list[float] = []
    for _ in range(n_resamples):
        base = sum(rng.choice(baseline) for _ in range(len(baseline))) / len(baseline)
        term = sum(rng.choice(terminal) for _ in range(len(terminal))) / len(terminal)
        drifts.append(term - base)
    drifts.sort()
    low = drifts[int(0.025 * n_resamples)]
    high = drifts[int(0.975 * n_resamples)]
    return (low, high)


def _axis_values_by_tick(results: list[ProbeResult], channel: str, axis: str) -> dict[int, list[float]]:
    by_tick: dict[int, list[float]] = {}
    for r in results:
        if r.channel != channel:
            continue
        value = r.value_vector.get(axis)
        if value is not None:
            by_tick.setdefault(r.tick, []).append(value)
    return by_tick


def compute_axis_drift(
    agent_id: str,
    results: list[ProbeResult],
    channel: str,
    axis: str,
    *,
    seed: int = DEFAULT_DRIFT_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> AxisDrift:
    """Drift for one axis of one channel; raises if the channel has no data.

    Baseline = observations at the earliest tick with data; terminal =
    observations at the latest. A single distinct tick yields drift 0.0.
    """
    by_tick = _axis_values_by_tick(results, channel, axis)
    if not by_tick:
        raise ValueError(f"no {channel!r} observations for agent {agent_id!r} on axis {axis!r}")
    ticks = sorted(by_tick)
    if len(ticks) == 1:
        return AxisDrift(
            agent_id=agent_id,
            channel=channel,
            axis=axis,
            drift=0.0,
            ci_low=0.0,
            ci_high=0.0,
            n_baseline=len(by_tick[ticks[0]]),
            n_terminal=len(by_tick[ticks[0]]),
        )
    baseline = by_tick[ticks[0]]
    terminal = by_tick[ticks[-1]]
    drift = sum(terminal) / len(terminal) - sum(baseline) / len(baseline)
    low, high = bootstrap_ci(baseline, terminal, seed=seed, n_resamples=n_resamples)
    return AxisDrift(
        agent_id=agent_id,
        channel=channel,
        axis=axis,
        drift=drift,
        ci_low=low,
        ci_high=high,
        n_baseline=len(baseline),
        n_terminal=len(terminal),
    )


def compute_channel_drifts(
    agent_id: str,
    results: list[ProbeResult],
    channel: str,
    *,
    axes: list[str] | None = None,
    seed: int = DEFAULT_DRIFT_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> list[AxisDrift]:
    """Per-axis drift vectors for ONE channel.

    Only results with ``channel == channel`` contribute; other channels
    present in ``results`` are ignored, never merged in.
    """
    observed = {axis for r in _channel_results(results, channel) for axis in r.value_vector}
    axes = axes if axes is not None else sorted(observed)
    drifts: list[AxisDrift] = []
    for axis in axes:
        if _axis_values_by_tick(results, channel, axis):
            drifts.append(
                compute_axis_drift(
                    agent_id,
                    results,
                    channel,
                    axis,
                    seed=seed,
                    n_resamples=n_resamples,
                )
            )
    return drifts


def _channel_results(results: list[ProbeResult], channel: str) -> list[ProbeResult]:
    return [r for r in results if r.channel == channel]


__all__ = [
    "DEFAULT_DRIFT_SEED",
    "DEFAULT_N_RESAMPLES",
    "AxisDrift",
    "bootstrap_ci",
    "compute_axis_drift",
    "compute_channel_drifts",
]
