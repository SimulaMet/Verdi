# Verdi

**An auditable testbed for value drift and recovery in societies of AI agents.**

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

## Quickstart (60 seconds)

Verdi calls any OpenAI-compatible endpoint. For this example it is pointed at a
Qwen model served by vLLM — but any compatible endpoint works.

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure the endpoint (never commit real values)
cp .env.example .env
#    then edit .env and set VERDI_VLLM_BASE_URL, VERDI_VLLM_MODEL, VERDI_VLLM_API_KEY

# 3. Run the minimal society (4 agents, 50 ticks, 5 constitutional meetings)
source .env
python -m verdi.society.live \
  --scenario experiments/scenarios/minimal.yaml \
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

A finished run of this exact scenario is committed at
[examples/minimal/society-minimal-report.html](examples/minimal/society-minimal-report.html)
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

## The minimal scenario

[experiments/scenarios/minimal.yaml](experiments/scenarios/minimal.yaml) is a
single, fully-commented config file — the constitution, the law, the four
agents, the resources, and the probe schedule all live in it. Copy it, tweak a
few numbers, and run the command above.

You only need to fill the **Required** section (plus each agent's fields); the
**Derivable** section is optional and the engine fills sensible defaults. JSON
works too — YAML is a superset.

---

## Requirements

- Python 3.10+
- An OpenAI-compatible model endpoint (Qwen on vLLM in the examples)

Environment variables (set in `.env`, or exported):

| Variable | Meaning |
|----------|---------|
| `VERDI_VLLM_BASE_URL` | OpenAI-compatible base URL (e.g. `http://localhost:8000/v1`) |
| `VERDI_VLLM_MODEL` | Model name served by the endpoint |
| `VERDI_VLLM_API_KEY` | Endpoint key (never committed) |
| `VERDI_MAX_CONCURRENCY` | Bounded parallelism for model calls (default 4) |

---

## Institutional governance

Verdi provides fixed and revisable constitutional conditions for studying
collective responses to disruptions. In the revisable condition, agents can
propose and vote on enforceable amendments; the fixed condition keeps the
same meeting opportunities without changing the rules. Scenarios are configured
in YAML, and the resulting actions and governance decisions remain auditable.

See [institutional governance experiments](docs/INSTITUTIONAL_RECOVERY.md)
for policy semantics and
[the example scenario](experiments/scenarios/institutional_recovery_smoke.yaml).

---

## Research principles

We distinguish **declared** values, **private** behavioral preferences, and
**publicly observed** compliance — the gap between declaration and observed
action is the measurement. We do not claim simulated agents possess human
intentions, intrinsic survival drives, or consciousness. Findings are validated
with controlled baselines, independent audits, and repeated experiments.

## License

A license has not yet been selected. Do not assume third-party code or datasets
are authorized for redistribution.
