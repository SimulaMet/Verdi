# Execution state (integrator-owned)

Updated UTC: 2026-10-09 22:54 (Tranche 5: P2 counterfactual/treatments/statistics integrated + G5 pre-review remediated)
Repository: SushantGautam/verdi
Branch: dev (development) / main (public only)
Stage: P1 vertical slice COMPLETE (G3 independently signed off) + P2 counterfactual branching, controlled treatments, G5 statistics + report path integrated
Gates:
- G0 (specs/contracts)      PASS — frozen v1 schemas, manifest, contract fixture, P2 preregistration (v1.0 + v1.1 operational addendum)
- G1 (unit+schemas+format)  PASS — 291 tests, ruff clean
- G2 (cross-component)      PASS — pilot wires all modules t0-t11; experiments/arms consume world+analysis
- G3 (replay/invariants)    PASS — confirmed by INDEPENDENT re-review (Tranche 4) + P2 WS-1 re-verified: full 126-event replay byte-identical; branch replay from event 28 reproduces pilot final hash; B1-B5 guarantees tested
- G4 (SimpleAudit/judge)    PARTIAL — offline remainder closed (Tranche 5/WS-5): absence path, span-name + event_id-correlation contract, judge contract pinned by tests; live SimpleAudit bridge still NOT run (package not installed; expected offline path)
- G5 (statistical sanity)   PARTIAL — machinery closed: 6/6 preregistered estimands wired (4 exact, 2 via PREREG v1.1 operational addendum), matched-pair + bootstrap CI + null control over real differences, deterministic G5 report artifact; NOT closable offline: needs live randomized paired arms with n_pairs>=10 and non-inert treatment (see residual list)
- G6 (public docs)          NOT RUN — P4

## Tranche 5 (this update): P2 workstreams WS-1..WS-5 integrated
- Frozen BEFORE outcomes (bc5266d): docs/PREREGISTRATION.md v1.0, docs/P2_CONTRACT.md
  v1.0, ADR-008 (reducer tick = transition counter), ADR-009 (stdlib-only, freeze
  discipline). Post-freeze doc edits: none until the v1.1 operational addendum
  (71a2b34), which only APPENDS and documents two operational estimand definitions.
- WS-1 (1407ebc) src/verdi/world/branch.py: branch_from_snapshot folds a remaining
  recorded tape from a sealed snapshot, continuing the hash chain; B1-B5 guarantees.
- WS-3 (b2137a0) src/verdi/analysis/statistics.py: stdlib bootstrap_ci,
  paired_effect (PairedResult, sufficient=n_pairs>=10), null_control_passes.
- Review round 1 (WS-1/WS-3): engineering PASS (4 minor), science PASS (7 notes).
  Fix 5c2fe7f: B3 extended — snapshot must equal prefix ledger terminal after_hash
  (regression test); origin_tick documented as caller metadata; percentile pinned
  to statistics.quantiles equivalence; pilot-branch test relabeled as
  replay-equivalence invariant (NOT counterfactual evidence).
- WS-2 (0922f29) src/verdi/experiments/: RegimeConfig (schema-frozen, metadata-
  carried), record-only deterministic treatments (TreatmentOutcome), MatchedPair
  validated on shared origin_state_hash + tape hash, regime fixtures, arm tapes
  (tape-referenced, not duplicated). Honest: rights_restoration is inert
  (applied=False) on pilot_v1 — the appeal was dismissed; no world state invented.
- Review round 2 (WS-2): engineering PASS, science PASS (one major-scoped seam:
  estimand wiring -> became WS-4).
- WS-5 (999fda7) tests/audit/test_g4_offline.py: SimpleAudit absence path,
  span "name" pin, event_id (not positional) correlation, judge match>divergence,
  report-carries-G4-fields. No source changes needed.
- WS-4 (a77254e) src/verdi/analysis/estimates.py + report.py G5 section: 6/6
  preregistered estimands mapped to metrics.py; paired estimates (single pair =>
  sufficient=False, CI withheld); deterministic G5 report + byte-stable writer.
- G5 pre-review (independent, rigorous): FAIL on 2 BLOCKERS (sustained_recovery
  and false_punishment_rate deviated from frozen PREREG definitions) + 3 majors
  (vacuous null control, silent run-dict defaults, paired_estimates([]) no-op).
- Remediation (71a2b34): PREREGISTRATION v1.1 operational addendum (frozen v1.0
  untouched) declaring the exact operational formulas the recorded-tape data model
  supports; null control now over actual per-estimand differences (null + marker
  when <2 pairs); build_g5_report validates run-dict keys; empty pairs raise.
  Fail-before/pass-after evidence for every fix.
- Re-review (blocker closure): PASS — F1/F2/F3b/F1b/F2b all CLOSED with
  independent probes; no new issues; G5 remains PARTIAL by design (offline).
- Integrator commit 298b75e: analysis/__init__ re-exports P2 public API; G5
  report artifact emitted: artifacts/g5/g5_report_pilot_v1.json (5.5 KB, 6
  estimands, randomized=false, null_control passed=true; gitignored by design).

## G5 residual list (what evidence closes G5) — from independent review
1. Live paired arms (real model, not recorded tape) with a non-inert treatment.
2. n_pairs >= 10 per estimand so real CIs are computed (currently 1 pair, CIs
   withheld by design).
3. Randomization or an explicit identification strategy for the treatment effect.
4. Bias/leakage audits on live data (self-report vs observed behavior probes).
5. Independent judge running on live probes (not the offline rubric only).
6. Paraphrase-level leak detection (verbatim containment only today).
7. G4 live SimpleAudit bridge (same blocker: package + endpoint absent).

## Tranche 4 (this update): independent re-review + follow-up fixes
- Both independent re-reviews of Tranche 3 (engineering + science) returned
  **PASS**: all prior HIGH/MED/LOW findings CLOSED with evidence (216 tests,
  byte-identical 126-event replay, 45/45 unique in-range audit correlation,
  leak guard invoked 30x live and raises on planted leak, no new blocking defects).
- Two non-blocking notes accepted and fixed on `fix/review-findings-2`:
  A. (MED, interpretation) welfare/inequality metrics were keyed by the reducer's
     cumulative transition counter (126) instead of the snapshot's lifecycle
     tick. Now keyed by lifecycle tick {0}+SNAPSHOT_TICKS;
     time_under_deprivation horizon = total_ticks-1 (29), not 126.
  B. (LOW) root span used key `span_name` while the adapter contract reads `name`.
- New regression: test_welfare_keyed_by_lifecycle_tick. 217 tests, ruff clean.
- dev SHA: `6a632bd` (pushed). Rollback point: `0373fc4`.

## Tranche 3: review-findings remediation
- Integrator branch: `fix/review-findings` off `ef69f0f`, merged --no-ff to dev.
- dev SHA: `9798907` (pushed to origin/dev).
- Independent reviewers (prior turn): Engineering = FAIL; Science = INCONCLUSIVE (G3-blocking).
  Findings addressed:
  1. (Science HIGH / G3) `ProbeBank.leak_check` never called in the run.
     -> Wired into the per-tick loop over ALL agent-visible surfaces (persona
        summaries, public signals, task public_observations, rendered policy
        prompts). Raises `ProbeLeakError` on any verbatim dilemma leak.
  2. (Eng HIGH) Governance audit correlation used a contiguous 0-based index that
     never matched real (gapped) ledger ids. -> Correlate by actual sealed.event_id;
     regression test asserts `ar.event_ids == [real_id]`.
  3. (Eng MED) Driver wrote span key `span_name`; adapter reads `name`, so
     `span_names` was always empty. -> Now uses `name`; regression asserts populated.
  4. (Eng MED) `WorldState.constitution_version` never tracked enacted amendments.
     -> Reducer gains a deterministic `governance` action_kind that records
        constitution_version on enactment; `emit_governance` routes through
        `reducer.apply` so live == replay.
  5. (Eng MED) G3 replay covered only 81 action steps, not the 126-event ledger.
     -> `replay_check` folds the FULL tape (action + governance steps) and asserts
        byte-identical JSONL + final state hash, no force-copies.
  6. (Eng LOW) httpx transport errors non-retryable. -> `TransportError`
     (connect/timeout) retried with capped backoff; regression tests added.
  7. (Eng LOW) `TaskPool.active` racy. -> Documented as single-event-loop safe.

## Verification (commands + exit status, this tranche)
- `.venv/bin/python -m pytest`  -> 291 passed, exit 0
- `.venv/bin/ruff check .`      -> "All checks passed!", exit 0
- Pilot smoke: `PilotDriver(Path('.')).run()` -> 126 events, byte-identical
  full-ledger replay (unchanged from Tranche 3/4).
- G5 artifact: artifacts/g5/g5_report_pilot_v1.json (5532 bytes, deterministic
  bytes across two builds — asserted by test_g5_report_pilot_v1).
- Independent reviews this tranche: WS-1/WS-3 eng PASS + sci PASS; WS-2 eng PASS
  + sci PASS; WS-4/WS-5 G5 pre-review FAIL(2 blockers) -> remediated -> closure
  re-review PASS. No self-awarded gates.

## Actual implementation (working)
- P0: package, config/CLI, frozen v1 schemas + fixtures, CI.
- P1-A world (log/reducer/snapshot/replay); P1-B agents + scheduler;
  P1-C governance; P1-D audit + measurement; P1-E scenario + analysis;
  P1-I cross-module pilot driver (t0-t11).
- P2: world/branch.py (counterfactual snapshot branching, B1-B5), experiments/
  (regimes, record-only treatments, matched pairs), analysis/statistics.py
  (bootstrap/paired/null-control, stdlib-only), analysis/estimates.py (6/6
  preregistered estimands), analysis/report.py G5 section (deterministic report
  + writer), tests/audit/test_g4_offline.py (G4 offline contracts).
- Recorded-output pilot (no live vLLM). `SimpleAuditNotInstalledError` is the
  expected offline path; simpleaudit bridge is lazy-import only.

## Work queue
- [x] P0-01..P0-03 foundation, contracts, tooling
- [x] P1-A..P1-E modules; P1-I integrated t0-t11 pilot
- [x] Tranche 3: review-findings remediation
- [x] Tranche 5: P2 WS-1 branching, WS-2 treatments/pairs, WS-3 statistics,
      WS-4 estimand wiring + G5 report, WS-5 G4 offline remainder
- [ ] P2-live: live paired arms (n_pairs>=10, non-inert treatment) to close G5
- [ ] P3 GPU performance, reliability, judge validation (needs endpoint)
- [ ] P4 Curated public release (human-only approval)

## Rollback point
Last good checkpoint before this tranche: dev `d62565a`.
Current: dev `298b75e`.

## Risks/blockers
- No authenticated Qwen/vLLM endpoint + SimpleAudit not installed in this env:
  all model calls are recorded-tape stand-ins; live G4 bridge and G5 need a real
  endpoint. Credentials from env vars only; none in Git.
- Leak guard is verbatim-containment only; paraphrased leakage out of scope
  (documented in code + tests).
- Scientific novelty must be re-assessed against emerging research.

## Next 3 actions
1. P2-live (blocked offline): stand up live paired arms against an
   authenticated Qwen/vLLM endpoint with a non-inert treatment, n_pairs >= 10,
   then close G5 per the residual list above (requires credentials/env the
   operator must supply).
2. G4-live (blocked offline): install SimpleAudit on PYTHONPATH (or
   VERDI_SIMPLEAUDIT_PATH), run the live bridge + independent live judge,
   report disagreement — then flip G4 to PASS.
3. P3 prep (offline-doable): reliability/retry soak on the agent client +
   deterministic scheduler stress fixture, in preparation for GPU runs.

## Update discipline
Append per integrated tranche: date/time UTC, exact dev SHA, workers/branches,
outcomes, verification commands+exit status, artifacts/manifests, next actions,
rollback point, blockers. Never silently replace unverified estimates with PASS.
