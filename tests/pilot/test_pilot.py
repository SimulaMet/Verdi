"""Cross-module pilot integration tests (P1-I, G2/G3).

These wire every frozen-contract module together over the recorded-output
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

from verdi.pilot.driver import (
    PROBE_TICKS,
    PilotDriver,
    probe_response_for,
)
from verdi.world.replay import replay

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pilot():
    return PilotDriver(REPO_ROOT).run()


def test_two_runs_are_byte_identical_in_state(pilot) -> None:
    """Running the pilot twice from the same scenario yields the same hash."""
    first_hash = pilot.final_state.state_hash
    again = PilotDriver(REPO_ROOT).run()
    assert again.final_state.state_hash == first_hash
    assert len(again.log.events) == len(pilot.log.events)


def test_event_log_verifies(pilot) -> None:
    pilot.log.verify()  # raises LogIntegrityError on any break


def test_replay_reproduces_substantive_state_hash(pilot) -> None:
    """G3: folding the recorded action tape through a fresh reducer+log
    reproduces the live run's substantive state hash (agents + world
    resources). The tape covers the 81 action steps; governance
    bookkeeping events are ledger-only and tracked separately by the
    driver."""
    from verdi.world.log import EventLog
    from verdi.world.reducer import WorldReducer

    final_state, _events = replay(EventLog(), pilot.loaded.t0_state, list(pilot.tape_steps))
    live = pilot.final_state
    assert WorldReducer.canonical_state_hash(final_state) == WorldReducer.canonical_state_hash(
        live.model_copy(
            update={
                "tick": final_state.tick,
                "event_cursor": final_state.event_cursor,
                "constitution_version": live.constitution_version,
            }
        )
    )
    # and the full ledger of the live run still verifies end-to-end
    pilot.log.verify()


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
    # The appeal was adjudicated (dismissed in this pilot), so a dismissal event
    # is present; an upheld appeal would instead emit appeal_upheld.
    assert "appeal_dismissed" in kinds or "appeal_upheld" in kinds


def test_three_assemblies_ran(pilot) -> None:
    proposals = [e for e in pilot.governance_events if e["kind"] == "proposal_submitted"]
    assert len(proposals) == 3


def test_probes_cover_required_ticks_and_channels(pilot) -> None:
    from verdi.pilot.driver import CHANNELS

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
