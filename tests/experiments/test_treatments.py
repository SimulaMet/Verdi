"""Unit tests for record-only deterministic treatments."""
from __future__ import annotations

from dataclasses import replace

import pytest
from tests.world.helpers import initial_world

from verdi_agents.experiments.treatments import (
    TreatmentOutcome,
    placebo_treatment,
    rights_restoration_treatment,
)
from verdi_agents.schemas import ProposedAction, ValidationResult
from verdi_agents.world.branch import BranchSpec, branch_from_snapshot
from verdi_agents.world.canonical import canonical_sha256
from verdi_agents.world.log import EventLog
from verdi_agents.world.reducer import WorldReducer
from verdi_agents.world.replay import TapeStep


class _Spec:
    """Minimal stand-in so treatments stay testable without a real BranchSpec."""


def _action_step(actor: str, task: str, kind: str = "harvest") -> TapeStep:
    # "harvest" is a record-only kind (not in MUTATING_KINDS): accepted
    # steps advance bookkeeping without needing resource arguments, so the
    # synthetic fold below is valid against the deterministic reducer.
    action = ProposedAction(
        actor_id=actor, task_id=task, action_kind=kind, arguments={"units": 1}
    )
    return TapeStep(action=action, validation=ValidationResult(accepted=True))


def _governance_step(kind: str, **extra) -> TapeStep:
    governance = {"kind": kind, "actor": "governance", "tick": 1, "decision": "accepted"}
    governance.update(extra)
    action = ProposedAction(
        actor_id="governance",
        task_id=f"governance-{kind}-0",
        action_kind="governance",
        arguments={},
    )
    return TapeStep(
        action=action, validation=ValidationResult(accepted=True),
        kind="governance", governance=governance,
    )


def _base_tape() -> list[TapeStep]:
    return [
        _action_step("a1", "task-1"),
        _governance_step("sanction_imposed", sanction_id="san-1"),
        _governance_step("appeal_filed", appeal_id="ap-1"),
        _governance_step("appeal_dismissed", appeal_id="ap-1"),
        _action_step("a2", "task-2"),
    ]


def _restoration_tape() -> list[TapeStep]:
    return _base_tape() + [_governance_step("rights_restored", sanction_id="san-1")]


def _fold(tape: list[TapeStep]) -> tuple[bytes, str]:
    """Fold a tape from the same sealed origin; return (jsonl bytes, state hash)."""
    snapshot = initial_world()
    log = EventLog()
    spec = BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(snapshot),
        origin_event_count=0,
        origin_tick=0,
        branch_id="treatment-test",
        treatment_seed=0,
    )
    result = branch_from_snapshot(snapshot, log, tape, spec)
    return result.log.to_jsonl_bytes(), result.branch_state_hash


def _tape_fingerprint(tape: list[TapeStep]) -> str:
    return canonical_sha256(
        [
            {"action": s.action.model_dump(mode="json"),
             "validation": s.validation.model_dump(mode="json"),
             "kind": s.kind,
             "governance": s.governance}
            for s in tape
        ]
    )


# --------------------------------------------------------------------------- #
# placebo
# --------------------------------------------------------------------------- #
def test_placebo_is_true_noop_with_byte_identical_fold():
    tape = _base_tape()
    fingerprint_before = _tape_fingerprint(tape)
    outcome = placebo_treatment(_Spec(), tape, seed=7)
    assert isinstance(outcome, TreatmentOutcome)
    assert outcome.applied is False
    # deep copy: equal, but structurally distinct objects
    assert outcome.tape == tape
    assert outcome.tape is not tape
    assert outcome.tape[0].action is not tape[0].action
    assert outcome.tape[0].governance is None
    assert outcome.tape[1].governance is not tape[1].governance
    # input untouched
    assert _tape_fingerprint(tape) == fingerprint_before
    # byte-identical after EventLog folding (true placebo, not just ==)
    placebolog_bytes, placebo_hash = _fold(outcome.tape)
    control_bytes, control_hash = _fold(tape)
    assert placebolog_bytes == control_bytes
    assert placebo_hash == control_hash


def test_placebo_deterministic_across_seeds():
    tape = _base_tape()
    a = placebo_treatment(_Spec(), tape, seed=1)
    b = placebo_treatment(_Spec(), tape, seed=999)
    assert a.tape == b.tape
    assert _tape_fingerprint(a.tape) == _tape_fingerprint(b.tape)
    assert a.reason == b.reason


# --------------------------------------------------------------------------- #
# rights restoration
# --------------------------------------------------------------------------- #
def test_rights_restoration_reports_unapplied_when_no_restoration_in_tape():
    tape = _base_tape()  # the pilot's shape: sanction -> appeal -> dismissed
    fingerprint_before = _tape_fingerprint(tape)
    outcome = rights_restoration_treatment(_Spec(), tape, seed=0)
    assert isinstance(outcome, TreatmentOutcome)
    assert outcome.applied is False
    assert "rights_restored" in outcome.reason
    # the unmodified tape is returned as a deep copy: input never shared/mutated
    assert outcome.tape == tape
    assert outcome.tape is not tape
    assert _tape_fingerprint(tape) == fingerprint_before
    # and it folds identically to the control
    treated_bytes, treated_hash = _fold(outcome.tape)
    control_bytes, control_hash = _fold(tape)
    assert treated_bytes == control_bytes
    assert treated_hash == control_hash


def test_rights_restoration_applies_when_restoration_is_recorded():
    tape = _restoration_tape()
    fingerprint_before = _tape_fingerprint(tape)
    outcome = rights_restoration_treatment(_Spec(), tape, seed=0)
    assert outcome.applied is True
    assert "step 5" in outcome.reason  # re-recorded at the sealed position
    # re-recording is order-stable: same steps, same positions, fresh objects
    assert outcome.tape == tape
    assert outcome.tape is not tape
    assert outcome.tape[5] is not tape[5]
    assert outcome.tape[5].governance is not tape[5].governance
    assert _tape_fingerprint(tape) == fingerprint_before
    # folds to the identical sealed ledger (no new world state invented)
    treated_bytes, treated_hash = _fold(outcome.tape)
    control_bytes, control_hash = _fold(tape)
    assert treated_bytes == control_bytes
    assert treated_hash == control_hash


def test_rights_restoration_deterministic_across_seeds():
    tape = _restoration_tape()
    a = rights_restoration_treatment(_Spec(), tape, seed=3)
    b = rights_restoration_treatment(_Spec(), tape, seed=12345)
    assert a.applied == b.applied
    assert a.reason == b.reason
    assert _tape_fingerprint(a.tape) == _tape_fingerprint(b.tape)


def test_treatment_outcome_is_frozen():
    import dataclasses

    outcome = placebo_treatment(_Spec(), _base_tape(), seed=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.applied = True  # type: ignore[misc]


def test_replace_semantics_do_not_affect_treatments():
    """Guard: treatments treat steps as value objects (frozen dataclass)."""
    step = _base_tape()[0]
    copy = replace(step, kind=step.kind)
    assert copy == step
    assert copy is not step
