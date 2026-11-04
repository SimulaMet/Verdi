"""P0-B: unit tests for the opt-in constrained economy + task oracle.

Covers production-limit rejection, production-cost deduction and
conservation, consumption non-negativity (no overdraft ever), deficit
tracking, delivery success/failure from committed state only, and the
typed ``max_produce_units_per_tick`` policy key. All functions are pure;
no campaign, no LLM, no RNG.
"""
from __future__ import annotations

import pytest

from verdi_agents.schemas import AgentState, ProposedAction, ValueProfile, WorldState
from verdi_agents.world.economy import (
    apply_consumption,
    apply_production_deduction,
    check_delivery,
    check_production_limits,
    deduct_production_cost,
    parse_economy_spec,
    parse_task_objectives,
    production_input_cost,
)
from verdi_agents.world.policy import ENFORCEABLE_KEYS, policy_block, validate_enforceable_rules
from verdi_agents.world.validate import validate_action

_VALUES = ValueProfile(
    integrity=0.8, fairness=0.9, cooperation=0.7, autonomy=0.5, solidarity=0.85
)


def _agent(agent_id: str, resources: dict[str, float], role: str = "farmer") -> AgentState:
    return AgentState(
        agent_id=agent_id, role=role, persona_summary="p",
        declared_values=_VALUES, resources=resources,
    )


def _state(agents: list[AgentState], world: dict[str, float]) -> WorldState:
    return WorldState(
        world_id="world-eco", tick=1, event_cursor=1,
        law_version="law-1", constitution_version="cons-1",
        agents={a.agent_id: a for a in agents}, world_resources=world,
    )


def _spec(**overrides):
    base = {
        "version": 1,
        "production_limit_per_tick": {"food": 2.0},
        "production_cost": {"food": 1.0},
        "consumption_per_tick": {"food": 1.0},
    }
    base.update(overrides)
    return parse_economy_spec(base)


# --------------------------------------------------------------------------- #
# spec parsing
# --------------------------------------------------------------------------- #
def test_parse_absent_block_is_legacy_none() -> None:
    assert parse_economy_spec(None) is None
    assert parse_economy_spec({}) is None


def test_parse_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        parse_economy_spec({"version": 99})


def test_parse_rejects_negative_numbers() -> None:
    with pytest.raises(ValueError):
        parse_economy_spec({"version": 1, "production_limit_per_tick": {"food": -1.0}})


def test_parse_role_consumption() -> None:
    spec = _spec(role_consumption={"farmer": {"food": 0.5}})
    assert spec.role_consumption[("farmer", "food")] == 0.5


# --------------------------------------------------------------------------- #
# production limits (reject semantics)
# --------------------------------------------------------------------------- #
def test_production_limit_rejects_excess() -> None:
    spec = _spec()
    blocked = check_production_limits(spec, "food", 3.0, already_produced_this_tick=0.0)
    assert blocked is not None
    assert blocked[0] == "max_produce_units_per_tick"


def test_production_limit_accumulates_within_tick() -> None:
    spec = _spec()
    assert check_production_limits(spec, "food", 1.0, already_produced_this_tick=1.0) is None
    assert check_production_limits(spec, "food", 1.0, already_produced_this_tick=1.5) is not None


def test_production_limit_unlimited_resource_passes() -> None:
    spec = _spec()
    assert check_production_limits(spec, "credits", 99.0, already_produced_this_tick=0.0) is None


def test_validator_rejects_over_cap_produce() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"credits": 100.0})], {})
    action = ProposedAction(actor_id="a1", task_id="t1", action_kind="produce",
                            arguments={"resource": "food", "amount": 3.0})
    v = validate_action(state, action, economy_spec=spec)
    assert v.accepted is False
    assert v.blocked_by == "law:production_limit"
    assert "production_limit" in (v.attempted_violation_codes or [])


def test_validator_accepts_in_limit_affordable_produce() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"credits": 10.0})], {})
    action = ProposedAction(actor_id="a1", task_id="t1", action_kind="produce",
                            arguments={"resource": "food", "amount": 2.0})
    v = validate_action(state, action, economy_spec=spec)
    assert v.accepted is True


def test_validator_rejects_unaffordable_production_input() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"credits": 0.5})], {})
    action = ProposedAction(actor_id="a1", task_id="t1", action_kind="produce",
                            arguments={"resource": "food", "amount": 2.0})
    v = validate_action(state, action, economy_spec=spec)
    assert v.accepted is False
    assert v.blocked_by == "law:insufficient_production_input"
    assert "insufficient_production_input" in (v.attempted_violation_codes or [])


def test_validator_without_spec_is_legacy_unlimited() -> None:
    """No economy_spec => today's behavior: unlimited produce, no cost."""
    state = _state([_agent("a1", {"credits": 0.0})], {})
    action = ProposedAction(actor_id="a1", task_id="t1", action_kind="produce",
                            arguments={"resource": "food", "amount": 10**6})
    assert validate_action(state, action).accepted is True


# --------------------------------------------------------------------------- #
# production cost + conservation
# --------------------------------------------------------------------------- #
def test_deduction_personal_credits_first() -> None:
    """Conservation: the reducer adds the output; the deduction removes the
    finite input from personal credits (and the pool when personal runs out)."""
    spec = _spec()
    # simulate: producer produced 2 food (reducer already added it), cost 2.
    state = _state([_agent("a1", {"credits": 10.0, "food": 2.0})], {})
    nxt = apply_production_deduction(state, "a1", resource="food", amount=2.0, spec=spec)
    res = nxt.agents["a1"].resources
    assert res["food"] == pytest.approx(2.0)   # output kept
    assert res["credits"] == pytest.approx(8.0)  # 2 units x 1 credit deducted


def test_deduction_falls_back_to_commons_pool() -> None:
    spec = _spec()
    # personal credits exhausted; the shared pool covers the remainder.
    state = _state([_agent("a1", {"credits": 0.5, "food": 2.0})], {"credits": 5.0})
    nxt = apply_production_deduction(state, "a1", resource="food", amount=2.0, spec=spec)
    assert nxt.agents["a1"].resources["credits"] == pytest.approx(0.0)
    assert nxt.world_resources["credits"] == pytest.approx(3.5)  # 5 - 1.5


def test_deduction_unaffordable_raises() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"credits": 0.0, "food": 2.0})], {"credits": 0.0})
    with pytest.raises(ValueError):
        apply_production_deduction(state, "a1", resource="food", amount=2.0, spec=spec)


def test_deduct_production_cost_conserves_total_credits() -> None:
    agent_res, world_res = deduct_production_cost(
        {"credits": 1.0, "food": 4.0}, {"credits": 4.0}, cost=3.0,
    )
    total_before = 1.0 + 4.0
    total_after = agent_res["credits"] + world_res["credits"]
    assert total_after == pytest.approx(total_before - 3.0)  # exactly the cost leaves
    assert agent_res["credits"] == pytest.approx(0.0)       # personal first
    assert world_res["credits"] == pytest.approx(2.0)        # pool covers the rest


def test_apply_production_free_resource_no_cost() -> None:
    spec = _spec(production_cost={})
    state = _state([_agent("a1", {"credits": 3.0, "food": 2.0})], {})
    nxt = apply_production_deduction(state, "a1", resource="food", amount=2.0, spec=spec)
    assert nxt.agents["a1"].resources["credits"] == pytest.approx(3.0)
    assert nxt.agents["a1"].resources["food"] == pytest.approx(2.0)


def test_production_input_cost_linear() -> None:
    spec = _spec()
    assert production_input_cost(spec, "food", 3.0) == pytest.approx(3.0)
    assert production_input_cost(spec, "credits", 5.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# consumption: non-negativity + deficit
# --------------------------------------------------------------------------- #
def test_consumption_clamps_at_zero_never_negative() -> None:
    spec = _spec(consumption_per_tick={"food": 2.0})
    state = _state([_agent("a1", {"food": 1.0})], {})
    nxt, payload = apply_consumption(state, spec)
    assert nxt.agents["a1"].resources["food"] == pytest.approx(0.0)
    assert "a1" in payload["deficit_agents"]
    assert payload["tick_consumed"]["a1"]["shortfall"]["food"] == pytest.approx(1.0)


def test_consumption_full_payment_no_deficit() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"food": 5.0})], {})
    nxt, payload = apply_consumption(state, spec)
    assert nxt.agents["a1"].resources["food"] == pytest.approx(4.0)
    assert payload["deficit_agents"] == []
    assert payload["tick_consumed"]["a1"]["paid"]["food"] == pytest.approx(1.0)


def test_consumption_role_override() -> None:
    spec = _spec(role_consumption={"farmer": {"food": 0.5}})
    state = _state([_agent("a1", {"food": 5.0}, role="farmer"),
                    _agent("b1", {"food": 5.0}, role="trader")], {})
    nxt, _ = apply_consumption(state, spec)
    assert nxt.agents["a1"].resources["food"] == pytest.approx(4.5)
    assert nxt.agents["b1"].resources["food"] == pytest.approx(4.0)


def test_consumption_is_deterministic_state_diff_only() -> None:
    spec = _spec()
    state = _state([_agent("a1", {"food": 3.0}), _agent("b1", {"food": 0.0})], {})
    _, p1 = apply_consumption(state, spec)
    _, p2 = apply_consumption(state, spec)
    assert p1 == p2


# --------------------------------------------------------------------------- #
# delivery oracle
# --------------------------------------------------------------------------- #
def _objective(**overrides) -> dict:
    base = {
        "objective_id": "obj-00", "start_tick": 0, "due_tick": 10,
        "resource": "food", "amount": 3.0, "target_pool": "world",
    }
    base.update(overrides)
    return base


def test_delivery_success_world_pool_delta() -> None:
    before = _state([], {"food": 20.0})
    after = _state([], {"food": 24.0})
    out = check_delivery(_objective(), before, after)
    assert out["status"] == "completed"
    assert out["delta"] == pytest.approx(4.0)


def test_delivery_failure_when_delta_insufficient() -> None:
    before = _state([], {"food": 20.0})
    after = _state([], {"food": 22.0})
    out = check_delivery(_objective(amount=3.0), before, after)
    assert out["status"] == "failed"
    assert out["delta"] == pytest.approx(2.0)


def test_delivery_agent_target() -> None:
    obj = _objective(target_pool="marta", amount=3.0)
    before = _state([_agent("marta", {"food": 5.0})], {})
    after = _state([_agent("marta", {"food": 8.0})], {})
    assert check_delivery(obj, before, after)["status"] == "completed"
    after_short = _state([_agent("marta", {"food": 7.0})], {})
    assert check_delivery(obj, before, after_short)["status"] == "failed"


def test_delivery_baseline_is_start_not_t0_absolute() -> None:
    """Success is a DELTA vs the start snapshot, not an absolute level."""
    before = _state([], {"food": 40.0})
    after = _state([], {"food": 42.0})
    assert check_delivery(_objective(amount=3.0), before, after)["status"] == "failed"
    assert check_delivery(_objective(amount=2.0), before, after)["status"] == "completed"


def test_parse_task_objectives_defaults_and_errors() -> None:
    objs = parse_task_objectives({"deliveries": [
        {"due_tick": 5, "resource": "food", "amount": 2.0},
    ]})
    assert objs[0]["objective_id"] == "obj-00"
    assert objs[0]["start_tick"] == 0
    assert objs[0]["target_pool"] == "world"
    assert parse_task_objectives(None) == []
    with pytest.raises(ValueError):
        parse_task_objectives({"deliveries": [{"due_tick": 1, "resource": "food", "amount": -1}]})
    with pytest.raises(ValueError):
        parse_task_objectives({"deliveries": [{"start_tick": 4, "due_tick": 2,
                                               "resource": "food", "amount": 1.0}]})


# --------------------------------------------------------------------------- #
# typed enforceable key max_produce_units_per_tick
# --------------------------------------------------------------------------- #
def test_max_produce_key_is_enforceable_and_validated() -> None:
    assert "max_produce_units_per_tick" in ENFORCEABLE_KEYS
    validate_enforceable_rules({"max_produce_units_per_tick": 2.0})
    with pytest.raises(ValueError):
        validate_enforceable_rules({"max_produce_units_per_tick": -1.0})
    with pytest.raises(ValueError):
        validate_enforceable_rules({"max_produce_units_per_tick": "two"})


def test_policy_block_rejects_over_cap_produce() -> None:
    rules = {"max_produce_units_per_tick": 2.0}
    blocked = policy_block("produce", {"resource": "food", "amount": 3.0}, {}, rules)
    assert blocked is not None
    assert blocked[0] == "max_produce_units_per_tick"
    ok = policy_block("produce", {"resource": "food", "amount": 2.0}, {}, rules)
    assert ok is None


def test_policy_block_other_kinds_unaffected_by_produce_cap() -> None:
    rules = {"max_produce_units_per_tick": 2.0}
    assert policy_block("transfer", {"to": "b", "resource": "food", "units": 9.0}, {}, rules) is None
