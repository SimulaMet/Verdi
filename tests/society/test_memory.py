"""Tests for src/verdi/society/memory.py."""
from __future__ import annotations

import pytest

from verdi.society import AgentMemory


def test_version_bumps_on_appends():
    mem = AgentMemory()
    assert mem.version("a1") == 0
    assert mem.append_observations("a1", {"tick": 1, "signal": "hi"}) == 1
    assert mem.append_reflection("a1", "I said hi at tick 1") == 2
    assert mem.version("a1") == 2


def test_render_is_deterministic():
    def build() -> AgentMemory:
        m = AgentMemory()
        m.append_observations("a1", {"tick": 1, "event": "transfer to a2"})
        m.append_reflection("a1", "should have kept more food")
        return m

    a, b = build(), build()
    assert a.render_history_summary("a1") == b.render_history_summary("a1")


def test_render_history_summary_header_includes_version():
    mem = AgentMemory()
    mem.append_reflection("a1", "first entry")
    summary = mem.render_history_summary("a1")
    assert summary.startswith("Your recorded history (v1):")
    assert "1. [reflection] first entry" in summary


def test_per_agent_privacy():
    mem = AgentMemory()
    mem.append_observations("a1", {"secret_only_for_a1": "top secret"})
    mem.append_reflection("a2", "a2 secret")
    summary_a1 = mem.render_history_summary("a1")
    summary_a2 = mem.render_history_summary("a2")
    assert "top secret" in summary_a1
    assert "a2 secret" not in summary_a1
    assert "a2 secret" in summary_a2
    assert "top secret" not in summary_a2


def test_compaction_truncates_but_preserves_determinism():
    mem = AgentMemory(max_entries=3)
    for i in range(7):
        mem.append_reflection("a1", f"entry {i}")
    assert mem.version("a1") == 7  # running count never lost
    summary = mem.render_history_summary("a1")
    assert "entry 0" not in summary and "entry 1" not in summary
    assert "entry 4" in summary and "entry 6" in summary
    assert "(4 earlier entries dropped by compaction)" in summary
    # Determinism: rebuilding the same sequence gives the same summary.
    mem2 = AgentMemory(max_entries=3)
    for i in range(7):
        mem2.append_reflection("a1", f"entry {i}")
    assert mem2.render_history_summary("a1") == summary


def test_compaction_never_rewrites_committed_content():
    mem = AgentMemory(max_entries=2)
    mem.append_reflection("a1", "first")
    first_before = mem.render_history_summary("a1")
    assert "1. [reflection] first" in first_before
    mem.append_reflection("a1", "second")
    mem.append_reflection("a1", "third")
    summary = mem.render_history_summary("a1")
    # Oldest entry dropped entirely; the retained window keeps exact
    # content+kind (renumbered within the window, which is deterministic).
    assert "first" not in summary
    assert "1. [reflection] second" in summary
    assert "2. [reflection] third" in summary


def test_checkpoint_round_trip_preserves_summary():
    mem = AgentMemory(max_entries=24)
    mem.append_observations("a1", {"tick": 1, "amount": 2})
    mem.append_reflection("a1", "note one")
    mem.append_observations("a2", {"tick": 1, "event": "signal"})
    blob = mem.to_checkpoint()
    assert isinstance(blob, bytes)
    # Stable bytes: same state -> same checkpoint.
    blob2 = mem.to_checkpoint()
    assert blob == blob2

    restored = AgentMemory.from_checkpoint(blob, max_entries=24)
    for agent_id in ("a1", "a2"):
        assert restored.render_history_summary(agent_id) == mem.render_history_summary(agent_id)
        assert restored.version(agent_id) == mem.version(agent_id)


def test_checkpoint_round_trip_with_compaction():
    mem = AgentMemory(max_entries=2)
    for i in range(5):
        mem.append_reflection("a1", f"e{i}")
    restored = AgentMemory.from_checkpoint(mem.to_checkpoint(), max_entries=2)
    assert restored.render_history_summary("a1") == mem.render_history_summary("a1")


def test_unseen_agent_version_zero_and_empty_summary():
    mem = AgentMemory()
    assert mem.version("ghost") == 0
    assert "v0" in mem.render_history_summary("ghost")


def test_blank_reflection_rejected():
    with pytest.raises(ValueError, match="non-blank"):
        AgentMemory().append_reflection("a1", "   ")


def test_invalid_max_entries_rejected():
    with pytest.raises(ValueError, match="max_entries"):
        AgentMemory(max_entries=0)
