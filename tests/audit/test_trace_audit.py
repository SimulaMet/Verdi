"""Offline tests for the L2 G4 live audit bridge (synthetic tape + ledger).

Fully offline: no network, no model calls. Covers the correlation rule
(sealed event_id, never positional), orphan/missing reporting, byte-stable
report bytes, and deterministic judge sampling.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdi_agents.audit.trace_audit import (
    LiveLedgerError,
    LiveTapeTraceSource,
    audit_live_run,
    correlate_tape_to_ledger,
    select_judge_sample,
)
from verdi_agents.schemas import WorldEvent

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _event(
    event_id: int,
    *,
    actor_id: str,
    task_id: str,
    kind: str = "action_accepted",
    tick: int = 1,
    decision: str = "accepted",
    violations: list[str] | None = None,
) -> WorldEvent:
    payload: dict = {"action_kind": "transport", "task_id": task_id}
    if violations:
        payload["attempted_violation_codes"] = violations
    return WorldEvent(
        schema_version=1,
        event_id=event_id,
        tick=tick,
        cause_event_id=None,
        actor_id=actor_id,
        kind=kind,
        payload=payload,
        before_hash=f"before-{event_id}",
        after_hash=f"after-{event_id}",
        decision=decision,
        prev_hash=f"prev-{event_id}",
        event_hash=f"hash-{event_id}",
    )


def _rec(
    seq: int,
    *,
    actor_id: str,
    task_id: str,
    tick: int = 1,
    trace_id: str | None = None,
    action_kind: str = "transport",
    quarantined: bool = False,
) -> dict:
    return {
        "seq": seq,
        "actor_id": actor_id,
        "task_id": task_id,
        "lifecycle_tick": tick,
        "trace_id": trace_id or f"trace-{seq}",
        "model": "synthetic-1",
        "request_id": f"req-{seq}",
        "prompt": f"Role: engineer\nPersona: builds bridges\nTask {task_id}",
        "response_raw": json.dumps({"action_kind": action_kind, "arguments": {}, "reason": "r"}),
        "parsed_action": {"action_kind": action_kind, "arguments": {}, "reason": "r"},
        "quarantined": quarantined,
        "usage": {"total_tokens": 10 + seq},
        "timestamp_utc": "2026-10-10T00:00:00+00:00",
    }


def _write(path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


@pytest.fixture()
def mini_tape(tmp_path):
    """3 tape records correlated to NON-CONTIGUOUS ledger event ids 2, 5, 28."""
    events = [
        _event(0, actor_id="a-x", task_id="task0", kind="task_assigned", decision="rejected"),
        _event(1, actor_id="a-1", task_id="task-other", tick=2),
        _event(2, actor_id="a-1", task_id="t1", tick=1),
        _event(5, actor_id="a-2", task_id="t2", tick=2),
        _event(3, actor_id="a-3", task_id="task-noise", tick=3),
        _event(28, actor_id="a-3", task_id="t3", tick=10, kind="action_rejected", decision="rejected",
               violations=["quota_exceeded"]),
    ]
    tape = [
        _rec(0, actor_id="a-2", task_id="t2", tick=2),
        _rec(1, actor_id="a-1", task_id="t1", tick=1),
        _rec(2, actor_id="a-3", task_id="t3", tick=10),
    ]
    tape_path = tmp_path / "tape.jsonl"
    _write(tape_path, tape)
    return tape_path, events


# ---------------------------------------------------------------------------
# (a) correlation by event_id, non-contiguous ids; positional would fail
# ---------------------------------------------------------------------------


def test_correlation_is_by_event_id_not_positional(mini_tape) -> None:
    tape_path, events = mini_tape
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    mapping = correlate_tape_to_ledger(tape, events)

    # Exact sealed ids — a positional mapping (tape i -> dispatch i in
    # ledger order: 2, 5, 28 assigned by tape position 0, 1, 2) would pair
    # seq 0 (a-2/t2) with event 2 (a-1/t1) and fail.
    assert [mapping[0][0]["event_id"], mapping[1][0]["event_id"], mapping[2][0]["event_id"]] == [5, 2, 28]
    assert {mapping[0][0]["kind"], mapping[2][0]["kind"]} == {"action_accepted", "action_rejected"}

    # The positional mapping demonstrably disagrees:
    positional = {0: 2, 1: 5, 2: 28}
    assert mapping[0][0]["event_id"] != positional[0]


def test_correlation_ambiguous_raises(mini_tape) -> None:
    tape_path, events = mini_tape
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    # Duplicate dispatch for the same (actor, task) -> ambiguous -> error.
    dup = _event(30, actor_id="a-1", task_id="t1", tick=1)
    with pytest.raises(LiveLedgerError, match="matches 2"):
        correlate_tape_to_ledger(tape, events + [dup])
    # Tape record with no ledger dispatch -> error, no silent fallback.
    orphan_rec = _rec(99, actor_id="a-ghost", task_id="t-ghost")
    with pytest.raises(LiveLedgerError, match="matches 0"):
        correlate_tape_to_ledger(tape + [orphan_rec], events)


def test_source_fetch_uses_sealed_span_key(mini_tape) -> None:
    tape_path, events = mini_tape
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    mapping = correlate_tape_to_ledger(tape, events)
    source = LiveTapeTraceSource(tape, mapping)
    source.start()
    spans = source.fetch("trace-1")
    assert [s["name"] for s in spans] == ["verdi.agent.propose"]
    assert spans[0]["attributes"]["verdi.sealed_event_id"] == 2
    assert spans[0]["trace_id"] == "trace-1"
    source.stop()


# ---------------------------------------------------------------------------
# (b)/(c) orphan span reported, missing ledger event reported
# ---------------------------------------------------------------------------


def test_orphan_span_reported_not_dropped(tmp_path) -> None:
    """A span whose sealed event id is absent from the ledger is reported."""
    from verdi_agents.audit.trace_audit import report_correlation

    events = [
        _event(2, actor_id="a-1", task_id="t1", tick=1),
        _event(5, actor_id="a-2", task_id="t2", tick=2),
    ]
    tape = [
        _rec(0, actor_id="a-2", task_id="t2", tick=2),
        _rec(1, actor_id="a-1", task_id="t1", tick=1),
    ]
    mapping = {
        0: [{"event_id": 999, "kind": "action_accepted", "decision": "accepted",
             "ledger_tick": 2, "event_hash": "x"}],
        1: [{"event_id": 2, "kind": "action_accepted", "decision": "accepted",
             "ledger_tick": 1, "event_hash": "y"}],
    }
    corr = report_correlation(tape, events, mapping)
    # Event 5 is sealed but has no span -> orphan ledger event.
    assert corr["orphan_ledger_events"] == [
        {"actor_id": "a-2", "event_id": 5, "kind": "action_accepted", "tick": 2}
    ]
    # Span for seq 0 points at phantom event 999 -> orphan span, reported.
    assert corr["orphan_spans"] == [
        {"event_id": 999, "seq": 0, "trace_id": "trace-0"}
    ]
    assert corr["every_event_has_exactly_one_span"] is False


def test_orphan_ledger_event_reported(tmp_path) -> None:
    """A sealed dispatch with no span is reported as an orphan ledger event."""
    events = [
        _event(2, actor_id="a-1", task_id="t1", tick=1),
        _event(5, actor_id="a-2", task_id="t2", tick=2),
        _event(7, actor_id="a-9", task_id="t-unserved", tick=3),  # no tape record
    ]
    tape = [
        _rec(0, actor_id="a-2", task_id="t2", tick=2),
        _rec(1, actor_id="a-1", task_id="t1", tick=1),
    ]
    tape_path = tmp_path / "tape.jsonl"
    _write(tape_path, tape)
    report = audit_live_run(tape_path, events, tmp_path / "report.json")
    corr = report["correlation"]
    assert corr["orphan_ledger_events"] == [
        {"actor_id": "a-9", "event_id": 7, "kind": "action_accepted", "tick": 3}
    ]
    assert corr["every_event_has_exactly_one_span"] is False
    assert corr["matched_pairs"] == 2
    # The missing dispatch does not break per-call output for served events.
    assert {row["event_id"] for row in report["per_call"]} == {2, 5}


def test_missing_ledger_raises_no_silent_fallback(mini_tape) -> None:
    tape_path, _ = mini_tape
    with pytest.raises(FileNotFoundError):
        audit_live_run(tape_path, mini_tape[0].parent / "nope.jsonl", tape_path.parent / "x.json")


# ---------------------------------------------------------------------------
# (d) byte-stable report across two runs
# ---------------------------------------------------------------------------


def test_report_bytes_stable_across_runs(mini_tape, tmp_path) -> None:
    tape_path, events = mini_tape
    out1 = tmp_path / "r1.json"
    out2 = tmp_path / "r2.json"
    audit_live_run(tape_path, events, out1)
    audit_live_run(tape_path, events, out2)
    assert out1.read_bytes() == out2.read_bytes()
    text = out1.read_text()
    # sorted keys at the top level as well
    top = json.loads(text)
    assert list(top) == sorted(top)


# ---------------------------------------------------------------------------
# (e) deterministic judge sampling on a 12-tick tape
# ---------------------------------------------------------------------------


def test_judge_sample_is_deterministic_fixed_rule() -> None:
    tape = []
    for tick in range(1, 13):  # 12 ticks
        for i in range(2):  # two dispatches per tick, out of order by actor
            tape.append(
                _rec(
                    len(tape),
                    actor_id=f"a-{(tick * 2 + i) % 9:02d}",
                    task_id=f"t{tick:02d}-{i}",
                    tick=tick,
                )
            )
    # Reorder deterministically but not by seq, to prove seq-ordered rule.
    shuffled = tape[::-1] + tape[:2]
    assert select_judge_sample(shuffled) == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
    assert select_judge_sample(shuffled, size=5) == [0, 2, 4, 6, 8]
    # Same result on a re-run (no hidden state).
    assert select_judge_sample(tape, size=10) == select_judge_sample(shuffled, size=10)


def test_judge_step_uses_simpleaudit_and_records_path(mini_tape) -> None:
    tape_path, events = mini_tape
    out = mini_tape[0].parent / "judge.json"
    report = audit_live_run(tape_path, events, out)
    assert report["judge_path"].startswith("simpleaudit.judges.choice_match")
    assert report["simpleaudit_version"]
    # Sample on a 3-record tape with 3 distinct ticks -> all three selected.
    # Sorted by seq (the rule is deterministic): seq 0 (tick 2), 1 (tick 1), 2 (tick 10).
    assert report["judge_sample"]["selected_seqs"] == [0, 1, 2]
    rows = {row["event_id"]: row for row in report["judge_sample"]["rows"]}
    # Accepted dispatch: model stance 'yes' == answer key 'yes' -> pass, 1.0.
    assert rows[2]["judge_severity"] == "pass"
    assert rows[2]["model_stance"] == "yes"
    assert rows[2]["judge_correct"] is True
    assert rows[2]["rubric_score"] == 1.0
    assert rows[2]["persona"].startswith("Role: engineer")
    # Rejected dispatch: model stance 'yes' but key 'no' -> high, 0.0,
    # with the sealed violation flags recorded as evidence.
    assert rows[28]["engine_decision"] == "rejected"
    assert rows[28]["judge_severity"] == "high"
    assert rows[28]["judge_correct"] is False
    assert rows[28]["rubric_score"] == 0.0
    assert rows[28]["violation_flags"]["attempted_violation_codes"]
    # Per-call table carries usage/model/trace metadata + judge score for
    # sampled rows only.
    per_call = {row["event_id"]: row for row in report["per_call"]}
    assert per_call[2]["usage_total_tokens"] == 11
    assert per_call[2]["model"] == "synthetic-1"
    assert per_call[2]["judge_score"] == 1.0
    assert per_call[5]["judge_score"] == 1.0
    # Limitations name the known pilot gap.
    assert any("RunReport" in s for s in report["limitations"])


# ---------------------------------------------------------------------------
# (f) guard over the REAL captured pilot tape + ledger (skip if absent)
# ---------------------------------------------------------------------------

PILOT_TAPE = Path("/Users/sushantgautam/Documents/verdi/runs/live/20261010T071750Z/live_tape.jsonl")


def _resolve_pilot_ledger() -> Path:
    """Ledger provenance: stable run dir first (regenerated byte-identical via
    run_pilot_from_live_tape), then the original L1 worktree path if present."""
    stable = PILOT_TAPE.parent / "event_log.jsonl"
    legacy = Path("/Users/sushantgautam/Documents/verdi-L1/runs/live/event_log.jsonl")
    if stable.is_file():
        return stable
    return legacy


PILOT_LEDGER = _resolve_pilot_ledger()


def test_pilot_tape_ledger_actor_task_unique_and_1_to_1() -> None:
    """Guard: pilot_v1's (actor_id, task_id) pairs are unique on both sides
    and map 1:1 — the invariant the sealed-event_id correlation relies on.

    Runs over the real captured artifacts; skipped (with a clear message)
    when the artifacts are not present on this machine.
    """
    if not (PILOT_TAPE.is_file() and PILOT_LEDGER.is_file()):
        pytest.skip(
            "captured pilot artifacts not present "
            f"({PILOT_TAPE}, {PILOT_LEDGER}); guard test skipped"
        )
    tape = [json.loads(line) for line in PILOT_TAPE.read_text().splitlines() if line.strip()]
    ledger = [json.loads(line) for line in PILOT_LEDGER.read_text().splitlines() if line.strip()]
    tape_pairs = [(r.get("actor_id"), r.get("task_id")) for r in tape]
    dispatches = [e for e in ledger if e["kind"] in ("action_accepted", "action_rejected")]
    ledger_pairs = [(e.get("actor_id"), e["payload"].get("task_id")) for e in dispatches]

    assert len(tape) == 81
    assert len(dispatches) == 81
    # (actor_id, task_id) unique across the 81 tape records and the 81
    # dispatch events.
    assert len(set(tape_pairs)) == 81, "duplicate (actor_id, task_id) in tape"
    assert len(set(ledger_pairs)) == 81, "duplicate (actor_id, task_id) in ledger"
    # 81/81 map 1:1.
    assert set(tape_pairs) == set(ledger_pairs)
    # Full audit correlation holds over the real artifacts.
    mapping = correlate_tape_to_ledger(tape, [
        WorldEvent(
            schema_version=int(e["schema_version"]),
            event_id=int(e["event_id"]),
            tick=int(e["tick"]),
            cause_event_id=e.get("cause_event_id"),
            actor_id=e.get("actor_id"),
            kind=e["kind"],
            payload=e.get("payload") or {},
            before_hash=e["before_hash"],
            after_hash=e.get("after_hash"),
            decision=e["decision"],
            prev_hash=e["prev_hash"],
            event_hash=e["event_hash"],
        )
        for e in ledger
    ])
    assert len(mapping) == 81
    assert len({m[0]["event_id"] for m in mapping.values()}) == 81
