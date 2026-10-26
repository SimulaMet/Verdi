# Institutional recovery experiment — development status

This is a FRAMEWORK feature, not a project assigned to nontechnical users.
Researchers should be able to choose a prepared scenario and condition and
obtain auditable, comparable outcomes without editing Python.

## What EXISTS on this branch (committed)

- Scenario key: institutional_condition: fixed | revisable (default revisable).
- Both conditions retain the same scheduled constitutional meetings,
  deliberation calls, proposals and votes. The fixed condition rejects enactment
  with reason institution_fixed_rules; revisable can enact valid amendments.
- Typed, machine-enforceable constitutional rules:
  - max_transfer_units: strictly positive finite number
  - max_withdraw_units: strictly positive finite number
  - max_produce_units_per_tick: strictly positive finite number (constitutional
    production cap, overrides the scenario-level economy cap when enacted)
  - min_commons_reserve: mapping of resource to nonnegative finite number
- The existing deterministic action validator enforces those rules AFTER
  original immutable invariants, under the separate constitution:* codes.
- Amendments with malformed recognized policy values are rejected before
  enactment. Narrative rules continue to be stored but are not executable.
- Future-effective versions do not affect action validation before their date.
- The meeting and action prompts expose rules relevant to participants.
- Opt-in constrained economy v1 (versioned): per-resource production caps,
  credit costs, and per-tick consumption. Absent in legacy scenarios, which
  keep unlimited free production exactly as before.
- Objective task oracle: delivery objectives evaluated purely from committed
  world state at their due tick (state diff vs the start-tick baseline) — no
  agent self-report, no LLM judge.
- Paired runner (P0-C): python -m verdi.experiments.paired_run --config
  <yaml> builds both arms from one base scenario changing ONLY
  institutional_condition, asserts identity programmatically, freezes a
  resolved scenario per run dir, writes an atomic resumable pair_manifest.json,
  and records partial failure instead of aborting. Offline unit tests cover
  validation, identity assertion, partial failure, resume, and the CLI path.
- End-to-end amendment-changes-action test (offline, scripted caller): an
  ENACTED amendment (max_transfer_units) inside a real SocietyCampaign causes
  an identical subsequent transfer to be REJECTED with
  constitution:max_transfer_units while a within-cap transfer is accepted.
- Researcher-facing docs: docs/RESEARCHER_GUIDE.md (quickstart, editable-field
  table, what-if recipes, glossary, troubleshooting) and docs/SCENARIOS.md
  (gallery of every scenario file). A docs lint-style test keeps the guide's
  command strings and links honest.

No other form of amendment is magically executable. In particular, prose
changes about fairness or rights currently remain descriptive metadata.

## Run the example

Install as in README, configure a real endpoint via .env, then run BOTH arms
with ONE command:

    python -m verdi.experiments.paired_run --config examples/paired_institutional_recovery.yaml

Outputs land in runs/paired-institutional-recovery/<arm>-seed<N>/report.html
plus pair_manifest.json. For a single arm only, the classic path still works:

    python -m verdi.society.live --scenario experiments/scenarios/institutional_recovery_smoke.yaml --run-dir runs/revisable-42

See docs/RESEARCHER_GUIDE.md for the full researcher workflow.

## What REMAINS

- No live end-to-end model run has been executed on this branch; the paired
  runner is validated offline (scripted callers) and in CI. A documented
  full-pair smoke run against a real endpoint is still outstanding.
- Comparative report tooling (cross-condition CSV/JSON exports, a single
  comparison page over a pair manifest) is planned in a sibling change and is
  NOT merged into this branch; outcome_metrics.py is not present here. Until
  it lands, compare arms by opening each report.html and raw ledgers.
- Statistical CIs / paired-seed uncertainty intervals are not implemented;
  seeds do not make LLM outputs deterministic, so paired repetitions across
  multiple seeds are required before any effect claim.
- Preregistered recovery-time definition (target fraction, censoring rules)
  is still a definition, not code.

## Crucial scientific limitations (do not report positive findings yet)

1. The constrained economy (opt-in v1) makes the smoke scenario's food shock a
   meaningful scarcity intervention, but legacy scenarios still allow costless
   production — do not run those as recovery studies.
2. There is no per-agent exclusion-duration metric or preregistered
   recovery-time definition implemented yet (the delivery oracle covers task
   success only).
3. Seeding the simulator does NOT make stochastic LLM generations identical.
   Record model revision, sampling settings and calls; run paired repetitions.
4. A currently registered future-dated constitution can differ from the
   currently effective constitution. The policy layer selects the effective
   version for action validation, but state/prompt version bookkeeping needs
   a full audit for multiple overlapping future-dated amendments.
5. Enacted narrative amendments remain non-binding. Do not count them as
   effective adaptations.
6. Probe self-reports and hypothetical public responses are exploratory, not
   evidence that actual world actions complied with a value.
7. Inner-life updates are opt-in; ordinary memory consolidation can still
   introduce reflective dynamics. Freeze optional goal development consistently
   in both conditions until it can be studied as a separate intervention.
8. Policy limit violations are logged as policy_limit, but this does NOT
   automatically impose punishment. Sanction rules are an independent mechanism.
9. No end-to-end LIVE model run has been executed on this branch. Offline
   scripted-caller tests and CI cover the mechanics; treat results as
   provisional until a documented live smoke run exists.

## Status of previously required next steps

DONE on this branch:
- P0: offline end-to-end scripted fixed/revisable integration tests, including
  an amendment-changes-action proof; ruff + pytest run in CI on push/PR to dev.
- P0 (partly): shock/task environment with production caps/costs, consumption,
  and objective delivery success checks evaluated from committed state.
- P0-C: researcher workflow — single documented command
  (python -m verdi.experiments.paired_run --config <yaml>), editable fields
  for seeds/duration/output, identity assertion between arms, partial-failure
  manifest with resume, readable config errors, docs/RESEARCHER_GUIDE.md and
  docs/SCENARIOS.md. No Python editing required.
- P1 (partly): researcher-facing documentation, scenario gallery, glossary and
  readable error messages.

STILL OPEN:
- P0-D: outcome definitions/extraction beyond the delivery oracle
  (resource_sharing, exclusion durations, recovery time with censoring,
  institutional-mechanism metrics) and their machine-readable exports.
- P1: comparative report view over a pair manifest, CSV exports, paired-seed
  CIs / uncertainty intervals, artifact checksums, and fixed-vs-revisable
  fairness checks for prompt visibility and call budgets.
- Live smoke: one documented full-pair run against a real endpoint with
  downloadable artifacts (or an explicit environmental blocker recorded).

## Mechanism and non-regression contract

A proposal becomes a new constitutional version only after passing universal
invariants, typing, quorum, threshold and temporal validity. The binding rules
applied to an action are those effective on the current simulation loop tick.
Immutable no-overdraft and nonnegative-resource checks cannot be weakened.
A rejected policy-constrained action must be present in the event ledger; the
reducer must not mutate its substantive world state. Tape replay and state
hashes must continue to match the recorded execution.

When implementing more executable policies, add each to an explicit finite
schema with deterministic semantics, unit tests, and invariant protections.
Never execute arbitrary LLM-authored Python, formulas or natural language.
