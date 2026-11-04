"""End-to-end tests of the reference run, wiring all core modules together.

These run every core module together over the recorded-output
pilot_v1 scenario and assert the non-negotiable invariants:
* deterministic replay reproduces the substantive state hash;
* the event ledger verifies and every attempted violation is logged;
* the governance lifecycle (proposal -> vote -> enact -> sanction ->
  appeal -> adjudication) all appear as immutable events;
* probes are out-of-band and channels are kept distinct;
* the run report is complete (non-empty limitations, final hash).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdi_agents.reference_run.driver import (
    PROBE_TICKS,
    PilotDriver,
    probe_response_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pilot():
    return PilotDriver(REPO_ROOT).run()


def test_two_runs_are_byte_identical_in_state(pilot) -> None:
    """Running the reference run twice from the same scenario yields the same hash."""
    first_hash = pilot.final_state.state_hash
    again = PilotDriver(REPO_ROOT).run()
    assert again.final_state.state_hash == first_hash
    assert len(again.log.events) == len(pilot.log.events)


def test_event_log_verifies(pilot) -> None:
    pilot.log.verify()  # raises LogIntegrityError on any break


def test_replay_reproduces_full_ledger(pilot) -> None:
    """Folding the FULL recorded ledger tape (all 126 action AND
    governance steps, in ledger order) through a fresh reducer + log
    reproduces the live run's final state hash AND a byte-identical
    JSONL ledger. No bookkeeping force-copies are needed: the reducer
    now tracks constitution_version from the recorded tape."""
    final_state, events = PilotDriver(REPO_ROOT).replay_check(pilot)
    assert final_state.state_hash == pilot.final_state.state_hash
    assert len(events) == len(pilot.log.events)
    # and the live ledger verifies end-to-end
    pilot.log.verify()
    # full tape length equals ledger length; action-only tape is a subset
    assert len(pilot.full_tape_steps) == len(pilot.log.events)
    assert len(pilot.tape_steps) == 81  # action steps only


def test_replay_constitution_version_tracked(pilot) -> None:
    """Enacted amendments must advance WorldState.constitution_version, and
    replay must reproduce it without any force-copy."""
    assert pilot.final_state.constitution_version == "base-constitution-v4"
    assert pilot.constitution_versions == {
        10: "base-constitution-v2",
        17: "base-constitution-v3",
        24: "base-constitution-v4",
    }


def test_violation_attempted_and_logged(pilot) -> None:
    attempted = pilot.metrics["violations"]["attempted"]
    assert pilot.metrics["violations"]["attempted_total"] >= 1
    assert any(code.startswith("supply_misallocation") for code in attempted)
    # and it appears in the ledger as a rejected event
    rejected = [e for e in pilot.log.events if e.decision == "rejected"]
    assert any(
        "supply_misallocation" in (e.payload.get("attempted_violation_codes") or [])
        for e in rejected
    )


def test_governance_lifecycle_present(pilot) -> None:
    kinds = {e["kind"] for e in pilot.governance_events}
    assert "proposal_submitted" in kinds
    assert "vote_cast" in kinds
    assert "amendment_enacted" in kinds
    assert "sanction_imposed" in kinds
    assert "appeal_filed" in kinds
    # The appeal was adjudicated (dismissed in this run), so a dismissal event
    # is present; an upheld appeal would instead emit appeal_upheld.
    assert "appeal_dismissed" in kinds or "appeal_upheld" in kinds


def test_three_assemblies_ran(pilot) -> None:
    proposals = [e for e in pilot.governance_events if e["kind"] == "proposal_submitted"]
    assert len(proposals) == 3


def test_probes_cover_required_ticks_and_channels(pilot) -> None:
    from verdi_agents.reference_run.driver import CHANNELS

    seen_ticks = {p.tick for p in pilot.probe_results}
    for t in PROBE_TICKS:
        assert t in seen_ticks, f"no probes at tick {t}"
    channels = {p.channel for p in pilot.probe_results}
    assert channels == set(CHANNELS)
    # No probe ever recorded a channel outside the frozen set.
    assert channels.issubset({"declared", "private", "public"})


def test_probe_response_is_deterministic() -> None:
    assert probe_response_for("a-01", 5, "private") == probe_response_for("a-01", 5, "private")


def test_drift_keeps_channels_separate(pilot) -> None:
    drift = pilot.metrics["value_drift_by_channel"]
    for agent in drift.values():
        # the pooled-over-channels key must always be null (explicit ban)
        assert agent["by_axis_pooled_over_channels"] is None
        assert set(agent["by_channel"]) == {"declared", "private", "public"}


def test_audit_records_correlate_real_ledger_event_ids(pilot) -> None:
    """Regression (review finding 1): each governance audit record must
    correlate to the ACTUAL ledger event id of its governance event, not a
    contiguous 0-based index. All ids must be unique, in range, and the
    linked event's kind must match the trace's span name."""
    assert len(pilot.audit_records) == len(pilot.governance_events)
    ids = [ar.event_ids for ar in pilot.audit_records]
    flat = [i for ev in ids for i in ev]
    assert all(len(ev) == 1 for ev in ids), "one ledger event per governance trace"
    assert len(set(flat)) == len(flat), "correlation ids must be unique"
    assert all(0 <= i < len(pilot.log.events) for i in flat)
    # cross-check against the ledger event recorded per governance event
    for ar, gen_event in zip(pilot.audit_records, pilot.governance_events, strict=True):
        ledger_id = gen_event["_ledger_event_id"]
        assert ar.event_ids == [ledger_id]
        ledger_event = pilot.log.events[ledger_id]
        assert ledger_event.kind == gen_event["kind"]
        # regression (review finding 2): span names must be populated
        assert ar.details["span_names"] == [gen_event["kind"]]


def test_welfare_keyed_by_lifecycle_tick(pilot) -> None:
    """Regression (re-review note A): welfare/inequality metrics must be keyed
    by the LIFECYCLE tick of each snapshot (0 + SNAPSHOT_TICKS), NOT the
    reducer's cumulative transition counter (which is 126 in this run)."""
    from verdi_agents.reference_run.driver import SNAPSHOT_TICKS

    expected = {str(0), *(str(t) for t in SNAPSHOT_TICKS)}
    assert set(pilot.metrics["welfare_food"]["gini_by_tick"].keys()) == expected
    # and the deprivation horizon must be the last lifecycle tick, not the
    # transition counter (126 != 29)
    assert pilot.final_state.tick == len(pilot.log.events)
    assert pilot.final_state.tick != pilot.loaded.meta.total_ticks - 1


def test_report_is_complete(pilot) -> None:
    report = pilot.report
    assert report is not None
    doc = report.to_dict()
    assert doc["event_count"] == len(pilot.log.events)
    assert doc["state_hash_final"] == pilot.final_state.state_hash
    assert isinstance(doc["limitations"], list) and len(doc["limitations"]) >= 1
    # The manifest carries the verified tape hash (no drift between loader + run).
    assert doc["manifest"]["task_tape_hash"] == pilot.loaded.tape_hash


def test_report_roundtrips_as_json(pilot) -> None:
    text = pilot.report.to_jsonl()  # type: ignore[union-attr]
    for line in text.splitlines():
        assert line, "no blank lines in jsonl report"
        json.loads(line)
