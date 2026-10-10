# ADR-012 (PROPOSAL — NOT ADOPTED): independent live-judge pass to close G4

Status: **PROPOSED — awaiting operator approval for judge-LLM budget.**
Nothing in this document is a frozen contract until adopted in
`state/DECISIONS.md` with operator sign-off. Frozen per the verdict program
rules BEFORE any judge outcome is observed.

Date proposed: 2026-10-10. Base dev SHA: `b48beab`.

## Context (what G4 needs to flip to PASS)

Per `docs/ACCEPTANCE.md`, G4 requires an independent judge with reported
disagreement — the L2 bridge uses SimpleAudit's *offline* judge API
(`grade_choice_match`, `normalize_span`, `validate_decision`), which is
deterministic and code-only: it is NOT an independent model. Residuals from
the Tranche 6 L2 review:

1. No independent LIVE judge-model pass over the sample, with reported
   agreement/disagreement vs the code-level judgment.
2. The fixed 10-row judge sample is ALL-ACCEPTED; the sole engine rejection
   (event 42) fell outside the sample, so the compliance-form metric is
   tautological on the sample (disclosed in the G4 report).
3. `RunReport.simpleaudit_version` — DONE in `b48beab` (live runs stamp the
   installed version; recorded runs keep `None` by design).

## Proposed scope

### A. Judge infrastructure (new, minimal, reviewable)

* New module `src/verdi/audit/judge_live.py` (disjoint from `live_bridge.py`
  except for reusing `LiveTapeTraceSource` + correlation).
* Environment-only configuration (no new env var names in code comments
  beyond these):
  * `VERDI_JUDGE_BASE_URL`, `VERDI_JUDGE_API_KEY` — judge endpoint (may be
    the SAME simulachat endpoint; see fallback below).
  * `VERDI_JUDGE_MODEL` — judge model id.
* SimpleAudit 0.4.0 `ModelAuditor` used through a thin adapter
  (`simpleaudit.model_auditor.ModelAuditor(model=..., provider="openai",
  judge_model=..., judge_provider="openai", base_url=..., judge_base_url=...,
  api_key=..., judge_api_key=...)`), fed with `ScenarioExecution` objects
  built from the captured tape (verbatim prompt as the turn,
  `trace_correlation` carrying the sealed `event_id` — correlation stays by
  sealed event_id, NEVER positional).
* SimpleAudit remains a dependency through its public API only; no SimpleAudit
  code is copied or modified (ADR-003 / ADR-011).

### B. Sample construction (preregistered, frozen before judging)

* Sample = **all 81 dispatch events** of the L1 run (81 calls is within the
  run's own 120-call live budget spirit; this is a separate, explicitly
  approved judge budget — see C), OR a fixed **20-row stratified sample**
  (operator choice at approval; default: all 81).
* Stratification guarantee: every engine-rejection row is IN the sample
  (event 42 must be judged) — this fixes residual 2.
* Sample selection is a pure function of the tape (sorted by (tick, actor_id,
  task_id), deterministic seed recorded in the report). No row is chosen
  after seeing judge output.

### C. Budget and nondeterminism (frozen)

* Judge model calls: **<= 96** (all-81 sample + 4 retry headroom),
  concurrency 1, temperature 0.1, judge max_tokens >= 1024 (reasoning-model
  lesson from L1: <1024 can yield empty content).
* The judge endpoint is acknowledged nondeterministic, exactly as in ADR-010:
  the judge result is reproducible-from-recorded-judge-tape (full judge
  prompts/responses/usage captured to `runs/live/judge/<ts>/`), not
  bit-reproducible on re-run.
* Quarantined (unparseable judge responses) are audited, not dropped.
* Judge output never feeds world state, scoring, or the event log; it is an
  ADVISOR artifact only (AGENTS.md hard rule; L2 framing kept).

### D. Report (deterministic post-processing)

* New report `artifacts/live_audit/g4_judge_<ts>.json` produced by a pure
  function over the judge tape + code-level judgments:
  * per-row: code judgment, judge verdict, agreement flag, disagreement
    reason string;
  * summary: agreement rate, disagreement table (full enumeration —
    disagreements are the point of G4, not an average), per-decision-class
    breakdown (accepted vs rejected rows judged separately);
  * model ids, endpoints (no keys), versions, sample seed, call counts.
* Disagreement with the deterministic engine/judge is EXPECTED and reported,
  never suppressed; the report states explicitly which party (engine,
  code-judge, model-judge) holds which verdict.

### E. Fallback if no distinct judge model is available

* If the operator has only Qwen3.8-27B on the endpoint, the "independent"
  judge is a same-family model: this MUST be disclosed in the report
  (same-model self-judgment, reduced independence), and G4 stays PARTIAL
  with that disclosure UNLESS the operator approves same-model judging as
  sufficient for this testbed (explicit decision recorded in ADR-012 adoption).

## Out of scope

* Any change to the pilot, world, governance, or scenario (G5 paired arms
  are ADR-013-territory / next tranche).
* CrossJudgeExperiment / A/B judge comparison (future; needs its own budget).
* Any main-branch work or public release.

## Adoption checklist (operator)

1. Approve judge-LLM budget (<=96 calls) and judge endpoint/model.
2. Approve all-81 vs 20-row sample (default all-81).
3. Approve same-model fallback policy (E) if applicable.
4. On approval: this file is frozen, ADR-012 is recorded in
   `state/DECISIONS.md`, work is dispatched to a feature branch with
   independent engineering + science review, serial merge to dev.
