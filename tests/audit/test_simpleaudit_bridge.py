"""Lazy SimpleAudit bridge tests (simpleaudit is NOT installed here)."""
from __future__ import annotations

import sys

import pytest

import verdi_agents.audit.simpleaudit_bridge as bridge
from verdi_agents.audit.simpleaudit_bridge import (
    SimpleAuditNotInstalledError,
    SimpleAuditTraceSource,
    build_simpleaudit_trace_source,
)


def _simpleaudit_installed() -> bool:
    try:
        import simpleaudit  # noqa: F401

        return True
    except ImportError:
        return False


def test_module_import_does_not_import_simpleaudit() -> None:
    # Freshly import a copy of the module and verify its module-level import
    # has no side effect: importing it must never import simpleaudit.
    import importlib

    fresh_name = "verdi_agents.audit.simpleaudit_bridge"
    original = sys.modules.get(fresh_name)
    sys.modules.pop(fresh_name, None)
    sys.modules.pop("simpleaudit", None)
    sys.modules.pop("simpleaudit.tracing", None)
    try:
        fresh = importlib.import_module(fresh_name)
        assert "simpleaudit" not in sys.modules
        assert "simpleaudit.tracing" not in sys.modules
    finally:
        sys.modules.pop(fresh_name, None)
        if original is not None:
            sys.modules[fresh_name] = original
        assert fresh is not None


def test_build_raises_when_simpleaudit_missing(monkeypatch) -> None:
    # Also forget any cached import machinery state from earlier tests.
    monkeypatch.setitem(sys.modules, "simpleaudit", None)
    monkeypatch.setitem(sys.modules, "simpleaudit.tracing", None)
    # Ensure a bare import cannot succeed even if a path is set.
    monkeypatch.delenv("VERDI_SIMPLEAUDIT_PATH", raising=False)
    if _simpleaudit_installed():
        pytest.skip("simpleaudit unexpectedly installed in this environment")
    with pytest.raises(SimpleAuditNotInstalledError) as excinfo:
        build_simpleaudit_trace_source()
    assert isinstance(excinfo.value, ImportError)
    message = str(excinfo.value)
    # Actionable: tells the operator how to fix it.
    assert "PYTHONPATH" in message or "VERDI_SIMPLEAUDIT_PATH" in message
    assert "InMemoryTraceSource" in message


def test_bridge_wraps_provider_when_available(monkeypatch) -> None:
    """Fake a minimal simpleaudit.tracing module to exercise the wrap path."""
    import types

    class FakeProvider:
        def start(self):
            return self

        def stop(self):
            return None

        @property
        def endpoint(self):
            return "http://127.0.0.1:4318/v1/traces"

        def fetch(self, trace_id):
            return [{"span_id": "s1", "trace_id": trace_id, "name": "x", "kind": "INTERNAL"}]

    fake_tracing = types.ModuleType("simpleaudit.tracing")
    fake_tracing.TraceProvider = FakeProvider
    fake_tracing.BuiltinOTLP = FakeProvider
    fake_pkg = types.ModuleType("simpleaudit")
    fake_pkg.tracing = fake_tracing
    monkeypatch.setitem(sys.modules, "simpleaudit", fake_pkg)
    monkeypatch.setitem(sys.modules, "simpleaudit.tracing", fake_tracing)
    monkeypatch.setattr(bridge, "_import_simpleaudit_tracing", lambda: fake_tracing)

    # Default (no provider) -> instantiates BuiltinOTLP.
    source = build_simpleaudit_trace_source()
    assert isinstance(source, SimpleAuditTraceSource)
    source.start()
    assert source.endpoint == "http://127.0.0.1:4318/v1/traces"
    assert source.fetch("t1")[0]["span_id"] == "s1"
    source.stop()

    # Passing an instance works too.
    source2 = build_simpleaudit_trace_source(FakeProvider())
    assert source2.fetch("t2")[0]["trace_id"] == "t2"

    # Wrong type rejected.
    with pytest.raises(TypeError):
        bridge.build_simpleaudit_trace_source(object())


def test_bridge_with_instance_from_fake_tracing() -> None:
    # Direct wrap without going through the importer.
    class P:
        def start(self):
            return self

        def stop(self):
            return None

        @property
        def endpoint(self):
            return None

        def fetch(self, tid):
            return []

    source = SimpleAuditTraceSource(P(), default_trace_id="default-tid")
    assert source.fetch() == []
    with pytest.raises(ValueError):
        SimpleAuditTraceSource(P()).fetch(None)
