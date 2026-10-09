"""Adapter-level audit evidence: TraceSource protocol, AuditAdapter, sources."""
from verdi.audit.adapter import AuditAdapter, TraceSource
from verdi.audit.in_memory import InMemoryTraceSource

__all__ = ["AuditAdapter", "InMemoryTraceSource", "TraceSource"]
