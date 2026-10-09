"""Counterfactual snapshot branching (P2 WS-1, frozen contract v1.0).

A *branch* forks a new ledger from a sealed point of the world: a
:class:`~verdi.schemas.WorldState` snapshot plus the hash-chained prefix
ledger up to the branch point. The counterfactual tape (the *remaining*
recorded :class:`~verdi.world.replay.TapeStep` sequence) is folded through
the same deterministic reducer the pilot uses, so the branch is a
replay-only construction: no model invocation, fully deterministic
(ADR-004, ADR-008).

Guarantees (tested in ``tests/world/test_branch.py``):
* B1: inputs (snapshot, prefix log) are never mutated;
* B2: two branches from the same inputs yield the same
  ``branch_state_hash`` and byte-identical JSONL;
* B3: a spec whose ``origin_state_hash`` / ``origin_event_count`` do not
  match the snapshot / prefix ledger raises :class:`BranchOriginError`;
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

from verdi.schemas import ProposedAction, ValidationResult, WorldEvent, WorldState
from verdi.world.log import EventLog
from verdi.world.reducer import GENESIS_HASH, WorldReducer
from verdi.world.replay import TapeStep


class BranchOriginError(RuntimeError):
    """Raised when a branch spec does not match the snapshot/ledger origin."""


@dataclass(frozen=True)
class BranchSpec:
    """A counterfactual branch: sealed snapshot + remaining tape + treatment."""

    origin_state_hash: str          # canonical_state_hash of the snapshot
    origin_event_count: int         # ledger length at the branch point
    origin_tick: int                # LIFECYCLE tick at the branch point
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
    engine has no dependency on ``verdi.pilot``. ``event_id`` comes from
    ``before.event_cursor`` (same as the reducer); ``prev_hash`` is left
    to the caller to match the log position.
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

    Returns
    -------
    BranchResult
        Fresh full ledger (prefix + fold), final state and its
        canonical state hash.

    Raises
    ------
    BranchOriginError
        If ``spec.origin_state_hash`` != canonical hash of ``snapshot``
        or ``spec.origin_event_count`` != ``len(log)`` (B3).
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
