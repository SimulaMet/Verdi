# Execution state (integrator-owned)

Updated UTC: 2026-10-10 10:55 (Tranche 6: LIVE tranche L1+L2 integrated — first live-model run + G4 live SimpleAudit audit + P3 reliability)
Repository: SushantGautam/verdi
Branch: dev (development) / main (public only)
Stage: P1 vertical slice + P2 (counterfactual/treatments/statistics) + LIVE tranche: first real Qwen3.8-27B run (L1) with full capture + tape replay, G4 live SimpleAudit audit (L2), P3 reliability/scheduler stress
Gates:
- G0 (specs/contracts)      PASS — frozen v1 schemas, manifest, P2 preregistration (v1.0 + v1.1), ADR-010 live-tranche contract (frozen pre-outcome), ADR-011 SimpleAudit
- G1 (unit+schemas+format)  PASS — 337 tests, ruff clean
- G2 (cross-component)      PASS — pilot wires all modules; experiments/arms consume world+analysis; live driver consumes agents+world+audit
- G3 (replay/invariants)    PASS — full 126-event replay byte-identical (recorded AND live); L1 tape replay reproduces the model-driven ledger 126/126 (event_hash + payload); branch B1-B5
- G4 (SimpleAudit/judge)    PARTIAL — LIVE bridge now RUN (L2): sealed-event_id correlation 81/81, no orphans/duplicates, never positional, SimpleAudit 0.4.0 used where it fits, byte-stable deterministic report. NOT closable for full PASS: fixed 10-sample all-accepted (sole rejection event 42 out-of-sample, 71 unjudged — disclosed), no independent live-judge-model with reported disagreement (needs new ADR + model-call budget), RunReport.simpleaudit_version populating is integrator follow-up
- G5 (statistical sanity)   PARTIAL — machinery closed (Tranche 5); L1 is a SINGLE live run, adds no paired-arm evidence; residual list intact
- G6 (public docs)          NOT RUN — P4

## Tranche 6 (this update): LIVE tranche L1 + L2 + P3
Live environment (credentials in gitignored .env, env vars only, NEVER in Git):
endpoint OpenAI-compatible (simulachat), model Qwen3.8-27B, SimpleAudit 0.4.0
installed in .venv. ADR-010 (live contract) + ADR-011 (SimpleAudit) frozen
BEFORE the live outcome.

L1 (2ba092f + dd70c4f fixes) — first live-model pilot run:
- 81 real Qwen3.8-27B proposals (temp 0, JSON mode, concurrency 1), 126 ledger
  events, 0 quarantined, call_count 81 <= 120 budget. Full verbatim capture
  (prompt + response_raw + usage + reasoning + trace/request ids) to
  runs/live/20261010T071750Z/ (gitignored). Key verified ABSENT from all
  artifacts. Final state hash fa30e297...
- CRITICAL HONEST FRAMING (from independent science review): the identical
  final state hash is a SCAFFOLD-INVARIANCE property, NOT evidence the model
  drove substantive state — every pilot_v1 action_kind is NON-mutating
  (absent from reducer MUTATING_KINDS), so the canonical state hash is fully
  determined by the deterministic scaffold (seed + scripted governance).
  The model DID drive the LEDGER: live event_hash 0/126 shared with recorded,
  80/126 action payloads differ, 70 distinct (action_kind,reason) pairs.
  Report/STATUS must keep this framing; "reproduced the same final state
  hash" alone would be a measurement illusion.
- Reproducibility = reproducible-from-recorded-tape: run_pilot_from_live_tape
  (TapeReplayCaller) replays the captured tape to a BYTE-IDENTICAL 126-event
  ledger (event_hash 126/126, payload 126/126, hash fa30e297). Live endpoint
  itself is not bit-reproducible (documented).
- Review round: engineering PASS (1 minor: pooling limitation documented),
  science PASS (1 major doc finding -> fixed). Recorded-path invariance proven
  byte-identical (reviewer ran parent vs current driver, all outputs identical).
- Deviation (mechanical, not contract): per-dispatch fresh ModelClient because
  the driver runs asyncio.run() per dispatch (fresh loop); a single httpx pool
  can't survive closed loops. Acceptable at concurrency-1/<=120 calls; pooling
  needed for high throughput (documented).

L2 (f852895 + fix) — G4 live SimpleAudit audit over the captured L1 run:
- Sealed event_id correlation: 81 tape records <-> 81 dispatch events, 0
  orphan spans, 0 missing events, 0 duplicates, never positional, ambiguity
  -> LiveLedgerError (no silent mis-pick). (actor,task) uniqueness guard test
  pins the pilot_v1 invariant.
- Judge: SimpleAudit grade_choice_match + normalize_span + validate_decision
  (code-only, 0 model calls); ModelAuditor/CrossJudge documented as not-fit
  (need live Target + judge LLM). HONEST METRIC FRAMING (review): measures
  closed-form compliance-form agreement, DETERMINISTIC on accepted rows, NOT
  independent model validation. Sample: fixed 10 (first dispatch/tick) = ALL
  ACCEPTED; sole engine rejection (event 42, tick 11) fell OUTSIDE sample;
  71 unjudged — all disclosed in report with exact counts + judge_metric section.
- Deterministic G4 report byte-stable: artifacts/live_audit/g4_live_audit_*.json.
- Review: engineering PASS; science INCONCLUSIVE (not FAIL) -> F1/F2 disclosure
  + metric-framing majors FIXED (5908cbb); G4 stays PARTIAL (see residual).

P3 (recovered + new, offline):
- P3 client retry/backoff soak (eb4a2a0): deterministic, no network, no real
  sleeps (asyncio.sleep recorder), no credential leakage in errors.
- P3 scheduler stress (WS-7, dc90d44): determinism at scale (4 signatures
  incl. reversed input), tie-break reorder under different seeds, full-
  collision total order, 1000-task permutation identity, causality (perturbed
  agent moves only its own tasks). Review PASS.
- P3 reliability soak (WS-6, 8e87597): RECOVERED from a dead worktree where the
  file was uncommitted and the branch merged as an empty no-op; re-committed on
  a fresh branch; ruff B023 + zip-strict fixed. 307-line deterministic soak.

## G4 residual (what closes G4 full PASS)
1. Independent LIVE judge-model pass over the sample (SimpleAudit
   ModelAuditor/CrossJudge) with reported disagreement — needs a new ADR +
   model-call budget (judge LLM calls), out of ADR-011 "no new model calls".
2. A rejection row IN the judge sample (or a run that produces one in-sample)
   so the compliance-form metric is non-tautological.
3. RunReport.simpleaudit_version populated from the live audit (integrator
   follow-up; pilot not modified this tranche).

## G5 residual (unchanged by L1 — single live run adds no paired evidence)
Live paired arms (non-inert treatment), n_pairs>=10, randomization/identification
strategy, bias/leakage audits on live data, independent live judge, paraphrase
leak detection. (See Tranche 5 residual list.)

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
- [x] Tranche 6: L1 first live run + capture + replay; L2 G4 live SimpleAudit
      audit; P3 client soak + reliability soak + scheduler stress
- [ ] G4-close: independent LIVE judge-model pass (new ADR + model-call budget)
- [ ] P2-live: live paired arms (n_pairs>=10, non-inert treatment) to close G5
- [ ] P4 Curated public release (human-only approval)

## Rollback point
Last good checkpoint before this tranche: dev `95098cd` (P2/P3, all tests pass).
Current: dev `d57689a` (provenance path fix after worktree cleanup).

## Risks/blockers
- Live endpoint + SimpleAudit ARE now available (credentials in gitignored
  .env, env vars only, never in Git). L1 done once (81 calls); further live
  budget is operator-gated per ADR-010.
- G4 full PASS and G5 full PASS both require LIVE model-judge / paired-arm
  budget that is out of scope without a new ADR + operator go-ahead.
- Qwen3.8-27B is a reasoning model (separate reasoning_content field, 50-900
  reasoning tokens); max_tokens must be >=1024 or content can be empty.
- Leak guard is verbatim-containment only; paraphrased leakage out of scope
  (documented in code + tests).
- Scientific novelty must be re-assessed against emerging research.

## Next 3 actions
1. G4-close (operator budget needed): write ADR for an independent LIVE
   judge-model pass (SimpleAudit ModelAuditor/CrossJudge) over a sample that
   includes at least one engine-rejected row, run it, report disagreement,
   then flip G4 to PASS. Requires user approval for judge-LLM model spend.
2. G5-close (operator budget + non-inert treatment): stand up live paired arms
   against a tape/scenario that contains an upheld appeal / rights_restored
   (pilot_v1 appeal is dismissed -> rights_restoration treatment is inert),
   n_pairs >= 10, then close G5 per the residual list.
3. RunReport.simpleaudit_version integrator follow-up: populate the field from
   the live audit so the public report carries the SimpleAudit version.

## Update discipline
Append per integrated tranche: date/time UTC, exact dev SHA, workers/branches,
outcomes, verification commands+exit status, artifacts/manifests, next actions,
rollback point, blockers. Never silently replace unverified estimates with PASS.
