"""Unit tests for matched pairs and the tape hash helper."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tests.world.helpers import initial_world

from verdi_agents.experiments.pairs import MatchedPair, make_matched_pair, tape_hash
from verdi_agents.schemas import ProposedAction, ValidationResult
from verdi_agents.world.branch import BranchSpec, branch_from_snapshot
from verdi_agents.world.canonical import canonical_json
from verdi_agents.world.log import EventLog
from verdi_agents.world.reducer import WorldReducer
from verdi_agents.world.replay import TapeStep, replay_in_memory


def _prefix_log() -> EventLog:
    """A small sealed prefix ledger (deterministic) used as the origin."""
    snapshot = initial_world()
    _, _, log = replay_in_memory(snapshot, [])
    return log


def _manifest(origin_hash: str, tape_hash: str, **extra) -> dict:
    manifest = {
        "experiment_id": "exp-p2-ws2-unit",
        "origin_state_hash": origin_hash,
        "tape_hash": tape_hash,
        "branch_origin": "counterfactual-v1",
        "treatment_seed": 0,
    }
    manifest.update(extra)
    return manifest


def test_tape_hash_matches_canonical_jsonl_bytes():
    log = _prefix_log()
    expected = hashlib.sha256(log.to_jsonl_bytes()).hexdigest()
    assert tape_hash(log) == expected


def test_tape_hash_accepts_jsonl_path(tmp_path: Path):
    log = _prefix_log()
    path = tmp_path / "prefix.jsonl"
    path.write_bytes(log.to_jsonl_bytes())
    assert tape_hash(path) == tape_hash(log)


def test_tape_hash_rejects_unknown_types():
    with pytest.raises(TypeError):
        tape_hash(123)  # type: ignore[arg-type]


def test_make_matched_pair_accepts_matching_runs():
    log = _prefix_log()
    th = tape_hash(log)
    origin = "deadbeef" * 8
    treatment = _manifest(origin, th, arm="treatment")
    placebo = _manifest(origin, th, arm="placebo")
    pair = make_matched_pair(treatment, placebo)
    assert isinstance(pair, MatchedPair)
    assert pair.origin_state_hash == origin
    assert pair.tape_hash == th
    # runs are carried as detached copies (round-tripped through JSON)
    pair.treatment_run["arm"] = "mutated"
    assert treatment["arm"] == "treatment"


def test_make_matched_pair_rejects_origin_mismatch():
    log = _prefix_log()
    th = tape_hash(log)
    with pytest.raises(ValueError, match="origin_state_hash mismatch"):
        make_matched_pair(_manifest("a" * 64, th), _manifest("b" * 64, th))


def test_make_matched_pair_rejects_tape_hash_mismatch():
    log = _prefix_log()
    th = tape_hash(log)
    with pytest.raises(ValueError, match="tape hash mismatch"):
        make_matched_pair(_manifest("a" * 64, th), _manifest("a" * 64, "c" * 64))


def test_make_matched_pair_requires_identity_fields():
    with pytest.raises(ValueError, match="origin_state_hash"):
        make_matched_pair({"tape_hash": "x" * 64}, {"origin_state_hash": "a", "tape_hash": "x" * 64})
    with pytest.raises(ValueError):
        make_matched_pair("not a dict", {})  # type: ignore[arg-type]


def test_end_to_end_pair_from_branch_results():
    """Two branch results from the SAME prefix log must form a valid pair."""
    log = _prefix_log()
    snapshot = initial_world()
    origin_hash = WorldReducer.canonical_state_hash(snapshot)
    base = {
        "experiment_id": "exp-p2-ws2-unit",
        "world_id": snapshot.world_id,
        "origin_state_hash": origin_hash,
        "tape_hash": tape_hash(log),
    }
    res_t = branch_from_snapshot(snapshot, log, [], BranchSpec(
        origin_state_hash=origin_hash, origin_event_count=len(log),
        origin_tick=snapshot.tick, branch_id="treatment", treatment_seed=1,
    ))
    res_p = branch_from_snapshot(snapshot, log, [], BranchSpec(
        origin_state_hash=origin_hash, origin_event_count=len(log),
        origin_tick=snapshot.tick, branch_id="placebo", treatment_seed=0,
    ))
    from verdi_agents.world.branch import manifest_for_branch

    treatment = manifest_for_branch(res_t.spec, base)
    placebo = manifest_for_branch(res_p.spec, base)
    pair = make_matched_pair(treatment, placebo)
    assert pair.origin_state_hash == res_t.spec.origin_state_hash
    assert pair.treatment_run["branch_origin"] == "treatment"
    assert pair.placebo_run["branch_origin"] == "placebo"
    assert json.dumps([treatment, placebo])  # manifests stay JSON-serializable
    # canonical json round-trip used by the pair is stable
    assert canonical_json({"origin": pair.origin_state_hash})


def test_different_prefix_logs_give_different_tape_hashes():
    """Different ledger bytes -> different tape hash (origin-sensitive)."""
    snapshot = initial_world()
    # two different recorded attempts -> two different sealed ledgers
    _, _, log_a = replay_in_memory(
        snapshot, [_tape_step("a1", "t1")]
    )
    _, _, log_b = replay_in_memory(
        snapshot, [_tape_step("a1", "t2")]
    )
    assert tape_hash(log_a) != tape_hash(log_b)


def _tape_step(actor: str, task: str) -> TapeStep:
    action = ProposedAction(
        actor_id=actor, task_id=task, action_kind="harvest", arguments={"units": 1}
    )
    return TapeStep(action=action, validation=ValidationResult(accepted=True))
