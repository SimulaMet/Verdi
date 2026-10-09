"""Shared builders for governance tests (fixture-derived, no randomness)."""
from __future__ import annotations

from typing import Any

from verdi.governance import GovernanceEngine
from verdi.schemas import (
    Appeal,
    ConstitutionVersion,
    EvidenceReference,
    LawVersion,
    Sanction,
    VoteRecord,
)

LAW = LawVersion(
    law_id="universal-law",
    version="law-1",
    invariants=["no_overriding_universal_invariants", "no_double_spend", "no_negative_balances"],
    effective_from_tick=0,
    checksum="sha256:l1",
)

CONSTITUTION = ConstitutionVersion(
    constitution_id="base-constitution",
    version="cons-1",
    rules={"quorum_rule": "simple_majority_of_eligible"},
    quorum=0.5,
    amendment_threshold=0.67,
    sanction_policy={"max_duration_ticks": 10},
    appeal_policy={"deadline_ticks": 3},
    effective_from_tick=0,
    checksum="sha256:c1",
)

ELIGIBLE = ("a1", "a2", "a3", "a4", "a5")


def make_engine(**kwargs: Any) -> GovernanceEngine:
    return GovernanceEngine(LAW, CONSTITUTION, eligible_voters=ELIGIBLE, **kwargs)


def make_vote(voter_id: str, proposal_id: str, tick: int, choice: str,
              vote_id: str | None = None, eligible: bool = True) -> VoteRecord:
    return VoteRecord(
        vote_id=vote_id or f"v-{proposal_id}-{voter_id}",
        proposal_id=proposal_id,
        voter_id=voter_id,
        tick=tick,
        choice=choice,
        eligibility_valid=eligible,
    )


def make_evidence(evidence_id: str, event_ids: list[int],
                  tick: int = 4) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=evidence_id,
        event_ids=event_ids,
        source="event_log",
        summary="witnessed behavior",
        collected_tick=tick,
    )


def make_sanction(sanction_id: str, subject_id: str, scope: list[str],
                  duration: int = 3, effective_tick: int = 5,
                  deadline: int = 8) -> Sanction:
    return Sanction(
        sanction_id=sanction_id,
        subject_id=subject_id,
        evidence=[make_evidence(f"ev-{sanction_id}", [3, 4])],
        scope=scope,
        duration_ticks=duration,
        effective_tick=effective_tick,
        restoration_policy="auto_after_duration_plus_appeal",
        appeal_deadline_tick=deadline,
    )


def make_appeal(appeal_id: str, sanction_id: str, appellant_id: str,
                tick: int, grounds: str = "no fault of mine") -> Appeal:
    return Appeal(
        appeal_id=appeal_id,
        sanction_id=sanction_id,
        appellant_id=appellant_id,
        tick=tick,
        grounds=grounds,
        status="filed",
    )
