"""G0/G1 tests for the audit adapter (P1-D)."""
from __future__ import annotations

import pytest

from verdi.audit import AuditAdapter, InMemoryTraceSource
from verdi.schemas import AuditRecord, WorldEvent

RUBRIC = "rubric-1"
SCORER = "option-rubric/v1.0"


def _event(event_id: int, tick: int = 0) -> WorldEvent:
    return WorldEvent(
        event_id=event_id,
        tick=tick,
        actor_id=f"a{event_id}",
        kind="action_accepted",
        before_hash=f"h{event_id}",
        prev_hash=f"prev{event_id}",
        event_hash=f"e{event_id}",
        decision="accepted",
    )


def _span(trace_id: str, span_id: str, name: str = "verdi.event") -> dict:
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "name": name,
        "kind": "INTERNAL",
        "attributes": {"secret.api_key": "do-not-leak"},
        "parent_span_id": None,
        "start_time": 0,
        "end_time": 1,
        "status": "OK",
    }


def _adapter() -> tuple[AuditAdapter, InMemoryTraceSource]:
    source = InMemoryTraceSource()
    adapter = AuditAdapter(source, rubric_id=RUBRIC, scorer=SCORER)
    adapter.start()
    return adapter, source


def test_audit_record_roundtrip_json() -> None:
    adapter, source = _adapter()
    source.add_span(_span("t-1", "s-1"))
    adapter.record_event(_event(3), "t-1")
    record = adapter.correlate("t-1")

    dumped = record.model_dump()
    restored = AuditRecord.model_validate(dumped)
    assert restored == record
    assert record.audit_trace_id == "t-1"
    assert record.event_ids == [3]
    assert record.rubric_id == RUBRIC
    assert record.scorer == SCORER
    assert record.verified_ground_truth is None
    # No secrets: raw span attributes must never be embedded.
    blob = str(dumped)
    assert "do-not-leak" not in blob


def test_correlate_links_only_attached_event_ids() -> None:
    adapter, source = _adapter()
    source.add_span(_span("t-a", "s-a1"))
    source.add_span(_span("t-a", "s-a2"))
    source.add_span(_span("t-b", "s-b1"))
    adapter.record_event(_event(1), "t-a")
    adapter.record_event(_event(2), "t-b")
    adapter.record_event(_event(5), "t-a")

    record_a = adapter.correlate("t-a")
    record_b = adapter.correlate("t-b")
    # t-a sees only its own spans and only its own events.
    assert record_a.event_ids == [1, 5]
    assert record_a.details["span_count"] == 2
    assert set(record_a.details["span_ids"]) == {"s-a1", "s-a2"}
    assert record_b.event_ids == [2]
    assert record_b.details["span_count"] == 1

    # A trace with no attached events correlates to no events.
    source.add_span(_span("t-c", "s-c1"))
    assert adapter.correlate("t-c").event_ids == []
    assert adapter.correlate("t-none").event_ids == []


def test_correlate_requires_started_adapter() -> None:
    source = InMemoryTraceSource()
    adapter = AuditAdapter(source, rubric_id=RUBRIC, scorer=SCORER)
    with pytest.raises(RuntimeError):
        adapter.correlate("t-1")
    adapter.start()
    assert adapter.correlate("t-1").audit_trace_id == "t-1"
    adapter.stop()
    with pytest.raises(RuntimeError):
        adapter.correlate("t-1")


def test_record_event_requires_trace_id() -> None:
    adapter, _ = _adapter()
    with pytest.raises(ValueError):
        adapter.record_event(_event(0), "")


def test_oracle_sets_verified_ground_truth_only_when_given() -> None:
    adapter, source = _adapter()
    source.add_span(_span("t-1", "s-1"))
    assert adapter.correlate("t-1").verified_ground_truth is None
    assert adapter.correlate("t-1", oracle=True).verified_ground_truth is True
    assert adapter.correlate("t-1", oracle=False).verified_ground_truth is False


def test_inmemory_fetch_is_isolated_per_trace_and_stable() -> None:
    source = InMemoryTraceSource()
    source.start()
    source.add_span(_span("t-1", "s-1"))
    source.add_span(_span("t-2", "s-2"))
    assert [s["span_id"] for s in source.fetch("t-1")] == ["s-1"]
    # fetch returns copies; mutating them must not corrupt the store.
    fetched = source.fetch("t-1")
    fetched[0]["name"] = "mutated"
    assert source.fetch("t-1")[0]["name"] == "verdi.event"
    # stop() discards spans (BuiltinOTLP semantics).
    source.stop()
    assert source.fetch("t-1") == []


def test_trace_source_protocol_runtime_checkable() -> None:
    from verdi.audit.adapter import TraceSource

    assert isinstance(InMemoryTraceSource(), TraceSource)
