"""Build and validate matched treatment/placebo pairs that share one origin and task tape.

A *matched pair* couples one treatment run and one placebo run that share
the SAME sealed snapshot and the SAME remaining task tape: only the
treatment may differ.  The pair is validated on two identity fields:

* ``origin_state_hash`` -- the canonical state hash of the sealed snapshot
  at the branch point (from each run's manifest, set by
  :func:`verdi_agents.world.branch.manifest_for_branch`-style metadata), and
* ``tape_hash`` -- the sha256 of the canonical JSONL *prefix ledger* bytes
  up to the branch point (see :func:`tape_hash`).  The prefix ledger bytes
  uniquely determine the sealed origin: the chain starts at genesis, so two
  ledgers with identical bytes have identical final hashes -- which is
  exactly the "same origin, same tape" invariant that reference-run replay
  and branching (:mod:`verdi_agents.world.branch`) rely on.

Runs are carried as plain dicts (the manifest dicts produced by the
reference-run + branch pipeline, including ``branch_origin`` / ``treatment_seed``),
so this module stays schema-free and integrator-owned schemas are never
modified.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MatchedPair:
    """One validated treatment/placebo run pair from a shared origin."""

    treatment_run: dict[str, Any]
    placebo_run: dict[str, Any]
    origin_state_hash: str
    tape_hash: str


def tape_hash(log_or_path) -> str:
    """Tape hash of a sealed ledger (documented definition).

    Definition: ``tape_hash = sha256`` of the canonical JSONL prefix-ledger
    bytes -- one canonical JSON object per line (sorted keys, compact
    separators, ``ensure_ascii=False``), newline-joined with a trailing
    newline -- taken over the ledger UP TO the branch point.

    Accepts either an :class:`~verdi_agents.world.log.EventLog` (its
    ``to_jsonl_bytes()`` is used verbatim) or a path to a ``.jsonl`` ledger
    file (its raw bytes are used verbatim).

    The prefix ledger bytes uniquely determine the sealed origin (the chain
    starts at genesis), so two manifests with matching tape hashes certify
    "same origin snapshot + same remaining tape" -- exactly the invariant
    that reference-run replay and branching rely on.
    """
    if isinstance(log_or_path, (str, Path)):
        payload = Path(log_or_path).read_bytes()
    elif hasattr(log_or_path, "to_jsonl_bytes"):
        payload = log_or_path.to_jsonl_bytes()
    else:
        raise TypeError(
            "tape_hash expects an EventLog or a .jsonl path, "
            f"got {type(log_or_path)!r}"
        )
    return hashlib.sha256(payload).hexdigest()


def _identity(run: dict[str, Any], field: str) -> str:
    value = run.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"manifest is missing required identity field {field!r}: {value!r}"
        )
    return value


def make_matched_pair(
    treatment_manifest: dict[str, Any],
    placebo_manifest: dict[str, Any],
) -> MatchedPair:
    """Validate that two run manifests form a matched pair.

    Both manifests must carry ``origin_state_hash`` and ``tape_hash`` and
    must agree on both.  Anything else raises :class:`ValueError` -- a
    mismatched pair is a design bug, not a result.
    """
    if not isinstance(treatment_manifest, dict) or not isinstance(placebo_manifest, dict):
        raise ValueError("both run manifests must be dicts")
    t_origin = _identity(treatment_manifest, "origin_state_hash")
    p_origin = _identity(placebo_manifest, "origin_state_hash")
    t_tape = _identity(treatment_manifest, "tape_hash")
    p_tape = _identity(placebo_manifest, "tape_hash")
    if t_origin != p_origin:
        raise ValueError(
            "matched pair origin_state_hash mismatch: "
            f"treatment={t_origin!r} placebo={p_origin!r}"
        )
    if t_tape != p_tape:
        raise ValueError(
            "matched pair tape hash mismatch: "
            f"treatment={t_tape!r} placebo={p_tape!r}"
        )
    return MatchedPair(
        treatment_run=json.loads(json.dumps(treatment_manifest)),
        placebo_run=json.loads(json.dumps(placebo_manifest)),
        origin_state_hash=t_origin,
        tape_hash=t_tape,
    )


__all__ = ["MatchedPair", "make_matched_pair", "tape_hash"]
