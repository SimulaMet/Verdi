"""Deterministic replay of a recorded-output tape.

``replay`` folds recorded (ProposedAction, ValidationResult) pairs --
the *tape* -- through the deterministic reducer and the hash-chained
event log. It performs NO model invocation: everything is read from the
recorded outputs, so two replays over the same tape yield an identical
final state hash and an identical event list.
"""
from __future__ import annotations

from dataclasses import dataclass

from verdi_agents.schemas import ProposedAction, ValidationResult, WorldEvent, WorldState
from verdi_agents.world.log import EventLog
from verdi_agents.world.reducer import WorldReducer


@dataclass(frozen=True)
class TapeStep:
    """One recorded step: the proposed action plus its deterministic
    validation outcome.

    ``kind`` defaults to the action semantics; steps produced by the
    governance engine carry ``kind="governance"`` so the reducer only
    applies the rule-version bookkeeping (no resource mutation).

    ``kind="rights_change"`` carries a deterministic world-state
    ``under_sanction`` flag flip (``rights``: ``{"agent_id": ..., "value":
    bool}``) that takes no action, advances no ledger event, and does not
    advance ``tick``/``event_cursor`` — so a live run and a replay both
    apply it at the same recorded position and the state hash still
    matches.
    """

    action: ProposedAction
    validation: ValidationResult
    kind: str = "action"
    governance: dict | None = None
    rights: dict | None = None
    # Deterministic world-resource shock ({resource, delta}) applied at this
    # position; takes no action, advances no ledger event or tick — so a live
    # run and its replay both apply it identically and the state hash matches.
    shock: dict | None = None
    # Constrained economy: deterministic production cost deduction
    # ({actor_id, resource, amount}) applied at this position before the
    # produce commit; state-only, like rights_change / world_shock.
    production: dict | None = None
    # End-of-tick consumption payload ({tick_consumed, deficit_agents})
    # recorded so replay applies the same deterministic deduction and rebuilds
    # the identical ledger event.
    consumption: dict | None = None


def replay(
    log: EventLog,
    initial_state: WorldState,
    tape: list[TapeStep],
) -> tuple[WorldState, list[WorldEvent]]:
    """Fold ``tape`` through the reducer, appending to ``log``.

    Parameters
    ----------
    log:
        Target ledger. For a clean replay pass an empty :class:`EventLog`
        (optionally persisted to a JSONL file). Pre-populated logs are
        supported: replay continues the chain from ``log.last_hash()``
        and ``log.events`` length.
    initial_state:
        State to start from (e.g. loaded from a snapshot).
    tape:
        Recorded (action, validation) pairs in execution order.

    Returns
    -------
    (final_state, events)
        ``final_state`` is the frozen WorldState after the last step;
        ``events`` is the list of events APPENDED by this replay.

    Raises
    ------
    InvariantViolationError
        If a recorded accepted action cannot be applied (should not
        happen for a tape produced by the same engine; a tape recorded
        from a different engine version is a contract violation).
    """
    reducer = WorldReducer()
    state = initial_state
    prev_hash = log.last_hash()
    appended: list[WorldEvent] = []
    for step in tape:
        before = state
        after = reducer.apply(state, step.action, step.validation)
        event = reducer.build_event(before, after, step.action, step.validation, prev_hash)
        sealed = log.append(event)
        appended.append(sealed)
        state = after
        prev_hash = sealed.event_hash
    return state, appended


def replay_in_memory(
    initial_state: WorldState,
    tape: list[TapeStep],
) -> tuple[WorldState, list[WorldEvent], EventLog]:
    """Convenience wrapper: fresh in-memory log, returns log too."""
    log = EventLog()
    state, events = replay(log, initial_state, tape)
    return state, events, log


__all__ = ["TapeStep", "replay", "replay_in_memory"]
