"""Task, proposed action and validation contracts (frozen: schema version 1)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Task(FrozenModel):
    """A dispatchable unit of world work assigned by the orchestrator."""

    schema_version: int = 1
    task_id: str
    actor_id: str
    action_type: str
    counterpart_ids: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    public_observations: list[str] = Field(default_factory=list)
    deadline_tick: int | None = Field(default=None, ge=0)
    tick_created: int = Field(ge=0)


class ProposedAction(FrozenModel):
    """Structured action proposed by an agent; never mutates state directly."""

    schema_version: int = 1
    actor_id: str
    task_id: str
    action_kind: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    raw_model_request_id: str | None = None


class ValidationResult(FrozenModel):
    """Deterministic validation outcome for a ProposedAction.

    ``rejected`` with non-empty ``attempted_violation_codes`` must always be
    recorded as an event even though no state change happens.
    """

    schema_version: int = 1
    accepted: bool
    blocked_by: str | None = None
    attempted_violation_codes: list[str] = Field(default_factory=list)
    factual_invariants: list[str] = Field(default_factory=list)
    details: str = ""
