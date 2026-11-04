"""Appeal engine tests: deadline, upholding, restoration, metrics."""
from __future__ import annotations

import pytest

from verdi_agents.governance import AppealEngine, SanctionEngine
from verdi_agents.governance.appeals import AppealError

from .conftest import make_appeal, make_sanction


def _engine_with_sanction(deadline=8):
    sanctions = SanctionEngine()
    sanction = make_sanction("s-1", "a2", ["speaking"], effective_tick=5, deadline=deadline)
    sanctions.impose(sanction)
    return sanctions, AppealEngine(sanctions), sanction


def test_appeal_within_deadline_filed():
    _, appeals, _ = _engine_with_sanction()
    outcome = appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    assert outcome.status == "filed"
    assert outcome.out_of_time is False
    assert appeals.status_of("ap-1") == "filed"


def test_appeal_exactly_at_deadline_allowed():
    _, appeals, _ = _engine_with_sanction(deadline=8)
    outcome = appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 8))
    assert outcome.out_of_time is False


def test_appeal_after_deadline_out_of_time():
    _, appeals, _ = _engine_with_sanction(deadline=8)
    outcome = appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 9))
    assert outcome.status == "dismissed"
    assert outcome.out_of_time is True
    assert appeals.metrics.out_of_time == 1


def test_upheld_restores_rights_and_records_restoration():
    sanctions, appeals, sanction = _engine_with_sanction()
    appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    outcome = appeals.adjudicate("ap-1", upheld=True, grounds="evidence flawed", tick=7)
    assert outcome.status == "upheld"
    restoration = outcome.restoration
    assert restoration is not None
    assert restoration.agent_id == "a2"
    assert restoration.effective_tick == 7
    assert restoration.rights_restored == ["speaking"]
    # Rights restored immediately.
    assert sanctions.rights.revoked_rights("a2", 7) == frozenset()
    assert not sanctions.is_active("s-1", 7)
    assert appeals.restoration_for("s-1") is restoration
    assert appeals.metrics.upheld == 1


def test_dismissed_keeps_sanction():
    sanctions, appeals, _ = _engine_with_sanction()
    appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    outcome = appeals.adjudicate("ap-1", upheld=False, grounds="grounds rejected", tick=7)
    assert outcome.status == "dismissed"
    assert outcome.restoration is None
    assert sanctions.rights.revoked_rights("a2", 7) == frozenset({"speaking"})
    assert sanctions.is_active("s-1", 7)
    assert appeals.metrics.dismissed == 1
    assert appeals.restoration_for("s-1") is None


def test_cannot_adjudicate_out_of_time_appeal():
    _, appeals, _ = _engine_with_sanction(deadline=8)
    appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 9))
    with pytest.raises(AppealError):
        appeals.adjudicate("ap-1", upheld=True, grounds="late", tick=10)


def test_wrong_appellant_rejected():
    _, appeals, _ = _engine_with_sanction()
    with pytest.raises(AppealError):
        appeals.file_appeal(make_appeal("ap-1", "s-1", "a3", 6))


def test_duplicate_appeal_rejected():
    _, appeals, _ = _engine_with_sanction()
    appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    with pytest.raises(AppealError):
        appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 7))


def test_metrics_counts():
    sanctions = SanctionEngine()
    sanctions.impose(make_sanction("s-1", "a2", ["speaking"], deadline=8))
    sanctions.impose(make_sanction("s-2", "a3", ["voting"], deadline=8))
    appeals = AppealEngine(sanctions)
    appeals.file_appeal(make_appeal("ap-1", "s-1", "a2", 6))
    appeals.file_appeal(make_appeal("ap-2", "s-2", "a3", 9))  # out of time
    appeals.adjudicate("ap-1", upheld=True, grounds="ok", tick=7)
    m = appeals.metrics
    assert m.upheld == 1
    assert m.out_of_time == 1
    assert m.total_adjudicated == 1
    assert len(appeals.outcomes) == 3  # filed, dismissed (o.o.t.), upheld
