"""RunReport serialization tests (P1-E)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdi.analysis import RunReport, RunReportError
from verdi.analysis.estimates import ESTIMANDS, paired_estimates, run_estimand_values
from verdi.analysis.report import G5_LIMITATIONS, build_g5_report, write_g5_report
from verdi.experiments.treatments import (
    placebo_treatment,
    rights_restoration_treatment,
)
from verdi.pilot.driver import PilotDriver, SeededScheduler, _precompute_dispatch
from verdi.schemas import ExperimentManifest
from verdi.world.branch import (
    BranchSpec,
    branch_from_snapshot,
    manifest_for_branch,
)
from verdi.world.reducer import WorldReducer
from verdi.world.replay import replay_in_memory


def _manifest() -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="e-pilot-1",
        world_id="world-pilot-v1",
        run_seed=20261009,
        model_endpoint_alias="local-vllm",
        model_fingerprint="qwen:sha256:0000",
        task_tape_hash="37c18e7870aff031f66ae6bf80104cbea83ab81ebd7e1e7e780c8c32e3db2e7b",
    )


def _report(**overrides) -> RunReport:
    kwargs = dict(
        manifest=_manifest(),
        lifecycle_ticks={"0": {"phase": "initialization"}, "10": {"phase": "assembly"}},
        metrics={"value_drift": None, "false_sanction_rate": {"rate": 0.25}},
        event_count=42,
        state_hash_final="sha256:final",
        data_availability={"missing_probes": ["p-01"], "quarantined_actions": [],
                           "skipped_tasks": ["t-3"]},
        limitations=["Pilot demo scale: 12 agents, single seed; descriptive only, no causal claim."],
    )
    kwargs.update(overrides)
    return RunReport(**kwargs)


def test_to_dict_shape():
    doc = _report().to_dict()
    assert set(doc) == {"manifest", "lifecycle_ticks", "metrics", "event_count",
                        "state_hash_final", "data_availability", "limitations"}
    assert doc["manifest"]["task_tape_hash"] is not None
    assert doc["data_availability"]["missing_probes"] == ["p-01"]
    assert len(doc["limitations"]) >= 1


def test_missing_metric_stays_null_never_zero():
    doc = _report().to_dict()
    assert doc["metrics"]["value_drift"] is None


def test_empty_limitations_rejected():
    with pytest.raises(RunReportError):
        _report(limitations=[])
    with pytest.raises(RunReportError):
        _report(limitations=["   "])


def test_to_jsonl_one_line_per_metric_section():
    lines = _report().to_jsonl().strip().split("\n")
    docs = [json.loads(line) for line in lines]
    assert docs[0]["section"] == "header"
    sections = [d["section"] for d in docs[1:]]
    assert sections.count("lifecycle") == 2
    assert sections.count("metric") == 2
    metric_lines = {d["name"]: d["value"] for d in docs if d["section"] == "metric"}
    assert metric_lines["value_drift"] is None
    assert metric_lines["false_sanction_rate"]["rate"] == 0.25


def test_write_and_roundtrip(tmp_path):
    path = tmp_path / "reports" / "run1.json"
    _report().write(path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == _report().to_dict()


def test_roundtrip_via_jsonl_parse():
    text = _report().to_jsonl()
    docs = [json.loads(line) for line in text.strip().split("\n")]
    assert all(d["section"] for d in docs)




# --------------------------------------------------------------------------- #
# G5 statistical sanity report (P2 WS-4): pilot + WS-2 arms end-to-end
# --------------------------------------------------------------------------- #

G5_REPO_ROOT = Path("/Users/sushantgautam/Documents/verdi")
G5_SNAPSHOT_TICKS = (5, 10, 17, 24, 29)


def _g5_setup():
    """Run the pilot and fork both WS-2 arms from the same branch point.

    Returns (pilot, snapshot, prefix_log, remaining_tape, placebo_result,
    treatment_result, dispatch_ticks).
    """
    pilot = PilotDriver(G5_REPO_ROOT).run()
    tape = pilot.full_tape_steps
    branch_index = next(i for i, step in enumerate(tape) if step.kind == "governance")
    snapshot, _, prefix_log = replay_in_memory(
        pilot.loaded.t0_state, tape[:branch_index]
    )
    remaining = tape[branch_index:]

    loaded = pilot.loaded
    scheduler = SeededScheduler(loaded.t0_state.seed)
    dispatch = _precompute_dispatch(scheduler, loaded.tape, loaded.t0_state)
    dispatch_ticks = [t for t, _, _ in dispatch]

    base_spec = BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(snapshot),
        origin_event_count=len(prefix_log),
        origin_tick=snapshot.tick,
        branch_id="counterfactual-v2",
        treatment_seed=0,
    )
    placebo_result = branch_from_snapshot(
        snapshot, prefix_log, placebo_treatment(base_spec, remaining, seed=0).tape,
        base_spec,
    )
    treatment_spec = BranchSpec(
        origin_state_hash=base_spec.origin_state_hash,
        origin_event_count=base_spec.origin_event_count,
        origin_tick=base_spec.origin_tick,
        branch_id=base_spec.branch_id,
        treatment_seed=1,
    )
    treatment_result = branch_from_snapshot(
        snapshot, prefix_log,
        rights_restoration_treatment(treatment_spec, remaining, seed=1).tape,
        treatment_spec,
    )
    # both arms are inert on pilot_v1 (appeal dismissed, no sealed
    # restoration) and must fold to the pilot's final hash exactly.
    assert placebo_result.branch_state_hash == pilot.final_state.state_hash
    assert treatment_result.branch_state_hash == pilot.final_state.state_hash
    # the placeholder prefix_log is the same for both arms
    return pilot, snapshot, prefix_log, remaining, placebo_result, treatment_result, \
        dispatch_ticks


def _arm_states_by_tick(remaining, snapshot, dispatch_ticks):
    """Per-LIFECYCLE-tick snapshots for one branch fold (ADR-008).

    The fold starts at the first governance step (tick 10 in pilot_v1):
    the first ``n_prefix`` dispatch entries (ticks < 10) were consumed by
    the prefix before the branch point, so the fold's ACTION steps map in
    order to ``dispatch_ticks[n_prefix:]``; governance steps carry their
    lifecycle tick in ``payload['tick']``. Snapshots are recorded at the
    pilot's ``SNAPSHOT_TICKS`` cadence plus the terminal tick, exactly
    mirroring the pilot's keying.

    Returns ``(states_by_tick, final_state)``.
    """
    # The fold starts at the first governance step; dispatch entries with
    # a lifecycle tick below that tick form the prefix, so the fold's
    # ACTION steps map in order to dispatch_ticks[n_prefix:].
    first_gov_tick = int(
        next(s for s in remaining if s.kind == "governance").governance["tick"]
    )
    n_prefix = sum(1 for t in dispatch_ticks if t < first_gov_tick)
    action_ptr = n_prefix
    reducer = WorldReducer()
    state = snapshot
    states_by_tick: dict[int, object] = {}
    covered = set()
    for step in remaining:
        if step.kind == "action":
            tick = dispatch_ticks[action_ptr]
            action_ptr += 1
        else:
            tick = int(step.governance["tick"])
        state = reducer.apply(state, step.action, step.validation)
        if tick in G5_SNAPSHOT_TICKS and tick not in covered:
            states_by_tick[tick] = state
            covered.add(tick)
    terminal = max(G5_SNAPSHOT_TICKS)
    states_by_tick[terminal] = state
    return states_by_tick, state


def _arm_run_dict(arm, applied, reason, result, states_by_tick, pilot_manifest):
    return {
        "arm": arm,
        "applied": applied,
        "reason": reason,
        "final_state_hash": result.branch_state_hash,
        "manifest": manifest_for_branch(
            result.spec, pilot_manifest
        ),
        "state_snapshots": sorted(states_by_tick.items()),
        "states_by_tick": states_by_tick,
        "log": result.log.events,
        "estimates": run_estimand_values(
            states_by_tick=states_by_tick, log=result.log.events
        ),
    }


def test_g5_report_pilot_v1(tmp_path):
    (
        pilot, snapshot, prefix_log, remaining,
        placebo_result, treatment_result, dispatch_ticks,
    ) = _g5_setup()

    # ---- arm run dicts -----------------------------------------------------
    placebo_states, placebo_final = _arm_states_by_tick(
        remaining, snapshot, dispatch_ticks
    )
    treatment_states, treatment_final = _arm_states_by_tick(
        remaining, snapshot, dispatch_ticks
    )
    # the fold's terminal state is the branch's final state
    assert WorldReducer.canonical_state_hash(placebo_final) == (
        placebo_result.branch_state_hash
    )
    assert WorldReducer.canonical_state_hash(treatment_final) == (
        treatment_result.branch_state_hash
    )
    manifest = pilot.manifest.model_dump()
    placebo_run = _arm_run_dict(
        "placebo", False,
        "inert by construction on pilot_v1: no upheld appeal exists to "
        "revoke; placebo_treatment appends no tape steps",
        placebo_result, placebo_states, manifest,
    )
    treatment_run = _arm_run_dict(
        "rights_restoration", False,
        "inert by construction on pilot_v1: the appeal was dismissed, so "
        "no upheld sanction exists to revoke",
        treatment_result, treatment_states, manifest,
    )
    assert placebo_run["manifest"]["branch_origin"] == "counterfactual-v2"
    assert treatment_run["manifest"]["treatment_seed"] == 1

    # ---- G5: arms are inert by construction --------------------------------
    assert (
        placebo_run["estimates"] == treatment_run["estimates"]
        == {e: float(v) for e, v in placebo_run["estimates"].items()}
    )
    pilot_states = {t: st for t, st in pilot.state_snapshots}
    pilot_values = run_estimand_values(
        states_by_tick=pilot_states, log=pilot.log.events
    )
    assert set(pilot_values) == set(ESTIMANDS)
    assert placebo_run["estimates"] == pilot_values  # arms fold to pilot

    # ---- per-estimand paired results: one pair, honest refusal --------------
    pairs_results = paired_estimates(
        [(treatment_run, placebo_run)], ci=0.95, n_boot=2000, seed=0
    )
    assert set(pairs_results) == set(ESTIMANDS)
    for res in pairs_results.values():
        assert res.n_pairs == 1
        assert res.sufficient is False
        assert res.ci_low is None and res.ci_high is None
        assert res.estimate == 0.0  # identical arms, zero difference

    # ---- build + byte-stability ---------------------------------------------
    report1 = build_g5_report(
        pilot, treatment_run, placebo_run, pairs_results
    )
    report2 = build_g5_report(
        pilot, treatment_run, placebo_run, pairs_results
    )
    p1 = write_g5_report(report1, tmp_path / "g5a" / "report.json")
    p2 = write_g5_report(report2, tmp_path / "g5b" / "report.json")
    assert p1.read_bytes() == p2.read_bytes()  # deterministic bytes
    assert json.loads(p1.read_text(encoding="utf-8")) is not None

    # ---- G5 report content checks --------------------------------------------
    for arm in ("pilot", "treatment", "placebo"):
        traj = report1["trajectories"][arm]
        assert set(traj["estimates"]) == set(ESTIMANDS)
        assert traj["state_hashes_by_lifecycle_tick"]
    assert report1["design"]["randomized"] is False
    assert report1["null_control"]["passed"] is True
    assert report1["design"]["arms"]["treatment"]["final_state_hash"] == (
        pilot.final_state.state_hash
    )
    assert report1["design"]["arms"]["placebo"]["final_state_hash"] == (
        pilot.final_state.state_hash
    )
    # limitations must carry the required honest statements
    joined = " | ".join(report1["limitations"])
    assert "no live LLM" in joined
    assert "no causal claims" in joined
    assert "n_pairs < 10" in joined
    assert "recorded tape" in joined
    assert set(report1["limitations"]) == set(G5_LIMITATIONS)
