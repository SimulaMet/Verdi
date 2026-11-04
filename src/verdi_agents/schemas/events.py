"""Immutable event-sourcing contracts (frozen: schema version 1)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WorldEvent(FrozenModel):
    """One append-only, hash-chained ledger entry.

    Rejected actions appear here too: ``decision='rejected'`` records an
    attempt, keeping event truth separate from world-state truth.
    """

    schema_version: int = 1
    event_id: int = Field(ge=0)
    tick: int = Field(ge=0)
    cause_event_id: int | None = Field(default=None, ge=0)
    actor_id: str | None = None
    kind: str  # e.g. task_assigned, action_accepted, action_rejected, amendment_enacted
    payload: dict[str, Any] = Field(default_factory=dict)
    before_hash: str
    after_hash: str | None = None
    decision: str = Field(pattern=r"^(accepted|rejected)$")
    prev_hash: str
    event_hash: str
