"""Private, per-agent, versioned memory with LLM-based consolidation.

Memory is private: entries are keyed per agent and
:meth:`AgentMemory.render_history_summary` can only return the requested
agent's own entries, never another agent's.

Consolidation model (inspired by human memory consolidation):
- Recent events stay as detailed individual entries (last ``_RECENT_WINDOW``).
- When the window overflows, the campaign triggers an LLM "consolidation"
  call where the agent reflects on their older memories and produces a
  compressed narrative summary ("Earlier, I remember...").
- The summary replaces the dropped entries — nothing is silently lost.
- The agent always has *some* trace of their full history, just at
  different granularities (detailed recent + summarized distant past).

``render_history_summary`` is deterministic: the same stored state always
produces the same string (replay-safe).
"""
from __future__ import annotations

import json

#: Marker token that flags a persona as the matched-opportunity adversary.
ADVERSARY_MARKER = "[adversary]"

_ENTRY_KINDS = ("observation", "reflection")
#: Max characters per memory entry when rendered into prompts.
_MAX_ENTRY_CHARS = 300
#: Number of recent entries kept in full detail before consolidation triggers.
_RECENT_WINDOW = 16
#: Max characters for the consolidated summary (bounded prompt growth).
_MAX_SUMMARY_CHARS = 500


def _render_observation_natural(obs_dict: dict) -> str:
    """Render an observation dict as natural language an agent would recall.

    Avoids raw JSON, tick numbers, task IDs, and schema field names.
    The agent should remember *what happened*, not *which event ID*.
    """
    parts: list[str] = []

    action_kind = obs_dict.get("action_kind")
    arguments = obs_dict.get("arguments", {})
    decision = obs_dict.get("decision")
    outcome = obs_dict.get("outcome")
    sanctioned = obs_dict.get("sanctioned")

    if action_kind:
        args_text = _describe_args(action_kind, arguments)
        if decision == "rejected" or outcome == "rejected":
            parts.append(f"I tried to {args_text} but it was rejected")
        else:
            parts.append(f"I {args_text}")
    elif decision:
        parts.append(f"decision: {decision}")
    elif outcome:
        parts.append(f"outcome: {outcome}")
    else:
        # Fallback: render unknown keys as natural text (no raw JSON)
        known_keys = {"agent_id", "tick", "memory_version", "task_id"}
        meaningful = {k: v for k, v in obs_dict.items() if k not in known_keys}
        if meaningful:
            rendered = "; ".join(f"{k}: {v}" for k, v in sorted(meaningful.items())
                                 if isinstance(v, (str, int, float, bool)))
            if rendered:
                parts.append(rendered[:200])
            else:
                parts.append("something changed in the community")
        else:
            parts.append("something changed in the community")

    if sanctioned is True:
        parts.append("(I am now under sanction)")

    return "; ".join(parts) if parts else "world state updated"


def _describe_args(action_kind: str, args: dict) -> str:
    """Turn (action_kind, arguments) into a short natural phrase."""
    if not isinstance(args, dict):
        return action_kind

    if action_kind == "transfer":
        to = args.get("to", "someone")
        resource = args.get("resource", "something")
        units = args.get("units", "?")
        return f"gave {units} {resource} to {to}"
    elif action_kind == "deposit":
        amount = args.get("amount", "?")
        resource = args.get("resource", "something")
        return f"put {amount} {resource} into the common pool"
    elif action_kind == "withdraw":
        amount = args.get("amount", "?")
        resource = args.get("resource", "something")
        return f"took {amount} {resource} from the common pool"
    elif action_kind == "produce":
        amount = args.get("amount", "?")
        resource = args.get("resource", "something")
        return f"produced {amount} {resource}"
    elif action_kind == "emit_signal":
        text = args.get("text", "")
        snippet = text[:80] + "..." if len(text) > 80 else text
        return f'said "{snippet}"'
    else:
        return f"performed a {action_kind} action"


class AgentMemory:
    """Private per-agent memory store with LLM-based consolidation.

    Two layers:
    - ``_entries``: recent detailed memories (capped at ``_RECENT_WINDOW``)
    - ``_summary``: consolidated narrative of older memories (LLM-produced)

    The campaign calls :meth:`needs_consolidation` and
    :meth:`apply_consolidation` to manage the LLM consolidation cycle.
    """

    def __init__(self, max_entries: int = 24) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: dict[str, list[dict[str, str]]] = {}
        self._summary: dict[str, str] = {}
        self._total: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def append_observations(self, agent_id: str, obs_dict: dict) -> int:
        """Record one committed observation; returns the new version."""
        text = _render_observation_natural(obs_dict)
        if len(text) > _MAX_ENTRY_CHARS:
            text = text[:_MAX_ENTRY_CHARS - 2] + ".."
        return self._add(agent_id, "observation", text)

    def append_reflection(self, agent_id: str, reflection: str) -> int:
        """Record one self-reflection string; returns the new version."""
        if not reflection.strip():
            raise ValueError("reflection must be non-blank")
        text = reflection if len(reflection) <= _MAX_ENTRY_CHARS else reflection[:_MAX_ENTRY_CHARS - 2] + ".."
        return self._add(agent_id, "reflection", text)

    # ------------------------------------------------------------------ #
    # consolidation (called by campaign after LLM call)
    # ------------------------------------------------------------------ #
    def needs_consolidation(self, agent_id: str) -> bool:
        """True if overflow has accumulated enough to warrant a consolidation call.

        Triggers when overflow >= 4 entries (avoids calling LLM every tick;
        with 2 entries/tick, this means consolidating roughly every 2 ticks
        after the window fills).
        """
        entries = self._entries.get(agent_id, [])
        return len(entries) - _RECENT_WINDOW >= 4

    def get_overflow_entries(self, agent_id: str) -> list[dict[str, str]]:
        """Return the entries that exceed the recent window (without removing)."""
        entries = self._entries.get(agent_id, [])
        if len(entries) <= _RECENT_WINDOW:
            return []
        return entries[: len(entries) - _RECENT_WINDOW]

    def apply_consolidation(self, agent_id: str, summary_text: str) -> None:
        """Drop overflow entries and store the LLM-produced summary.

        Called by the campaign AFTER the consolidation LLM call completes.
        The summary replaces all overflow entries with a single narrative.
        """
        entries = self._entries.get(agent_id, [])
        if len(entries) <= _RECENT_WINDOW:
            return
        overflow = len(entries) - _RECENT_WINDOW
        # Remove the oldest entries (they're now captured in the summary)
        del entries[:overflow]
        # Merge with existing summary if any (keep most recent summary content)
        existing = self._summary.get(agent_id, "")
        # Prepend new consolidated content, cap total length
        combined = f"{summary_text}; {existing}" if existing else summary_text
        self._summary[agent_id] = combined[:_MAX_SUMMARY_CHARS]

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def version(self, agent_id: str) -> int:
        """Number of entries ever committed for this agent (0 if unseen)."""
        return self._total.get(agent_id, 0)

    def consolidated_summary(self, agent_id: str) -> str:
        """The LLM-produced early summary alone ("" when none yet)."""
        return self._summary.get(agent_id, "")

    def render_history_summary(self, agent_id: str) -> str:
        """Deterministic prompt-facing text of this agent's memory.

        Two layers:
        1. Consolidated early summary (if any) — LLM-produced narrative
        2. Recent detailed entries — last ``_RECENT_WINDOW`` individual memories
        """
        entries = self._entries.get(agent_id)
        summary = self._summary.get(agent_id, "")

        if not entries and not summary:
            return "You have no memories yet."

        lines = ["What you remember:"]
        if summary:
            lines.append(f"- From earlier: {summary}")
        for entry in entries:
            kind_label = "you noticed" if entry["kind"] == "observation" else "you reflected"
            lines.append(f"- {kind_label}: {entry['text']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # checkpoint
    # ------------------------------------------------------------------ #
    def to_checkpoint(self) -> bytes:
        """Stable JSON bytes of every agent's retained entries + summary."""
        payload = {
            "max_entries": self._max_entries,
            "agents": {
                agent_id: {
                    "entries": self._entries.get(agent_id, []),
                    "summary": self._summary.get(agent_id, ""),
                    "total": self._total.get(agent_id, 0),
                }
                for agent_id in sorted(set(list(self._entries.keys()) + list(self._total.keys())))
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @classmethod
    def from_checkpoint(cls, blob: bytes, *, max_entries: int = 24) -> AgentMemory:
        """Restore an :class:`AgentMemory` from :meth:`to_checkpoint` bytes."""
        payload = json.loads(blob.decode("utf-8"))
        mem = cls(max_entries=max_entries)
        for agent_id, data in payload.get("agents", {}).items():
            mem._entries[agent_id] = list(data.get("entries", []))
            mem._summary[agent_id] = data.get("summary", "")
            mem._total[agent_id] = data.get("total", 0)
        return mem

    # ------------------------------------------------------------------ #
    # internal
    # ------------------------------------------------------------------ #
    def _add(self, agent_id: str, kind: str, text: str) -> int:
        if kind not in _ENTRY_KINDS:
            raise ValueError(f"unknown entry kind {kind!r}")
        entries = self._entries.setdefault(agent_id, [])
        entries.append({"kind": kind, "text": text})
        self._total[agent_id] = self._total.get(agent_id, 0) + 1
        return self._total[agent_id]


__all__ = [
    "ADVERSARY_MARKER",
    "AgentMemory",
    "_RECENT_WINDOW",
    "_MAX_SUMMARY_CHARS",
]
