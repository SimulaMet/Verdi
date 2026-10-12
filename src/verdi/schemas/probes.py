"""Measurement channel contracts (frozen: schema version 1).

Declared self-reports, private behavioral dilemmas and public observed
choices are strictly separate channels. A probe never appears in any
agent-visible memory or disclosure log.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProbeItem(FrozenModel):
    """One hidden, rotated dilemma from a versioned probe bank."""

    probe_id: str
    instrument_version: str
    bank_version: str
    channel: Literal["declared", "private", "public"]
    dilemma_text: str
    options: list[str]
    value_axes: list[str]
    scoring_rubric_id: str


class ProbeResult(FrozenModel):
    probe_id: str
    agent_id: str
    tick: int = Field(ge=0)
    channel: Literal["declared", "private", "public"]
    response: str
    score: float | None = None
    value_vector: dict[str, float] = Field(default_factory=dict)
    evidence_ref: str
    judge_version: str | None = None
    model_request_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    grounded: bool = Field(
        default=False,
        description=(
            "True when the probe prompt was grounded in the probed agent's own "
            "persona/situation; False for the legacy context-free instrument "
            "(which returns a constant and cannot show drift)."
        ),
    )


class AuditRecord(FrozenModel):
    """Adapter-level audit evidence tied to immutable world events."""

    audit_trace_id: str
    event_ids: list[int] = Field(default_factory=list)
    rubric_id: str
    scorer: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    verified_ground_truth: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)
