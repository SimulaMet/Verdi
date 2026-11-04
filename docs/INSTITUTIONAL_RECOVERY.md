# Institutional governance experiments

Verdi can compare fixed and amendable constitutional rules in otherwise
matched societies of LLM agents. The experiment tests whether institutional
rule revision changes observable performance after a disruption. Agents
deliberate and vote in both conditions; only enactment is varied.

## Run a paired comparison

Configure an OpenAI-compatible endpoint as described in the
[README](../README.md), then run:

```bash
python -m verdi_agents.experiments.paired_run --config examples/paired_institutional_recovery.yaml
python -m verdi_agents.analysis.outcome_metrics --pair-root runs/paired-institutional-recovery
```

The paired runner copies a common base scenario into fixed and revisable
conditions, matching agent definitions, seeds, shocks, tasks, and meeting times.
It writes the resolved scenario and model-call evidence for each run, plus a
resumable `pair_manifest.json`. The metrics command creates a comparative
`comparison.html` report, `metrics.json`, and `metrics.csv`.

For configuration options and example scenarios, see the
[researcher guide](RESEARCHER_GUIDE.md) and [scenario gallery](SCENARIOS.md).

## Constitutional conditions

Set `institutional_condition: fixed` or
`institutional_condition: revisable` in a single-arm scenario. The paired
runner sets this field automatically, so users should not manually change
it when preparing matched comparisons.

All agents receive the same scheduled meeting opportunities. Valid
revisable-rule amendments take effect at their declared tick after passing
the normal quorum, voting-threshold, immutable-law and type checks.
Fixed-rule meetings still record proposals and votes, but cannot enact them.

The validator recognizes four binding constitutional policy keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `max_transfer_units` | Positive finite number | Maximum size of an individual transfer |
| `max_withdraw_units` | Positive finite number | Maximum withdrawal from common resources per action |
| `max_produce_units_per_tick` | Positive finite number | Production ceiling when constrained economy is enabled |
| `min_commons_reserve` | Resource-to-nonnegative-number map | Minimum common resource balance after a withdrawal |

For example:

```yaml
constitution:
  rules:
    max_withdraw_units: 2
    min_commons_reserve:
      food: 12
```

These restrictions complement immutable resource invariants. Policy
violations appear in the event ledger with a `constitution:<rule>` reason.
An action blocked by a policy is not automatically a punitive sanction.

Other amendment keys can be recorded as governance or narrative rules but
do not execute arbitrary instructions. Only explicitly implemented and
validated policies affect world actions.

## Constrained economy and objectives

The opt-in economy supports production caps and costs, periodic consumption,
and deterministic external shocks. Legacy scenarios without an economy
configuration retain their original semantics.

Delivery objectives are checked against committed world-state changes,
rather than what an agent reports accomplishing. The
`examples/institutional_recovery_constrained.yaml` and `examples/full.yaml`
demonstrate the relevant scenario settings.

## Interpreting the results

The outcome analysis includes verified objective completion, attempted and
committed resource flows, reserve levels, inequality, exclusion episodes,
institutional decisions, and recovery observations. Aggregates include
references to the underlying event ledger where applicable; runs without
adequate evidence should not be interpreted as zeros.

A passed amendment is a governance outcome, not proof of improved recovery.
Meaningful inference needs independent outcome definitions, repeated matched
runs, uncertainty reporting, and examination of unsuccessful or no-change
meetings. A seed controls simulator randomness but does not make live model
generation deterministic.

Keep private goal-development settings and other noninstitutional parameters
matched between conditions. Report missing or failed runs. In particular,
a full live-model evaluation is separate from the offline test suite; do not
treat successful scripted tests as evidence of a real-model causal effect.
