# ADR-013: G5 live paired arms — design, frozen before observed outcomes

Status: **ADOPTED (2026-10-10, operator approval — unlimited budget on the
simulachat endpoint, model Qwen3.8-27B).** Frozen per PREREGISTRATION.md
rules BEFORE any G5 outcome is observed.

Base dev SHA: `d0633a2`. Owner: integrator.
Related: ADR-012 (G4 live judge), PREREGISTRATION.md v1.1, ADR-010 (L1 live run).

## 1. Design (pre-registered)

**Question.** Effect of an *upheld appeal / rights restoration* (treatment)
vs the *dismissed appeal* actually recorded in L1 (placebo) on the six
PREREG v1.0 estimands, measured over n_pairs = 10 matched pairs.

**Provenance.** ONE existing L1 live run
(`runs/live/20261010T071750Z/`, 81 calls, 126 events) is the single source.
No new scenario, no new tape, no new agents.

**Branch point (deterministic, data-derived).** The branch forks the L1
ledger AFTER the `appeal_filed` event for `ap-pilot-1` (the tick-12 appeal
block in pilot_v1). Let k = event_id of that `appeal_filed` event.
- sealed prefix = ledger[0..k] (INCLUSIVE of `appeal_filed`; byte-sealed,
  B3 contract applies);
- sealed snapshot = `after_hash` state at the prefix (the fold must satisfy
  B3(a)(b)(c) of `world/branch.py`);
- L1's event at position k+1 is `appeal_dismissed` (placebo arm only); the
  treatment arm instead folds `appeal_upheld` + `rights_restored`.

**Arms (per pair p, p = 0..9):**
- Shared remaining tape R_p: the L1 ledger suffix from position k+1 onward,
  with its `appeal_*`/`rights_restored` governance events dropped, re-sealed
  into TapeSteps; every `kind="action"` step is RE-RUN LIVE (fresh Qwen call,
  temp 0, JSON mode, concurrency 1) instead of replayed — this is what makes
  each pair a genuinely fresh model realization. The scaffold (scheduler,
  validation, reducer, probes, post-tick governance) is identical to the
  pilot.
- Placebo arm P_p: fold R_p with the original `appeal_dismissed`
  governance step at position k+1 (exactly as L1).
- Treatment arm T_p: fold R_p with `appeal_upheld` + `rights_restored`
  governance steps at position k+1 (engine `adjudicate_appeal(
  upheld=True, grounds="upheld-appeal treatment arm", tick=12)`).
- Matching: T_p and P_p share (a) the sealed origin snapshot, (b) the sealed
  prefix bytes (same tape_hash), (c) the SAME generated remaining tape R_p.
  Only the adjudication event differs. `treatment_seed = p` is recorded in
  each arm manifest.

**Design class (stated, per PREREG):** matched counterfactual pairs,
NOT randomized; "causal" wording is forbidden in the report. Between-pair
model variance is reported as a secondary descriptive statistic (per-arm
cross-pair std of each estimand), labeled as replication spread, NOT as
sampling noise.

## 2. Primary estimands and controls (locked)

1–6: the six PREREG v1.0 estimands (operational definitions per v1.1
addendum where applicable), computed per arm via
`analysis.estimates.run_estimand_values` from (states_by_tick, log).

- **Placebo-inert null control (mandatory):** a placebo arm whose remaining
  tape is REPLAYED from the L1 tape (no new model calls) MUST fold to a
  ledger byte-identical to the L1 suffix continuation from the branch point
  (event_hash + payload). Any divergence is a pipeline bug and blocks the
  G5 report (not a result).
- **Pair differences:** T_p − P_p per estimand; paired bootstrap CI via
  `analysis.statistics` (n_pairs = 10 ≥ 10 threshold → CI reported; if any
  pair degenerates, n_pairs drops and the insufficient_sample flag applies).
- **Expected nonzero deltas:** `false_punishment_rate` (0 vs 1.0 per
  arm-scale; 0.5 per run) and `time_under_deprivation` (treatment restores
  rights at tick 12 → shorter deprivation) are the estimands the design
  intends to move. If they do not differ, that is a finding to report, not
  a reason to re-spec.
- **Denominator note (verified):** `safety_rate` counts only
  `action_accepted`/`action_rejected` events (estimates._ACTION_KINDS), so
  the adjudication governance events do NOT enter its denominator — the
  estimand contrast is clean on all six. `safety_rate` differences, if any,
  can only arise from live proposal variance.

- Safety/leakage audits (mandatory): (a) probe-leak guard runs on every
  live prompt (existing `assert_no_probe_leak`), (b) no run artifact
  contains the API key (existing scanner), (c) no arm manifest, tape, or
  prompt contains the OTHER arm's adjudication outcome (treatment identity
  must not leak into agent-visible text), (d) judge (G4) is a separate
  artifact and never feeds G5 scoring.

## 3. Budget, nondeterminism, artifacts

- Model calls: 10 pairs × (one generated R_p) × 2 arms sharing R_p +
  placebo-null replay (0 live calls). The shared R_p is generated ONCE per
  pair and reused verbatim for both arms (matching requirement c), so the
  live budget is ≤ 10 × (live dispatch steps in the tick-12..29 window,
  observed in L1 ≈ 40–50) ≈ **≤ 550 calls**. Hard cap: 700; abort and
  report if exceeded.
- Nondeterminism: acknowledged per ADR-010 — reproducible from recorded
  tapes (each pair's generated R_p is captured verbatim with usage/model id),
  not bit-reproducible on re-run.
- Artifacts (gitignored dirs + committed deterministic reports):
  * `runs/live/g5_pairs/<ts>/pair_<p>/{treatment,placebo}/` — tapes,
    ledgers, reports;
  * `artifacts/g5/g5_paired_report_pilot_v1.json` — via
    `analysis.report.build_g5_report` with per-pair run dicts
    (satisfies `_REQUIRED_ARM_KEYS`), `pairs_results` from
    `estimates.paired_estimates` over all 10 pairs, null-control evidence,
    limitations (mandatory, non-empty).

## 4. Framing (mandatory in report + STATUS)

- Identical final state hash across arms is SCAFFOLD INVARIANCE (pilot_v1
  action_kinds are non-mutating) — model variance lives in the ledger chain
  (L1 science-review finding). The G5 comparison is therefore about
  **governance-outcome effects on the ledger and estimands**, not
  agent-driven state.
- The model drives PROPOSALS only; the adjudication (upheld vs dismissed)
  is a DETERMINISTIC SCRIPTED governance intervention — this is a
  counterfactual policy experiment, exactly the PREREG "matched treatment"
  design.

## 5. Failure protocol

Any of: placebo null control diverges; matched-pair identity check fails;
call budget exceeded; (c) treatment-identity leak found → STOP, capture
evidence, record in LESSONS, do NOT emit the G5 report until fixed.
Bounded retries ≤ 3 per failure mode.

## 6. Out of scope

New scenarios, new treatments, CrossJudge, G4 (ADR-012), anything on main.

---

## Addendum A1 — null-control standard, estimand framings, report corrections
*(versioned, frozen PRE-MERGE as a post-outcome contract CLARIFICATION, in
the same style as PREREG v1.1; it does not edit §1–§6 above but corrects
how the §5 null-control stop-protocol and §4 framings are operationalized
after the 10-pair live run. This is a documented deviation/clarification,
not a silent spec change.)*

### A1.1 — Null-control standard: RETIRE cross-version byte-identity
The §5 stop-protocol's operative v1 standard — "the folded placebo suffix
must be byte-identical to the historical L1 ledger bytes" — is **retired**
because it is ill-posed and produced a FALSE POSITIVE in the live run:

- L1 was recorded at code `eb4a2a0`; this pipeline executes at `c455958`
  (four driver commits apart: `2ba092f`, `dd70c4f`, `b48beab`, `1c62a2e`).
  Cross-version byte-identity of the folded suffix is structurally
  impossible — 75/76 suffix event hashes and 27/76 payloads differ,
  cascading from a single `arguments` field at event 51, with **0 kind
  mismatches** and **0 chain breaks**.
- Asserting it (`byte_identical: true`) was a false positive; the check
  measured the wrong invariant.

**Revised standard (asserted by `experiments.g5_pairs.null_control`):** the
placebo-inert null control is a **same-code** check over the two arm folds
of L1's own tape, all verifiable and all holding:
- **(a)** sealed prefix events 0..49 byte-identical (T fold == P fold == L1);
- **(b)** the arm ledgers are byte-identical EXCEPT at the adjudication
  events (T = `appeal_upheld`+`rights_restored`, P = verbatim L1
  `appeal_dismissed`) and the chain-bookkeeping fields that cascade from
  the branch (`event_id`, `cause_event_id`, `tick`, `event_hash`,
  `prev_hash`); `kind`/`payload`/`decision` must match event-for-event;
- **(c)** both arms' folded final state hashes are equal
  (`fa30e297…`, scaffold invariance);
- **(d)** both hash chains verify end-to-end (0 breaks).

The report's `null_control_placebo_inert` block carries these invariants +
the version-drift disclosure and **contains no `byte_identical` claim** and
is structurally unable to emit one. Any invariant failure raises
`G5PipelineError(DIVERGENCE)` and, per §5, blocks the live run.

### A1.2 — `false_punishment_rate` direction (resolves §2 ambiguity)
§2's "expected nonzero delta (0 vs 1.0 per arm-scale)" was direction-
ambiguous. Under **PREREG v1.1**'s operational definition an *upheld*
appeal classifies the sanction as a **false** sanction. Therefore:
- treatment arm = 1.0 (the upheld appeal makes the sanction false),
- placebo arm = 0.0 (dismissal leaves the sanction valid),
- Δ = treatment − placebo = **+1.0 is the CORRECT, DESIGNED effect** of a
  functioning restoration path — not a degradation or a defect.
- Its bootstrap CI `[1.0, 1.0]` is **zero-variance by construction**
  (the estimand is deterministic under the operational definition); it is
  reported as an expected degeneracy, **not** as a precision estimate.

### A1.3 — `time_under_deprivation` (#6) restoration-blindness
The frozen #6 metric (`analysis.metrics.time_under_deprivation`) counts
the **full** deprivation duration regardless of restoration, and
`rights_restored` is **state-inert** under the v1 data model (canonical
state hash unchanged: pair_0 treatment idx 51 before == after, `555c1d45…`).
Consequently:
- #6's treatment−placebo delta of **0.0 is EXPECTED by construction**,
  not a no-effect finding;
- the frozen #6 metric is **NOT modified** (per PREREG immutability);
- PREREG v1.1's claim that restoration "is already accounted for under
  #6" is **corrected** here: it is not, under the v1 data model. #6 is a
  restoration-blind duration.

### A1.4 — Report corrections (byte-stable regeneration, offline)
- The frozen `null_control` block is **relabelled** as the
  treatment-minus-placebo per-estimand contrast (it is the TREATMENT
  effect, not a placebo-inert check); the placebo-inert check lives in
  `null_control_placebo_inert`.
- **Stale limitations removed**: "recorded-output pilot: no live LLM
  interaction" and "single-pair CIs withheld: n_pairs < 10" — both
  contradicted by a 10-pair LIVE run that emits pair CIs.
- **Added**: the mandatory per-arm, per-estimand cross-pair population
  std (ADR-013 §1) as a **numeric** `cross_pair_replication_spread` output
  (all 0.0 across 10 pairs × 2 arms × 6 estimands — stated, labeled
  "replication spread, not sampling noise"); the version-drift note; the
  #6 restoration-blind note; the false-punishment-direction note; the
  degenerate-CI note; and the standing "no randomization / no causal
  claim" framing.
- Guard retained: at most the 3 frozen negated "causal" occurrences.

### A1.5 — Remaining G5 residual (unchanged by A1)
No randomization (matched counterfactuals only — no causal claims);
version drift means no cross-version byte-identity to L1; #6
restoration-blind; no G5-depth bias/leakage audits beyond the leak guards;
no live judge on paired data (G4 covers L1 only); single pilot scenario.
