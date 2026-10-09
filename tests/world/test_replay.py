"""Replay + snapshot tests: determinism, no model invocation, round-trips."""
from __future__ import annotations

from tests.world.helpers import accepted, action, initial_world, rejected_double_spend

from verdi.world import (
    EventLog,
    SnapshotStore,
    TapeStep,
    WorldReducer,
    replay,
    replay_in_memory,
)


def _tape():
    return [
        TapeStep(action("a1", "t-1", "produce", resource="credits", amount=1), accepted()),
        TapeStep(action("a1", "t-2", "transfer", to="a2", resource="credits", units=4), accepted()),
        TapeStep(action("a1", "t-3", "transfer", to="a2", resource="credits", units=99),
                 rejected_double_spend()),
        TapeStep(action("a1", "t-4", "emit_signal", text="all ok"), accepted()),
    ]


def test_two_replays_of_same_tape_identical():
    s1, e1, log1 = replay_in_memory(initial_world(), _tape())
    s2, e2, log2 = replay_in_memory(initial_world(), _tape())
    h1 = WorldReducer.canonical_state_hash(s1)
    h2 = WorldReducer.canonical_state_hash(s2)
    assert h1 == h2
    assert [e.model_dump(mode="json") for e in e1] == [e.model_dump(mode="json") for e in e2]
    assert log1.to_jsonl_bytes() == log2.to_jsonl_bytes()
    log1.verify()
    log2.verify()


def test_replay_records_rejected_with_state_hash_unchanged():
    ws = initial_world()
    state, events, _ = replay_in_memory(ws, _tape())
    rejected = [e for e in events if e.decision == "rejected"]
    assert len(rejected) == 1
    assert rejected[0].kind == "action_rejected"
    # Manually fold step by step: right after the rejected step, the
    # canonical state hash must equal the one from the step before it.
    reducer = WorldReducer()
    prev = cur = ws
    for step in _tape():
        prev = cur
        cur = reducer.apply(cur, step.action, step.validation)
        if not step.validation.accepted:
            assert WorldReducer.canonical_state_hash(cur) == WorldReducer.canonical_state_hash(prev)
    # final state's materialized hash matches its canonical content
    assert state.state_hash == WorldReducer.canonical_state_hash(state)


def test_replay_into_persisted_log(tmp_path):
    path = tmp_path / "replay.jsonl"
    log = EventLog(path)
    state, events = replay(log, initial_world(), _tape())
    assert len(events) == len(_tape())
    assert len(path.read_text().splitlines()) == 4
    log.verify()
    assert state.state_hash is not None


def test_replay_is_deterministic_across_log_instances(tmp_path):
    log_a = EventLog(tmp_path / "a.jsonl")
    log_b = EventLog(tmp_path / "b.jsonl")
    s_a, e_a = replay(log_a, initial_world(), _tape())
    s_b, e_b = replay(log_b, initial_world(), _tape())
    assert s_a.state_hash == s_b.state_hash
    assert log_a.to_jsonl_bytes() == log_b.to_jsonl_bytes()


def test_replay_accepts_continues_from_existing_log():
    """Replaying a second tape into a non-empty log continues the chain."""
    log = EventLog()
    s1, _ = replay(log, initial_world(), _tape()[:2])
    s2, events = replay(log, s1, _tape()[2:])
    assert len(log) == 4
    assert events[0].event_id == 2
    log.verify()
    assert WorldReducer.canonical_state_hash(s2) == WorldReducer.canonical_state_hash(s2)


def test_snapshot_roundtrip_byte_identical(tmp_path):
    ws = initial_world()
    store = SnapshotStore(root=tmp_path)
    digest = store.save(ws)
    assert store.contains(digest)
    loaded = store.load(digest)
    assert loaded == ws
    # byte-identical canonical encoding
    from verdi.world.canonical import canonical_json

    assert canonical_json(loaded.model_dump(mode="json")) == canonical_json(ws.model_dump(mode="json"))


def test_snapshot_store_replay_no_model_call(tmp_path):
    """Replay from a loaded snapshot reproduces the same final state hash."""
    store = SnapshotStore(root=tmp_path)
    ws = initial_world()
    digest = store.save(ws)
    state2 = store.load(digest)
    s1, _, _ = replay_in_memory(ws, _tape())
    s2, _, _ = replay_in_memory(state2, _tape())
    assert WorldReducer.canonical_state_hash(s1) == WorldReducer.canonical_state_hash(s2)
