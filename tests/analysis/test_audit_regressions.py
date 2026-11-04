"""Regression tests for previously fixed bugs."""
from __future__ import annotations

import json

import pytest
import yaml

from verdi_agents.analysis.outcome_metrics import recovery_time


def test_recovery_time_maps_outcomes_to_due_ticks_in_evaluation_order(tmp_path):
    # Declared out of order (as in examples/full.yaml); the campaign evaluates
    # objectives in due-tick order, so outcomes arrive for 12, 30, 30, 46.
    (tmp_path / "scenario_resolved.yaml").write_text(yaml.safe_dump({"tasks": {
        "deliveries": [{"due_tick": t} for t in (12, 30, 46, 30)]}}))
    (tmp_path / "manifest.json").write_text("{}")
    outcomes = [(1, 13, "task_completed"), (2, 31, "task_failed"),
                (3, 31, "task_failed"), (4, 47, "task_completed")]
    (tmp_path / "events.jsonl").write_text("".join(
        json.dumps({"event_id": eid, "tick": tick, "kind": kind}) + "\n"
        for eid, tick, kind in outcomes))
    rec = recovery_time(tmp_path, pre_shock_window=(0, 12), post_shock_start=13,
                        target_fraction=1.0, sustained_ticks=1)
    assert rec["per_tick_rates"] == {"12": 1.0, "30": 0.0, "46": 1.0}
    assert rec["recovery_tick"] == 46


def test_recovery_time_degrades_on_malformed_due_tick(tmp_path):
    (tmp_path / "scenario_resolved.yaml").write_text(yaml.safe_dump({"tasks": {
        "deliveries": [{"due_tick": "soon"}]}}))
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"event_id": 1, "tick": 3, "kind": "task_completed"}) + "\n")
    rec = recovery_time(tmp_path, pre_shock_window=(0, 3), post_shock_start=4,
                        target_fraction=1.0, sustained_ticks=1)
    assert rec["per_tick_rates"] == {"3": 1.0}


def _write_run(tmp_path, sanctions, resource_ticks, ledger_ticks):
    (tmp_path / "sanctions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sanctions))
    (tmp_path / "resources.jsonl").write_text(
        "".join(json.dumps({"tick": t, "agents": {}}) + "\n" for t in resource_ticks))
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps({"event_id": i, "tick": t}) + "\n" for i, t in enumerate(ledger_ticks)))


def _imposed(sid, subject, tick):
    return {"record_type": "sanction", "kind": "sanction_imposed", "decision": "accepted",
            "sanction_id": sid, "subject_id": subject, "tick": tick}


def test_open_exclusion_ends_at_last_lifecycle_tick_not_ledger_tick(tmp_path):
    from verdi_agents.analysis.outcome_metrics import exclusion_episodes

    _write_run(tmp_path, [_imposed("s1", "grace", 19)], range(20), [1, 50, 104])
    # bounded by the last LIFECYCLE tick (19), not the ledger counter (104 -> 85)
    assert exclusion_episodes(tmp_path)["agents"]["grace"]["excluded_ticks"] == 0


def test_overlapping_exclusions_are_merged(tmp_path):
    from verdi_agents.analysis.outcome_metrics import exclusion_episodes

    _write_run(tmp_path, [
        _imposed("s1", "dave", 0), _imposed("s2", "dave", 2),
        {"record_type": "restoration", "sanction_id": "s2", "tick": 3},
        {"record_type": "restoration", "sanction_id": "s1", "tick": 6},
    ], range(20), [1, 2])
    dave = exclusion_episodes(tmp_path)["agents"]["dave"]
    assert dave["excluded_ticks"] == 6
    assert dave["repeat_episode"] is False


def test_comparative_report_marks_undefined_baseline_not_evaluable(tmp_path):
    from verdi_agents.analysis.comparative_report import _recovery_row

    (tmp_path / "events.jsonl").write_text(
        json.dumps({"event_id": 1, "tick": 31, "kind": "task_completed"}) + "\n")
    (tmp_path / "scenario_resolved.yaml").write_text(yaml.safe_dump(
        {"tasks": {"deliveries": [{"due_tick": 30}]}}))
    html = _recovery_row({"arms": {"a": {"1": {"run_dir": str(tmp_path)}}}}, "a", 1)
    assert "not evaluable" in html and "CENSORED" not in html


def test_completion_due_tick_is_lifecycle_tick_not_ledger_tick(tmp_path):
    from verdi_agents.analysis.outcome_metrics import completion_rate

    rows = [
        {"event_id": 0, "tick": 55, "kind": "action_accepted",
         "payload": {"task_id": "soc-t10-03"}},
        {"event_id": 1, "tick": 56, "kind": "task_completed",
         "payload": {"objective_id": "pre"}},
    ]
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (obj,) = completion_rate(tmp_path)["objectives"]
    assert (obj["due_tick"], obj["ledger_tick"]) == (10, 56)

    (tmp_path / "scenario_resolved.yaml").write_text(yaml.safe_dump({"tasks": {
        "deliveries": [{"objective_id": "pre", "due_tick": 12, "resource": "food",
                        "amount": 1}]}}))
    assert completion_rate(tmp_path)["objectives"][0]["due_tick"] == 12


def _scenario(**extra):
    import copy
    from pathlib import Path

    from verdi_agents.analysis.scenario import load_scenario

    sc = copy.deepcopy(load_scenario(Path(__file__).resolve().parents[2] / "examples" / "starter.yaml"))
    sc.update(extra)
    return sc


@pytest.mark.parametrize("mutate, message", [
    (lambda sc: sc["agents"][0]["resources"].update(food=-1), "finite number >= 0"),
    (lambda sc: sc["agents"][0]["resources"].update(food=float("nan")), "finite number >= 0"),
    (lambda sc: sc.update(shocks=[{"tick": "soon", "resource": "food", "delta": -1}]), "shocks"),
    (lambda sc: sc.update(shocks=[{"tick": 2, "resource": "food", "delta": float("nan")}]), "delta"),
    (lambda sc: sc.update(tasks={"deliveries": [{"due_tick": 2, "resource": "food",
                                                  "amount": 1, "target_pool": "alcie"}]}),
     "target_pool"),
    (lambda sc: sc.update(agents=[]), "no agents"),
])
def test_bad_scenario_values_fail_at_load_time(mutate, message):
    from verdi_agents.analysis.scenario import ScenarioLoadError, build_t0_state

    sc = _scenario()
    mutate(sc)
    with pytest.raises(ScenarioLoadError, match=message):
        build_t0_state(sc)


def test_completion_due_tick_counts_consumption_ticks_when_economy_active(tmp_path):
    from verdi_agents.analysis.outcome_metrics import completion_rate

    rows = [{"event_id": i, "tick": i + 1, "kind": "consumption"} for i in range(11)]
    # every tick-10 action was quarantined: no soc-t10 task id in the ledger
    rows.append({"event_id": 11, "tick": 12, "kind": "task_completed",
                 "payload": {"objective_id": "pre"}})
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert completion_rate(tmp_path)["objectives"][0]["due_tick"] == 10
