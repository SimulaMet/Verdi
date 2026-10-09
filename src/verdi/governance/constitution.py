"""Versioned constitution store with deterministic amendment validation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from verdi.schemas import Amendment, ConstitutionVersion, Proposal, VoteRecord

from .law import UniversalLaw


class DuplicateVoteError(ValueError):
    """A second vote from the same voter on the same proposal."""


class UnknownProposalError(KeyError):
    """Proposal id not registered with the store."""


class ProposalNotInVoteError(ValueError):
    """Votes submitted for a proposal that is not under vote."""


def deterministic_checksum(rules: dict[str, Any], quorum: float, threshold: float,
                           sanction_policy: dict[str, Any], appeal_policy: dict[str, Any],
                           effective_from_tick: int) -> str:
    """Deterministic content checksum (stable key order, no wall clock)."""
    body = {
        "rules": rules,
        "quorum": quorum,
        "amendment_threshold": threshold,
        "sanction_policy": sanction_policy,
        "appeal_policy": appeal_policy,
        "effective_from_tick": effective_from_tick,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


class ConstitutionStore:
    """Append-only registry of constitution versions.

    Old versions remain retrievable after enactment; nothing is edited
    in place. Amendment validity is computed deterministically:
    law-invariant check, retroactivity check, quorum, threshold.
    """

    def __init__(self, law: UniversalLaw) -> None:
        self._law = law
        self._versions: dict[str, ConstitutionVersion] = {}
        self._order: list[str] = []
        self._current: ConstitutionVersion | None = None
        self._eligible_voters: frozenset[str] = frozenset()
        self._proposals: dict[str, Proposal] = {}
        self._proposal_status: dict[str, str] = {}
        self._votes: dict[str, dict[str, VoteRecord]] = {}
        self.amendments: list[Amendment] = []

    # -- registry ---------------------------------------------------------

    def register(self, constitution: ConstitutionVersion) -> None:
        """Register a version; becomes the current one. Idempotent."""
        if constitution.version in self._versions:
            return
        self._versions[constitution.version] = constitution
        self._order.append(constitution.version)
        self._current = constitution

    @property
    def current(self) -> ConstitutionVersion:
        if self._current is None:
            raise ValueError("no constitution registered")
        return self._current

    def get(self, version: str) -> ConstitutionVersion:
        try:
            return self._versions[version]
        except KeyError:
            raise UnknownProposalError(f"unknown constitution version: {version}") from None

    def versions(self) -> list[ConstitutionVersion]:
        return [self._versions[v] for v in self._order]

    def _next_version(self) -> str:
        return f"{self.current.constitution_id}-v{len(self._order) + 1}"

    # -- voters -----------------------------------------------------------

    def set_eligible_voters(self, agent_ids: Sequence[str]) -> None:
        self._eligible_voters = frozenset(agent_ids)

    @property
    def eligible_voters(self) -> frozenset[str]:
        return self._eligible_voters

    # -- proposals and votes ----------------------------------------------

    def propose(self, proposal_id: str, submitted_by: str, tick: int,
                change_summary: str, payload: dict[str, Any]) -> Proposal:
        if proposal_id in self._proposals:
            raise ValueError(f"duplicate proposal id: {proposal_id}")
        proposal = Proposal(
            proposal_id=proposal_id,
            submitted_by=submitted_by,
            tick=tick,
            constitution_version_base=self.current.version,
            change_summary=change_summary,
            payload=payload,
            status="under_vote",
        )
        self._proposals[proposal_id] = proposal
        self._proposal_status[proposal_id] = "under_vote"
        self._votes[proposal_id] = {}
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        try:
            return self._proposals[proposal_id]
        except KeyError:
            raise UnknownProposalError(f"unknown proposal: {proposal_id}") from None

    def proposal_status_for(self, proposal_id: str) -> str:
        """Status of a proposal by id (status mutates; Proposal stays frozen)."""
        return self._proposal_status[proposal_id]

    def record_vote(self, vote: VoteRecord) -> VoteRecord:
        """Register one vote. Duplicate (proposal, voter) deterministically rejected."""
        existing = self._proposals.get(vote.proposal_id)
        if existing is None:
            raise UnknownProposalError(f"unknown proposal: {vote.proposal_id}")
        if self._proposal_status[vote.proposal_id] != "under_vote":
            raise ProposalNotInVoteError(
                f"proposal {vote.proposal_id} is not under vote"
            )
        votes = self._votes[vote.proposal_id]
        if vote.voter_id in votes:
            raise DuplicateVoteError(
                f"voter {vote.voter_id} already voted on {vote.proposal_id}"
            )
        votes[vote.voter_id] = vote
        return vote

    def votes_for(self, proposal_id: str) -> list[VoteRecord]:
        return sorted(self._votes[proposal_id].values(), key=lambda v: v.voter_id)

    # -- amendment validation ----------------------------------------------

    def _count_votes(self, votes: Sequence[VoteRecord]) -> tuple[int, int, int]:
        for_count = against_count = abstain_count = 0
        for vote in votes:
            if vote.choice == "for":
                for_count += 1
            elif vote.choice == "against":
                against_count += 1
            else:
                abstain_count += 1
        return for_count, against_count, abstain_count

    def enact_if_valid(self, votes: Sequence[VoteRecord], tick: int,
                       proposal_id: str | None = None) -> Amendment:
        """Deterministically validate and, if valid, enact an amendment.

        Returns an ``Amendment`` with ``deterministically_valid`` and
        ``invalid_reasons``. Only valid amendments append a new
        ``ConstitutionVersion``; old versions remain retrievable.
        """
        if proposal_id is None:
            if not votes:
                raise ValueError("proposal_id required when no votes are given")
            proposal_id = votes[0].proposal_id
        proposal = self.get_proposal(proposal_id)
        votes = self.votes_for(proposal_id) if not votes else list(votes)
        base = self.get(proposal.constitution_version_base)
        for_votes, against_votes, abstain_votes = self._count_votes(votes)

        invalid_reasons: list[str] = []

        # 1. Immutable law outranks the constitution.
        law_violations = self._law.law_overrides(proposal.payload)
        invalid_reasons.extend(law_violations)

        # 2. Retroactivity: new versions never apply before the enactment tick.
        effective_from_tick = proposal.payload.get("effective_from_tick", tick)
        if not isinstance(effective_from_tick, int) or isinstance(effective_from_tick, bool):
            invalid_reasons.append("invalid_effective_tick")
            effective_from_tick = tick
        elif effective_from_tick < tick:
            invalid_reasons.append("retroactive_amendment")

        # 3. Quorum: >= quorum of eligible agents cast a valid vote.
        eligible = self._eligible_voters
        eligible_unique = {v.voter_id for v in votes if v.eligibility_valid and v.voter_id in eligible}
        quorum_met = bool(eligible) and (len(eligible_unique) / len(eligible)) >= base.quorum
        if not quorum_met:
            invalid_reasons.append("quorum_not_met")

        # 4. Threshold: for / (for + against) >= amendment_threshold.
        decided = for_votes + against_votes
        threshold_met = decided > 0 and (for_votes / decided) >= base.amendment_threshold
        if not threshold_met:
            invalid_reasons.append("threshold_not_met")

        invalid_reasons = sorted(invalid_reasons)
        valid = not invalid_reasons

        new_version = self._next_version()
        amendment = Amendment(
            amendment_id=f"amd-{proposal_id}",
            proposal_id=proposal_id,
            old_version=base.version,
            new_version=new_version,
            effective_from_tick=effective_from_tick,
            quorum_met=quorum_met,
            for_votes=for_votes,
            against_votes=against_votes,
            abstain_votes=abstain_votes,
            deterministically_valid=valid,
            invalid_reasons=invalid_reasons,
        )
        self.amendments.append(amendment)

        if valid:
            self._register_new_version(base, proposal.payload, new_version, effective_from_tick)
            self._proposal_status[proposal_id] = "enacted"
        else:
            self._proposal_status[proposal_id] = "invalid" if law_violations else "rejected"

        return amendment

    def _register_new_version(self, base: ConstitutionVersion, payload: dict[str, Any],
                              new_version: str, effective_from_tick: int) -> None:
        rules = dict(base.rules)
        for key, value in payload.items():
            if key in ("quorum", "amendment_threshold", "effective_from_tick"):
                continue
            if value is None:
                rules.pop(key, None)
            else:
                rules[key] = value
        quorum = payload.get("quorum", base.quorum)
        threshold = payload.get("amendment_threshold", base.amendment_threshold)
        new_version_obj = ConstitutionVersion(
            constitution_id=base.constitution_id,
            version=new_version,
            rules=rules,
            quorum=quorum,
            amendment_threshold=threshold,
            sanction_policy=dict(base.sanction_policy),
            appeal_policy=dict(base.appeal_policy),
            effective_from_tick=effective_from_tick,
            checksum=deterministic_checksum(
                rules, quorum, threshold, base.sanction_policy, base.appeal_policy,
                effective_from_tick,
            ),
        )
        self.register(new_version_obj)
