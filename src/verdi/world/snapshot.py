"""Content-addressed, immutable world snapshots.

A snapshot is the canonical JSON encoding of a :class:`WorldState`,
stored under its sha256 digest. Loading returns a fully materialized,
frozen :class:`WorldState`, so snapshots replay without any model
invocation (RESEARCH_SPEC).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from verdi.schemas import WorldState
from verdi.world.canonical import canonical_json, canonical_sha256


class SnapshotError(RuntimeError):
    """Raised for unknown, corrupt, or non-UTF8 snapshot payloads."""


class SnapshotStore:
    """Content-addressed snapshot storage.

    Parameters
    ----------
    root:
        Directory for the ``snapshots/`` folder. May be ``None`` for a
        pure in-memory store.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._memory: dict[str, bytes] = {}
        self._dir: Path | None = None
        if root is not None:
            self._dir = Path(root) / "snapshots"
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def save(self, world_state: WorldState) -> str:
        """Store ``world_state``; returns its content hash (the address)."""
        payload = canonical_json(world_state.model_dump(mode="json"))
        digest = canonical_sha256(world_state.model_dump(mode="json"))
        self._memory[digest] = payload
        if self._dir is not None:
            (self._dir / f"{digest}.json").write_bytes(payload)
        return digest

    def load(self, digest: str) -> WorldState:
        """Materialize the snapshot stored under ``digest``."""
        payload = self._find(digest)
        try:
            return WorldState.model_validate_json(payload)
        except ValidationError as exc:
            raise SnapshotError(f"corrupt snapshot {digest!r}: {exc}") from exc

    def contains(self, digest: str) -> bool:
        """True if a snapshot with this digest is stored."""
        if digest in self._memory:
            return True
        return self._dir is not None and (self._dir / f"{digest}.json").exists()

    def _find(self, digest: str) -> bytes:
        if digest in self._memory:
            return self._memory[digest]
        if self._dir is not None:
            path = self._dir / f"{digest}.json"
            if path.exists():
                payload = path.read_bytes()
                self._memory[digest] = payload
                return payload
        raise SnapshotError(f"unknown snapshot digest: {digest!r}")
