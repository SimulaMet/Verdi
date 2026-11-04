"""Regression tests for defects found in the 2026-11 system audit (AUDIT.md)."""
from __future__ import annotations

import threading
import time

import pytest

from verdi_agents.analysis.comparative_report import _institution_rows
from verdi_agents.experiments import sweep as sw

from .test_sweep import STARTER, _no_real_env  # noqa: F401 - autouse fixture


def _cfg(tmp_path, **raw):
    return sw.build_config({"scenario": STARTER, "output": str(tmp_path / "out"), **raw})


def test_enacted_rule_values_with_braces_render_without_crashing():
    metrics = {"institutional_mechanism": {"enactments": [{
        "version": "c-v2", "tick": 4,
        "rule_deltas": {"added": {"limits": {"max": 3}}, "changed": {}, "removed": {}},
    }]}}
    html = "".join(_institution_rows(metrics))
    assert "c-v2" in html and "@ tick 4" in html
    assert "&#x27;max&#x27;" in html  # escaped exactly once
    assert "&amp;#x27;" not in html


@pytest.mark.parametrize("value", ["..", ".", "..."])
def test_dot_only_labels_cannot_escape_the_sweep_folder(value):
    assert sw._label(value) == "value"


def test_resume_reruns_complete_runs_whose_scenario_changed(tmp_path):
    calls: list[str] = []

    def launcher(run, conc):
        calls.append(run.run_id)
        return 0

    cfg1 = _cfg(tmp_path, seeds=[1, 2], ticks=1)
    sw.run_sweep(cfg1, sw.expand(cfg1), launcher=launcher, out=lambda _: None)
    calls.clear()
    cfg2 = _cfg(tmp_path, seeds=[1], ticks=3)
    result = sw.run_sweep(cfg2, sw.expand(cfg2), launcher=launcher, out=lambda _: None)
    assert calls == ["baseline/seed-1"], "changed ticks must not reuse the old run"
    # seed-2 from the earlier invocation is not part of this sweep's result
    assert set(result.entries) == {"baseline/seed-1"}
    assert [r["run"] for r in sw.collect(cfg2.output)] == ["baseline/seed-1"]


def test_ctrl_c_does_not_start_queued_runs(tmp_path):
    cfg = _cfg(tmp_path, seeds=[1, 2, 3, 4], ticks=1, parallel_runs=1)
    started: list[str] = []
    lock = threading.Lock()

    def launcher(run, conc):
        with lock:
            started.append(run.run_id)
        time.sleep(0.05)
        return 0

    real_as_completed = sw.as_completed

    def interrupting(futures):
        for i, fut in enumerate(real_as_completed(futures)):
            if i == 1:
                raise KeyboardInterrupt
            yield fut

    sw.as_completed = interrupting
    try:
        with pytest.raises(KeyboardInterrupt):
            sw.run_sweep(cfg, sw.expand(cfg), launcher=launcher, out=lambda _: None)
    finally:
        sw.as_completed = real_as_completed
    assert len(started) < 4


def test_interview_post_pass_audit_returns_summary(tmp_path, monkeypatch):
    # audit_from_file is synchronous; wrapping its dict in asyncio.run crashed
    # every post-pass audit with "a coroutine was expected".
    import verdi_agents.audit.interview_audit as ia
    from verdi_agents.experiments.interview_experiment import _audit_run

    (tmp_path / "interviews.jsonl").write_text("{}\n")
    monkeypatch.setattr(ia, "audit_from_file", lambda *a, **k: {"provenance_rows": 1})
    summary = _audit_run(tmp_path, base_url="http://x/v1", api_key="k", model="m",
                         concurrency=1)
    assert summary == {"provenance_rows": 1}


@pytest.mark.parametrize("reply, choice", [
    ("Yes.", "yes"), ('"no"', "no"), ("Yes, I would.", "yes"),
])
def test_probe_parser_accepts_punctuated_yes_no(reply, choice):
    from verdi_agents.measurement.longitudinal import _parse_choice

    assert _parse_choice(reply, ["yes", "no"]) == (choice, False)


@pytest.mark.parametrize("reply, expected", [
    ("On a scale of 0 to 5, I would say 4", ("4", False)),
    ("10", ("0", True)),
    ("I rate it 7 out of 10", ("0", True)),
])
def test_probe_parser_likert_takes_last_in_range_integer(reply, expected):
    from verdi_agents.measurement.longitudinal import _parse_choice

    assert _parse_choice(reply, [str(i) for i in range(6)]) == expected


def test_paired_run_without_output_root_fails_readably(tmp_path):
    from verdi_agents.experiments.paired_run import run_pair

    config = {"experiment_id": "x", "base_scenario": STARTER, "arms": ["fixed"],
              "seeds": [1], "total_ticks_override": None, "output_root": None}
    with pytest.raises(SystemExit, match="output_root"):
        run_pair(config, lambda sc, rd: None)


def test_rerun_moves_previous_attempt_aside_instead_of_appending(tmp_path):
    def launcher(run, conc):
        log = run.run_dir / "events.jsonl"
        with log.open("a") as fh:
            fh.write('{"event_id": 0}\n')
        return 0

    cfg1 = _cfg(tmp_path, seeds=[1], ticks=1)
    sw.run_sweep(cfg1, sw.expand(cfg1), launcher=launcher, out=lambda _: None)
    cfg2 = _cfg(tmp_path, seeds=[1], ticks=2)
    (run,) = sw.expand(cfg2)
    sw.run_sweep(cfg2, [run], launcher=launcher, out=lambda _: None)
    assert (run.run_dir / "events.jsonl").read_text().count('"event_id": 0') == 1
    assert list(run.run_dir.parent.glob(f"{run.run_dir.name}.prev-*"))


@pytest.mark.parametrize("reply, choice", [
    ("4.", "4"), ("4.0", "4"), ("I rate it 4/5", "4"), ("4 out of 5", "4"),
    ("Answer: 3 (scale 0-5)", "3"),
])
def test_probe_parser_likert_ignores_scale_and_denominator(reply, choice):
    from verdi_agents.measurement.longitudinal import _parse_choice

    assert _parse_choice(reply, [str(i) for i in range(6)]) == (choice, False)


def test_paired_run_rerun_sets_previous_attempt_aside(tmp_path, monkeypatch):
    import verdi_agents.experiments.paired_run as pr

    calls = []

    async def fake_arm(*, arm, seed, scenario, run_dir, caller_factory, concurrency):
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "events.jsonl").open("a") as fh:
            fh.write('{"event_id": 0}\n')
        calls.append(arm)
        return {"status": "failed" if len(calls) == 1 else "complete"}

    monkeypatch.setattr(pr, "_run_one_arm", fake_arm)
    config = {"experiment_id": "x", "base_scenario": STARTER, "arms": ["fixed"],
              "seeds": [1], "total_ticks_override": 1, "output_root": str(tmp_path / "o")}
    pr.run_pair(config, lambda sc, rd: None)
    pr.run_pair(config, lambda sc, rd: None)
    run_dir = tmp_path / "o" / "fixed-seed1"
    assert (run_dir / "events.jsonl").read_text().count('"event_id": 0') == 1
    assert list(run_dir.parent.glob("fixed-seed1.prev-*"))


def test_causal_guard_ignores_verbatim_model_text():
    from verdi_agents.experiments.g5_close import _without_model_text, causal_word_guard

    report = {"judge": {"rows": [{"rationale": "The proposal is causally linked.",
                                  "raw": "causal causal causal"}]}}
    causal_word_guard(_without_model_text(report))  # must not raise
    with pytest.raises(AssertionError):
        causal_word_guard(_without_model_text({"summary": "The effect is causal."}))


def test_nearest_event_returns_latest_event_by_lifecycle_tick():
    from verdi_agents.measurement.interview_values import _nearest_event

    events = [
        {"event_id": 0, "tick": 1, "actor_id": "e", "kind": "a",
         "payload": {"task_id": "soc-t00-00"}},
        {"event_id": 1, "tick": 2, "actor_id": "e", "kind": "a",
         "payload": {"task_id": "soc-t01-00"}},
        {"event_id": 2, "tick": 3, "actor_id": "e", "kind": "a",
         "payload": {"task_id": "soc-t05-00"}},
    ]
    assert _nearest_event("e", 3, events)["event_id"] == 1
    assert _nearest_event("e", 29, events)["event_id"] == 2


@pytest.mark.parametrize("reply, choice", [
    ("Choice: 4\n\nReasoning: 2 factors", "4"),
    ("I choose 3. That is because 5 people agree.", "3"),
    ('{"choice": 4, "reason": "weighing 2 duties"}', "4"),
    ("Answer: 4 — 10x more", "4"),
])
def test_probe_parser_likert_answer_leads_over_trailing_reasoning(reply, choice):
    from verdi_agents.measurement.longitudinal import _parse_choice

    assert _parse_choice(reply, [str(i) for i in range(6)]) == (choice, False)


def test_causal_guard_ignores_model_stance_in_error_field():
    from verdi_agents.experiments.g5_close import _without_model_text, causal_word_guard

    causal_word_guard(_without_model_text(
        {"rows": [{"error": "invalid stance 'causally_compliant'"}]}))
