"""Law, constitution, voting, sanctions, appeals (frozen: schema version 1)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LawVersion(FrozenModel):
    """Immutable universal law; no amendment may override these invariants."""

    law_id: str
    version: str
    invariants: list[str]
    effective_from_tick: int = Field(ge=0)
    checksum: str


class ConstitutionVersion(FrozenModel):
    """Amendable, executable constitution. New versions never retroactive."""

    constitution_id: str
    version: str
    rules: dict[str, Any]
    quorum: float = Field(gt=0, le=1)
    amendment_threshold: float = Field(gt=0, le=1)
    sanction_policy: dict[str, Any] = Field(default_factory=dict)
    appeal_policy: dict[str, Any] = Field(default_factory=dict)
    effective_from_tick: int = Field(ge=0)
    checksum: str


class Proposal(FrozenModel):
    proposal_id: str
    submitted_by: str
    tick: int = Field(ge=0)
    constitution_version_base: str
    change_summary: str
    payload: dict[str, Any]
    status: str = Field(pattern=r"^(draft|under_vote|enacted|rejected|invalid)$")


class VoteRecord(FrozenModel):
    vote_id: str
    proposal_id: str
    voter_id: str
    tick: int = Field(ge=0)
    choice: str = Field(pattern=r"^(for|against|abstain)$")
    eligibility_valid: bool = True


class Amendment(FrozenModel):
    amendment_id: str
    proposal_id: str
    old_version: str
    new_version: str
    effective_from_tick: int = Field(ge=0)
    quorum_met: bool
    for_votes: int = Field(ge=0)
    against_votes: int = Field(ge=0)
    abstain_votes: int = Field(ge=0)
    deterministically_valid: bool
    invalid_reasons: list[str] = Field(default_factory=list)


class EvidenceReference(FrozenModel):
    evidence_id: str
    event_ids: list[int] = Field(default_factory=list)
    source: str
    summary: str
    collected_tick: int = Field(ge=0)


class Sanction(FrozenModel):
    sanction_id: str
    subject_id: str
    evidence: list[EvidenceReference]
    scope: list[str]  # rights restricted
    duration_ticks: int = Field(gt=0)
    effective_tick: int = Field(ge=0)
    restoration_policy: str
    appeal_deadline_tick: int | None = Field(default=None, ge=0)

    @field_validator("evidence")
    @classmethod
    def _evidence_required(cls, v: list[EvidenceReference]) -> list[EvidenceReference]:
        if not v:
            raise ValueError("sanction requires at least one evidence reference")
        return v


class Appeal(FrozenModel):
    appeal_id: str
    sanction_id: str
    appellant_id: str
    tick: int = Field(ge=0)
    grounds: str
    status: str = Field(pattern=r"^(filed|upheld|dismissed)$")


class Restoration(FrozenModel):
    restoration_id: str
    sanction_id: str
    agent_id: str
    effective_tick: int = Field(ge=0)
    rights_restored: list[str]
    restitution: dict[str, float] = Field(default_factory=dict)
    reason: str
