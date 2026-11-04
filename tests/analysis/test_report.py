"""RunReport serialization tests (P1-E)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from verdi_agents.analysis import RunReport, RunReportError
from verdi_agents.analysis.estimates import ESTIMANDS, paired_estimates, run_estimand_values
from verdi_agents.analysis.report import (
    G5_ESTIMANDS,
    G5_LIMITATIONS,
    build_g5_report,
    write_g5_report,
)
from verdi_agents.experiments.treatments import (
    placebo_treatment,
    rights_restoration_treatment,
)
from verdi_agents.reference_run.driver import PilotDriver, SeededScheduler, _precompute_dispatch
from verdi_agents.schemas import ExperimentManifest
from verdi_agents.world.branch import (
    BranchSpec,
    branch_from_snapshot,
    manifest_for_branch,
)
from verdi_agents.world.reducer import WorldReducer
from verdi_agents.world.replay import replay_in_memory


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

# SF4: derive the repo root from THIS test file's location — the
# checkout (worktree or main) that contains src/verdi_agents/reference_run/driver.py —
# instead of a hardcoded absolute path. Optional override via the
# VERDI_REPO_ROOT env var: use it only when the pilot's recorded assets
# live in a different checkout than the code under test.
# parents: [0]=analysis, [1]=tests, [2]=checkout root
G5_REPO_ROOT = Path(os.environ.get("VERDI_REPO_ROOT") or
                    Path(__file__).resolve().parents[2])


def _pilot():
    """Run the pilot with the derived repo root.

    ``PilotDriver`` itself would already default to
    ``Path(__file__).parents[3]`` of ``src/verdi_agents/reference_run/driver.py``; we
    pass the explicitly-derived root so the derivation lives in one,
    testable place and an env-var override can redirect it.
    """
    assert (G5_REPO_ROOT / "src" / "verdi_agents" / "reference_run" / "driver.py").is_file(), (
        f"G5_REPO_ROOT {G5_REPO_ROOT} does not contain src/verdi_agents/reference_run/driver.py"
    )
    return PilotDriver(G5_REPO_ROOT).run()


def _g5_setup():
    """Run the pilot and fork both WS-2 arms from the same branch point.

    Returns (pilot, snapshot, prefix_log, remaining_tape, placebo_result,
    treatment_result, dispatch_ticks).
    """
    pilot = _pilot()
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


def _arm_states_by_tick(remaining, snapshot, dispatch_ticks, snapshot_ticks):
    """Per-LIFECYCLE-tick snapshots for one branch fold (ADR-008).

    The fold starts at the first governance step (tick 10 in pilot_v1):
    the first ``n_prefix`` dispatch entries (ticks < 10) were consumed by
    the prefix before the branch point, so the fold's ACTION steps map in
    order to ``dispatch_ticks[n_prefix:]``; governance steps carry their
    lifecycle tick in ``payload['tick']``. Snapshots are recorded at the
    cadence given by ``snapshot_ticks`` plus the terminal tick, exactly
    mirroring the pilot's keying. ``snapshot_ticks`` is derived from the
    pilot run's own recorded ``state_snapshots`` keys (SF4), not a
    frozen tuple.

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
        if tick in snapshot_ticks and tick not in covered:
            states_by_tick[tick] = state
            covered.add(tick)
    terminal = max(snapshot_ticks)
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

    # ---- snapshot cadence: the pilot's own recorded keys (SF4) --------------
    # Exclude the initial t0 snapshot: the branch fold starts at the
    # first governance step, so the arm cadence is the pilot's cadence
    # ticks only (its t0 predates the branch point).
    snapshot_ticks = tuple(t for t, _ in pilot.state_snapshots if t > 0)
    assert snapshot_ticks, "pilot recorded no post-initial snapshots"

    # ---- arm run dicts -----------------------------------------------------
    placebo_states, placebo_final = _arm_states_by_tick(
        remaining, snapshot, dispatch_ticks, snapshot_ticks
    )
    treatment_states, treatment_final = _arm_states_by_tick(
        remaining, snapshot, dispatch_ticks, snapshot_ticks
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
    # SF3b: the null-control CI leg is computed over the ACTUAL
    # per-estimand differences (here all zero), never constant zeros as
    # an input label.
    assert report1["null_control"]["ci_input"] == "per-estimand differences"
    assert report1["null_control"]["all_differences_zero"] is True


# --------------------------------------------------------------------------- #
# SF3b: null control is computed over actual differences, not constant zeros
# --------------------------------------------------------------------------- #

def _single_result(estimate):
    from verdi_agents.analysis.statistics import PairedResult

    return PairedResult(
        estimate=estimate, ci_low=None, ci_high=None, n_pairs=1, sufficient=False
    )


def test_null_control_nonzero_differences_fail_ci_leg():
    # SF3b: every per-estimand difference is a constant 5.0 (non-zero).
    # The CI must be computed over these REAL differences, not constant
    # zeros: the bootstrap CI of the mean over [5.0]*6 is [5, 5], which
    # does NOT contain 0, so the CI leg must FAIL. The old code computed
    # over [0,0,0,0] and would have reported passed=True here.
    from verdi_agents.analysis.report import _null_control

    pairs = {name: _single_result(5.0) for name in G5_ESTIMANDS}
    control = _null_control(pairs)
    assert control["ci_input"] == "per-estimand differences"
    assert control["all_differences_zero"] is False
    assert control["ci_contains_zero"] is False
    assert control["passed"] is False
    for name in G5_ESTIMANDS:
        assert control["per_estimand"][name]["difference"] == 5.0
        assert control["per_estimand"][name]["ci_contains_zero"] is False


def test_null_control_zero_differences_ci_is_over_real_zeros():
    # SF3b: all-zero differences -> CI trivially [0,0], contains 0,
    # passes truthfully (this is the inert-arm wiring check).
    from verdi_agents.analysis.report import _null_control

    pairs = {name: _single_result(0.0) for name in G5_ESTIMANDS}
    control = _null_control(pairs)
    assert control["ci_input"] == "per-estimand differences"
    assert control["all_differences_zero"] is True
    assert control["ci_contains_zero"] is True
    assert control["passed"] is True


def test_null_control_insufficient_pairs_marks_null():
    # SF3b: fewer than two difference values -> CI cannot be computed;
    # the field is null with an explicit marker, not computed over
    # constant zeros.
    from verdi_agents.analysis.report import _null_control

    pairs = {G5_ESTIMANDS[0]: _single_result(0.0)}
    control = _null_control(pairs)
    assert control["ci_input"] == "insufficient pairs"
    assert control["ci_contains_zero"] is None
    assert control["passed"] is None
    assert control["per_estimand"][G5_ESTIMANDS[0]]["ci_contains_zero"] is None


# --------------------------------------------------------------------------- #
# SF1b: build_g5_report validates the required run-dict key set up front
# --------------------------------------------------------------------------- #

def _valid_run_dict() -> dict:
    from tests.analysis.conftest import world_state

    return {
        "arm": "placebo",
        "applied": False,
        "reason": "inert",
        "final_state_hash": "h0",
        "state_snapshots": [
            (0, world_state(0, {"a-01": {"food": 3.0}})),
            (5, world_state(5, {"a-01": {"food": 3.0}})),
        ],
        "estimates": {name: 0.0 for name in G5_ESTIMANDS},
    }


def _dummy_pilot():
    from tests.analysis.conftest import world_state

    class _State:
        state_hash = "h0"

    class _Manifest:
        def model_dump(self):
            return {"experiment_id": "e", "branch_origin": "b"}

    class _Pilot:
        manifest = _Manifest()
        final_state = _State()
        state_snapshots = [
            (0, world_state(0, {"a-01": {"food": 3.0}})),
        ]

    return _Pilot()


def test_build_g5_report_missing_key_raises():
    # SF1b: a missing required key raises ValueError naming the key,
    # instead of silently writing a null into the statistical report.
    run = _valid_run_dict()
    del run["final_state_hash"]
    with pytest.raises(ValueError, match="final_state_hash"):
        build_g5_report(
            _dummy_pilot(), run, _valid_run_dict(),
            {name: _single_result(0.0) for name in G5_ESTIMANDS},
        )


def test_build_g5_report_missing_estimates_and_raw_raises():
    # SF1b: neither 'estimates' nor both 'states_by_tick'+'log' present.
    run = _valid_run_dict()
    del run["estimates"]
    with pytest.raises(ValueError, match="estimates"):
        build_g5_report(
            _dummy_pilot(), run, _valid_run_dict(),
            {name: _single_result(0.0) for name in G5_ESTIMANDS},
        )


def test_build_g5_report_raw_source_satisfies_estimates():
    # SF1b: 'states_by_tick' + 'log' in place of 'estimates' is accepted.
    from tests.analysis.conftest import world_state

    run = _valid_run_dict()
    del run["estimates"]
    run["state_snapshots"] = [
        (0, world_state(0, {"a-01": {"food": 3.0}})),
        (5, world_state(5, {"a-01": {"food": 3.0}})),
    ]
    run["states_by_tick"] = {
        0: world_state(0, {"a-01": {"food": 3.0}}),
        5: world_state(5, {"a-01": {"food": 3.0}}),
    }
    run["log"] = []
    build_g5_report(
        _dummy_pilot(), run, _valid_run_dict(),
        {name: _single_result(0.0) for name in G5_ESTIMANDS},
    )


# --------------------------------------------------------------------------- #
# SF4: repo root derivation
# --------------------------------------------------------------------------- #

def test_repo_root_derivation_contains_driver():
    # SF4: the root is derived from this test file's own location, not a
    # frozen hardcoded absolute path — so it points at the checkout
    # containing src/verdi_agents/reference_run/driver.py and the pilot assets.
    assert G5_REPO_ROOT.is_absolute()
    # derived from __file__ (or the VERDI_REPO_ROOT override), not frozen
    expected = (
        Path(os.environ["VERDI_REPO_ROOT"])
        if os.environ.get("VERDI_REPO_ROOT")
        else Path(__file__).resolve().parents[2]
    )
    assert expected.resolve() == G5_REPO_ROOT
    assert (G5_REPO_ROOT / "src" / "verdi_agents" / "reference_run" / "driver.py").is_file()
    assert (G5_REPO_ROOT / "tests" / "fixtures" / "reference_run" / "scenarios" / "pilot_v1.json").is_file()
