"""Counterfactual snapshot branching (P2 WS-1, frozen contract v1.0).

A *branch* forks a new ledger from a sealed point of the world: a
:class:`~verdi_agents.schemas.WorldState` snapshot plus the hash-chained prefix
ledger up to the branch point. The counterfactual tape (the *remaining*
recorded :class:`~verdi_agents.world.replay.TapeStep` sequence) is folded through
the same deterministic reducer the pilot uses, so the branch is a
replay-only construction: no model invocation, fully deterministic
(ADR-004, ADR-008).

Guarantees (tested in ``tests/world/test_branch.py``):
* B1: inputs (snapshot, prefix log) are never mutated;
* B2: two branches from the same inputs yield the same
  ``branch_state_hash`` and byte-identical JSONL;
* B3: the full origin contract — (a) ``origin_state_hash`` matches the
  snapshot, (b) ``origin_event_count`` matches the prefix ledger length,
  and (c) the snapshot equals the state the prefix ledger terminates in
  (``canonical_state_hash(snapshot) == log.events[-1].after_hash`` for a
  non-empty ledger) — any violation raises :class:`BranchOriginError`;
* B4: the branch ledger re-verifies and its first folded event chains
  onto the prefix ledger's ``last_hash()``;
* B5: :func:`manifest_for_branch` carries ``branch_id`` /
  ``treatment_seed`` into an ``ExperimentManifest``-compatible dict.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verdi_agents.schemas import ProposedAction, ValidationResult, WorldEvent, WorldState
from verdi_agents.world.log import EventLog
from verdi_agents.world.reducer import GENESIS_HASH, WorldReducer
from verdi_agents.world.replay import TapeStep


class BranchOriginError(RuntimeError):
    """Raised when a branch spec does not match the snapshot/ledger origin."""


@dataclass(frozen=True)
class BranchSpec:
    """A counterfactual branch: sealed snapshot + remaining tape + treatment."""

    origin_state_hash: str          # canonical_state_hash of the snapshot
    origin_event_count: int         # ledger length at the branch point
    # Caller-supplied LIFECYCLE-tick metadata at the branch point. Per
    # ADR-008 the reducer tick is a pure transition counter, not the
    # pilot's lifecycle tick, so this is identity/provenance metadata
    # only — branch_from_snapshot performs no cross-check on it.
    origin_tick: int
    branch_id: str                  # e.g. "counterfactual-v2"
    treatment_seed: int = 0


@dataclass(frozen=True)
class BranchResult:
    """Outcome of one counterfactual branch."""

    spec: BranchSpec
    log: EventLog                   # full branch ledger (prefix + fold)
    final_state: WorldState
    branch_state_hash: str          # == canonical_state_hash(final_state)


# --------------------------------------------------------------------------- #
# governance event construction (ported from the pilot's replay path)
# --------------------------------------------------------------------------- #
def _governance_event(
    before: WorldState,
    after: WorldState,
    action: ProposedAction,
    validation: ValidationResult,
    gen_event: dict[str, Any],
) -> WorldEvent:
    """Unsealed governance ledger event, byte-identical to the action-path
    construction except for ``kind``/``payload``.

    Local copy of the pilot's ``build_governance_event`` so the world
    engine has no dependency on ``verdi_agents.reference_run``. Keep in sync with its
    counterpart in ``verdi_agents.reference_run.driver.build_governance_event``; the
    pilot-branch byte-identity test
    (``tests/world/test_branch.py::test_branch_replay_matches_live_ledger``)
    guards against drift between the two builders. ``event_id`` comes
    from ``before.event_cursor`` (same as the reducer); ``prev_hash`` is
    left to the caller to match the log position.
    """
    event_id = before.event_cursor
    accepted = validation.accepted
    payload: dict[str, Any] = {
        "task_id": action.task_id,
        "action_kind": action.action_kind,
        "arguments": action.arguments,
    }
    if not accepted:
        payload.update(
            {
                "blocked_by": validation.blocked_by,
                "attempted_violation_codes": validation.attempted_violation_codes,
                "details": validation.details,
            }
        )
    return WorldEvent(
        event_id=event_id,
        tick=after.tick,
        cause_event_id=event_id - 1 if event_id > 0 else None,
        actor_id=gen_event.get("actor"),
        kind=gen_event["kind"],
        payload={
            k: v
            for k, v in gen_event.items()
            if k not in ("kind", "_ledger_event_id")
        },
        before_hash=WorldReducer.canonical_state_hash(before),
        after_hash=(
            WorldReducer.canonical_state_hash(after) if accepted else None
        ),
        decision="accepted" if accepted else "rejected",
        prev_hash=GENESIS_HASH,
        event_hash="",
    )


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def branch_from_snapshot(
    snapshot: WorldState,
    log: EventLog,
    remaining_tape: list[TapeStep],
    spec: BranchSpec,
) -> BranchResult:
    """Fold ``remaining_tape`` from a sealed snapshot into a branch ledger.

    The fold uses the exact pilot replay semantics (governance steps take
    their ``kind``/``payload`` from ``step.governance``) and CONTINUES the
    hash chain: the first folded event has ``prev_hash = log.last_hash()``
    and ``event_id = len(log) + i``.

    Parameters
    ----------
    snapshot:
        Sealed world state at the branch point. Never mutated.
    log:
        Sealed prefix ledger up to the branch point. Never mutated.
    remaining_tape:
        Recorded :class:`TapeStep` sequence in ledger order
        (``kind="action"`` or ``kind="governance"``).
    spec:
        Branch origin + treatment identity (see :class:`BranchSpec`).
        ``spec.origin_tick`` is caller-supplied LIFECYCLE-tick metadata
        only (per ADR-008 the reducer tick is a transition counter, so
        no hard cross-check against the reducer tick is possible); it is
        carried through to the result but never validated here.

    Returns
    -------
    BranchResult
        Fresh full ledger (prefix + fold), final state and its
        canonical state hash.

    Raises
    ------
    BranchOriginError
        If the origin contract (B3) is violated:
        (a) ``spec.origin_state_hash`` != canonical hash of ``snapshot``,
        or (b) ``spec.origin_event_count`` != ``len(log)``, or
        (c) the snapshot is not the terminal state of the prefix ledger
        — for a non-empty ``log``, ``canonical_state_hash(snapshot)``
        must equal ``log.events[-1].after_hash``.
    """
    # B3: the spec must name the exact origin we are forking from.
    actual_hash = WorldReducer.canonical_state_hash(snapshot)
    if spec.origin_state_hash != actual_hash:
        raise BranchOriginError(
            f"origin_state_hash mismatch: spec={spec.origin_state_hash!r} "
            f"snapshot={actual_hash!r}"
        )
    if spec.origin_event_count != len(log):
        raise BranchOriginError(
            f"origin_event_count mismatch: spec={spec.origin_event_count} "
            f"ledger={len(log)}"
        )
    # B3 (c): the snapshot must be the state the prefix ledger
    # terminates in. Without this, a spec can match a snapshot that is
    # merely consistent with the ledger length but divergent from the
    # ledger's terminal state (an inconsistent (snapshot, log) pair).
    # A rejected event leaves state unchanged and carries no after_hash, so
    # its terminal state is its before_hash.
    terminal = (log.events[-1].after_hash or log.events[-1].before_hash) if log.events else None
    if log.events and actual_hash != terminal:
        raise BranchOriginError(
            "snapshot is not the terminal state of the prefix ledger: "
            f"snapshot={actual_hash!r} ledger_terminal={terminal!r}"
        )

    # Rebuild the prefix in a fresh ledger from the sealed JSONL bytes.
    # load_jsonl verifies hashes/order and performs no re-sealing, so the
    # copy is byte-identical to the source prefix.
    with tempfile.TemporaryDirectory(prefix="verdi-branch-") as tmp:
        prefix_path = Path(tmp) / "prefix.jsonl"
        prefix_path.write_bytes(log.to_jsonl_bytes())
        branch_log = EventLog.load_jsonl(prefix_path)

    reducer = WorldReducer()
    state = snapshot
    prev_hash = branch_log.last_hash()  # == log.last_hash()
    for step in remaining_tape:
        before = state
        after = reducer.apply(state, step.action, step.validation)
        if step.kind == "governance":
            event = _governance_event(
                before, after, step.action, step.validation, step.governance
            )
        else:
            event = reducer.build_event(
                before, after, step.action, step.validation, prev_hash
            )
        event = event.model_copy(update={"prev_hash": prev_hash})
        sealed = branch_log.append(event)
        state = after
        prev_hash = sealed.event_hash

    branch_log.verify()  # B4
    return BranchResult(
        spec=spec,
        log=branch_log,
        final_state=state,
        branch_state_hash=WorldReducer.canonical_state_hash(state),
    )


def manifest_for_branch(spec: BranchSpec, base_manifest: dict[str, Any]) -> dict[str, Any]:
    """Return ``base_manifest`` with branch identity applied (B5).

    Schema-free helper: the pilot owns :class:`ExperimentManifest`
    construction; this only guarantees ``branch_origin`` /
    ``treatment_seed`` carry through. The input dict is not mutated.
    """
    manifest = dict(base_manifest)
    manifest["branch_origin"] = spec.branch_id
    manifest["treatment_seed"] = spec.treatment_seed
    return manifest


__all__ = [
    "BranchOriginError",
    "BranchResult",
    "BranchSpec",
    "branch_from_snapshot",
    "manifest_for_branch",
]
