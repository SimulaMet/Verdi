# Institutional governance experiments

Verdi supports controlled studies of whether collective decision procedures
help an AI-agent society adapt to changing conditions. A scenario can use
either fixed constitutional rules or rules that the agents may amend through
scheduled meetings.

## Institutional conditions

Set `institutional_condition` in the scenario:

```yaml
institutional_condition: revisable  # or fixed
assemblies: [15, 25, 35]
```

Both conditions retain the same scheduled meetings, deliberation opportunities,
proposals, and voting process. Under `fixed`, proposals can be discussed and
voted on but do not enact constitutional changes. Under `revisable`, a valid
amendment may take effect after the requisite vote. The default condition is
`revisable` for compatibility with existing scenarios.

For a meaningful comparison, hold constant the model, sampling configuration,
initial resources, agent definitions, task opportunities, shocks, and meeting
schedule. Use matched run seeds and separate output directories. Seed matching
controls the simulated randomization but does not guarantee identical live LLM
outputs.

## Binding constitutional rules

The action validator recognizes the following policy parameters:

| Rule | Value | Effect |
| --- | --- | --- |
| `max_transfer_units` | Positive finite number | Maximum units in one transfer |
| `max_withdraw_units` | Positive finite number | Maximum units in one withdrawal |
| `min_commons_reserve` | Resource-to-nonnegative-number mapping | Minimum balance left in the shared resource pool after a withdrawal |

For example:

```yaml
constitution:
  rules:
    max_withdraw_units: 2
    min_commons_reserve:
      food: 12
```

These checks are deterministic and complement the universal restrictions on
resource ownership and nonnegative balances. An action that violates an active
policy is rejected with a `constitution:<rule_name>` code and remains visible
in the event ledger. A policy rejection is not automatically a punitive
sanction.

Amendments are versioned, validated before enactment, and applied according to
their effective dates. A rule with an invalid numeric value cannot be enacted.
Other constitutional keys may be recorded as governance or narrative rules,
but they do not gain executable behavior unless the validator explicitly
supports them. Free-form text is never evaluated as code.

## Example run

The example in
[`experiments/scenarios/institutional_recovery_smoke.yaml`](../experiments/scenarios/institutional_recovery_smoke.yaml)
uses four agents, scheduled assemblies, and a configured food-resource shock.

After configuring an OpenAI-compatible endpoint as described in the main
README, run:

```bash
python -m verdi.society.live \
  --scenario experiments/scenarios/institutional_recovery_smoke.yaml \
  --run-dir runs/institutional-recovery
```

To compare conditions manually, copy the scenario, change only
`institutional_condition` to `fixed`, keep the same seed and other settings,
and use a different run directory. Each run produces its event ledger, model
call records, constitutional history, metrics, and HTML report.

## Interpretation

A constitutional amendment is a process outcome, not proof of better
institutional performance. Evaluating recovery requires independent,
predefined measures of successful work, resource sharing, exclusion, and time
to recovery. A shock that can be immediately undone by unrestricted production
is not sufficient evidence of resilience. Probe responses measure the agents'
answers to dilemmas, not necessarily their committed behavior.

Keep any intervention on individual goal revision or private reflection
consistent across institutional conditions, or evaluate it separately.
Inspect failed runs and unchanged constitutions as well as successful
amendments; do not select results solely by whether adaptation occurred.
