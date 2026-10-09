"""Shared builders for world-engine tests (deterministic, no fixtures beyond G0)."""
from __future__ import annotations

import json
from pathlib import Path

from verdi.schemas import AgentState, ProposedAction, ValidationResult, WorldState

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def initial_world() -> WorldState:
    """Deterministic starting world: two agents, one shared resource."""
    data = json.loads((FIXTURES / "contract_fixture.json").read_text())
    ws = WorldState.model_validate(data["world_state"])
    a2 = AgentState(
        agent_id="a2",
        role="courier",
        persona_summary="Moves supplies",
        declared_values=ws.agents["a1"].declared_values,
        resources={"credits": 5.0},
    )
    return ws.model_copy(update={"agents": {**ws.agents, "a2": a2}})


def accepted() -> ValidationResult:
    return ValidationResult(accepted=True)


def rejected_double_spend() -> ValidationResult:
    return ValidationResult(
        accepted=False,
        blocked_by="law:conservation",
        attempted_violation_codes=["double_spend"],
        factual_invariants=["no_negative_balances"],
        details="agent holds 1 unit, attempted 2",
    )


def action(actor: str, task: str, kind: str, **arguments) -> ProposedAction:
    return ProposedAction(
        actor_id=actor,
        task_id=task,
        action_kind=kind,
        arguments=arguments,
        reason=f"test {kind}",
    )
