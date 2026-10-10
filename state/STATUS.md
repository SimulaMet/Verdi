# Execution state (integrator-owned)

Update UTC: 2026-10-10 23:40 (post-Tranche 9: operator reviewed and approved the G5-close framework; operator will verify independently and run the other-family cross-judge later on a separately supplied endpoint; model policy confirmed — SimulaChat default Qwen only; dev pushed)

Updated UTC: 2026-10-10 22:15 (Tranche 9: G5-close (ADR-014, Qwen-only) live Qwen judge pass over the G5 paired data + G5-depth bias/leakage audits + identification strategy; independent engineering+science reviews PASS after remediation; G6 public release SKIPPED per operator)
Repository: SushantGautam/verdi
Branch: dev (development) / main (public only)
Stage: P1 vertical slice + P2 + LIVE tranche (L1/L2/P3) + G4 live-judge pass (ADR-012, integrated) + G5 live paired arms (ADR-013, integrated) + G5-close (ADR-014, integrated)
Gates:
- G0 (specs/contracts)      PASS — frozen v1 schemas, manifest, P2 preregistration (v1.0 + v1.1 + v1.2 erratum), ADR-010, ADR-011, ADR-012 (+ Addendum A1, A1-1 DEFERRED by operator), ADR-013 (+ Addendum A1, A1 frozen pre-merge), ADR-014 (frozen pre-outcome)
- G1 (unit+schemas+format)  PASS — 432 tests, ruff clean
- G2 (cross-component)      PASS — pilot wires all modules; experiments/arms consume world+analysis; live driver + judge_live consume agents+world+audit
- G3 (replay/invariants)    PASS — full 126-event replay byte-identical (recorded AND live); L1 tape replay 126/126; branch B1-B5; G5 same-code chain integrity invariants hold (both arms verify)
- G4 (SimpleAudit/judge)    PARTIAL — live-judge pass RUN (ADR-012): sealed all-81 sample (sole DISPATCH rejection event 42 in-sample), sealed-event_id correlation 81/81, 81 judge calls ≤96 budget, 80/81 agreement (Wilson 95% CI 0.9333–0.9978; per-class CIs, n=1 class flagged), single disagreement (event 42) enumerated as a STRUCTURAL artifact of the code-judge closed-form rule; measured quantity is JUDGE-TO-ENGINE SELF-CONSISTENCY (judge shown engine outcome; blinding satisfied by code judge only), same-family self-judgment disclosed, judge advisor-only, report byte-stable from tape (sha256 2296dcb4…, artifacts/live_audit/g4_judge_20261010T132817Z.json). NOT closable for full PASS: A1-1 independent different-family cross-judge pass DEFERRED by operator — independent-judge evidence pending an operator-supplied endpoint/model
- G5 (statistical sanity)   PARTIAL — machinery closed (Tranche 5) + ADR-013 live paired arms (n_pairs=10, 470 calls, 0 quarantines) INTEGRATED (merge a1357e8): per-pair shared R_p replayed byte-identically into treatment (rights_restored) vs placebo (dismissed); treatment−placebo contrast over all 10 PREREG v1.0/v1.1 estimands (paired, n_pairs=10): exactly ONE estimand changes — false_punishment_rate 0.0→+1.0 (CI [1.0, 1.0], zero-variance by construction, the DESIGNED effect of the upheld appeal under the PREREG v1.1 operational definition); the other five are 0.0 (time_under_deprivation 0.0 is restoration-blind by construction, ADR-013 A1.3) — same-code null control (sealed prefix / aligned suffix / folded finals / chain integrity) PASSES with L1@eb4a2a0 vs G5@c455958 version drift disclosed; scaffold invariance final_state_hash fa30e297… held on all 10 pairs; report byte-stable (sha256 fbc1fe13…). Independent science closure re-review PASS (2026-10-10). + G5-CLOSE (ADR-014, Tranche 9, integrated): live Qwen3.8-27B judge pass over the SEALED G5 sample (504 rows = complete census of unique G5 model behavior: 470 fresh suffix + 34 L1-replayed prefix rows, prefix byte-identical across all 10 pairs; 505 calls incl. 1 smoke, 0 quarantined, cap 700): judge-to-engine SELF-CONSISTENCY 504/504 (1.0, Wilson 95% [0.9924, 1.0]; prefix 34/34, suffix 470/470), 0 disagreements; engine breakdown 503 accepted / 1 rejected (the pair_0 event-42 rejection, n=1, flagged not statistically meaningful); same-family self-judgment + judge-shown-engine-outcome disclosed; advisor-only. G5-depth bias/leakage audits (offline, pure): B1 structural self-report consistency 810/810 (honest scope: plumbing, not full ADR B1 — scope flag + bounds_check_implemented:false in report), B2 leakage 0 hits (ground-truth tokens / key material / ordering-leak), B3 quarantine vector all 0, B4 cross-pair prompt invariance 0 drift (470 suffix rows). Identification: estimand statement + 3 assumptions + not_claimed block; leave-one-pair-out stable (all zeros/ones by construction); seed-0 permutation null (200 perms, deterministic, 57/200 exact-zero means) contains 0 for all estimands — a design-robustness diagnostic, EXPLICITLY not randomization; ADR §5's "tied to the manipulation" wording deliberately NOT inherited. Report byte-stable (sha256 51ad15c3…, artifacts/g5/g5_close_report_v1.json, gitignored), causal-word guard 2 negated occurrences, no key material, frozen deviations_from_contract (6) + 5 limitations. Residuals (honest, frozen): no randomization → no causal claim; paraphrase-level leak detection out of scope (verbatim only); cross-family judge deferred (operator); single pilot scenario
- G6 (public docs)          SKIPPED — operator directive 2026-10-10 ("skip, just push online now"); dev pushed to origin; human-only curated release remains available for later

## Tranche 9 (this update): G5-close (ADR-014, Qwen-only) — dev a8e9880
Operator directive 2026-10-10 (verbatim intent): (1) skip the cross-family
judge for now; (2) do what is doable with SimulaChat default Qwen only;
(3) skip the G6 public release and push dev online now. This tranche scopes
the Qwen-only portion of the G5 residual list: a live model-judge pass over
the SEALED G5 paired tapes, G5-depth bias/leakage audits on live data, and an
explicit identification strategy. Cross-family judge (ADR-012 A1-1) stays
DEFERRED; only Qwen3.8-27B / simulachat is used.

ADR-014 (frozen pre-outcome, branch 23c9216): sealed 504-row judge sample
(pair_0 all 81 + pairs 1..9 suffix 47 each) — verified to be a complete
census of unique G5 model behavior (the 34-row prefix is byte-identical
across all 10 pairs, so only pair_0's copy is needed; 470 fresh suffix +
34 L1-replayed prefix). 505 expected calls / 700 hard cap, concurrency 1,
max_retries=0 (client-internal retries are real HTTP calls and would breach
the cap), temperature 0.1.

Implementation + review (branch feat/adr014-g5-close-qwen-only):
- New modules: src/verdi/experiments/g5_close.py (sealed sample manifest,
  B1..B4 audits, identification diagnostics, byte-stable report builder +
  causal-word guard) and g5_judge_live.py (live Qwen judge, advisor-only,
  mockable, cap enforcement, cap-abort meta recording).
- Independent engineering review: 1 HIGH (client max_retries=5 could breach
  the 700 cap via internal 429/502/503 retries) + 4 MED + 4 LOW. Science
  review: 0 blocker/high, no overclaim in code; 3 MED (silent ADR §3 gaps,
  B1 narrower than ADR text, ADR §5 sentence overstates the permutation
  null) + 4 LOW + 2 NF. ALL findings remediated (commit 670986b):
  max_retries=0 enforced (one logical call == one HTTP request); n_suffix_rows
  corrected (470); frozen deviations_from_contract block (6) + explicit
  code_judge.available=false section; B1 honest scope flag +
  bounds_check_implemented:false; ADR §5 "tied to the manipulation" wording
  deliberately NOT inherited (frozen limitations line); dead code removed;
  cap-abort meta recording; row_provenance + n<=1 statistical-meaningfulness
  flags; JSON-escape blind spot noted; __all__ dedup.
- Integrator fix b525b62 (live-run blocker): the program .env uses
  `export KEY="value"` lines; the minimal .env loader kept the `export `
  prefix in the key, so VERDI_VLLM_* never reached os.environ. Fixed +
  regression test. (Also be95bae: test_g5_pairs.py no longer hardcodes the
  removed worker worktree — repo root resolved from the test file location.)

LIVE JUDGE RUN (Qwen3.8-27B / simulachat, runs/live/g5judge/20261010T215000Z/
gitignored): 505 calls (1 smoke + 504), 1661s, 0 quarantined, under the 700
cap, git_sha a8e9880. RESULT: judge-to-engine self-consistency 504/504
(rate 1.0, Wilson 95% [0.9924, 1.0]; prefix 34/34, suffix 470/470),
0 disagreements. Engine outcome breakdown: 503 accepted / 1 rejected (the
pair_0 event-42 supply_misallocation rejection — the same structural G4
artifact; n=1, flagged "not statistically meaningful"). HONEST FRAMING
(report `judge.framing`, verbatim): the judge is same-family (Qwen judging
Qwen), SHOWN the engine outcome, so this measures judge-to-engine
SELF-CONSISTENCY on the G5 paired data, NOT an independent compliance
verdict; blinding is satisfied by the deterministic code judge only (which
has no G5 artifact — it runs on the L1 all-81 sample per ADR-012, recorded
as code_judge.available=false); the judge is advisor-only (never feeds world
state / scoring / ledger).

Bias/leakage audits (offline, pure functions over the run root): B1
self-report structural consistency 810/810 (honest scope: the parser forces
task_id/actor_id, so this measures plumbing consistency, not self-report vs
behavior — scope flag + bounds_check_implemented:false in the report); B2
leakage 0 hits (no response contains appeal_upheld/rights_restored/
appeal_dismissed; no key material; no within-tape ordering-leak — JSON-escape
blind spot disclosed); B3 quarantine symmetry all 0; B4 cross-pair prompt
invariance 0 drift over 470 suffix rows.

Identification (frozen; no causal claim): estimand = within-pair T−P contrast
on the six PREREG estimands holding the model-generated stream fixed; 3
assumptions (same-code replay, scaffold invariance, single manipulation);
not_claimed = [causal/ATE, external validity, population generalization].
Diagnostics: leave-one-pair-out stable (constants by construction — tests the
paired-estimate machinery, not effect heterogeneity); seed-0 permutation null
(200 sign-flip perms, deterministic, 57/200 exact-zero means) contains 0 for
every estimand — explicitly a design-robustness diagnostic, NOT a
randomization claim.

Report: artifacts/g5/g5_close_report_v1.json (gitignored), regenerated
OFFLINE from the judge tape (pure function of run root + manifest + tape;
double-build byte-stable) — sha256
51ad15c3d07236d991e49a8cd0cc421774c58a2947e1568084a7d96d9d25cd27. Sections:
judge (agreement + CIs + framing + row_provenance + engine breakdown +
n<=1 flag + code_judge), bias_audits (B1..B4), identification, context,
limitations (5), deviations_from_contract (6). Causal-word guard: 2
occurrences, both negated. No key material in any artifact.

Verification (post-merge, dev a8e9880, main checkout): pytest →
432 passed, 1 skipped; ruff check . → All checks passed. Live-run integrity:
no key material in judge tape/meta/report; manifest recompute
fe38443a…a336df matches the committed constant; 504/504 verdicts parse.

G6: SKIPPED per operator (human-only curated public release remains a later
manual step; nothing was merged to main).

## Tranche 8 (this update): G5 paired arms (ADR-013) integrated — dev a1357e8
G5 live paired-arms run (ADR-013) merged to dev serially (merge commit
a1357e8, 2026-10-10), after independent engineering review PASS + science
review FAIL → remediation → science closure re-review PASS. Worker
feat/adr013-g5-paired-arms (branch base 26fb369; final branch commits
0600a62 + f48f4ae). Merge topology verified safe: G5 branch touched only
4 files, none also changed by dev → zero conflicts; G5-side state/ net
zero → dev's state and the ADR-012 DEFERRED version preserved verbatim.

Live run (runs/live/g5_pairs/20261010T162928Z/, gitignored): 10 pairs,
470 live Qwen3.8-27B calls (10 × 47 shared R_p), 0 quarantines, under
HARD_CALL_CAP 700. After sealed L1 event 49 (appeal_filed),
one shared R_p response tape (47 calls) is replayed BYTE-IDENTICALLY into
both arms; arms diverge at exactly one event — treatment: engine-sourced
appeal_upheld + rights_restored (frozen adjudication override, grounds
frozen pre-run); placebo: verbatim L1 appeal_dismissed. Scaffold
invariance (restoration is a state change, not a scaffold change) held on
ALL 10 pairs: identical final_state_hash
fa30e29786877c6534013a2f6a2683a6d19e1cb136a66830d35cd9a8defdd582, 127
treatment vs 126 placebo events (the +1 is rights_restored).

Scientific results (treatment − placebo, paired, n_pairs=10): exactly ONE of
the six PREREG estimands changes —
false_punishment_rate 0.0→+1.0 (CI [1.0, 1.0], zero-variance by
construction: an upheld appeal classifies the sanction as false under the
PREREG v1.1 operational definition, so +1.0 is the DESIGNED effect of a
functioning restoration path, ADR-013 A1.2). The other five are all
0.0 across arms (time_under_deprivation 3.0==3.0, welfare_mean_food
5.0==5.0, safety_rate, inequality_gini_food, sustained_recovery all
unchanged — including #6, whose 0.0 delta is restoration-blind by
construction, ADR-013 A1.3). Honest reading: the manipulation's observable
effect is confined to the ledger's sanction-status classification;
welfare/inequality/safety/food metrics are unaffected by the adjudication
outcome in this scenario.

Null control (the FAIL→PASS core fix): the cross-version byte-identity
standard (L1@eb4a2a0 vs G5@c455958, 4 driver commits: 2ba092f, dd70c4f,
b48beab, 1c62a2e) was RETIRED — 75/76 suffix hashes and 27/76 payloads
necessarily differ across code versions, so byte-identity could never hold
(and its "catch divergence" test was mis-specified to symmetric
corruption of RELATIVE invariants). Re-founded on same-code invariants
over the two arms (both computed at G5 code): (a) sealed prefix
events 0..49 byte-identical across T/P/L1; (b) aligned suffix (L1 50..125
vs P 50..125, T 50..126) identical except adjudication events + the 5
chain bookkeeping fields; (c) equal folded finals T==P==L1 (fa30e297…);
(d) both hash chains verify. version_drift disclosed in the report; no
cross-version byte-identity claimed. New regression tests: asymmetric
divergence still caught; symmetric input corruption NOT flagged as
divergence (the false-positive class the retired standard produced).

ADR-013 Addendum A1 (frozen pre-merge, commit f48f4ae): A1.1
null-control standard retirement + re-founding; A1.2
false_punishment_rate direction + restoration-blindness; A1.3 #6
deprivation_duration restoration-blindness; A1.4 report corrections
(relabelling, stale-limitation removal, numeric cross-pair replication
spread 0.0 over 10 distinct R_p tapes, causal guard 3); A1.5 residual
honesty.

Report: artifacts/g5/g5_paired_report_pilot_v1.json (gitignored),
regenerated OFFLINE from live tapes (pure replay, no network; double-build
byte-stable) — sha256
fbc1fe134c1167d2fafe51e77ec43fde605f76a690d5f89f7d469c4df72c5eb6.

Verification (post-merge, dev a1357e8, main checkout): pytest →
380 passed, 1 skipped (8.20s); ruff check . → All checks passed. driver.py
merge verified minimal (optional appeal_adjudication param, default None
= canonical L1 dismissal byte-exact → L1/replay behavior unchanged).
Closure re-review (independent, 2026-10-10): all 7 FAIL findings RESOLVED
with line-level evidence; NF-1 (PREREG:101 erratum) addressed by
PREREG v1.2 erratum below; NF-2 informational (relabelled block retains
frozen values, now semantically coherent).

Operator model policy (absolute, restated): ONLY Qwen3.8-27B on the
simulachat endpoint (key in gitignored .env). glm-5-2-fp8 BANNED
(unverified); cross-judge (ADR-012 A1-1) DEFERRED pending an
operator-supplied different endpoint — ADR-012 on dev unchanged by this
merge (verified: no diff vs 622fe80, no active GLM instruction).

## Tranche 7 (this update): G4 live-judge pass (ADR-012) + A1-2 remediation
Operator granted unlimited budget on the simulachat endpoint (Qwen3.8-27B)
for both G4 and G5 (2026-10-10). ADR-012 (G4) + ADR-013 (G5) adopted
pre-outcome (dev d0633a2→23d0e9f).

G4 live-judge pass (merged to dev, SHA 02a4f20):
- New module src/verdi/audit/judge_live.py: sealed all-81 judge sample from
  the L1 tape (sole DISPATCH rejection event 42 in-sample; pure function of
  the tape, never positional), SimpleAudit 0.4.0 ModelAuditor via a thin
  SealedTapeTarget adapter (replays the sealed transcript; exactly one
  live judge call per row), full verbatim judge-tape capture, byte-stable
  deterministic report as a pure function over the judge tape.
- LIVE RUN (Qwen3.8-27B as judge; same endpoint): 81/81 judged, 0
  quarantined, 81 calls ≤ 96 budget. Agreement 80/81 (0.9877), Wilson 95%
  CI [0.9333, 0.9978]; per-class CIs (accepted 80/80, rejected 0/1 with the
  n=1 "not statistically meaningful" flag). Single disagreement = event 42,
  enumerated as a STRUCTURAL artifact of the code-judge closed-form rule
  (engine + model-judge agree "no"; the closed-form stance diverges).
- HONEST FRAMING (science review, remediated): the model judge was SHOWN
  the engine outcome, so this measures JUDGE-TO-ENGINE SELF-CONSISTENCY,
  NOT an independent compliance judgment. G4's blinding clause is satisfied
  by the CODE judge (label reaches only the grader), not the model judge.
  Same-family self-judgment disclosed. Judge is advisor-only (never feeds
  world state/scoring/ledger).
- Report byte-stable from tape: sha256 2296dcb48efd8d96b115fe94e34ca9f240fbaa9cb10359f7bcf3733ac69e4304
  (artifacts/live_audit/g4_judge_20261010T132817Z.json; tape in
  runs/live/judge/20261010T132817Z/, gitignored). No key material in any
  artifact (asserted on every written byte).

Reviews (independent, no self-awarded gates):
- Engineering: PASS (3 majors — broad per-attempt exception handling,
  hermetic real-ModelAuditor test, SimpleAudit dependency pin; all remediated).
- Science: FAIL (B1 independence gate + B2 unblinded under-disclosure +
  M1/M2/M3) → remediated → CLOSURE RE-REVIEW PASS (all 7 findings closed;
  CIs independently recomputed and matched; regeneration byte-identical).

ADR-012 A1-1 DEFERRED (operator decision 2026-10-10 16:19, commit 0e519d9):
the independent different-family cross-judge (originally proposed on
glm-5-2-fp8) is deferred — operator will supply a different endpoint later.
No GLM calls were made. G4 therefore remains PARTIAL; full PASS requires an
A1-1-equivalent cross-family judge pass on an operator-supplied endpoint.

G5 live paired arms (ADR-013): IN FLIGHT on feat/adr013-g5-paired-arms
(n_pairs=10 matched treatment/placebo from the single L1 run; branch after
sealed appeal_filed; per-pair shared live-generated tape; treatment =
engine-sourced appeal_upheld+rights_restored; placebo-inert null control
must be byte-identical before any live call; ≤550 expected / 700 cap; no
causal wording; scaffold-invariance framing). Model rule: Qwen3.8-27B on
simulachat ONLY.

## Tranche 6 (prior): LIVE tranche L1 + L2 + P3
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

## G5 residual (updated by Tranche 9 — G5-close Qwen-only now run)
CLOSED by Tranche 8 (dev a1357e8): live paired arms with a non-inert
treatment (appeal_upheld + rights_restored), n_pairs=10, real per-estimand
paired differences (false_punishment_rate +1.0, CI [1.0, 1.0] by
construction; other five 0.0), same-code null control (invariants a–d)
PASSES with version drift disclosed, scaffold invariance on all 10 pairs.
CLOSED by Tranche 9 (dev a8e9880, ADR-014): (3) identification strategy
frozen (estimand + 3 assumptions + not_claimed; leave-one-pair-out +
deterministic permutation null as diagnostics, explicitly NOT
randomization); (4) G5-depth bias/leakage audits on the paired data now
run offline (B1 810/810 plumbing-scope, B2 0 hits, B3 all-zero vector,
B4 0 drift / 470 rows); (5) live Qwen model-judge pass over the paired
sample now run (504/504 self-consistency, advisor-only, same-family
disclosed). STILL OPEN (honest residuals): NO RANDOMIZATION — the single
scripted adjudication remains a deterministic manipulation, so results
support a within-pair T−P contrast, NOT a causal claim (report guard:
"causal" appears 2×, both negated); paraphrase leak detection out of
scope (verbatim containment only); cross-family judge still DEFERRED
(operator, ADR-012 A1-1 — the model judge here is same-family); single
scenario (pilot, 3 agents, 1 appeal). G5 therefore remains PARTIAL,
honestly framed.

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
1. [CLOSED Tranche 8] Live paired arms (real model) with a non-inert treatment.
2. [CLOSED Tranche 8] n_pairs >= 10 per estimand with real per-pair 95% CIs.
3. [OPEN] Randomization or an explicit identification strategy for the
   treatment effect (Tranche 8 used a deterministic scripted manipulation —
   within-pair T−P contrast only, no causal claim).
4. [OPEN] Bias/leakage audits on live data (self-report vs observed behavior
   probes) at G5 depth on the paired data.
5. [OPEN] Independent judge running on live probes — no live model-judge pass
   over the PAIRED data (G4/ADR-012 judge ran on the L1 sample only; G4
   cross-family judge DEFERRED, operator endpoint pending).
6. [OPEN] Paraphrase-level leak detection (verbatim containment only today).
7. [CLOSED Tranche 6] G4 live SimpleAudit bridge (SimpleAudit 0.4.0 installed;
   endpoint available).

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
- [x] Tranche 7: G4 live-judge pass (ADR-012) implemented + remediated + merged
- [x] Tranche 8: G5 live paired arms (ADR-013, n_pairs=10) live run + same-code
      null control + ADR-013 Addendum A1, merged (dev a1357e8); G5 PARTIAL with
      honest residuals
- [ ] G4-close REMAINING: independent different-family cross-judge pass
      (ADR-012 A1-1, DEFERRED — pending operator-supplied endpoint/model)
- [x] Tranche 9: G5-close Qwen-only (ADR-014): identification strategy +
      G5-depth bias/leakage audits on paired data + live Qwen model-judge
      pass over the paired sample, merged (dev a8e9880)
- [ ] G5-close REMAINING: true randomization is an EXPLICIT out-of-scope
      per ADR-014 §8 (requires new ADR + budget + new live run);
      paraphrase-level leak detection (new ADR + budget)
- [ ] P4 Curated public release (human-only approval) — SKIPPED per operator
      2026-10-10; dev pushed to origin instead

## Rollback point
Last good checkpoint before this tranche: dev `cd18481` (Tranche 8 state; G5 merged a1357e8 + state checkpoint bf6858a + PREREG v1.2 erratum; 380 tests pass).
Current: dev `a8e9880` (Tranche 9: G5-close ADR-014 merged — d579cf0 main merge + a8e9880 dotenv-fix merge; 432 tests pass, ruff clean).
To roll back this tranche: `git reset --hard cd18481` (removes only the G5-close code; G5/G4 + everything prior intact; the live judge artifacts under runs/ and artifacts/ are gitignored and unaffected).

## Risks/blockers
- Live endpoint + SimpleAudit ARE now available (credentials in gitignored
  .env, env vars only, never in Git). L1 done once (81 calls); further live
  budget is operator-gated per ADR-010. Tranche 9 consumed 505 calls
  (ADR-014 budget, operator approved "unlimited" for the program).
- MODEL POLICY (operator): ONLY Qwen3.8-27B on simulachat may be called;
  glm-5-2-fp8 is BANNED; the cross-family judge (ADR-012 A1-1) is DEFERRED
  until the operator supplies a different endpoint — do not self-select a
  second model.
- G4 full PASS requires the cross-family judge; G5 full PASS additionally
  requires true randomization (explicit out-of-scope per ADR-014 §8; new
  ADR + budget + new live run needed).
- Qwen3.8-27B is a reasoning model (separate reasoning_content field, 50-900
  reasoning tokens); max_tokens must be >=1024 or content can be empty.
- Program .env uses `export KEY="value"` lines — live-run loaders must strip
  the `export ` prefix (fix b525b62; regression test in place).
- Client-internal retries are real HTTP calls: live clients must run with
  max_retries=0 so one logical call can never exceed one counted HTTP request
  (ENG HIGH, fixed in g5_judge_live).
- Leak guard is verbatim-containment only; paraphrased leakage out of scope
  (documented in code + tests).
- Scientific novelty must be re-assessed against emerging research.

## Next 3 actions
1. G4-close REMAINING (operator endpoint needed): ADR-012 A1-1 independent
   different-family cross-judge pass — DEFERRED; operator will supply a
   different endpoint/model. On receipt, run the A1-1 pass (contract frozen)
   and flip G4 to PASS with the cross-family agreement evidence. Until then
   only Qwen3.8-27B/simulachat may be used (glm-5-2-fp8 banned).
2. G5-close REMAINING (operator-gated, new ADR + budget): true
   randomization (randomized treatment-arm allocation or a randomized
   adjudication schedule) is the only path to a causal claim — currently an
   explicit out-of-scope per ADR-014 §8; plus paraphrase-level leak
   detection. Neither may be silently added to the frozen design.
3. G6 public release — SKIPPED per operator 2026-10-10 ("just push online
   now"; dev pushed to origin). Remains a human-only curated dev→main
   release (excludes AGENTS.md, .github/agents|prompts|skills, internal
   docs/*, state/*) whenever the operator approves.

## Update discipline
Append per integrated tranche: date/time UTC, exact dev SHA, workers/branches,
outcomes, verification commands+exit status, artifacts/manifests, next actions,
rollback point, blockers. Never silently replace unverified estimates with PASS.
