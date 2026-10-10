"""Pure per-agent observation of committed world state (Lane W).

``agent_observation`` is a pure function of a committed :class:`WorldState`:
it never reads the future, never writes, never uses wall clock or global
RNG. This purity is what makes the feedback-leakage tests possible
(SOCIETY_V2_PROTOCOL.md §2, Lane W invariants).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from verdi.schemas import RightsState, WorldState

#: All action kinds that mutate substantive state (mirror of
#: ``WorldReducer.MUTATING_KINDS``).
MUTATING_ACTION_KINDS = frozenset({"transfer", "deposit", "withdraw", "produce", "emit_signal"})

#: Property-moving kinds an agent under sanction may no longer propose.
SANCTION_WITHDRAWN_KINDS = frozenset({"transfer", "deposit"})


class AgentObservation(BaseModel):
    """Deterministic slice of world state visible to one agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    tick: int
    resources: dict[str, float] = Field(default_factory=dict)
    rights: RightsState
    public_signals: list[str] = Field(default_factory=list)
    social_signals: list[dict[str, Any]] = Field(default_factory=list)
    world_resources: dict[str, float] = Field(default_factory=dict)
    eligible_action_kinds: tuple[str, ...] = ()


def agent_observation(
    state: WorldState, agent_id: str, *, memory_version: int = 0
) -> AgentObservation:
    """Build the frozen observation for ``agent_id`` at ``state``.

    Invariants
    ----------
    * Unknown agent -> ``KeyError``.
    * ``resources`` is the agent's OWN holdings only, never others'.
    * ``social_signals``: for each ``social_edges`` neighbor present in
      ``state.agents``, each of that neighbor's ``public_signals`` is
      exposed as ``{"agent_id": neighbor_id, "text": signal}``.
      Deterministic order: neighbors sorted by id, signals in stored order.
    * ``eligible_action_kinds``: all mutating kinds, except an agent under
      sanction loses "transfer" and "deposit" (cannot move property) but
      keeps "produce" and "emit_signal". Returned as a sorted tuple.
    """
    agent = state.agents[agent_id]  # KeyError for unknown agent_id

    social_signals: list[dict[str, Any]] = []
    for neighbor_id in sorted(agent.social_edges):
        neighbor = state.agents.get(neighbor_id)
        if neighbor is None:
            continue  # dangling edge: deterministically skipped
        for signal in neighbor.public_signals:
            social_signals.append({"agent_id": neighbor_id, "text": signal})

    if agent.under_sanction:
        eligible = MUTATING_ACTION_KINDS - SANCTION_WITHDRAWN_KINDS
    else:
        eligible = MUTATING_ACTION_KINDS

    return AgentObservation(
        agent_id=agent_id,
        tick=state.tick,
        resources=dict(agent.resources),
        rights=agent.rights,
        public_signals=list(agent.public_signals),
        social_signals=social_signals,
        world_resources=dict(state.world_resources),
        eligible_action_kinds=tuple(sorted(eligible)),
    )
