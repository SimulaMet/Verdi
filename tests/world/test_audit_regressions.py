"""Regression tests for defects found in the 2026-11 system audit (AUDIT.md)."""
from __future__ import annotations

import pytest

from verdi_agents.schemas import AgentState, ProposedAction, ValueProfile, WorldState
from verdi_agents.world.reducer import InvariantViolationError, WorldReducer
from verdi_agents.world.validate import validate_action

_VALUES = ValueProfile(
    integrity=0.8, fairness=0.9, cooperation=0.7, autonomy=0.5, solidarity=0.85
)


def _state() -> WorldState:
    def agent(aid: str) -> AgentState:
        return AgentState(agent_id=aid, role="citizen", persona_summary="p",
                          declared_values=_VALUES, resources={"food": 10.0})

    return WorldState(world_id="w", tick=1, event_cursor=1, law_version="law-1",
                      constitution_version="cons-1",
                      agents={"a": agent("a"), "b": agent("b")},
                      world_resources={"food": 5.0})


def _action(kind: str, **arguments) -> ProposedAction:
    return ProposedAction(actor_id="a", action_kind=kind, arguments=arguments, task_id="t-1")


def test_self_transfer_is_rejected_without_a_sanctionable_code():
    result = validate_action(_state(), _action("transfer", to="a", resource="food", units=10))
    assert not result.accepted
    assert result.attempted_violation_codes == ["self_transfer"]


def test_reducer_refuses_self_transfer_instead_of_minting_resources():
    with pytest.raises(InvariantViolationError, match="self-transfer"):
        WorldReducer._transfer(
            _state().agents["a"], "a", dict(_state().agents),
            {"to": "a", "resource": "food", "units": 10},
        )


@pytest.mark.parametrize("kind, args", [
    ("transfer", {"to": "b", "resource": ["food"], "units": 1}),
    ("deposit", {"resource": None, "amount": 1}),
    ("withdraw", {"resource": {"x": 1}, "amount": 1}),
    ("produce", {"resource": ["food"], "amount": 1}),
])
def test_non_string_resource_is_rejected_not_crashing(kind, args):
    result = validate_action(_state(), _action(kind, **args))
    assert not result.accepted
    assert result.attempted_violation_codes == ["missing_argument"]


def test_branch_from_a_prefix_ending_in_a_rejected_event():
    from tests.world.helpers import accepted, action, initial_world, rejected_double_spend

    from verdi_agents.world.branch import BranchSpec, branch_from_snapshot
    from verdi_agents.world.log import EventLog
    from verdi_agents.world.replay import TapeStep, replay

    log = EventLog()
    state, _ = replay(log, initial_world(), [
        TapeStep(action=action("a1", "p-1", "emit_signal", text="s0"), validation=accepted()),
        TapeStep(action=action("a1", "p-2", "transfer", recipient="a2", amount=1.0),
                 validation=rejected_double_spend()),
    ])
    assert log.events[-1].after_hash is None
    spec = BranchSpec(origin_state_hash=WorldReducer.canonical_state_hash(state),
                      origin_event_count=len(log), origin_tick=state.tick,
                      branch_id="b", treatment_seed=1)
    tape = [TapeStep(action=action("a2", "t-1", "emit_signal", text="x"), validation=accepted())]
    result = branch_from_snapshot(state, log, tape, spec)
    assert len(result.log) == len(log) + 1


def test_snapshot_store_rejects_traversal_and_tampering(tmp_path):
    from tests.world.helpers import initial_world

    from verdi_agents.world.snapshot import SnapshotError, SnapshotStore

    store = SnapshotStore(tmp_path)
    digest = store.save(initial_world())
    assert store.load(digest) == initial_world()
    with pytest.raises(SnapshotError, match="invalid snapshot digest"):
        store.load("../" + digest[3:])
    assert not store.contains("../../etc/passwd")
    snap = tmp_path / "snapshots" / f"{digest}.json"
    tampered = snap.read_text().replace('"a1"', '"zz"', 1)
    snap.write_text(tampered)
    with pytest.raises(SnapshotError, match="content hash"):
        SnapshotStore(tmp_path).load(digest)


def test_produce_policy_cap_is_cumulative_per_tick_without_economy():
    rules = {"max_produce_units_per_tick": 5}
    ok = validate_action(_state(), _action("produce", resource="food", amount=5),
                         policy_rules=rules, produced_this_tick={"food": 0.0})
    over = validate_action(_state(), _action("produce", resource="food", amount=5),
                           policy_rules=rules, produced_this_tick={"food": 5.0})
    assert ok.accepted
    assert not over.accepted and over.blocked_by == "constitution:max_produce_units_per_tick"


def test_role_only_consumption_override_is_rejected():
    from verdi_agents.world.economy import parse_economy_spec

    with pytest.raises(ValueError, match="consumption_per_tick"):
        parse_economy_spec({"version": 1, "consumption_per_tick": {"food": 1},
                            "role_consumption": {"guard": {"water": 2}}})


def test_amount_that_would_overflow_a_balance_is_rejected():
    big = _state().model_copy(update={"agents": {
        **_state().agents,
        "a": _state().agents["a"].model_copy(update={"resources": {"food": 1e308}})}})
    result = validate_action(big, _action("produce", resource="food", amount=1e308))
    assert not result.accepted


@pytest.mark.parametrize("kind, args", [
    ("produce", {"resource": "food", "amount": True}),
    ("deposit", {"resource": "food", "amount": True}),
])
def test_boolean_amount_is_not_a_quantity(kind, args):
    assert not validate_action(_state(), _action(kind, **args)).accepted


def test_duplicate_objective_ids_are_rejected():
    from verdi_agents.world.economy import parse_task_objectives

    with pytest.raises(ValueError, match="duplicate objective_id"):
        parse_task_objectives({"deliveries": [
            {"objective_id": "obj-01", "due_tick": 4, "resource": "food", "amount": 1},
            {"due_tick": 6, "start_tick": 3, "resource": "food", "amount": 1},  # auto obj-01
        ]})
