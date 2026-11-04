"""Reducer tests: invariants, frozen state, canonical hashing."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.world.helpers import accepted, action, initial_world, rejected_double_spend

from verdi_agents.world import InvariantViolationError, WorldReducer
from verdi_agents.world.canonical import canonical_sha256


def test_canonical_hash_is_stable_and_sorted():
    ws = initial_world()
    h1 = WorldReducer.canonical_state_hash(ws)
    h2 = WorldReducer.canonical_state_hash(initial_world())
    assert h1 == h2
    substantive = {
        k: v for k, v in ws.model_dump(mode="json").items() if k not in ("state_hash", "tick", "event_cursor")
    }
    assert h1 == canonical_sha256(substantive)


def test_apply_returns_new_frozen_state():
    reducer = WorldReducer()
    ws = initial_world()
    nxt = reducer.apply(ws, action("a1", "t-1", "produce", resource="credits", amount=3), accepted())
    assert nxt is not ws
    # original untouched
    assert ws.tick == 0 and ws.agents["a1"].resources == {"credits": 10.0}
    # result is frozen
    with pytest.raises(ValidationError):
        nxt.tick = 99  # type: ignore[misc]
    # state_hash materialized and equals canonical
    assert nxt.state_hash == WorldReducer.canonical_state_hash(nxt)


def test_tick_and_cursor_monotonic_on_accepted():
    reducer = WorldReducer()
    ws = initial_world()
    nxt = reducer.apply(ws, action("a1", "t-1", "produce", resource="credits", amount=1), accepted())
    assert nxt.tick == ws.tick + 1
    assert nxt.event_cursor == ws.event_cursor + 1


def test_rejected_action_no_substantive_change_and_hash_unchanged():
    reducer = WorldReducer()
    ws = initial_world()
    before = WorldReducer.canonical_state_hash(ws)
    nxt = reducer.apply(ws, action("a1", "t-1", "produce", resource="credits", amount=100), rejected_double_spend())
    after = WorldReducer.canonical_state_hash(nxt)
    assert before == after, "rejected action must not change substantive state hash"
    assert nxt.agents == ws.agents
    assert nxt.world_resources == ws.world_resources
    # bookkeeping still advanced
    assert nxt.tick == ws.tick + 1
    assert nxt.event_cursor == ws.event_cursor + 1


def test_transfer_moves_balance():
    reducer = WorldReducer()
    ws = initial_world()
    nxt = reducer.apply(
        ws, action("a1", "t-1", "transfer", to="a2", resource="credits", units=4), accepted()
    )
    assert nxt.agents["a1"].resources == {"credits": 6.0}
    assert nxt.agents["a2"].resources == {"credits": 9.0}


def test_deposit_and_withdraw_move_world_resources():
    reducer = WorldReducer()
    ws = initial_world()
    nxt = reducer.apply(ws, action("a1", "t-1", "deposit", resource="credits", amount=2), accepted())
    assert nxt.agents["a1"].resources == {"credits": 8.0}
    assert nxt.world_resources == {**ws.world_resources, "credits": 2.0}
    nxt2 = reducer.apply(nxt, action("a1", "t-2", "withdraw", resource="credits", amount=2), accepted())
    assert nxt2.agents["a1"].resources == {"credits": 10.0}
    assert nxt2.world_resources["credits"] == 0.0


def test_double_spend_rejected_via_invariant():
    """An *accepted* overspend must raise, never produce a negative balance."""
    reducer = WorldReducer()
    ws = initial_world()  # a1 has 10 credits
    with pytest.raises(InvariantViolationError, match="negative balance"):
        reducer.apply(ws, action("a1", "t-1", "transfer", to="a2", resource="credits", units=11), accepted())


def test_overdraw_world_resources_raises():
    reducer = WorldReducer()
    ws = initial_world()
    with pytest.raises(InvariantViolationError, match="negative world balance"):
        reducer.apply(ws, action("a1", "t-1", "withdraw", resource="food", amount=101), accepted())


def test_unknown_actor_raises():
    reducer = WorldReducer()
    ws = initial_world()
    with pytest.raises(InvariantViolationError, match="unknown actor"):
        reducer.apply(ws, action("ghost", "t-1", "produce", resource="credits", amount=1), accepted())


def test_reducer_does_not_mutate_input():
    reducer = WorldReducer()
    ws = initial_world()
    reducer.apply(ws, action("a1", "t-1", "emit_signal", text="hi"), accepted())
    assert ws.agents["a1"].public_signals == []


def test_canonical_hash_excludes_bookkeeping():
    ws = initial_world()
    same_content_later = ws.model_copy(update={"tick": ws.tick + 5, "event_cursor": ws.event_cursor + 5})
    assert WorldReducer.canonical_state_hash(ws) == WorldReducer.canonical_state_hash(same_content_later)
