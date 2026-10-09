"""Optional bridge to the real SimpleAudit package (lazy, never at import).

SimpleAudit is integrator-owned and not published to PyPI, so it must NOT be
declared as a dependency. This module imports ``simpleaudit.tracing`` only
when :func:`build_simpleaudit_trace_source` is called and wraps a
SimpleAudit ``TraceProvider`` (e.g. ``BuiltinOTLP``) behind the adapter's
structural :class:`~verdi.audit.adapter.TraceSource` protocol.

If the package is unavailable, :class:`SimpleAuditNotInstalledError` is
raised with an actionable message — tests exercise exactly that path.
"""
from __future__ import annotations

from typing import Any


class SimpleAuditNotInstalledError(ImportError):
    """Raised when the SimpleAudit package cannot be imported."""

    def __init__(self, detail: str = "") -> None:
        message = (
            "The 'simpleaudit' package is not installed in this environment. "
            "SimpleAudit is integrator-owned and not on PyPI: add it to the "
            "PYTHONPATH of the integration environment, e.g. "
            "PYTHONPATH=/path/to/SimpleAudit:$PYTHONPATH, or set "
            "VERDI_SIMPLEAUDIT_PATH to the directory containing the "
            "'simpleaudit' package. If real tracing is not required, use "
            "verdi.audit.in_memory.InMemoryTraceSource instead."
        )
        if detail:
            message += f" (import failure: {detail})"
        super().__init__(message)
        self.detail = detail


def _import_simpleaudit_tracing() -> Any:
    import os

    try:
        import simpleaudit.tracing as tracing
    except ImportError as exc:
        override = os.environ.get("VERDI_SIMPLEAUDIT_PATH")
        if override:
            try:
                import sys

                if override not in sys.path:
                    sys.path.insert(0, override)
                import simpleaudit.tracing as tracing
            except ImportError as exc2:
                raise SimpleAuditNotInstalledError(str(exc2)) from exc2
        else:
            raise SimpleAuditNotInstalledError(str(exc)) from exc
    return tracing


class SimpleAuditTraceSource:
    """Adapts a SimpleAudit TraceProvider to the TraceSource protocol."""

    def __init__(self, provider: Any, *, default_trace_id: str | None = None) -> None:
        self._provider = provider
        self._default_trace_id = default_trace_id

    def start(self) -> SimpleAuditTraceSource:
        self._provider.start()
        return self

    def stop(self) -> None:
        self._provider.stop()

    @property
    def endpoint(self) -> str | None:
        return self._provider.endpoint

    def fetch(self, trace_id: str | None = None) -> list[dict[str, Any]]:
        if trace_id is None:
            if self._default_trace_id is None:
                raise ValueError("no trace_id given and no default_trace_id set")
            trace_id = self._default_trace_id
        return list(self._provider.fetch(trace_id))

    @property
    def provider(self) -> Any:
        return self._provider


def build_simpleaudit_trace_source(
    provider: Any | None = None, *, default_trace_id: str | None = None
):
    """Build a TraceSource backed by a real SimpleAudit TraceProvider.

    ``provider`` may be an already-constructed SimpleAudit provider
    (e.g. ``simpleaudit.tracing.BuiltinOTLP()``) or a class to instantiate
    with no arguments; when omitted, SimpleAudit's ``BuiltinOTLP`` is used.
    Importing simpleaudit happens only here, at call time, so module import
    of this file is always side-effect free.
    """
    tracing = _import_simpleaudit_tracing()
    provider_cls: Any = tracing.TraceProvider
    if provider is None:
        instance: Any = tracing.BuiltinOTLP()
    elif isinstance(provider, type):
        instance = provider()
    else:
        instance = provider
    if not isinstance(instance, provider_cls):
        raise TypeError(
            f"provider must be a SimpleAudit TraceProvider instance or subclass, "
            f"got {type(instance)!r}"
        )
    return SimpleAuditTraceSource(instance, default_trace_id=default_trace_id)


__all__ = [
    "SimpleAuditNotInstalledError",
    "SimpleAuditTraceSource",
    "build_simpleaudit_trace_source",
]
