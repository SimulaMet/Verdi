"""Longitudinal value trajectories combining interviews, probes, and observed
behavior, linked to real world events (todo C).

Scientific-honesty contract (mirrors :mod:`verdi.measurement.longitudinal`):
channels are NEVER merged into a single "belief" number. Each trajectory row
carries the per-channel scores separately:

* ``declared``  — probe self-report channel (0-5 Likert on the declared axis),
* ``private``   — probe private behavioral-dilemma channel (yes/no),
* ``public``    — probe public behavioral-dilemma channel (yes/no),
* ``interview`` — SELF-REPORTED declared values from the read-only checkpoint
  interview (channel "interview"); this is what the agent SAID when asked, not
  an independent observation of action.

Every row is linked to the nearest committed world event up to that tick so a
score can be traced to what actually happened in the world. A metadata block
states instrument versions and that judges/scores measure OBSERVABLE EXPRESSION
of value, never internal belief.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")

#: Fixed set of measurement channels; deliberately NOT merged downstream.
CHANNELS = ("declared", "private", "public", "interview")


# --------------------------------------------------------------------------- #
# Interview -> deterministic per-axis self-report scores (no LLM judge)
# --------------------------------------------------------------------------- #
def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def interview_axis_scores(parsed: dict[str, Any] | None) -> dict[str, float]:
    """Deterministically extract per-axis 'now' self-assessment (0..1) from an
    interview record's parsed JSON. Falls back gracefully when the model answer
    was unparseable or missing an axis (returns only axes it could read).

    This is a SELF-REPORT score (what the agent said), recorded under the
    "interview" channel — it is never mixed with the observed-behavior channels.
    """
    out: dict[str, float] = {}
    if not isinstance(parsed, dict):
        return out
    value = parsed.get("value") if isinstance(parsed.get("value"), dict) else parsed
    sa = value.get("self_assessment") if isinstance(value, dict) else None
    if not isinstance(sa, dict):
        return out
    for axis in VALUE_AXES:
        entry = sa.get(axis)
        now = entry.get("now") if isinstance(entry, dict) else None
        if now is None:
            continue
        try:
            v = float(now)
        except (TypeError, ValueError):
            continue
        # Accept either a 0..1 value or a 0..5 Likert rating (normalize /5).
        out[axis] = _clamp01(v / 5.0 if v > 1.0 else v)
    return out


# --------------------------------------------------------------------------- #
# Nearest-event linking
# --------------------------------------------------------------------------- #
_TASK_TICK = re.compile(r"-t(\d+)-")


def _nearest_event(agent_id: str, tick: int, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The most recent committed world event at-or-before lifecycle ``tick`` that
    involves this agent (or a society-level governance event if none
    agent-specific). Returns {event_id, kind, tick, lifecycle_tick} or None.

    ``tick`` is the campaign's lifecycle tick, while a ledger event's ``tick``
    is the reducer's transition counter, so the two are not comparable. The
    lifecycle tick of an event is read from its task id (``soc-tNN-..``);
    events without one (governance, consumption) inherit the tick of the
    preceding event, since the ledger is chronological.
    """
    agent_ev = None
    society_ev = None
    current = 0
    for ev in events:
        task_id = str((ev.get("payload") or {}).get("task_id") or "")
        m = _TASK_TICK.search(task_id)
        if m:
            current = int(m.group(1))
        if current > tick:
            break
        actor = ev.get("actor_id") or ev.get("actor")
        ref = {"event_id": ev.get("event_id"), "kind": ev.get("kind", ""),
               "tick": ev.get("tick"), "lifecycle_tick": current}
        # Later events overwrite earlier ones: we want the MOST RECENT match.
        if actor == agent_id:
            agent_ev = ref
        elif actor in (None, "assembly", "governance"):
            society_ev = ref
    return agent_ev or society_ev


# --------------------------------------------------------------------------- #
# Trajectory assembly
# --------------------------------------------------------------------------- #
def _probe_rows_by_agent(probes: list[Any]) -> dict[str, list[dict[str, Any]]]:
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for p in probes:
        d = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        by_agent.setdefault(d["agent_id"], []).append(d)
    return by_agent


def build_value_trajectories(
    *,
    probes: list[Any],
    interviews: list[dict[str, Any]],
    events: list[dict[str, Any]],
    assembly_ticks: list[int] | None = None,
    constitution_versions: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble per-agent temporal value trajectories across checkpoints.

    Returns ``{agent_id: [row, ...]}`` sorted by tick, plus a top-level
    ``"_meta"`` key with instrument provenance and the honesty disclaimer. Each
    row: ``{tick, phase, declared, private, public, interview,
    linked_world_event, constitution_version}`` where each channel field is a
    per-axis dict (only axes observed at that tick are present). Channels are
    kept separate — there is no combined/belief score anywhere in this output.
    """
    assembly_set = frozenset(assembly_ticks or [])
    meta = {
        "instrument_versions": {
            "probe_bank": "verdi.probe-bank/1",
            "interview_prompt": "verdi.interview/1",
        },
        "value_axes": list(VALUE_AXES),
        "channels": list(CHANNELS),
        "disclaimer": (
            "Scores measure OBSERVABLE EXPRESSION of value (self-report on "
            "probes/interviews and observed behavioral dilemmas), NOT internal "
            "beliefs. The 'interview' channel is self-reported declared value "
            "and is never merged with observed-behavior channels. Judges are "
            "advisors, never sufficient proof."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    probe_by_agent = _probe_rows_by_agent(probes)
    interview_by_agent: dict[str, list[dict[str, Any]]] = {}
    for rec in interviews:
        interview_by_agent.setdefault(rec.get("agent_id", ""), []).append(rec)

    agents = sorted(set(list(probe_by_agent) + list(interview_by_agent)))
    out: dict[str, Any] = {"_meta": meta}

    for aid in agents:
        rows: list[dict[str, Any]] = []
        seen_ticks: set[int] = set()

        # Probe-derived rows (declared/private/public channels).
        for p in sorted(probe_by_agent.get(aid, []), key=lambda r: (r["tick"], r["channel"])):
            tick = p["tick"]
            seen_ticks.add(tick)
            vv = p.get("value_vector") or {}
            rows.append({
                "tick": tick,
                "phase": p.get("evidence_ref", ""),
                "source": "probe",
                "channel": p["channel"],
                "scores": {axis: vv[axis] for axis in VALUE_AXES if axis in vv},
                "linked_world_event": _nearest_event(aid, tick, events),
                "constitution_version": _constitution_at(tick, assembly_set, constitution_versions),
            })

        # Interview-derived rows (self-report "interview" channel).
        for rec in sorted(interview_by_agent.get(aid, []), key=lambda r: r.get("tick", 0)):
            tick = rec.get("tick", 0)
            seen_ticks.add(tick)
            scores = interview_axis_scores(rec.get("parsed"))
            rows.append({
                "tick": tick,
                "phase": rec.get("phase", ""),
                "source": "interview",
                "channel": "interview",
                "scores": scores,
                "narrative": _narrative(rec),
                "linked_world_event": _nearest_event(aid, tick, events),
                "constitution_version": _constitution_at(tick, assembly_set, constitution_versions),
            })

        rows.sort(key=lambda r: (r["tick"], r["channel"]))
        out[aid] = rows
    return out


def _narrative(rec: dict[str, Any]) -> str | None:
    parsed = rec.get("parsed") or {}
    value = parsed.get("value") if isinstance(parsed, dict) else None
    if isinstance(value, dict):
        n = value.get("narrative")
        if isinstance(n, str) and n.strip():
            return n
    return None


def _constitution_at(tick: int, assembly_set: frozenset[int], versions: list[str] | None) -> str | None:
    """Best-effort constitution version label at a tick: count assemblies held
    strictly before/at tick to index the version history (version i+1 after i
    enacted meetings). Returns None when version history unavailable."""
    if not versions:
        return None
    prior = sum(1 for a in assembly_set if a <= tick)
    idx = min(prior, len(versions) - 1)
    return versions[idx]


def write_value_trajectories(run_dir: Path, trajectories: dict[str, Any]) -> Path:
    """Write the assembled trajectories to ``run_dir/value_trajectories.json``.
    No key material is ever in this payload (it is derived purely from scored,
    already-guarded artifacts); still assert defensively."""
    path = Path(run_dir) / "value_trajectories.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(trajectories, sort_keys=True, ensure_ascii=True, indent=2, default=str)
    from verdi.audit.judge_live import _assert_no_key_material
    _assert_no_key_material(text, str(path))
    path.write_text(text + "\n", encoding="utf-8")
    return path


__all__ = [
    "VALUE_AXES",
    "CHANNELS",
    "interview_axis_scores",
    "build_value_trajectories",
    "write_value_trajectories",
]
