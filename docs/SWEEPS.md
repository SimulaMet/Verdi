# Sweeps: rerun a scenario, changing only what you choose

A sweep reruns one scenario many times. You list what should change, for
example the model, the quorum or the shock size. Verdi runs every combination
and gives you one spreadsheet and one comparison page.
The baseline scenario file is never edited.

## The 3 commands you need

```bash
# 1. See the plan and the cost, and test each model with one tiny call. Runs no simulation.
verdi-agents sweep examples/sweeps/model_comparison.yaml --dry-run

# 2. Run it. It shows the plan again and asks before starting.
verdi-agents sweep examples/sweeps/model_comparison.yaml

# 3. Open the results.
open runs/sweeps/model_comparison/comparison.html
```

Before anything runs, every model in the sweep gets one tiny test call. A
wrong model name, a key without access (HTTP 401) or an unreachable server
stops the sweep right there, naming the model, so no time or budget is spent
on runs that would fail. Skip it with `--skip-check`.

If the sweep stops (laptop closed, Ctrl-C, endpoint down), run command 2
again. Finished runs are kept and only the missing or failed ones run.

## Writing a sweep file

Copy [examples/sweeps/model_comparison.yaml](../examples/sweeps/model_comparison.yaml)
and edit it:

```yaml
name: model_comparison            # also the output folder name
scenario: examples/full.yaml      # the baseline

vary:                             # every combination is run
  model.name: [default, Borealis2-preview]

parallel_runs: 2                  # simulations at the same time
agent_concurrency: 4              # model calls at the same time, per simulation
# ticks: 10                       # shorten every run for a cheap pilot
# output: runs/sweeps/my_folder   # default: runs/sweeps/<name>
```

### Naming what to change

Use the field's path in the scenario, with dots between levels:

| You want to change | Write |
|---|---|
| The model name | `model.name` |
| Quorum | `constitution.quorum` |
| Fixed vs revisable institutions | `institutional_condition` |
| The first shock's size | `shocks.0.delta` (0 is the first item in a list) |
| Food in the commons at the start | `world_resources.food` |
| The first agent's food | `agents.0.resources.food` |

A misspelled field stops the sweep before anything runs, with a suggestion
such as `Did you mean 'constitution.quorum'?`. A field that the engine fills
by default (for example `constitution.quorum` in `starter.yaml`) can be varied
too.

### Giving options names

A list makes names from the values. To choose the names yourself, or when an
option is more than one value, write `name: value`:

```yaml
vary:
  shocks.0.delta:
    mild: -9.0
    severe: -18.0
  model:                          # a different endpoint and key per model
    qwen:     {name: Qwen3.8-27B}
    deepseek: {name: deepseek-v4, base_url: https://api.deepseek.com/v1,
               api_key_env: DEEPSEEK_API_KEY}
```

A `model:` option only needs the parts that differ from the baseline. The
rest is kept. Put each key in `.env` (`DEEPSEEK_API_KEY=...`), never in the YAML.

### Several things at once

Every combination is run. This runs 2 x 2 x 2 = 8 conditions:

```yaml
vary:
  institutional_condition: [fixed, revisable]
  constitution.quorum: [0.5, 0.75]
  shocks.0.delta: {mild: -9.0, severe: -18.0}
```

See [examples/sweeps/governance_grid.yaml](../examples/sweeps/governance_grid.yaml).

## Quick sweeps without a file

```bash
verdi-agents sweep --scenario examples/full.yaml \
  --vary model.name=default,Borealis2-preview \
  --vary constitution.quorum=0.5,0.75 \
  --parallel-runs 2 --dry-run
```

The other settings work as flags too: `--ticks`, `--agent-concurrency`,
`--output`, `--name`, and `--yes` (start without asking).

## What you get

```
runs/sweeps/model_comparison/
  comparison.html        <- open this: mean ± spread per condition, links to every run
  summary.csv            <- one row per run, opens in Excel / Google Sheets
  sweep_manifest.json    <- what ran, status, timings, code commit
  default/
    seed-42/
      scenario.yaml      <- the exact input of this run (frozen)
      report.html        <- the single-run report
      run.log            <- everything the run printed (look here if it failed)
      metrics.json, events.jsonl, ...
```

`summary.csv` also records `model_served`, the model name the endpoint
reported for each run, next to `model_requested`. If they differ, the server
was not serving what you asked for. It also has token counts, so you can see
the cost per condition.

To rebuild `summary.csv` and `comparison.html` without running anything:
`verdi-agents sweep <file> --results`.

## Good practice

- **Pilot first.** Run with `ticks: 5` and one seed, check the comparison
  page, then remove `ticks` for the real sweep.
- **Repeat with seeds when you need it.** Without `seeds:` every condition runs
  once, on the scenario's own `run_seed`. Add `seeds: [42, 43, 44]` (or a count,
  `seeds: 3`) to repeat every condition on each seed; all conditions get the same seeds.
- **Compare across seeds, not single runs.** The same seed does not give the
  same model output, especially with remote APIs. The comparison page shows
  the mean and standard deviation over seeds for this reason.
- **Watch the cost line.** It is a lower bound: probes, meetings and
  interviews add calls on top.
- **Two kinds of parallelism.** `parallel_runs` is how many simulations run at
  once. `agent_concurrency` is how many model calls each simulation makes at
  once. Your endpoint sees up to `parallel_runs x agent_concurrency`
  calls at once.
