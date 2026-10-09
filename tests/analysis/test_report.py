"""RunReport serialization tests (P1-E)."""
from __future__ import annotations

import json

import pytest

from verdi.analysis import RunReport, RunReportError
from verdi.schemas import ExperimentManifest


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
