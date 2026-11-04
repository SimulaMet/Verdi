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
            state = WorldState.model_validate_json(payload)
        except ValidationError as exc:
            raise SnapshotError(f"corrupt snapshot {digest!r}: {exc}") from exc
        # Content addressing is only a guarantee if it is checked on read.
        if canonical_sha256(state.model_dump(mode="json")) != digest:
            raise SnapshotError(f"snapshot {digest!r} does not match its content hash")
        return state

    def contains(self, digest: str) -> bool:
        """True if a snapshot with this digest is stored."""
        if not _is_digest(digest):
            return False
        if digest in self._memory:
            return True
        return self._dir is not None and (self._dir / f"{digest}.json").exists()

    def _find(self, digest: str) -> bytes:
        if not _is_digest(digest):
            # Digests become file names: refuse anything that could traverse.
            raise SnapshotError(f"invalid snapshot digest: {digest!r}")
        if digest in self._memory:
            return self._memory[digest]
        if self._dir is not None:
            path = self._dir / f"{digest}.json"
            if path.exists():
                payload = path.read_bytes()
                self._memory[digest] = payload
                return payload
        raise SnapshotError(f"unknown snapshot digest: {digest!r}")


def _is_digest(value: str) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )
