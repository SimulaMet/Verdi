"""Offline tests for P0-D primary outcome metrics + comparative report.

Runs REAL offline paired campaigns (scripted fake caller, small ticks —
same pattern as tests/experiments/test_paired_run.py) into tmp_path and
asserts every metric function returns well-formed, auditable values from
the recorded artifacts alone. A second "never recovers" scenario (shock
wipes everything, production credit-starved) exercises recovery-time
censoring end to end.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from verdi.agents.client import ModelResult
from verdi.analysis.comparative_report import write_comparison_html
from verdi.analysis.outcome_metrics import (
    RunArtifactError,
    completion_rate,
    exclusion_episodes,
    gini,
    institutional_mechanism,
    recovery_time,
    resource_sharing,
    summarize_pair,
    write_metrics_csv,
    write_metrics_json,
)
from verdi.experiments.paired_run import load_paired_config, run_pair

AGENTS = ("alice", "bob", "chen", "dave")


# --------------------------------------------------------------------------- #
# scenarios + scripted callers
# --------------------------------------------------------------------------- #

def _agent(agent_id: str, role: str, edges: list[str]) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "role": role,
        "persona_summary": f"{role} in the outcome-metrics test world.",
        "declared_values": {"integrity": 0.8, "fairness": 0.8,
                             "cooperation": 0.7, "autonomy": 0.5, "solidarity": 0.7},
        "resources": {"food": 4.0, "credits": 6.0},
        "social_edges": edges,
    }


def _tiny_scenario() -> dict[str, Any]:
    """Shock at t5, delivery due t8, ONE assembly at tick 3, 10 ticks total."""
    return {
        "world_id": "outcome_metrics_test_world",
        "run_seed": 42,
        "law_version": "universal-law-1",
        "constitution_version": "base-constitution-v1",
        "total_ticks": 10,
        "inner_life": False,
        "assemblies": [3],
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
        # World food grows ~1/tick pre-shock (3 producers net of consumption);
        # amount 2 is achievable -> the baseline delivery COMPLETES, giving a
        # defined pre-shock success rate of 1.0 for recovery_time tests.
        "tasks": {
            "deliveries": [{
                "objective_id": "recovery-delivery",
                "start_tick": 0,
                "due_tick": 8,
                "resource": "food",
                "amount": 2.0,
                "target_pool": "world",
            }],
        },
        "world_resources": {"food": 0.0, "credits": 20.0},
        "agents": [
            _agent("alice", "farmer", ["bob"]),
            _agent("bob", "logistics_worker", ["alice"]),
            _agent("chen", "community_guard", ["dave"]),
            _agent("dave", "repair_worker", ["chen"]),
        ],
    }


def _starved_scenario() -> dict[str, Any]:
    """A shock that wipes ALL shared food AND burns most credits; with only
    ~2 credits left nobody can afford production (cost 1.0/food), so the
    post-shock delivery is objectively unachievable -> recovery must censor.
    No assemblies (keeps the run cheap)."""
    return {
        "world_id": "outcome_metrics_starved_world",
        "run_seed": 42,
        "law_version": "universal-law-1",
        "constitution_version": "base-constitution-v1",
        "total_ticks": 12,
        "inner_life": False,
        "assemblies": [],
        "shocks": [
            {"tick": 4, "resource": "food", "delta": -50.0},
            {"tick": 4, "resource": "credits", "delta": -22.0},
        ],
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
            "deliveries": [
                # ~1 world-food/tick pre-shock (production minus consumption);
                # 2 is achievable -> baseline rate 1.0 by construction.
                {"objective_id": "pre-baseline", "start_tick": 0, "due_tick": 3,
                 "resource": "food", "amount": 2.0, "target_pool": "world"},
                # Post-shock: the credit pool is burned to zero at t4 and no
                # agent has credits left, so production is impossible forever
                # -> objectively unachievable, recovery must censor.
                {"objective_id": "post-recovery", "start_tick": 4, "due_tick": 9,
                 "resource": "food", "amount": 6.0, "target_pool": "world"},
            ],
        },
        "world_resources": {"food": 0.0, "credits": 24.0},
        "agents": [
            _agent("alice", "farmer", ["bob"]),
            _agent("bob", "logistics_worker", ["alice"]),
            _agent("chen", "community_guard", ["dave"]),
            _agent("dave", "repair_worker", ["chen"]),
        ],
    }


class ScriptedCaller:
    """Deterministic fake ModelCaller.

    * alice/bob/chen produce up to 2 food per tick; dave deposits 1 food when
      he has any (else produces);
    * assembly: alice proposes max_withdraw_units=2 effective now; all vote
      "for" (enacted in revisable, rejected by institution_fixed_rules in fixed);
    * probes/reflections/orchestrator: minimal deterministic replies.
    """

    def __init__(self, *, propose: bool = True) -> None:
        self.n = 0
        self.propose = propose

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
            if self.propose and voter == "alice":
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


def _write_base(tmp_path: Path, scenario: dict[str, Any]) -> Path:
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump(scenario, sort_keys=True), encoding="utf-8")
    return base


def _write_config(tmp_path: Path, base: Path, out_root: Path) -> Path:
    cfg = {
        "experiment_id": "outcome-metrics-test",
        "base_scenario": str(base),
        "arms": ["fixed", "revisable"],
        "seeds": [42],
        "total_ticks_override": None,
        "output_root": str(out_root),
    }
    path = tmp_path / "paired.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def pair_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real offline paired run (fixed vs revisable, seed 42)."""
    tmp = tmp_path_factory.mktemp("pair")
    base_file = _write_base(tmp, _tiny_scenario())
    out_root = tmp / "out"
    config = load_paired_config(_write_config(tmp, base_file, out_root))
    result = run_pair(config, lambda sc, rd: ScriptedCaller())
    assert result.exit_code == 0, "offline paired run failed"
    return out_root


@pytest.fixture(scope="module")
def starved_pair_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Offline paired run whose post-shock economy cannot recover (censors)."""
    tmp = tmp_path_factory.mktemp("starved")
    base_file = _write_base(tmp, _starved_scenario())
    out_root = tmp / "out"
    config = load_paired_config(_write_config(tmp, base_file, out_root))
    result = run_pair(config, lambda sc, rd: ScriptedCaller(propose=False))
    assert result.exit_code == 0, "offline starved paired run failed"
    return out_root


def _arm_dir(root: Path, arm: str, seed: int = 42) -> Path:
    return root / f"{arm}-seed{seed}"


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #

def test_gini_deterministic_bounds():
    assert gini([1.0, 1.0, 1.0]) == 0.0
    assert gini([]) is None
    assert gini([0.0, 5.0]) is not None
    n = 4
    assert abs(gini([0.0, 0.0, 0.0, 1.0]) - (n - 1) / n) < 1e-12


# --------------------------------------------------------------------------- #
# per-run metric functions on real artifacts
# --------------------------------------------------------------------------- #

def test_completion_rate_well_formed(pair_root):
    comp = completion_rate(_arm_dir(pair_root, "fixed"))
    assert comp["evaluated"] >= 1
    assert comp["completed"] + comp["failed"] == comp["evaluated"]
    assert 0.0 <= comp["overall"] <= 1.0
    for obj in comp["objectives"]:
        assert set(obj) == {"objective_id", "status", "required", "baseline",
                            "actual", "delta", "due_tick", "ledger_tick",
                            "evidence_event_ids"}
        assert obj["status"] in ("completed", "failed")
        assert obj["evidence_event_ids"], "every objective carries ledger evidence"


def test_resource_sharing_well_formed(pair_root):
    sharing = resource_sharing(_arm_dir(pair_root, "revisable"))
    # The scripted depositor commits at least one deposit of food.
    assert "food" in sharing["committed"]
    bucket = sharing["committed"]["food"]
    assert bucket["total"] > 0.0
    assert bucket["total"] == pytest.approx(bucket["transfer"] + bucket["deposit"])
    assert len(set(bucket["evidence_event_ids"])) == len(bucket["evidence_event_ids"])
    # Reserve levels exist for both resources and are non-decreasing in tick.
    for series in sharing["reserve_levels"].values():
        ticks = [p[0] for p in series]
        assert ticks == sorted(ticks)
    fin = sharing["final_inequality"]
    # Primary resource = largest final world-pool balance (here credits, the
    # shared reserve; food ends up in agent holdings after deposits).
    assert fin["primary_resource"] in ("food", "credits")
    assert fin["tick"] is not None
    assert set(fin["holdings"]) == set(AGENTS)
    if fin["gini"] is not None:
        assert 0.0 <= fin["gini"] <= 1.0


def _assert_dave_episode(entry: dict[str, Any]) -> None:
    """Dave's single overdraft sanction: one closed episode, restored by his
    timely (pre-registered) appeal before duration expiry."""
    assert len(entry["episodes"]) == 1
    ep = entry["episodes"][0]
    assert ep["sanction_id"].startswith("san-soc-dave-")
    assert ep["effective_tick"] >= 0 and ep["restored_tick"] is not None
    assert ep["restored_tick"] > ep["effective_tick"]
    assert entry["excluded_ticks"] == ep["restored_tick"] - ep["effective_tick"]
    assert entry["repeat_episode"] is False


def test_exclusion_episodes_well_formed(pair_root):
    """The scripted depositor (dave) over-deposits when short on food — a
    substantive overdraft that sanctions him; the episode must be derived
    exactly from the sanctions stream, with the appeal-upheld restoration."""
    excl = exclusion_episodes(_arm_dir(pair_root, "fixed"))
    assert set(excl["agents"]) == {"dave"}
    _assert_dave_episode(excl["agents"]["dave"])
    # The same appeal lifecycle appears in totals.
    assert excl["totals"]["sanctions_imposed"] == 1
    assert excl["totals"]["appeals_filed"] == 1
    assert excl["totals"]["appeals_upheld"] == 1
    assert excl["totals"]["restorations"] == 1


def test_exclusion_well_formed_in_starved_run(starved_pair_root):
    """The starved world also sanctions dave once (overdraft); the episode
    record must be well-formed there too. Repeat-episode semantics are unit
    -tested below with a synthetic sanctions stream instead of depending on
    run dynamics."""
    excl = exclusion_episodes(_arm_dir(starved_pair_root, "fixed"))
    assert set(excl["agents"]) == {"dave"}
    _assert_dave_episode(excl["agents"]["dave"])


def test_exclusion_repeat_episode_flag_synthetic(tmp_path):
    """Repeat-episode flag: two distinct sanctioned intervals for one agent
    (impose -> restore -> impose) must yield repeat_episode=True and summed
    excluded ticks; an OPEN second episode counts to the last ledger tick."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = [
        # ledger ticks only need to exist for the open-episode bound.
        {"event_id": i, "tick": t, "kind": "action_accepted",
         "payload": {}, "actor_id": "a1"}
        for i, t in enumerate(range(0, 12))
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n")
    sanctions = [
        {"record_type": "sanction", "kind": "sanction_imposed",
         "decision": "accepted", "subject_id": "a1", "sanction_id": "s1",
         "tick": 2, "duration_ticks": 3},
        {"record_type": "restoration", "sanction_id": "s1", "tick": 5},
        {"record_type": "appeal", "kind": "appeal_filed", "phase": "filed",
         "sanction_id": "s1", "tick": 4},
        {"record_type": "sanction", "kind": "sanction_imposed",
         "decision": "accepted", "subject_id": "a1", "sanction_id": "s2",
         "tick": 8, "duration_ticks": 5},
        # s2 never restored -> open until last ledger tick (11).
    ]
    (run_dir / "sanctions.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sanctions) + "\n")

    excl = exclusion_episodes(run_dir)
    entry = excl["agents"]["a1"]
    assert len(entry["episodes"]) == 2
    assert entry["episodes"][0]["restored_tick"] == 5
    assert entry["episodes"][1]["restored_tick"] is None
    assert entry["excluded_ticks"] == (5 - 2) + (11 - 8)
    assert entry["repeat_episode"] is True
    assert excl["totals"]["sanctions_imposed"] == 2
    assert excl["totals"]["appeals_filed"] == 1
    assert excl["totals"]["restorations"] == 1


def test_exclusion_blocks_transfer_deposit_in_ledger(pair_root):
    """While dave is under sanction his deposit attempts are rejected with
    law:under_sanction — the metric's 'excluded from' definition is real."""
    from verdi.analysis.outcome_metrics import _read_jsonl

    events = _read_jsonl(_arm_dir(pair_root, "fixed"), "events")
    under_sanction_rejects = [
        ev for ev in events
        if ev.get("kind") == "action_rejected"
        and (ev.get("payload") or {}).get("blocked_by") == "law:under_sanction"
    ]
    assert under_sanction_rejects, "sanctioned agent's blocked flow is ledgered"
    for ev in under_sanction_rejects:
        assert ev["actor_id"] == "dave"
        assert (ev.get("payload") or {}).get("action_kind") in ("transfer", "deposit")


def test_institutional_mechanism_diverges_between_arms(pair_root):
    fixed = institutional_mechanism(_arm_dir(pair_root, "fixed"))
    revisable = institutional_mechanism(_arm_dir(pair_root, "revisable"))

    # Both arms saw the identical proposal and votes (identical exogenous
    # circumstances)...
    assert len(fixed["proposals"]) == 1
    assert revisable["proposals"][0]["submitted_by"] == "alice"
    assert fixed["votes"] == revisable["votes"]
    assert fixed["votes"]["for"] == 4  # all four voters voted "for"
    assert pytest.approx(fixed["turnout"]["mean_turnout"]) == 1.0
    assert fixed["turnout"]["eligible_voters"] == 4

    # ...but ONLY the revisable arm enacted (fixed arm's engine rejects it).
    assert fixed["enactments"] == []
    assert len(revisable["enactments"]) == 1
    enactment = revisable["enactments"][0]
    assert enactment["rule_deltas"]["added"].get("max_withdraw_units") == 2
    assert revisable["amendment_enacted_event_ids"], "enactment carries ledger ids"
    assert fixed["amendment_rejected_event_ids"], \
        "fixed arm's rejection is ledgered with event ids"
    # Turnout/evidence fields are present and well-typed.
    assert isinstance(fixed["blocked_actions_by_code"], dict)
    for code, count in fixed["blocked_actions_by_code"].items():
        assert count >= 1
        assert code in fixed["blocked_action_evidence"]


def test_recovery_time_recovers_after_shock(starved_pair_root):
    """In the starved world the PRE-shock delivery objectively completes
    (delta 2 >= required 2) while the post-shock one fails (credit pool
    burned). A preregistration whose baseline covers only the completed
    objective gets baseline 1.0; with threshold 0.5x and sustained 1 tick,
    the failed post-shock outcome never qualifies, so this run must CENSOR —
    and a lenient-enough criterion on a run whose post-shock delivery DID
    pass would recover at its due tick (asserted in the pair root below)."""
    rec = recovery_time(
        _arm_dir(starved_pair_root, "fixed"),
        pre_shock_window=(0, 3),
        post_shock_start=4,
        target_fraction=0.5,
        sustained_ticks=1,
    )
    assert rec["pre_shock_rate"] == 1.0
    assert rec["recovered"] is False and rec["censored"] is True
    assert rec["per_tick_rates"].get("9") == 0.0, "post-shock delivery failed"


def test_recovery_time_recovers_on_completed_post_delivery(pair_root):
    """Pair-root sanity for the POSITIVE path: whatever the single delivery's
    outcome, the metric must agree exactly with the ledger (rate 1.0 iff the
    objective completed) and, when it did complete, report recovery at the
    objective's due tick."""
    comp = completion_rate(_arm_dir(pair_root, "fixed"))
    obj = comp["objectives"][0]
    rec = recovery_time(
        _arm_dir(pair_root, "fixed"),
        pre_shock_window=(0, obj["due_tick"]),
        post_shock_start=obj["due_tick"],
        target_fraction=1.0,
        sustained_ticks=1,
    )
    expected = 1.0 if obj["status"] == "completed" else 0.0
    assert rec["pre_shock_rate"] == expected
    if obj["status"] == "completed":
        assert rec["recovered"] is True and rec["censored"] is False
        assert rec["recovery_tick"] == obj["due_tick"]
    else:
        assert rec["recovered"] is False and rec["censored"] is True
    assert rec["evidence_event_ids"]


def test_recovery_time_undefined_baseline_censors(pair_root):
    """A preregistration whose baseline window contains NO due objectives has
    an undefined baseline: the criterion cannot be met, so the run censors
    (not an error)."""
    rec = recovery_time(
        _arm_dir(pair_root, "fixed"),
        pre_shock_window=(0, 4),
        post_shock_start=6,
        target_fraction=0.5,
        sustained_ticks=1,
    )
    assert rec["pre_shock_rate"] is None
    assert rec["recovered"] is False and rec["censored"] is True
    assert rec["recovery_tick"] is None
    assert rec["baseline_window"] == [0, 4]


def test_recovery_time_rejects_bad_preregistration(pair_root):
    with pytest.raises(ValueError):
        recovery_time(_arm_dir(pair_root, "fixed"), pre_shock_window=(3, 1),
                      post_shock_start=2, target_fraction=0.5, sustained_ticks=1)
    with pytest.raises(ValueError):
        recovery_time(_arm_dir(pair_root, "fixed"), pre_shock_window=(0, 8),
                      post_shock_start=2, target_fraction=0.0, sustained_ticks=1)
    with pytest.raises(ValueError):
        recovery_time(_arm_dir(pair_root, "fixed"), pre_shock_window=(0, 8),
                      post_shock_start=2, target_fraction=0.5, sustained_ticks=0)


def test_recovery_time_censors_on_never_recovering_run(starved_pair_root):
    """The starved scenario's shock wipes all shared food AND burns the credit
    pool to zero; with no credits left, production is impossible forever, so
    the post-shock delivery objectively fails and recovery must censor."""
    rec = recovery_time(
        _arm_dir(starved_pair_root, "fixed"),
        pre_shock_window=(0, 3),
        post_shock_start=5,
        target_fraction=0.5,
        sustained_ticks=2,
    )
    assert rec["pre_shock_rate"] == 1.0, "pre-shock baseline delivery succeeded"
    assert rec["recovered"] is False
    assert rec["censored"] is True
    assert rec["recovery_tick"] is None
    assert rec["per_tick_rates"].get("9") == 0.0, \
        "post-shock delivery objectively failed (credit-starved)"
    assert rec["evidence_event_ids"]


# --------------------------------------------------------------------------- #
# paired summary + exports
# --------------------------------------------------------------------------- #

def test_summarize_pair_shape_and_evidence(pair_root):
    summary = summarize_pair(pair_root)
    assert summary["experiment_id"] == "outcome-metrics-test"
    assert summary["paired_seeds"] == [42]
    assert summary["skipped"] == []
    for arm in ("fixed", "revisable"):
        entry = summary["arms"][arm]["42"]
        assert entry["arm"] == arm and entry["seed"] == 42
        assert entry["replay_matches"] is True
        assert entry["final_state_hash"]
        # Arms genuinely diverged: revisable enacted, fixed did not.
    assert summary["arms"]["revisable"]["42"]["institutional_mechanism"]["enactments"]
    assert not summary["arms"]["fixed"]["42"]["institutional_mechanism"]["enactments"]


def test_write_metrics_json_roundtrip(tmp_path, pair_root):
    summary = summarize_pair(pair_root)
    path = write_metrics_json(summary, tmp_path / "metrics.json")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == summary
    # Deterministic: rewriting yields byte-identical output.
    again = write_metrics_json(summary, tmp_path / "metrics2.json")
    assert path.read_bytes() == again.read_bytes()


def test_write_metrics_csv_long_format(tmp_path, pair_root):
    summary = summarize_pair(pair_root)
    path = write_metrics_csv(summary, tmp_path / "metrics.csv")
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "CSV must contain data rows"
    expected_cols = ["arm", "seed", "metric", "value", "evidence_ref"]
    assert list(rows[0].keys()) == expected_cols
    metrics_seen = {r["metric"] for r in rows}
    assert "completion.overall" in metrics_seen
    assert any(m.startswith("institution.blocked[") for m in metrics_seen) or \
        "institution.enactments" in metrics_seen
    assert "sharing.committed[food]" in metrics_seen
    # Every completion aggregate row carries non-empty ledger evidence.
    completion_rows = [r for r in rows if r["metric"].startswith("completion")]
    assert completion_rows and all(r["evidence_ref"] for r in completion_rows)
    # Optional cross-check: if pandas happens to be installed, the CSV must also
    # parse through it. pandas is NOT a declared dependency, so skip otherwise
    # (the stdlib csv checks above already validate structure and content).
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")
    else:
        frame = pd.read_csv(path)
        assert list(frame.columns) == expected_cols
        assert frame["arm"].isin(["fixed", "revisable"]).all()


def test_comparison_html_contains_both_arms(tmp_path, pair_root):
    summary = summarize_pair(pair_root)
    out = write_comparison_html(summary, tmp_path / "comparison.html")
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "fixed" in text and "revisable" in text
    assert "CENSORED" in text or "Recovery time" in text
    # Both arm sections render their run dir and an audit hint.
    assert "fixed-seed42" in text and "revisable-seed42" in text
    assert "ledger events:" in text or "sanctions.jsonl" in text
    # Deterministic: same summary -> byte-identical document.
    out2 = write_comparison_html(summary, tmp_path / "comparison2.html")
    assert out.read_bytes() == out2.read_bytes()


def test_missing_run_dir_raises_friendly_error(tmp_path):
    with pytest.raises(RunArtifactError):
        completion_rate(tmp_path / "nope")
    with pytest.raises(RunArtifactError):
        summarize_pair(tmp_path)  # no pair_manifest.json


def test_main_cli_writes_three_files(tmp_path, pair_root):
    from verdi.analysis import outcome_metrics

    exit_code = outcome_metrics.main(["--pair-root", str(pair_root)])
    assert exit_code == 0
    for name in ("metrics.json", "metrics.csv", "comparison.html"):
        assert (pair_root / name).exists(), f"{name} missing after CLI run"
    # CSV produced by the CLI loads with the documented columns.
    with (pair_root / "metrics.csv").open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == ["arm", "seed", "metric", "value", "evidence_ref"]
