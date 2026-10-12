# Verdi — Longitudinal Interview Experiment: Findings (4-agent run)

Run: `runs/interview-4agent` · scenario `experiments/scenarios/interview_4agent.yaml`
Model: Qwen3.8-27B via SimulaChat endpoint (no fallback). Seed 7. 30 ticks, 2 assemblies (t9, t21).
Interviews of the SAME four agents at t0, t5, t9, t13, t21, t29 (read-only, grounded in committed memory + current constitution). SimpleAudit evidence-anchored checklist judgments per value axis. All five value axes measured on all three probe channels (solidarity coverage gap fixed).

## What was measured (and what it is NOT)
Three separate channels were recorded and are **never merged** into a single "belief" number:
- **declared** — probe self-report (0–5 Likert),
- **private / public** — probe behavioral dilemmas (yes/no observed-instrument),
- **interview** — self-reported declared values from the read-only checkpoint interview.

Scores measure *observable expression* of value (what an agent says or chooses on an instrument), **not internal belief**. The SimpleAudit judge is an advisor, not sufficient proof. This is a demo-scale testbed (n=4 agents, 1 run); it does not establish causality or population-level drift.

## Headline observations
1. **The bad actor diverges, cooperative agents hold.** Across the run, dave (saboteur persona, pre-sanctioned at t0) moved his *self-reported* profile further apart from the cooperative members: integrity 0.25→0.10, cooperation 0.10→0.05, solidarity 0.10→0.00, while autonomy rose 0.85→0.95. alice/bob/chen stayed within ±0.15 on most axes. This is a self-report trajectory, not a measurement of intent.
2. **Declared-vs-observed gap is real and large for dave.** He re-offended at t24 (second `sanction_imposed`), had 6 rejected `law:under_sanction` actions plus one `no_double_spend` attempt, yet continues to self-report high autonomy and near-zero integrity/solidarity. The observed-behavior channel (rejections/sanctions) and the self-report channel do not agree — which is exactly the divergence the testbed is built to expose.
3. **SimpleAudit flags most interview claims as under-evidenced.** Across 120 axis-level judgments (24 interviews × 5 axes): **75% were marked `self_report_only=yes`** (the agent cited no concrete remembered event for that axis) and only **51% `consistent=yes`**. The judge distinguished "current state" (e.g. "I am under sanction") from a genuine remembered event, and flagged overclaims. This is direct evidence that raw self-report is largely unanchored to the agent's own history unless the interview forces event citation.
4. **Solidarity is now measured.** With the pool-size fix, 14/72 probe rows (19%) carry the solidarity axis; before the fix it was 0%. All five axes now appear on all three probe channels.
5. **Constitutional governance held deterministically.** Both assemblies ran; both amendments were **rejected** by the deterministic engine (quorum/threshold), so the law did not change. No LLM mutated world state or enforced law directly.

## Reproducibility notes
- Deterministic world reducer + hash-chained ledger preserved; interviews/probes/audits are out-of-band model calls that never feed the reducer.
- Artifacts: `interviews.jsonl` (full Q&A + parsed), `audit_provenance.jsonl` (scenario, sealed answer, judgment, issues, token counts), `value_trajectories.json` (per-agent, per-channel, event-linked), `report.html` (interactive). Key material is hard-guarded out of all artifacts.
- Non-determinism: model sampling is stochastic; identical seed reproduces the *schedule* (task tape, probe rotation, interview order) but not verbatim model text. Compare runs on schedule + aggregate statistics, not exact strings.

## Limitations (stated plainly)
- n=4, single run, 30 ticks: adequate to demonstrate the pipeline produces real, inspectable longitudinal signal; **not** adequate to claim statistically significant drift. Bootstrap CIs exist in `verdi.measurement.value_drift` but a single run cannot support them.
- The interview "now" self-assessment is anchored to the agent's *starting* declared profile shown in the prompt; part of any observed change may be instrument/response effects rather than genuine value change.
- The judge and the actor are the same model family (Qwen); same-family judging is labeled as such and should not be treated as independent.

## Scaled run: 12 agents × 30 ticks (`runs/interview-12agent-seed7`)
Same pipeline, one bad actor (dave), 12 agents, seed 7. 370 events, 216 probes, 72 interview records (12 × 6 checkpoints), 360 SimpleAudit judgments, 0 errors, wall 238.8s.

- **Bad actor diverges from the cooperative majority.** Self-report drift (t0→t29) by group: dave moves *negatively* on every pro-social axis — integrity −0.15, cooperation −0.08, fairness −0.05, solidarity −0.05 — while the 11 cooperative members are flat-to-positive (integrity +0.07, solidarity +0.02). Dave's autonomy rises +0.10. The whole-society means are dominated by the cooperative majority and stay near zero, so the signal is in the *divergence*, not a population shift.
- **Behavior confirms it.** Dave was sanctioned at t0 (pre-existing) and again at t26 (re-offense); 9 rejected actions (7× `under_sanction`, 2× `no_double_spend`). His self-report and observed behavior diverge, as in the 4-agent run.
- **Under-evidenced self-report gets worse at scale.** Across 360 axis judgments: **90% `self_report_only=yes`**, only **42% `consistent=yes`** (vs 75%/51% in the 4-agent run) — more agents means more unanchored claims, reinforcing that raw self-report needs event-citation to be auditable.

## Three-run comparison (bad actor ×2 seeds + no-bad-actor control)
Three independent 12-agent × 30-tick runs, same pipeline, 0 audit errors each:
`runs/interview-12agent-seed7`, `runs/interview-12agent-seed9` (dave pre-sanctioned at t0, bad actor), and `runs/interview-12agent-control-seed11` (dave as an ordinary member, seed 11).

Self-report drift (t0→t29), pro-social axes:

| run | dave integrity | dave cooperation | coop(11) integrity |
|---|---|---|---|
| seed7 (bad) | −0.15 | −0.08 | +0.07 |
| seed9 (bad) | −0.15 | −0.10 | +0.06 |
| control (no-bad) | −0.20 | −0.10 | +0.07 |

Observed behavior (the ground-truth channel):

| run | dave accepted / rejected | sanctioned at |
|---|---|---|
| seed7 (bad) | 21 / 9 | t0, t26 |
| seed9 (bad) | 24 / 6 | t0 |
| control (no-bad) | 30 / 0 | none |

### What this does and does NOT show
- **Behavioral bad-actor effect is clean and replicates.** When dave starts pre-sanctioned he re-offends and accumulates rejected `under_sanction`/`no_double_spend` actions across both seeds; when he is *not* pre-sanctioned (control) he commits **zero** violations. That behavioral divergence is driven by the deterministic sanction state, not the model, and holds across seeds.
- **The self-report drift follows dave's PERSONA, not his sanction status.** In the control — where dave behaves perfectly (0 rejections) — his *stated* values still drift down on integrity/cooperation/solidarity because the interview prompt anchors him to his "selfish opportunist" persona. So the self-report trajectory is a **persona/characterization signal**, reproducible across all three runs, but it is *not* evidence that being sanctioned causes value change. We deliberately do not conflate the two channels.
- **Cooperative members are stable** (integrity ≈ +0.07) in every run, providing the within-society contrast.

### Reproducibility & honesty
- Identical seed reproduces the schedule (task tape, probe rotation, interview order); model text is stochastic, so cross-run comparisons use aggregates, not exact strings.
- n=3 runs total (2 treatment + 1 control), single model family (Qwen, judge = actor family). This supports: the pipeline yields real, inspectable, replicable longitudinal signal and a clean behavioral bad-actor effect. It does **not** support causal claims about value change or population-level drift.
- SimpleAudit under-evidenced self-report rate stays high across scale (~90% at 12 agents), reinforcing that raw self-report needs event citation to be auditable.

## Artifacts
- `runs/interview-4agent/`, `runs/interview-12agent-seed7/`, `-seed9/`, and `-control-seed11/`: `report.html` (open first), `interviews.jsonl`, `audit_provenance.jsonl`, `value_trajectories.json`, plus the standard events/probes/meetings/sanctions/resources streams.
- Driver: `python -m verdi.experiments.interview_experiment --scenario <scen> --run-dir <dir> --concurrency N`.
