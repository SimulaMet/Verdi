"""Audit adapter: bridges immutable world events to a trace/audit backend.

SimpleAudit is reused, not rewritten: this adapter talks to a minimal
``TraceSource`` protocol that mirrors SimpleAudit's ``TraceProvider`` contract
(``start()`` / ``endpoint`` / ``fetch(trace_id)`` / ``stop()``). Any
in-memory fake or the real SimpleAudit provider (via
:mod:`verdi.audit.simpleaudit_bridge`) satisfies it.

Guarantees:
- Records never embed raw span attributes, only span identity and names,
  so no secret material can leak into an ``AuditRecord``.
- Event truth (which world events exist) stays separate from audit evidence
  (which spans were observed); audit evidence never claims ground truth
  unless an oracle explicitly attests one.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from verdi.schemas import AuditRecord, WorldEvent


@runtime_checkable
class TraceSource(Protocol):
    """Minimal structural contract mirroring SimpleAudit's TraceProvider."""

    def start(self) -> TraceSource: ...

    def stop(self) -> None: ...

    @property
    def endpoint(self) -> str | None:
        """OTLP endpoint the target should export to, or None for fetch-based."""
        ...

    def fetch(self, trace_id: str) -> list[dict[str, Any]]:
        """Return the normalized spans for ``trace_id`` (may be empty)."""
        ...


class AuditAdapter:
    """Attaches trace ids to ledger entries and builds frozen AuditRecords.

    ``record_event`` is the only way a trace learns about world events, so a
    trace can only ever be correlated with events that were explicitly
    attached to it — never with events from other traces.
    """

    def __init__(self, source: TraceSource, *, rubric_id: str, scorer: str) -> None:
        self._source = source
        self._rubric_id = rubric_id
        self._scorer = scorer
        self._trace_events: dict[str, set[int]] = {}
        self._running = False

    def start(self) -> None:
        self._source.start()
        self._running = True

    def stop(self) -> None:
        self._source.stop()
        self._running = False

    @property
    def source(self) -> TraceSource:
        return self._source

    def record_event(self, event: WorldEvent, trace_id: str) -> int:
        """Link an immutable world event to a trace. Returns the event_id."""
        if not trace_id:
            raise ValueError("trace_id must be a non-empty string")
        self._trace_events.setdefault(trace_id, set()).add(event.event_id)
        return event.event_id

    def linked_event_ids(self, trace_id: str) -> list[int]:
        return sorted(self._trace_events.get(trace_id, ()))

    def correlate(self, trace_id: str, *, oracle: bool | None = None) -> AuditRecord:
        """Build an AuditRecord from the trace's spans + linked event_ids.

        ``verified_ground_truth`` stays ``None`` unless an oracle explicitly
        attests one (True/False); observed spans alone never claim ground
        truth.
        """
        if not self._running:
            raise RuntimeError("AuditAdapter is not started; call start() first")
        spans = self._source.fetch(trace_id)
        return AuditRecord(
            audit_trace_id=trace_id,
            event_ids=self.linked_event_ids(trace_id),
            rubric_id=self._rubric_id,
            scorer=self._scorer,
            confidence=None,
            verified_ground_truth=oracle,
            details={
                "span_count": len(spans),
                "span_ids": [str(s.get("span_id", "")) for s in spans if s.get("span_id")],
                "span_names": [str(s.get("name", "")) for s in spans],
            },
        )


__all__ = ["AuditAdapter", "TraceSource"]
