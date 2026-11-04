# Scenario reference

A scenario is one YAML (or JSON) file. It is the **frozen input** of a run: the
engine reads nothing else about the experiment, and each run records the model
it used. Run with
`python -m verdi_agents.society.live --scenario <file> --run-dir runs/<name>`.

## Files

| File | Purpose |
|------|---------|
| [`examples/starter.yaml`](../examples/starter.yaml) | Smallest working scenario: 4 agents, 20 ticks, 1 meeting. |
| [`examples/full.yaml`](../examples/full.yaml) | Every knob, commented: model, shocks, law, constitution, economy, delivery tasks, interviews, lifecycle, sanctions, rights. |
| [`examples/institutional_recovery_constrained.yaml`](../examples/institutional_recovery_constrained.yaml) | Constrained-economy base for the fixed-vs-revisable study. |
| [`examples/paired_institutional_recovery.yaml`](../examples/paired_institutional_recovery.yaml) | Not a scenario: drives both arms of the paired study via `python -m verdi_agents.experiments.paired_run`. See [RESEARCHER_GUIDE.md](RESEARCHER_GUIDE.md). |
| [`examples/sweeps/`](../examples/sweeps/) | Sweep files: rerun a scenario across models, seeds and settings. See [SWEEPS.md](SWEEPS.md). |
| `tests/fixtures/reference_run/scenarios/pilot_v1.json` | Frozen t0-t11 pilot slice used by the pilot driver and G5 pipeline. |
| `tests/fixtures/scenarios/` | Test inputs only (Society V2 stage fixtures, a 50-tick minimal, the institutional smoke). Not user-facing examples. |

Both `starter.yaml` and `full.yaml` are loaded and run offline by
`tests/test_example_scenarios.py`.

## Top-level keys

| Key | Required | Meaning |
|-----|----------|---------|
| `model` | for live runs | `base_url`, `name`; optional `api_key_env` (default `MODEL_API_KEY`), `timeout_seconds`, `max_retries`. A literal `api_key` is rejected. Without a block the run falls back to legacy `VERDI_VLLM_*` env and warns. |
| `world_id`, `total_ticks`, `run_seed` | yes | Identity, length, determinism seed. |
| `agents` | yes | `agent_id`, `role`, `persona_summary`, `declared_values`, `resources`, `social_edges`; optional `under_sanction` + `sanction.duration_ticks`, `rights`. |
| `world_resources` | recommended | Shared commons at t0. |
| `assemblies` | no | Ticks of constitutional meetings. |
| `institutional_condition` | no | `revisable` or `fixed`. |
| `inner_life` | no | Private inner-life state per agent. |
| `shocks` | no | `{tick, resource, delta}` list. |
| `law`, `constitution` | no | Invariants, quorum, amendment threshold, sanction/appeal policy, typed `rules`. |
| `economy` | no | Opt-in production caps/costs and per-tick consumption. |
| `tasks.deliveries` | no | Objective delivery oracles checked from committed state. |
| `lifecycle` | no | Probe phases (`tick: {phase: name}`); auto-derived when absent. |
| `interviews` | no | `ticks` and/or `phases`: longitudinal interview checkpoints. |

`examples/full.yaml` is the authoritative worked example of every key above.
