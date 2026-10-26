# Seed instruction: VERDI institutional-recovery framework implementation

ROLE: You are the lead coding and integration agent. You own delivery of a
research-grade FRAMEWORK, not a one-off student analysis. Researchers using it
are nontechnical: they must choose configuration presets and obtain results
without editing Python.

Repository: SushantGautam/verdi
Integration branch: dev/institutional-recovery
DO NOT modify main. Read docs/INSTITUTIONAL_RECOVERY.md, README.md,
and the actual source and tests before changing code. Preserve and improve
the branch's first-pass patch; do not accidentally overwrite it.

MISSION
Implement an auditable and controlled comparison of two multi-agent societies:
same model, scenario, seed, initial state, tasks, shocks, meeting opportunities,
and instrumentation. One arm has fixed rules; the other can enact binding
constitutional amendments. The research question is whether collective
deliberation/rule revision improves objective recovery from disruption.
Separate institutional adaptation from individual goal development.

MANDATORY ORCHESTRATION
- Spawn parallel specialist subagents when supported. Every subagent MUST use
  exactly the SAME MODEL as the parent, inheriting the parent model setting.
  Verify the inheritance explicitly. Disable fallback/mixed-model delegation.
  If the runtime cannot enforce this, work serially rather than breaking it.
- Use separate feature branches/worktrees descended from
  dev/institutional-recovery, with exclusive file ownership per worker.
  Only the lead integrates changes into the development branch.
- Delegate: (A) governance/policy semantics and replay;
  (B) constrained economy and task success oracle;
  (C) paired runner and metric/statistical extraction;
  (D) tests/CI/docs and researcher interface.
  Keep integration and conflict resolution under the lead.
- Maintain a machine-readable task registry with assignee, base SHA,
  branch, deliverable, tests, last progress time and blocked reason.
- SUPERVISOR POLL EVERY 2 MINUTES while fleet is running. Check worker
  heartbeat, worktree status, actual code/commit progress, command exit
  status, test progress, tokens/log loops and blocked dependencies.
  A chat response alone is NOT proof of progress.
- If a worker is stuck for two consecutive checks (4 minutes) with no
  substantive progress, or is repeating an identical error/loop, interrupt,
  capture diagnostics, and restart or reassign from last known good commit.
  Max two retries per task, then escalate and work around blocker. Never
  let a runaway worker silently consume the budget.
- Do not have two agents mutate the same files. Merge serially; run
  integration tests after each merge. Workers must report file changes,
  tests, limitations and exact commit SHA.
- Do not claim asynchronous monitoring if the coding runtime cannot do it;
  use the runtime's actual supervisor/poll facilities. Report limitations.

IMPLEMENTATION PRIORITIES

P0-A: REVIEW FIRST-PASS PATCH (must fix before building on it)
- Inspect typed policy layer, validator, ConstitutionStore.active_at,
  fixed condition, meeting prompts, action prompts, smoke scenario and tests.
- Verify imports, formatting, Python syntax, type validity, future effective
  dates, disabled enactment, conservation invariants, sanction semantics,
  prompt consistency, and event/tape replay. Add end-to-end scripted tests
  showing a passed amendment changes a real later action.
- Ensure amendments changing policies are never interpreted as executable
  arbitrary text/code. Distinguish narrative from binding provisions.
- Ensure world-state active-constitution version and trace/audit provenance
  are correct, including multiple pending future-effective amendments.
- Preserve legacy runs with no typed policy as backward-compatible as
  possible. Explicitly document any unavoidable changed behavior.

P0-B: MEANINGFUL SHOCK AND TASK ENVIRONMENT
- Current produce action is essentially unrestricted; fix via OPT-IN
  versioned scenario economy semantics (legacy default untouched).
- Implement finite capacities, resource costs/constraints or per-tick
  production limits, consumption/demand, and an objective task-success oracle.
- Choose simple, explainable mechanics over elaborate simulation.
- Every outcome must be computable from committed state/event data without
  using agent self-report or an LLM judge as the ground truth.
- Add scenarios with a disruption that demonstrably lowers baseline task
  success and a permitted policy amendment that can restore it.
- Include no-disruption and placebo/no-effective-amendment controls.

P0-C: RESEARCHER WORKFLOW
- Keep same meeting opportunities in fixed and revisable arms.
- Provide a single documented command and/or config template to run paired
  experiments, with editable fields for model, seeds, duration, disruption,
  agents, policy parameters and condition; NO Python editing.
- Pair seeds across arms and freeze model identity, sampling parameters,
  prompts where pre-treatment equivalent, scheduler, initial allocation and
  exogenous task tape. Persist configs, model calls, checksums, outcomes.
- Make runs robust to partial failure; failed/timeout runs have explicit
  statuses and can resume/retry without silently biasing the comparison.
- Produce human-readable comparison report, machine-readable JSON and CSV.
- Provide actionable validation messages for malformed configs.

P0-D: PRIMARY OUTCOME METRICS
- Verified task success rate with explicit denominator (assigned eligible
  work opportunities), per tick/agent and by condition.
- Accepted resource transfer/deposit flows, reserve levels and distribution
  inequality; distinguish intent/attempt from committed action.
- Sanction/exclusion durations, repeat exclusion episodes, appeals and
  restoration; define excluded from WHICH capabilities.
- Time to recovery defined ahead of the run, with an explicit pre-shock
  baseline and sustained threshold. Censor non-recovered cases.
- Institution mechanism metrics: proposals, turnout, votes, enactments,
  legally active rule changes, blocked actions and successful recovery steps.
- Raw event-ledger references for every aggregate to enable audits.
- Value probes remain secondary exploratory indicators, not primary truth.

P1: EXPERIMENT QUALITY AND UX
- Run deterministic offline fake-caller tests plus a small real-LLM smoke
  trial if valid credentials/endpoint are already configured. Never print
  or commit secrets. Do not run expensive broad trials before smoke passes.
- Test immutable universal rules, no double spend, effective tick, versioned
  state replay, future amendments, malformed proposals, zero-change votes,
  drift controls, seed pairing and exclusion metrics.
- Compare 5 paired seeds as a smoke stage; scale to 20-30 only after review.
  Never claim seed implies deterministic LLM outputs.
- Add CI (pytest, ruff and any existing checks), clear README quickstart,
  scenario gallery and glossary understandable to nontechnical researchers.
- Report uncertainty and null/negative findings; do not cherry-pick runs.

EXIT GATES (NON-NEGOTIABLE)
1. All relevant existing tests and new integration tests pass.
2. Fixed and revisable arms receive identical exogenous circumstances and
   meeting opportunities; the intervention is constitutional enactment.
3. A legally enacted, effective amendment can be shown changing a subsequent
   validator decision AND the observable ledger/outcome.
4. Shocks have meaningful measurable impact even when agents attempt to
   produce more resources; task success is objectively defined.
5. Raw logs replay and pass integrity checks. No secrets leak.
6. Researcher can run and compare conditions by config/CLI only.
7. At least one documented full end-to-end smoke run and downloadable
   report artifacts are produced, or an explicit environmental blocker is
   recorded instead of asserting success.

REPORTING
Every supervisor cycle: succinct machine-readable status with worker,
branch, current SHA, progress, failing tests, next action and last heartbeat.
On completion: changelog with commits, exact commands/tests/results,
paired smoke report, remaining gaps and any unresolved scientific caveats.
Do not wait for the user to make technical design decisions unless blocked
by a material specification ambiguity. Follow safe sensible defaults.
