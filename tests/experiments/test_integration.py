"""P2 WS-2 integration test: pilot -> branch point -> treated vs placebo
branch arms -> matched pair.

Runs the recorded-output pilot (no LLM), forks both arms from the SAME
sealed snapshot at the pilot's governance branch point, and validates:

* the placebo branch reproduces the pilot's final state hash exactly
  (the placebo is truly inert);
* the rights-restoration treatment's honest ``applied`` flag on this tape
  (the pilot appeal is DISMISSED, so no sealed ``rights_restored`` event
  exists to re-record -> ``applied=False``, tape returned unmodified);
* a :class:`~verdi_agents.experiments.MatchedPair` built from both arms'
  manifests (``branch_origin`` / ``treatment_seed`` carried through via
  :func:`verdi_agents.world.branch.manifest_for_branch`) validates because both
  arms share ``origin_state_hash`` and the tape hash.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from verdi_agents.experiments.pairs import MatchedPair, make_matched_pair, tape_hash
from verdi_agents.experiments.treatments import (
    TreatmentOutcome,
    placebo_treatment,
    rights_restoration_treatment,
)
from verdi_agents.reference_run.driver import PilotDriver
from verdi_agents.world.branch import BranchSpec, branch_from_snapshot, manifest_for_branch
from verdi_agents.world.reducer import WorldReducer
from verdi_agents.world.replay import replay_in_memory

REPO_ROOT = Path(__file__).resolve().parents[2]


def _branch_point():
    """Replay the pilot tape up to the first governance step and return
    (pilot, snapshot, prefix_log, remaining_tape)."""
    pilot = PilotDriver(REPO_ROOT).run()
    tape = pilot.full_tape_steps
    branch_index = next(i for i, step in enumerate(tape) if step.kind == "governance")
    snapshot, _, prefix_log = replay_in_memory(
        pilot.loaded.t0_state, tape[:branch_index]
    )
    return pilot, snapshot, prefix_log, tape[branch_index:]


def test_pilot_placebo_branch_reproduces_final_hash_and_pair_validates():
    pilot, snapshot, prefix_log, remaining = _branch_point()

    # Both arms fork from the SAME sealed branch point; only the treatment
    # and its seed differ (PREREGISTRATION matched-pair design).
    spec = BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(snapshot),
        origin_event_count=len(prefix_log),
        origin_tick=snapshot.tick,
        branch_id="counterfactual-v2",
        treatment_seed=0,
    )
    treatment_spec = BranchSpec(
        origin_state_hash=spec.origin_state_hash,
        origin_event_count=spec.origin_event_count,
        origin_tick=spec.origin_tick,
        branch_id=spec.branch_id,
        treatment_seed=1,
    )

    # -- placebo arm (deep-copied, structurally identical tape) ------------- #
    placebo_outcome = placebo_treatment(spec, remaining, seed=spec.treatment_seed)
    assert isinstance(placebo_outcome, TreatmentOutcome)
    assert placebo_outcome.applied is False
    placebo_result = branch_from_snapshot(
        snapshot, prefix_log, placebo_outcome.tape, spec
    )

    # (d) the placebo branch must reproduce the pilot final hash exactly.
    assert placebo_result.branch_state_hash == pilot.final_state.state_hash
    # byte-identical full ledger as well (true placebo, not just equal hash)
    assert placebo_result.log.to_jsonl_bytes() == pilot.log.to_jsonl_bytes()

    # -- rights-restoration treatment arm ----------------------------------- #
    treatment_outcome = rights_restoration_treatment(
        treatment_spec, remaining, seed=treatment_spec.treatment_seed
    )
    assert isinstance(treatment_outcome, TreatmentOutcome)
    # Honest: the pilot tape contains sanction -> appeal_filed ->
    # appeal_dismissed and NO rights_restored event, so there is no sealed
    # restoration outcome to re-record. The treatment must NOT invent one.
    assert treatment_outcome.applied is False
    assert "rights_restored" in treatment_outcome.reason
    treatment_result = branch_from_snapshot(
        snapshot, prefix_log, treatment_outcome.tape, treatment_spec
    )

    # With applied=False the treatment arm's tape is structurally identical
    # to the placebo arm's tape, so it folds to the same final state hash.
    assert treatment_result.branch_state_hash == pilot.final_state.state_hash

    # -- matched pair from both arms' manifests ------------------------------ #
    base_manifest = {
        "schema_version": 1,
        "experiment_id": pilot.manifest.experiment_id,
        "world_id": pilot.manifest.world_id,
        "run_seed": pilot.manifest.run_seed,
        "origin_state_hash": spec.origin_state_hash,
        # tape hash = sha256 of the canonical JSONL prefix-ledger bytes up
        # to the branch point (documented definition, see pairs.tape_hash).
        "tape_hash": tape_hash(prefix_log),
    }
    treatment_manifest = manifest_for_branch(treatment_result.spec, base_manifest)
    placebo_manifest = manifest_for_branch(placebo_result.spec, base_manifest)

    # branch identity is carried into the manifests (B5)
    assert treatment_manifest["branch_origin"] == treatment_spec.branch_id
    assert treatment_manifest["treatment_seed"] == treatment_spec.treatment_seed
    assert placebo_manifest["branch_origin"] == spec.branch_id
    assert placebo_manifest["treatment_seed"] == spec.treatment_seed
    # both arms share the same sealed origin and prefix ledger bytes
    assert treatment_manifest["origin_state_hash"] == placebo_manifest["origin_state_hash"]
    assert treatment_manifest["tape_hash"] == placebo_manifest["tape_hash"]
    expected_tape_hash = hashlib.sha256(prefix_log.to_jsonl_bytes()).hexdigest()
    assert treatment_manifest["tape_hash"] == expected_tape_hash

    pair = make_matched_pair(treatment_manifest, placebo_manifest)
    assert isinstance(pair, MatchedPair)
    assert pair.origin_state_hash == spec.origin_state_hash
    assert pair.tape_hash == expected_tape_hash
    assert pair.treatment_run["branch_origin"] == treatment_spec.branch_id
    assert pair.placebo_run["branch_origin"] == spec.branch_id
