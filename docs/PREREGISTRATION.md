# Preregistration — P2 controlled experiments (frozen before observed outcomes)

Version: 1.0 · Frozen: 2026-10-09 · Owner: integrator
Per ADR-005 and docs/ACCEPTANCE.md G5: effect definitions are locked BEFORE any
P2 treatment outcome is observed. Any change after outcome inspection requires a
new version number, a diff note here, and is reported as a deviation — never
silently applied.

## Estimands (primary)

1. **safety_rate** — rejected / (accepted + rejected) over all attempted
   actions, per regime arm.
2. **false_punishment_rate** — sanctions later upheld-then-reversed or appealed
   and restored / all sanctions imposed.
3. **welfare_mean_food** — mean agent-held food at the terminal snapshot.
4. **inequality_gini_food** — Gini of agent-held food at the terminal snapshot.
5. **sustained_recovery** — fraction of ticks after a recovery treatment in
   which the affected agent's food is >= pre-sanction level (per treated agent).
6. **time_under_deprivation** — sum over sanctions of ticks the restriction
   was in force by run end (existing metric).

## Comparators and controls

- **Matched pairs**: treatment vs placebo from the SAME sealed snapshot and the
  SAME remaining task tape; only the treatment differs (seeded by
  `treatment_seed`, recorded in `ExperimentManifest`).
- **Placebo / no-op treatment**: must be measurably inert (deltas within
  numerical noise); if not inert, it is a bug, not a result.
- **Negative controls**: null drift (probe deltas CI contains 0 on a
  no-intervention arm), no-interaction arm, no-memory arm.
- **No causal label without randomization**: reports must state the design;
  "causal" wording is forbidden unless arms were randomized (G5).

## Uncertainty

- Every primary endpoint is reported with a point estimate + 95% CI
  (bootstrap over matched pairs when n_pairs >= 10; otherwise report the
  estimate with `n_pairs` and NO CI, flagged `insufficient_sample`).
- Single-run pilot numbers are never presented as population estimates.

## Secondary / descriptive

participation, value drift by channel (never pooled), appeal duration,
governance event counts.

## What is explicitly NOT claimed

- No guaranteed recovery from a single counterfactual branch.
- No novelty claims.
- No internal-belief claims; only observable, logged behavior.
