"""In-memory, deterministic TraceSource for tests and offline replay."""
from __future__ import annotations

from typing import Any


class InMemoryTraceSource:
    """List-backed TraceSource mirroring SimpleAudit's SpanStore queries.

    Spans are plain dicts in SimpleAudit's normalized shape:
    ``span_id``, ``trace_id``, ``name``, ``kind``, ``attributes``,
    ``parent_span_id``, ``start_time``, ``end_time``, ``status``.
    """

    def __init__(self) -> None:
        self._spans: list[dict[str, Any]] = []
        self._running = False

    def start(self) -> InMemoryTraceSource:
        self._running = True
        return self

    def stop(self) -> None:
        self._running = False
        self._spans.clear()

    @property
    def endpoint(self) -> str | None:
        # In-memory source has no transport; endpoint is only meaningful for
        # BuiltinOTLP-style sources.
        return None

    def add_span(self, span: dict[str, Any]) -> dict[str, Any]:
        """Append one span (deduplicated by span_id, last write wins)."""
        span_id = span.get("span_id")
        if not span_id:
            raise ValueError("span_id is required")
        for i, existing in enumerate(self._spans):
            if existing.get("span_id") == span_id:
                self._spans[i] = dict(span)
                return dict(span)
        self._spans.append(dict(span))
        return dict(span)

    def fetch(self, trace_id: str) -> list[dict[str, Any]]:
        return [dict(s) for s in self._spans if s.get("trace_id") == trace_id]

    def __len__(self) -> int:
        return len(self._spans)


__all__ = ["InMemoryTraceSource"]
