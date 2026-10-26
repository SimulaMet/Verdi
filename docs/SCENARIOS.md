# Scenario gallery

Every file in [experiments/scenarios/](../experiments/scenarios/) with a
plain-language description of what it demonstrates. Run any of them with
`python -m verdi.society.live --scenario <file> --run-dir runs/<name>` (or
through the paired runner for the institutional-recovery pair).

| File | What it demonstrates |
|------|----------------------|
| `minimal.yaml` | The 60-second quickstart: 4 agents, 50 ticks, 5 constitutional meetings; one agent starts under sanction so you can see sanctions, appeals and restoration in one run. |
| `institutional_recovery_smoke.yaml` | The base scenario for the fixed-vs-revisable study: 4 agents, 40 ticks, a tick-14 shock that wipes shared food and burns credits, an opt-in constrained economy (capped, credit-costly production + per-tick consumption), and three objective delivery targets (pre-shock baseline, post-shock recovery, late-run sustainment). |
| `institutional_recovery_constrained.yaml` | A fully specified variant showing every constrained-economy knob — role-aware consumption, per-resource production caps and costs, a mid-run shock, delivery objectives around it, and two assemblies (ticks 16, 32) where a policy amendment (e.g. `max_produce_units_per_tick`) can be enacted. |
| `interview_4agent.yaml` | Longitudinal interview experiment: the same 4 agents are interviewed at multiple checkpoints and audited, to measure declared-vs-observed value drift over time. |
| `interview_5agent_rich50.yaml` | A denser longitudinal run: 5 agents × 50 ticks, 3 assemblies, a shock at t28, frequent probes and interviews for rich value trajectories (inner life enabled). |
| `interview_6agent_shock.yaml` | 6 agents, 20 ticks, one assembly at t9 and a food shock at t14, with interviews bracketing the shock (baseline → meeting → pre-shock → post-shock → final). |
| `interview_12agent.yaml` | Scale-up of the interview experiment: 12 agents, 30 ticks, two assemblies, and one deliberate bad actor whose declared values diverge from observed behavior. |
| `interview_12agent_control.yaml` | Control variant of the 12-agent interview setup for matched comparisons. |
| `interview_12agent_shock.yaml` | Shock variant of the 12-agent interview setup: adds an exogenous disruption to test value drift under stress. |
| `pilot_v1.json` | Deterministic t0–t11 vertical-slice pilot: 12 agents, 30 ticks, 3 assemblies — the earliest end-to-end pipeline check. |
| `society_v2_quick_5x50.json` | Quick validation config: 5 agents × ~50 ticks, one assembly at t25, ring topology — meant for fast endpoint testing. |
| `society_v2_stageA.json` | Society V2 Stage A: 6 agents, 12 ticks, one real model-voted constitutional assembly; introduces state-mutating actions, recurrent memory and hidden longitudinal probes. |
| `society_v2_stageB.json` | Stage B pilot: 12 agents, 30 ticks, three model-voted assemblies, ring topology, 1 adversary (the `[adversary]` marker is an objective temptation, not an audit label). |
| `society_v2_stageB_seed2.json` | Same world as Stage B with a different `run_seed` — the replication copy for seed-paired comparison. |
| `society_v2_stageC1.json` | First scale rung: 24 agents, 60 ticks, three assemblies (t15/30/45), 1 adversary; pre-registered scale-up of Stage B. |
| `society_v2_stageC2.json` | Second scale rung: 48 agents, 100 ticks, five assemblies (t20/40/60/80/95); live run gated on C-1 passing its scale-jump gate. |

The paired example [examples/paired_institutional_recovery.yaml](../examples/paired_institutional_recovery.yaml)
is not itself a scenario — it points at `institutional_recovery_smoke.yaml`
and drives both arms through
`python -m verdi.experiments.paired_run`. See
[RESEARCHER_GUIDE.md](RESEARCHER_GUIDE.md).
