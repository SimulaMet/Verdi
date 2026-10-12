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


def test_render_history_summary_natural_language():
    mem = AgentMemory()
    mem.append_reflection("a1", "first entry")
    summary = mem.render_history_summary("a1")
    assert summary.startswith("What you remember:")
    assert "you reflected: first entry" in summary
    # No version numbers or JSON in the rendered output
    assert "v1" not in summary
    assert "{" not in summary


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


def test_consolidation_triggers_when_window_overflows():
    """Entries beyond _RECENT_WINDOW trigger consolidation (not silent drop)."""
    from verdi.society.memory import _RECENT_WINDOW
    mem = AgentMemory()
    for i in range(_RECENT_WINDOW + 3):
        mem.append_reflection("a1", f"entry {i}")
    assert mem.version("a1") == _RECENT_WINDOW + 3
    assert mem.needs_consolidation("a1") is True
    overflow = mem.get_overflow_entries("a1")
    assert len(overflow) == 3
    assert overflow[0]["text"] == "entry 0"

    # Apply consolidation (simulating LLM output)
    mem.apply_consolidation("a1", "I remember entries zero, one, and two from early on.")
    assert mem.needs_consolidation("a1") is False
    summary = mem.render_history_summary("a1")
    assert "From earlier:" in summary
    assert "entries zero, one, and two" in summary
    assert "entry 0" not in summary.split("From earlier:")[1]  # not in recent
    assert "entry 15" in summary  # most recent still there


def test_consolidation_preserves_recent_content():
    """After consolidation, recent entries are untouched."""
    from verdi.society.memory import _RECENT_WINDOW
    mem = AgentMemory()
    for i in range(_RECENT_WINDOW + 2):
        mem.append_reflection("a1", f"memory {i}")
    # Before consolidation: all entries present
    pre = mem.render_history_summary("a1")
    assert "memory 0" in pre and "memory 17" in pre

    # Consolidate the overflow
    mem.apply_consolidation("a1", "I remember my earliest experiences.")
    post = mem.render_history_summary("a1")
    # Recent entries intact
    assert "memory 15" in post
    assert "memory 17" in post
    # Old entries replaced by summary
    assert "From earlier:" in post
    assert "earliest experiences" in post


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
    assert "no memories yet" in mem.render_history_summary("ghost")


def test_blank_reflection_rejected():
    with pytest.raises(ValueError, match="non-blank"):
        AgentMemory().append_reflection("a1", "   ")


def test_invalid_max_entries_rejected():
    with pytest.raises(ValueError, match="max_entries"):
        AgentMemory(max_entries=0)
