"""Executable constitutional policy: boundary, type and invariant tests."""
from __future__ import annotations

import pytest

from verdi.schemas import AgentState, ProposedAction, ValueProfile, WorldState
from verdi.world.policy import validate_enforceable_rules
from verdi.world.validate import validate_action


def _state() -> WorldState:
    values = ValueProfile(
        integrity=0.8, fairness=0.8, cooperation=0.8, autonomy=0.8, solidarity=0.8
    )
    agents = {
        aid: AgentState(
            agent_id=aid, role="citizen", persona_summary="Citizen",
            declared_values=values, resources={"food": 10.0},
        )
        for aid in ("alice", "bob")
    }
    return WorldState(
        world_id="policy-test", tick=0, event_cursor=0,
        law_version="universal", constitution_version="base",
        agents=agents, world_resources={"food": 20.0},
    )


def _action(kind: str, arguments: dict) -> ProposedAction:
    return ProposedAction(
        actor_id="alice", task_id="task-1", action_kind=kind, arguments=arguments
    )


def test_legacy_behavior_unchanged_without_policy() -> None:
    state = _state()
    action = _action("transfer", {"to": "bob", "resource": "food", "units": 5})
    assert validate_action(state, action).accepted
    assert validate_action(state, action, policy_rules={}).accepted


def test_transfer_cap_is_binding_and_has_distinct_rejection() -> None:
    state = _state()
    action = _action("transfer", {"to": "bob", "resource": "food", "units": 5})
    result = validate_action(state, action, policy_rules={"max_transfer_units": 3})
    assert not result.accepted
    assert result.blocked_by == "constitution:max_transfer_units"
    assert "policy_limit" in result.attempted_violation_codes
    assert validate_action(state, action, policy_rules={"max_transfer_units": 5}).accepted


def test_withdraw_quota_and_commons_reserve() -> None:
    state = _state()
    action = _action("withdraw", {"resource": "food", "amount": 3})
    limit = validate_action(state, action, policy_rules={"max_withdraw_units": 2})
    assert limit.blocked_by == "constitution:max_withdraw_units"
    reserve = validate_action(
        state, action, policy_rules={"min_commons_reserve": {"food": 18}}
    )
    assert reserve.blocked_by == "constitution:min_commons_reserve"
    assert validate_action(
        state, action, policy_rules={"min_commons_reserve": {"food": 17}}
    ).accepted


def test_immutable_overdraft_still_rejected() -> None:
    action = _action("transfer", {"to": "bob", "resource": "food", "units": 11})
    outcome = validate_action(_state(), action, policy_rules={"max_transfer_units": 2})
    assert outcome.blocked_by == "law:no_double_spend"


@pytest.mark.parametrize("rules", [
    {"max_transfer_units": 0},
    {"max_transfer_units": -1},
    {"max_transfer_units": True},
    {"max_transfer_units": "10"},
    {"max_withdraw_units": float("inf")},
    {"min_commons_reserve": {"food": -1}},
    {"min_commons_reserve": []},
])
def test_malformed_recognized_policy_fails_closed(rules: dict) -> None:
    with pytest.raises(ValueError):
        validate_enforceable_rules(rules)


def test_unrecognized_narrative_rule_does_not_execute() -> None:
    validate_enforceable_rules({"aspirational_fairness": "everybody shares"})
    assert validate_action(
        _state(), _action("withdraw", {"resource": "food", "amount": 3}),
        policy_rules={"aspirational_fairness": "everybody shares"},
    ).accepted
