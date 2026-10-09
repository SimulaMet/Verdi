"""GovernanceEngine facade: event emission, lifecycle, determinism (G3)."""
from __future__ import annotations

from verdi.governance.engine import GOVERNANCE_EVENT_KINDS

from .conftest import (
    ELIGIBLE,
    make_appeal,
    make_engine,
    make_sanction,
    make_vote,
)


def _vote_up_to_quorum(engine, proposal_id="p1", tick=10, voters=("a1", "a2")):
    for voter in voters:
        engine.cast_vote(make_vote(voter, proposal_id, tick, "for"))


def test_event_dict_conforms_to_world_event_payload_conventions():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    event = engine.events[-1]
    # kind/actor/tick + rule versions + decision, per WorldEvent.payload conventions.
    assert event["kind"] in GOVERNANCE_EVENT_KINDS
    assert event["actor"] == "a1"
    assert event["tick"] == 10
    assert event["decision"] in ("accepted", "rejected")
    assert event["law_version"] == "law-1"
    assert event["constitution_version"] == "cons-1"


def test_full_enactment_lifecycle_emits_kinds():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    _vote_up_to_quorum(engine, voters=("a1", "a2", "a3", "a4"))
    engine.enact([], 10, proposal_id="p1")
    kinds = [e["kind"] for e in engine.events]
    assert kinds == [
        "proposal_submitted",
        "vote_cast", "vote_cast", "vote_cast", "vote_cast",
        "amendment_enacted",
    ]
    enacted = engine.events_of_kind("amendment_enacted")[0]
    assert enacted["new_version"] != "cons-1"
    assert enacted["decision"] == "accepted"


def test_law_violating_amendment_rejected_not_enacted():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "remove invariant", {"no_double_spend": False})
    _vote_up_to_quorum(engine, voters=("a1", "a2", "a3", "a4"))
    event = engine.enact([], 10, proposal_id="p1")
    assert event["kind"] == "amendment_rejected"
    assert event["decision"] == "rejected"
    assert "law:no_double_spend" in event["invalid_reasons"]
    # Law not silently overridden; constitution unchanged.
    assert engine.constitution.current.version == "cons-1"
    assert not engine.events_of_kind("amendment_enacted")


def test_quorum_not_met_rejected():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    engine.cast_vote(make_vote("a1", "p1", 10, "for"))
    event = engine.enact([], 10, proposal_id="p1")
    assert event["kind"] == "amendment_rejected"
    assert "quorum_not_met" in event["invalid_reasons"]


def test_retroactive_amendment_rejected_via_engine():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "retro", {"tax_rate": 0.2, "effective_from_tick": 9})
    _vote_up_to_quorum(engine, voters=("a1", "a2", "a3", "a4"))
    event = engine.enact([], 10, proposal_id="p1")
    assert event["kind"] == "amendment_rejected"
    assert "retroactive_amendment" in event["invalid_reasons"]


def test_double_vote_deduplicated_deterministically():
    engine = make_engine()
    engine.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
    first = engine.cast_vote(make_vote("a1", "p1", 10, "for"))
    second = engine.cast_vote(make_vote("a1", "p1", 11, "against"))
    assert first["decision"] == "accepted"
    assert second["decision"] == "rejected"
    assert second["reason"] == "duplicate_vote"
    engine.cast_vote(make_vote("a2", "p1", 10, "for"))
    engine.cast_vote(make_vote("a3", "p1", 10, "for"))
    engine.cast_vote(make_vote("a4", "p1", 10, "for"))
    event = engine.enact([], 10, proposal_id="p1")
    assert event["kind"] == "amendment_enacted"
    assert event["for_votes"] == 4  # the duplicate did not change the count


def test_sanction_imposed_event_and_evidence_recorded():
    engine = make_engine()
    event = engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"]))
    assert event["kind"] == "sanction_imposed"
    assert event["decision"] == "accepted"
    assert event["evidence_event_ids"] == [3, 4]
    assert event["scope"] == ["speaking"]


def test_sanction_without_evidence_emits_rejected_event():
    engine = make_engine()
    from verdi.schemas import Sanction

    # Bypass schema validation so the engine-level refusal is exercised.
    bare = Sanction.model_construct(
        sanction_id="s-empty", subject_id="a2", evidence=[],
        scope=["speaking"], duration_ticks=3, effective_tick=5,
        restoration_policy="auto",
    )
    event = engine.impose_sanction(bare)
    assert event["kind"] == "sanction_imposed"
    assert event["decision"] == "rejected"


def test_sanction_expiry_emits_rights_restored():
    engine = make_engine()
    engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"], duration=3,
                                         effective_tick=5))
    assert engine.sanctions.rights.revoked_rights("a2", 6) == frozenset({"speaking"})
    engine.advance_tick(8)
    restored = engine.events_of_kind("rights_restored")
    assert len(restored) == 1
    assert restored[0]["sanction_id"] == "s-1"
    assert restored[0]["rights_restored"] == ["speaking"]
    assert restored[0]["reason"] == "duration_expired"
    assert engine.sanctions.rights.revoked_rights("a2", 8) == frozenset()


def test_appeal_upheld_restores_rights_and_emits_events():
    engine = make_engine()
    engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"], deadline=8))
    filed = engine.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    assert filed["kind"] == "appeal_filed"
    assert filed["decision"] == "accepted"
    events = engine.adjudicate_appeal("ap-1", upheld=True, grounds="flawed evidence",
                                      tick=7)
    assert [e["kind"] for e in events] == ["appeal_upheld", "rights_restored"]
    restored = events[1]
    assert restored["rights_restored"] == ["speaking"]
    assert restored["reason"] == "appeal_upheld"
    assert engine.sanctions.rights.revoked_rights("a2", 7) == frozenset()
    assert engine.appeals.metrics.upheld == 1


def test_appeal_after_deadline_rejected_out_of_time():
    engine = make_engine()
    engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"], duration=6,
                                         effective_tick=5, deadline=6))
    event = engine.file_appeal(make_appeal("ap-1", "s-1", "a2", 7))
    assert event["kind"] == "appeal_filed"
    assert event["decision"] == "rejected"
    assert event["reason"] == "out_of_time"
    # Sanction intact; no restoration recorded.
    assert engine.sanctions.rights.revoked_rights("a2", 7) == frozenset({"speaking"})
    assert engine.appeals.metrics.out_of_time == 1


def test_appeal_dismissed_emits_event_and_keeps_sanction():
    engine = make_engine()
    engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"], deadline=8))
    engine.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    events = engine.adjudicate_appeal("ap-1", upheld=False, grounds="grounds rejected",
                                      tick=7)
    assert [e["kind"] for e in events] == ["appeal_dismissed"]
    assert events[0]["decision"] == "rejected"
    assert engine.sanctions.rights.revoked_rights("a2", 7) == frozenset({"speaking"})


def test_deterministic_replay_same_event_tape():
    """G3: same recorded input tape -> byte-identical governance event log."""

    def run() -> list[dict]:
        engine = make_engine()
        engine.propose("p1", "a1", 10, "tweak", {"tax_rate": 0.2})
        for voter in ("a1", "a2", "a3", "a4"):
            engine.cast_vote(make_vote(voter, "p1", 10, "for"))
        engine.enact([], 10, proposal_id="p1")
        engine.impose_sanction(make_sanction("s-1", "a2", ["speaking"]))
        engine.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
        engine.adjudicate_appeal("ap-1", upheld=True, grounds="ok", tick=7)
        engine.advance_tick(10)
        return engine.events

    assert run() == run()
    import json

    assert json.dumps(run(), sort_keys=True) == json.dumps(run(), sort_keys=True)


def test_events_of_kind_validates_kind():
    engine = make_engine()
    import pytest

    with pytest.raises(ValueError):
        engine.events_of_kind("not_a_kind")
    assert engine.events_of_kind("vote_cast") == []


def test_eligible_voters_from_fixture():
    engine = make_engine()
    assert engine.constitution.eligible_voters == frozenset(ELIGIBLE)
