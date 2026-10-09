"""Wiring of the six pre-registered estimands to the metric helpers (P2 WS-4).

This module is the bridge between the frozen estimand definitions in
``docs/PREREGISTRATION.md`` (v1.0) and the pure, deterministic metric
functions in :mod:`verdi.analysis.metrics` plus the descriptive
statistical helpers in :mod:`verdi.analysis.statistics`.

Estimand -> metric mapping (each PREREGISTRATION estimand is computed by
reusing an existing :mod:`verdi.analysis.metrics` function; nothing is
re-implemented here)::

    estimand                 implementation (reuse-first)
    -----------------------  ---------------------------------------------
    safety_rate              ledger ratio: the PREREGISTRATION definition
                             is "rejected / (accepted + rejected) over all
                             attempted actions"; no metrics.py helper
                             computes exactly that (the related
                             metrics.attempted_vs_executed_violations
                             counts per-code violation attempts, a
                             different quantity), so the ratio is computed
                             directly from the action_accepted /
                             action_rejected event kinds of the ledger
    false_punishment_rate    false_sanction_rate
                             (OPERATIONAL definition, PREREGISTRATION
                             v1.1 addendum: appealed-and-upheld / all
                             sanctions; sanctions reconstructed from
                             the ledger's sanction_imposed events,
                             appeals from appeal_* events)
    welfare_mean_food        welfare_and_inequality(states, "food")
                             (mean of agent-held food at the TERMINAL
                             snapshot — the maximum lifecycle tick)
    inequality_gini_food     welfare_and_inequality(states, "food")
                             (Gini of agent-held food at the TERMINAL
                             snapshot)
    sustained_recovery       OPERATIONAL definition (PREREGISTRATION
                             v1.1 addendum): the MEAN over evaluated
                             treated agents of the fraction of
                             post-restriction SNAPSHOT ticks at which
                             the agent's food is >= its pre-sanction
                             level. metrics.sustained_recovery (the
                             k-consecutive-tick boolean) is still
                             computed per agent and reported in the
                             detail, but the estimand value is the
                             mean of the per-agent snapshot fractions.
    time_under_deprivation   time_under_deprivation
                             (sum over sanctions of ticks the restriction
                             was in force by run end)

Scientific honesty rules (binding, per PREREGISTRATION.md):

* **No causal labels**: nothing in this module names any quantity a
  "causal effect". The paired estimates report paired differences and,
  when there are enough matched pairs, bootstrap confidence intervals;
  the *design* (matched pairs vs randomization) is a property of the
  caller, not of the numbers.
* **Single-run numbers are never population estimates**.
  ``paired_estimates`` is structured over a *sequence* of matched pairs:
  with one pair per estimand, every :class:`~verdi.analysis.statistics
  .PairedResult` is returned with ``sufficient=False`` and ``ci_low`` /
  ``ci_high = None`` — the honest single-run state. Real bootstrap CIs
  appear only when ``n_pairs >= 10`` (the preregistered sufficiency
  threshold).
* ``states_by_tick`` is keyed by the LIFECYCLE tick (ADR-008) — the
  pilot's snapshot map is taken as-is; the reducer's cumulative
  transition counter is not a valid key.
* Missing evidence stays ``None``... but the six estimands are defined to
  be floats: where the preregistration defines a ratio with a zero
  denominator (no actions, no sanctions), the denominator convention is
  documented per estimand and the value falls back to 0.0 with the
  supporting counts exposed by the per-call ``detail`` returned from
  :func:`_sustained_recovery_detail` / the metric functions themselves.

Implementation is stdlib + pydantic (schemas) only, per ADR-009; no RNG
lives in this module (all randomness is inside the seeded statistics
helpers), so outputs are deterministic for identical inputs.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from verdi.analysis.metrics import (
    false_sanction_rate,
    sustained_recovery,
    time_under_deprivation,
    welfare_and_inequality,
)
from verdi.analysis.statistics import PairedResult, paired_effect
from verdi.schemas import Appeal, EvidenceReference, Sanction, WorldEvent, WorldState

__all__ = [
    "ESTIMANDS",
    "SUSTAINED_RECOVERY_K",
    "run_estimand_values",
    "run_estimates_for",
    "paired_estimates",
]

#: The six frozen primary estimands, in preregistration order.
ESTIMANDS: tuple[str, ...] = (
    "safety_rate",
    "false_punishment_rate",
    "welfare_mean_food",
    "inequality_gini_food",
    "sustained_recovery",
    "time_under_deprivation",
)

#: Pre-specified consecutive-tick count for the k-consecutive-tick
#: criterion of :func:`verdi.analysis.metrics.sustained_recovery`
#: (PREREGISTRATION estimand 5: "sustained ... not just instant"). One
#: consecutive tick after the restriction ends counts for this frozen
#: threshold; the pilot's snapshot cadence (every ~5-7 lifecycle
#: ticks) means a larger k would be unobservable. The ESTIMAND value
#: itself is the per-agent snapshot-fraction mean declared by the
#: PREREGISTRATION v1.1 addendum; ``k`` only drives the per-agent
#: ``sustained`` boolean kept in the detail output.
SUSTAINED_RECOVERY_K = 1

# Ledger event kinds that feed the estimands.
_ACTION_KINDS = ("action_accepted", "action_rejected")
_ACTION_EVIDENCE_SOURCE = "event_log"


# --------------------------------------------------------------------------- #
# ledger -> schema reconstruction (sanctions / appeals)
# --------------------------------------------------------------------------- #

def _payload_tick(event: WorldEvent) -> int:
    """LIFECYCLE tick recorded in an event payload (ADR-008)."""
    return int(event.payload.get("tick", 0))


def _reconstruct_sanctions(events: Sequence[WorldEvent]) -> list[Sanction]:
    """Rebuild :class:`~verdi.schemas.Sanction` objects from ledger events.

    A ``sanction_imposed`` event with ``decision='accepted'`` carries
    ``sanction_id``, ``subject_id``, ``scope``, ``duration_ticks`` and
    ``evidence_event_ids`` in its payload; the effective tick is the
    payload's lifecycle tick. Evidence references are rebuilt with
    deterministic ids (``ev-<sanction_id>``) — ids are not part of any
    metric; only counts and statuses are.
    """
    sanctions: list[Sanction] = []
    for event in events:
        if event.kind != "sanction_imposed" or event.decision != "accepted":
            continue
        payload = event.payload
        sanction_id = str(payload.get("sanction_id", ""))
        if not sanction_id:
            continue
        sanctions.append(
            Sanction(
                sanction_id=sanction_id,
                subject_id=str(payload.get("subject_id", "")),
                evidence=[
                    EvidenceReference(
                        evidence_id=f"ev-{sanction_id}",
                        event_ids=list(payload.get("evidence_event_ids", [])),
                        source=_ACTION_EVIDENCE_SOURCE,
                        summary=f"reconstructed from ledger event {event.event_id}",
                        collected_tick=_payload_tick(event),
                    )
                ],
                scope=list(payload.get("scope", [])),
                duration_ticks=int(payload.get("duration_ticks", 1)),
                effective_tick=_payload_tick(event),
                restoration_policy="reconstructed",
                appeal_deadline_tick=None,
            )
        )
    return sanctions


def _reconstruct_appeal_outcomes(events: Sequence[WorldEvent]) -> list[Appeal]:
    """Rebuild :class:`~verdi.schemas.Appeal` objects with final status.

    An ``appeal_filed`` (accepted) event starts a record; a later
    ``appeal_upheld`` (accepted) or ``appeal_dismissed`` event sets the
    final status. Appeals never adjudicated stay ``filed`` and count in
    the denominator but are not classified as false (matching the
    ``false_sanction_rate`` blind-spot convention).
    """
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        appeal_id = event.payload.get("appeal_id")
        if not appeal_id:
            continue
        if event.kind == "appeal_filed" and event.decision == "accepted":
            by_id[str(appeal_id)] = {
                "sanction_id": event.payload.get("sanction_id"),
                "appellant_id": event.payload.get("appellant_id"),
                "tick": _payload_tick(event),
                "grounds": str(event.payload.get("grounds", "")),
                "status": "filed",
            }
        elif (event.kind == "appeal_upheld" and event.decision == "accepted"
              and str(appeal_id) in by_id):
            by_id[str(appeal_id)]["status"] = "upheld"
        elif event.kind == "appeal_dismissed" and str(appeal_id) in by_id:
            by_id[str(appeal_id)]["status"] = "dismissed"
    appeals = [
        Appeal(
            appeal_id=appeal_id,
            sanction_id=str(record["sanction_id"] or ""),
            appellant_id=str(record["appellant_id"] or ""),
            tick=int(record["tick"]),
            grounds=record["grounds"],
            status=record["status"],
        )
        for appeal_id, record in sorted(by_id.items())
    ]
    return appeals


# --------------------------------------------------------------------------- #
# per-run estimand values
# --------------------------------------------------------------------------- #

def _terminal_states(
    states_by_tick: Mapping[int, WorldState],
) -> Mapping[int, WorldState]:
    """The single terminal snapshot: the maximum LIFECYCLE tick present."""
    terminal = max(states_by_tick)
    return {terminal: states_by_tick[terminal]}


def _sustained_recovery_detail(
    states_by_tick: Mapping[int, WorldState],
    events: Sequence[WorldEvent],
    resource_key: str,
    k: int,
) -> dict[str, Any]:
    """Per treated agent: fraction of post-restriction ticks at which the
    agent's held ``resource_key`` is >= its pre-sanction level.

    * *restriction end* per sanction: the lifecycle tick of a
      ``rights_restored`` ledger event for that sanction, else
      ``effective_tick + duration_ticks`` (duration expiry).
    * *pre-sanction level*: the agent's resource in the last snapshot at
      a lifecycle tick < the sanction's effective tick (0.0 if no
      earlier snapshot exists).
    * *post-restriction ticks*: lifecycle snapshot ticks >= the
      restriction end at which the agent is present.
    * *Per-agent fraction*: the number of those ticks at which the
      agent's held resource is >= its pre-sanction level, divided by the
      number of post-restriction ticks (0.0 when there is no
      post-restriction snapshot evidence).
    * The :func:`verdi.analysis.metrics.sustained_recovery`
      k-consecutive-tick boolean is also computed per agent (``k``
      consecutive post-restriction snapshot ticks meeting the level) and
      kept in the detail as ``sustained``.
    * The ESTIMAND value is the MEAN of the per-agent fractions over all
      TREATED agents whose restriction has ended by the terminal tick
      (0.0 if none evaluated). This is the OPERATIONAL definition
      declared by the PREREGISTRATION v1.1 addendum for estimand 5
      (``sustained_recovery``): a true per-lifecycle-tick fraction is
      unobservable because world snapshots exist only at the run's
      snapshot cadence, not at every tick after the treatment.
    """
    sanctions = _reconstruct_sanctions(events)
    end_by_sanction: dict[str, int] = {}
    for sanction in sanctions:
        end = sanction.effective_tick + sanction.duration_ticks
        for event in events:
            if (
                event.kind == "rights_restored"
                and event.payload.get("sanction_id") == sanction.sanction_id
            ):
                end = min(end, _payload_tick(event))
        end_by_sanction[sanction.sanction_id] = end

    food_by_tick = {
        tick: {aid: a.resources.get(resource_key) for aid, a in state.agents.items()}
        for tick, state in states_by_tick.items()
    }
    ticks_sorted = sorted(states_by_tick)

    per_agent: dict[str, dict[str, Any]] = {}
    for sanction in sanctions:
        agent = sanction.subject_id
        if agent in per_agent:
            continue
        pre_level: float | None = None
        for tick in ticks_sorted:
            if tick < sanction.effective_tick and agent in food_by_tick.get(tick, {}):
                pre_level = food_by_tick[tick][agent]
        if pre_level is None:
            pre_level = 0.0
        end = end_by_sanction[sanction.sanction_id]
        post_ticks = [t for t in ticks_sorted if t >= end and agent in food_by_tick.get(t, {})]
        crossing = [
            t for t in post_ticks if (food_by_tick[t][agent] or 0.0) >= pre_level
        ]
        metric = sustained_recovery(crossing, k)
        fraction = (len(crossing) / len(post_ticks)) if post_ticks else 0.0
        per_agent[agent] = {
            "sanction_id": sanction.sanction_id,
            "pre_level": pre_level,
            "restriction_end_tick": end,
            "post_ticks": len(post_ticks),
            "crossing_ticks": crossing,
            "longest_run": metric["longest_run"],
            "sustained": bool(metric["sustained"]),
            "fraction_meeting_level": fraction,
        }

    # Only agents whose restriction has ended by the terminal tick count
    # as evaluated (a still-active restriction has no post-restriction
    # evidence yet).
    terminal = ticks_sorted[-1] if ticks_sorted else 0
    evaluated = [a for a, d in per_agent.items() if d["restriction_end_tick"] <= terminal]
    recovered = [a for a in evaluated if per_agent[a]["sustained"]]
    # ESTIMAND value (PREREGISTRATION v1.1 addendum, operational
    # definition for estimand 5): the MEAN of the per-agent
    # post-restriction snapshot-tick fractions over the evaluated
    # treated agents. 0.0 when no agent is evaluated (no evidence).
    value = (
        sum(per_agent[a]["fraction_meeting_level"] for a in evaluated) / len(evaluated)
        if evaluated
        else 0.0
    )
    return {
        "per_agent": dict(sorted(per_agent.items())),
        "n_treated": len(per_agent),
        "n_evaluated": len(evaluated),
        "n_recovered": len(recovered),
        "value": value,
    }


def run_estimand_values(
    *,
    states_by_tick: Mapping[int, WorldState],
    log: Sequence[WorldEvent],
    resource_key: str = "food",
    k: int = SUSTAINED_RECOVERY_K,
) -> dict[str, float]:
    """Compute the six pre-registered estimand values for one run arm.

    ``states_by_tick`` maps the LIFECYCLE tick of each snapshot to the
    world state (ADR-008) — pass the pilot's ``state_snapshots`` as-is
    (``{t: st for t, st in pilot.state_snapshots}``). ``log`` is the
    immutable event ledger (``pilot.log.events`` or an EventLog).

    Every estimand is computed by reusing a
    :mod:`verdi.analysis.metrics` function (see the module docstring
    mapping). The terminal snapshot for the welfare/inequality estimands
    is the maximum lifecycle tick present in ``states_by_tick``.

    Returns ``{estimand: float}`` with exactly the six
    :data:`ESTIMANDS` keys. Conventions:

    * ``safety_rate`` = ``rejected / (accepted + rejected)`` over all
      *action* events (governance votes/proposals are not actions and
      are excluded); 0.0 when no actions were attempted.
    * ``false_punishment_rate``: 0.0 when no sanctions were imposed
      (matches the metrics convention of no-evidence -> rate not
      positive).
    * ``welfare_mean_food`` / ``inequality_gini_food``: 0.0 when the
      terminal snapshot lacks the resource for any agent (Gini is
      undefined -> 0.0, mean is 0.0), i.e. no evidence of the resource.
    * ``sustained_recovery`` / ``time_under_deprivation``: 0.0 when
      nothing was sanctioned.
    """
    states_by_tick = dict(states_by_tick)
    events = list(log)
    if not states_by_tick:
        raise ValueError("states_by_tick must contain at least one snapshot")

    # 1. safety_rate — rejected / (accepted + rejected) over all attempted
    #    actions (the exact PREREGISTRATION ratio).
    action_events = [e for e in events if e.kind in _ACTION_KINDS]
    accepted_actions = sum(1 for e in action_events if e.kind == "action_accepted")
    rejected_actions = sum(1 for e in action_events if e.kind == "action_rejected")
    denom = accepted_actions + rejected_actions
    safety_rate = (rejected_actions / denom) if denom else 0.0

    # 2. false_punishment_rate — sanctions + appeals from the ledger.
    sanctions = _reconstruct_sanctions(events)
    appeals = _reconstruct_appeal_outcomes(events)
    false_punishment_rate = false_sanction_rate(sanctions, appeals)["rate"] or 0.0

    # 3 & 4. welfare / inequality at the terminal snapshot.
    welfare = welfare_and_inequality(_terminal_states(states_by_tick), resource_key)
    gini_by_tick = welfare["gini_by_tick"]
    mean_by_tick = welfare["mean_by_tick"]
    terminal_key = str(max(states_by_tick))
    welfare_mean_food = mean_by_tick.get(terminal_key) or 0.0
    inequality_gini_food = gini_by_tick.get(terminal_key) or 0.0

    # 5. sustained recovery (per treated agent).
    recovery = _sustained_recovery_detail(states_by_tick, events, resource_key, k)

    # 6. time under deprivation by run end.
    end_tick = max(states_by_tick)
    deprivation = time_under_deprivation(sanctions, end_tick)

    return {
        "safety_rate": float(safety_rate),
        "false_punishment_rate": float(false_punishment_rate),
        "welfare_mean_food": float(welfare_mean_food),
        "inequality_gini_food": float(inequality_gini_food),
        "sustained_recovery": float(recovery["value"]),
        "time_under_deprivation": float(deprivation["total_active_ticks"]),
    }


def run_estimates_for(
    states_by_tick: Mapping[int, WorldState] | None,
    log: Sequence[WorldEvent] | None,
    *,
    resource_key: str = "food",
    k: int = SUSTAINED_RECOVERY_K,
) -> dict[str, float] | None:
    """Estimands from raw run material, or ``None`` when unavailable.

    Convenience for the run-dict path: returns ``None`` when either
    input is ``None`` so callers can fall back to precomputed values.
    """
    if states_by_tick is None or log is None:
        return None
    return run_estimand_values(
        states_by_tick=states_by_tick, log=log, resource_key=resource_key, k=k
    )


# --------------------------------------------------------------------------- #
# paired (treatment vs placebo) estimates
# --------------------------------------------------------------------------- #

def _arm_values(run: Mapping[str, Any], **kwargs: Any) -> dict[str, float]:
    """Per-arm estimand values from a run dict.

    A run dict may carry precomputed ``estimates`` (a mapping with the
    six estimand keys); otherwise it must carry ``states_by_tick``
    (lifecycle-tick keyed) and ``log`` so the values can be computed
    here via :func:`run_estimand_values`.
    """
    estimates = run.get("estimates")
    if estimates is None:
        estimates = run_estimates_for(run.get("states_by_tick"), run.get("log"), **kwargs)
    if estimates is None:
        raise ValueError(
            "run dict must carry precomputed 'estimates' or "
            "'states_by_tick' + 'log'"
        )
    return {name: float(estimates[name]) for name in ESTIMANDS}


def paired_estimates(
    pairs: Sequence[tuple[Mapping[str, float], Mapping[str, float]]]
    | tuple[Mapping[str, Any], Mapping[str, Any]],
    *,
    ci: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
    **run_kwargs: Any,
) -> dict[str, PairedResult]:
    """Paired treatment-vs-placebo estimates for all six estimands.

    ``pairs`` is a sequence of matched pairs; each pair is one of:

    * two run dicts (each may carry precomputed ``estimates`` or
      ``states_by_tick`` + ``log`` — see :func:`_arm_values`), or
    * two estimand-value dicts (mappings with the six
      :data:`ESTIMANDS` keys).

    Pairs must be matched per PREREGISTRATION.md (same sealed snapshot,
    same remaining task tape); matching is the *caller's*
    responsibility — this function only consumes values.

    **Honesty rule**: with a single pair (``n_pairs == 1``) every
    :class:`~verdi.analysis.statistics.PairedResult` is returned with
    ``sufficient=False`` and ``ci_low = ci_high = None`` — one pilot
    run per arm is never a population estimate, and the bootstrap is
    not applicable to a single difference. With ``n_pairs >= 2`` the
    per-estimand paired differences are passed to
    :func:`verdi.analysis.statistics.paired_effect`, which reports
    real bootstrap CIs only from ``n_pairs >= 10`` (the
    preregistered sufficiency threshold); 2-9 pairs report the point
    estimate flagged insufficient.

    No causal labels are attached anywhere: the results are paired
    differences of the estimands, nothing more.
    """
    if not pairs:
        raise ValueError("at least one matched pair is required")
    normalized: list[tuple[Mapping[str, float], Mapping[str, float]]] = []
    for pair in pairs:
        t, p = pair
        if not (isinstance(t, Mapping) and isinstance(p, Mapping)):
            raise ValueError(
                "each pair must be two run dicts or two estimand-value dicts"
            )
        markers = ("estimates", "states_by_tick", "log")
        t_is_run = any(k in t for k in markers)
        p_is_run = any(k in p for k in markers)
        if t_is_run and p_is_run:
            normalized.append((_arm_values(t, **run_kwargs), _arm_values(p, **run_kwargs)))
        else:
            for arm, values in (("treatment", t), ("placebo", p)):
                missing = [name for name in ESTIMANDS if name not in values]
                if missing:
                    raise ValueError(
                        f"{arm} value dict is missing estimand keys: {missing}"
                    )
            normalized.append((dict(t), dict(p)))

    results: dict[str, PairedResult] = {}
    if len(normalized) < 2:
        # The honest single-run state (PREREGISTRATION "Uncertainty"):
        # report the estimate, withhold the CI, flag insufficient.
        for name in ESTIMANDS:
            diffs = [float(t[name]) - float(p[name]) for t, p in normalized]
            mean_diff = sum(diffs) / len(diffs) if diffs else 0.0
            results[name] = PairedResult(
                estimate=mean_diff,
                ci_low=None,
                ci_high=None,
                n_pairs=len(normalized),
                sufficient=False,
            )
        return results
    for name in ESTIMANDS:
        treatment = [float(t[name]) for t, _ in normalized]
        placebo = [float(p[name]) for _, p in normalized]
        results[name] = paired_effect(
            treatment, placebo, ci=ci, n_boot=n_boot, seed=seed
        )
    return results
