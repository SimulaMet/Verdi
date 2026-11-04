"""Tests for agent_observation + validate_action (pure, deterministic)."""
from __future__ import annotations

import pytest

from verdi_agents.schemas import AgentState, ProposedAction, RightsState, ValueProfile, WorldState
from verdi_agents.world.observation import agent_observation
from verdi_agents.world.validate import validate_action


def _values() -> ValueProfile:
    return ValueProfile(integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5)


def _agent(agent_id: str, **kwargs) -> AgentState:
    return AgentState(
        agent_id=agent_id,
        role="trader",
        persona_summary="test agent",
        declared_values=_values(),
        **kwargs,
    )


def _world(*agents: AgentState, world_resources: dict | None = None) -> WorldState:
    return WorldState(
        world_id="w-test",
        tick=3,
        law_version="1",
        constitution_version="1",
        agents={a.agent_id: a for a in agents},
        world_resources=world_resources or {},
        event_cursor=0,
    )


def _action(actor: str, kind: str, **arguments) -> ProposedAction:
    return ProposedAction(actor_id=actor, task_id="t-1", action_kind=kind, arguments=arguments)


# ------------------------------------------------------------------ #
# observation
# ------------------------------------------------------------------ #
def test_observation_own_resources_only():
    a1 = _agent("a1", resources={"credits": 10.0, "food": 2.0})
    a2 = _agent("a2", resources={"credits": 999.0})
    ws = _world(a1, a2)
    obs = agent_observation(ws, "a1")
    assert obs.resources == {"credits": 10.0, "food": 2.0}
    assert obs.tick == 3
    assert obs.agent_id == "a1"
    assert obs.public_signals == []
    # own signals only, never others'
    assert "a2" not in str(obs.public_signals)


def test_observation_unknown_agent_raises_keyerror():
    ws = _world(_agent("a1"))
    with pytest.raises(KeyError):
        agent_observation(ws, "ghost")


def test_observation_social_signals_from_neighbor():
    a1 = _agent("a1", social_edges=["a2", "a3"])
    a2 = _agent("a2", public_signals=["hello", "again"])
    a3 = _agent("a3", public_signals=["ping"])
    ws = _world(a1, a2, a3)
    obs = agent_observation(ws, "a1")
    # neighbors sorted, signals in stored order
    assert obs.social_signals == [
        {"agent_id": "a2", "text": "hello"},
        {"agent_id": "a2", "text": "again"},
        {"agent_id": "a3", "text": "ping"},
    ]


def test_observation_dangling_edge_skipped():
    a1 = _agent("a1", social_edges=["ghost", "a2"])
    a2 = _agent("a2", public_signals=["s"])
    obs = agent_observation(_world(a1, a2), "a1")
    assert obs.social_signals == [{"agent_id": "a2", "text": "s"}]


def test_observation_eligible_kinds_full_when_sane():
    obs = agent_observation(_world(_agent("a1")), "a1")
    assert obs.eligible_action_kinds == (
        "deposit",
        "emit_signal",
        "produce",
        "transfer",
        "withdraw",
    )


def test_observation_eligible_kinds_under_sanction():
    a1 = _agent("a1", under_sanction=True)
    ws = _world(a1)
    obs = agent_observation(ws, "a1")
    assert "transfer" not in obs.eligible_action_kinds
    assert "deposit" not in obs.eligible_action_kinds
    assert "produce" in obs.eligible_action_kinds
    assert "emit_signal" in obs.eligible_action_kinds
    assert obs.rights == RightsState()


def test_observation_is_deterministic():
    a1 = _agent(
        "a1",
        resources={"credits": 4.0},
        social_edges=["a2"],
        public_signals=["x"],
    )
    a2 = _agent("a2", public_signals=["y", "z"])
    ws = _world(a1, a2, world_resources={"credits": 7.0})
    o1 = agent_observation(ws, "a1")
    o2 = agent_observation(ws, "a1")
    assert o1 == o2
    assert o1.world_resources == {"credits": 7.0}
    # observation did not mutate the state
    assert ws.agents["a1"].resources == {"credits": 4.0}


# ------------------------------------------------------------------ #
# validation
# ------------------------------------------------------------------ #
def test_validate_transfer_success():
    a1 = _agent("a1", resources={"credits": 10.0})
    a2 = _agent("a2")
    ws = _world(a1, a2)
    result = validate_action(ws, _action("a1", "transfer", to="a2", resource="credits", units=4.0))
    assert result.accepted is True
    assert result.blocked_by is None
    assert result.attempted_violation_codes == []


def test_validate_transfer_overdraft_rejected_no_world_change():
    a1 = _agent("a1", resources={"credits": 10.0})
    a2 = _agent("a2")
    ws = _world(a1, a2)
    result = validate_action(ws, _action("a1", "transfer", to="a2", resource="credits", units=11.0))
    assert result.accepted is False
    assert result.blocked_by == "law:no_double_spend"
    assert "overdraft" in result.attempted_violation_codes
    assert result.details
    # validation never mutates state
    assert ws.agents["a1"].resources == {"credits": 10.0}
    assert ws.agents["a2"].resources == {}


def test_validate_transfer_unknown_recipient():
    a1 = _agent("a1", resources={"credits": 10.0})
    ws = _world(a1)
    result = validate_action(ws, _action("a1", "transfer", to="ghost", resource="credits", units=1.0))
    assert result.accepted is False
    assert result.blocked_by == "law:unknown_recipient"
    assert "unknown_recipient" in result.attempted_violation_codes


def test_validate_under_sanction_transfer_rejected():
    a1 = _agent("a1", under_sanction=True, resources={"credits": 10.0})
    a2 = _agent("a2")
    ws = _world(a1, a2)
    for action in (
        _action("a1", "transfer", to="a2", resource="credits", units=1.0),
        _action("a1", "deposit", resource="credits", amount=1.0),
    ):
        result = validate_action(ws, action)
        assert result.accepted is False
        assert result.blocked_by == "law:under_sanction"
    # sanctioned agent may still produce / emit
    assert validate_action(ws, _action("a1", "produce", resource="food", amount=1.0)).accepted
    assert validate_action(ws, _action("a1", "emit_signal", text="ok")).accepted


def test_validate_unknown_actor_rejected():
    ws = _world(_agent("a1"))
    result = validate_action(ws, _action("ghost", "produce", resource="food", amount=1.0))
    assert result.accepted is False
    assert result.blocked_by == "law:unknown_actor"
    assert result.attempted_violation_codes or result.details


def test_validate_withdraw_insufficient_world():
    a1 = _agent("a1", resources={"credits": 10.0})
    ws = _world(a1, world_resources={"credits": 2.0})
    result = validate_action(ws, _action("a1", "withdraw", resource="credits", amount=5.0))
    assert result.accepted is False
    assert result.blocked_by == "law:insufficient_world"
    assert "insufficient_world" in result.attempted_violation_codes
    ok = validate_action(ws, _action("a1", "withdraw", resource="credits", amount=2.0))
    assert ok.accepted is True


def test_validate_deposit_accepted():
    a1 = _agent("a1", resources={"credits": 10.0})
    ws = _world(a1, world_resources={"credits": 2.0})
    result = validate_action(ws, _action("a1", "deposit", resource="credits", amount=3.0))
    assert result.accepted is True


def test_validate_produce_requires_positive_amount():
    a1 = _agent("a1")
    ws = _world(a1)
    result = validate_action(ws, _action("a1", "produce", resource="food", amount=0))
    assert result.accepted is False
    assert result.blocked_by == "law:non_positive_amount"
    assert result.details


def test_validate_emit_signal_requires_nonempty_text():
    a1 = _agent("a1")
    ws = _world(a1)
    result = validate_action(ws, _action("a1", "emit_signal", text=""))
    assert result.accepted is False
    assert result.blocked_by == "law:empty_signal"
    assert validate_action(ws, _action("a1", "emit_signal", text="hi")).accepted


def test_validate_unknown_kind_rejected():
    a1 = _agent("a1")
    ws = _world(a1)
    result = validate_action(ws, _action("a1", "teleport", where="moon"))
    assert result.accepted is False
    assert result.blocked_by == "law:unknown_kind"
    assert result.details
