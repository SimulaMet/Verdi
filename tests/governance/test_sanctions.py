"""Sanction engine tests: evidence requirement, expiry, rights, false tracking."""
from __future__ import annotations

import pytest

from verdi_agents.governance import SanctionEngine
from verdi_agents.governance.sanctions import SanctionError

from .conftest import make_evidence, make_sanction


def test_impose_updates_rights_view():
    engine = SanctionEngine()
    sanction = make_sanction("s-1", "a2", ["speaking", "voting"])
    record = engine.impose(sanction)
    assert record.expires_at == 5 + 3
    assert engine.rights.revoked_rights("a2", 5) == frozenset({"speaking", "voting"})
    assert engine.rights.revoked_rights("a2", 4) == frozenset()  # before effective
    assert not engine.rights.has_active_revocation("a1", 5)


def test_sanction_auto_expires_after_duration():
    engine = SanctionEngine()
    engine.impose(make_sanction("s-1", "a2", ["speaking"], duration=3, effective_tick=5))
    assert engine.is_active("s-1", 5)
    assert engine.is_active("s-1", 7)
    assert engine.rights.revoked_rights("a2", 7) == frozenset({"speaking"})
    expired = engine.advance_tick(8)  # 5 + 3 = 8 -> expires
    assert expired == ["s-1"]
    assert not engine.is_active("s-1", 8)
    assert engine.rights.revoked_rights("a2", 8) == frozenset()
    # Late advances are idempotent.
    assert engine.advance_tick(9) == []


def test_expire_not_early():
    engine = SanctionEngine()
    engine.impose(make_sanction("s-1", "a2", ["speaking"], duration=3, effective_tick=5))
    assert engine.advance_tick(7) == []  # still within duration
    assert engine.rights.revoked_rights("a2", 7) == frozenset({"speaking"})


def test_sanction_without_evidence_refused():
    engine = SanctionEngine()
    from pydantic import ValidationError

    from verdi_agents.schemas import Sanction

    # Schema itself refuses empty evidence (frozen contract).
    with pytest.raises(ValidationError):
        Sanction.model_validate({
            "sanction_id": "s-empty", "subject_id": "a2", "evidence": [],
            "scope": ["speaking"], "duration_ticks": 3, "effective_tick": 5,
            "restoration_policy": "auto_after_duration_plus_appeal",
        })
    # Engine re-validates and refuses even if validation is bypassed.
    bare = Sanction.model_construct(
        sanction_id="s-empty", subject_id="a2", evidence=[],
        scope=["speaking"], duration_ticks=3, effective_tick=5,
        restoration_policy="auto_after_duration_plus_appeal",
    )
    with pytest.raises(SanctionError):
        engine.impose(bare)
    assert engine.history == []


def test_sanction_evidence_without_event_ids_refused():
    engine = SanctionEngine()
    # A reference with no cited event ids is evidence in name only -> refused.
    empty_ev = make_evidence("ev-0", [])
    from verdi_agents.schemas import Sanction

    bare = Sanction(
        sanction_id="s-1", subject_id="a2", evidence=[empty_ev],
        scope=["speaking"], duration_ticks=3, effective_tick=5,
        restoration_policy="auto_after_duration_plus_appeal",
    )
    with pytest.raises(SanctionError):
        engine.impose(bare)


def test_duplicate_sanction_refused():
    engine = SanctionEngine()
    sanction = make_sanction("s-1", "a2", ["speaking"])
    engine.impose(sanction)
    with pytest.raises(SanctionError):
        engine.impose(sanction)


def test_evidence_stored_for_false_sanction_tracking():
    engine = SanctionEngine()
    engine.impose(make_sanction("s-1", "a2", ["speaking"]))
    evidence = engine.evidence_for("s-1")
    assert [ev.event_ids for ev in evidence] == [[3, 4]]
    engine.mark_false_sanction("s-1", "judge found event log misattributed")
    assert engine.false_sanction_flags["s-1"].startswith("judge")


def test_active_for_agent():
    engine = SanctionEngine()
    engine.impose(make_sanction("s-1", "a2", ["speaking"]))
    engine.impose(make_sanction("s-2", "a3", ["voting"], effective_tick=5))
    assert [s.sanction_id for s in engine.active_for("a2", 5)] == ["s-1"]
    assert [s.sanction_id for s in engine.active_for("a3", 5)] == ["s-2"]
    assert engine.active_for("a9", 5) == []
    assert len(engine.history) == 2
