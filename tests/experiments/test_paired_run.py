"""Offline tests for the P0-C paired fixed-vs-revisable runner.

Uses a scripted fake caller (pattern from tests/society/test_campaign.py):
producers produce food, depositors top up the commons, assembly voters vote
"for" so the revisable arm can enact an amendment; the fixed arm's identical
vote is deterministically rejected by the engine (institution_fixed_rules) —
so the two arms genuinely diverge while sharing every exogenous input.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from verdi_agents.agents.client import ModelResult
from verdi_agents.experiments.paired_run import (
    build_arm_scenarios,
    load_paired_config,
    run_pair,
)

AGENTS = ("alice", "bob", "chen", "dave")


def _tiny_scenario() -> dict[str, Any]:
    """Small constrained-economy scenario: shock at t5, one delivery due t8,
    ONE assembly at tick 3, 10 ticks total. Cheap enough for offline pairs."""

    def agent(agent_id: str, role: str, edges: list[str]) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "role": role,
            "persona_summary": f"{role} in the paired-runner test world.",
            "declared_values": {"integrity": 0.8, "fairness": 0.8,
                                 "cooperation": 0.7, "autonomy": 0.5,
                                 "solidarity": 0.7},
            "resources": {"food": 4.0, "credits": 6.0},
            "social_edges": edges,
        }

    return {
        "world_id": "paired_runner_test_world",
        "run_seed": 42,
        "law_version": "universal-law-1",
        "constitution_version": "base-constitution-v1",
        "total_ticks": 10,
        "inner_life": False,
        "assemblies": [3],
        # Deliberate pre-existing asymmetry between agents (not between arms)
        # to prove the identity assertion compares whole dicts, not shapes.
        "shocks": [{"tick": 5, "resource": "food", "delta": -50.0}],
        "constitution": {
            "quorum": 0.5,
            "amendment_threshold": 0.67,
            "rules": {"quorum_rule": "simple_majority_of_eligible"},
        },
        "economy": {
            "version": 1,
            "production_limit_per_tick": {"food": 2.0},
            "production_cost": {"food": 1.0},
            "consumption_per_tick": {"food": 1.0},
        },
        "tasks": {
            "deliveries": [{
                "objective_id": "recovery-delivery",
                "start_tick": 0,
                "due_tick": 8,
                "resource": "food",
                "amount": 4.0,
                "target_pool": "world",
            }],
        },
        "world_resources": {"food": 0.0, "credits": 20.0},
        "agents": [
            agent("alice", "farmer", ["bob"]),
            agent("bob", "logistics_worker", ["alice"]),
            agent("chen", "community_guard", ["dave"]),
            agent("dave", "repair_worker", ["chen"]),
        ],
    }


class ScriptedPairedCaller:
    """Deterministic fake ModelCaller for paired runs.

    * producers (alice/bob/chen): produce 2 food per tick (at the cap);
    * depositor (dave): deposits 1 food when he has it, else produces;
    * assembly deliberation: alice proposes max_withdraw_units=2 effective at
      the assembly tick; everyone votes "for" — enacted in the revisable arm,
      deterministically REJECTED (institution_fixed_rules) in the fixed arm;
    * probes/reflection/orchestrator: minimal deterministic replies.
    """

    def __init__(self) -> None:
        self.n = 0

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.n += 1
        content = "\n".join(m["content"] for m in messages)
        trace = f"trace-{self.n}"

        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            tick = int(content.split("at tick ")[1].split(".")[0])
            if voter == "alice":
                payload = {
                    "change_summary": "Cap single withdrawals at 2 units",
                    "payload": {"max_withdraw_units": 2,
                                "effective_from_tick": tick},
                }
            else:
                payload = {"abstain": True}
            return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "assembly:vote" in content:
            return ModelResult(content=json.dumps({"choice": "for"}),
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "probe" in content.lower():
            return ModelResult(content="yes", request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "verdi.reflection" in content or "verdi.consolidation" in content:
            return ModelResult(content="Adjusted based on committed outcomes.",
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "Assign one task per person" in content:
            # Orchestrator tape: keep it trivially valid; the campaign clamps.
            return ModelResult(content=json.dumps({a: {"action_type": "produce"}
                                                   for a in AGENTS}),
                               request_id=f"r{self.n}", model="fake", trace_id=trace)

        actor = next(aid for aid in AGENTS if f'agent "{aid}"' in content)
        if actor == "dave":
            payload = {"action_kind": "deposit",
                       "arguments": {"resource": "food", "amount": 1.0},
                       "reason": "top up the commons"}
        else:
            payload = {"action_kind": "produce",
                       "arguments": {"resource": "food", "amount": 2.0},
                       "reason": "grow food for the commons"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}",
                           model="fake", trace_id=trace)


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #

def _write_base(tmp_path: Path) -> Path:
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump(_tiny_scenario(), sort_keys=True), encoding="utf-8")
    return base


def _write_config(tmp_path: Path, base: Path, out_root: Path,
                  extra: dict[str, Any] | None = None) -> Path:
    cfg = {
        "experiment_id": "paired-test",
        "base_scenario": str(base),
        "arms": ["fixed", "revisable"],
        "seeds": [42],
        "total_ticks_override": None,
        "output_root": str(out_root),
    }
    if extra:
        cfg.update(extra)
    path = tmp_path / "paired.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")
    return path


def _entry(manifest: dict[str, Any], seed: int, arm: str) -> dict[str, Any]:
    matches = [e for e in manifest["runs"] if e.get("seed") == seed and e.get("arm") == arm]
    assert len(matches) == 1, f"expected exactly one manifest entry for ({seed}, {arm})"
    return matches[0]


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_pair_runs_both_arms_with_identical_scenarios(tmp_path):
    base_file = _write_base(tmp_path)
    out_root = tmp_path / "out"
    config = load_paired_config(_write_config(tmp_path, base_file, out_root))

    result = run_pair(config, lambda sc, rd: ScriptedPairedCaller())

    # Both run dirs exist with frozen resolved scenarios.
    fixed_dir = out_root / "fixed-seed42"
    revisable_dir = out_root / "revisable-seed42"
    assert fixed_dir.is_dir() and revisable_dir.is_dir()
    fixed_resolved = yaml.safe_load((fixed_dir / "scenario_resolved.yaml").read_text())
    rev_resolved = yaml.safe_load((revisable_dir / "scenario_resolved.yaml").read_text())

    # The resolved scenarios differ ONLY in institutional_condition.
    assert fixed_resolved["institutional_condition"] == "fixed"
    assert rev_resolved["institutional_condition"] == "revisable"
    minus = {k: v for k, v in fixed_resolved.items() if k != "institutional_condition"}
    minus_rev = {k: v for k, v in rev_resolved.items() if k != "institutional_condition"}
    assert minus == minus_rev

    # Manifest lists both arms complete with replay matching.
    manifest = json.loads((out_root / "pair_manifest.json").read_text())
    assert manifest["experiment_id"] == "paired-test"
    for arm in ("fixed", "revisable"):
        entry = _entry(manifest, 42, arm)
        assert entry["status"] == "complete", entry
        assert entry["replay_matches"] is True, entry
        assert entry["final_state_hash"]

    assert result.complete is True
    assert result.exit_code == 0

    # Arms genuinely diverged: the same "for" votes enact in revisable but are
    # rejected (institution_fixed_rules) in the fixed arm, so final states differ.
    fixed_final = json.loads((fixed_dir / "snapshots" / "after_tick09.json").read_text())
    rev_final = json.loads((revisable_dir / "snapshots" / "after_tick09.json").read_text())
    assert fixed_final != rev_final


def test_partial_failure_recorded_not_fatal(tmp_path):
    base_file = _write_base(tmp_path)
    out_root = tmp_path / "out"
    config = load_paired_config(_write_config(tmp_path, base_file, out_root))

    def factory(scenario: dict[str, Any], run_dir: Path) -> Any:
        if scenario["institutional_condition"] == "fixed":
            raise RuntimeError("simulated endpoint outage (fixed arm only)")
        return ScriptedPairedCaller()

    # Must NOT raise despite one arm failing.
    result = run_pair(config, factory)

    manifest = json.loads((out_root / "pair_manifest.json").read_text())
    fixed_entry = _entry(manifest, 42, "fixed")
    rev_entry = _entry(manifest, 42, "revisable")
    assert fixed_entry["status"] == "failed"
    assert "simulated endpoint outage" in fixed_entry["error"]
    assert rev_entry["status"] == "complete"
    assert rev_entry["replay_matches"] is True
    assert result.exit_code != 0
    assert result.complete is False


def test_resume_skips_complete(tmp_path):
    base_file = _write_base(tmp_path)
    out_root = tmp_path / "out"
    config = load_paired_config(_write_config(tmp_path, base_file, out_root))

    counters: dict[str, int] = {}

    def factory(scenario: dict[str, Any], run_dir: Path) -> Any:
        counters[scenario["institutional_condition"]] = \
            counters.get(scenario["institutional_condition"], 0) + 1
        return ScriptedPairedCaller()

    first = run_pair(config, factory)
    assert first.exit_code == 0
    after_first = dict(counters)
    assert after_first.get("fixed", 0) >= 1 and after_first.get("revisable", 0) >= 1

    second = run_pair(config, factory)
    assert second.exit_code == 0
    # No pair was re-executed: the call counter did not move at all.
    assert counters == after_first

    manifest = json.loads((out_root / "pair_manifest.json").read_text())
    for arm in ("fixed", "revisable"):
        assert _entry(manifest, 42, arm)["status"] == "complete"


def test_resume_reruns_failed_pairs(tmp_path):
    base_file = _write_base(tmp_path)
    out_root = tmp_path / "out"
    config = load_paired_config(_write_config(tmp_path, base_file, out_root))

    state = {"fail_fixed": True}

    def factory(scenario: dict[str, Any], run_dir: Path) -> Any:
        if state["fail_fixed"] and scenario["institutional_condition"] == "fixed":
            raise RuntimeError("transient failure")
        return ScriptedPairedCaller()

    first = run_pair(config, factory)
    assert first.exit_code != 0

    state["fail_fixed"] = False
    second = run_pair(config, factory)
    assert second.exit_code == 0
    manifest = json.loads((out_root / "pair_manifest.json").read_text())
    assert _entry(manifest, 42, "fixed")["status"] == "complete"
    assert _entry(manifest, 42, "revisable")["status"] == "complete"


def test_validation_errors_friendly(tmp_path):
    base_file = _write_base(tmp_path)

    # Unknown key -> SystemExit naming the offending key, no traceback text.
    bad_cfg = tmp_path / "bad_key.yaml"
    bad_cfg.write_text(yaml.safe_dump({
        "experiment_id": "x",
        "base_scenario": str(base_file),
        "arms": ["fixed", "revisable"],
        "seeds": [42],
        "totally_unknown_key": 1,
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        load_paired_config(bad_cfg)
    msg = str(excinfo.value)
    assert "totally_unknown_key" in msg
    assert "Traceback" not in msg

    # Missing base_scenario file.
    missing = tmp_path / "missing_base.yaml"
    missing.write_text(yaml.safe_dump({
        "experiment_id": "x",
        "base_scenario": str(tmp_path / "nope.yaml"),
        "arms": ["fixed", "revisable"],
        "seeds": [42],
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        load_paired_config(missing)
    assert "nope.yaml" in str(excinfo.value)
    assert "Traceback" not in str(excinfo.value)

    # Invalid arm name.
    bad_arm = tmp_path / "bad_arm.yaml"
    bad_arm.write_text(yaml.safe_dump({
        "experiment_id": "x",
        "base_scenario": str(base_file),
        "arms": ["fixed", "mutable"],
        "seeds": [42],
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        load_paired_config(bad_arm)
    msg = str(excinfo.value)
    assert "'mutable'" in msg
    assert "Traceback" not in msg


def test_build_arm_scenarios_asserts_identity():
    base = _tiny_scenario()
    built = build_arm_scenarios(base, ["fixed", "revisable"])
    assert built["fixed"]["institutional_condition"] == "fixed"
    assert built["revisable"]["institutional_condition"] == "revisable"
    # Base untouched (deep copy): no key was added to the original dict.
    assert "institutional_condition" not in base

    # total_ticks_override applies identically to both arms.
    built2 = build_arm_scenarios(base, ["fixed", "revisable"], total_ticks_override=7)
    assert built2["fixed"]["total_ticks"] == 7
    assert built2["revisable"]["total_ticks"] == 7
    minus_a = {k: v for k, v in built2["fixed"].items() if k != "institutional_condition"}
    minus_b = {k: v for k, v in built2["revisable"].items() if k != "institutional_condition"}
    assert minus_a == minus_b


def test_cli_main_validates_before_endpoint(tmp_path, monkeypatch):
    """main() must reject a bad config BEFORE any endpoint resolution."""
    from verdi_agents.experiments import paired_run

    base_file = _write_base(tmp_path)
    bad_cfg = tmp_path / "bad.yaml"
    bad_cfg.write_text(yaml.safe_dump({
        "experiment_id": "x",
        "base_scenario": str(base_file),
        "arms": ["broken-arm"],
        "seeds": [42],
    }), encoding="utf-8")

    def boom():
        raise AssertionError("endpoint resolved despite invalid config")

    monkeypatch.setattr(paired_run, "_real_caller_factory", boom)
    with pytest.raises(SystemExit) as excinfo:
        paired_run.main(["--config", str(bad_cfg)])
    assert "broken-arm" in str(excinfo.value)


def test_example_config_is_valid_and_self_consistent():
    """The shipped example config parses and its base scenario exists."""
    repo_root = Path(__file__).resolve().parents[2]
    example = repo_root / "examples" / "paired_institutional_recovery.yaml"
    assert example.exists(), "shipped example config is missing"
    config = load_paired_config(example)
    assert config["arms"] == ["fixed", "revisable"]
    assert config["seeds"] == [42]
    assert (repo_root / config["base_scenario"]).exists()
    # Identity holds for the real smoke scenario too.
    from verdi_agents.analysis.scenario import load_scenario
    base = load_scenario(repo_root / config["base_scenario"])
    built = build_arm_scenarios(base, config["arms"])
    minus_a = {k: v for k, v in built["fixed"].items() if k != "institutional_condition"}
    minus_b = {k: v for k, v in built["revisable"].items() if k != "institutional_condition"}
    assert minus_a == minus_b
