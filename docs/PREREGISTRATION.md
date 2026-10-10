# Preregistration — P2 controlled experiments (frozen before observed outcomes)

Version: 1.2 (v1.0 frozen 2026-10-09 + v1.1 operational addendum 2026-10-10 + v1.2 erratum 2026-10-10) · Owner: integrator
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

---

## v1.1 operational addendum (2026-10-10, post-freeze, pre-G5-close)

This section is a documented, versioned OPERATIONAL interpretation, applied
pre-G5-close. It does **not** edit or replace the frozen v1.0 text above: the
v1.0 estimand definitions stand unchanged as the *ideal* definitions. Where an
ideal definition is not computable from the recorded-tape data model, this
addendum declares the operational definition actually implemented in
`src/verdi/analysis/estimates.py`. The declared formula below is
character-for-character equivalent in meaning to the code. Two estimands are
affected: **#2 false_punishment_rate** and **#5 sustained_recovery**. The
other four estimands are implemented exactly per v1.0.

### #5 sustained_recovery — operational definition (path b)

*Ideal (v1.0, unchanged):* "fraction of ticks after a recovery treatment in
which the affected agent's food is >= pre-sanction level (per treated agent)."

*Reason for operational interpretation:* the ideal definition quantifies over
every lifecycle tick after the treatment. World states are only observed at
the run's snapshot cadence (pilot: ticks 5, 10, 17, 24, 29), not at every
post-treatment tick, so the literal per-tick fraction is **unobservable** from
the recorded tape.

*Operational definition (implemented):* for each treated agent, take the
**snapshot ticks** at or after the restriction's end (the lifecycle tick of a
`rights_restored` event for that sanction, else `effective_tick + duration_ticks`)
at which the agent is present. The agent's **per-agent fraction** is the number
of those snapshot ticks at which the agent's food is >= its pre-sanction level
(the agent's food in the last snapshot at a lifecycle tick < the sanction's
effective tick; 0.0 if no earlier snapshot), divided by the number of those
snapshot ticks (0.0 when there is no post-restriction snapshot evidence). The
estimand value is the **mean of the per-agent fractions** over all treated
agents whose restriction has ended by the terminal snapshot tick (0.0 if none).

The `k`-consecutive-tick boolean (`metrics.sustained_recovery`, frozen
`k = SUSTAINED_RECOVERY_K = 1`) is still computed per agent and retained in the
detail output, but it no longer gates the estimand value.

### #2 false_punishment_rate — operational definition (path b)

*Ideal (v1.0, unchanged):* "sanctions upheld-then-reversed or appealed and
restored / all sanctions imposed."

*Reason for operational interpretation:* the v1.0 schema expresses sanction
reversal **only** via an upheld appeal — the `Appeal.status` enum is
`filed | upheld | dismissed` and `Sanction` carries no `reversed`/`restored`
state distinct from an upheld appeal. Restoration events are tracked separately
(and are already accounted for under `#6 time_under_deprivation`), so the
"upheld-then-reversed" and "appealed-and-restored" legs of the ideal numerator
are not expressible as a distinct state in the v1.1 schema.

*Operational definition (implemented):* the fraction is
**appealed-and-upheld sanctions / all sanctions imposed** — the count of
sanctions having at least one appeal with final status `upheld`, divided by the
total number of sanctions imposed. Sanctions never appealed, or appealed and
dismissed, are not classified as false (the same blind-spot convention already
carried by `metrics.false_sanction_rate`, reported alongside as
`appeal_coverage`). With no sanctions imposed the value is 0.0.

## v1.2 erratum (2026-10-10, post-freeze, post-G5-outcome)

This is a **correction** to the frozen v1.1 addendum, recorded additively. It
**does not edit or replace** the v1.0 or v1.1 text above; the frozen estimand
definitions stand unchanged. It corrects one factual claim made in the v1.1
#2 `false_punishment_rate` justification (the line "restoration events are
tracked separately and are already accounted for under `#6 time_under_
deprivation`"), in light of the G5 paired-arms live run (ADR-013, dev
`a1357e8`) and ADR-013 Addendum A1.3.

**Erratum (NF-1 closure).** The v1.1 claim that restoration is "already
accounted for under `#6 time_under_deprivation`" is **incorrect** under the
v1 data model. The frozen #6 metric (`analysis.metrics.time_under_
deprivation`) counts the **full** duration the restriction was in force and
is **blind to restoration**: `rights_restored` is state-inert under the v1
data model (the canonical state hash is unchanged by a restoration event —
verified in the G5 run, pair_0 treatment idx 51: state hash identical before
== after the restoration, `555c1d45…`). Therefore:

- #6 is a **restoration-blind duration**, not a restoration-adjusted metric.
- Its treatment−placebo delta of **0.0** in the G5 run is **expected by
  construction** (both arms' restrictions end under the same rule), not a
  no-effect finding.
- The frozen #6 metric is **NOT modified** (PREREG immutability). The
  restoration effect is instead observed through the T−P contrast on the
  ledger-derived estimands that DO change with the adjudication outcome
  (deprivation, false_punishment_rate, deprivation_duration), as reported in
  `artifacts/g5/g5_paired_report_pilot_v1.json`.
- ADR-013 Addendum A1.3 is the authoritative correction; this erratum makes
  the same correction explicit in the preregistration document so the frozen
  record is internally consistent.
