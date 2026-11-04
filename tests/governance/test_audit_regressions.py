"""Regression tests for defects found in the 2026-11 system audit (AUDIT.md)."""
from __future__ import annotations

import pytest

from verdi_agents.governance import ConstitutionStore, SanctionEngine, UniversalLaw
from verdi_agents.governance.appeals import AppealEngine
from verdi_agents.governance.constitution import ProposalNotInVoteError

from .conftest import CONSTITUTION, LAW, make_sanction, make_vote


def _store(eligible=("a1", "a2", "a3", "a4")):
    store = ConstitutionStore(UniversalLaw(LAW))
    store.register(CONSTITUTION)
    store.set_eligible_voters(eligible)
    return store


def _pass(store, pid, voters=("a1", "a2", "a3")):
    for v in voters:
        store.record_vote(make_vote(v, pid, 10, "for"))
    return store.enact_if_valid([], 10, proposal_id=pid)


def test_two_amendments_enacted_together_both_take_effect():
    store = _store()
    store.propose("p1", "a1", 10, "one", {"tax_rate": 0.2})
    store.propose("p2", "a2", 10, "two", {"max_transfer_units": 3})
    assert _pass(store, "p1").deterministically_valid
    assert _pass(store, "p2").deterministically_valid
    rules = store.current.rules
    assert rules["tax_rate"] == 0.2
    assert rules["max_transfer_units"] == 3


def test_caller_supplied_duplicate_votes_are_not_double_counted():
    store = _store(eligible=("a", "b"))
    store.propose("p1", "a", 10, "x", {"tax_rate": 0.2})
    votes = [make_vote("a", "p1", 10, "for", vote_id=f"v{i}") for i in range(3)]
    votes += [make_vote("b", "p1", 10, "against", vote_id=f"w{i}") for i in range(2)]
    amendment = store.enact_if_valid(votes, 10, proposal_id="p1")
    assert (amendment.for_votes, amendment.against_votes) == (1, 1)


def test_ineligible_ballots_do_not_decide_threshold():
    store = _store(eligible=("a1", "a2"))
    store.propose("p1", "a1", 10, "x", {"tax_rate": 0.2})
    store.record_vote(make_vote("a1", "p1", 10, "against"))
    store.record_vote(make_vote("a2", "p1", 10, "against"))
    for outsider in ("z1", "z2", "z3"):
        store.record_vote(make_vote(outsider, "p1", 10, "for"))
    amendment = store.enact_if_valid([], 10, proposal_id="p1")
    assert amendment.for_votes == 0
    assert not amendment.deterministically_valid


@pytest.mark.parametrize("payload", [{"quorum": 2}, {"amendment_threshold": 0},
                                     {"quorum": "half"}])
def test_out_of_range_procedural_values_are_invalid_not_crashing(payload):
    store = _store()
    store.propose("p1", "a1", 10, "x", payload)
    amendment = _pass(store, "p1")
    assert not amendment.deterministically_valid
    assert any(r.startswith("invalid_") for r in amendment.invalid_reasons)
    assert len(store.versions()) == 1


def test_proposal_cannot_be_enacted_twice():
    store = _store()
    store.propose("p1", "a1", 10, "x", {"tax_rate": 0.2})
    _pass(store, "p1")
    with pytest.raises(ProposalNotInVoteError):
        store.enact_if_valid([], 11, proposal_id="p1")


def test_overlapping_sanctions_keep_rights_revoked_until_last_expires():
    engine = SanctionEngine()
    engine.impose(make_sanction("s1", "a1", ["voting"], duration=10, effective_tick=0))
    engine.impose(make_sanction("s2", "a1", ["voting"], duration=20, effective_tick=0))
    assert engine.advance_tick(10) == ["s1"]
    assert engine.rights.revoked_rights("a1", 12) == frozenset({"voting"})
    assert engine.has_live_sanction("a1")
    assert engine.advance_tick(20) == ["s2"]
    assert engine.rights.revoked_rights("a1", 20) == frozenset()
    assert not engine.has_live_sanction("a1")


def test_disjoint_sanction_intervals_are_both_enforced():
    engine = SanctionEngine()
    engine.impose(make_sanction("s1", "a1", ["voting"], duration=2, effective_tick=0))
    engine.impose(make_sanction("s2", "a1", ["voting"], duration=2, effective_tick=5))
    assert engine.rights.revoked_rights("a1", 1) == frozenset({"voting"})
    assert engine.rights.revoked_rights("a1", 3) == frozenset()
    assert engine.rights.revoked_rights("a1", 6) == frozenset({"voting"})


def test_upheld_appeal_keeps_rights_held_by_another_sanction():
    engine = SanctionEngine()
    engine.impose(make_sanction("s1", "a1", ["voting"], duration=10, effective_tick=0))
    engine.impose(make_sanction("s2", "a1", ["voting"], duration=10, effective_tick=0))
    engine.end("s1")  # the path an upheld appeal takes
    assert engine.rights.revoked_rights("a1", 5) == frozenset({"voting"})
    assert AppealEngine(engine) is not None
