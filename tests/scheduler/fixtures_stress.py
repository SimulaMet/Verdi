"""Deterministic task/world generators for scheduler stress tests.

Test-support only. Reuses ``make_agent`` / ``make_task`` from ``conftest.py``
so Task / AgentState construction stays identical to the unit tests.
"""
from __future__ import annotations

from verdi_agents.schemas import AgentState, WorldState

from .conftest import make_agent, make_task

#: Idle-tick profile for the adversarial pool: deliberately clumped (pairs of
#: identical values) so the digest tie-break does most of the ordering work.
_ADVERSARIAL_IDLE = {f"a{i}": v for i, v in enumerate((0, 0, 1, 1, 3, 3, 7, 7, 12, 12))}
#: Agents sanctioned in the adversarial world.
ADVERSARIAL_SANCTIONED = ("a4", "a5")


def adversarial_world(world):
    """World with 10 agents, clumped idle_ticks, and two sanctioned agents."""
    agents = {}
    for agent_id, idle in _ADVERSARIAL_IDLE.items():
        agents[agent_id] = make_agent(agent_id, idle_ticks=idle, under_sanction=agent_id in ADVERSARIAL_SANCTIONED)
    return world(agents)


def adversarial_load(n_tasks: int = 500) -> list:
    """Deterministic adversarial task load (no RNG by construction).

    - agent ids stride through a 10-agent pool (repeated actor ids, mixed);
    - the idle_ticks profile is clumped, so many tasks share the first sort-key
      component and only the seed-derived digest separates them;
    - sanctioned actors carry ``requires_rights`` so a stable slice is skipped.
    """
    pool = sorted(_ADVERSARIAL_IDLE)
    tasks = []
    for i in range(n_tasks):
        actor = pool[(i * 7) % len(pool)]
        constraints = (
            {"requires_rights": True} if actor in ADVERSARIAL_SANCTIONED else {"note": "adversarial"}
        )
        tasks.append(make_task(f"t{i:04d}", actor, constraints=constraints))
    return tasks


def full_signature(schedule) -> tuple:
    """Byte-identical comparison of every field in a Schedule."""
    return (
        tuple((a.order, a.task_id, a.agent_id) for a in schedule.assignments),
        tuple((s.task_id, s.reason) for s in schedule.skipped),
        tuple(sorted(schedule.fairness_stats.items())),
    )


def rebuild_world(world: WorldState, agents: dict) -> WorldState:
    """Copy of ``world`` with a different agents mapping (models are frozen)."""
    return world.model_copy(update={"agents": agents})


def with_agent(world: WorldState, agent_id: str, **changes) -> WorldState:
    """Copy of ``world`` with one agent field changed (models are frozen)."""
    data = world.agents[agent_id].model_dump()
    data.update(changes)
    agents = dict(world.agents)
    agents[agent_id] = AgentState(**data)
    return rebuild_world(world, agents)
