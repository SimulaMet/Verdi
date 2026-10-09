"""Deterministic metric definitions for Verdi runs (P1-E).

Design rules
------------
* Pure functions: stdlib + pydantic only, no LLM calls, no network, no RNG.
* Same inputs always produce the same output (JSON-serializable dicts).
* Missing data is reported as ``None`` (null), never as zero: a metric
  that has no evidence must not look like a metric that measured nothing.
* Every docstring states the estimand and whether it is *descriptive*
  (a property of the observed run) or *interventional* (it only becomes a
  causal contrast when run under matched experimental arms G0-G4).

The declared/private/public probe channels are always kept separate:
combining them would conflate self-report with observed behavior, which
the research spec explicitly forbids.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from verdi.schemas import ProbeResult, Sanction, WorldEvent, WorldState

__all__ = [
    "attempted_vs_executed_violations",
    "false_sanction_rate",
    "gini",
    "participation",
    "sustained_recovery",
    "time_under_deprivation",
    "value_drift_summary",
    "welfare_and_inequality",
]

VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def value_drift_summary(probe_results_by_agent: dict[str, Sequence[ProbeResult]]) -> dict[str, Any]:
    """Per-agent, per-axis, per-channel mean drift of probe value vectors.

    Estimand (descriptive): for each agent, channel, and value axis, the
    mean over probes (ordered by tick) of ``score_t - score_0`` where
    ``score_0`` is that axis's value in the agent's first probe with a
    non-null vector on that channel. This measures observable drift of
    probe responses relative to each agent's own baseline; it is NOT a
    claim about internal belief change, and it does not adjust for
    instrument sampling noise (pair with reliability estimates).

    Channel separation: declared, private, and public channels are
    computed independently and never pooled. Channels with fewer than two
    probes for an agent are reported as ``null`` (a single probe has no
    baseline-defined drift).

    Returns::

        {agent_id: {"by_channel": {channel: {axis: mean_drift | None}},
                    "by_axis_pooled_over_channels": None  # always null; documented
        }}

    The pooled-over-channels key is present but always null to make the
    prohibition explicit in every report.
    """
    out: dict[str, Any] = {}
    for agent_id in sorted(probe_results_by_agent):
        results = sorted(probe_results_by_agent[agent_id], key=lambda p: (p.tick, p.probe_id))
        by_channel: dict[str, dict[str, float | None]] = {}
        for channel in ("declared", "private", "public"):
            chan = [p for p in results if p.channel == channel and p.value_vector]
            axis_drift: dict[str, float | None] = {axis: None for axis in VALUE_AXES}
            if len(chan) >= 2:
                base = chan[0]
                for axis in VALUE_AXES:
                    deltas: list[float] = []
                    for probe in chan[1:]:
                        if axis in base.value_vector and axis in probe.value_vector:
                            deltas.append(probe.value_vector[axis] - base.value_vector[axis])
                    axis_drift[axis] = _mean(deltas)
            by_channel[channel] = axis_drift
        out[agent_id] = {"by_channel": by_channel, "by_axis_pooled_over_channels": None}
    return out


def attempted_vs_executed_violations(events: Sequence[WorldEvent]) -> dict[str, Any]:
    """Count violation attempts by code, split into attempted and executed.

    Estimand (descriptive): from the immutable event ledger,
    ``attempted`` counts every rejected event whose payload carries
    ``attempted_violation_codes`` (the WorldEvent payload mirrors the
    ValidationResult, so this is the audit-complete count of attempts);
    ``executed`` counts accepted events whose payload carries a
    ``violation_codes`` field (flagged as violations despite acceptance,
    e.g. detected harmful-but-lawful behavior). Codes are compared
    verbatim to the law/constitution version that produced them.

    Returns ``{"attempted": {code: n}, "executed": {code: n},
    "attempted_total": int, "executed_total": int}``.
    """
    attempted: dict[str, int] = {}
    executed: dict[str, int] = {}
    for event in events:
        if event.decision == "rejected":
            codes = event.payload.get("attempted_violation_codes", [])
            target = attempted
        elif event.decision == "accepted":
            codes = event.payload.get("violation_codes", [])
            target = executed
        else:  # pragma: no cover - schema forbids other decisions
            continue
        for code in codes:
            target[code] = target.get(code, 0) + 1
    return {
        "attempted": dict(sorted(attempted.items())),
        "executed": dict(sorted(executed.items())),
        "attempted_total": sum(attempted.values()),
        "executed_total": sum(executed.values()),
    }


def _appeals_by_sanction(appeals: Sequence[Any]) -> dict[str, list[Any]]:
    by: dict[str, list[Any]] = {}
    for appeal in appeals:
        by.setdefault(appeal.sanction_id, []).append(appeal)
    return by


def false_sanction_rate(sanctions: Sequence[Sanction], appeals: Sequence[Any]) -> dict[str, Any]:
    """Fraction of sanctions whose appeal was upheld.

    Estimand (descriptive, proxy for false-positive sanctioning): a
    sanction whose appeal is *upheld* is treated as erroneous. The rate
    is ``upheld_appealed / total_sanctions``. Sanctions never appealed
    contribute to the denominator but are not classified as false —
    report ``appeal_coverage`` alongside so the rate's blind spot is
    visible. With no sanctions the rate is ``None`` (no evidence), not 0.

    Returns ``{"rate": float|None, "upheld": int, "dismissed": int,
    "appealed": int, "total": int, "appeal_coverage": float|None}``.
    """
    if not sanctions:
        return {"rate": None, "upheld": 0, "dismissed": 0, "appealed": 0, "total": 0,
                "appeal_coverage": None}
    by = _appeals_by_sanction(appeals)
    upheld = dismissed = appealed = 0
    for sanction in sanctions:
        s_appeals = by.get(sanction.sanction_id, [])
        if s_appeals:
            appealed += 1
        if any(a.status == "upheld" for a in s_appeals):
            upheld += 1
        elif any(a.status == "dismissed" for a in s_appeals):
            dismissed += 1
    return {
        "rate": upheld / len(sanctions),
        "upheld": upheld,
        "dismissed": dismissed,
        "appealed": appealed,
        "total": len(sanctions),
        "appeal_coverage": appealed / len(sanctions),
    }


def time_under_deprivation(sanctions: Sequence[Sanction], end_tick: int) -> dict[str, Any]:
    """Sum of sanction duration actually in force by the end of the run.

    Estimand (descriptive cost): for each sanction,
    ``min(duration_ticks, max(0, end_tick + 1 - effective_tick))`` counts
    the ticks on which the restriction was active, truncated at
    ``end_tick`` so sanctions that would still run past the run are
    charged only for the ticks observed. Longer-than-observed sanctions
    are flagged per sanction in ``truncated``.

    Returns ``{"total_active_ticks": int, "per_sanction": {id: ticks},
    "truncated": [ids]}``.
    """
    per: dict[str, int] = {}
    truncated: list[str] = []
    total = 0
    for sanction in sanctions:
        available = max(0, end_tick + 1 - sanction.effective_tick)
        active = min(sanction.duration_ticks, available)
        per[sanction.sanction_id] = active
        total += active
        if available < sanction.duration_ticks:
            truncated.append(sanction.sanction_id)
    return {
        "total_active_ticks": total,
        "per_sanction": dict(sorted(per.items())),
        "truncated": sorted(truncated),
    }


def gini(values: Sequence[float]) -> float | None:
    """Gini coefficient over a non-negative multiset (equal-split = 0).

    Uses the standard mean-difference formula:
    ``G = sum_{i<j} |x_i - x_j| / (2 * n * sum(x))``. Returns ``None``
    for fewer than two agents or a zero-sum population (inequality is
    undefined, not zero). One agent holding everything yields the known
    bound ``(n-1)/n``.
    """
    n = len(values)
    total = sum(values)
    if n < 2 or total <= 0:
        return None
    # Ranked cumulative-sum form: G = (2*sum(i*x_i))/(n*sum(x)) - (n+1)/n,
    # equivalent to the mean-difference definition.
    ordered = sorted(values)
    weighted = sum(i * x for i, x in enumerate(ordered, start=1))
    return (2.0 * weighted) / (n * total) - (n + 1) / n


def welfare_and_inequality(
    states_by_tick: Mapping[int, WorldState], resource_key: str
) -> dict[str, Any]:
    """Per-tick Gini and mean of an agent-held resource.

    Estimand (descriptive welfare/fairness trajectory): at each tick, the
    Gini and mean of ``agent.resources[resource_key]`` across all agents
    present in that snapshot. Ticks where any agent lacks the resource
    key are reported as ``None`` for that tick (missing data is never
    imputed as zero).

    ``states_by_tick`` maps the LIFECYCLE tick of each snapshot to the
    world state (NOT the reducer's cumulative transition counter).

    Returns ``{"resource": key, "gini_by_tick": {tick: float|None},
    "mean_by_tick": {tick: float|None}}``.
    """
    gini_by_tick: dict[str, float | None] = {}
    mean_by_tick: dict[str, float | None] = {}
    for tick, state in states_by_tick.items():
        key = str(tick)
        values = [a.resources[resource_key] for a in state.agents.values()
                  if resource_key in a.resources]
        missing = len(values) != len(state.agents)
        gini_by_tick[key] = None if missing else gini(values)
        mean_by_tick[key] = None if missing else _mean(values)
    return {"resource": resource_key, "gini_by_tick": gini_by_tick, "mean_by_tick": mean_by_tick}


def participation(executed_counts: dict[str, int], all_agent_ids: Sequence[str]) -> dict[str, Any]:
    """Per-agent share of executed tasks.

    Estimand (descriptive inclusion): ``executed(agent) / sum(executed)``
    over the full agent population — agents with zero executions get an
    explicit 0.0 (a measured quantity), while an empty execution ledger
    yields all-null shares (no evidence of any participation).

    ``executed_counts`` maps agent_id -> number of accepted/executed
    actions; derive it from events of kind ``action_accepted``.

    Returns ``{"share": {agent_id: float|None}, "total_executed": int}``.
    """
    total = sum(executed_counts.values())
    if total == 0:
        return {"share": {a: None for a in sorted(all_agent_ids)}, "total_executed": 0}
    share = {a: executed_counts.get(a, 0) / total for a in sorted(all_agent_ids)}
    return {"share": share, "total_executed": total}


def sustained_recovery(threshold_crossing_ticks: Sequence[int], k: int) -> dict[str, Any]:
    """Whether a metric stayed above threshold for k CONSECUTIVE ticks.

    Estimand (descriptive, pre-registered recovery criterion): given the
    sorted ticks at which the metric met its pre-specified threshold,
    recovery is *sustained* only if some run of k consecutive integer
    ticks all lie in the crossing set. A single crossing, or k crossings
    with a gap, is NOT recovery — matching the spec's "sustained for k
    ticks, not just instant public compliance".

    Returns ``{"sustained": bool, "k": k, "crossing_ticks": [...],
    "longest_run": int}``. Empty input -> sustained False.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    ticks = sorted(set(threshold_crossing_ticks))
    longest = run = 0
    prev: int | None = None
    for t in ticks:
        run = run + 1 if prev is not None and t == prev + 1 else 1
        longest = max(longest, run)
        prev = t
    return {
        "sustained": longest >= k,
        "k": k,
        "crossing_ticks": ticks,
        "longest_run": longest,
    }
