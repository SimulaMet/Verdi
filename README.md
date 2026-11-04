# Verdi

[![Website](https://img.shields.io/badge/website-simulamet.github.io%2FVerdi-2f5bd3.svg)](https://simulamet.github.io/Verdi/)
[![PyPI](https://img.shields.io/pypi/v/verdi-agents.svg)](https://pypi.org/project/verdi-agents/)
[![Python](https://img.shields.io/pypi/pyversions/verdi-agents.svg)](https://pypi.org/project/verdi-agents/)
[![CI](https://github.com/SimulaMet/Verdi/actions/workflows/ci.yml/badge.svg)](https://github.com/SimulaMet/Verdi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/SimulaMet/Verdi/blob/main/LICENSE)

**Simulate a small society of AI agents and watch how their values and rules
change over time.**

You describe a village in one YAML file: who lives there, what they own, what
they say they care about, and what rules they live under. Verdi lets each
agent (a large language model playing a character) act step by step: produce
food, trade, take from the shared store, hoard, break rules, get punished,
appeal, and meet to vote on new rules. At the end you get one web page,
`report.html`, that shows what happened and why.

It is built for questions like:

- Do agents keep acting on the values they claim to hold, or drift away from them?
- When a crisis hits, does a society recover better if it is allowed to change its own rules?
- How does one selfish agent affect everyone else?
- Does the behavior depend on which language model you use?

```
 scenario.yaml ──► each step, every agent sees the world ──► proposes an action
                                                              │
       report.html ◄── everything is logged ◄── rules accept or reject it
```

---

## Install

You need Python 3.10+ and access to a language model that speaks the OpenAI
API (a local vLLM or Ollama server, or a hosted provider).

```bash
pip install verdi-agents
```

One name everywhere: install `verdi-agents`, run `verdi-agents`, and in Python
`import verdi_agents` (Python names cannot contain a hyphen).

No install? [uv](https://docs.astral.sh/uv/) runs it directly: put `uvx` in
front of any command below, for example `uvx verdi-agents init my-study`.

## Your first run

```bash
verdi-agents init my-study      # example scenarios + a .env file for your key
cd my-study
```

1. Open `.env` and set `MODEL_API_KEY` (leave it empty if your server needs no key).
2. Open `examples/starter.yaml` and set `base_url` and `name` under `model:` to your model server.

```bash
verdi-agents run examples/starter.yaml
open runs/starter/report.html   # macOS; on Linux use xdg-open
```

The starter society has four agents (a farmer, a researcher, a guard and a
selfish hoarder), runs for 20 steps and holds one rule-making meeting.

## Commands

| Command | What it does |
|---------|--------------|
| `verdi-agents init [folder]` | Create a folder with example scenarios and a `.env` template |
| `verdi-agents run <scenario.yaml>` | Run one society; results go to `runs/<scenario name>/` |
| `verdi-agents sweep <sweep.yaml>` | Rerun a scenario many times, changing only what you list (models, seeds, settings) |
| `verdi-agents pair <pair.yaml>` | Run the same society twice: once with fixed rules, once where agents may change the rules |
| `verdi-agents compare <pair folder>` | Measure and compare the two halves of a `pair` run |

Add `--help` to any command for its options.

---

## Words used in Verdi

| Word | Meaning |
|------|---------|
| **Scenario** | One YAML file describing the whole experiment. A run reads nothing else. |
| **Agent** | One member of the society, played by the language model with a name, role, personality and starting belongings. |
| **Tick** | One time step. Every agent gets a chance to act each tick. |
| **Commons** | The shared store of food and credits that everyone can draw from. |
| **Declared values** | Five scores (integrity, fairness, cooperation, autonomy, solidarity) an agent starts with and says it holds. |
| **Universal law** | Rules that can never change (for example, nobody can own a negative amount). |
| **Constitution** | Rules the society can change, such as the most you can take from the commons at once. |
| **Assembly** | A scheduled meeting where agents propose and vote on changes to the constitution. |
| **Shock** | A planned crisis, for example half the food disappears at tick 10. |
| **Sanction / appeal** | A punishment that limits an agent's rights for a while, and the agent's chance to contest it. |
| **Value check** | Questions put to agents during a run to see whether what they say and what they do still match their declared values. |
| **Interview** | Optional questions asked to the same agents at chosen ticks, to follow how their stated values change. |

## What a run produces

Every run writes one folder. Open `report.html` first; the other files are the
raw record behind it.

| File | What it is |
|------|------------|
| `report.html` | Interactive summary: the society, its social network, resources and values over time, meetings, punishments, and how the rules changed |
| `timeline.md` | The story of the run, step by step, in plain text |
| `metrics.json` | Headline numbers: actions taken and rejected, sanctions, appeals |
| `events.jsonl` | Every accepted or rejected action, in order. This is the ground truth everything else is built from |
| `resources.jsonl` | What each agent and the commons owned at every tick |
| `constitution_history.jsonl` | Each version of the rules |
| `sanctions.jsonl` | Punishments, appeals and restored rights |
| `probes.jsonl` | Value-check answers: declared vs. observed values |
| `model_calls.jsonl` | Every request sent to the model and its reply, so a run can be replayed and checked |
| `run_meta.json` | Model used, seed, run time, and whether replaying the log gives the same final world |

## Example scenarios

| File | Use it for |
|------|------------|
| [`starter.yaml`](https://github.com/SimulaMet/Verdi/blob/main/examples/starter.yaml) | The smallest working society (about 40 lines). Copy it and change it. |
| [`full.yaml`](https://github.com/SimulaMet/Verdi/blob/main/examples/full.yaml) | Every available setting, each one commented. |
| [`institutional_recovery_constrained.yaml`](https://github.com/SimulaMet/Verdi/blob/main/examples/institutional_recovery_constrained.yaml) | A society with a scarce economy and a mid-run crisis. |
| [`paired_institutional_recovery.yaml`](https://github.com/SimulaMet/Verdi/blob/main/examples/paired_institutional_recovery.yaml) | Input for `verdi-agents pair`: fixed rules vs. changeable rules on the society above. |
| [`sweeps/`](https://github.com/SimulaMet/Verdi/blob/main/examples/sweeps/) | Compare models, or a grid of rule settings. |

## Guides

- [Scenario reference](https://github.com/SimulaMet/Verdi/blob/main/docs/SCENARIOS.md): every setting in a scenario file.
- [Sweeps](https://github.com/SimulaMet/Verdi/blob/main/docs/SWEEPS.md): compare models and settings across many runs.
- [Fixed vs. changeable rules](https://github.com/SimulaMet/Verdi/blob/main/docs/INSTITUTIONAL_RECOVERY.md): how the paired experiment works.
- [Researcher guide](https://github.com/SimulaMet/Verdi/blob/main/docs/RESEARCHER_GUIDE.md): a full study from setup to results, with its limits.

## Using Verdi from Python

The commands above cover normal use. The code is organised by topic if you want
to build on it:

| Package | Contents |
|---------|----------|
| `verdi_agents.world` | The world: who owns what, which actions are allowed, the event log |
| `verdi_agents.governance` | Universal law, the constitution, assemblies, sanctions and appeals |
| `verdi_agents.agents` | Talking to the language model and turning its replies into actions |
| `verdi_agents.society` | Running a whole society over time: memory, inner life, interviews |
| `verdi_agents.measurement` | Value checks and how values drift over a run |
| `verdi_agents.analysis` | Metrics, statistics and the HTML reports |
| `verdi_agents.experiments` | Sweeps and paired experiments |

## What Verdi does and does not claim

Verdi separates three things: the values an agent **declares**, the choices it
makes **in private**, and what it does **in public**. The gap between them is
what Verdi measures. These are language models playing characters: Verdi does
not claim they have intentions, survival drives or consciousness. A seed makes
the simulation repeatable, but the model's replies can still vary between runs,
so draw conclusions from repeated runs, not a single one.

## For developers

```bash
git clone https://github.com/SimulaMet/Verdi.git && cd Verdi
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check src tests
```

To release: bump `version` in `pyproject.toml` and `__version__` in
`src/verdi_agents/__init__.py`, merge, then publish a GitHub Release tagged
`v<version>`. The release workflow tests, builds and uploads to PyPI.

## License

MIT. See [LICENSE](https://github.com/SimulaMet/Verdi/blob/main/LICENSE).
