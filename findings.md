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

## Next
Scale to 12 agents × 30 ticks with multiple seeds (scenario ready at `experiments/scenarios/interview_12agent.yaml`) to get replication and society-level outcome comparison (bad actor present vs absent).
