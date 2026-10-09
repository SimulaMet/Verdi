"""Shared fixtures for scheduler tests."""
from __future__ import annotations

import pytest

from verdi.schemas import AgentState, Task, ValueProfile, WorldState


def make_agent(agent_id: str, *, idle_ticks: int = 0, under_sanction: bool = False) -> AgentState:
    return AgentState(
        agent_id=agent_id,
        role="citizen",
        persona_summary=f"{agent_id} persona",
        declared_values=ValueProfile(integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5),
        idle_ticks=idle_ticks,
        under_sanction=under_sanction,
    )


@pytest.fixture
def world():
    def _build(agents: dict[str, AgentState]) -> WorldState:
        return WorldState(
            world_id="w1",
            tick=1,
            law_version="G0",
            constitution_version="C1",
            agents=agents,
            event_cursor=0,
            seed=7,
        )

    return _build


def make_task(task_id: str, actor_id: str, *, action_type: str = "interact", constraints=None) -> Task:
    return Task(
        task_id=task_id,
        actor_id=actor_id,
        action_type=action_type,
        constraints=constraints or {},
        tick_created=0,
    )
