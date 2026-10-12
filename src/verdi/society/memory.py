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
            # Render as "key: value" pairs but in plain text
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
    elif sanctioned is False and "sanctioned" in obs_dict:
        pass  # no need to note absence

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
        # Generic fallback without dumping raw JSON
        return f"performed a {action_kind} action"


class AgentMemory:
    """Private per-agent memory store with versioning and compaction."""

    def __init__(self, max_entries: int = 24) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: dict[str, list[dict[str, str]]] = {}
        self._total: dict[str, int] = {}

    def append_observations(self, agent_id: str, obs_dict: dict) -> int:
        """Record one committed observation; returns the new version.

        Stored as a natural-language summary (bounded to ``_MAX_ENTRY_CHARS``)
        so agents see their history as lived experience, not raw JSON/IDs.
        The full state snapshot would make every action prompt grow linearly
        with the timeline (Tranche 12j).
        """
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

    def version(self, agent_id: str) -> int:
        """Number of entries ever committed for this agent (0 if unseen)."""
        return self._total.get(agent_id, 0)

    def render_history_summary(self, agent_id: str) -> str:
        """Deterministic prompt-facing text of this agent's recent entries.

        Includes ONLY ``agent_id``'s own entries (privacy guard): an
        observation stored under another agent can never appear here.
        Rendered as natural memory, not a versioned log with IDs.
        """
        entries = self._entries.get(agent_id)
        if not entries:
            return "You have no memories yet."
        lines = ["What you remember:"]
        start = max(0, len(entries) - self._max_entries)
        for entry in entries[start:]:
            kind_label = "you noticed" if entry["kind"] == "observation" else "you reflected"
            lines.append(f"- {kind_label}: {entry['text']}")
        dropped = self._total[agent_id] - len(entries)
        if dropped > 0:
            lines.append(f"(you don't clearly remember {dropped} earlier moment{'s' if dropped > 1 else ''})")
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
