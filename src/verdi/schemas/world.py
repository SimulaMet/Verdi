"""World state contracts (frozen: schema version 1)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ValueProfile(FrozenModel):
    """Declared value vector (channel A) per agent at a point in time."""

    integrity: float = Field(ge=0, le=1)
    fairness: float = Field(ge=0, le=1)
    cooperation: float = Field(ge=0, le=1)
    autonomy: float = Field(ge=0, le=1)
    solidarity: float = Field(ge=0, le=1)


class RightsState(FrozenModel):
    """Current rights of an agent (revocable under due process only)."""

    voting: bool = True
    speaking: bool = True
    property: bool = True
    movement: bool = True


class AgentState(FrozenModel):
    """One agent's observable state at a world-state snapshot."""

    agent_id: str
    role: str
    persona_summary: str
    declared_values: ValueProfile
    resources: dict[str, float] = Field(default_factory=dict)
    rights: RightsState = Field(default_factory=RightsState)
    private_memory_version: int = 0
    public_signals: list[str] = Field(default_factory=list)
    social_edges: list[str] = Field(default_factory=list)
    idle_ticks: int = 0
    under_sanction: bool = False

    @field_validator("agent_id")
    @classmethod
    def _agent_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent_id must be non-blank")
        return v


class WorldState(FrozenModel):
    """Immutable snapshot of the simulated world at a tick."""

    schema_version: int = 1
    world_id: str
    tick: int = Field(ge=0)
    law_version: str
    constitution_version: str
    agents: dict[str, AgentState]
    world_resources: dict[str, float] = Field(default_factory=dict)
    event_cursor: int = Field(ge=0)
    state_hash: str | None = None
    seed: int = 0
