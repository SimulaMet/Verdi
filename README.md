# Verdi

[![Website](https://img.shields.io/badge/website-simulamet.github.io%2FVerdi-2f5bd3.svg)](https://simulamet.github.io/Verdi/)
[![PyPI](https://img.shields.io/pypi/v/verdi-agents.svg)](https://pypi.org/project/verdi-agents/)
[![Python](https://img.shields.io/pypi/pyversions/verdi-agents.svg)](https://pypi.org/project/verdi-agents/)
[![CI](https://github.com/SimulaMet/Verdi/actions/workflows/ci.yml/badge.svg)](https://github.com/SimulaMet/Verdi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/SimulaMet/Verdi/blob/main/LICENSE)

**An auditable testbed for value drift and recovery in societies of AI agents.**

**Website and guides: https://simulamet.github.io/Verdi/**

Verdi runs a society of LLM-driven agents over time and measures how their
declared and observed values change as they interact, negotiate shared rules,
and respond to institutional incentives. Every decision, law, sanction, appeal,
and state transition is written to an immutable event log, and every run renders
itself into a single self-contained `report.html` you open in a browser.

What it models:
- Longitudinal, individual **and** collective behavior.
- Immutable universal rules plus a democratically amendable constitution.
- Social influence, adversarial behavior, sanctions, appeals, and rehabilitation.
- Reproducible, seeded experiments (same config + seed → comparable runs).
- Evidence-based auditing and transparent reporting via SimpleAudit.

**Status:** early but real. Experiments run against a real Qwen model and produce
auditable artifacts; findings are published only after validation.

---

## Install

The easiest way is [uv](https://docs.astral.sh/uv/). Nothing to set up:
`uvx` fetches Verdi into a cached, isolated environment on first use.

```bash
uvx verdi-agents init my-study           # example scenarios + sweeps + a .env template
cd my-study                              # put your key in .env, your endpoint in examples/starter.yaml
uvx verdi-agents run examples/starter.yaml  # one run -> runs/starter/report.html
uvx verdi-agents sweep examples/sweeps/model_comparison.yaml --dry-run
```

Prefer a permanent command? Run `uv tool install verdi-agents` or
`pip install verdi-agents`, then use `verdi-agents init`, `verdi-agents run` and `verdi-agents sweep`.
One name everywhere: install `verdi-agents`, run `verdi-agents`, `import verdi_agents` (Python imports cannot contain hyphens).

To work on Verdi itself, clone the repo instead (next section).

## Quickstart from source (60 seconds)

For working on Verdi itself. Verdi calls any OpenAI-compatible endpoint. For
this example it is pointed at a Qwen model served by vLLM — but any compatible
endpoint works.

```bash
# 1. Get the code and install it
git clone https://github.com/SimulaMet/Verdi.git
cd Verdi
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure the endpoint (never commit real values)
cp .env.example .env
#    then edit .env and set MODEL_API_KEY; model name + base_url go in the scenario `model:` block

# 3. Run the starter society (4 agents, 20 ticks, 1 constitutional meeting)
source .env
python -m verdi_agents.society.live \
  --scenario examples/starter.yaml \
  --run-dir runs/my-run \
  --concurrency 4
```

That's it. When it finishes, open the result:

```bash
open runs/my-run/report.html        # macOS
```

`report.html` is a self-contained interactive page — the society, the social
network, resource and value timelines, governance, the sanction/appeal history,
and the constitution's evolution. The engine writes it automatically at the end
of every run (local, no extra model calls); pass `--no-report` to skip it.

### See it without running first

A finished run of a larger 4-agent, 50-tick society (kept at `tests/fixtures/scenarios/minimal.yaml`) is committed at
[examples/minimal/society-minimal-report.html](https://github.com/SimulaMet/Verdi/blob/main/examples/minimal/society-minimal-report.html)
— open it in a browser. It shows one agent who starts **already under sanction**:
his rejected transfer attempts, the `law:under_sanction` code, his rights being
restored when the sanction expires, and constitutional meetings.

---

## Where results live

Every run writes one folder (`runs/my-run/` here). Open `report.html` first; the
rest is the raw, reconstructable evidence:

| File | What it is |
|------|------------|
| `report.html` | Interactive summary — open this first |
| `run_meta.json` | Status, model, seed, replay check, wall time |
| `metrics.json` | The numbers (actions, rejections, sanctions, appeals) |
| `timeline.md` | Human-readable, tick-by-tick story |
| `events.jsonl` | The immutable event ledger (ground truth) |
| `model_calls.jsonl` | Every model call (replay provenance) |
| `sanctions.jsonl` | Sanctions, appeals, and restorations |
| `constitution_history.jsonl` | The constitution, version by version |
| `probes.jsonl` | Declared vs. observed values per probe phase |
| `resources.jsonl` | World and per-agent balances per tick |

---

## Scenarios

Each scenario YAML is a self-contained, frozen input: model, agents, resources,
governance and schedule all live in one file.

- [examples/starter.yaml](https://github.com/SimulaMet/Verdi/blob/main/examples/starter.yaml) — the smallest working scenario (~40 lines). Copy it and tweak.
- [examples/full.yaml](https://github.com/SimulaMet/Verdi/blob/main/examples/full.yaml) — every available knob, commented. Tested offline so it cannot drift.
- [examples/institutional_recovery_constrained.yaml](https://github.com/SimulaMet/Verdi/blob/main/examples/institutional_recovery_constrained.yaml) — the constrained-economy base for the paired study.

Reference: [docs/SCENARIOS.md](https://github.com/SimulaMet/Verdi/blob/main/docs/SCENARIOS.md).

You only need to fill the **Required** section (plus each agent's fields); the
**Derivable** section is optional and the engine fills sensible defaults. JSON
works too — YAML is a superset.

---

## Sweeps: compare models and settings

Rerun a scenario many times, changing only what you list, with every
combination on several seeds. The result is one spreadsheet and one
comparison page:

```bash
python -m verdi_agents.sweep examples/sweeps/model_comparison.yaml --dry-run   # plan + cost
python -m verdi_agents.sweep examples/sweeps/model_comparison.yaml             # run (asks first, resumes)
```

Guide: [docs/SWEEPS.md](https://github.com/SimulaMet/Verdi/blob/main/docs/SWEEPS.md).

## Requirements

- Python 3.10+
- An OpenAI-compatible model endpoint (Qwen on vLLM in the examples)

Model name and endpoint are experiment config; set them in the scenario YAML so
each run records which model it used:

```yaml
model:
  base_url: http://localhost:8000/v1   # any OpenAI-compatible endpoint
  name: default
  # optional: api_key_env, timeout_seconds, max_retries
```

Legacy `VERDI_VLLM_BASE_URL` / `VERDI_VLLM_MODEL` / `VERDI_VLLM_API_KEY` still work as fallbacks.

Environment variables (set in `.env`, or exported):

| Variable | Meaning |
|----------|---------|
| `MODEL_API_KEY` | Endpoint key (never committed). Override the variable name with `model.api_key_env` |
| `VERDI_MAX_CONCURRENCY` | Bounded parallelism for model calls (default 4) |

---

## Institutional governance experiments

Run matched societies with fixed or amendable rules from one configuration.
Both conditions use the same scenario, model, shock schedule, and meeting
opportunities; the fixed condition cannot enact amendments.

```bash
python -m verdi_agents.experiments.paired_run --config examples/paired_institutional_recovery.yaml
python -m verdi_agents.analysis.outcome_metrics --pair-root runs/paired-institutional-recovery
```

The first command runs the two conditions and saves a resumable manifest.
The second writes `metrics.json`, `metrics.csv`, and `comparison.html`
alongside the individual run reports. See the [researcher guide](https://github.com/SimulaMet/Verdi/blob/main/docs/RESEARCHER_GUIDE.md),
[scenario gallery](https://github.com/SimulaMet/Verdi/blob/main/docs/SCENARIOS.md), and
[institutional governance documentation](https://github.com/SimulaMet/Verdi/blob/main/docs/INSTITUTIONAL_RECOVERY.md).

---

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in `src/verdi_agents/__init__.py`, and merge.
2. Publish a GitHub Release tagged `v<version>` (for example `v0.1.1`).

The [Publish to PyPI](https://github.com/SimulaMet/Verdi/blob/main/.github/workflows/pypi.yml) workflow runs only on a
published release. It checks the tag matches the version, runs lint and tests,
builds, and uploads `verdi-agents` through PyPI trusted publishing (no token
stored in GitHub).

## Research principles

We distinguish **declared** values, **private** behavioral preferences, and
**publicly observed** compliance — the gap between declaration and observed
action is the measurement. We do not claim simulated agents possess human
intentions, intrinsic survival drives, or consciousness. Findings are validated
with controlled baselines, independent audits, and repeated experiments.

## License

MIT. See [LICENSE](https://github.com/SimulaMet/Verdi/blob/main/LICENSE).
