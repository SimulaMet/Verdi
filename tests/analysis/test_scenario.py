"""Scenario loader tests: pilot_v1 fixture invariants + tamper detection."""
from __future__ import annotations

import json

import pytest
from tests.analysis.conftest import SCENARIO, TAPE

from verdi.analysis import LoadedScenario, ScenarioLoader, ScenarioLoadError, canonical_tape_hash
from verdi.schemas import ExperimentManifest


def _stored_hash() -> str:
    return json.loads(TAPE.read_text(encoding="utf-8"))["tape_hash"]


def test_pilot_scenario_shape():
    loaded: LoadedScenario = ScenarioLoader(SCENARIO, TAPE).load()
    meta = loaded.meta
    assert meta.scenario_id == "pilot_v1"
    assert meta.regime == "G3"
    assert meta.total_ticks == 30
    assert meta.assemblies == [10, 17, 24]
    assert len(loaded.t0_state.agents) == 12
    assert len({a.role for a in loaded.t0_state.agents.values()}) >= 4
    # heterogeneous declared values across the 5 axes
    axes = set(meta.value_axes)
    assert len(axes) == 5
    profiles = {json.dumps(a.declared_values.model_dump(), sort_keys=True)
                for a in loaded.t0_state.agents.values()}
    assert len(profiles) > 1
    assert loaded.t0_state.world_id == "world-pilot-v1"
    assert loaded.t0_state.seed == 20261009
    # lifecycle covers t0-t11 explicitly
    for tick in range(12):
        assert str(tick) in meta.lifecycle
    assert meta.lifecycle["0"]["probe_points"] == ["baseline"]
    assert "pre_meeting" in meta.lifecycle["9"]["probe_points"]
    assert "post_meeting" in meta.lifecycle["10"]["probe_points"]


def test_tape_shape_and_hash():
    loaded = ScenarioLoader(SCENARIO, TAPE).load()
    assert loaded.tape_hash == _stored_hash()
    assert len(loaded.tape) == 30
    # t0 has no tasks; assembly ticks have exactly one convene_assembly task
    assert loaded.tape[0] == []
    for tick in (10, 17, 24):
        batch = loaded.tape[tick]
        assert len(batch) == 1
        assert batch[0].action_type == "convene_assembly"
    # every non-zero, non-assembly tick has a 3-task batch
    for tick in range(1, 30):
        if tick in (10, 17, 24):
            continue
        assert len(loaded.tape[tick]) == 3
    # task ids are globally unique
    ids = [t.task_id for batch in loaded.tape for t in batch]
    assert len(ids) == len(set(ids))


def test_canonical_tape_hash_is_order_independent():
    doc = json.loads(TAPE.read_text(encoding="utf-8"))
    reordered = dict(reversed(list(doc.items())))
    assert canonical_tape_hash(reordered) == canonical_tape_hash(doc) == _stored_hash()


def test_loader_is_deterministic():
    a = ScenarioLoader(SCENARIO, TAPE).load()
    b = ScenarioLoader(SCENARIO, TAPE).load()
    assert a.t0_state == b.t0_state
    assert a.tape == b.tape
    assert a.tape_hash == b.tape_hash


def test_loader_manifest_linkage_ok():
    manifest = ExperimentManifest(
        experiment_id="e-pilot-1",
        world_id="world-pilot-v1",
        run_seed=20261009,
        model_endpoint_alias="local-vllm",
        model_fingerprint="qwen:sha256:0000",
        task_tape_hash=_stored_hash(),
    )
    loaded = ScenarioLoader(SCENARIO, TAPE).load(manifest=manifest)
    assert loaded.tape_hash == manifest.task_tape_hash


def test_loader_manifest_mismatch_raises():
    manifest = ExperimentManifest(
        experiment_id="e-pilot-1",
        world_id="world-pilot-v1",
        run_seed=20261009,
        model_endpoint_alias="local-vllm",
        model_fingerprint="qwen:sha256:0000",
        task_tape_hash="0" * 64,
    )
    with pytest.raises(ScenarioLoadError, match="task_tape_hash"):
        ScenarioLoader(SCENARIO, TAPE).load(manifest=manifest)
    wrong_world = ExperimentManifest(
        experiment_id="e-2", world_id="other", run_seed=20261009,
        model_endpoint_alias="local-vllm", model_fingerprint="qwen:sha256:0000",
    )
    with pytest.raises(ScenarioLoadError, match="world_id"):
        ScenarioLoader(SCENARIO, TAPE).load(manifest=wrong_world)


def test_tampered_task_value_raises(pilot_tape_copy_factory):
    bad = pilot_tape_copy_factory([(["ticks", 5, "tasks", 0, "constraints"], {"units": 999})])
    with pytest.raises(ScenarioLoadError, match="tape_hash mismatch"):
        ScenarioLoader(SCENARIO, bad).load()


def test_tampered_seed_raises(pilot_tape_copy_factory):
    bad = pilot_tape_copy_factory([(["run_seed"], 12345)])
    with pytest.raises(ScenarioLoadError, match="run_seed mismatch"):
        ScenarioLoader(SCENARIO, bad).load()


def test_missing_hash_field_raises(pilot_tape_copy_factory, tmp_path):
    data = json.loads(TAPE.read_text(encoding="utf-8"))
    del data["tape_hash"]
    bad = tmp_path / "nohash.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="missing its tape_hash"):
        ScenarioLoader(SCENARIO, bad).load()
