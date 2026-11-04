"""Append-only, hash-chained event ledger with JSONL persistence.

Every attempted action is recorded here -- accepted AND rejected
(``decision='rejected'`` / ``kind='action_rejected'``), keeping event
truth separate from world-state truth (ARCHITECTURE.md non-negotiable
invariant). Each event is chained to its predecessor via ``prev_hash``;
``event_hash`` is the sha256 of the canonical serialization of every
field except ``event_hash`` itself (including ``prev_hash``,
``before_hash``, ``after_hash``, ``payload`` and ``decision``).

:class:`EventLog` is the single writer of ``event_hash``: the reducer
builds events with an empty ``event_hash`` and the log seals them on
append, so the chain is computed in exactly one place.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from verdi_agents.schemas import WorldEvent
from verdi_agents.world.canonical import canonical_json, canonical_sha256
from verdi_agents.world.reducer import GENESIS_HASH


class LogIntegrityError(RuntimeError):
    """Raised when the hash chain or event ordering is corrupt."""


class EventLog:
    """Append-only in-memory ledger with optional JSONL persistence.

    Parameters
    ----------
    path:
        If given, each appended event is also appended to this JSONL
        file (one canonical JSON object per line).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path: Path | None = Path(path) if path is not None else None
        self._events: list[WorldEvent] = []
        self._last_hash: str = GENESIS_HASH
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(exist_ok=True)

    # ------------------------------------------------------------------ #
    # read side
    # ------------------------------------------------------------------ #
    @property
    def events(self) -> tuple[WorldEvent, ...]:
        """All sealed events in append order."""
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[WorldEvent]:
        return iter(self._events)

    def last_hash(self) -> str:
        """``event_hash`` of the last event, or the genesis marker."""
        return self._last_hash

    # ------------------------------------------------------------------ #
    # write side (single writer of event_hash)
    # ------------------------------------------------------------------ #
    def seal(self, event: WorldEvent) -> WorldEvent:
        """Return ``event`` with ``event_hash`` computed (pure)."""
        data = event.model_dump(mode="json")
        data.pop("event_hash", None)
        return event.model_copy(update={"event_hash": canonical_sha256(data)})

    def append(self, event: WorldEvent) -> WorldEvent:
        """Append ``event`` (expected unsealed) and persist; returns sealed.

        Raises :class:`LogIntegrityError` if the event is out of order
        (prev_hash mismatch or event_id not equal to current length).
        """
        expected_id = len(self._events)
        if event.event_id != expected_id:
            raise LogIntegrityError(
                f"event ordering violation: expected event_id {expected_id}, got {event.event_id}"
            )
        if event.prev_hash != self._last_hash:
            raise LogIntegrityError(
                f"event ordering violation: prev_hash {event.prev_hash!r} != last {self._last_hash!r}"
            )
        sealed = self.seal(event)
        if event.event_hash and event.event_hash != sealed.event_hash:
            raise LogIntegrityError(
                f"hash mismatch on append: event {expected_id} not sealed for its content"
            )
        self._events.append(sealed)
        self._last_hash = sealed.event_hash
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json(sealed.model_dump(mode="json")).decode("utf-8"))
                fh.write("\n")
        return sealed

    # ------------------------------------------------------------------ #
    # integrity
    # ------------------------------------------------------------------ #
    def verify(self) -> None:
        """Re-walk the chain; raise :class:`LogIntegrityError` on any
        hash mismatch, out-of-order id, or prev_hash breakage."""
        last = GENESIS_HASH
        for index, event in enumerate(self._events):
            if event.event_id != index:
                raise LogIntegrityError(
                    f"ordering violation at position {index}: event_id={event.event_id}"
                )
            if event.prev_hash != last:
                raise LogIntegrityError(
                    f"chain break at event {index}: prev_hash {event.prev_hash!r} != {last!r}"
                )
            expected = canonical_sha256(
                {k: v for k, v in event.model_dump(mode="json").items() if k != "event_hash"}
            )
            if event.event_hash != expected:
                raise LogIntegrityError(f"hash mismatch at event {index}: tampered or corrupted")
            last = event.event_hash

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def to_jsonl_bytes(self) -> bytes:
        """Full ledger as canonical JSONL bytes (one event per line)."""
        lines = [canonical_json(e.model_dump(mode="json")) for e in self._events]
        return b"\n".join(lines) + (b"\n" if lines else b"")

    @classmethod
    def load_jsonl(cls, path: str | Path) -> EventLog:
        """Load a ledger from a JSONL file, verifying hashes and order.

        Stored ``event_hash`` values are checked against the
        recomputation (no silent re-sealing), so a tampered file raises
        :class:`LogIntegrityError`.
        """
        log = cls()
        for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = WorldEvent.model_validate(json.loads(line))
            expected = canonical_sha256(
                {k: v for k, v in event.model_dump(mode="json").items() if k != "event_hash"}
            )
            if event.event_hash != expected:
                raise LogIntegrityError(f"hash mismatch at line {lineno}: tampered or corrupted")
            if event.event_id != len(log._events) or event.prev_hash != log._last_hash:
                raise LogIntegrityError(f"ordering violation at line {lineno}")
            log._events.append(event)
            log._last_hash = event.event_hash
        return log
