"""ADR-013 G5 paired arms — offline tests (NO NETWORK, NO REAL SLEEPS).

Pins the ADR-013 pipeline against the sealed L1 provenance:
* L1 loading / provenance invariants;
* matching invariants (origin hash B3 + sealed prefix tape hash,
  ``make_matched_pair`` identity);
* the mandatory placebo-inert null control (same-code invariants per
  ADR-013 Addendum A1: prefix byte-identical, arms identical except the
  adjudication cascade, equal folded finals, unbroken chains);
* arm folds: treatment (upheld + rights restored) vs placebo (verbatim L1
  dismissal), scaffold invariance (identical final state hash),
  cross-arm adjudication-kind isolation, placebo byte-identity to L1;
* arm run dicts satisfy ``analysis.report._validate_arm_run_dict``;
* deterministic G5 report byte-stability (build twice, identical bytes);
* no API-key material in any artifact.

The REAL live run (10 pairs × 47 Qwen calls) is script-style outside
pytest; CI must never make network calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from verdi.analysis.estimates import ESTIMANDS, paired_estimates
from verdi.analysis.report import _validate_arm_run_dict
from verdi.experiments.g5_pairs import (
    EXPECTED_SUFFIX_CALLS,
    HARD_CALL_CAP,
    L1_EVENT_LOG_PATH,
    L1_TAPE_PATH,
    PLACEBO_EVENT_ID,
    G5PipelineError,
    arm_manifest,
    arm_run_dict,
    audit_artifacts,
    build_g5_paired_report,
    generate_pair_tape,
    load_l1,
    matched_pair,
    matching_context,
    null_control,
    prefix_event_log,
    run_arm,
)
from verdi.experiments.pairs import make_matched_pair, tape_hash
from verdi.world.reducer import WorldReducer

WORKTREE = Path("/Users/sushantgautam/Documents/verdi-g5pairs")
pytestmark = pytest.mark.skipif(
    not L1_TAPE_PATH.is_file() or not L1_EVENT_LOG_PATH.is_file(),
    reason="L1 live run artifacts not present (run artifacts, not repo assets)",
)


@pytest.fixture(scope="module")
def l1():
    return load_l1()


@pytest.fixture(scope="module")
def context(l1):
    return matching_context(l1, WORKTREE)


@pytest.fixture(scope="module")
def arms(l1):
    """One pair folded from L1's own tape (R = L1 suffix): fast, offline."""
    tr = run_arm(WORKTREE, l1.tape_rows, "treatment")
    pr = run_arm(WORKTREE, l1.tape_rows, "placebo")
    return tr, pr


# --------------------------------------------------------------------------- #
# L1 provenance
# --------------------------------------------------------------------------- #


def test_l1_provenance_invariants(l1):
    assert len(l1.event_log.events) == 126
    assert l1.event_log.events[49].kind == "appeal_filed"
    assert l1.event_log.events[50].kind == "appeal_dismissed"
    assert len(l1.tape_rows) == 81
    assert len(l1.prefix_tape_rows) == 34
    assert len(l1.suffix_tape_rows) == EXPECTED_SUFFIX_CALLS == 47
    # B3(a): origin hash = sealed after_hash of event 49.
    assert l1.origin_state_hash == l1.event_log.events[49].after_hash


def test_prefix_event_log_chain(l1):
    prefix = prefix_event_log(l1)
    assert len(prefix.events) == 50
    prefix.verify()  # raises LogIntegrityError on any break


# --------------------------------------------------------------------------- #
# Matching invariants
# --------------------------------------------------------------------------- #


def test_matching_context_b3(l1, context):
    # B3(a): origin hash equals the folded prefix terminal canonical hash.
    assert context["folded_prefix_terminal_hash"] == context["origin_state_hash"]
    # B3(b): origin event count.
    assert context["origin_event_count"] == 50
    # tape_hash is over the sealed prefix ledger.
    assert context["prefix_tape_hash"] == tape_hash(prefix_event_log(l1))


def test_arm_manifests_and_matched_pair(l1, context):
    t = arm_manifest(l1, 3, "treatment", context)
    p = arm_manifest(l1, 3, "placebo", context)
    # Both arms share the sealed origin snapshot hash + prefix tape hash.
    assert t["origin_state_hash"] == p["origin_state_hash"] == context["origin_state_hash"]
    assert t["tape_hash"] == p["tape_hash"] == context["prefix_tape_hash"]
    assert t["branch_origin"] == p["branch_origin"] == "g5-pair-3"
    assert t["treatment_seed"] == p["treatment_seed"] == 3
    pair = matched_pair(l1, 3, context)
    assert pair == make_matched_pair(t, p)


def test_matching_context_fold_is_pure(l1, context):
    # Re-folding reproduces the identical terminal hash (determinism).
    from verdi.experiments.g5_pairs import _fold_prefix_state

    # repo_root=None resolves package-relative (the worktree src layout).
    h1 = WorldReducer.canonical_state_hash(_fold_prefix_state(l1))
    h2 = WorldReducer.canonical_state_hash(_fold_prefix_state(l1))
    assert h1 == h2 == context["origin_state_hash"]


# --------------------------------------------------------------------------- #
# Mandatory placebo-inert null control
# --------------------------------------------------------------------------- #


def test_null_control_placebo_inert(l1):
    """Same-code invariants (ADR-013 Addendum A1) all hold on the L1 tape."""
    evidence = null_control(l1, WORKTREE)
    invariants = evidence["invariants"]
    assert invariants["a_prefix_byte_identical"] is True
    assert invariants["b_arms_identical_except_adjudication"] is True
    assert invariants["b_aligned_suffix_differing_fields"] == [
        "cause_event_id",
        "event_hash",
        "event_id",
        "prev_hash",
        "tick",
    ]
    assert invariants["b_branch_kinds"] == {
        "treatment": ["appeal_upheld", "rights_restored"],
        "placebo": ["appeal_dismissed"],
    }
    assert invariants["c_finals_equal"] is True
    assert invariants["d_treatment_chain_unbroken"] is True
    assert invariants["d_placebo_chain_unbroken"] is True
    # Scaffold invariance: the convergent final state hash.
    assert evidence["final_state_hash"] == (
        "fa30e29786877c6534013a2f6a2683a6d19e1cb136a66830d35cd9a8defdd582"
    )
    assert evidence["event_counts"] == {"treatment": 127, "placebo": 126, "l1": 126}
    # Structurally unable to claim byte-identity to historical L1.
    assert "byte_identical" not in evidence
    assert "RETIRED" in evidence["standard"]
    assert evidence["version_drift"]["l1_git_sha"] != evidence["version_drift"][
        "g5_run_git_sha"
    ]


def test_null_control_catches_divergence(l1, monkeypatch):
    """Invariant (b): an ASYMMETRIC arm-vs-arm fold divergence must be caught.

    The redefined same-code invariants are RELATIVE (treatment fold vs
    placebo fold). A symmetric input corruption — e.g. corrupting one row of
    the shared R_p tape — folds identically into both arms and is therefore
    NOT a divergence (both arms still agree), so it must NOT trip the check.
    The genuine defect is one arm folding differently from the other: a
    post-branch event whose payload no longer matches the placebo fold,
    which invariant (b) (aligned suffix, non-bookkeeping fields) detects.
    """
    from verdi.experiments import g5_pairs

    real_run_arm = g5_pairs.run_arm

    def biased_run_arm(repo_root, pair_tape, arm, out_dir=None):
        run = real_run_arm(repo_root, pair_tape, arm, out_dir)
        if arm == "treatment":
            # Break the treatment fold at the first aligned-suffix event
            # (index PLACEBO_EVENT_ID + 2, offset 0 in the alignment loop) so
            # it diverges from the placebo fold in a non-bookkeeping field.
            ev = run.log.events[PLACEBO_EVENT_ID + 2]
            run.log._events[PLACEBO_EVENT_ID + 2] = ev.model_copy(
                update={"payload": dict(ev.payload, corrupt="true")}
            )
        return run

    monkeypatch.setattr(g5_pairs, "run_arm", biased_run_arm)
    with pytest.raises(
        G5PipelineError, match="aligned suffix differs in non-bookkeeping fields"
    ):
        null_control(l1, WORKTREE)


def test_null_control_symmetric_input_corruption_is_not_divergence(l1):
    """A symmetric R_p corruption folds identically into both arms.

    Regresses the false-positive class the retired v1 standard had: the check
    must remain RELATIVE, so corrupting the shared input tape (which reaches
    both arms identically) must NOT be reported as a placebo-inert
    divergence — the arms still agree with each other.
    """
    bad_rows = [dict(r) for r in l1.tape_rows]
    idx = next(i for i, r in enumerate(bad_rows) if int(r["lifecycle_tick"]) >= 13)
    bad_rows[idx] = dict(bad_rows[idx])
    bad_rows[idx]["response_raw"] = (
        '{"action_kind": "emit_signal", "arguments": {"text": "x"}, "reason": "corr"}'
    )
    bad = type(l1)(event_log=l1.event_log, tape_rows=bad_rows)
    # Must NOT raise: the two same-code folds still agree (relative invariants).
    evidence = null_control(bad, WORKTREE)
    assert evidence["invariants"]["b_arms_identical_except_adjudication"] is True


def test_null_control_catches_state_divergence(l1, monkeypatch):
    """Invariant (c): a fold that changes the final state hash must fail."""
    from verdi.experiments import g5_pairs

    real_run_arm = g5_pairs.run_arm

    def biased_run_arm(repo_root, pair_tape, arm, out_dir=None):
        run = real_run_arm(repo_root, pair_tape, arm, out_dir)
        if arm == "treatment":
            # Simulate a broken reducer: final state no longer convergent.
            class _BiasedFinal:
                state_hash = "0" * 64

            run.final_state = _BiasedFinal()
        return run

    monkeypatch.setattr(g5_pairs, "run_arm", biased_run_arm)
    with pytest.raises(G5PipelineError, match="DIVERGENCE"):
        null_control(l1, WORKTREE)


# --------------------------------------------------------------------------- #
# Arm folds
# --------------------------------------------------------------------------- #


def test_placebo_arm_is_byte_identical_to_l1_suffix(l1, arms):
    _, pr = arms
    l1_events = [json.loads(line) for line in L1_EVENT_LOG_PATH.read_text().splitlines()]
    assert len(pr.log.events) == 126
    for i in range(50, 126):
        folded, ref = pr.log.events[i], l1_events[i]
        assert folded.event_hash == ref["event_hash"], f"divergence at event {i}"
        assert folded.payload == ref["payload"], f"payload divergence at event {i}"


def test_treatment_arm_adjudication(arms):
    tr, _ = arms
    kinds = [e.kind for e in tr.log.events]
    # Treatment arm: upheld + rights restored present, dismissal absent.
    assert "appeal_upheld" in kinds
    assert "rights_restored" in kinds
    assert "appeal_dismissed" not in kinds
    upheld = next(e for e in tr.log.events if e.kind == "appeal_upheld")
    assert upheld.payload["decision"] == "accepted"
    assert upheld.payload["grounds"] == "upheld-appeal treatment arm"
    restored = next(e for e in tr.log.events if e.kind == "rights_restored")
    assert restored.payload["rights_restored"] == ["speaking"]


def test_scaffold_invariance_final_state_hash(arms):
    tr, pr = arms
    # pilot_v1 action kinds are non-mutating: both arms converge.
    assert tr.final_state.state_hash == pr.final_state.state_hash


def test_arm_ledger_verifies_and_counts(arms):
    tr, pr = arms
    # Placebo arm: byte-identical to L1 (126 events, dismissal = 1 event).
    # Treatment arm: 127 events (upheld + rights_restored = 2 events at the
    # branch position, no dismissal).
    tr.log.verify()
    pr.log.verify()
    assert len(pr.log.events) == 126
    assert len(tr.log.events) == 127
    assert tr.quarantined == []
    assert pr.quarantined == []


# --------------------------------------------------------------------------- #
# Arm run dicts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arm", ["treatment", "placebo"])
def test_arm_run_dict_valid(l1, arms, arm):
    run = arms[0] if arm == "treatment" else arms[1]
    d = arm_run_dict(run, arm)
    _validate_arm_run_dict(arm, d)  # raises on missing keys
    assert d["applied"] is (arm == "treatment")
    assert set(d["estimates"]) == set(ESTIMANDS)
    assert [t for t, _ in d["state_snapshots"]] == [17, 24, 29]
    assert d["final_state_hash"] == d["terminal_state_hash"]


# --------------------------------------------------------------------------- #
# Deterministic report
# --------------------------------------------------------------------------- #


def test_report_byte_stability(l1, arms, context):
    tr, pr = arms
    td = arm_run_dict(tr, "treatment")
    pd = arm_run_dict(pr, "placebo")
    pairs = [(td, pd)] * 10
    evidence = {
        "method": "test same-code invariants",
        "standard": "same-code invariants; byte-identity to L1 RETIRED",
        "invariants": {"a_prefix_byte_identical": True},
        "version_drift": {"l1_git_sha": "a" * 40, "g5_run_git_sha": "b" * 40},
    }
    r1 = build_g5_paired_report(
        l1, td, pd, paired_estimates(pairs), evidence, context,
        pairs_run_dicts=pairs,
    )
    r2 = build_g5_paired_report(
        l1, td, pd, paired_estimates(pairs), dict(evidence), dict(context),
        pairs_run_dicts=pairs,
    )
    b1 = json.dumps(r1, indent=2, sort_keys=True) + "\n"
    b2 = json.dumps(r2, indent=2, sort_keys=True) + "\n"
    assert b1 == b2
    # No NEW causal wording beyond the three frozen negated occurrences.
    assert len(re.findall(r"causal", b1, flags=re.IGNORECASE)) == 3
    # ADR-013 §1 extensions present.
    assert "null_control_placebo_inert" in r1
    assert r1["matching"]["origin_state_hash"] == context["origin_state_hash"]
    assert r1["replication"]["n_pairs"] == 10
    assert r1["design"]["randomized"] is False
    # Addendum A1: numeric cross-pair replication spread (replicated single
    # pair → zero spread, labeled as replication spread).
    spread = r1["cross_pair_replication_spread"]
    assert spread["n_pairs"] == 10
    assert "replication spread, not sampling noise" in spread["label"]
    assert set(spread["values"]) == set(ESTIMANDS)
    for per_arm in spread["values"].values():
        assert per_arm["treatment"] == 0.0 and per_arm["placebo"] == 0.0
    # Addendum A1 framings + corrected limitations (stale entries removed).
    assert "false_punishment_direction" in r1["framings"]
    assert "deprivation_metric_restoration_blind" in r1["framings"]
    assert not any("no live LLM interaction" in s for s in r1["limitations"])
    assert not any("n_pairs < 10" in s for s in r1["limitations"])
    assert any("live 10-pair run" in s for s in r1["limitations"])
    assert any("version drift" in s for s in r1["limitations"])
    # The frozen null_control block is relabeled: it is the T−P contrast.
    assert "TREATMENT contrast" in r1["null_control"]["design"]


def test_report_pair_cis_present(l1, arms, context):
    tr, pr = arms
    td = arm_run_dict(tr, "treatment")
    pd = arm_run_dict(pr, "placebo")
    pairs = [(td, pd)] * 10
    r = build_g5_paired_report(
        l1,
        td,
        pd,
        paired_estimates(pairs),
        {"method": "test same-code invariants"},
        context,
        pairs_run_dicts=pairs,
    )
    assert set(r["paired_estimates"]) == set(ESTIMANDS)
    for res in r["paired_estimates"].values():
        assert res["n_pairs"] == 10
        assert res["sufficient"] is True
        assert res["ci_low"] is not None and res["ci_high"] is not None


# --------------------------------------------------------------------------- #
# Audits
# --------------------------------------------------------------------------- #


def test_audit_artifacts_clean(tmp_path, l1, arms, context, monkeypatch):
    # Build a mini run dir: pair dir with both arm ledgers + meta.
    pair = tmp_path / "pair_0"
    (pair / "treatment").mkdir(parents=True)
    (pair / "placebo").mkdir(parents=True)
    tr, pr = arms
    (pair / "treatment" / "event_log.jsonl").write_bytes(tr.log.to_jsonl_bytes())
    (pair / "placebo" / "event_log.jsonl").write_bytes(pr.log.to_jsonl_bytes())
    (pair / "live_tape.jsonl").write_text("\n".join(json.dumps(r) for r in l1.tape_rows) + "\n")
    meta = {
        "endpoint_env_vars": ["VERDI_VLLM_API_KEY"],
        "base_url_env_var": "VERDI_VLLM_BASE_URL",
    }
    (tmp_path / "g5_run_meta.json").write_text(json.dumps(meta))
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "sk-test-secret-never-written")
    out = audit_artifacts(tmp_path)
    assert out["clean"] is True
    assert out["api_key_value_scanned"] is True


def test_audit_catches_key_leak(tmp_path, monkeypatch):
    secret = "sk-test-secret-never-written"
    monkeypatch.setenv("VERDI_VLLM_API_KEY", secret)
    (tmp_path / "leak.txt").write_text(f"key was {secret}")
    with pytest.raises(G5PipelineError, match="api key value"):
        audit_artifacts(tmp_path)


def test_audit_catches_cross_arm_leak(tmp_path, l1):
    """A treatment ledger containing appeal_dismissed must be flagged."""
    pair = tmp_path / "pair_0"
    (pair / "treatment").mkdir(parents=True)
    bad = [json.loads(line) for line in L1_EVENT_LOG_PATH.read_text().splitlines()]
    bad_lines = [json.dumps(e) for e in bad]
    (pair / "treatment" / "event_log.jsonl").write_text("\n".join(bad_lines) + "\n")
    (pair / "placebo").mkdir()
    with pytest.raises(G5PipelineError, match="cross-arm leak"):
        audit_artifacts(tmp_path)


def test_generate_pair_tape_budget_guard(l1):
    """Exceeding the hard cap must raise before any network call."""
    import types

    fake_config = types.SimpleNamespace(model="Qwen3.8-27B", base_url="http://none")
    with pytest.raises(G5PipelineError, match="budget"):
        generate_pair_tape(
            l1,
            WORKTREE,
            Path("/nonexistent-pair-dir"),
            fake_config,
            used_calls=HARD_CALL_CAP - EXPECTED_SUFFIX_CALLS + 1,
        )


def test_run_arm_rejects_unknown_arm(l1):
    with pytest.raises(ValueError, match="unknown arm"):
        run_arm(WORKTREE, l1.tape_rows, "bogus")
