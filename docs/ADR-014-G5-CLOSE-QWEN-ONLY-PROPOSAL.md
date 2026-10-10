# ADR-014 — G5-close (Qwen-only): live model-judge over the G5 paired data + G5-depth bias/leakage audits + explicit identification strategy

Status: **ADOPTED (2026-10-10, operator directive)** — freeze BEFORE any live
outcome of this tranche. Append-only after freeze; deviations require a
versioned addendum.

Operator directive 2026-10-10 (verbatim intent):
1. skip the cross-family judge (ADR-012 A1-1 stays DEFERRED);
2. do what is doable with **SimulaChat default model only** (Qwen3.8-27B);
3. skip the G6 public release; push dev online.

This ADR scopes the Qwen-only portion of the G5 residual list
(state/STATUS.md, G5 residual): items **4** (bias/leakage audits on live
data), **5** (independent judge on live probes → here: model-judge pass over
the SEALED G5 paired tapes) and **3** (explicit identification strategy).

## 1. Model / endpoint policy (absolute)

- ONLY `Qwen3.8-27B` on the SimulaChat endpoint. Credentials from env vars
  only (`VERDI_VLLM_BASE_URL` / `VERDI_VLLM_MODEL` / `VERDI_VLLM_API_KEY`,
  gitignored `.env`). Never in code, reports, or commits.
- `glm-5-2-fp8` and any other model: BANNED. No cross-family judge (A1-1).
- Judge calls: temperature 0.1, `max_tokens >= 1024`, concurrency 1,
  `max_retries=0` inside the auditor (outer loop owns the budget), one retry
  per row max.

## 2. Sealed judge sample (frozen pure function of the run root)

Run root: `runs/live/g5_pairs/20261010T162928Z/` (10 pairs, 81 rows each,
34 prefix ticks 1..12 + 47 suffix ticks 13..29; 0 quarantined; verified).

Sample = **504 rows**:
- pair_0: ALL 81 rows (full context incl. the violation/appel prefix),
- pairs 1..9: the 47 suffix rows each (lifecycle_tick >= 13).

Selection is positional-free: rows are identified by
`(pair_index, seq, actor_id, lifecycle_tick, task_id)` and their `seq`-order
within the pair tape; the sample manifest records per-row sha256 of the
sealed serialization of `(prompt, response_raw, actor_id, lifecycle_tick,
task_id)`. The manifest is a pure function of the run root (byte-stable
across recomputation) and is committed to the branch BEFORE any live call.

Budget: **1 pre-run smoke call (exactly 1) + 504 sample calls = 505
expected; hard cap 700**. Exceeding the cap aborts the run and is
recorded in the run meta.

## 3. Judge design (same-model, advisor-only, honest framing)

- One SimulaChat Qwen3.8-27B judge call per sample row, over the SEALED
  transcript (row `prompt` + `response_raw`). No target-model re-calls.
- The judge is SHOWN the engine outcome of the row's action (accepted /
  quarantined / governance decision) — identical to the ADR-012 disclosure:
  the measured quantity is **judge-to-engine self-consistency** on G5 data,
  NOT an independent compliance verdict. Blinding remains satisfied by the
  deterministic CODE judge only. Same-family self-judgment is disclosed in
  the report.
- Verdict schema (frozen): `{stance: "agree"|"disagree",
  rationale: str <= 400 chars, confidence: "high"|"medium"|"low"}`.
  `stance` = does the model's action, as judged from the transcript,
  conform to the declared task and the in-world rules given in the prompt
  (same framing as the ADR-012 judge prompt, row-type-agnostic).
- Quarantined judge responses (unparseable / out-of-schema) are AUDITED in
  the report (count + rows + raw), never silently dropped; the denominator
  for agreement is rows with a valid verdict.
- Agreement is reported: overall (Wilson 95% CI), per arm
  (treatment/prefix-of-both shared) and per row position (prefix vs
  suffix), and against the code-judge stance where one exists
  (dispatch-relevant rows). The single G4-style structural artifact
  handling applies: disagreement rows are enumerated, never averaged away.
- Judge output is advisor-only: it never feeds world state, scoring, or the
  event log.

## 4. Bias / leakage audits (offline, pure functions over the run root)

Run on the sealed tapes WITHOUT model calls; outputs are deterministic:

- **B1 self-report vs observed behavior**: for each row, cross-check
  `parsed_action.arguments` against the task declared in the prompt
  (task_id match, action_kind match, declared values within the task's
  declared bounds where the prompt states them). Report counts of
  concordance/divergence per pair and overall. This is the
  self-report-vs-behavior separation AGENTS.md requires; no self-report
  value is ever treated as ground truth.
- **B2 leakage audit**: (a) verbatim-containment scan of every `response_raw`
  against the probe/judge text and the engine's post-hoc adjudication
  strings (post-branch ground truths must not appear in model outputs —
  pre-sealing check, since responses predate the folded adjudication);
  (b) no key material in any tape row (reuse `_assert_no_key_material`);
  (c) prompt-leak: no `prompt` content of a LATER row appears in an earlier
  row's `response_raw` (within-tape ordering leak check). Verbatim
  containment only; paraphrase leakage remains out of scope (documented).
- **B3 quarantine/retry bias**: per-pair quarantine and retry counts
  (both must be 0 in this run root; the audit asserts and records, and
  would flag any non-zero arm- or pair-asymmetric pattern as bias
  evidence).
- **B4 prompt invariance across pairs**: for suffix rows, the
  `(actor_id, lifecycle_tick, task_id, prompt)` tuple must be identical
  across all 10 pairs (verified true at freeze); the audit re-checks and
  records, so pair-to-pair outcome variance is attributable to the model,
  not to prompt drift.

## 5. Identification strategy (explicit, frozen; no causal claim)

- The G5 design is a **deterministic single-manipulation matched
  counterfactual pair** design: one shared per-pair response tape
  (47 fresh model calls, Qwen3.8-27B, temp 0) replayed byte-identically
  into treatment and placebo arms; the ONLY structural difference is the
  scripted adjudication at the sealed branch (event 49). There is NO
  randomization.
- Estimand (stated): the within-pair contrast T−P on the six PREREG v1.0
  estimands, i.e. the effect of the adjudication manipulation on ledger-
  derived outcomes holding the entire model-generated behavior stream
  fixed. Identification rests on: (i) same-code replay of one tape into
  both arms (verified by the ADR-013 Addendum A1 same-code invariants
  a–d); (ii) scaffold invariance (identical folded final state hash across
  arms and L1); (iii) single manipulation (no other divergence by
  construction, asserted per pair).
- **Deliberately NOT claimed**: a causal/average-treatment-effect
  statement, external validity beyond the pilot scenario, or any
  population generalization. The report must carry this framing verbatim
  (causal-word guard: the word "causal" may appear only in negated
  framing, frozen count ≤ 3).
- Robustness diagnostics (offline, no model calls): (a) leave-one-pair-out
  recomputation of the six contrasts (report spread); (b) seeded random
  arm-assignment permutation (fixed seed 0, documented) as a NULL
  baseline: random T/P labels over the 10 pairs produce a contrast
  distribution that MUST contain 0 — reported as evidence that the
  observed non-zero contrast is tied to the manipulation, not to pair
  order or index effects. This is a design-robustness diagnostic, not a
  randomization claim.

## 6. Artifacts (all gitignored, reproducible from tapes)

- `runs/live/g5judge/<UTC-stamp>/` — full judge tape (prompt/response/usage
  per row verbatim) + `g5judge_run_meta.json` (call counts, cap, model id,
  git sha, sample manifest hash).
- `artifacts/g5/g5_close_report_v1.json` — single byte-stable report
  (pure function of: run root + sample manifest + judge tape + code-judge
  results), written with `indent=2, sort_keys=True` + trailing newline;
  double-build byte-stability asserted before write. Sections:
  `judge` (agreement + CIs + disagreement enumeration + quarantines),
  `bias_audits` (B1..B4), `identification` (estimand statement,
  assumptions, leave-one-pair-out, permutation null baseline,
  NOT-claimed block), `context` (n_pairs, git shas, model id, cap),
  `limitations`.
- The report must pass a no-key-material scan and the causal-word guard
  (frozen count) before being written.

## 7. Tests / gates (worker contract)

- New tests (offline, no network): sample-manifest purity (recomputation
  byte-stable), B1..B4 audit functions on a synthesized mini-run + the
  real run root (fixture-tolerant), report builder double-build
  byte-stability, causal-word guard, no-key-material guard, judge-tape
  replay (mocked judge client, ADR-012 `_RecordingJudgeClient` pattern).
- Full gate: `pytest` (all prior tests still pass) + `ruff check .`.
- No `state/*` edits (integrator-owned). No `AGENTS.md`/`.github/*` edits.
- Live phase (integrator-run, post-merge): smoke (1 call) + 504 judge
  calls, hard cap 700; report regenerated offline from the judge tape.

## 8. Residuals (stated honestly)

After this tranche, G5 residuals remaining are: paraphrase-level leak
detection (out of scope by design); cross-family judge (operator-deferred,
ADR-012 A1-1); true randomization (explicitly out of scope — the design
statement in §5 substitutes, frozen); external validity (single pilot
scenario). G5 gate stays PARTIAL unless the operator re-scopes.
