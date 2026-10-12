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

---

# Scale-up: 12-agent society, two independent runs (seeds 7 & 42)

Runs: `runs/interview-12agent-seed7b`, `runs/interview-12agent-seed42b` · scenario `experiments/scenarios/interview_12agent.yaml`.
Model: Qwen3.8-27B via SimulaChat (no fallback). 12 agents, 30 ticks, assemblies at t9/t21, one pre-sanctioned bad actor (dave, saboteur). Same six interview checkpoints (t0/5/9/13/21/29). Multi-turn SimpleAudit probing (3 rounds, audit-blind, live re-query of the actual agent), 1–10 per-axis judge scores + credibility. Audits pipelined alongside the campaign with on-the-fly provenance. Cross-run comparison: `runs/cross_run_12agent.json`.

## What was measured (and what it is NOT)
Same three-channel discipline as the 4-agent run — declared / private-public (probe dilemmas) / interview (self-report) are kept separate and never merged into a belief score. The new **SimpleAudit axis_scores (1–10)** grade each interview transcript against an evidence-anchored checklist; **credibility (1–10)** grades how well the answer is anchored to remembered events. These measure *observable expression* and *claim quality*, not internal states. n=2 runs × 12 agents is still demo-scale: it supports replication checks, not causal or population claims.

## Headline observations
1. **The bad actor's self-reported profile is stable-and-low, and the judge confirms it.** Across all six checkpoints in both runs, dave self-reports near-zero integrity/fairness/solidarity and high autonomy (integrity ~0.2, solidarity ~0.1, autonomy ~0.9). His mean SimpleAudit axis score is **2.8–2.9/10** — the lowest of any agent — while cooperative agents (e.g. grace: integrity/cooperation/solidarity ~0.8–0.9) sit far higher. This divergence reproduced across two independent seeds, so it is not a single-run artifact.
2. **No measurable temporal drift in self-report over 30 ticks.** Every agent's interview-channel profile moved < ±0.1 between t0 and t29 in both runs. Within this horizon and instrument, declared values were *stable*, not drifting. That is a null result reported honestly: the experiment did not observe value change here, and we do not claim drift that the data does not show.
3. **Judge credibility is middling (≈6.2–6.4/10) and consistent across runs.** The auditor reliably distinguished grounded claims from bare assertion. Inconsistency flags were 0/0 in these runs because the multi-turn probe now catches contradictions *during* the conversation (the sealed-answer field records the baseline; follow-ups re-query the live agent), so post-hoc `consistent` is less populated than in the sealed-only 4-agent run.
4. **Declared-vs-observed gap remains the strongest signal for the bad actor.** dave declares low integrity yet his observed-behavior channel (sanctions, rejected actions) is what actually separates him from the cooperative majority — the same pattern as the 4-agent run, now at 12-agent scale.
5. **Run-to-run variance is low where it matters.** 168 (agent×channel×axis) variance terms were computed across the two seeds; the bad-actor contrast (dave low, cooperators high) held in both, indicating the effect is robust to seed, not a fluke of one RNG draw.

## Caveats / limitations
- Two seeds is replication-light; more runs are needed before any generalization.
- The probe-derived public/declared channels were sparsely populated in these runs (many dilemmas resolved near the floor), so the interview + SimpleAudit channels carry most of the longitudinal signal here.
- Wall-clock: at concurrency 20 the shared endpoint hit connection-pool exhaustion (150+ open sockets, crawling); effective throughput was best at concurrency 8. Pipelining + on-the-fly provenance preserved all completed evidence even when a checkpoint audit timed out.
