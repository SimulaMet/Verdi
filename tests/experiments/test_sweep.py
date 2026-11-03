"""Sweep engine: grid expansion, overrides, validation, resume, results.

Offline: runs use an in-process launcher with a scripted caller instead of
the live subprocess, so no endpoint is needed.
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path

import pytest
import yaml

from verdi.analysis.scenario import build_t0_state
from verdi.experiments import sweep as sw
from verdi.society.campaign import SocietyCampaign

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))
from test_example_scenarios import _SignalCaller  # noqa: E402

STARTER = str(REPO_ROOT / "examples" / "starter.yaml")
FULL = str(REPO_ROOT / "examples" / "full.yaml")


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch, tmp_path):
    """Never load the developer's real .env (keys) into the test process."""
    monkeypatch.setenv("VERDI_DOTENV_PATH", str(tmp_path / "no.env"))
    for var in ("MODEL_API_KEY", "VERDI_VLLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _cfg(tmp_path, **raw):
    return sw.build_config({"scenario": STARTER, "output": str(tmp_path / "out"), **raw})


def offline_launcher(run: sw.RunSpec, agent_concurrency: int) -> int:
    sc = yaml.safe_load((run.run_dir / sw.SCENARIO_NAME).read_text(encoding="utf-8"))
    build_t0_state(sc)
    asyncio.run(SocietyCampaign(scenario=sc, run_dir=run.run_dir, caller=_SignalCaller(),
                                run_seed=run.seed, concurrency=agent_concurrency).run())
    return 0


# --------------------------------------------------------------------------- #
# config + expansion
# --------------------------------------------------------------------------- #

def test_grid_expands_every_combination_with_overrides(tmp_path):
    cfg = sw.build_config({
        "scenario": FULL, "output": str(tmp_path),
        "vary": {
            "model.name": ["m1", "m2"],
            "constitution.quorum": [0.5, 0.75],
            "shocks.0.delta": {"mild": -9.0, "severe": -18.0},
        },
        "seeds": [1, 2], "ticks": 5,
    })
    runs = sw.expand(cfg)
    assert len(runs) == 2 * 2 * 2 * 2
    assert len({r.run_id for r in runs}) == len(runs)
    r = next(r for r in runs if r.labels == {
        "model.name": "m2", "constitution.quorum": "0.75", "shocks.0.delta": "severe"} and r.seed == 2)
    assert r.scenario["model"]["name"] == "m2"
    assert r.scenario["model"]["base_url"] == cfg.base["model"]["base_url"]
    assert r.scenario["constitution"]["quorum"] == 0.75
    assert r.scenario["shocks"][0]["delta"] == -18.0
    assert r.scenario["run_seed"] == 2 and r.scenario["total_ticks"] == 5
    assert r.run_dir == tmp_path / "model-m2__quorum-0.75__delta-severe" / "seed-2"
    # the baseline is never mutated
    assert cfg.base["constitution"]["quorum"] == 0.5


def test_mapping_value_merges_into_model_block(tmp_path):
    cfg = _cfg(tmp_path, vary={"model": {
        "local": {"name": "a"},
        "remote": {"name": "b", "base_url": "http://other/v1", "api_key_env": "OTHER_KEY"}}})
    runs = {r.condition: r.scenario["model"] for r in sw.expand(cfg)}
    assert runs["local"] == {**cfg.base["model"], "name": "a"}
    assert runs["remote"]["base_url"] == "http://other/v1"
    assert runs["remote"]["api_key_env"] == "OTHER_KEY"


def test_seed_count_and_single_factor_names(tmp_path):
    cfg = _cfg(tmp_path, vary={"model.name": ["a", "b"]}, seeds=3)
    runs = sw.expand(cfg)
    assert cfg.seeds == [42, 43, 44]
    assert {r.condition for r in runs} == {"a", "b"}
    assert sw.expand(_cfg(tmp_path))[0].condition == "baseline"


@pytest.mark.parametrize(("raw", "message"), [
    ({"vary": {"constitution.qourum": [1]}}, "Did you mean"),
    ({"vary": {"model.nmae": ["x"]}}, "model.name"),
    ({"seds": [1]}, "did you mean 'seeds'"),
    ({"vary": {"model": [{"name": "a"}]}}, "need names"),
    ({"seeds": [1, 1]}, "unique"),
    ({"parallel_runs": 0}, ">= 1"),
])
def test_user_errors_are_readable(tmp_path, raw, message):
    with pytest.raises(SystemExit, match=message):
        _cfg(tmp_path, **raw)


def test_optional_model_keys_may_be_added(tmp_path):
    cfg = _cfg(tmp_path, vary={"model.timeout_seconds": [30, 60]})
    assert [r.scenario["model"]["timeout_seconds"] for r in sw.expand(cfg)] == [30, 60]


def test_validation_rejects_invalid_resolved_scenario(tmp_path):
    cfg = _cfg(tmp_path, vary={"world_resources": {"bad": "oops"}})
    with pytest.raises(SystemExit, match="invalid after overrides"):
        sw.validate_runs(sw.expand(cfg))


# --------------------------------------------------------------------------- #
# running, resume, results
# --------------------------------------------------------------------------- #

def test_run_sweep_offline_writes_frozen_inputs_and_results(tmp_path):
    cfg = _cfg(tmp_path, vary={"model.name": ["a", "b"]}, seeds=[1, 2],
               ticks=3, parallel_runs=2)
    runs = sw.expand(cfg)
    result = sw.run_sweep(cfg, runs, launcher=offline_launcher, out=lambda _: None)
    assert result.exit_code == 0
    for r in runs:
        frozen = yaml.safe_load((r.run_dir / sw.SCENARIO_NAME).read_text())
        assert frozen == r.scenario
        assert (r.run_dir / "metrics.json").is_file()
    manifest = json.loads((cfg.output / sw.MANIFEST_NAME).read_text())
    assert {e["status"] for e in manifest["runs"].values()} == {"complete"}
    assert manifest["factors"] == {"model.name": {"a": "a", "b": "b"}}

    csv_path, html_path = sw.write_results(cfg.output, cfg.name)
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 4
    assert {row["model.name"] for row in rows} == {"a", "b"}
    assert {row["model_requested"] for row in rows} == {"a", "b"}
    assert all(row["accepted_mutating_actions"] for row in rows)
    page = html_path.read_text()
    assert "Runs completed" in page and "2/2" in page


def test_resume_skips_complete_and_failures_do_not_stop_the_sweep(tmp_path):
    cfg = _cfg(tmp_path, vary={"model.name": ["a", "b"]}, seeds=[1], ticks=2)
    runs = sw.expand(cfg)
    calls: list[str] = []

    def flaky(run, conc):
        calls.append(run.run_id)
        if run.condition == "a":
            raise RuntimeError("boom")
        return offline_launcher(run, conc)

    first = sw.run_sweep(cfg, runs, launcher=flaky, out=lambda _: None)
    assert first.exit_code == 1
    assert first.entries["a/seed-1"]["status"] == "failed"
    assert "boom" in first.entries["a/seed-1"]["error"]
    assert first.entries["b/seed-1"]["status"] == "complete"

    calls.clear()
    second = sw.run_sweep(cfg, runs, launcher=offline_launcher_recording(calls),
                          out=lambda _: None)
    assert calls == ["a/seed-1"], "only the failed run is retried"
    assert second.exit_code == 0


def offline_launcher_recording(calls):
    def launch(run, conc):
        calls.append(run.run_id)
        return offline_launcher(run, conc)
    return launch


def test_stalled_exit_code_is_recorded(tmp_path):
    cfg = _cfg(tmp_path, seeds=[1], ticks=2)
    res = sw.run_sweep(cfg, sw.expand(cfg), launcher=lambda r, c: 3, out=lambda _: None)
    assert res.entries["baseline/seed-1"]["status"] == "stalled"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_dry_run_quick_mode(tmp_path, capsys):
    code = sw.main(["--scenario", STARTER, "--vary", "model.name=a,b",
                    "--vary", "constitution.quorum=0.5,0.75", "--seeds", "1,2",
                    "--output", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "8 = 4 condition(s) x 2 seed(s)" in out
    assert "all 8 scenario(s) valid" in out
    assert not (tmp_path / sw.MANIFEST_NAME).exists(), "dry run writes nothing"


def test_cli_refuses_to_start_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="--yes"):
        sw.main(["--scenario", STARTER, "--output", str(tmp_path)])


@pytest.mark.parametrize("name", ["model_comparison.yaml", "governance_grid.yaml"])
def test_shipped_example_sweeps_validate(name, tmp_path, capsys):
    path = REPO_ROOT / "examples" / "sweeps" / name
    assert sw.main([str(path), "--output", str(tmp_path), "--dry-run"]) == 0
    assert "scenario(s) valid" in capsys.readouterr().out


def test_verdi_cli_routes_sweep(capsys, tmp_path):
    from verdi.cli import main
    assert main(["sweep", "--scenario", STARTER, "--output", str(tmp_path), "--dry-run"]) == 0
    assert "Sweep:" in capsys.readouterr().out
