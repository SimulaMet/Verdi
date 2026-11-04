"""Deterministic, event-sourced world engine (Worker A, P1-A).

Public API
----------
* :class:`WorldReducer` -- pure transition function (the only thing that
  mutates world state), plus :func:`canonical_state_hash`.
* :class:`EventLog` -- append-only, hash-chained, JSONL-persisted ledger
  that records every attempt (accepted and rejected).
* :class:`SnapshotStore` -- content-addressed immutable snapshots,
  replayable without any model invocation.
* :func:`replay` / :func:`replay_in_memory` -- deterministic fold of a
  recorded-output tape through reducer + log.
"""
from __future__ import annotations

from verdi_agents.world.canonical import canonical_json, canonical_sha256
from verdi_agents.world.log import EventLog, LogIntegrityError
from verdi_agents.world.reducer import (
    GENESIS_HASH,
    InvariantViolationError,
    WorldReducer,
    canonical_state_hash,
)
from verdi_agents.world.replay import TapeStep, replay, replay_in_memory
from verdi_agents.world.snapshot import SnapshotError, SnapshotStore

__all__ = [
    "EventLog",
    "LogIntegrityError",
    "WorldReducer",
    "InvariantViolationError",
    "GENESIS_HASH",
    "canonical_state_hash",
    "canonical_json",
    "canonical_sha256",
    "SnapshotStore",
    "SnapshotError",
    "TapeStep",
    "replay",
    "replay_in_memory",
]
