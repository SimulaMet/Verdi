"""EventLog tests: sealing, chain integrity, JSONL persistence, tamper detection."""
from __future__ import annotations

import json

import pytest
from tests.world.helpers import accepted, action, initial_world, rejected_double_spend

from verdi.schemas import WorldEvent
from verdi.world import EventLog, LogIntegrityError
from verdi.world.reducer import WorldReducer


def _first_events():
    """Build the first two (action, validation) steps from a fresh world."""
    reducer = WorldReducer()
    ws = initial_world()
    log = EventLog()
    steps = [
        (action("a1", "t-1", "produce", resource="credits", amount=1), accepted()),
        (action("a1", "t-2", "transfer", to="a2", resource="credits", units=2), rejected_double_spend()),
    ]
    for a, v in steps:
        before = ws
        after = reducer.apply(ws, a, v)
        log.append(reducer.build_event(before, after, a, v, log.last_hash()))
        ws = after
    return log, steps


def test_log_seals_and_chains():
    log, _ = _first_events()
    assert log.last_hash() != "genesis"
    first, second = log.events
    assert second.prev_hash == first.event_hash
    assert all(e.event_hash for e in log.events)
    log.verify()  # no raise


def test_every_attempt_recorded_including_rejected():
    log, _ = _first_events()
    decisions = [e.decision for e in log.events]
    kinds = [e.kind for e in log.events]
    assert decisions == ["accepted", "rejected"]
    assert kinds == ["action_accepted", "action_rejected"]
    rejected = log.events[1]
    assert rejected.after_hash is None
    assert rejected.payload["attempted_violation_codes"] == ["double_spend"]
    assert rejected.payload["blocked_by"] == "law:conservation"


def test_forged_event_hash_rejected_on_append():
    log, _ = _first_events()
    bad = EventLog()
    forged = log.events[0].model_copy(update={"event_hash": "0" * 64})
    with pytest.raises(LogIntegrityError, match="hash mismatch"):
        bad.append(forged)


def test_verify_rewalk_detects_tamper():
    log, _ = _first_events()
    e0, e1 = log.events
    # Forge a log whose second entry's payload no longer matches its hash.
    tampered1 = e1.model_copy(update={"payload": {**e1.payload, "amount": 999}})
    bad = EventLog()
    bad._events.extend((e0, tampered1))  # test-only: bypass append to build corrupt chain
    bad._last_hash = tampered1.event_hash
    with pytest.raises(LogIntegrityError, match="hash mismatch"):
        bad.verify()
    log.verify()  # the real log is still intact


def test_ordering_violation_on_append():
    log, _ = _first_events()
    # Re-appending an old event (id 0) violates monotonic ordering.
    with pytest.raises(LogIntegrityError, match="ordering"):
        log.append(log.events[0])


def test_tampered_persisted_chain_raises(tmp_path):
    """Corrupt one line of the persisted JSONL -> load raises."""
    log, _ = _first_events()
    path = tmp_path / "log.jsonl"
    path.write_bytes(log.to_jsonl_bytes())
    lines = path.read_text().splitlines()
    original = json.loads(lines[1])
    # Keep the original event_hash; change payload -> stored hash must no
    # longer match the recomputation.
    doctored = {**original, "payload": {**original["payload"], "amount": 12345}}
    lines[1] = json.dumps(doctored, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LogIntegrityError, match="hash mismatch"):
        EventLog.load_jsonl(path)


def test_jsonl_roundtrip_is_byte_identical(tmp_path):
    log, _ = _first_events()
    path = tmp_path / "log.jsonl"
    path.write_bytes(log.to_jsonl_bytes())
    reloaded = EventLog.load_jsonl(path)
    assert reloaded.to_jsonl_bytes() == log.to_jsonl_bytes()
    assert [e.model_dump(mode="json") for e in reloaded.events] == [
        e.model_dump(mode="json") for e in log.events
    ]


def test_append_persists_jsonl(tmp_path):
    path = tmp_path / "sub" / "log.jsonl"
    log = EventLog(path)
    reducer = WorldReducer()
    ws = initial_world()
    a = action("a1", "t-1", "produce", resource="credits", amount=1)
    v = accepted()
    after = reducer.apply(ws, a, v)
    log.append(reducer.build_event(ws, after, a, v, log.last_hash()))
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    loaded = WorldEvent.model_validate(json.loads(lines[0]))
    assert loaded.event_hash == log.events[0].event_hash
    log.verify()
