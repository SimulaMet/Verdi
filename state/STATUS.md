# Execution state (integrator-owned)

Update UTC: 2026-10-11 17:45 (Tranche 12f — PARALLEL ASSEMBLY batch runner on feat/society-v2-stage-c: assembly deliberation/vote + reflection model calls now run through an injected bounded-concurrency runner (asyncio stays out of verdi.governance per its determinism guard test); determinism contract = engine mutations in the same sorted input order, so identical model outputs → identical ledger/state, only wall-clock + model_calls.jsonl row order differ. Attempt 5 (sequential, concurrency 6) preserved at 639 events / past t20 / amendment #1 enacted as stageC1-20261014-attempt5-partial. Attempt 6 LIVE on the parallel code, concurrency 10, same seed 20261014 (PID 45753, smoke OK). Full suite + ruff clean. `main` untouched.)

Prior: Update UTC: 2026-10-12 18:05 (Tranche 11 — Society V2 Stage B COMPLETE and merged to dev @ f116387: review BLOCK N1-N4 remediated (7ccabcf); LIVE crash root-caused + fixed (09080dc, deposit invariant + generalized sanction gate); two review fix sets (bae0e8f mixed-assembly ledgering + fresh-Assembly-per-meeting; 00fee2c per-meeting quarantine auditability + cross-assembly regression test). Three LIVE runs all complete + replay-identical: seed-1 fix2 (concurrency 4), seed-1 repeat (concurrency 6), TRUE seed-2 20261013 (concurrency 6). Both live runs surfaced a REAL mixed assembly (b05 stale-base proposal rejected threshold_not_met 5:7) correctly ledgered — live proof of the Finding-1 fix. 512 passed, 1 skipped, ruff clean on dev. `main` untouched.)

## Tranche 11 (this update): Society V2 Stage B BLOCK remediation + deposit-invariant crash fix
Campaign: docs/LIVE_SOCIETY_CAMPAIGN.md (Stage B: 12 agents b01-b12, 30 ticks, 3 assemblies, honest+adversarial).
Base: dev @ 305198c. Worker branch: feat/society-v2-stage-b-fix1 (commits 7ccabcf, 09080dc). `main` untouched.

### 11a. Independent Stage B review: BLOCK (4 findings N1-N4) — remediated on 7ccabcf
- N1 (HIGH): per-call accounting was missing for assembly deliberation/vote +
  probe model calls (model_calls.jsonl under-recorded). Added _RecordingCaller
  wrapper in campaign.py that times every wrapped call and appends a _ModelCall
  row (assembly subject from the `agent "X"` prompt anchor; probe subject from
  the known agent×channel order). Vote prompt retains the anchor.
- N2 (MEDIUM): the sanctioned scope recorded as ["speaking"] did not match the
  actual world effect (tape suspension + transfer/deposit blocking). Documented
  in _impose_sanction docstring + protocol §1.4 (scope string vs world effect).
- N3 (HIGH): a deterministically REJECTED constitutional proposal was not
  ledgered, so tape replay diverged from the live ledger. Assembly.finalize now
  returns engine enact_events; campaign _assemble ledgeres every rejected
  proposal (governance tape step + amendment_rejected ledger event with
  invalid_reasons); reducer applies governance bookkeeping only on acceptance.
- N4 (LOW): seed-2 scenario_id collided with seed-1 (world_id uniqueness).
  Renamed to society_v2_stageB_seed2.
- Also fixed a pre-existing crash in prompting.py: the prompt template had an
  {arg_schema} placeholder that format() never supplied (KeyError on every
  prompt build). Now passes arg_schema=_ACTION_ARG_SCHEMA.get(action_type).

### 11b. LIVE Stage B seed-1 (fix1) run — CRASH (failure evidence preserved)
- Run dir: runs/society-v2/stageB-20261012-seed1-fix1 (run_meta status=failed).
- Real Qwen3.8-27B interactions, ~5.5s/call at concurrency 4, ~20 min.
- Committed 229 events before crashing at tick 230 (225 action_accepted,
  2 amendment_enacted @ ticks 109/206, 2 action_rejected @ 191/222).
- Ledger hash chain verifies OK (EventLog.verify) and matches the live
  snapshot after_tick16.json (tick 206, hash 616905571678e0ac) — the LIVE
  committed state is ground truth.
- Crash: InvariantViolationError: negative balance: agent=b01 resource=credits
  value=-2.0, raised by reducer._assert_nonnegative at tick 230.

### 11c. Root cause (reproduced with a failing unit test before fixing)
A `deposit` was being validated WITHOUT the agent's held-balance check that
`transfer` already had. The deposit/withdraw branch only checked the WORLD pool
(on `withdraw`); on `deposit` it accepted any positive amount regardless of what
the agent held. An over-deposit (amount > held) passed validate_action, then the
reducer applied agent -= amount and its final non-negative assertion tripped.
Crash math is exact: b01 was held down to 0.0 by committed outflows; a 2.0-credit
over-deposit → -2.0 (the crash value). My first offline replay was UNFAITHFUL
(it omitted rights_change sanction flips that are part of the canonical hash),
so its "b01 = 0.0" reading was from the wrong state — the live snapshot confirms
b01 credits = 0.0 at tick 206 with b05 under_sanction.

Secondary gap found while fixing: the sanction gate fed the queue only for
`action_kind == "transfer"`, so a substantive over-deposit/over-withdraw breach
would have been logged but NOT sanctioned.

### 11d. Fix — 09080dc (validator/reducer agree; substantive gate is kind-agnostic)
- validate.py: deposit now requires held >= amount (rejects over-deposit with
  law:no_double_spend, code overdraft), mirroring transfer. Reducer/validator
  now agree; InvariantViolationError can no longer be reached via over-deposit.
- campaign.py: sanction gate generalized from transfer-only to all substantive
  rejections (overdraft / insufficient_world / unknown_recipient). Format codes
  (missing_argument, empty_signal, non_positive_amount, unknown_kind), already-
  sanctioned actors (under_sanction) and unknown actors remain excluded — the
  SUBSTANTIVE_VIOLATION_CODES set is the single source of truth.
- tests/world/test_validate.py (new): deposit invariant incl. boundary; verified
  RED on the old code (accepted the over-deposit), GREEN on the fix.
- tests/society/test_campaign.py: over-deposit sanctioning regression; verified
  RED on the old transfer-only gate (0 sanctions), GREEN on the fix.
- protocol §1.4: documents the deposit invariant + crash provenance.

### Verification (worker branch @ 09080dc, not yet merged)
- python -m pytest: 510 passed, 1 skipped.
- ruff check src tests: clean.
- 2 new regression tests confirmed RED→GREEN (bug reproduction, not just green).
- 2026-10-12 16:20 UTC LIVE seed-1 FIX2 re-run STARTED (runs/society-v2/
  stageB-20261012-seed1-fix2, SimulaChat default Qwen3.8-27B, concurrency 4).
  Smoke OK; events committing. Awaiting completion → replay/assembly/probe
  verification → independent re-review.

### 11e — Independent review remediation (verdi-reviewer, verdict SHIP-WITH-NITS)
- Reviewer (agent 55eef6bd) independently reproduced the crash root cause,
  verified the deposit fix closes the invariant for all 4 property-moving
  kinds, and confirmed the sanction boundary is exactly right (format/under-
  sanction/unknown-actor rejections are not sanctions; substantive
  overdraft/insufficient_world/unknown_recipient are). Two findings:
- **Finding 1 (MEDIUM, FIXED):** mixed assembly (1 enacted + 1 rejected)
  silently dropped the rejected sibling from the ledger + SimpleAudit tape
  (guard `if outcome != "amendment_enacted"` skipped the rejection loop).
  Fixed in `campaign.py::_assemble`: the rejection loop is now unconditional
  and the already-accepted proposals are filtered out (no double-ledgering).
  Regression: `tests/society/test_campaign.py::
  test_mixed_assembly_ledgers_rejected_sibling_and_replays` (RED on old code,
  GREEN on fix; 2 governance steps, 1 enactment + 1 rejection event, replay
  clean).
- **Finding 2 (LOW, FIXED by the deeper fix below):** no mixed-assembly test.
  (The test above now covers it.)
- **Deeper pre-existing bug EXPOSED by the Finding-1 fix (MEDIUM, FIXED):** the
  campaign reused ONE `Assembly` instance across all 3 constitutional
  meetings. An `Assembly` is designed for one run; its `_proposal_order` /
  `_vote_attempt` accumulate, so meeting #2 re-enacted meeting #1's proposals
  (now failing as `retroactive_amendment`) and meeting #3 re-enacted both —
  3 spurious rejections with NO observable symptom until instrumented
  (each `finalize` returns `amendment_enacted` because a sibling still passes).
  Fix: construct a **fresh `Assembly(governance)` per meeting** in
  `campaign.py`; the shared `GovernanceEngine` (constitution state) persists,
  the per-meeting bookkeeping does not. Regression: `tests/society/
  test_stageB.py::test_stageB_offline_run_completes_and_replays` asserts 3
  meetings → exactly 3 enactments (no cross-assembly re-enaction).
- Protocol updated (`docs/SOCIETY_V2_PROTOCOL.md` §1.2): "one Assembly per
  meeting" invariant + "mixed assemblies must ledger every proposal" rule.
- Verification: python -m pytest = 512 passed, 1 skipped; ruff clean. Both new
  tests RED→GREEN. Committed `bae0e8f` on feat/society-v2-stage-b-fix1.

### 11f — LIVE Stage B seed-1 (fix2) COMPLETED — crash fixed, run clean
- run: `runs/society-v2/stageB-20261012-seed1-fix2`, status=**complete**,
  SimulaChat default Qwen3.8-27B, seed 20261012, concurrency 4, retries 5.
- final_state_hash = ca00c60a9a2383d083b38dab11c09a39d7d6939230e96b946f8c4c34759d090e
- **361 events / 970 model calls / 324 probes / 2384 s wall** (≈40 min).
  replay_matches=True, replay_log_identical=True. Health: mean 3.36 s/call,
  p95 8.9 s, **zero retries, zero quarantine**.
- **Crash point (tick 230) passed cleanly.** The b01 2.0-credit over-deposit
  at tick 230 is now *ledgered as a rejection* (action_rejected,
  law:no_double_spend / overdraft, "holds 0.0 'credits', cannot deposit 2.0")
  instead of breaching `_assert_nonnegative`.
- Actions: 355 accepted / 3 rejected across 12 agents × 30 ticks
  (29–30 accepted each — no agent starved; b01/b05 each had 1 rejection).
  Rejections: tick 191 (transfer 0 units, overdraft code path), tick 222
  (b01 transfer 1 credit holding 0.0), tick 230 (b01 over-deposit, the
  original crash).
- Governance: 3 real model-voted assemblies (ticks 109/206/301), each
  **amendment_enacted** → constitution society-v2-base → v7 → v13 → v20
  (quorum + threshold met by the deterministic engine; 0 quarantined
  proposals). Enforcement cycle observed: 2 sanctions → 4 appeals → 2
  restorations. Probes: 324 (108 each declared/private/public) with
  same-family judge caveat (judge_version verdi.social/v1.0).
- Note: this run executed on `09080dc` (deposit fix) but BEFORE the
  fresh-Assembly fix `bae0e8f`; its 3 assemblies each enacted exactly one
  proposal, so no cross-assembly re-enaction artifact is present in this
  evidence (verified: 3 amendment_enacted events, none spurious). The
  Assembly fix is validated offline by the test suite and is a second real
  validation via the seed-2-fix1 run (launched below, concurrency 6).
- Concurrency decision: at 4, mean 3.36 s / p95 8.9 s / zero retries →
  endpoint has headroom; seed 2 launched at concurrency 6 per campaign plan.

### 11h — Second independent review of `bae0e8f` + nit remediation
- Fresh verdi-reviewer (agent a9bf5251) re-reviewed the remediation commits.
  Verdict **SHIP-WITH-NITS**: both fixes correct and verified (reproduced
  pre-fix behavior in a throwaway worktree). Findings and dispositions:
  - NIT-1 (MEDIUM-low, ADDRESSED): test_stageB guards bug 2 only
    post-bug-1-fix, not on the original dual-bug baseline. Added
    `test_multi_meeting_no_cross_assembly_reenaction`: 2 meetings, one
    proposer per meeting, all-for votes → asserts exactly 2 enactments,
    ZERO amendment_rejected events (shared Assembly would produce a
    retroactive_amendment rejection at meeting 2, visible on the clean
    baseline), and byte-identical replay.
  - NIT-2 (LOW, ADDRESSED): timeline.md "## Assembly" section now labelled
    "final meeting" (last-meeting-only struct kept as-is, documented).
  - NIT-3 (LOW, ADDRESSED): per-meeting Assembly quarantine is now lifted
    into `SocietyResult.assembly_quarantined` (tick-keyed), counted in
    metrics.json `assembly_quarantined`, and rendered in timeline.md when
    non-empty — assembly quarantine is auditable at run level again.
  - NIT-4 (LOW, documented, out of scope): an assembly enacting 2+
    amendments in one meeting writes one enacted ledger step (keyed to the
    final enacted version). Shipped scenarios enact ≤1 per meeting;
    recorded as a known limitation.
  - NIT-5 (COSMETIC, CONFIRMED — my off-by-one): the `bae0e8f` commit
    message claims "512 passed, 1 skipped" but the suite at that tree
    actually reports 511 passed, 1 skipped (reviewer re-ran and confirmed;
    no test added/removed between `bae0e8f` and `972dd15`). I recorded the
    total (512 collected) instead of the passed count. No missing tests;
    the NIT-1 test added in this nit-remediation commit is what brings the
    suite to 512 passed, 1 skipped as of now.
- Verification: python -m pytest tests = 512 passed, 1 skipped (30.9 s);
  ruff check src tests = clean.

### 11g — LIVE Stage B second run (mislabelled "seed2-fix1", actually a
SECOND SEED-1 RUN) — COMPLETED
- run: `runs/society-v2/stageB-20261012-seed2-fix1`, concurrency 6, code at
  `bae0e8f` (includes fresh-Assembly fix). **Labeling correction:** this
  run used `society_v2_stageB.json` + `--seed 20261012` — i.e. the SAME
  scenario and seed as seed1-fix2, so it is a *repeat of seed 1*, not a
  distinct seed. (The true second-seed scenario `society_v2_stageB_seed2.json`
  carries `run_seed: 20261013` and a different agent roster; launching it
  under the wrong name was a launch error. The repeat is preserved as a
  seed-1 reproducibility data point, not claimed as seed 2.)
- Code at launch included the fresh-Assembly fix (verified
  `fresh_assembly = Assembly(governance)` at campaign.py:359, commit bae0e8f).
- status=**complete**; SimulaChat default Qwen3.8-27B; final_hash
  bd889c51f6fe0f16 (≠ seed1-fix2's ca00c60a… — expected: the Qwen model is
  sampling, so a repeat run is NOT byte-identical; determinism lives in the
  deterministic ledger/replay, which is identical-structure:
  replay_matches=True, replay_log_identical=True).
- 362 events / 1006 model calls / 324 probes / 2106 s wall (faster than
  seed1-fix2's 2384 s at concurrency 4 — concurrency 6 gave ~18% speedup,
  mean 3.57 s/call, p95 10.7 s, zero retries, zero quarantine).
- Actions: 355 accepted / 3 rejected (same shape as seed1-fix2: b01's
  over-deposit at tick 231 ledgered as a rejection, no invariant breach).
  Per-agent accepted 27–30 — no starvation.
- Governance: 3 assemblies. Assemblies 1 and 3 enacted one amendment each
  (base→v10→v17→v23). **Assembly 2 (tick 206) was a REAL mixed assembly**:
  it enacted b05's new proposal (→v17) AND the deterministic engine
  rejected a second proposal (asm-b05-16, 5 for / 7 against,
  threshold_not_met) — now correctly ledgered as `amendment_rejected`
  (ev206). This is the first live evidence of the Finding-1
  mixed-assembly ledgering fix working in production: pre-fix that
  rejection would have been silently dropped from the ledger + tape.
  Note the rejected proposal's `old_version` is `base-constitution-v10`
  (a stale base from assembly 1) — this is a legitimate live-world
  phenomenon (a voter proposing against a superseded constitution base),
  NOT the cross-assembly re-enaction bug: the fresh-Assembly fix is active
  in this run (confirmed `fresh_assembly = Assembly(governance)` present
  at campaign.py:359) and no `retroactive_amendment` rejection appears.
- Enforcement cycle: 2 sanctions → 4 appeals → 2 restorations.

### 11i — LIVE Stage B TRUE seed-2 (seed 20261013) — COMPLETED
- run: `runs/society-v2/stageB-20261012-seed2`, scenario
  `society_v2_stageB_seed2.json` (seed 20261013, distinct agent roster),
  concurrency 6, code at `00fee2c`.
- status=**complete**; final_hash 874436f0ff14934b (distinct from the other
  two runs — expected under model sampling); replay_matches=True,
  replay_log_identical=True. 362 events / 982 model calls / 324 probes /
  2177 s wall; health healthy, mean 4.65 s, p95 11.5 s, zero quarantine.
- Actions: 355 accepted / 3 rejected (b12 world-pool over-withdraw at t4;
  b05 food over-deposit at t231/t243 — all ledgered as rejections, no
  invariant breach). Per-agent accepted 27–30, no starvation.
- Governance: 3 assemblies; constitution base→v10→v15→v21. **Assembly 2
  (tick 205/206) again a REAL mixed assembly**: enacted a proposal (→v15)
  AND rejected b05's stale-base proposal `asm-b05-16` (threshold_not_met).
  Cross-seed reproducibility: seed-1's repeat ALSO rejected `asm-b05-16`
  5:7 — the same agent+proposal_id signature failing the amendment
  threshold in two independent live worlds.
- Enforcement cycle: 2 sanctions → 4 appeals → 2 restorations.
- **NIT-3 fix live in production:** metrics.json now carries
  `assembly_quarantined: {tick-8: 0, tick-16: 0, tick-24: 0}` (assembly
  quarantine is auditable at run level; empty here = no quarantined
  proposals in either live seed).

### 11j — Tranche 11 COMPLETE — merged to dev (serial fast-forward)
- Worker branch `feat/society-v2-stage-b-fix1` (commits 7ccabcf, 09080dc,
  ea5307c, bae0e8f, 972dd15, 00fee2c, f116387) fast-forward merged to
  **dev @ f116387** (dev base was 305198c = the merge-base, so clean FF, no
  rebase). Pushed. `main` untouched (never touched this whole tranche).
- Cross-module validation on dev: python -m pytest tests = **512 passed,
  1 skipped** (13.5 s); ruff check src tests = clean.
- Three independent verdi-reviewer passes: (1) SHIP-WITH-NITS → Finding 1
  mixed-assembly + exposed Assembly-reuse bug (fixed bae0e8f); (2)
  SHIP-WITH-NITS → NIT-1..5 (fixed 00fee2c: per-meeting quarantine,
  cross-assembly regression test, timeline labelling; NIT-4 documented
  out-of-scope; NIT-5 was my commit-message off-by-one, confirmed).
- Stage B gates passed: G0 (frozen protocol docs/LIVE_SOCIETY_CAMPAIGN.md +
  SOCIETY_V2_PROTOCOL.md), G1 (512 passed + ruff), G2 (cross-component:
  validator/reducer/ledger/assembly/sanction/probe all exercised live),
  G3 (deterministic replay + byte-identical log across all 3 live runs +
  offline Stage B replay tests), G4 (SimpleAudit: same-family judge
  caveat recorded on 324 probes each run; 0 quarantine), G5 (sanity: no
  agent starvation, no invariant breach, sanctions/appeals/restorations
  cycled, honest limitation that the Qwen judge is same-family — not
  independent).
- **Honest limitations (recorded, not hidden):** (a) The Qwen judge for
  probes is same-family as the agents — probe scores are a signal, not an
  independent ground truth; an independent-judge check is a G4 gap for a
  future tranche. (b) A "repeat of seed 1" (seed2-fix1) is NOT a second
  seed — it re-ran seed 20261012; the true second seed is 20261013.
  (c) The b01 over-deposit (seed 1) / b05 over-deposit (seed 2) rejections
  are the enforcement working correctly, not failures — the validator now
  catches over-deposits before the reducer would breach nonnegativity.
- Three LIVE run dirs preserved as evidence (never deleted):
  runs/society-v2/stageB-20261012-seed1-fix1 (the original CRASHED run —
  retained as the failure evidence), stageB-20261012-seed1-fix2 (clean
  seed-1 @ concurrency 4), stageB-20261012-seed2-fix1 (seed-1 REPEAT @
  concurrency 6), stageB-20261012-seed2 (TRUE seed 20261013 @ concurrency 6).

### Next 3 actions (Stage C gate)
1. Throughput soak before raising concurrency: seed-1-repeat (concurrency 6,
   mean 3.57 s/p95 10.7 s) and seed-2 (concurrency 6, mean 4.65 s/p95
   11.5 s) show no retry degradation at 6 vs 4 — candidate to raise to 8
   for Stage C only if a dedicated soak at 8 stays zero-retry/zero-quarantine.
2. Stage C (24 → 48 → 96 agents, longer timelines) is now UNBLOCKED: the
   validator/reducer invariant, mixed-assembly ledgering, fresh-Assembly
   lifecycle, and per-meeting quarantine auditability are all on dev and
   live-verified. Build the Stage C scenario(s) from dev on a fresh feature
   branch.
3. Independent-judge G4 gap: source or implement an out-of-family judge for
   the 324-probe battery so probe scores are not same-family self-assessment
   (currently a recorded limitation, not a blocker for Stage C structure).

## Tranche 12 (this update): Society V2 Stage C-1 scale-jump (24x60) — dev lineage @ `5784c62`
Campaign: docs/LIVE_SOCIETY_CAMPAIGN.md Stage C (24/48/96 agents, 60/100/200 ticks).
Branch: `feat/society-v2-stage-c` (worker commits 65c7d0a, 553bd5a, 5784c62) off dev @ b36bafa. `main` untouched.
Pre-registration: docs/STAGE_C_PREREGISTRATION.md (committed BEFORE live outcomes, per scale-jump gate).

### 12a. C-1 scenario + offline scale gate (65c7d0a)
- experiments/generate_stageC.py deterministic generator; society_v2_stageC1.json
  (24 agents c01-c24 ring, 60 ticks, 3 assemblies @15/30/45, 1 adversary c13,
  world food 80, seed 20261014, world_id world-society-v2-stagec1).
- tests/society/test_stageC_offline_scale.py runs the REAL scenario through the
  full engine with a deterministic scripted caller (NO API): byte-identical
  replay + hash chain, denominator 24*60-1=1439 actions, 3 assemblies each
  enacting one (base+3 versions), one-tick suspension + rights restoration,
  648-probe battery (9 ticks x 24 x 3). This is the fast pre-live gate.
- C-2 rung added (553bd5a): 48x100, 5 assemblies @20/40/60/80/95, adversary
  d25, seed 20261015; offline ~42s. Test parametrized over both rungs.

### 12b. LIVE C-1 attempt 1 HUNG (preserved) + stall watchdog (5784c62)
- Attempt 1: `python -m verdi.society.live ... --concurrency 6` progressed to
  event 377 (~27 min) then zero new bytes 9+ min at 0% CPU, one ESTABLISHED
  socket to the vLLM host that never completed a read. httpx read-timeout only
  fires on read activity, so a held-open-without-data connection never times
  out; one hung call holds a semaphore slot and parks the WHOLE campaign.
  Root cause: transient endpoint-side stall (re-probed healthy at 1.3s after
  kill). Recorded in state/LESSONS.md (Tranche 12a).
- Fix (5784c62): independent _stall_watchdog asyncio task in verdi.society.live
  writes heartbeat_utc + events_logged to run_meta.json every 20s; if the
  events.jsonl line count stops growing for --stall-timeout (default 600s) it
  records status=stalled and exits code 3, preserving the partial run. Runs as
  a SEPARATE task so it survives a hung in-flight read. 2 offline tests.
- Evidence preservation: runs/society-v2/stageC1-20261014-attempt1-hung/
  (377 events, gitignored, NOT deleted).
- Verification at commit: 516 passed, 1 skipped; ruff clean.

### 12c. LIVE C-1 attempt 3 — watchdog FALSE-FIRE at t15 (preserved), F1-F3 fixed
- Attempt 3 (5784c62, `--stall-timeout 600`) ran healthily to event 497
  (~35%, ~28 min: 09:58:12Z -> 10:25:54Z) then the watchdog fired: "STALL: no new
  events for 600s (last count 497)", exit 3, run_meta status=stalled. The
  run was NOT hung — it was mid-assembly at t15: assembly ticks write zero
  ledger events until finalize (n probes + n deliberation + n×votes
  sequential calls ≈ 120+ calls ≈ 1000 s+ at p95 latency > 600 s window).
- Independent review (verdi-reviewer 694580fb, `b36bafa..bc9ed5e`,
  verdict SHIP-WITH-NITS) confirmed this as F1 (HIGH) and revises the
  attempt-1 interpretation: both failures stopped at t15, the same assembly
  no-write window.
- Remediation (on this branch): per-model-call liveness beats
  (`progress.jsonl` via `_LiveProgressCaller`; watchdog count = events +
  beats); default `--stall-timeout` 600 -> 1200 s (stuck-call retry window
  180 s × 6 = 1080 s + heartbeat) with a measured floor for user values;
  F2 MEDIUM: atomic `_write_run_meta` (temp file + os.replace);
  F3 LOW: scenario `probe_points` now Stage-B string-list format with a
  NOTE that lifecycle keys drive engine probing; F4 LOW: pre-registration
  doc wording corrected (committed on campaign branch, not dev).
- New tests: `tests/society/test_watchdog_calibrated.py` (5). Verification:
  521 passed, 1 skipped, ruff clean. LESSONS Tranche 12d recorded.
  Partial run preserved: `runs/society-v2/stageC1-20261014-attempt3-stalled/`
  (497 events, status=stalled); attempt 4 writes to a fresh
  `runs/society-v2/stageC1-20261014/`.
- Second independent review (7e512896 on 58c33f3): SHIP-WITH-NITS — F1 fix
  verified sound, no masking of a wedged campaign (per-tick call sets are
  bounded; an isolated stuck call freezes beats), F2-F4 pass, and the
  F3 regeneration is byte-identical for BOTH C-1 and C-2 scenarios. One
  factual nit (attempt-3 duration "~66 min" -> "~28 min" per preserved
  run_meta timestamps) corrected in STATUS + LESSONS; LOW residual
  (dead-call detection latency up to ~20 min) accepted and recorded.

### 12d. LIVE C-1 attempt 4 — F1 fix validated in the field; process lapsed at 735 (preserved), attempt 5 relaunched
- Attempt 4 (58c33f3 + help-string fix, started 10:53:22Z, concurrency 6)
  proved the F1 fix under live conditions: it passed **t15** (the
  kill-point of attempts 1 and 3 — 8+ min of event-free assembly, beats
  kept advancing, no false-fire; the t15 assembly enacted amendment
  #1) and t20 reflection, and by 13:05:01Z had reached 735 events with the
  **t30 assembly enacting amendment #2** (710 accepted / 23 rejected).
- The process then died silently (no crash report, no output; last beat
  13:05:18Z). Root cause recorded as hypothesis (local-environment event)
  in LESSONS Tranche 12e; endpoint re-probed healthy (0.7 s) afterward.
- Preserved as `runs/society-v2/stageC1-20261014-attempt4-lapsed/` +
  console log. All four prior partials retained.
- Attempt 5 relaunched 14:10:16Z (PID 17325, seed 20261014, same
  deterministic scenario, fresh `runs/society-v2/stageC1-20261014/`).
  Smoke OK. Monitor: beat rate per check (lapse detection, not just
  mtime).

### 12f. Parallel assembly batch runner (this update) + attempt 5 partial + attempt 6 launch
- Why: user-reported the SimulaChat endpoint sustains ~10 concurrent calls;
  the live bottleneck was the SEQUENTIAL assembly model calls (24-voter
  deliberation + proposals×voters voting, ~2-3h per assembly pair at
  concurrency 6), not action ticks (already parallel via TaskPool).
- Change: split each assembly call into call+parse (parallelizable) and
  engine mutation (deterministic post-pass in sorted input order).
  `Assembly.run_batch` is an injected runner (default sequential
  `_run_sequential`); the campaign injects `_run_bounded` (asyncio
  Semaphore, size = run concurrency) per assembly. Governance stays
  asyncio-free (its determinism-guard import test blocked the direct
  import and drove the injected design). Reflection parallelized too;
  probes stay sequential (smaller cost; stateful subject_provider).
- Determinism contract: engine mutations / quarantine / proposal_order /
  vote records in the SAME order as the old loops → identical model outputs
  give identical ledger/state; only wall-clock and model_calls.jsonl row
  order (telemetry) differ. Replay re-folds the tape with zero model calls,
  so call order cannot affect replay hashes.
- Evidence: new tests (a) assembly-level seq-vs-concurrency-8 equivalence
  (proposals, votes, quarantine, abstentions, outcome, engine version all
  equal) and (b) full-campaign seq-vs-parallel equivalence — identical
  final_state_hash, identical state model_dump, byte-identical
  events.jsonl, replay True/True — PLUS a mechanical proof that the two
  paths differ (probe caller: max in-flight assembly calls >1 parallel,
  ==1 when sequential forced; the equivalence-only version of this test
  silently ran both paths sequentially due to monkeypatch lifetime and
  proved nothing — see LESSONS Tranche 12f). Full suite green, ruff clean.
- Attempt 5 (sequential code, concurrency 6) reached 639 events, past t20,
  amendment #1 enacted, healthy beat rate — then its run dir was renamed to
  `stageC1-20261014-attempt5-partial` to hand the path to attempt 6; the
  process survived the rename briefly (re-opened its beat file on the new
  path) and died on the next atomic run_meta write (stale tmp path —
  expected failure mode of the rename trick, no run data lost).
- Attempt 6 LIVE: same seed 20261014, same scenario, concurrency 10,
  parallel assembly code, PID 45753, 17:4x UTC, smoke OK (trace
  7c14362b). First-assembly passage (t15) is the gate for the parallel
  path; 429 throttling, if any, shows as quarantine spike + retries, not
  a crash (VERDI_MAX_RETRIES=5).
- **Attempt 6 gate PASSED (18:17 UTC): past t15 AND t20 assemblies.**
  569 events, 1455 model-call beats, snapshots t00/t05/t09/t15/t20 all
  written — the two parallel-assembly ticks that killed attempts 1 and 3
  (sequential) completed cleanly on the parallel path. Beat rate ~1/s
  sustained vs ~0.5/s sequential (attempt 5). Both parallel assemblies
  (~3 min wall-clock each) vs ~2-3h projected sequential.
- Independent review verdict on a8e1745: SHIP-WITH-NITS (determinism
  contract, exception parity, no-asyncio boundary, replay safety, and no
  t15 crash risk all verified). Nits fixed in fed2992: docstrings now
  describe the parallel regime (a single wedged call is masked by
  continuing healthy beats for up to the 1200s stall window — documented
  sensitivity loss, not a bug; pinned by
  test_watchdog_masks_single_wedged_call_under_parallelism), run_batch
  concurrency arg marked advisory. Full suite + ruff clean at fed2992.
- **Attempt 6 OUTCOME (19:05 UTC, status=failed, preserved as
  stageC1-20261014-attempt6-524-failed): 1094 events, 3274 beats, all 3
  assembly ticks PASSED (t15/t20/t45 snapshots written, 3 amendments
  enacted — t45's amendment is event 1093), died at ~tick 46 on ONE
  un-retried HTTP 524 (Cloudflare origin-timeout; RETRYABLE_STATUS lacked
  524).** The parallel-assembly hypothesis is now validated live: three
  full 24-voter parallel assemblies completed in a ~92-min run where the
  sequential code died at the first (attempts 1 & 3) or was projected at
  ~2-3h per assembly pair. Partial data fully intact for analysis.
- **Fix 25a76a4:** 524 added to RETRYABLE_STATUS (same transient class as
  502/503), 524-then-200 recovery test + budget-exhaustion parametrization
  extended, full suite + ruff clean. Endpoint re-probed healthy (3x OK,
  0.5-0.7s) before relaunch.
- **Attempt 7 LIVE:** same seed 20261014 / scenario, concurrency 10, 524-
  resilient client, PID 377, ~19:06 UTC, smoke OK (trace f6d29383). This
  attempt only needs to reach t60 to complete C-1.
- **C-1 COMPLETE (attempt 10, c25 non-thinking, 22:14 UTC):**
  - status=healthy, replay True/True, log_verify True, quarantine 0%
  - 1428 events, 2672 model calls, wall 83.7 min (vs ~3.5 h thinking)
  - 3 amendments enacted (v11/v15/v21) + 1 amendment rejected
  - 25 distinct actors (24 agents + assembly), 38 rejections
    (19 overdraft, 19 insufficient_world)
  - Wealth 396→576 (+45%), credits spread 0–28 (started all 10)
  - timeline.md written (86 KB with deliberation texts + votes)
  - Attempts 7/8/9 preserved as partials (thinking-mode c10, c10, c25)

### Next 3 actions (Stage C)
1. Monitor attempt 6 (PID 45753, concurrency 10, parallel assembly) to
   completion with per-check beat-rate sampling; the t15 assembly is the
   parallel-path gate (wall-clock should drop from ~2h to ~15-30 min);
   verify replay True/True, 24/24 agent denominator, 3 assemblies,
   endpoint health, final hash; record evidence in 12f.
2. On C-1 pass: independent review of the parallel-runner commit, then
   merge feat/society-v2-stage-c to dev (serial), then C-2 (48x100) live
   launch (gated on C-1 scale-jump gate).
3. Throughput measurements on attempt 6 (beats/sec per tick class) inform
   the C-3 (96 agents) concurrency choice; multi-seed / topology /
   adversary-prevalence effect study stays on C-2/C-3 and is NOT
   claimable until independently reviewed.

## Tranche 10 (prior): Society V2 Stage A (live society campaign) — dev 55a6c5d

## Tranche 10 (this update): Society V2 Stage A (live society campaign) — dev 55a6c5d
Campaign: docs/LIVE_SOCIETY_CAMPAIGN.md + docs/SOCIETY_V2_PROTOCOL.md (frozen Stage A protocol, G0).
Branch lineage: feat/society-v2-protocol off dev@bf4bfd4, fast-forward merged to dev (commits 6b1cfa3, c73ca22, 288a307, f56f747, 55a6c5d).

Scope delivered:
- src/verdi/society/ — campaign orchestrator (concurrent-generation /
  sequential-commit loop, private AgentMemory feedback, longitudinal probe
  battery + leak guard, real model-voted constitutional assembly, sanction +
  deferred-appeal governance), live entrypoint (SimulaChat default Qwen3.8-27B
  via existing vLLM config, bounded concurrency, smoke call, key-material
  guard, status meta), memory, prompting, seeded task tape.
- src/verdi/world/validate.py + observation.py (deterministic action
  validation incl. under_sanction blocking; observation channel).
- src/verdi/world/replay.py TapeStep extended (kind="rights_change" with
  rights payload) so world-state flag flips are tape-recorded and replayed.
- src/verdi/governance/assembled.py, src/verdi/measurement/longitudinal.py.
- experiments/scenarios/society_v2_stageA.json (6 agents, 12 ticks).
- tests: 40 new society tests + governance/measurement/world lanes.

Independent review (BLOCK → remediated → SHIP):
- F1 (HIGH, resolved): commit loop re-validates against evolving state;
  same-tick double-spend now rejected as insufficient_world (regression test
  test_two_same_tick_withdraws_do_not_double_spend).
- F2 (HIGH, resolved): sanctions deterministically flip under_sanction in
  world state (agent suspended from tape; validator blocks transfer/deposit);
  flips recorded as rights_change tape steps (regression test
  test_rights_change_flips_world_state_and_blocks_sanctioned_agent).
- F3 (resolved): appeals filed at violation_tick+2, adjudicated against
  pre-registered deadline violation_tick+4 (deferred, no same-tick tautology).
- F4 (resolved): adversary test now asserts real enforcement effects.
- Re-review verdict: SHIP-WITH-NIT; nit #1 (library replay() rights_change-
  blind) tracked as issue #1. 4 additional non-blocking nits recorded.

Verification (dev @ 55a6c5d):
- python -m pytest: 502 collected, all passing (incl. 40 society), no
  failures.
- ruff check src tests: clean.
- G2/G3 offline: full offline Stage A run produces all artifacts, replay
  byte-identical (log bytes + final state hash), 90 probes, tick accounting
  reflects sanctioned-agent suspension (n_agents*ticks-1 committed actions
  when one agent is suspended one tick).

LIVE Stage A re-run (fixed code), runs/society-v2/stageA-20261011-fix1/
(gitignored), SimulaChat default Qwen3.8-27B, concurrency 4, seed 20261011:
- 72 model calls, 604.4s wall (~8.4s/call incl. governance; prior baseline
  78 calls / 1045.7s — endpoint faster this window).
- 67 events: 42 action_accepted, 24 action_rejected (12 empty_signal,
  12 missing_argument), 1 amendment_enacted (constitution v5 enacted).
- Governance: 6 sanctions (all 6 agents violated over 12 ticks), 6 appeals
  all filed at violation+2 and upheld (deadline violation+4), 6 restorations;
  all final under_sanction flags False (rights restored before tick 11).
- Replay: replay_matches=True, replay_log_identical=True.
- Probes: 90 points (30 per channel), leak guard clean.
- run_meta status: "complete"; final_state_hash f7b4874e… (recorded).
HONEST LIMITATIONS (frozen): 0 under_sanction-blocked rejections observed in
this run (sanctioned agents' suspended tick produced no transfer/deposit
attempts — the F2 blocking path is covered by unit tests, not yet by live
traffic); 24 rejections are malformed arguments (empty_signal/
missing_argument), reflecting prompt/schema friction of the reasoning model,
not substantive violations; no live double-spend occurred (F1 path covered
by regression test). Pre-fix live run (pre-F1-F4 code) preserved at
runs/society-v2/stageA-20261011/ for comparison.

Blockers: none.
Next 3 actions:
1. Stage B scenario (12 agents, 30 ticks, multi-seed, honest+adversarial,
   governance interventions) — build + offline-validate, then live run.
2. Concurrency soak: raise to 8 after throughput measurements confirm no
   429/5xx trend; record per-call latency distribution.
3. Fix issue #1 (library replay() should handle/reject rights_change steps)
   before Stage B live runs.
Release readiness: NOT READY for main (Society V2 is dev-only; main
publication remains manual/curated). main untouched in this tranche.

---

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

## 2026-10-12 (UTC) — Tranche 12i + 12j merged to dev
- dev HEAD: 327d2b4 (12i reconstructability logging, 12j memory cap); full suite + ruff green post-merge
- New artifacts: sanctions.jsonl, resources.jsonl (per-tick), action_reasons.jsonl (sidecar, hash-chain untouched), constitution_history full text; model_calls.jsonl now streaming (watchdog liveness); progress.jsonl eliminated
- Prompt-growth root cause #2 found & fixed: full-state observation snapshots in private memory grew every prompt linearly (capped run: 99K tok @ t59, 2.9s→32.1s). Fix: field projection + 300-char entry cap; 50-tick offline probe max action prompt ~540 tok (flat)
- quick-5x50-capped run completed healthy (373 calls, replay True/True) — used as diagnosis source
- Next: fresh live quick-5x50 on dev (concurrency 10) to validate flat prompts + new artifacts, then C-2 scale decision
