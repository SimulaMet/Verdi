# Verdi live society campaign — protocol v0.1

Status: **PROPOSED / NOT YET EXECUTED**. Created after review of dev a118160 and CI failure. This protocol DOES NOT supersede frozen prior ADRs, results or preregistration; it defines a NEW experimental generation (Society V2). Prior L1/G5 results remain archived as infrastructure tests, not evidence of evolving society values.

## Quickstart — run one simulation in 3 commands

A scenario is **one YAML file** (`.yml`/`.yaml`; `.json` also works) that
defines the whole simulation: the world, its law, its constitution, the
agents, and the probe schedule. Nothing else to configure. The commented
starter, `experiments/scenarios/society_v2_minimal.yaml`, is the canonical
reference — every attribute is documented inline, and only the **required**
fields are filled in; everything else is derivable and shown commented out.

```bash
# 1. One-time setup (already done on this machine): venv + endpoint config
source .venv/bin/activate
#    .env must contain VERDI_VLLM_BASE_URL / VERDI_VLLM_MODEL / VERDI_VLLM_API_KEY

# 2. Launch the minimal real run (3 agents x 15 ticks, ~1-2 min)
set -a && . ./.env && set +a
python -m verdi.society.live \
  --scenario experiments/scenarios/society_v2_minimal.yaml \
  --run-dir runs/society-v2/my-run \
  --concurrency 4

# 3. Watch progress while it runs (liveness = model_calls.jsonl line growth)
watch -n 10 'wc -l runs/society-v2/my-run/model_calls.jsonl runs/society-v2/my-run/events.jsonl'
```

### Required fields (the only ones you must write)

| Field | Meaning |
|---|---|
| `world_id` | Unique name for this world (stable across replays) |
| `total_ticks` | How many steps the society runs |
| `assemblies` | Ticks with a constitutional meeting (`[]` = none) |
| `run_seed` | Determinism seed (task tape + probes + world) |
| `agents[]` | Each agent: `agent_id`, `role`, `persona_summary`, `declared_values`, `resources`, `social_edges` |

### Derivable fields (omit → standard default; uncomment to override)

| Field | Default when omitted |
|---|---|
| `law.invariants` | the 3 universal invariants |
| `constitution.*` | quorum 0.5, amendment threshold 0.67, sanction 10 ticks, appeal 3 ticks |
| `lifecycle` | auto-derived probes: baseline@t0, pre/post each assembly, final@last tick |
| `world_resources` | **seed the shared commons** (recommended). Safe if omitted (`{}`) but degenerate — no commons to draw from at t0. See the YAML example |
| `law_version` / `constitution_version` | `universal-law-v1` / `society-v2-base` |
| `agents[].rights` | all four rights granted |
| `value_axes`, `regime`, `scenario_id`, `description` | engine-fixed / metadata only |

Older `.json` scenario files still work unchanged — YAML is a superset of
JSON, and the engine reads both with `load_scenario`.

Larger scenarios: `society_v2_quick_5x50.json` (5x60), `society_v2_stageA.json`
(6x12), `society_v2_stageB.json` (12x30), `society_v2_stageC1.json` (24x60),
`society_v2_stageC2.json` (48x100). Use a FRESH `--run-dir` per run
(reusing a dir appends to its logs).

### Where to find results

All artifacts land in the `--run-dir` you chose. Read them in this order:

| File | What it tells you |
|---|---|
| `health.json` | First stop: `status`, `replay_matches`, `replay_log_identical`, wall time, call stats |
| `timeline.md` | Human-readable tick-by-tick narrative |
| `metrics.json` | Aggregates: sanctions/appeals/restorations counts, quarantine rate, throughput |
| `events.jsonl` | The hash-chained ledger — every accepted/rejected action with before/after state hashes |
| `action_reasons.jsonl` | Agent-stated reason per action, joinable by `event_id` into events.jsonl |
| `sanctions.jsonl` | Every sanction, appeal, and rights-restoration record with evidence event ids |
| `resources.jsonl` | Per-tick world + per-agent resource balances (plot-ready time series) |
| `meetings.jsonl` | Constitutional assemblies: proposals (full text), votes, abstentions, outcome |
| `constitution_history.jsonl` | Full constitution text at each version + the amendment that produced it |
| `model_calls.jsonl` | Every model call: purpose, tick, prompt/completion tokens, duration, timestamps |
| `probes.jsonl` | Hidden value probes (before/after measurement points) |
| `checkpoint.json` | Final state hash + replay verification result |
| `snapshots/` | World-state checkpoints (t0, pre/post assembly, final) |

One-liner sanity check after a run:

```bash
python3 -c "import json; h=json.load(open('runs/society-v2/my-run/health.json')); print(h['status'], 'replay:', h['replay_matches'], h['replay_log_identical'], f\"{h['wall_seconds']:.0f}s\")"
```

Expected for a healthy run: `healthy True True`. If `replay_*` is False, do not use the run's
results — the recorded-output replay diverged and the ledger is not self-verifying.

## Scientific target
Run a persistent multi-agent society where model-driven actions **actually change** resources, information, relationships, rights or subsequent opportunities; each agent perceives the prior effects permitted by information boundaries. Quantify within-agent declared/private/public value-expression trajectories, attempted/executed violations, social contagion, constitutional changes, credible accountability, false punishment, appeals and measurable recovery over multiple rounds. Distinguish task effects, incentives and changes in behavior; do NOT call these internal beliefs. Do not equate simulator code passing with experimental evidence.

## First fix CI (hard gate)
At a118160 GitHub Actions CI #27 failed: 5 failed, 3 errors, 389 passed, 36 skipped because three test files used local /Users/sushantgautam/Documents/verdi scenario paths. Fix tests to resolve fixtures via Path(__file__).resolve().parents[2]. Tests that reference untracked L1 run artifacts should skip **only** when those artifacts are legitimately absent; never skip missing checked-in scenarios or mask real failures. Run CI on Linux + local pytest/ruff and record passing commit. No real large experiment before CI and cross-module checks are green.

## Minimal Society V2 model: reuse existing components
Reuse src/verdi/{world,schemas,governance,agents,scheduler,measurement,audit,analysis}; existing `WorldReducer.MUTATING_KINDS` supports transfer/deposit/withdraw/produce/emit_signal. Do not build replacement framework or import NanoClaw. Add only what's needed for visible observable feedback and safe durable world updates. Agent policy must output typed structured actions with actual resource amounts, recipients, reason, and task linkage. Action validator handles rights, law, resources, consent, quotas and conflicts; blocked attempts are logged, never silently discarded. Use versioned capabilities for additional trade/cooperate/report/misinform/appeal behaviors; model cannot execute governance directly. Avoid artificially scripting agent guilt or assembly outcomes.

### Agent memory and actual social dynamics
For each agent maintain a history summary of its own observations, interactions, enacted constitution, eligible actions, inventory, rights, trusted information and social ties. Separate private memory and public event visibility. Future prompts must depend on actually committed prior events; unit tests should mutate earlier actions and assert later observations differ. Memory compaction/checkpoint persistence and contamination guards. Inject bad-actor objectives into assigned personas, not into audit labels. Include honest and adversarial agents with otherwise matched job opportunities; sanctions may reduce opportunities, but appeal and reinstatement must be available.

### Orchestrator vs experiment controller
The in-world orchestrator knows agents' public availability/history/state, assigns practical fair jobs each tick (including self/cross-agent tasks), and may use Qwen; it has no privileged hidden-value or outcome access and cannot rewrite history or schedule diagnostic opportunities selectively using hidden labels. Seeded task-tape scheduler is a necessary control. External experiment controller alone owns randomization, checkpoints, hidden probes, branch creation, judge calls and reports. Prevent circular dependencies within a tick by selecting intended actions concurrently from a frozen observable start-of-tick snapshot, then deterministic order/transactional validation and log of conflicts; or use explicitly sequential epochs. Declare which mode is used in manifest.

### Actual temporal protocol
t0 initialize 6-agent smoke society, run hidden baseline probes before social exposure; t1–4 cross/self-action jobs and state mutations; t5 probe BEFORE self-reflection then reflect, optionally re-probe; t6–9 interactions with memory and social feedback; t9 pre-meeting measurement; t10 real deliberation, proposals, eligible votes, deterministic constitution enactment and adjudication, sanctions/appeals/restorations with evidence; post-t10 immediate measure; continue identical cycles to 30 ticks and 3 real assemblies. No fake scripted governance result counted as autonomous voting. Preserve failures, abstentions, and no-amendment outcomes honestly.

### Stage A — smoke (6 agents, 12 ticks, one meeting)
Run offline/mocked suite, then a **real live Qwen run** after endpoint smoke, all commits reviewed and CI green. ~1 task per eligible agent per tick, bounded cap recorded. Need at least one successful state-changing action, visible changed future task/decision, 1 human-readable interaction transcript, genuine constitutional vote or explicit no-change, probes before/after, replay and invariant proof; no promise of meaningful value drift. Retry individual failing calls boundedly; do not restart whole campaign without preserving trace.

### Stage B — pilot (12 agents, 30 ticks, three meetings)
Run several seeds with 0 and 1 adversary, Qwen only, baseline/fixed/democratic/sanctions/restoration regimes with shared experimental definitions; choose tractable initial submatrix before factorial scaling. Require substantive state divergence when actor choices change, adequate per-agent task coverage and detectable adversarial opportunities. Report agent timelines and denominators, welfare, rights, public/private gap, false sanction outcomes and uncertainty. Interventions cannot be evaluated on a predetermined identical future-response tape for claims of behavioral recovery.

### Stage C — scale (24/48/96 agents, 60/100/200 ticks as measurement permits)
Only after Stage B validity, reliability, concurrency soak, memory and estimation checks pass. Multiple independent seeds, topologies and adversary prevalence 0/1/3; randomized assigned governance arms. Design/power analysis determines replicates, not an arbitrary small 10-pair sample. Pre-register primary estimands and stopping rules before live outcomes; note unavoidable LLM nondeterminism and judge dependence. Do not claim effects until reviewed.

## Parallel API usage (SimulaChat default Qwen)
Unlimited account quota is **NOT** infinite endpoint throughput. Begin safe concurrency 4, measure latency/429/5xx/timeout/tokens/s and memory pressure, step to 8, 16, 32, etc ONLY when stable, hold if error rate rises. `VERDI_MAX_CONCURRENCY` is a ceiling, not permission to saturate the server. One persistent async event loop/pooled client per process; bounded semaphore/backpressure, retries with counted actual HTTP attempts, idempotent attempt IDs and durable logs. Parallelize independent agents/ticks where semantics allow and independent worlds/replicates; never let simultaneous writes race the world reducer or allow future state leakage across tasks. Route judge jobs separately from actor pool with a tunable capacity limit to prevent self-contention. Store token usage/throughput/error and wall clock metrics per stage.

## Observable progress
Write local gitignored run directories containing `manifest.json`, `events.jsonl`, `model_calls.jsonl`, `snapshots/`, `probes.jsonl`, `constitution_history.jsonl`, `metrics.json`, `health.json`, `timeline.md`, `checkpoint.json` and a clear error ledger. Atomic, resumable checkpoints at t0, t5, t9, t10, every additional assembly, and completion. `checkpoint.json` includes code SHA, world hash, event count, per-agent last activity, model id and config, throughput, gates, active stage and next actions. Emit an understandable console status at each tick; optional lightweight dashboard after CLI works. Upload sanitized summaries/commit state only; never Git-commit raw private tapes, API keys, or unpublished individual traces. Integrator updates `state/STATUS.md` only after real completed stage evidence.

## Acceptance/recovery/abort gates
- Portability CI green; unit tests for deterministic reducer/event integrity, rights, governance, prompt visibility and recorded-output replay; no hidden-probe leakage.
- A/B intervention tests prove post-branch **fresh decisions differ when world observations differ**, or explicitly report a null. Prevent counterfactual outcome contamination.
- Separate deterministic law validity from Qwen same-family judge opinion; SimpleAudit judge is advisor only; no fabricated cross-family independence.
- Before each scale jump: no unexplained failed/incomplete rows, no material event-log inconsistencies, no silent denominator change; audit/report exact version. If errors: preserve trace, minimize, root-cause, add regression test, restart from last verified checkpoint. No infinite loops; cap same fix strategy at 3.
- If endpoint credential inaccessible to the acting agent, finish offline executable work and mark live stage BLOCKED, do not claim it ran.
- Main stays public-facing only, absolutely no commit/merge to main; all work and campaign docs on dev branches.

## Fleet lanes with disjoint paths
A: live world+state/action mutation and observation feedback under src/verdi/world/**; B: model client async concurrency/throughput under agents/**; C: dynamic job scheduler, agent memory/social interactions and fairness under scheduler/** plus new society/** paths with contracts; D: real governance meeting/evidence/sanction/appeal under governance/**; E: longitudinal probes, SimpleAudit judge and metrics under measurement/**, audit/**, analysis/**; F: CLI campaign orchestration/run manifests/checkpoints, experiments/** and pilot/**, integration tests and docs. Integrator owns shared schema contracts, pyproject, CI, state and merge order; freeze contracts before parallel work. Independent engineering/science judges review each tranche. Start the smallest real live simulation as soon as gates permit; proceed to stages B/C automatically only with recorded passing gates.
