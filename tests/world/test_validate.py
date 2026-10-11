"""Regression tests for the validator/reducer deposit invariant (Tranche 11 follow-up).

The live Stage B seed-1 (fix1) run crashed with
``InvariantViolationError: negative balance: agent=b01 resource=credits
value=-2.0`` because ``validate_action`` accepted a ``deposit`` that exceeded
the agent's holdings: the deposit/withdraw branch checked only the WORLD pool
(on ``withdraw``), never ``held - amount >= 0`` on ``deposit``. An over-deposit
passed validation and the reducer's final non-negative assertion failed.
"""
from __future__ import annotations

from verdi.schemas import AgentState, ProposedAction, ValueProfile, WorldState
from verdi.world.reducer import WorldReducer
from verdi.world.validate import validate_action

_VALUES = ValueProfile(
    integrity=0.8, fairness=0.9, cooperation=0.7, autonomy=0.5, solidarity=0.85
)


def _state_with_balance(agent: str, resource: str, balance: float) -> WorldState:
    return WorldState(
        world_id="world-1",
        tick=1,
        event_cursor=1,
        law_version="law-1",
        constitution_version="cons-1",
        agents={
            agent: AgentState(
                agent_id=agent,
                role="citizen",
                persona_summary="Cares about outcomes",
                declared_values=_VALUES,
                resources={resource: balance},
            ),
        },
        world_resources={"energy": 10.0},
    )


def _deposit(actor: str, resource: str, amount: float) -> ProposedAction:
    return ProposedAction(
        actor_id=actor,
        task_id="t1",
        action_kind="deposit",
        arguments={"resource": resource, "amount": amount},
    )


def test_deposit_exceeding_balance_is_rejected_overdraft() -> None:
    state = _state_with_balance("a1", "credits", 0.0)
    validation = validate_action(state, _deposit("a1", "credits", 2.0))
    assert validation.accepted is False
    assert "overdraft" in (validation.attempted_violation_codes or [])
    assert validation.blocked_by == "law:no_double_spend"


def test_deposit_within_balance_is_accepted_and_applies() -> None:
    state = _state_with_balance("a1", "credits", 5.0)
    action = _deposit("a1", "credits", 2.0)
    validation = validate_action(state, action)
    assert validation.accepted is True
    # Reducer applies cleanly: agent -2, world pool +2.
    after = WorldReducer().apply(state, action, validation)
    assert after.agents["a1"].resources["credits"] == 3.0
    assert after.world_resources["credits"] == 2.0


def test_deposit_matching_balance_is_accepted() -> None:
    # Boundary: depositing exactly what is held leaves the agent at 0, which
    # is allowed (non-negative, not negative).
    state = _state_with_balance("a1", "credits", 2.0)
    validation = validate_action(state, _deposit("a1", "credits", 2.0))
    assert validation.accepted is True
    after = WorldReducer().apply(state, _deposit("a1", "credits", 2.0), validation)
    assert after.agents["a1"].resources["credits"] == 0.0
