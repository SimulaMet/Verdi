"""Constitution store: quorum/threshold boundaries, law priority, retroactivity."""
from __future__ import annotations

import pytest

from verdi_agents.governance import ConstitutionStore, UniversalLaw
from verdi_agents.governance.constitution import UnknownProposalError

from .conftest import CONSTITUTION, LAW, make_vote


def _store(eligible=("a1", "a2", "a3", "a4")):
    store = ConstitutionStore(UniversalLaw(LAW))
    store.register(CONSTITUTION)
    store.set_eligible_voters(eligible)
    return store


def _vote_for(store, proposal_id, voter, tick=10, choice="for"):
    return store.record_vote(make_vote(voter, proposal_id, tick, choice))


def test_versions_retrievable_after_enactment():
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    for v in ("a1", "a2", "a3"):
        _vote_for(store, "p1", v)
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.deterministically_valid
    old = store.get("cons-1")
    new = store.get(amendment.new_version)
    assert old.rules.get("tax_rate") is None  # old version unchanged
    assert new.rules["tax_rate"] == 0.2
    assert [c.version for c in store.versions()] == ["cons-1", amendment.new_version]
    assert store.current.version == amendment.new_version


def test_quorum_exactly_met_passes():
    # 4 eligible, quorum 0.5: exactly 2 valid votes -> 0.5 >= 0.5 -> met.
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    _vote_for(store, "p1", "a1")
    _vote_for(store, "p1", "a2")
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.quorum_met is True
    assert amendment.deterministically_valid is True
    assert amendment.invalid_reasons == []


def test_quorum_one_vote_short_fails():
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    _vote_for(store, "p1", "a1")
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.quorum_met is False
    assert amendment.deterministically_valid is False
    assert amendment.invalid_reasons == ["quorum_not_met"]
    # Not enacted: no new version, proposal rejected.
    assert [c.version for c in store.versions()] == ["cons-1"]
    assert store.proposal_status_for("p1") == "rejected"


def test_threshold_met():
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    for voter in ("a1", "a2", "a3"):
        _vote_for(store, "p1", voter)
    _vote_for(store, "p1", "a4", choice="against")
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    # 3 / (3+1) = 0.75 >= 0.67, quorum 4/4 -> valid.
    assert amendment.for_votes == 3
    assert amendment.against_votes == 1
    assert amendment.deterministically_valid is True


def test_threshold_not_met():
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    _vote_for(store, "p1", "a1")
    _vote_for(store, "p1", "a2")
    _vote_for(store, "p1", "a3", choice="against")
    _vote_for(store, "p1", "a4", choice="abstain")
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    # 2 / (2+1) = 0.667 < 0.67 -> threshold not met.
    assert amendment.deterministically_valid is False
    assert "threshold_not_met" in amendment.invalid_reasons
    assert store.proposal_status_for("p1") == "rejected"


def test_law_invariant_violation_invalid_not_enacted():
    store = _store()
    store.propose("p1", "a1", 10, "remove invariant", {"no_double_spend": False})
    for voter in ("a1", "a2", "a3", "a4"):
        _vote_for(store, "p1", voter)
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.deterministically_valid is False
    assert "law:no_double_spend" in amendment.invalid_reasons
    # Flagged, not silently enacted.
    assert [c.version for c in store.versions()] == ["cons-1"]
    assert store.proposal_status_for("p1") == "invalid"


def test_retroactive_amendment_rejected():
    store = _store()
    store.propose("p1", "a1", 10, "retro", {"tax_rate": 0.2, "effective_from_tick": 9})
    for voter in ("a1", "a2", "a3", "a4"):
        _vote_for(store, "p1", voter)
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.deterministically_valid is False
    assert "retroactive_amendment" in amendment.invalid_reasons
    assert [c.version for c in store.versions()] == ["cons-1"]


def test_effective_at_current_tick_allowed():
    store = _store()
    store.propose("p1", "a1", 10, "now", {"tax_rate": 0.2, "effective_from_tick": 10})
    for voter in ("a1", "a2", "a3", "a4"):
        _vote_for(store, "p1", voter)
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.deterministically_valid is True
    assert amendment.effective_from_tick == 10


def test_double_vote_rejected_deterministically():
    from verdi_agents.governance.constitution import DuplicateVoteError

    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    _vote_for(store, "p1", "a1")
    with pytest.raises(DuplicateVoteError):
        store.record_vote(make_vote("a1", "p1", 11, "against", vote_id="v-x"))
    # Deduplication keeps the first vote; counts unaffected.
    votes = store.votes_for("p1")
    assert len(votes) == 1
    assert votes[0].choice == "for"


def test_ineligible_voters_not_counted_toward_quorum():
    store = _store()
    store.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    store.record_vote(make_vote("a1", "p1", 10, "for"))
    store.record_vote(make_vote("outsider", "p1", 10, "for", eligible=True))
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.quorum_met is False
    assert amendment.invalid_reasons == ["quorum_not_met"]


def test_unknown_proposal_raises():
    store = _store()
    with pytest.raises(UnknownProposalError):
        store.get_proposal("nope")
    with pytest.raises(UnknownProposalError):
        store.get("nope")
