# Stage C-1 pre-registration (scale-jump proof)

**Pre-registered BEFORE the C-1 live run.** Per `docs/LIVE_SOCIETY_CAMPAIGN.md`
("Stage C" + "Acceptance/recovery/abort gates"), estimands and stopping rules
must be fixed before live outcomes. This file is committed to `dev` *before*
the run so its SHA is auditable.

## What C-1 is and is not
C-1 is the **first rung of the scale ladder** (24 agents / 60 ticks, 3
assemblies, 1 adversary, ring topology). It is a **scale-jump proof**, not a
behavioral-effect experiment. Its job is to show the verified Stage B system
runs correctly and coherently at 2× Stage B scale. The multi-seed,
topology-varying, adversary-prevalence (0/1/3) **effect** study is C-2/C-3 and
is gated on C-1 passing. **No behavioral / value-drift effect is claimed from
C-1 (single seed).**

## Primary estimands (fixed, not post-hoc)
1. **Completion & determinism (E1).** Run reaches `status=complete` and the
   replay is bit-identical (`replay_matches=true`, `replay_log_identical=true`)
   — i.e. the deterministic reducer holds at scale.
2. **Denominator completeness (E2).** Every one of the 24 agents performs ≥1
   accepted action over the run; the number of eligible-agent-task offers
   matches what the tape generator actually issued (no silent drop), and the
   per-agent coverage is reported, not assumed.
3. **Event-log coherence (E3).** The event log is internally consistent:
   hash-chain intact, `cause_event_id` links resolve, rejected/accepted
   partitions match recorded `after_hash` transitions, and every assembly
   ledgered its enacted *and* rejected proposals (mixed-assembly invariant
   from Finding-1).
4. **Governance integrity (E4).** All 3 assemblies run with real
   deliberation + voting (no fake scripted result); any enacted amendment is
   ledgered with a `governance` step; sanctions/appeals/restorations (if any)
   are recorded with evidence.
5. **Endpoint health (E5).** At concurrency **6** (held at the proven-safe
   value; raised to 8 only after a dedicated zero-retry/zero-quarantine soak
   per Stage B next-actions), endpoint error rate (429/5xx/timeout) is within
   the retry ceiling and mean/p95 call latency is reported.

## Uncertainty / nondeterminism (declared limitation)
The actor/judge is a single same-family Qwen3.8-27B. LLM output is
nondeterministic at temperature > 0; `replay_matches` only proves the
**deterministic reducer + recorded outputs** are stable, not that a fresh API
run reproduces identical model text. Judge dependence and the same-family
judge limitation (a G4 gap) carry over from Stage B. These are reported, not
averaged away.

## Stopping / recovery rules (fixed)
- **Invariant breach or unexplained crash** → preserve the trace and the
  crashed run dir, minimize to a repro, add a regression test, fix on a
  feature branch, restart from the last verified checkpoint. Do NOT silently
  restart the whole campaign.
- **Unexplained denominator drift** (agents who acted ≠ 24, or task offers
  silently changed) → stop, inspect, report the exact version + counts.
- **Rising endpoint error rate at concurrency 6** → reduce concurrency
  (do not saturate the server), record the measurement.
- **Cap at 3 identical failed fix attempts** before escalating to the human
  operator.
- **Do not claim effects** until the multi-seed C-2/C-3 phase is complete and
  independently reviewed.

## Concurrency decision
Hold at **6** for C-1 (proven zero-retry in Stage B at 6). The 6→8 raise is
gated on a dedicated soak, which is tracked separately and is not a C-1
prerequisite for a 24-agent run.
