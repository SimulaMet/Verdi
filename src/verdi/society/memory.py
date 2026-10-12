"""Private, per-agent, versioned memory with compaction + contamination guards.

Memory is private: entries are keyed per agent and
:meth:`AgentMemory.render_history_summary` can only return the requested
agent's own entries, never another agent's. Compaction truncates the
render window (oldest first) but never rewrites committed entry content;
a running total keeps track of how many entries were dropped.

``render_history_summary`` is deterministic: the same stored entries always
produce the same string, which is what makes the prompt-observable feedback
test (docs/SOCIETY_V2_PROTOCOL.md, section 3) possible.
"""
from __future__ import annotations

import json

#: Marker token that flags a persona as the matched-opportunity adversary.
ADVERSARY_MARKER = "[adversary]"

_ENTRY_KINDS = ("observation", "reflection")
#: Max characters per memory entry when rendered into prompts (Tranche 12j).
#: Full state snapshots as raw JSON otherwise grow every action prompt
#: linearly with the timeline (observed: 100K+ tokens at tick ~60 of a
#: 5x50 run, corr(prompt_tokens, call_duration)=0.91). The stored entry is
#: also capped so checkpoints stay bounded.
_MAX_ENTRY_CHARS = 300


class AgentMemory:
    """Private per-agent memory store with versioning and compaction."""

    def __init__(self, max_entries: int = 24) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: dict[str, list[dict[str, str]]] = {}
        self._total: dict[str, int] = {}

    def append_observations(self, agent_id: str, obs_dict: dict) -> int:
        """Record one committed observation dict; returns the new version.

        Stored as a compact deterministic summary (bounded to
        ``_MAX_ENTRY_CHARS``) — the full state snapshot would make every
        action prompt grow linearly with the timeline (Tranche 12j).
        """
        keys = sorted(obs_dict)
        compact = {k: obs_dict[k] for k in keys
                   if k in ("tick", "action_kind", "task_id", "arguments",
                            "outcome", "decision", "sanctioned")}
        if not compact:  # keep arbitrary (e.g. test) payloads, still bounded
            compact = obs_dict
        text = json.dumps(compact, sort_keys=True, ensure_ascii=True)
        if len(text) > _MAX_ENTRY_CHARS:
            text = text[:_MAX_ENTRY_CHARS - 2] + ".."
        return self._add(agent_id, "observation", text)

    def append_reflection(self, agent_id: str, reflection: str) -> int:
        """Record one self-reflection string; returns the new version."""
        if not reflection.strip():
            raise ValueError("reflection must be non-blank")
        text = reflection if len(reflection) <= _MAX_ENTRY_CHARS else reflection[:_MAX_ENTRY_CHARS - 2] + ".."
        return self._add(agent_id, "reflection", text)

    def version(self, agent_id: str) -> int:
        """Number of entries ever committed for this agent (0 if unseen)."""
        return self._total.get(agent_id, 0)

    def render_history_summary(self, agent_id: str) -> str:
        """Deterministic prompt-facing text of this agent's recent entries.

        Includes ONLY ``agent_id``'s own entries (privacy guard): an
        observation stored under another agent can never appear here.
        """
        entries = self._entries.get(agent_id)
        if not entries:
            return "Your recorded history (v0):\n(no entries)"
        lines = [f"Your recorded history (v{self.version(agent_id)}):"]
        start = max(0, len(entries) - self._max_entries)
        for n, entry in enumerate(entries[start:], start=start + 1):
            lines.append(f"{n}. [{entry['kind']}] {entry['text']}")
        dropped = self._total[agent_id] - len(entries)
        if dropped > 0:
            lines.append(f"({dropped} earlier entr{'y' if dropped == 1 else 'ies'} dropped by compaction)")
        return "\n".join(lines)

    def to_checkpoint(self) -> bytes:
        """Stable JSON bytes of every agent's retained entries + totals."""
        payload = {
            "max_entries": self._max_entries,
            "agents": {
                agent_id: {"entries": entries, "total": self._total[agent_id]}
                for agent_id, entries in sorted(self._entries.items())
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @classmethod
    def from_checkpoint(cls, blob: bytes, *, max_entries: int = 24) -> AgentMemory:
        """Restore an :class:`AgentMemory` from :meth:`to_checkpoint` bytes.

        The ``max_entries`` argument (not the checkpointed one) bounds the
        restore-time render window.
        """
        payload = json.loads(blob.decode("utf-8"))
        mem = cls(max_entries=max_entries)
        for agent_id, data in payload.get("agents", {}).items():
            mem._entries[agent_id] = list(data["entries"])
            mem._total[agent_id] = data["total"]
        return mem

    def _add(self, agent_id: str, kind: str, text: str) -> int:
        if kind not in _ENTRY_KINDS:
            raise ValueError(f"unknown entry kind {kind!r}")
        entries = self._entries.setdefault(agent_id, [])
        entries.append({"kind": kind, "text": text})
        # Version = running count of entries ever committed (0 if unseen);
        # compaction below only shrinks the retained window, never the count.
        self._total[agent_id] = self._total.get(agent_id, 0) + 1
        total = self._total[agent_id]
        if len(entries) > self._max_entries:
            del entries[: len(entries) - self._max_entries]
        return total


__all__ = ["ADVERSARY_MARKER", "AgentMemory"]
