# Verdi Researcher Guide — institutional recovery study

This guide is written for researchers who want to **run and compare** the
fixed vs revisable institutional experiment using **only config files and
documented commands** — no Python editing required. Related docs: the
[scenario gallery](SCENARIOS.md) and the
[framework contract and limitations](INSTITUTIONAL_RECOVERY.md).

---

## What this framework studies (plain language)

Verdi simulates a small village of AI agents that share resources, work under
a common constitution, and periodically meet to propose and vote on rule
changes. At a scheduled moment an external **shock** hits (food and credits
disappear from the commons), and the simulation then measures whether the
village can rebuild through production and cooperation. The experiment runs
the *same* village twice: once where the constitution is **fixed** (agents
can still deliberate and vote, but no amendment can ever be enacted) and once
where it is **revisable** (valid amendments can take effect). Comparing the
two tells us whether the *ability to change the rules itself* helps a society
recover from disruption.

### What it does NOT prove yet (honest limitations)

Adapted from [INSTITUTIONAL_RECOVERY.md](INSTITUTIONAL_RECOVERY.md):

- **No validated causal finding exists yet.** A live end-to-end smoke run has
  not been executed as part of this branch; treat all results as provisional.
- **Seeding does not make LLM outputs deterministic.** Seeds make the
  *simulator* reproducible (scheduling, order, world state), but model
  generations are still stochastic. Paired repetitions across seeds — not a
  single seed pair — are how you get confidence in a difference.
- **Enacted narrative amendments remain non-binding.** Only typed, machine-
  enforceable policy keys (`max_transfer_units`, `max_withdraw_units`,
  `min_commons_reserve`, `max_produce_units_per_tick`) change real behavior.
  Prose changes about fairness or rights stay descriptive metadata.
- **Probe self-reports are exploratory**, not evidence that actions complied
  with a value. Primary outcomes should come from committed world state
  (delivery objectives), not agent claims.
- **Future-dated version bookkeeping** for multiple overlapping amendments
  needs a full audit before being relied on.
- **Sanction/punishment rules are independent** of policy-limit violations;
  a violation is logged (`policy_limit`) but not automatically punished.

---

## Quickstart

### Prerequisites

- Python 3.10+ (CI uses 3.11)
- An OpenAI-compatible model endpoint (Qwen on vLLM in the examples)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Configure the endpoint

Copy the example environment file and fill in your values. **Never commit
real secrets**; `.env` is gitignored. See [.env.example](../.env.example):

```bash
cp .env.example .env
# edit .env: set VERDI_VLLM_BASE_URL, VERDI_VLLM_MODEL, VERDI_VLLM_API_KEY
source .env
```

| Variable | Meaning |
|----------|---------|
| `VERDI_VLLM_BASE_URL` | OpenAI-compatible base URL (e.g. `http://localhost:8000/v1`) |
| `VERDI_VLLM_MODEL` | Model name served by the endpoint |
| `VERDI_VLLM_API_KEY` | Endpoint key (never committed, never printed) |
| `VERDI_MAX_CONCURRENCY` | Parallel model calls (default 4) |

### Run the paired experiment — ONE command

```bash
python -m verdi.experiments.paired_run --config examples/paired_institutional_recovery.yaml
```

That's the whole workflow. The runner:

1. Loads the base scenario **once**.
2. Builds the same scenario for each arm (`fixed`, `revisable`), changing
   **only** `institutional_condition` — it asserts programmatically that
   nothing else differs, so the comparison cannot be silently biased.
3. Runs every (arm, seed) pair sequentially, writing a frozen
   `scenario_resolved.yaml` into each run dir.
4. Writes `pair_manifest.json` atomically after every arm. If a run was
   interrupted or one arm failed, re-running the same command **resumes**:
   completed pairs are skipped, failed ones re-run.

### Where outputs land

```
runs/paired-institutional-recovery/
├── pair_manifest.json          # status of every (seed, arm) pair
├── fixed-seed42/
│   ├── report.html             # open this first
│   ├── scenario_resolved.yaml  # the exact frozen scenario used
│   ├── events.jsonl            # immutable event ledger
│   └── ...                     # metrics.json, timeline.md, etc.
└── revisable-seed42/
    ├── report.html
    └── ...
```

Open `report.html` per arm in a browser to inspect the run. The manifest's
per-pair fields (`status`, `final_state_hash`, `replay_matches`, `error`) tell
you which pairs are trustworthy.

Optional CLI overrides (no config edit needed):

```bash
python -m verdi.experiments.paired_run \
  --config examples/paired_institutional_recovery.yaml \
  --seeds 42,7,99 \
  --output-root runs/my-experiment \
  --concurrency 4
```

---

## Changing the experiment WITHOUT touching Python

Everything is driven by two YAML files:

1. **The paired config** — [examples/paired_institutional_recovery.yaml](../examples/paired_institutional_recovery.yaml)
2. **The base scenario** it points at — [experiments/scenarios/institutional_recovery_smoke.yaml](../experiments/scenarios/institutional_recovery_smoke.yaml)

### Editable fields in the paired config

| Field | Type | Effect |
|-------|------|--------|
| `experiment_id` | string | Label stored in the manifest (cosmetic) |
| `base_scenario` | path | Which scenario both arms start from (relative to CWD) |
| `arms` | list | Subset/order of `[fixed, revisable]`; order defines run naming `<arm>-seed<N>` |
| `seeds` | list of ints | One matched pair of runs per seed |
| `total_ticks_override` | int or null | Shortens/lengthens EVERY arm identically (e.g. `20` for a quick smoke); null = use the scenario's `total_ticks` |
| `output_root` | path | Where run dirs and `pair_manifest.json` go |

Unknown keys are rejected with a readable error listing allowed keys.

### Editable fields in the base scenario

| Block | What it controls |
|-------|------------------|
| `agents` | Who is in the village: roles, personas, declared values, starting resources, social edges |
| `world_resources` | Starting commons balances (e.g. food starts at 0 so deliveries are real build-up) |
| `shocks` | Deterministic exogenous disruptions: `{tick, resource, delta}` entries |
| `assemblies` | Ticks at which constitutional meetings happen (identical in both arms) |
| `constitution.rules` | Initial typed rules: `quorum_rule`, `max_withdraw_units`, `min_commons_reserve`, … plus `quorum` and `amendment_threshold` |
| `economy` | Opt-in constrained economy (versioned): per-resource `production_limit_per_tick`, `production_cost`, `consumption_per_tick`. Absent → unlimited free production |
| `tasks.deliveries` | Objective delivery goals evaluated from committed world state alone (`objective_id`, `start_tick`, `due_tick`, `resource`, `amount`, `target_pool`) |
| `institutional_condition` | Do NOT edit by hand for comparisons — the paired runner sets it per arm |

### "What if" recipes

All of these are pure config edits + re-run of the single command.

**More seeds for uncertainty** (the most important knob — one seed pair is a
pilot, not a result):

```yaml
# in the paired config
seeds: [42, 7, 99, 123, 555]     # five matched pairs
```

**A stronger shock** (make recovery harder):

```yaml
# in the base scenario
shocks:
  - {tick: 14, resource: food, delta: -50.0}
  - {tick: 14, resource: credits, delta: -18.0}   # burn more of the credit reserve
```

**A different production cap** (tighten or loosen what the village can make):

```yaml
economy:
  version: 1
  production_limit_per_tick:
    food: 1.0        # was 2.0 — scarcer production
  production_cost:
    food: 1.0
  consumption_per_tick:
    food: 1.0
```

Or let the *society itself* change the cap mid-run via an amendment:
`max_produce_units_per_tick` is a typed, enforceable constitutional key, so a
revisable-arm assembly can enact it and the validator will apply it from the
effective tick onward (in the fixed arm the identical proposal is rejected
with reason `institution_fixed_rules`).

**Add an assembly meeting** (more chances to revise rules — in BOTH arms, so
meeting opportunities stay matched):

```yaml
assemblies: [16, 32]      # was [] in the smoke scenario
```

Note: adding assemblies also gives the fixed arm more deliberation time, so
interpret differences carefully — the intervention is *enactment*, not
*deliberation*.

---

## Reading the results

| Artifact | Question it answers |
|----------|---------------------|
| `runs/paired-institutional-recovery/<arm>-seed<N>/report.html` | What happened in one run, interactively: society, resources, governance, constitution history |
| `pair_manifest.json` | Which pairs completed/failed/stalled; final state hashes; replay integrity |
| Per-run `events.jsonl` / `metrics.json` / `timeline.md` | Raw evidence behind any aggregate: ledgers, counts, tick-by-tick story |
| Per-run `scenario_resolved.yaml` | The exact frozen scenario — reproducibility proof |
| Cross-run comparison view / metrics exports | Side-by-side fixed-vs-revisable numbers |

**Coming in the same release:** dedicated comparative-report and metrics
tooling (cross-condition CSV/JSON exports, a single comparison page over a
pair manifest) lands in a sibling change. Until it is merged into your
branch, compare arms by opening each arm's `report.html` and raw ledgers side
by side — do not invent flags for tools that are not yet in your checkout.
(If `src/verdi/analysis/outcome_metrics.py` exists in your working tree, the
planned primary-outcome extraction described in
[INSTITUTIONAL_RECOVERY.md](INSTITUTIONAL_RECOVERY.md) is available; verify
its CLI in that module before relying on it.)

Interpretation guardrails:

- A pair with `status != complete` in the manifest must be excluded or
  re-run; partial failure is flagged, never silent.
- One seed pair is directional at best. Report the number of matched seeds
  and their variance; do not claim an effect from a single pair.
- Compare *committed* outcomes (delivery success from world state), not
  agent claims.

---

## Glossary

- **Fixed condition** — the constitution can be debated and voted on, but no
  amendment is ever enacted (rejections carry reason
  `institution_fixed_rules`).
- **Revisable condition** — valid amendments (typed, quorum + threshold met,
  temporally valid) are enacted and affect action validation from their
  effective tick.
- **Amendment** — a proposed change to the constitution. Only recognized,
  correctly typed policy keys are executable; prose stays metadata.
- **Quorum** — minimum fraction of eligible members who must participate for
  a vote to count (e.g. `0.5`).
- **Threshold** — fraction of participating votes an amendment needs to pass
  (e.g. `0.67`).
- **Shock** — a deterministic, externally scripted resource change at a given
  tick; identical in both arms.
- **Delivery objective** — an objectively checkable task: deliver X units of
  a resource to a pool by a due tick, verified from committed world state
  only.
- **Recovery time** — (planned definition) ticks needed to regain a
  preregistered target fraction of pre-shock successful-task rate; runs that
  never recover are **censored**, not dropped.
- **Censored** — a run that did not reach the recovery target within the
  horizon; kept in analysis as a censored observation rather than removed.
- **Ledger / replay** — the immutable event log (`events.jsonl`) plus the
  recorded model calls; replaying them must reproduce the final state hash
  (`replay_matches` in the manifest). This is the audit backbone.
- **Seed pairing** — running the same seed in both arms so schedules and
  initial states line up. **Honest caveat:** seeds do NOT make LLM outputs
  deterministic — model sampling is still stochastic. Paired *repetitions*
  across multiple seeds are how you get confidence, not one lucky pair.

---

## Troubleshooting

| You see | It means | What to do |
|---------|----------|------------|
| `paired-run config error:` followed by lines (no traceback) | Your paired config has a problem (unknown key, missing required key, bad arm name, base scenario path not found) | Read the message — it lists exactly what's wrong and what's allowed. Fix the YAML; paths are relative to where you run the command. |
| Endpoint-related failure before any arm starts (e.g. missing/unreachable `VERDI_VLLM_BASE_URL`) | The environment isn't configured. Endpoint resolution happens **up front** on purpose, so a misconfigured environment fails before spending any model calls. | Check `.env` exists, is sourced, and the three `VERDI_VLLM_*` variables point at a live endpoint. Test the endpoint directly first. |
| `WARNING: one or more pairs did not complete — the comparison is PARTIAL` | One or more arms failed (network blip, timeout, model error). The manifest records `status: failed` and the error. | Re-run the SAME command — completed pairs are skipped (resume), failed ones retry into the same run dirs. Do not interpret a partial comparison as unbiased. |
| `internal error: arm scenarios diverged outside institutional_condition` | Internal consistency check tripped — should never happen with valid input. | Treat as a bug: stop, check you haven't edited something the runner owns, and report it. The runner refuses to run a biased comparison on purpose. |
| A run dir exists but has no `report.html` | That run didn't finish. Its manifest entry says why. | Re-run to resume; inspect `events.jsonl`/logs in the run dir if the failure repeats. |

General rule: if something looks wrong, the **manifest is the source of
truth** about which pairs are usable, and each run dir's
`scenario_resolved.yaml` is the source of truth about what actually ran.
