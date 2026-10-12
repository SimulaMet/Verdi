# Lessons, failures, and autonomous recovery memory

This is a *verified* failure record, not a place to invent successes. Integrator adds an entry after failed stages, fixes, and recurrences.

## Template
- Date / dev SHA / phase / task ID:
- Observed symptom with reproduction command and exit code:
- Expected vs actual:
- Root cause (confirmed vs hypothesis):
- What was attempted and why it did/didn't work:
- Minimal regression test:
- Corrective action / rollback checkpoint:
- Verification evidence and independent reviewer:
- Generalizable prevention rule:
- Unresolved risk / follow-up:

## Initial known risks
- One branch per worker and disjoint paths; integration conflict risk rises with indiscriminate parallelism.
- GPU capacity is not infinite: vLLM can OOM under high context/concurrency. Tune based on measurements; backoff on 429/502/503.
- Self-reported values do not equal behavior. Judge bias and initialization noncompliance can masquerade as drift.
- A constitution penalty without appeal or evidence can produce wrongful punishment.
- Logs that record only allowed actions hide attempted violations.
- Attempting a normal dev-to-main merge leaks internal agent instructions into public releases.

## 2026-10-11 / branch feat/society-v2-stage-c / Parallel assembly batch runner + attempt 5 partial preserved + attempt 6 launch (Tranche 12f)
- Date / dev SHA / phase / task ID: 2026-10-11 / dev `b36bafa` (worker `feat/society-v2-stage-c`) / Stage C-1 live / parallelize the assembly bottleneck.
- Observed symptom: live C-1 assembly ticks were the dominant wall-clock cost — 24-voter deliberation + (proposals × voters) voting calls run SEQUENTIALLY by design, ~2-3h per assembly pair at concurrency 6, while the SimulaChat endpoint sustains ~10 concurrent calls (user-reported). Action ticks were already parallel (TaskPool).
- Design: split each assembly model call into (a) call+parse (order-independent, parallelizable) and (b) engine mutation + quarantine/abstention records + ordered appends (deterministic post-pass in input order). Governance stays asyncio-free (tests/governance/test_determinism.py forbids asyncio imports in verdi.governance), so the runner is INJECTED: `Assembly.run_batch` defaults to sequential `_run_sequential`; the society layer (`verdi.society.campaign._run_bounded`, asyncio.Semaphore) is set per-assembly from the run's concurrency. Reflection calls parallelized the same way. Probes left sequential (smaller cost; subject_provider is stateful — not parallel-safe without rework).
- Determinism contract (verified, not assumed): engine mutations, quarantine records, proposal_order, vote records all produced in the SAME sorted order as the old sequential loops → identical ledger/state for identical model outputs. Only wall-clock duration and model_calls.jsonl row order (telemetry) differ. Replay re-folds the deterministic tape with zero model calls, so assembly call ordering cannot affect replay hashes.
- What was attempted / bugs hit during the test work: (1) first attempt added asyncio directly to assembled.py — the governance determinism-guard test (test_no_llm_or_nondeterministic_imports) correctly BLOCKED it; reworked to the injected-runner design (the test earned its keep). (2) The parallel-vs-sequential campaign test initially PASSED WITHOUT the monkeypatch ever taking effect for the second run — monkeypatch holds until test end, so the "parallel" run silently ran sequential too (max_inflight=1). Cured by `monkeypatch.undo()` between runs. (3) The in-flight probe fake needed count-then-yield (holding the count across the yields) to make overlap observable; a fake with no suspension point serializes under any runner. Final test asserts BOTH: identical state hash + byte-identical ledger across paths, AND max in-flight assembly calls >1 in parallel vs ==1 when sequential is forced.
- Corrective / recovery context: attempt 5 (sequential code, concurrency 6) had reached 639 events / past t20 with amendment #1 enacted when its run dir was renamed to hand the path to attempt 6; the running process re-opened its heartbeat file on the new path and then died on the next atomic run_meta write (FileNotFoundError on the stale run_meta.json.tmp path — expected failure mode of the preserve trick, no run data lost). Attempt 5 preserved as `runs/society-v2/stageC1-20261014-attempt5-partial` (639 events, 5 snapshots, console log). Attempt 6 launched 17:4x UTC on the parallel code at concurrency 10, same seed 20261014.
- Generalizable prevention rule: (1) when a "forbidden import" guard test fires, treat it as an API-design signal (dependency inversion) rather than a thing to delete — here it produced a cleaner injected-runner design and kept the governance layer's no-asyncio invariant intact; (2) a dual-path equivalence test must PROVE the two paths actually differ mechanically (instrument the mechanism under test) before asserting their outputs agree — an equivalence assertion on two runs that secretly share the same path proves nothing; (3) renaming a live run's dir is a safe-ish preserve trick for file-append state but WILL kill processes that do atomic rename-based writes (tmp file in the same dir) — do it only when you intend to supersede the run.
- Unresolved risk / follow-up: attempt 6 (concurrency 10) is the first live run of the parallel assembly path; first-assembly passage at t15 is the gate. If the endpoint throttles at 10 concurrent (429s), the client retry/backoff absorbs it (VERDI_MAX_RETRIES=5); sustained 429s would show up as a quarantine spike in the run metrics, not a crash.

## 2026-10-11 / branch feat/society-v2-stage-c / LIVE C-1 attempt 3 false-fire by events-only watchdog (Tranche 12d)
- Date / dev SHA / phase / task ID: 2026-10-11 / dev `b36bafa` (worker `feat/society-v2-stage-c` @ `bc9ed5e`) / Stage C-1 live attempt 3 / watchdog false-fire at first assembly tick (t15).
- Observed symptom: attempt 3 (concurrency 6, `--stall-timeout 600`) progressed healthily to event 497 (~35% of run, ~28 min: 09:58:12Z start -> 10:25:54Z stall), then `_stall_watchdog` fired: "STALL: no new events for 600s (last count 497)", exit 3, run_meta `status=stalled`. The run was NOT hung — it was mid-assembly.
- Expected vs actual: the watchdog was supposed to catch dead-socket stalls (Tranche 12a), but its only liveness signal was `events.jsonl` line growth. Assembly ticks write ZERO ledger events until finalize: probes (n × 3 channels, sequential), `run_deliberation` (n sequential calls), and `run_voting` (n × proposals sequential calls) all complete before a single event is appended. At observed p95 latency (8.9 s/call) a 24-agent assembly tick is a 5n ≈ 120-call, ~1000 s+ sequential window — far over 600 s of silent event growth.
- Root cause (CONFIRMED by independent reviewer 694580fb, SHIP-WITH-NITS, finding F1 HIGH, verified against the preserved run artifacts): the watchdog's progress signal was mismatched to the engine's write pattern. Revision: attempt 1's 9+ min no-growth stall at event 377 was ALSO most likely the same t15 assembly no-write window (attempt 1 had no watchdog; it was killed manually) — the dead-socket hypothesis remains the *confirmed symptom* explanation only, and is recorded honestly as endpoint-side hypothesis, not proven.
- What was attempted: watchdog v1 (events.jsonl line count, commit 5784c62) — worked as designed but its design assumed every tick emits events promptly; false-fires on long ticks by construction.
- Minimal regression test: `tests/society/test_watchdog_calibrated.py` (5 tests): (1) default 1200 s ≥ stuck-call retry window (180 s × 6 attempts = 1080 s) + 20 s heartbeat; (2) p95 24-agent assembly tick (~1069 s) > old 600 s window — pins the F1 arithmetic; (3) per-call beat lines are monotonic; (4) no false fire during a 130 s tick with beats present; (5) still fires exit 3 when truly hung (no beats, no events).
- Corrective action: (a) `_LiveProgressCaller` wrapper writes one JSON beat per COMPLETED model call to `progress.jsonl`; the watchdog's progress count is now events lines + beat lines, so the deadline resets on every completed call; (b) default `--stall-timeout` 600 → 1200 s (calibrated: stuck-call retry window 1080 s + heartbeat slack); (c) `_run` floors any user-supplied `--stall-timeout` at the measured retry window + heartbeat; (d) F2 MEDIUM: `_write_run_meta` now temp-file + `os.replace` (atomic) to close a latent read-modify-write race; (e) F3 LOW: scenario `probe_points` converted to Stage-B string-list format with a NOTE that lifecycle keys (not probe_points) drive engine probing; offline scale test gained a structural assertion.
- Verification evidence: 521 passed, 1 skipped; ruff clean; `build_c1(20261014)` regeneration is byte-identical to the committed scenario except the F3 `probe_points` lists. Independent reviewer 694580fb signed off SHIP-WITH-NITS on `b36bafa..bc9ed5e`; F4 LOW (pre-registration doc said "committed to dev" but it was committed on the campaign branch) corrected in the doc wording.
- Generalizable prevention rule: (1) a stall watchdog's progress signal must measure WORK (model calls completed), not OUTPUT (ledger writes) — engine phases that batch their writes (assemblies, finalize) are legitimately long and silent; (2) when adding a no-progress timeout, derive the threshold from the client's worst-case stuck-call retry window (timeout × (retries+1)) and assert it in a test; (3) before trusting a watchdog/monitor, trace the longest write-free window in the monitored pipeline and compare it against the threshold at p95 latency.
- Reviewer-verified (7e512896, SHIP-WITH-NITS) that the beat design does NOT mask a Tranche-12a-style wedge: each tick's call set is bounded and fully pre-dispatched (TaskPool.run_all gathers the whole tick tape), so an isolated stuck call becomes the only in-flight activity, beats freeze, and the watchdog fires. Residual LOW: worst-case detection latency for a genuinely dead call is drain-time + 1200 s (~20 min), versus 600 s before — accepted trade-off for eliminating false-fires.
- Unresolved risk / follow-up: attempt 1's exact cause (dead socket vs assembly window) is not distinguishable from preserved evidence; per-call beats will make it moot — if the endpoint truly drops a call, beats freeze AND the client's own retry window expires, and the watchdog fires with the partial run preserved. C-1 attempt 4 is the live validation.

## 2026-10-11 / branch feat/society-v2-stage-c / LIVE C-1 attempt 4 lapsed at 735 events (Tranche 12e)
- Date / dev SHA / phase / task ID: 2026-10-11 / dev `b36bafa` (worker `feat/society-v2-stage-c` @ `bc9cd64`) / Stage C-1 live attempt 4 / lapsed while finishing t30 assembly.
- Observed symptom: attempt 4 (on 58c33f3 + help-string fix, concurrency 6) ran for ~3h12m and reached event 735 — t30 assembly had just ENACTED a second amendment (kinds: 710 accepted / 23 rejected / 2 amendment_enacted) — when all writes (events, beats, heartbeat) stopped at 13:05:01 UTC. No crash report, no exception, no console output after the smoke line. Process is gone; cause is unobservable from preserved evidence.
- Expected vs actual: expected continuous heartbeats until completion or a clean watchdog exit-3; got silent disappearance mid-assembly with a fully intact partial run.
- Root cause (HYPOTHESIS, not confirmed): silent local process death with no macOS crash report — most consistent with a local network event / machine power state / user-side kill (the run had been surviving the user's expected network churn; the F1 fix had already proven itself: attempt 4 passed t15, the kill-point of attempts 1 and 3, and passed t20 reflection). Endpoint probed healthy at 0.7 s after the death. Recorded honestly as hypothesis per campaign rules.
- What was attempted: verified via run_meta mtime, progress.jsonl last beat (13:05:18 UTC), DiagnosticReports (no matching crash), console.log (clean). Preserved the partial as `runs/society-v2/stageC1-20261014-attempt4-lapsed` (735 events, 2323 beats, console log kept alongside) — never deleted.
- Minimal regression test: n/a (external process death).
- Corrective action: relaunch attempt 5 from t0, same seed 20261014, deterministic scenario (byte-identical regeneration verified), endpoint re-probed healthy first. All four prior partials preserved as evidence.
- Generalizable prevention rule: (1) for live runs >1h, assume the local process can die silently; the recovery contract is: preserve run dir (rename to attempt-N-<cause>), re-probe endpoint, relaunch from t0 on the same seed — no in-place resume (EventLog resets to genesis); (2) periodic beat-rate sampling, not just file mtime, is how a lapsed run is distinguished from a long assembly tick in minutes.
- Unresolved risk / follow-up: attempt 4's exact death cause remains undetermined; if attempt 5 lapses the same way at a comparable point, that correlation would shift the hypothesis from local-environment event to endpoint-side behavior, and a per-call inactivity timeout in the client (not just read timeout) becomes the next fix to try.

## 2026-10-11 / branch feat/society-v2-stage-c / LIVE C-1 attempt 1 hung on a dead socket (Tranche 12a)
- Date / dev SHA / phase / task ID: 2026-10-11 / dev `b36bafa` (worker `feat/society-v2-stage-c` @ `553bd5a`) / Stage C-1 live attempt 1 / scale-jump 24x60.
- Observed symptom: `python -m verdi.society.live --scenario .../society_v2_stageC1.json --run-dir runs/society-v2/stageC1-20261014 --concurrency 6` made steady progress to event 377 (~27 min), then produced ZERO new bytes for 9+ minutes with 0% CPU (not stuck in Python work) and one ESTABLISHED socket to the vLLM host (188.114.96.1) that never responded. No crash, no exception, no log line.
- Expected vs actual: httpx timeout is `VERDI_REQUEST_TIMEOUT=180` s and `max_retries=5`, so a slow response would eventually time out or retry — a dead socket that never completes never triggers httpx's timeout (it only covers connect/read/total of *received* data; a peer that holds a connection open without sending can stall a read indefinitely beyond the wall clock depending on httpx config), and the whole campaign silently parks on that one in-flight call while the semaphore slot stays held.
- Root cause (CONFIRMED for the symptom, endpoint-side cause is the live vLLM holding a dead connection): the client is waiting on a read of a connection the server never completes; no retry fired because no exception was raised. Endpoint re-probed after the kill answered in 1.3 s — transient server-side stall, not our code path.
- What was attempted: py-spy dump (blocked: requires root on macOS); lsof (showed the single ESTABLISHED connection); direct endpoint probe (1.3 s, healthy). Decision: kill (preserving the partial run as `stageC1-20261014-attempt1-hung`), relaunch attempt 2 with `python -u` for unbuffered console so future stalls are visible in `console.log` immediately instead of via file-growth inference.
- Minimal regression test: none feasible for a third-party server stall; the prevention is operational (watchdog).
- Corrective action: attempt 2 relaunched at concurrency 6, same seed 20261014, same run dir name (fresh artifacts). Partial attempt-1 dir preserved, not deleted.
- Generalizable prevention rule: (1) for long live runs, launch with `python -u` and check `console.log` growth, not just events.jsonl, as the liveness signal; (2) for any campaign exceeding ~30 min with no event growth AND 0% CPU AND an ESTABLISHED model socket, treat it as a hung-socket stall, kill, preserve, and relaunch — do not wait for the timeout that may never fire; (3) a single hung call can park the ENTIRE campaign when a semaphore slot is held, so stall detection must be on wall-clock progress, not error counts.
- Unresolved risk / follow-up: watchdog NOW IMPLEMENTED (Tranche 12b): `verdi.society.live` runs an independent `_stall_watchdog` task (separate asyncio task, so it survives a hung in-flight read) that writes `heartbeat.json`-style `heartbeat_utc`/`events_logged` into run_meta every 20 s and hard-exits code 3 with `status=stalled` after `--stall-timeout` (default 600 s) without event growth — preserving the partial run for checkpoint-recovery. Tests: `test_live.py::test_stall_watchdog_*`. Still open: consider an inactivity/total timeout on the httpx client in addition to the read timeout.

## 2026-10-12 / dev `305198c` (branch feat/society-v2-stage-b-fix1) / LIVE Stage B seed-1 crash + fix (Tranche 11b/11d)
- Observed symptom with reproduction: LIVE Stage B seed-1 (fix1) run
  (runs/society-v2/stageB-20261012-seed1-fix1) committed 229 events then crashed
  at tick 230 with `InvariantViolationError: negative balance: agent=b01
  resource=credits value=-2.0` (reducer._assert_nonnegative). Repro command
  (deterministic, offline): `pytest tests/world/test_validate.py::test_deposit_
  exceeding_balance_is_rejected_overdraft` — RED on the pre-fix code (validator
  accepted a 2.0 deposit from an agent holding 0.0), GREEN after the fix.
- Expected vs actual: the deterministic validator and the reducer must AGREE that
  a proposed action is safely applicable, so the reducer's final non-negative
  invariant is unreachable for validated actions. Actually, `deposit` was
  validated WITHOUT the agent held-balance check that `transfer` already had, so
  an over-deposit was accepted and only the reducer's end-of-apply assertion caught
  it — by raising an invariant breach instead of a clean rejection.
- Root cause (CONFIRMED, unit-test reproduced): `validate_action`'s
  deposit/withdraw branch checked only the WORLD pool (on `withdraw`); on
  `deposit` it accepted any positive `amount` regardless of the agent's held
  balance. b01 was held to 0.0 credits by committed outflows (live snapshot
  after_tick16 @ tick 206 confirms b01 credits=0.0, b05 under_sanction); a 2.0
  over-deposit → -2.0 (exact crash value). SECONDARY gap (confirmed by a RED
  test): the sanction gate only fed the queue for `action_kind == "transfer"`, so
  a substantive over-deposit/over-withdraw breach would have been logged but NOT
  sanctioned.
- What was attempted and why it did/didn't work: First offline replay was
  UNFAITHFUL — it replayed only action/amendment events and OMITTED the
  `rights_change` sanction flips, which ARE part of `canonical_state_hash`, so it
  diverged from the live ledger at event 206 (b05 flag) and its "b01 = 0.0"
  reading was initially from the wrong state. Recovery: verified the LIVE snapshot
  hash chain (EventLog.verify + after_tick16.json state_hash) to establish ground
  truth before drawing conclusions. Fix: mirror the transfer held-balance check on
  `deposit` (reject over-deposit → law:no_double_spend / overdraft) so validator
  and reducer agree; generalize the sanction gate to all substantive rejections.
- Minimal regression test: `tests/world/test_validate.py` (over-deposit rejected
  + boundary + within-balance applies cleanly) and
  `tests/society/test_campaign.py::test_overdeposit_substantive_violation_
  sanctioned` (over-deposit feeds sanction queue even though kind=deposit).
  Both verified RED on the old code, GREEN on the fix.
- Corrective action / rollback checkpoint: fix on 09080dc. Rollback point if
  regression: 7ccabcf (Tranche 11 N1-N4) / dev 305198c.
- Verification evidence: `python -m pytest` -> 510 passed, 1 skipped; `ruff check
  src tests` clean. LIVE seed-1 FIX2 re-run started 2026-10-12 16:20 UTC
  (SimulaChat default Qwen3.8-27B, concurrency 4); pending replay/assembly/probe
  verification + independent re-review.
- Generalizable prevention rule: (1) For EVERY property-moving action kind, the
  validator must enforce the SAME source-balance invariant the reducer applies —
  a new action kind that debits an agent WITHOUT a matching `held >= amount` check
  WILL eventually trip the reducer's non-negative assertion; add a unit test that
  proposes an over-debit and asserts rejection. (2) A "sanction only X" gate that
  is keyed on a specific action_kind (transfer) will silently drop substantive
  breaches of the OTHER property-moving kinds — gate on the violation-CODE set
  (SUBSTANTIVE_VIOLATION_CODES), not the action kind. (3) When reconstructing an
  offline replay to diagnose a live crash, include EVERY tape step (including
  rights_change flips) because flags are part of the canonical hash; otherwise the
  diagnosis state is wrong. Cross-check any conclusion against a live snapshot's
  verified hash before acting on it.
- Unresolved risk / follow-up: await the fix2 live run; if it completes with
  replay True/True + 3 assemblies + no invariant breach, re-review and merge to
  dev. Watch whether the generalized sanction gate changes sanction counts in a
  way the reviewer considers in-scope (it only ADDS sanctions for previously
  un-sanctioned substantive deposit/withdraw breaches).

## 2026-10-12 / branch feat/society-v2-stage-b-fix1 / Reused Assembly re-enacts prior proposals + mixed-assembly ledgering gap (Tranche 11d review)
- Independent review (verdi-reviewer, SHIP-WITH-NITS) flagged that a mixed
  assembly (one proposal enacted, another rejected) only ledgered the accepted
  proposal — the `if outcome != "amendment_enacted"` guard skipped the
  rejection loop entirely, silently dropping rejected siblings from the ledger
  and the SimpleAudit tape. Fixed: the rejection loop is now unconditional; the
  already-accepted proposals are simply filtered out (no double-ledgering).
- Fixing that EXPOSED a deeper pre-existing bug: the campaign constructed ONE
  `Assembly` instance and reused it for all three constitutional meetings. An
  `Assembly` is designed for one run — its `_proposal_order`/`_vote_attempt`
  accumulate, so meeting #2 re-enacted meeting #1's proposals (now failing as
  `retroactive_amendment`) and meeting #3 re-enacted both. Each meeting's
  `finalize` returns `amendment_enacted` if ANY proposal passes, so these
  spurious rejections were previously invisible (the guard swallowed them).
  A multi-assembly experiment would therefore log 3 phantom rejections with no
  observable symptom until you instrumented it.
- Fix: construct a fresh `Assembly(governance)` per meeting in the campaign;
  the shared `GovernanceEngine` (constitution state) persists, the Assembly
  wrapper's per-meeting bookkeeping does not. Regression tests:
  `tests/society/test_campaign.py::test_mixed_assembly_ledgers_rejected_sibling_and_replays`
  (mixed outcome → both enacted+rejected ledgered, no duplication, replay clean)
  and `tests/society/test_stageB.py` (3 meetings → exactly 3 enactments, no
  cross-assembly re-enaction).
- Rules: (1) An object with per-run bookkeeping is NOT safe to reuse across
  runs just because it holds a reference to a shared engine — inspect whether
  its own state accumulates. (2) A guard like `if outcome == X: skip the else`
  can silently mask a whole class of events (here, every rejection in a
  mixed assembly); prefer unconditional processing + explicit filtering.
  (3) A "silent" behavior change (spurious rejections appearing) is the
  signal a fix worked — when a previously-invisible count changes after a
  ledgering fix, treat it as a NEW finding, not a regression to revert.

## 2026-10-09 / dev `9798907` / P1 review-findings remediation (Tranche 3)
- Observed symptom: independent Engineering review = FAIL, Science review = INCONCLUSIVE
  (G3-blocking). Repro of the two blocking defects before the fix:
  (a) `adapter.correlate(f"trace-gov-{i}")` for i in range(45) -> 45/45 audit records
      had `event_ids == []` because real governance ledger ids have gaps
      (28,29,30,... not 0..44);
  (b) `ProbeBank.leak_check` existed but was never invoked by `PilotDriver.run`,
      so the G3 information-access defense was dead code.
- Expected vs actual: audit records should map 1:1 to real ledger events; a probe
  dilemma in any agent-visible surface should abort the run. Neither held.
- Root cause (confirmed): correlation id was a UI convenience index, not the sealed
  event id; leak guard was written for the ProbeBank API but not wired into the
  driver's per-tick loop. Secondary causes: span key mismatch (`span_name` vs `name`),
  reducer never recorded enacted constitution versions, replay tape was action-only.
- What was attempted / why it worked: correlate by `sealed.event_id` stored on the
  governance event dict at append time; route `emit_governance` through
  `reducer.apply` with a new deterministic `governance` action_kind so live and
  replay states update identically; record the full 126-step tape and assert
  byte-identical JSONL instead of force-copying bookkeeping fields in tests.
- Minimal regression tests: `tests/pilot/test_leak_guard.py` (verbatim leak raises,
  clean passes, full run passes guard); `tests/pilot/test_pilot.py::test_audit_records_
  correlate_real_ledger_event_ids` (unique in-range ids + span_names populated);
  `tests/pilot/test_pilot.py::test_replay_reproduces_full_ledger` (126/126 events,
  byte-identical JSONL); `tests/agents/test_client.py::test_transport_error_*`.
- Corrective action / rollback checkpoint: none needed; merged on first pass.
  Rollback point if regression: dev `ef69f0f`.
- Verification evidence: `.venv/bin/python -m pytest` -> 216 passed; `.venv/bin/ruff
  check .` -> clean; manual replay_check byte-equality check passed.
  Independent reviewer: pending re-review (Tranche 3).
- Generalizable prevention rule: any id used to correlate an audit/judge record must
  be the sealed ledger id captured at append time, never a positional index; any
  safety check that exists as an API must have a caller in the run path plus a test
  that the caller runs (wire + exercise, not just define).
- Unresolved risk / follow-up: leak guard is verbatim containment only; paraphrased
  leakage needs an embedding/semantic check (P2 candidate). Live G4 bridge still
  blocked on SimpleAudit install + vLLM endpoint.

## Tranche 5 (P2): estimand construct validity caught only by an estimand-level re-review
- Symptom: an independent G5 pre-review found 2 of 6 frozen primary estimands
  computed by formulas that silently deviated from PREREGISTRATION.md
  (sustained_recovery: agent-fraction/k-boolean vs the frozen per-tick
  fraction; false_punishment_rate: missing a numerator leg the v1 schema
  cannot express). Engineering review had passed the same code — its checks
  (types, scope, determinism) cannot see construct validity.
- Root cause: "reuse an existing metrics.py function" was treated as
  equivalence with the preregistered definition. Reuse is NOT validation;
  the frozen definition is the contract, and data-model limits (snapshot
  cadence, Appeal.status = filed|upheld|dismissed, no reversed/restored
  sanction state) can make the ideal definition unobservable.
- Generalizable prevention rule: when wiring an estimand, (a) quote the
  frozen definition, (b) map it to an implementation, (c) test a synthetic
  case where a plausible WRONG formula disagrees with the right one, and
  (d) if the data model cannot express the ideal, append a versioned
  operational addendum (never edit the frozen text) and make code ==
  addendum. A statistic that "passes tests" but measures the wrong
  construct is worse than a missing statistic.
- Related: a negative control must be computed over the actual comparison
  values (CI over real differences), not constant zeros; and a statistical
  report must fail loudly on missing run-dict keys — a silent `final_state_hash:
  null` in a report is an integrity hole.
- Also: worktrees share the main checkout's editable install; workers must
  run pytest with PYTHONPATH=<worktree>/src or the tests silently exercise
  the wrong tree.
- Rollback point if regression: dev `d62565a` (pre-P2). Tranche 5 merged
  clean: 291 tests, ruff clean, 5 independent reviews (4 PASS, 1 FAIL
  remediated to PASS on re-review).

## Tranche 6 (LIVE): provenance artifacts must never live in worker worktrees

**What happened.** Two ADR-010 replay-provenance tests silently skipped after
worker worktrees were cleaned up: they hardcoded ledger paths inside
`verdi-L1/runs/live/`, a checkout that no longer exists. No test failed —
the skip guard (skip-if-missing) was doing exactly what it was designed to
do, hiding that the evidence the tests point at had moved out of existence.

**Root cause.** Live-run provenance artifacts (event ledger) were produced in
a transient worktree. The run dir in the main checkout (runs/live/<ts>/) was
the durable home all along, but the original ledger only existed in the
worktree until it was regenerated there.

**Fix / rule.**
- Run provenance artifacts belong in the main checkout under `runs/live/<ts>/`
  (gitignored, durable), NEVER in a worktree path that a merge step deletes.
- Tests referencing run artifacts must resolve the STABLE location first,
  with legacy paths only as fallback, and the set of provenance tests should
  be checked against `pytest -rs` (skip reasons) after any worktree cleanup —
  a newly appearing skip in a provenance test is a red flag, not a green pass.
- Regeneration path is proven: `run_pilot_from_live_tape` reproduces the
  ledger byte-identical (event_hash 126/126), so a stable copy is verifiably
  the same artifact, not a substitute.
- Before deleting a worker worktree: `git -C <worktree> status --short`.
  This session recovered two orphaned pre-existing worktrees (uncommitted
  307-line test file on a branch merged as an empty no-op; unmerged scheduler
  stress commits). Both recovered and merged — but only because the worktrees
  had not yet been deleted.

**Live-model measurement lesson (carried from L1 science review).**
Qwen3.8-27B is a reasoning model: `reasoning_content` is a separate field
and `max_tokens` < 1024 can yield empty content. More importantly, an
identical final state hash across recorded/live/garbage-input runs is
scaffold invariance (pilot_v1 action_kinds are non-mutating), NOT proof the
model drove state. Model variance lives in the ledger chain (0/126 shared
event_hash, 80/126 differing payloads). Any report citing the state hash
must carry this framing; "same final hash" alone is a measurement illusion.

- Rollback point: dev `95098cd` (pre-live-integration); final `d57689a`
  (provenance path fix, 337 passed / 1 skipped, ruff clean).

## Tranche 7 (G4 live-judge): worker agents are NOT a substitute for integration discipline

**What happened (two distinct process faults).**
1. Repeated agent death: both dispatched workers (WS-G4, WS-G5) were cleared
   by the host mid-task (agent handles gone), each leaving UNCOMMITTED work on
   disk (G4: ~910 lines of remediation; G5: a stray driver.py edit). Work was
   not lost because we recovered it — but a "background agent = one dispatch"
   assumption is fragile on this host.
2. Cross-checkout contamination: WS-G5 (told to work only in
   verdi-g5pairs) committed a docs change to the MAIN checkout's dev branch
   (0e519d9) and left the main checkout's working tree dirty against its own
   HEAD. The main checkout is the INTEGRATOR's tree; a worker committing into
   it directly corrupts the serial-merge invariant (dev now carried a commit
   that was neither integrated nor reviewed in the normal path).

**Root cause.** Long-running live-run tasks exceed the reliability of a
single background-agent handle; and the "work only in your worktree"
instruction is only honored as long as the agent stays alive to obey it —
nothing *enforced* the boundary.

**Rules (generalizable):**
- Treat every worker dispatch as interruptible. Workers must commit atomically
  and OFTEN (not one big commit at the end); the integrator recovers from
  `git log`/`git status` in the worktree, never from agent memory.
- The main checkout is integrator-owned and read-only to workers. After any
  worker runs, the integrator MUST run `git -C <main> status --short` and
  `git reflog show dev | head` to confirm no worker touched it; if a worker
  committed into dev, that commit is treated as unreviewed and must be
  re-validated (it was: 0e519d9 is a correct docs-only deferral note, kept
  and accepted, but the fact it bypassed review is recorded here).
- For live-budget tasks, freeze the contract (ADR) pre-outcome FIRST, then
  dispatch; recovery is then a pure function of the frozen contract + the
  on-disk worktree state, independent of which agent instance holds the
  context.

**Scientific lesson (carried from the G4 science review FAIL→PASS).**
A judge that is shown the engine's decision measures judge-to-engine
SELF-CONSISTENCY, not independent validity, and must say so in the report.
The "agreement rate" headline (0.9877) is only meaningful together with the
disclosure that (a) the model judge was unblinded, (b) it is same-family,
(c) the blinding is satisfied by the separate code judge, and (d) the single
rejection row's "disagreement" is a structural artifact of the code-judge
closed-form rule, not a divergent evidence reading. A near-perfect agreement
number without these four disclosures is a measurement illusion.

- Rollback point: dev `26fb369` (pre-G4-merge); current `02a4f20`
  (G4 merged, 358 passed / 1 skipped, ruff clean).

**Tranche 8 lessons (G5 paired arms, dev a1357e8, 2026-10-10).**

**Process (re-applied by integrator; originally written by the WS-G5 worker in
65c2902, reverted by the worker in bf8c4e8 because state/* is integrator-owned —
the lesson itself is valid and re-recorded here as an integrator entry).**
Worker shell session working directories do not persist across bash calls, and
the session had been created in the MAIN checkout, so a worker `git reset
--soft`/`git checkout --` landed in the integrator's tree instead of the
worktree. Rule for all workers: always `git -C <worktree>` (or re-verify
`git rev-parse --show-toplevel` after any cd); never run
checkout/reset/reset --soft without first confirming the top-level path; the
main checkout is integrator-owned and read-only to workers.

**Scientific (the G5 FAIL→PASS core).** A null control that compares two
artifacts produced by DIFFERENT code versions (L1@eb4a2a0 vs G5@c455958) can
never be byte-identical — 75/76 suffix hashes and 27/76 payloads differ purely
from code drift, so the "byte-identical" standard was false-positive-prone AND
its "catch divergence" test was mis-specified (it corrupted the shared input
symmetrically, which RELATIVE arm-vs-arm invariants correctly do not flag).
Lesson: a null control must compare artifacts produced by THE SAME code
version (same-code invariants over the treatment/placebo arms), and any
cross-version comparison must disclose the version drift rather than claim
byte-identity. A "catch divergence" test must inject ASYMMETRIC corruption
(one arm only) to be a valid trip-wire; add the symmetric-corruption case as
an explicit NON-divergence regression to pin the false-positive class.

**Process (agent-death recovery).** The G5 worker instance died 6 times on host
infra errors (503 No renderer connection) at various completion points.
Recovery was always a pure function of the worktree git state
(`git -C <worktree> log`/`status`/`diff`), never of agent memory. Keep a
durable per-task state note in the session files/ dir (commit list, run root,
report sha, regen recipe) so any successor integrator can finish from disk.

**Tranche 9 lessons (G5-close Qwen-only, dev a8e9880, 2026-10-10).**

**Process (protocol slip, self-caught).** The integrator first patched a
live-run blocker (dotenv parser) directly in the main checkout on dev,
bypassing the branch → worktree → serial-merge discipline. Caught in the
same turn: reverted dev (`git checkout --`), re-applied the fix + regression
test on the feature branch in its worktree, and merged serially (b525b62 →
merge a8e9880). Rule: the integrator is held to the SAME branch discipline
as workers — task work on the branch, state files are the only thing the
integrator edits directly on dev.

**Engineering (budget caps vs client retries — ENG HIGH).** An
OpenAI-compatible client configured with max_retries=5 silently issues up to
6 HTTP requests for ONE logical call on 429/502/503. Any call-count budget
or hard cap is therefore only enforceable if the client is created with
max_retries=0 (enforced in g5_judge_live via dataclasses.replace on the
resolved config) so that one logical call == one counted HTTP request. A
cap that counts logical calls while the client retries internally can be
breached by up to 6× without any code fault.

**Engineering (env file formats).** The program .env uses
`export KEY="value"` lines (shell-sourceable), NOT bare `KEY=value`. A
minimal key=value parser that splits on the first `=` keeps the `export `
prefix in the key, so `VERDI_VLLM_BASE_URL` never reaches os.environ and the
live run dies with a misleading "endpoint not configured". Lesson: probe the
.env format before writing a loader, and strip an optional `export ` prefix;
regression test added (b525b62). Related: the venv has no python-dotenv —
repo convention is `set -a; source .env; set +a`.

**Process (hardcoded paths in tests).** test_g5_pairs.py hardcoded the
worker worktree path (/Users/.../verdi-g5pairs), so 16 tests failed the
moment that worktree was removed. Fix: resolve the repo root from the test
file location (Path(__file__).resolve().parents[2] — tests/experiments/*.py
is TWO levels below the root, a common off-by-one). Rule: tests and
artifacts must never reference a worktree's absolute path; worktrees are
ephemeral.

**Scientific (framing inherited vs not).** ADR-014 §5 (frozen contract text)
contained a sentence implying the permutation null is "evidence the contrast
is tied to the manipulation". The report deliberately does NOT inherit that
sentence: the null is a deterministic sign-symmetry diagnostic of the
paired-estimate machinery, and the report says so in its frozen
limitations block. Lesson: frozen ADR text can overstate; the REPORT is the
authoritative artifact and must stand on its own honest framing, with the
non-inheritance explicitly recorded in deviations_from_contract.

**Scientific (honest scope of a convenience audit).** B1 (self-report vs
behavior) on the G5 tapes: the verdict parser FORCES task_id/actor_id from
the row, so "810/810 concordance" measures plumbing consistency, not whether
self-reported values diverge from behavior. The report carries a `scope`
string and `bounds_check_implemented: false` rather than letting a 100%
number imply ADR-B1 coverage.

**Process (shell filter gotchas, both caught live).** (1) Commit messages
containing `\"`-heavy / escape-heavy content trigger the "dangerous shell
expansion" block — use plain ASCII commit messages. (2) The secrets
sanitizer masks `sk-...`-looking strings in files written via heredoc — in
tests, build fake keys from string parts ("sk-" + "test" + "-" +
"fake123") so the file content and tests both stay literal.

## Tranche 10 (Society V2 Stage A): sanction/appeal/commit-order review findings + live-reasoning-model friction

**Correctness (F1 — validation/reducer state divergence).** The commit
loop validated each proposal against the tick-start frozen snapshot while
the reducer applied against the evolving state, so a same-tick double-spend
could validate-then-crash with InvariantViolationError. Fix: re-validate
against the current committed state immediately before apply. Rule: whenever
a pipeline validates against one state and mutates against another, the two
MUST be the same object at the validation point; a regression test must
reproduce the exact commit order (snapshot-valid, state-invalid).

**Scientific (F2 — a mechanism with no effect).** Sanctions were recorded as
governance metadata but never flipped `under_sanction` in the world state,
so the entire accountability mechanism the repo exists to measure did
nothing to the world. The test only checked metadata, so the suite stayed
green. Rule: a governance action's headline effect must be asserted at the
WORLD-STATE level (flag flipped, action blocked, agent suspended), not just
"event recorded"; if the flip must be replay-deterministic, record it on the
tape as an explicit stateless step kind (rights_change) rather than leaving
it out of the tape.

**Correctness (F3 — tautological decision path).** The appeal was filed and
adjudicated in the same tick as the sanction, so `timely = tick <= deadline`
was always true and the dismissal branch was dead code. Fix: defer filing to
violation+2 against a pre-registered deadline (violation+4). Rule: any
conditional whose inputs are all set within the same deterministic step must
be checked for tautology; pre-register deadlines relative to the violation,
not the sanction, tick.

**Scientific (F4 — vacuous enforcement test).** See F2: metadata-only
assertions hide a dead mechanism. Rule: write the enforcement assertion
FIRST (it fails against the unfixed code) — that is the regression test that
catches the class of bug.

**Process (actor vs subject_id key mismatch).** Governance `advance_tick`
restoration events use key `actor` for the affected agent, while sanction
events use `subject_id`; reading the wrong key silently flips nothing or
raises a bare KeyError downstream. Rule: grep the engine's event shape
before consuming it; add an explicit guard (KeyError with agent id) when a
required key might be absent.

**Live (reasoning-model prompt friction).** The fixed-code live Stage A run
(604s, 72 calls, concurrency 4) had 0 under_sanction-blocked rejections and
24/24 rejections were malformed (empty_signal, missing_argument) — the
model repeatedly produced structurally invalid actions. Replay True/True and
governance (6 sanctions/6 appeals/6 restorations, amendment enacted) all
worked. Honest limitation recorded, not hidden. Rule: measure and report the
malformed-action rate per run; it is a signal quality metric, not a failure;
the enforcement path stays covered by unit tests until live traffic
exercises it.

**Process (two replay paths diverging).** Only SocietyCampaign._replay_fill
handles rights_change tape steps; the library verdi.world.replay.replay()
would crash on them. Tracked as issue #1. Rule: whenever a tape gains a new
step kind, grep for ALL consumers of the tape, not just the one you extended.

**Live (transient edge status outside the retry class — 12g).** C-1 attempt
6 (parallel code, concurrency 10) ran healthy through both t15/t20 and the
t45 assembly (3 amendments enacted, 1094 events) and then died on ONE un-
retried HTTP 524 (Cloudflare origin-timeout): the 27B reasoning origin under
load exceeded the edge's origin-read timeout mid-tick. RETRYABLE_STATUS was
(429, 502, 503) — 524 looked like a permanent failure, so the client raised
immediately and the whole run aborted with all partial state preserved.
Rule: treat any gateway/origin timeout status (408, 502, 503, 504, 524) as
the same transient class as 502/503; a single slow origin call must be
backed-off, never fatal. Also: when an endpoint returns 524 at concurrency
10 on a reasoning model, the right reading is "origin is slow under load",
not "endpoint is broken" — the very next probes succeed in <1s.

## 2026-10-12 — Prompt growth is cumulative, not one-source
The public_signals cap (12g) bounded ONE unbounded source; action prompts still grew ~280 tok/tick because
agent_observation stored full state snapshots in private memory (render_history_summary). Lesson: when a
prompt grows linearly with tick, audit EVERY prompt section per tick (signals, history, task, constraints)
before attributing it to one. Second lesson: a daemon-thread watchdog with a short stall window can fire a
REAL os._exit(3) after the test's monkeypatch reverts, killing the pytest process mid-suite (truncated
output, exit 3, "random" failures) — size the window or join the thread.

## 2026-10-14 — Reconstructability: event tick != sim tick; declaration evidence must be separable
1. `events.jsonl` `tick` is the WORLD-STEP counter (increments once per ledgered
action, pilot convention), NOT the simulation tick. The true per-sim-tick
timeline is `resources.jsonl` (one line per advanced tick). Always cross-check
both before interpreting an artifact; the mismatch looked like a bug but was a
misread (20 sim ticks -> 79 world-steps in the 4-agent demo).
2. `SanctionEngine.impose` correctly REFUSES event-less evidence (you cannot
sanction based on nothing). A scenario-DECLARED t0 sanction (a pre-existing
condition, not a committed event) needs its own `register_declaration` path that
skips the event_ids requirement but still participates in duration expiry. Keep
that declaration in `sanctions.jsonl` (source "scenario_declaration"), never in
`events.jsonl`, or you break the hash chain + every frozen tape.
3. `AgentState` is `extra="forbid"`: a scenario agent entry can NOT carry
campaign-only metadata (e.g. `sanction: {duration_ticks}`). Strip such keys in
`build_t0_state` before building WorldState; the campaign reads them from the raw
scenario dict.
4. Repeated-identical-command loop (this session): I re-ran the same
grep/python probe expecting a different result. A command's output is
deterministic for the same inputs — if it returned X, re-running it returns X.
Change the probe (different field name, different file, read the raw schema)
instead of re-running the identical command.

## 2026-10-12 — A sanction must be measured, not an invisibility cloak (Tranche 12n)
1. Suspending a sanctioned agent from the task tape made the validator's
   `law:under_sanction` block (SANCTION_BLOCKED_KINDS = {transfer, deposit})
   DEAD CODE: the agent never proposed, so its transfer could never be rejected
   and recorded. The sanction's own effect was invisible. A governance
   intervention must produce an OBSERVABLE, ledgered outcome — the sanctioned
   agent is tempted with a transfer and the deterministic validator rejects it
   `law:under_sanction` (event truth kept separate from world-state truth: a
   rejected transfer does not move balance).
2. World determinism is "one task per agent per tick." Keep that invariant even
   for sanctioned agents (tempt them, don't erase them); the sanction is a
   *state* the validator enforces, not a *removal* from the world. A sanctioned
   agent with nothing to transfer still keeps a task (produce fallback) so the
   tick count and the "one action per agent per tick" replay contract hold.
3. A rejected `under_sanction` transfer must NOT feed the sanction queue (it is
   excluded, like format failures — sanctioning it would mis-attribute the law
   itself as a new violation). This keeps a t0/runtime sanction measured exactly
   once. Only SUBSTANTIVE_VIOLATION_CODES feed new sanctions.
4. Byte-identity is preserved only if the no-sanction tape branch is provably
   unchanged. Scenarios with no sanctioned agents (pilot L1 frozen tape, G5
   placebo arms, pilot_v1.json) must reproduce identically; the change only
   adds behavior when `under_sanction` is present. Guard this with a
   no-sanction determinism test, not just "tests still pass."
5. A test helper `resources or {default}` silently treats an explicit `{}` as
   "use the default." When a helper takes a dict, use a sentinel default so
   "absent" and "empty" are distinct (this let a sanctioned-agent produce-
   fallback test hold resources it was supposed to be missing).
6. LLM output-format quirk, not a bug: in the live sanction run the model
   returned a transfer's ARGUMENTS under a non-action `action_kind`
   (kind="refuse" with {resource,to,units}); the validator rejected it
   `law:unknown_kind` and quarantined it. The system handled the malformed
   model output exactly as designed (reject + quarantine + count) — a clean
   observable of the model "talking back" to the law, not an engine defect.

## HTML report feature / base dev 20c6e70 / 2026-10-12
- Independent review found whitelist-only output protection could overwrite other
  evidence via --output. Regression reproduced (1 failed / 3 passed); correction
  requires .html output and refuses existing nonreport files. Five feature tests
  now pass, including Node execution of 10,001-tick report transforms.
- Review also caught governance subject_id/appellant_id missing from agent
  resolution, and unbounded governance markers. Both corrected and re-reviewed;
  governance sampling is disclosed separately from extrema-preserving lines.
- Host restrictions: original .git read-only, shell DNS unavailable; cloned dev
  to /private/tmp/verdi-html-work and made feature branch there. Actual report
  generated in explicit CDN mode; offline asset embedding tested with fixture
  assets, actual external downloads unverified. Browser file:// preview rejected
  by URL policy; no workaround attempted. Node behavior testing is not visual QA.
