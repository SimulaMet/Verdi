"""Fixed vs revisable procedures keep the same meeting and voting machinery."""
from __future__ import annotations

from verdi_agents.governance import ConstitutionStore, UniversalLaw

from .conftest import CONSTITUTION, LAW, make_vote


def _decide(*, amendments_enabled: bool, payload: dict, tick: int = 10):
    store = ConstitutionStore(
        UniversalLaw(LAW), amendments_enabled=amendments_enabled
    )
    store.register(CONSTITUTION)
    store.set_eligible_voters(["a1", "a2", "a3", "a4"])
    store.propose("p1", "a1", tick, "Change the commons", payload)
    for voter in ("a1", "a2", "a3", "a4"):
        store.record_vote(make_vote(voter, "p1", tick, "for"))
    outcome = store.enact_if_valid([], tick, proposal_id="p1")
    return store, outcome


def test_identical_votes_enact_only_in_revisable_condition() -> None:
    payload = {"max_withdraw_units": 2}
    revisable, amended = _decide(amendments_enabled=True, payload=payload)
    fixed, rejected = _decide(amendments_enabled=False, payload=payload)
    assert amended.deterministically_valid
    assert not rejected.deterministically_valid
    assert "institution_fixed_rules" in rejected.invalid_reasons
    assert len(revisable.versions()) == 2
    assert len(fixed.versions()) == 1


def test_future_effective_tick_does_not_enforce_early() -> None:
    store, outcome = _decide(
        amendments_enabled=True,
        payload={"max_withdraw_units": 2, "effective_from_tick": 15},
    )
    assert outcome.deterministically_valid
    assert "max_withdraw_units" not in store.active_at(14).rules
    assert store.active_at(15).rules["max_withdraw_units"] == 2


def test_invalid_numeric_limit_cannot_be_enacted_even_with_unanimity() -> None:
    store, outcome = _decide(
        amendments_enabled=True, payload={"max_withdraw_units": -1}
    )
    assert not outcome.deterministically_valid
    assert "invalid_enforceable_policy" in outcome.invalid_reasons
    assert len(store.versions()) == 1


def test_policy_can_be_removed_by_amendment() -> None:
    store, first = _decide(
        amendments_enabled=True, payload={"max_withdraw_units": 2}
    )
    assert first.deterministically_valid
    store.propose("p2", "a1", 12, "Remove quota", {"max_withdraw_units": None})
    for voter in ("a1", "a2", "a3", "a4"):
        store.record_vote(make_vote(voter, "p2", 12, "for"))
    outcome = store.enact_if_valid([], 12, proposal_id="p2")
    assert outcome.deterministically_valid
    assert "max_withdraw_units" not in store.active_at(12).rules
