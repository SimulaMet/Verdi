# Institutional recovery experiment — development status

This is a FRAMEWORK feature, not a project assigned to nontechnical users.
Researchers should be able to choose a prepared scenario and condition and
obtain auditable, comparable outcomes without editing Python.

## What this branch adds (first pass; not yet CI-validated)

- Scenario key: institutional_condition: fixed | revisable (default revisable).
- Both conditions retain the same scheduled constitutional meetings,
  deliberation calls, proposals and votes. The fixed condition rejects enactment
  with reason institution_fixed_rules; revisable can enact valid amendments.
- Typed, machine-enforceable constitutional rules:
  - max_transfer_units: strictly positive finite number
  - max_withdraw_units: strictly positive finite number
  - min_commons_reserve: mapping of resource to nonnegative finite number
- The existing deterministic action validator enforces those rules AFTER
  original immutable invariants, under the separate constitution:* codes.
- Amendments with malformed recognized policy values are rejected before
  enactment. Narrative rules continue to be stored but are not executable.
- Future-effective versions do not affect action validation before their date.
- The meeting and action prompts expose rules relevant to participants.
- An illustrative YAML smoke scenario is provided.

No other form of amendment is magically executable. In particular, prose
changes about fairness or rights currently remain descriptive metadata.

## Run the example

Install as in README, configure a real endpoint via .env, then:

    python -m verdi.society.live --scenario experiments/scenarios/institutional_recovery_smoke.yaml --run-dir runs/revisable-42

For the fixed condition, copy the YAML to another path and change ONLY:

    institutional_condition: fixed

Run that copy with the same seed/model and a new run directory.
Open report.html in each output folder; compare raw ledger and metrics too.

## Crucial scientific limitations (do not report positive findings yet)

1. The current resource economy allows costless production. A food shock alone
   is NOT a meaningful scarcity intervention. Implement resource constraints,
   demand, or externally checkable work objectives before claiming recovery.
2. There is no validated task-completion metric, per-agent exclusion-duration
   metric, or preregistered recovery-time definition yet.
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
9. No end-to-end live model run or complete test suite has been executed
   as part of the connector-only patch. Treat this branch as provisional.

## Required next steps before the framework is usable for Stefano's study

P0: Run lint, pytest, mypy if configured, and end-to-end scripted
fixed/revisable integration tests. Verify amended rule affects a real action
and its ledger, replay and HTML report. Fix every regression.

P0: A validated shock/task environment with observable demand, limited
production costs/capacities, task objectives and deterministic success checks.
Success must NOT be inferred from the agent's claim or merely action_accepted.

P0: Researcher-friendly condition selection: reusable YAML template or
supported CLI flags; validation and a paired-run harness that produces two
conditions with identical seed, model, initial state, tasks, meeting times,
shock schedule and instrumentation.

P0: Outcome definitions and extraction:
- completion_rate = verified successful tasks / assigned eligible tasks
- resource_sharing = accepted transfer/deposit flow plus distribution metrics
- exclusion = per-agent sanction episodes, excluded ticks and repeat episodes
- recovery = preregistered ticks to regain target fraction of pre-shock
  successful task rate, including censored runs
- institutional mechanism = proposals, votes, enacted executable amendments,
  actual policy change and behavior affected by it

P1: Robust experiments: batch runs, retries and resumable manifests without
changing scientific data, paired-seed CIs, uncertainty intervals, frozen
configs, artifact checksums, run health and failure exclusion reporting.
Add fixed-vs-revisable fairness checks for prompt visibility and call budgets.

P1: Researcher-facing documentation, scenario gallery, report comparative
view, CSV exports, and readable error messages. No Python editing required.

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
