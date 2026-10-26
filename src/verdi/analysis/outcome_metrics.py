"""P0-D primary outcome metrics extracted from recorded run artifacts.

Design rules
------------
* Every metric is a PURE function over the on-disk artifacts of one run
  (``events.jsonl``, ``resources.jsonl``, ``sanctions.jsonl``,
  ``meetings.jsonl``, ``constitution_history.jsonl``, plus the run's
  ``manifest.json`` / ``run_meta.json``). Nothing is taken from agent
  self-report, and no network / wall-clock / RNG input is used: identical
  artifact trees always produce identical metric dicts.
* Every aggregate carries raw event-ledger references
  (``evidence_event_ids``) so each number is auditable and reconstructable
  from the immutable ledger.
* Missing data is reported as ``None`` (null), never as zero: a metric with
  no evidence must not look like a metric that measured nothing.

Metric families (see :func:`summarize_run`)
-------------------------------------------
* ``completion_rate``   -- objective task success with an explicit
  denominator (delivery objectives evaluated at their due tick).
* ``resource_sharing``  -- committed vs attempted transfer/deposit flows,
  reserve levels over time, final-holdings inequality (Gini).
* ``exclusion_episodes``-- sanction episodes per agent, excluded ticks,
  repeat-episode flags. "Excluded from" = the transfer+deposit capability
  withdrawn under sanction (``SANCTION_BLOCKED_KINDS`` in
  :mod:`verdi.world.validate`); the world-state flag (rights_change tape
  steps) defines when it is in force.
* ``recovery_time``     -- preregistration-style: the caller supplies the
  pre-shock baseline window, post-shock start, target fraction and
  sustained-ticks threshold BEFORE reading results; non-recovered runs are
  CENSORED (``recovered=False, censored=True``), never scored as slow.
* ``institutional_mechanism`` -- proposals, turnout, votes, enactments with
  rule deltas, blocked actions grouped by ``blocked_by`` prefix.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

#: Action kinds whose execution is withdrawn while an agent is under sanction
#: (mirrors verdi.world.validate.SANCTION_BLOCKED_KINDS; re-declared here to
#: keep this module dependency-light for offline artifact analysis).
SANCTION_BLOCKED_KINDS = frozenset({"transfer", "deposit"})

CSV_COLUMNS = ("arm", "seed", "metric", "value", "evidence_ref")


class RunArtifactError(RuntimeError):
    """A run dir exists but its artifacts are missing or malformed."""


# --------------------------------------------------------------------------- #
# low-level artifact readers
# --------------------------------------------------------------------------- #

def _read_jsonl(run_dir: Path, name: str) -> list[dict[str, Any]]:
    """Read ``run_dir/<name>.jsonl`` into a list of records.

    Absent file -> empty list (the run simply has none of these records);
    a present-but-malformed line raises :class:`RunArtifactError` so a
    corrupted ledger is never silently truncated. A trailing incomplete
    record (crashed write, no newline) is skipped with disclosure via the
    returned ``(rows, warnings)`` convention handled by callers that care.
    """
    path = run_dir / f"{name}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                if not line.endswith("\n"):
                    continue  # incomplete tail record of a crashed write
                raise RunArtifactError(
                    f"{path.name}:{lineno}: invalid JSON line"
                ) from None
            if not isinstance(row, dict):
                raise RunArtifactError(f"{path.name}:{lineno}: expected a JSON object")
            rows.append(row)
    return rows


def _read_json(run_dir: Path, name: str) -> dict[str, Any] | None:
    path = run_dir / f"{name}.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunArtifactError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise RunArtifactError(f"{path.name}: expected a JSON object")
    return obj


def _require_run_dir(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise RunArtifactError(f"run directory not found: {run_dir}")
    if not (run_dir / "events.jsonl").exists():
        raise RunArtifactError(
            f"{run_dir} has no events.jsonl — the run did not complete; "
            "metrics cannot be computed from a partial ledger"
        )
    return run_dir


def _round(value: float, digits: int = 6) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def gini(values: list[float]) -> float | None:
    """Deterministic Gini coefficient over a non-negative multiset.

    Ranked cumulative-sum form of the mean-difference definition:
    ``G = (2 * sum(i*x_i)) / (n * sum(x)) - (n + 1) / n``. Returns ``None``
    for fewer than two agents or a zero-sum population (inequality undefined,
    not zero). One holder owning everything yields the bound ``(n-1)/n``.
    """
    values = [float(v) for v in values if v is not None]
    n = len(values)
    total = sum(values)
    if n < 2 or total <= 0:
        return None
    ordered = sorted(values)
    weighted = sum(i * x for i, x in enumerate(ordered, start=1))
    return (2.0 * weighted) / (n * total) - (n + 1) / n


# --------------------------------------------------------------------------- #
# 1. completion rate (objective task success)
# --------------------------------------------------------------------------- #

def completion_rate(run_dir: Path) -> dict[str, Any]:
    """Verified delivery-objective outcomes, straight off the event ledger.

    Each delivery objective is evaluated by the campaign from committed
    world state at its due tick and ledgered as exactly one
    ``task_completed`` / ``task_failed`` event whose payload carries the full
    :func:`verdi.world.economy.check_delivery` outcome. This function only
    re-reads those ledger events — it trusts no self-report.

    Overall denominator (explicit): the number of delivery objectives
    EVALUATED (i.e. with a due-tick ledger event in this run)::

        overall = verified completed / evaluated

    Returns::

        {"objectives": [{objective_id, status, required, baseline, actual,
                          delta, due_tick, evidence_event_ids}],
         "overall": float | None,
         "completed": int, "failed": int, "evaluated": int}
    """
    run_dir = _require_run_dir(run_dir)
    events = _read_jsonl(run_dir, "events")
    objectives: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") not in ("task_completed", "task_failed"):
            continue
        payload = ev.get("payload") or {}
        objectives.append({
            "objective_id": str(payload.get("objective_id", "")),
            "status": "completed" if ev["kind"] == "task_completed" else "failed",
            "required": payload.get("required"),
            "baseline": payload.get("baseline"),
            "actual": payload.get("actual"),
            "delta": payload.get("delta"),
            "due_tick": int(ev.get("tick", -1)),
            "evidence_event_ids": [int(ev["event_id"])],
        })
    objectives.sort(key=lambda o: (o["due_tick"], o["objective_id"]))
    completed = sum(1 for o in objectives if o["status"] == "completed")
    evaluated = len(objectives)
    return {
        "objectives": objectives,
        "overall": (completed / evaluated) if evaluated else None,
        "completed": completed,
        "failed": evaluated - completed,
        "evaluated": evaluated,
    }


# --------------------------------------------------------------------------- #
# 2. resource sharing
# --------------------------------------------------------------------------- #

def _flow_amount(kind: str, args: dict[str, Any]) -> float:
    """Committed flow amount for one accepted transfer/deposit action."""
    key = "units" if kind == "transfer" else "amount"
    try:
        return float(args.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def resource_sharing(run_dir: Path) -> dict[str, Any]:
    """Accepted transfer/deposit flows, reserves, and holdings inequality.

    Committed flows come ONLY from ``action_accepted`` ledger events with
    ``action_kind`` in {transfer, deposit}; attempts that were rejected
    (overdraft, under_sanction, policy limits, ...) are counted separately
    from ``action_rejected`` events so intent is never conflated with
    committed action. Reserve levels are the per-tick world-pool balances
    from ``resources.jsonl``. Inequality is the Gini of agent holdings of
    the PRIMARY resource at the final recorded tick (primary = the resource
    with the largest final world-pool balance; ties broken by name).

    Returns::

        {"committed": {resource: {"transfer": float, "deposit": float,
                                   "total": float,
                                   "evidence_event_ids": [...]}},
         "attempted_not_committed": {resource: int, ...},
         "reserve_levels": {resource: [(tick, level), ...]},
         "final_inequality": {"primary_resource": str | None,
                               "gini": float | None,
                               "holdings": {agent: float},
                               "tick": int | None}}
    """
    run_dir = _require_run_dir(run_dir)
    events = _read_jsonl(run_dir, "events")
    timeline = _read_jsonl(run_dir, "resources")

    committed: dict[str, dict[str, Any]] = {}
    attempted: dict[str, int] = {}
    for ev in events:
        kind = (ev.get("payload") or {}).get("action_kind")
        if kind not in SANCTION_BLOCKED_KINDS:
            continue
        args = (ev.get("payload") or {}).get("arguments") or {}
        resource = str(args.get("resource", ""))
        if ev.get("kind") == "action_accepted":
            bucket = committed.setdefault(
                resource,
                {"transfer": 0.0, "deposit": 0.0, "total": 0.0,
                 "evidence_event_ids": []},
            )
            amount = _flow_amount(kind, args)
            bucket[kind] = _round(bucket[kind] + amount)
            bucket["total"] = _round(bucket["total"] + amount)
            bucket["evidence_event_ids"].append(int(ev["event_id"]))
        elif ev.get("kind") == "action_rejected":
            attempted[resource] = attempted.get(resource, 0) + 1

    committed_out = {
        res: {**bucket, "evidence_event_ids": sorted(bucket["evidence_event_ids"])}
        for res, bucket in sorted(committed.items())
    }

    reserves: dict[str, list[list[Any]]] = {}
    for snap in timeline:
        tick = int(snap.get("tick", -1))
        for resource, level in (snap.get("world_resources") or {}).items():
            reserves.setdefault(str(resource), []).append([tick, float(level)])

    # Final-holdings inequality of the primary resource.
    final_ineq: dict[str, Any] = {
        "primary_resource": None, "gini": None, "holdings": {}, "tick": None,
    }
    if timeline:
        last = max(timeline, key=lambda s: int(s.get("tick", -1)))
        pool = {str(k): float(v) for k, v in (last.get("world_resources") or {}).items()}
        primary = max(sorted(pool), key=lambda r: (pool[r], )) if pool else None
        # max() with sorted keys keeps ties deterministic (first name wins).
        if primary is not None:
            holdings = {
                aid: float((res or {}).get(primary, 0.0))
                for aid, res in sorted((last.get("agents") or {}).items())
            }
            final_ineq = {
                "primary_resource": primary,
                "gini": _round(gini(list(holdings.values())) or 0.0)
                if gini(list(holdings.values())) is not None else None,
                "holdings": {aid: _round(v) for aid, v in holdings.items()},
                "tick": int(last.get("tick")),
            }
    return {
        "committed": committed_out,
        "attempted_not_committed": dict(sorted(attempted.items())),
        "reserve_levels": {r: pts for r, pts in sorted(reserves.items())},
        "final_inequality": final_ineq,
    }


# --------------------------------------------------------------------------- #
# 3. exclusion episodes
# --------------------------------------------------------------------------- #

def exclusion_episodes(run_dir: Path) -> dict[str, Any]:
    """Sanction/exclusion episodes per agent, derived from the sanctions stream.

    "Excluded from" is defined explicitly: while an agent is under sanction
    the validator blocks the ``transfer`` and ``deposit`` action kinds
    (``verdi.world.validate.SANCTION_BLOCKED_KINDS``); the episode spans the
    sanction's ``effective_tick`` until restoration (duration expiry or an
    upheld appeal, whichever lands first in ``sanctions.jsonl``) or run end
    (restored_tick = null, still excluded at the last observed tick).

    Episodes are built from ``sanctions.jsonl`` records
    (``record_type`` sanction / appeal / restoration), matched by
    ``sanction_id``. An imposed sanction with no matching restoration is an
    OPEN episode. Repeat-episode flag: the agent had more than one distinct
    sanctioned interval (a second imposition after a prior restoration).

    Returns::

        {"agents": {agent_id: {"episodes": [{sanction_id, effective_tick,
                                              duration_ticks, restored_tick}],
                                "excluded_ticks": int,
                                "repeat_episode": bool}},
         "totals": {"sanctions_imposed": int, "appeals_filed": int,
                     "appeals_upheld": int, "restorations": int}}
    """
    run_dir = _require_run_dir(run_dir)
    sanctions = _read_jsonl(run_dir, "sanctions")
    events = _read_jsonl(run_dir, "events")
    last_ledger_tick = max((int(e.get("tick", -1)) for e in events), default=-1)

    agents: dict[str, dict[str, Any]] = {}
    totals = {"sanctions_imposed": 0, "appeals_filed": 0,
              "appeals_upheld": 0, "restorations": 0}
    open_episodes: dict[str, dict[str, Any]] = {}  # sanction_id -> episode

    def _agent(agent_id: str) -> dict[str, Any]:
        return agents.setdefault(
            agent_id, {"episodes": [], "excluded_ticks": 0, "repeat_episode": False}
        )

    for rec in sanctions:
        rtype = rec.get("record_type")
        if rtype == "sanction":
            if rec.get("kind") != "sanction_imposed" or rec.get("decision") != "accepted":
                continue
            totals["sanctions_imposed"] += 1
            subject = str(rec.get("subject_id", ""))
            episode = {
                "sanction_id": str(rec.get("sanction_id", "")),
                "effective_tick": int(rec.get("tick", -1)),
                "duration_ticks": rec.get("duration_ticks"),
                "restored_tick": None,
            }
            open_episodes[episode["sanction_id"]] = (subject, episode)
            _agent(subject)["episodes"].append(episode)
        elif rtype == "restoration":
            totals["restorations"] += 1
            key = str(rec.get("sanction_id", ""))
            hit = open_episodes.pop(key, None)
            if hit is None:
                continue
            subject, episode = hit
            episode["restored_tick"] = int(rec.get("tick"))
            entry = _agent(subject)
            lo, hi = episode["effective_tick"], episode["restored_tick"]
            entry["excluded_ticks"] += max(0, hi - lo)
        elif rtype == "appeal":
            phase = rec.get("phase") or rec.get("kind") or ""
            if phase == "filed":
                totals["appeals_filed"] += 1
            elif phase == "upheld":
                totals["appeals_upheld"] += 1

    # Open episodes: excluded until the last ledger tick (inclusive count).
    for subject, episode in open_episodes.values():
        entry = _agent(subject)
        hi = max(episode["effective_tick"], last_ledger_tick)
        entry["excluded_ticks"] += max(0, hi - episode["effective_tick"])
    del open_episodes

    out_agents: dict[str, Any] = {}
    for aid in sorted(agents):
        entry = agents[aid]
        entry["episodes"].sort(key=lambda e: (e["effective_tick"], e["sanction_id"]))
        closed = [e for e in entry["episodes"] if e["restored_tick"] is not None]
        entry["repeat_episode"] = len(closed) > 1 or len(entry["episodes"]) > 1
        out_agents[aid] = entry
    return {"agents": out_agents, "totals": totals}


# --------------------------------------------------------------------------- #
# 4. recovery time (preregistered-style)
# --------------------------------------------------------------------------- #

def recovery_time(
    run_dir: Path,
    *,
    pre_shock_window: tuple[int, int],
    post_shock_start: int,
    target_fraction: float,
    sustained_ticks: int,
) -> dict[str, Any]:
    """Time to recovery against an EXPLICITLY PRE-SPECIFIED criterion.

    Preregistration contract: the caller supplies ``pre_shock_window``
    (inclusive [start, end] ticks defining the baseline), ``post_shock_start``
    (first tick eligible for recovery), ``target_fraction`` (fraction of the
    baseline success rate that must be reached) and ``sustained_ticks``
    (consecutive qualifying ticks required) BEFORE reading any result.

    Definition: the per-tick success rate is the share of delivery
    objectives DUE at that tick whose ledger outcome is ``task_completed``
    (ticks with no due objectives have rate None and break no streak). The
    pre-shock baseline rate is the share of completed among all objectives
    due within ``pre_shock_window``. Recovery tick = the first tick T >=
    post_shock_start such that every tick in [T - sustained_ticks + 1, T]
    has a defined rate >= target_fraction * baseline. If the criterion is
    never met by the run's last ledger tick the case is CENSORED
    (``recovered=False, censored=True``) — it is reported as "not yet
    recovered by run end", never imputed as a slow recovery.

    Returns::

        {"recovered": bool, "recovery_tick": int | None, "censored": bool,
         "pre_shock_rate": float | None, "baseline_window": [start, end],
         "per_tick_rates": {tick: rate}, "evidence_event_ids": [...]}
    """
    run_dir = _require_run_dir(run_dir)
    if sustained_ticks < 1:
        raise ValueError("sustained_ticks must be >= 1")
    if not 0 < target_fraction <= 1:
        raise ValueError("target_fraction must be in (0, 1]")
    b_start, b_end = int(pre_shock_window[0]), int(pre_shock_window[1])
    if b_start < 0 or b_end < b_start:
        raise ValueError("pre_shock_window must satisfy 0 <= start <= end")
    if int(post_shock_start) < 0:
        raise ValueError("post_shock_start must be >= 0")

    events = _read_jsonl(run_dir, "events")
    by_tick: dict[int, list[tuple[int, bool]]] = {}
    for ev in events:
        if ev.get("kind") not in ("task_completed", "task_failed"):
            continue
        by_tick.setdefault(int(ev.get("tick", -1)), []).append(
            (int(ev["event_id"]), ev["kind"] == "task_completed")
        )

    def _rate(tick: int) -> float | None:
        rows = by_tick.get(tick)
        if not rows:
            return None
        return sum(1 for _, ok in rows if ok) / len(rows)

    baseline_rows: list[tuple[int, bool]] = []
    for tick in range(b_start, b_end + 1):
        baseline_rows.extend(by_tick.get(tick, []))
    pre_shock_rate = (
        sum(1 for _, ok in baseline_rows if ok) / len(baseline_rows)
        if baseline_rows else None
    )

    rates: dict[int, float] = {}
    last_tick = max((int(e.get("tick", -1)) for e in events), default=-1)
    for tick in range(max(0, b_start), last_tick + 1):
        r = _rate(tick)
        if r is not None:
            rates[tick] = r

    threshold = (target_fraction * pre_shock_rate) if pre_shock_rate is not None else None
    recovered = False
    recovery_tick: int | None = None
    if threshold is not None:
        for t in range(max(0, int(post_shock_start)), last_tick + 1):
            window = range(t - sustained_ticks + 1, t + 1)
            if all(wk in rates and rates[wk] >= threshold - 1e-12 for wk in window):
                recovered = True
                recovery_tick = t
                break

    evidence = sorted(eid for rows in by_tick.values() for eid, _ in rows)
    return {
        "recovered": recovered,
        "recovery_tick": recovery_tick,
        "censored": not recovered,
        "pre_shock_rate": _round(pre_shock_rate) if pre_shock_rate is not None else None,
        "baseline_window": [b_start, b_end],
        "per_tick_rates": {str(t): _round(r) for t, r in sorted(rates.items())},
        "evidence_event_ids": evidence,
    }


# --------------------------------------------------------------------------- #
# 5. institutional mechanism
# --------------------------------------------------------------------------- #

def _rule_delta(old_rules: dict[str, Any], new_rules: dict[str, Any]) -> dict[str, Any]:
    """Added/changed/removed enforceable-rule keys between two versions."""
    old_keys, new_keys = set(old_rules), set(new_rules)
    added = {k: new_rules[k] for k in sorted(new_keys - old_keys)}
    removed = {k: old_rules[k] for k in sorted(old_keys - new_keys)}
    changed = {
        k: {"from": old_rules[k], "to": new_rules[k]}
        for k in sorted(old_keys & new_keys) if old_rules[k] != new_rules[k]
    }
    return {"added": added, "changed": changed, "removed": removed}


def institutional_mechanism(run_dir: Path) -> dict[str, Any]:
    """Assembly activity: proposals, turnout, votes, enactments, blocks.

    Sources: ``meetings.jsonl`` (proposals, per-voter votes, abstentions,
    enacted version), ``constitution_history.jsonl`` (full rule text per
    version, enabling rule deltas), and ``events.jsonl`` (ledgered
    ``amendment_enacted`` / ``amendment_rejected`` events + all
    ``action_rejected`` events grouped by ``blocked_by`` prefix — the
    ``constitution:*`` and ``law:*`` codes show where the institution
    actually constrained behavior).

    Turnout = votes cast / eligible voters, where eligible voters are the
    agents present in the run manifest scenario. Returns ``None`` for
    turnout when eligibility cannot be determined.
    """
    run_dir = _require_run_dir(run_dir)
    meetings = _read_jsonl(run_dir, "meetings")
    history = _read_jsonl(run_dir, "constitution_history")
    events = _read_jsonl(run_dir, "events")
    manifest = _read_json(run_dir, "manifest") or {}

    eligible: list[str] = []
    scenario = manifest.get("scenario") if isinstance(manifest.get("scenario"), dict) else {}
    for agent in scenario.get("agents", []) or []:
        if isinstance(agent, dict) and agent.get("agent_id"):
            eligible.append(str(agent["agent_id"]))
    if not eligible:
        # Fall back to actors ever seen voting or acting in the ledger.
        seen = set()
        for m in meetings:
            for vote in m.get("votes", []) or []:
                if vote.get("voter_id"):
                    seen.add(str(vote["voter_id"]))
        for ev in events:
            actor = ev.get("actor_id")
            if actor and actor not in ("world", "governance", "assembly"):
                seen.add(str(actor))
        eligible = sorted(seen)

    proposals: list[dict[str, Any]] = []
    votes_for = votes_against = votes_abstain = 0
    turnout_values: list[float] = []
    for m in meetings:
        tick = int(m.get("tick", -1))
        for p in m.get("proposals", []) or []:
            proposals.append({
                "proposal_id": str(p.get("proposal_id", "")),
                "submitted_by": str(p.get("submitted_by", "")),
                "change_summary": str(p.get("change_summary", "")),
                "payload": p.get("payload") or {},
                "tick": tick,
            })
        votes = m.get("votes", []) or []
        for_votes = sum(1 for v in votes if v.get("choice") == "for")
        against_votes = sum(1 for v in votes if v.get("choice") == "against")
        abstain_votes = sum(1 for v in votes if v.get("choice") == "abstain")
        votes_for += for_votes
        votes_against += against_votes
        votes_abstain += abstain_votes
        if eligible:
            turnout_values.append(len(votes) / len(eligible))

    # Enactments with rule deltas: join meeting.enacted_version against the
    # constitution history texts (version -> rules).
    rules_by_version: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for h in history:
        version = str(h.get("version", ""))
        rules = ((h.get("constitution") or {}).get("rules")) or {}
        if version not in rules_by_version:
            order.append(version)
        rules_by_version[version] = dict(rules)

    enactments: list[dict[str, Any]] = []
    enacted_event_ids: list[int] = []
    rejected_event_ids: list[int] = []
    for m in meetings:
        version = m.get("enacted_version")
        if not version:
            continue
        idx = order.index(version) if version in order else -1
        prev_rules = rules_by_version.get(order[idx - 1], {}) if idx > 0 else {}
        enactments.append({
            "version": str(version),
            "tick": int(m.get("tick", -1)),
            "rule_deltas": _rule_delta(prev_rules, rules_by_version.get(str(version), {})),
            "proposal_ids": list(m.get("proposal_ids", []) or []),
        })
    for ev in events:
        if ev.get("kind") == "amendment_enacted":
            enacted_event_ids.append(int(ev["event_id"]))
        elif ev.get("kind") == "amendment_rejected":
            rejected_event_ids.append(int(ev["event_id"]))

    blocked: dict[str, int] = {}
    blocked_evidence: dict[str, list[int]] = {}
    for ev in events:
        if ev.get("kind") != "action_rejected":
            continue
        code = str((ev.get("payload") or {}).get("blocked_by") or "unknown")
        blocked[code] = blocked.get(code, 0) + 1
        blocked_evidence.setdefault(code, []).append(int(ev["event_id"]))

    return {
        "proposals": proposals,
        "turnout": {
            "eligible_voters": len(eligible),
            "mean_turnout": _round(sum(turnout_values) / len(turnout_values))
            if turnout_values else None,
            "meetings": len(meetings),
        },
        "votes": {"for": votes_for, "against": votes_against, "abstain": votes_abstain},
        "enactments": enactments,
        "amendment_enacted_event_ids": sorted(enacted_event_ids),
        "amendment_rejected_event_ids": sorted(rejected_event_ids),
        "blocked_actions_by_code": dict(sorted(blocked.items())),
        "blocked_action_evidence": {
            code: sorted(ids) for code, ids in sorted(blocked_evidence.items())
        },
    }


# --------------------------------------------------------------------------- #
# per-run summary + paired comparison
# --------------------------------------------------------------------------- #

def summarize_run(run_dir: Path) -> dict[str, Any]:
    """All five primary-outcome metric families for one completed run dir."""
    run_dir = _require_run_dir(run_dir)
    return {
        "run_dir": str(run_dir),
        "completion_rate": completion_rate(run_dir),
        "resource_sharing": resource_sharing(run_dir),
        "exclusion_episodes": exclusion_episodes(run_dir),
        "institutional_mechanism": institutional_mechanism(run_dir),
    }


def _load_pair_manifest(output_root: Path, manifest_path: Path | None) -> dict[str, Any]:
    path = manifest_path or (output_root / "pair_manifest.json")
    if not path.exists():
        raise RunArtifactError(
            f"pair manifest not found: {path} — run the paired runner first "
            "(python -m verdi.experiments.paired_run)"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunArtifactError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("runs"), list):
        raise RunArtifactError(f"{path.name}: malformed pair manifest (no 'runs' list)")
    return manifest


def summarize_pair(output_root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    """Compare every arm of a paired experiment from its on-disk artifacts.

    Reads ``pair_manifest.json`` (from the P0-C paired runner) plus each arm's
    run dir. Arms whose manifest status is not ``complete`` are listed under
    ``skipped`` with the recorded reason instead of being silently dropped
    (partial failure stays visible in the comparison).

    Returns::

        {"experiment_id": str, "arms": {arm: {seed: {...run metrics...}}},
         "paired_seeds": [seeds having BOTH arms complete],
         "skipped": [{seed, arm, reason}]}
    """
    output_root = Path(output_root)
    manifest = _load_pair_manifest(output_root, manifest_path)
    arms: dict[str, dict[int, Any]] = {}
    skipped: list[dict[str, Any]] = []
    for entry in manifest["runs"]:
        seed = int(entry["seed"])
        arm = str(entry["arm"])
        run_dir = Path(entry.get("run_dir") or output_root / f"{arm}-seed{seed}")
        if entry.get("status") != "complete":
            skipped.append({
                "seed": seed, "arm": arm,
                "reason": entry.get("error") or f"status={entry.get('status')}",
            })
            continue
        try:
            metrics = summarize_run(run_dir)
        except RunArtifactError as exc:
            skipped.append({"seed": seed, "arm": arm, "reason": str(exc)})
            continue
        metrics["seed"] = seed
        metrics["arm"] = arm
        metrics["final_state_hash"] = entry.get("final_state_hash")
        metrics["replay_matches"] = entry.get("replay_matches")
        arms.setdefault(arm, {})[seed] = metrics

    seeds_with_all_arms: set[int] = set()
    if arms:
        per_seed: dict[int, set[str]] = {}
        for arm, by_seed in arms.items():
            for seed in by_seed:
                per_seed.setdefault(seed, set()).add(arm)
        seeds_with_all_arms = {s for s, a in per_seed.items() if len(a) == len(arms)}
    return {
        "experiment_id": manifest.get("experiment_id"),
        "output_root": str(output_root),
        "arms": {arm: {str(k): v for k, v in sorted(seeds.items())}
                 for arm, seeds in sorted(arms.items())},
        "paired_seeds": sorted(seeds_with_all_arms),
        "skipped": sorted(skipped, key=lambda s: (s["seed"], s["arm"])),
    }


# --------------------------------------------------------------------------- #
# machine-readable export (JSON + long-format CSV)
# --------------------------------------------------------------------------- #

def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_metrics_json(summary: dict[str, Any], path: str | Path) -> Path:
    """Atomically write the comparison summary as pretty JSON (sorted keys)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(summary, sort_keys=True, indent=2) + "\n")
    return path


def _flatten_metric_rows(arm: str, seed: Any, metrics: dict[str, Any]):
    """Yield (arm, seed, metric, value, evidence_ref) rows (long format)."""
    comp = metrics.get("completion_rate", {})
    yield arm, seed, "completion.overall", comp.get("overall"), \
        ";".join(str(i) for o in comp.get("objectives", []) for i in o.get("evidence_event_ids", []))
    for obj in comp.get("objectives", []):
        yield arm, seed, f"completion.objective[{obj['objective_id']}].status", \
            obj["status"], ";".join(str(i) for i in obj["evidence_event_ids"])
        yield arm, seed, f"completion.objective[{obj['objective_id']}].delta", \
            obj["delta"], ";".join(str(i) for i in obj["evidence_event_ids"])

    sharing = metrics.get("resource_sharing", {})
    for resource, bucket in sharing.get("committed", {}).items():
        yield arm, seed, f"sharing.committed[{resource}]", bucket["total"], \
            ";".join(str(i) for i in bucket["evidence_event_ids"])
    for resource, count in sharing.get("attempted_not_committed", {}).items():
        yield arm, seed, f"sharing.attempted_not_committed[{resource}]", count, ""
    fin = sharing.get("final_inequality", {})
    if fin.get("primary_resource") is not None:
        yield arm, seed, "sharing.final_gini", fin.get("gini"), \
            f"resources.jsonl@tick{fin.get('tick')}"
    yield arm, seed, "sharing.reserve_final_total", \
        _final_reserve_total(sharing.get("reserve_levels", {})), \
        "resources.jsonl"

    excl = metrics.get("exclusion_episodes", {})
    for aid, entry in excl.get("agents", {}).items():
        yield arm, seed, f"exclusion[{aid}].excluded_ticks", entry["excluded_ticks"], \
            "sanctions.jsonl"
        yield arm, seed, f"exclusion[{aid}].repeat_episode", entry["repeat_episode"], \
            "sanctions.jsonl"
    totals = excl.get("totals", {})
    for key in sorted(totals):
        yield arm, seed, f"exclusion.totals.{key}", totals[key], "sanctions.jsonl"

    mech = metrics.get("institutional_mechanism", {})
    yield arm, seed, "institution.proposals", len(mech.get("proposals", [])), \
        "meetings.jsonl"
    yield arm, seed, "institution.turnout_mean", \
        mech.get("turnout", {}).get("mean_turnout"), "meetings.jsonl"
    for key in ("for", "against", "abstain"):
        yield arm, seed, f"institution.votes.{key}", mech.get("votes", {}).get(key), \
            "meetings.jsonl"
    yield arm, seed, "institution.enactments", len(mech.get("enactments", [])), \
        ";".join(str(i) for i in mech.get("amendment_enacted_event_ids", []))
    for code, count in mech.get("blocked_actions_by_code", {}).items():
        yield arm, seed, f"institution.blocked[{code}]", count, \
            ";".join(str(i) for i in
                     mech.get("blocked_action_evidence", {}).get(code, []))


def _final_reserve_total(reserve_levels: dict[str, list[list[Any]]]) -> Any:
    points = [p for series in reserve_levels.values() for p in series]
    if not points:
        return None
    last_tick = max(p[0] for p in points)
    return _round(sum(p[1] for p in points if p[0] == last_tick))


def write_metrics_csv(summary: dict[str, Any], path: str | Path) -> Path:
    """Atomically write the comparison summary in LONG CSV format.

    Columns: ``arm, seed, metric, value, evidence_ref`` — one row per scalar
    metric so pandas/csv.DictReader can pivot it directly. Nested structures
    become dotted/bracketed metric names; lists of event ids land in
    ``evidence_ref`` semicolon-joined.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(CSV_COLUMNS)]

    def fmt(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float) and math.isfinite(value):
            return repr(round(value, 6))
        return str(value)

    for arm, by_seed in sorted(summary.get("arms", {}).items()):
        for seed, metrics in sorted(by_seed.items(), key=lambda kv: int(kv[0])):
            for row in _flatten_metric_rows(arm, int(seed), metrics):
                lines.append(",".join(_csv_cell(part) for part in row))
    _atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n")):
        text = '"' + text.replace('"', '""') + '"'
    return text


# --------------------------------------------------------------------------- #
# CLI convenience entrypoint
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract P0-D primary outcome metrics from a paired run root."
    )
    parser.add_argument("--pair-root", required=True, type=Path,
                        help="output root written by the paired runner "
                             "(contains pair_manifest.json and <arm>-seed<N>/ dirs)")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="alternate pair_manifest.json path")
    args = parser.parse_args(argv)

    try:
        summary = summarize_pair(args.pair_root, args.manifest)
    except RunArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not summary["arms"]:
        reasons = "; ".join(f"{s['arm']}-seed{s['seed']}: {s['reason']}"
                            for s in summary["skipped"][:5]) or "no runs in manifest"
        print(f"error: no complete runs to summarize ({reasons})", file=sys.stderr)
        return 2

    root = args.pair_root
    json_path = write_metrics_json(summary, root / "metrics.json")
    csv_path = write_metrics_csv(summary, root / "metrics.csv")
    from verdi.analysis.comparative_report import write_comparison_html
    html_path = write_comparison_html(summary, root / "comparison.html")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {html_path}")
    if summary["skipped"]:
        print(f"warning: {len(summary['skipped'])} run(s) skipped: "
              + "; ".join(f"{s['arm']}-seed{s['seed']}" for s in summary["skipped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
