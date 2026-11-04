"""Record-only, deterministic recovery treatments (P2 WS-2).

A *treatment* is a pure function over a recorded
:class:`~verdi_agents.world.replay.TapeStep` sequence.  The rules that keep the
P2 design honest:

* **Record-only** -- a treatment may only re-order or re-record steps that
  are already present in the input tape (provenance of the sealed ledger).
  It never invents new events, never mutates world state, and never
  invokes a model (ADR-004, ADR-008; PREREGISTRATION.md "What is
  explicitly NOT claimed").
* **Deterministic** -- given the same tape and ``seed`` the output tape is
  byte-stable; two invocations produce structurally identical steps whose
  canonical serializations hash identically, so folding the output through
  the same reducer/ledger pipeline yields the same hashes as folding the
  input when the treatment is inert.
* **No input mutation** -- inputs are never modified; output steps are
  fresh objects (deep copies of the recorded steps).

All treatments return a :class:`TreatmentOutcome` so the caller has a
uniform, self-describing result: the (possibly unchanged) output tape,
whether the treatment actually changed anything (``applied``) and a
human-readable ``reason`` that is honest about what happened to *this*
tape.  When a genuinely new event would be required, the treatment does
NOT invent it: it returns the input tape with ``applied=False``.
"""
from __future__ import annotations

from dataclasses import dataclass

from verdi_agents.world.replay import TapeStep

__all__ = ["TreatmentOutcome", "placebo_treatment", "rights_restoration_treatment"]

#: Governance event kind recorded when a sanction's rights are restored
#: (appeal upheld or duration expiry).  Sourced from the frozen governance
#: engine constants, but kept as a local literal so this module has no
#: dependency on the governance package.
_RIGHTS_RESTORED = "rights_restored"


@dataclass(frozen=True)
class TreatmentOutcome:
    """Uniform, self-describing result of a treatment application."""

    tape: list[TapeStep]
    applied: bool
    reason: str


def _deep_copy_tape(tape: list[TapeStep]) -> list[TapeStep]:
    """Return a fresh, structurally identical tape (deep copy).

    Every step is a new :class:`TapeStep` whose pydantic models are deep
    copies, so the output is byte-identical under canonical serialization
    while sharing no mutable structure with the input.
    """
    return [
        TapeStep(
            action=step.action.model_copy(deep=True),
            validation=step.validation.model_copy(deep=True),
            kind=step.kind,
            governance=(dict(step.governance) if step.governance is not None else None),
        )
        for step in tape
    ]


def placebo_treatment(
    spec,
    tape: list[TapeStep],
    *,
    seed: int,
) -> TreatmentOutcome:
    """No-effect control arm.

    Returns a deep copy of ``tape``: structurally re-serialized identically
    (byte-identical canonical hashes, and byte-identical ledger JSONL after
    EventLog folding), so a placebo branch from the same snapshot reproduces
    the pilot final hash exactly.  This is a true placebo, not merely an
    equal list -- the input tape is never shared with or mutated by the
    result.

    Record-only: no step is added, removed, reordered or modified.
    Deterministic: ``seed`` does not change the output (documented here
    for the uniform ``seed`` contract across treatments).
    """
    del spec, seed  # the placebo is inert by definition
    return TreatmentOutcome(
        tape=_deep_copy_tape(tape),
        applied=False,
        reason="placebo: tape deep-copied, structurally identical, no steps changed",
    )


def rights_restoration_treatment(
    spec,
    tape: list[TapeStep],
    *,
    seed: int,
) -> TreatmentOutcome:
    """Re-recording of an already-sealed rights-restoration outcome.

    What this treatment CAN honestly do on a recorded tape: if the tape's
    provenance already contains a governance step whose recorded event kind
    is ``rights_restored`` (the sealed outcome of an appeal adjudication or
    a duration expiry), the treatment re-applies that outcome at the same
    ledger position and reports ``applied=True``.  No NEW world state is
    invented: the step is a re-recording of the same sealed event
    (same kind, actor, payload), and the only counterfactual value it can
    carry is "restoration timing under the treatment" for reorderings that
    are themselves already part of the recorded tape.

    What this treatment CANNOT do: the pilot_v1 tape contains
    ``sanction_imposed`` -> ``appeal_filed`` -> ``appeal_dismissed`` with
    no ``rights_restored`` step (the appeal was dismissed), so there is no
    sealed restoration outcome to re-record.  In that case -- and for any
    tape lacking a ``rights_restored`` step -- the treatment returns the
    UNMODIFIED tape (deep-copied, so the input is still never shared or
    mutated) with ``applied=False`` and an honest ``reason``.

    Record-only: only steps already present in ``tape`` are re-recorded;
    nothing is invented.  Deterministic: same tape + same ``seed`` ->
    structurally identical output.  Never mutates the input tape.
    """
    del spec, seed  # the re-recording is position- and order-stable
    restored_indices = [
        i
        for i, step in enumerate(tape)
        if step.kind == "governance"
        and step.governance is not None
        and step.governance.get("kind") == _RIGHTS_RESTORED
    ]
    output = _deep_copy_tape(tape)
    if restored_indices:
        first = restored_indices[0]
        return TreatmentOutcome(
            tape=output,
            applied=True,
            reason=(
                f"re-recorded sealed rights_restored outcome at step {first} "
                f"({len(restored_indices)} restoration step(s) in tape provenance); "
                "no new world state invented"
            ),
        )
    return TreatmentOutcome(
        tape=output,
        applied=False,
        reason=(
            "no rights_restored event exists in this tape's provenance (the "
            "pilot appeal was dismissed, not upheld); a genuinely new "
            "restoration event must not be invented, so the tape is returned "
            "unmodified"
        ),
    )
