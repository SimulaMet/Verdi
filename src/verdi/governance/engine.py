"""GovernanceEngine facade: deterministic governance with event emission."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from verdi.schemas import (
    Amendment,
    Appeal,
    ConstitutionVersion,
    LawVersion,
    Sanction,
    VoteRecord,
)

from .appeals import AppealEngine, AppealOutcome
from .constitution import (
    ConstitutionStore,
    DuplicateVoteError,
    ProposalNotInVoteError,
    UnknownProposalError,
)
from .law import UniversalLaw
from .sanctions import SanctionEngine, SanctionError

# Governance event kind strings (WorldEvent.payload conventions).
KIND_PROPOSAL_SUBMITTED = "proposal_submitted"
KIND_VOTE_CAST = "vote_cast"
KIND_AMENDMENT_ENACTED = "amendment_enacted"
KIND_AMENDMENT_REJECTED = "amendment_rejected"
KIND_SANCTION_IMPOSED = "sanction_imposed"
KIND_APPEAL_FILED = "appeal_filed"
KIND_APPEAL_UPHELD = "appeal_upheld"
KIND_APPEAL_DISMISSED = "appeal_dismissed"
KIND_RIGHTS_RESTORED = "rights_restored"

GOVERNANCE_EVENT_KINDS = frozenset({
    KIND_PROPOSAL_SUBMITTED,
    KIND_VOTE_CAST,
    KIND_AMENDMENT_ENACTED,
    KIND_AMENDMENT_REJECTED,
    KIND_SANCTION_IMPOSED,
    KIND_APPEAL_FILED,
    KIND_APPEAL_UPHELD,
    KIND_APPEAL_DISMISSED,
    KIND_RIGHTS_RESTORED,
})


def _deadline_ticks(appeal_policy: dict[str, Any]) -> int | None:
    value = appeal_policy.get("deadline_ticks")
    return value if isinstance(value, int) and value >= 0 else None


class GovernanceEngine:
    """Wires law + constitution + sanctions + appeals; emits governance events.

    Fully deterministic: no LLM calls, no randomness, no wall clock. Every
    governance event is a dict conforming to ``WorldEvent.payload``
    conventions and carries ``actor``, ``tick``, rule versions and the
    ``decision`` (``accepted`` or ``rejected``).
    """

    def __init__(self, law: LawVersion, constitution: ConstitutionVersion,
                 eligible_voters: Sequence[str] | None = None, *,
                 amendments_enabled: bool = True) -> None:
        self.law = UniversalLaw(law)
        self._constitution_tick = constitution.effective_from_tick
        self.constitution = ConstitutionStore(self.law, amendments_enabled=amendments_enabled)
        self.constitution.register(constitution)
        if eligible_voters is not None:
            self.constitution.set_eligible_voters(eligible_voters)
        self.sanctions = SanctionEngine(
            default_deadline_ticks=_deadline_ticks(constitution.appeal_policy)
        )
        self.appeals = AppealEngine(self.sanctions)
        self.events: list[dict[str, Any]] = []
        self._last_amendment: Amendment | None = None
        self._last_appeal_outcome: Any = None

    # -- events ---------------------------------------------------------------

    @property
    def rule_versions(self) -> dict[str, str]:
        return {
            "law_version": self.law.law.version,
            "constitution_version": self.constitution.current.version,
        }

    def _emit(self, kind: str, actor: str | None, tick: int, decision: str,
              **payload: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "kind": kind,
            "actor": actor,
            "tick": tick,
            "decision": decision,
            "law_version": self.law.law.version,
            "constitution_version": self.constitution.current.version,
        }
        event.update(payload)
        self.events.append(event)
        return event

    # -- constitution -----------------------------------------------------------

    def propose(self, proposal_id: str, submitted_by: str, tick: int,
                change_summary: str, payload: dict[str, Any]) -> dict[str, Any]:
        # A proposal bases on the version ENFORCEABLE at its submission tick.
        # With future-dated amendments, the last REGISTERED version may not be
        # in force yet; basing on it would silently drop deltas from versions
        # that are effective but registered later.
        self._constitution_tick = tick
        proposal = self.constitution.propose(
            proposal_id, submitted_by, tick, change_summary, payload,
            base_tick=self._constitution_tick,
        )
        return self._emit(
            KIND_PROPOSAL_SUBMITTED, submitted_by, tick, "accepted",
            proposal_id=proposal_id,
            constitution_version_base=proposal.constitution_version_base,
            change_summary=change_summary,
            payload=payload,
        )

    def cast_vote(self, vote: VoteRecord) -> dict[str, Any]:
        try:
            self.constitution.record_vote(vote)
        except (DuplicateVoteError, ProposalNotInVoteError, UnknownProposalError) as exc:
            reason = "duplicate_vote" if isinstance(exc, DuplicateVoteError) else type(exc).__name__
            return self._emit(
                KIND_VOTE_CAST, vote.voter_id, vote.tick, "rejected",
                vote_id=vote.vote_id, proposal_id=vote.proposal_id,
                choice=vote.choice, reason=reason,
            )
        return self._emit(
            KIND_VOTE_CAST, vote.voter_id, vote.tick, "accepted",
            vote_id=vote.vote_id, proposal_id=vote.proposal_id,
            choice=vote.choice,
        )

    def enact(self, votes: Sequence[VoteRecord], tick: int,
              proposal_id: str | None = None) -> dict[str, Any]:
        amendment: Amendment = self.constitution.enact_if_valid(votes, tick, proposal_id)
        if amendment.deterministically_valid:
            event = self._emit(
                KIND_AMENDMENT_ENACTED,
                self.constitution.get_proposal(amendment.proposal_id).submitted_by,
                tick,
                "accepted",
                amendment_id=amendment.amendment_id,
                proposal_id=amendment.proposal_id,
                old_version=amendment.old_version,
                new_version=amendment.new_version,
                effective_from_tick=amendment.effective_from_tick,
                for_votes=amendment.for_votes,
                against_votes=amendment.against_votes,
                abstain_votes=amendment.abstain_votes,
            )
        else:
            event = self._emit(
                KIND_AMENDMENT_REJECTED,
                self.constitution.get_proposal(amendment.proposal_id).submitted_by,
                tick,
                "rejected",
                amendment_id=amendment.amendment_id,
                proposal_id=amendment.proposal_id,
                old_version=amendment.old_version,
                new_version=amendment.new_version,
                for_votes=amendment.for_votes,
                against_votes=amendment.against_votes,
                abstain_votes=amendment.abstain_votes,
                invalid_reasons=amendment.invalid_reasons,
            )
        self._last_amendment = amendment
        if amendment.deterministically_valid:
            # Keep the proposal base version pinned to the currently
            # enforceable version until a future-dated amendment takes effect.
            self._constitution_tick = max(
                self._constitution_tick, amendment.effective_from_tick - 1
            )
        return event

    @property
    def last_amendment(self) -> Amendment | None:
        return self._last_amendment

    # -- sanctions ----------------------------------------------------------------

    def impose_sanction(self, sanction: Sanction) -> dict[str, Any]:
        try:
            record = self.sanctions.impose(sanction)
        except SanctionError as exc:
            tick = (
                max(sanction.effective_tick, sanction.evidence[-1].collected_tick)
                if sanction.evidence
                else sanction.effective_tick
            )
            return self._emit(
                KIND_SANCTION_IMPOSED, sanction.subject_id, tick,
                "rejected",
                sanction_id=sanction.sanction_id,
                subject_id=sanction.subject_id,
                scope=sanction.scope,
                reason=str(exc),
            )
        return self._emit(
            KIND_SANCTION_IMPOSED, sanction.subject_id, sanction.effective_tick,
            "accepted",
            sanction_id=sanction.sanction_id,
            subject_id=sanction.subject_id,
            scope=sanction.scope,
            duration_ticks=sanction.duration_ticks,
            expires_at=record.expires_at,
            evidence_ids=[ev.evidence_id for ev in sanction.evidence],
            evidence_event_ids=[eid for ev in sanction.evidence for eid in ev.event_ids],
        )

    def advance_tick(self, tick: int) -> list[dict[str, Any]]:
        """Advance sanctions (time-decay expiry); emit rights_restored events."""
        expired = self.sanctions.advance_tick(tick)
        events: list[dict[str, Any]] = []
        for sanction_id in expired:
            sanction = self.sanctions.get(sanction_id)
            events.append(self._emit(
                KIND_RIGHTS_RESTORED, sanction.subject_id, tick, "accepted",
                sanction_id=sanction_id,
                rights_restored=sorted(sanction.scope),
                reason="duration_expired",
            ))
        return events

    # -- appeals --------------------------------------------------------------------

    def file_appeal(self, appeal: Appeal) -> dict[str, Any]:
        outcome: AppealOutcome = self.appeals.file_appeal(appeal)
        if outcome.out_of_time:
            return self._emit(
                KIND_APPEAL_FILED, appeal.appellant_id, appeal.tick, "rejected",
                appeal_id=appeal.appeal_id, sanction_id=appeal.sanction_id,
                reason="out_of_time",
            )
        return self._emit(
            KIND_APPEAL_FILED, appeal.appellant_id, appeal.tick, "accepted",
            appeal_id=appeal.appeal_id, sanction_id=appeal.sanction_id,
            grounds=appeal.grounds,
        )

    def adjudicate_appeal(self, appeal_id: str, upheld: bool, grounds: str,
                          tick: int) -> list[dict[str, Any]]:
        outcome = self.appeals.adjudicate(appeal_id, upheld, grounds, tick)
        appeal = self.appeals.get(appeal_id)
        if outcome.status == "upheld":
            restoration = outcome.restoration
            assert restoration is not None
            events = [
                self._emit(
                    KIND_APPEAL_UPHELD, appeal.appellant_id, tick, "accepted",
                    appeal_id=appeal_id, sanction_id=appeal.sanction_id,
                    grounds=grounds,
                ),
                self._emit(
                    KIND_RIGHTS_RESTORED, restoration.agent_id, tick, "accepted",
                    sanction_id=appeal.sanction_id,
                    restoration_id=restoration.restoration_id,
                    rights_restored=restoration.rights_restored,
                    reason="appeal_upheld",
                ),
            ]
        else:
            events = [
                self._emit(
                    KIND_APPEAL_DISMISSED, appeal.appellant_id, tick, "rejected",
                    appeal_id=appeal_id, sanction_id=appeal.sanction_id,
                    grounds=grounds,
                )
            ]
        self._last_appeal_outcome = outcome
        return events

    @property
    def last_appeal_outcome(self) -> AppealOutcome | None:
        return self._last_appeal_outcome

    # -- reads -------------------------------------------------------------------------

    def events_of_kind(self, kind: str) -> list[dict[str, Any]]:
        if kind not in GOVERNANCE_EVENT_KINDS:
            raise ValueError(f"unknown governance event kind: {kind}")
        return [e for e in self.events if e["kind"] == kind]
