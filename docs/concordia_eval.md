# Concordia Integration Evaluation (Worker D)

Date: 2026-10-12 · Status: evaluation complete, no integration recommended.

## TL;DR

**Do not integrate.** The `concordia` package on PyPI is *not* Google DeepMind's
Concordia agent-simulation library — it is an unrelated, abandoned MLOps
monitoring tool. Installing it would add heavy, broken dependencies for zero
scientific value. The genuine DeepMind Concordia remains a GitHub-only source
distribution whose API and license have never been published as a stable
release. No adapter was written.

## What we checked

| Check | Result |
|---|---|
| `pip index versions concordia` | v0.1.1 (latest), oldest 0.0.1 |
| Wheel contents (`concordia-0.1.1-py2.py3-none-any.whl`) | 4 modules, ~1,400 lines total |
| Package summary | "Automated monitoring of machine learning models in production… feature discrepancies, train/serve skew" |
| Author | Preston Parry (ClimbsBytes) — **not** Google DeepMind |
| Repository | github.com/ClimbsRocks/Concordia |
| Runtime deps | `auto-ml>=2.9.4`, `pymongo>3,<4`, `redis>2,<3`, `dill`, `numpy`, `pandas` |
| Python support declared | 2.7–3.6 era classifiers |
| Imports at module load | `pymongo.MongoClient`, `redis`, `tabulate` — connects to localhost Mongo/Redis in `__init__` |

There is no `Agent`, `AgentEnvironment`, `World`, or `Message` class anywhere
in the wheel. It is a name collision, nothing more.

The real DeepMind Concordia (github.com/google-deepmind/concordia) is
distributed from source only, pins specific dependency versions, has no
PyPI release, and its LICENSE situation for programmatic reuse in a research
testbed is unverified. Pulling it in would violate our "verify licenses before
implementing" rule without any compensating capability.

## Overlap/gap analysis (vs. what Verdi already has)

Even assuming access to the *genuine* Concordia, the fit is poor:

| Capability | Concordia (DeepMind) | Verdi today |
|---|---|---|
| World state | Mutable `World` object, agents mutate directly | Deterministic reducer over frozen `WorldState`; LLMs can never write state |
| Events | Ad-hoc log records | Hash-chained append-only `WorldEvent` ledger incl. rejected actions |
| Turn control | Central scheduler loop | Seeded task pool + `GovernanceEngine` deciding procedurally |
| Governance | None built-in | Constitution, law, sanctions, appeals, rights restoration |
| Measurement | None | Hidden probes, probe recorder, independent judge separation |
| Reproducibility | Agent prompts via template files | Content-addressed snapshots, replay, counterfactual branching |
| Audit trail | None | SimpleAudit adapter + event truth separate from allegations |

Verdi's design constraints (LLM proposes, deterministic engine disposes;
event truth separate from world truth; auditable governance proceedings) are
structurally incompatible with Concordia's mutable-world, message-passing
model. An adapter would either (a) duplicate already-working machinery or
(b) force Verdi data through Concordia's mutation model, breaking the
determinism guarantees G3/G4 depend on.

## Licensing & supply-chain notes

- PyPI `concordia`: MIT, but depends on `auto-ml` (pulls scikit-learn-class ML
  stack) and EOL-era `redis<3`/`pymongo<4`. Heavy, unmaintained, and irrelevant
  to this project.
- DeepMind Concordia: no stable release artifact; per-repo license terms must
  be verified before any vendoring. Not done here (out of scope/timebox).

## Risks if integrated anyway

1. Import-time DB connections (PyPI version) crash or hang headless runs.
2. Dependency bloat (~GB of ML stack) breaks lightweight reproducibility.
3. Name confusion in documentation/publication ("Concordia") without the
   actual library present.
4. Temptation to route world mutations through a second, non-deterministic
   state path — direct threat to the hash-chained ledger.

## Recommendation

**Do-not-integrate.** Keep the name "Concordia" out of Verdi's public docs to
avoid the collision. If future work wants a shared vocabulary with DeepMind's
framework, cite it in the spec and implement the few useful ideas natively
(they are simple prompt-template patterns) rather than depending on the
package.

No code artifacts were produced; no `src/verdi/adapters/` module exists, which
is the correct outcome — an adapter to the wrong package would be worse than
none.
