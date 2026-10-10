"""Offline tests for verdi.experiments.g5_close (ADR-014 §2/§4/§5/§6).

No network, no .env reads. Real-run-root tests skip when the gitignored
production run root is absent; a synthetic 2-pair mini-run carries the
clean-pass + planted-corruption matrix.
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from tests.experiments.conftest import (
    REAL_G5_RUN_ROOT,
    make_mini_run_root,
    mutate_tape,
    requires_real_run_root,
)

from verdi.audit.judge_live import _assert_no_key_material
from verdi.experiments.g5_close import (
    EXPECTED_SAMPLE_ROWS,
    FROZEN_LIMITATIONS,
    NOT_CLAIMED,
    audit_leakage,
    audit_prompt_invariance,
    audit_quarantine_symmetry,
    audit_self_report,
    build_g5_close_report,
    build_judge_section,
    build_sample_manifest,
    causal_word_guard,
    identification_diagnostics,
    leave_one_pair_out,
    permutation_null,
    write_g5_close_report,
)

#: A valid per-row judge verdict entry (expected_stance matches).
GOOD_VERDICT_ROW = {
    "row_id": "pair_0:seq_0",
    "pair_index": 0,
    "seq": 0,
    "arm_relevant": "prefix",
    "actor_id": "a-01",
    "lifecycle_tick": 1,
    "task_id": "pilotv1-t01-01",
    "engine_outcome": {"event_id": 1, "decision": "accepted", "violations": []},
    "expected_stance": "agree",
    "quarantined": False,
    "verdict": {
        "stance": "agree",
        "rationale": "conforms to the declared task",
        "confidence": "high",
    },
    "error": None,
    "raw": None,
}


@pytest.fixture()
def mini_run_root(tmp_path):
    return make_mini_run_root(tmp_path / "mini_run")


def _identification_stub():
    return {
        "estimand_statement": "within-pair contrast T-P on the six estimands",
        "assumptions": ["same-code replay", "scaffold invariance", "single manipulation"],
        "not_claimed": list(NOT_CLAIMED),
        "diagnostics": {},
    }


def _context_stub():
    return {"n_pairs": 2, "git_sha": "test", "model_id": "Qwen3.8-27B", "cap": 700}


def _report_inputs(mini_run_root=None):
    manifest = build_sample_manifest(mini_run_root or REAL_G5_RUN_ROOT)
    audits = {}
    if mini_run_root is not None:
        audits = {
            "B1": audit_self_report(mini_run_root),
            "B2": audit_leakage(mini_run_root),
            "B3": audit_quarantine_symmetry(mini_run_root),
            "B4": audit_prompt_invariance(mini_run_root),
        }
    else:
        audits = {"placeholder": {"passed": True}}
    judge_section = build_judge_section([GOOD_VERDICT_ROW], manifest)
    return {
        "manifest": manifest,
        "judge_section": judge_section,
        "audits": audits,
        "identification": _identification_stub(),
        "context": _context_stub(),
    }


# --------------------------------------------------------------------------- #
# Sealed sample manifest (ADR-014 §2)
# --------------------------------------------------------------------------- #


@requires_real_run_root
def test_sample_manifest_real_run_root_counts_and_hash():
    manifest = build_sample_manifest(REAL_G5_RUN_ROOT)
    assert manifest["n_rows"] == EXPECTED_SAMPLE_ROWS == 504
    rows = manifest["rows"]
    pair_0 = [r for r in rows if r["pair_index"] == 0]
    assert len(pair_0) == 81
    for pidx in range(1, 10):
        subset = [r for r in rows if r["pair_index"] == pidx]
        assert len(subset) == 47
        assert all(r["arm_relevant"] == "suffix" for r in subset)
        assert all(r["lifecycle_tick"] >= 13 for r in subset)
    assert all(r["arm_relevant"] in ("prefix", "suffix") for r in pair_0)
    assert all(len(r["row_sha256"]) == 64 for r in rows)
    assert manifest["manifest_sha256"] == "fe38443ab209997f0695634a40d5cad19ca4d40aa81fe2a73406e217a2a336df"


@requires_real_run_root
def test_sample_manifest_double_build_byte_identical():
    m1 = build_sample_manifest(REAL_G5_RUN_ROOT)
    m2 = build_sample_manifest(REAL_G5_RUN_ROOT)
    assert json.dumps(m1, sort_keys=True) == json.dumps(m2, sort_keys=True)
    assert m1["manifest_sha256"] == m2["manifest_sha256"]
    # The manifest hash is over the canonical row-list serialization only.
    assert m1["manifest_sha256"] != json.dumps(m1, sort_keys=True)


def test_sample_manifest_mini_run(tmp_path):
    root = make_mini_run_root(tmp_path / "m")
    manifest = build_sample_manifest(root)
    # pair_0: all 3 rows; pair_1: suffix only (1 row) = 4 rows.
    assert manifest["n_rows"] == 4
    by_pair = {}
    for r in manifest["rows"]:
        by_pair.setdefault(r["pair_index"], []).append(r)
    assert len(by_pair[0]) == 3
    assert len(by_pair[1]) == 1
    assert by_pair[1][0]["lifecycle_tick"] == 13


# --------------------------------------------------------------------------- #
# Bias / leakage audits (ADR-014 §4): clean mini-run passes, corruptions fail
# --------------------------------------------------------------------------- #


def test_audits_pass_on_clean_mini_run(mini_run_root):
    assert audit_self_report(mini_run_root)["passed"] is True
    assert audit_leakage(mini_run_root)["passed"] is True
    assert audit_quarantine_symmetry(mini_run_root)["passed"] is True
    assert audit_prompt_invariance(mini_run_root)["passed"] is True


@requires_real_run_root
def test_audits_pass_on_real_run_root():
    assert audit_self_report(REAL_G5_RUN_ROOT)["passed"] is True
    assert audit_leakage(REAL_G5_RUN_ROOT)["passed"] is True
    assert audit_quarantine_symmetry(REAL_G5_RUN_ROOT)["passed"] is True
    assert audit_prompt_invariance(REAL_G5_RUN_ROOT)["passed"] is True


def test_b1_task_id_mismatch_fails_self_report(mini_run_root):
    mutate_tape(
        mini_run_root,
        0,
        0,
        parsed_action={
            "action_kind": "transport",
            "actor_id": "a-01",
            "arguments": {"task_id": "WRONG", "units": 1},
            "task_id": "WRONG",
            "schema_version": 1,
            "reason": "mini",
        },
    )
    res = audit_self_report(mini_run_root)
    assert res["passed"] is False
    assert res["divergent"] == 1
    assert res["divergences"][0]["seq"] == 0
    assert res["divergences"][0]["reported_task_id"] == "WRONG"


def test_b1_action_kind_mismatch_fails_self_report(mini_run_root):
    mutate_tape(mini_run_root, 1, 1, parsed_action={"action_kind": "harvest", "task_id": "pilotv1-t02-01", "actor_id": "a-02", "arguments": {}, "schema_version": 1, "reason": "x"})
    res = audit_self_report(mini_run_root)
    assert res["passed"] is False
    assert res["divergences"][0]["declared_action_kind"] == "transport"
    assert res["divergences"][0]["reported_action_kind"] == "harvest"


def test_b2_planted_ground_truth_string_fails_leakage(mini_run_root):
    mutate_tape(mini_run_root, 0, 0, response_raw='{"appeal_upheld": true}')
    res = audit_leakage(mini_run_root)
    assert res["passed"] is False
    assert res["ground_truth_hits"] == [
        {"pair_index": 0, "seq": 0, "term": "appeal_upheld"}
    ]


def test_b2_planted_key_material_fails_leakage(mini_run_root):
    mutate_tape(mini_run_root, 1, 2, response_raw='{"reason": "key sk-testfake123456 here"}')
    res = audit_leakage(mini_run_root)
    assert res["passed"] is False
    assert len(res["key_material_findings"]) == 1
    assert "sk-testfake123456" in res["key_material_findings"][0]


def test_b2_prompt_ordering_leak_fails_leakage(mini_run_root):
    # row j=2's prompt body head (64 chars, includes the tick-13 task line)
    # appears verbatim in earlier row i=0's response_raw.
    rows = [json.loads(line) for line in (mini_run_root / "pair_0" / "live_tape.jsonl").read_text().splitlines() if line.strip()]
    head = rows[2]["prompt"].partition("\n")[2][:64]
    # Embed the head verbatim (no JSON escaping, which would defeat the
    # verbatim-containment check).
    mutate_tape(mini_run_root, 0, 0, response_raw="prefix " + head)
    res = audit_leakage(mini_run_root)
    assert res["passed"] is False
    assert res["ordering_leaks"] == [
        {"pair_index": 0, "earlier_seq": 0, "later_seq": 2, "frag_head": head}
    ]


def test_b3_asymmetric_quarantine_fails_symmetry(mini_run_root):
    mutate_tape(
        mini_run_root,
        1,
        0,
        quarantined=True,
        quarantine_reason="empty completion",
    )
    res = audit_quarantine_symmetry(mini_run_root)
    assert res["passed"] is False
    assert res["quarantine_vector"] == [0, 1]
    assert res["per_pair"]["pair_1"]["quarantine_reasons"] == ["empty completion"]


def test_b4_prompt_drift_fails_invariance(mini_run_root):
    rows = [json.loads(line) for line in (mini_run_root / "pair_1" / "live_tape.jsonl").read_text().splitlines() if line.strip()]
    drift = [r for r in rows if r["lifecycle_tick"] >= 13][0]
    mutate_tape(mini_run_root, 1, drift["seq"], prompt=drift["prompt"].replace("tick 13", "tick 14"))
    res = audit_prompt_invariance(mini_run_root)
    assert res["passed"] is False
    assert res["drifts"][0]["pair_index"] == 1
    # only the prompt field drifted: actor_id / lifecycle_tick / task_id stay
    # single-valued, and the drifted prompt surfaces as one drift entry.
    assert res["n_distinct_per_field"] == {"actor_id": 1, "lifecycle_tick": 1, "task_id": 1}
    assert res["n_distinct_prompt_tuples"] == 1
    assert len(res["drifts"]) == 1


# --------------------------------------------------------------------------- #
# Identification diagnostics (ADR-014 §5)
# --------------------------------------------------------------------------- #


@requires_real_run_root
def test_leave_one_pair_out_real_run_root():
    res = leave_one_pair_out(REAL_G5_RUN_ROOT)
    assert res["n_pairs"] == 10
    for estimand, block in res["per_estimand"].items():
        assert block["n_values"] == 10, estimand
        assert len(block["leave_one_out_means"]) == 10
        assert block["pstdev_spread"] >= 0.0
    # false_punishment_rate: all deltas are +1.0, so every LOPO mean is 1.0.
    fp = res["per_estimand"]["false_punishment_rate"]
    assert all(abs(v - 1.0) < 1e-12 for v in fp["leave_one_out_means"])
    assert fp["pstdev_spread"] == 0.0


@requires_real_run_root
def test_permutation_null_real_run_root_symmetry():
    res = permutation_null(REAL_G5_RUN_ROOT)
    assert res["n_perms"] == 200
    assert res["seed"] == 0
    assert res["is_randomization"] is False
    for estimand, block in res["per_estimand"].items():
        assert block["n_perms"] == 200
        # Required assertion: the null distribution contains 0 (equal
        # magnitudes + some exactly-half-negative permutation => exact 0.0
        # mean), or the mean set is symmetric around 0.
        assert (
            block["magnitudes_equal"]
            and block["fraction_of_means_exactly_zero"] > 0.0
        ) or (abs(block["min"] + block["max"]) <= 1e-12), estimand
        assert block["contains_zero_mean"] or abs(block["min"] + block["max"]) <= 1e-12
    fp = res["per_estimand"]["false_punishment_rate"]
    assert fp["magnitudes_equal"] is True
    # Exact math: all 10 magnitudes equal 1.0, so a permutation mean is
    # exactly 0.0 iff exactly 5 of the 10 signs are negative. All 200
    # sign patterns occur among 200 draws, so 0.0 MUST appear. (The full
    # set is not symmetric in min/max: all-negative signs give -1.0 while
    # the most-positive draw is k<=4 negative -> +0.8.)
    assert fp["contains_zero_mean"] is True
    assert fp["fraction_of_means_exactly_zero"] > 0.0


def test_permutation_null_mini_run(tmp_path):
    root = make_mini_run_root(tmp_path / "m")
    # Deltas: false_punishment_rate +0.1 for both pairs, 0.0 elsewhere.
    # 0.0 IS one of the 200 permutation means for the +0.1 estimand
    # (exactly-half-negative sign draws), and every mean is 0.0 for the rest.
    res = permutation_null(root)
    assert res["per_estimand"]["false_punishment_rate"]["contains_zero_mean"] is True
    assert res["per_estimand"]["welfare_mean_food"]["contains_zero_mean"] is True
    for block in res["per_estimand"].values():
        assert block["contains_zero_mean"] is True


def test_leave_one_pair_out_and_permutation_minimal(tmp_path):
    # conftest arm dicts: both pairs carry delta 0.1 on
    # false_punishment_rate and 0.0 on the other five estimands.
    root = make_mini_run_root(tmp_path / "m")
    lo = leave_one_pair_out(root)
    assert lo["n_pairs"] == 2
    fp = lo["per_estimand"]["false_punishment_rate"]
    assert fp["n_values"] == 2
    # dropping either pair -> mean of the single remaining delta = 0.1
    assert fp["leave_one_out_means"] == [0.1, 0.1]
    assert fp["pstdev_spread"] == 0.0
    other = lo["per_estimand"]["welfare_mean_food"]
    assert other["leave_one_out_means"] == [0.0, 0.0]


# --------------------------------------------------------------------------- #
# Report (ADR-014 §6)
# --------------------------------------------------------------------------- #


@requires_real_run_root
def test_full_report_real_run_root(tmp_path):
    manifest = build_sample_manifest(REAL_G5_RUN_ROOT)
    audits = {
        "B1": audit_self_report(REAL_G5_RUN_ROOT),
        "B2": audit_leakage(REAL_G5_RUN_ROOT),
        "B3": audit_quarantine_symmetry(REAL_G5_RUN_ROOT),
        "B4": audit_prompt_invariance(REAL_G5_RUN_ROOT),
    }
    identification = identification_diagnostics(REAL_G5_RUN_ROOT)
    context = {"n_pairs": 10, "git_sha": "test", "model_id": "Qwen3.8-27B", "cap": 700}
    judge_section = build_judge_section([GOOD_VERDICT_ROW], manifest)
    report = build_g5_close_report(manifest, judge_section, audits, identification, context)
    assert set(report.keys()) == {
        "judge",
        "bias_audits",
        "identification",
        "context",
        "limitations",
        "sample_manifest",
    }
    assert report["identification"]["not_claimed"] == list(NOT_CLAIMED)
    assert report["limitations"] == list(FROZEN_LIMITATIONS)
    framing = report["judge"]["framing"]
    assert framing["judge_shown_engine_outcome"] is True
    assert framing["measured_quantity"] == (
        "judge-to-engine self-consistency on G5 paired data, not an "
        "independent compliance verdict"
    )
    assert framing["judge_blinding_satisfied_by"] == "deterministic code judge only"
    assert framing["same_family_self_judgment"] is True
    assert framing["advisor_only"] is True
    out = tmp_path / "report.json"
    write_g5_close_report(report, out)
    assert json.loads(out.read_text()) == report
    assert out.read_text().endswith("\n")


def test_report_double_build_byte_stable(tmp_path):
    # Same inputs -> byte-identical canonical serialization (the manifest's
    # run_root path is part of the inputs, so build twice from ONE set).
    inputs = _report_inputs(mini_run_root=make_mini_run_root(tmp_path / "m"))
    r1 = build_g5_close_report(
        inputs["manifest"], inputs["judge_section"], inputs["audits"],
        inputs["identification"], inputs["context"],
    )
    r2 = build_g5_close_report(
        inputs["manifest"], inputs["judge_section"], inputs["audits"],
        inputs["identification"], inputs["context"],
    )
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    out = tmp_path / "report.json"
    write_g5_close_report(r2, out)
    # write asserts double-build byte-stability and re-loads equal.
    assert json.loads(out.read_text()) == r2
    assert out.read_text() == json.dumps(r2, indent=2, sort_keys=True) + "\n"


def test_causal_word_guard_passes_real_shape(tmp_path):
    inputs = _report_inputs(mini_run_root=make_mini_run_root(tmp_path / "m"))
    # The guard already ran inside build_g5_close_report; run it directly.
    report = build_g5_close_report(
        inputs["manifest"], inputs["judge_section"], inputs["audits"],
        inputs["identification"], inputs["context"],
    )
    causal_word_guard(report)  # must not raise
    blob = json.dumps(report)
    n = len(re.findall(r"causal", blob, flags=re.IGNORECASE))
    assert n <= 3  # the frozen cap, hit by the not_claimed block


def test_causal_word_guard_unaffected_by_run_root_path(tmp_path):
    # The pytest tmp_path here CONTAINS the substring "causal" (this test
    # is named test_causal_word_guard_*). build_g5_close_report omits the
    # manifest's run_root path from the report, so filesystem paths can
    # never feed the causal-word guard: the guard must pass even though
    # the underlying run root path trips it.
    assert "causal" in str(tmp_path).lower()
    inputs = _report_inputs(mini_run_root=make_mini_run_root(tmp_path / "m"))
    assert "causal" in inputs["manifest"]["run_root"].lower()
    report = build_g5_close_report(
        inputs["manifest"], inputs["judge_section"], inputs["audits"],
        inputs["identification"], inputs["context"],
    )
    # Every occurrence in the REPORT is the frozen negated framing, even
    # though the run_root path (deliberately excluded from the report)
    # would have tripped it.
    causal_word_guard(report)
    assert "run_root" not in report["sample_manifest"]


def test_causal_word_guard_raises_on_unnegated_occurrences():
    bad = {
        "a": "The result is causal.",
        "b": "We claim the effect is causal.",
        "c": "This is a causal finding.",
        "d": "The contrast is causal by design.",
        "e": "no causal claim here",
    }
    with pytest.raises(AssertionError):
        causal_word_guard(bad)


def test_causal_word_guard_raises_on_count_exceeded():
    blob_seed = {"x": "no causal claim"}
    for i in range(3):
        blob_seed[f"n{i}"] = "no causal claim"
    # 4 occurrences, all negated, but the frozen cap is 3.
    with pytest.raises(AssertionError):
        causal_word_guard(blob_seed)


def test_report_rejects_unfrozen_not_claimed_block(mini_run_root):
    inputs = _report_inputs(mini_run_root=mini_run_root)
    identification = copy.deepcopy(inputs["identification"])
    identification["not_claimed"] = ["something else"]
    with pytest.raises(AssertionError):
        build_g5_close_report(
            inputs["manifest"], inputs["judge_section"], inputs["audits"],
            identification, inputs["context"],
        )


def test_no_key_material_in_tape_and_report(mini_run_root):
    fake_key = "sk-testfake123"
    # The key-material scanner (judge_live behavior) must raise on the tape
    # text that would carry a fake key ...
    with pytest.raises(AssertionError):
        _assert_no_key_material(f'"raw": "verdict {fake_key} end"', "fake tape")
    # ... but the clean artifacts of a mock judge run must NOT raise.
    from verdi.experiments.g5_judge_live import MockJudgeClient, run_g5_judge

    run = run_g5_judge(
        mini_run_root,
        judge_client=MockJudgeClient(),
        out_dir=mini_run_root.parent / "judge_out",
        smoke_first=False,
        hard_call_cap=700,
        expected_rows=4,
    )
    tape_text = (mini_run_root.parent / "judge_out" / "judge_tape.jsonl").read_text()
    meta_text = (mini_run_root.parent / "judge_out" / "g5judge_run_meta.json").read_text()
    _assert_no_key_material(tape_text, "judge_tape.jsonl")
    _assert_no_key_material(meta_text, "g5judge_run_meta.json")
    # A report containing a fake key must fail the write guard.
    from verdi.experiments.g5_close import write_g5_close_report

    inputs = _report_inputs(mini_run_root=mini_run_root)
    report = build_g5_close_report(
        inputs["manifest"], inputs["judge_section"], inputs["audits"],
        inputs["identification"], inputs["context"],
    )
    report["context"]["leaked"] = fake_key
    with pytest.raises(AssertionError):
        write_g5_close_report(report, mini_run_root.parent / "bad_report.json")
    assert run["n_calls"] == 4


# --------------------------------------------------------------------------- #
# build_judge_section (consumed by both the live run and report builder)
# --------------------------------------------------------------------------- #


def test_build_judge_section_agreement_and_enumeration():
    rows = [
        GOOD_VERDICT_ROW,
        {
            **GOOD_VERDICT_ROW,
            "row_id": "pair_1:seq_0",
            "pair_index": 1,
            "arm_relevant": "suffix",
            "expected_stance": "agree",
            "verdict": {
                "stance": "disagree",
                "rationale": "violates the deadline rule",
                "confidence": "medium",
            },
        },
        {
            **GOOD_VERDICT_ROW,
            "row_id": "pair_1:seq_2",
            "pair_index": 1,
            "arm_relevant": "suffix",
            "quarantined": True,
            "verdict": None,
            "error": "unparseable JSON",
            "raw": "{not json",
        },
    ]
    manifest = {"run_root": "x", "n_rows": 3, "manifest_sha256": "0" * 64}
    section = build_judge_section(rows, manifest)
    assert section["n_rows"] == 3
    assert section["n_valid_verdicts"] == 2
    assert section["agreement_overall"]["n_agree"] == 1
    assert section["agreement_overall"]["wilson_ci_95"] is not None
    assert section["agreement_by_row_position"]["suffix"]["n_judged"] == 1
    assert section["agreement_by_row_position"]["prefix"]["n_agree"] == 1
    assert section["disagreements"] == [
        {
            "row_id": "pair_1:seq_0",
            "pair_index": 1,
            "judge_stance": "disagree",
            "engine_stance": "agree",
            "rationale": "violates the deadline rule",
        }
    ]
    assert section["quarantined_judge_rows"] == [
        {
            "row_id": "pair_1:seq_2",
            "pair_index": 1,
            "error": "unparseable JSON",
            "raw": "{not json",
        }
    ]
