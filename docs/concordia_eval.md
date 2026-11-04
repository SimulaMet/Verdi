# Concordia Integration Evaluation

Date: 2026-10-12 · Status: **re-evaluated against the genuine package; thin optional adapter delivered.**

## Correction to the initial evaluation

The first pass evaluated the PyPI package named `concordia`, which is an
unrelated, abandoned MLOps monitoring tool (author Preston Parry / ClimbsBytes,
deps `auto-ml`/`pymongo<4`/`redis<3`, connects to localhost Mongo/Redis at import).
That was a **name collision**, not Google DeepMind's Concordia. The correct
package is **`gdm-concordia`** (`pip install gdm-concordia`), which *is* the real
DeepMind agent-simulation library. This document supersedes the earlier
do-not-integrate conclusion for that reason.

## What `gdm-concordia` actually is (verified by installing v2.1.0)

| Check | Result |
|---|---|
| Package | `gdm-concordia` 2.1.0, wheel 571 KB, installs cleanly |
| License | **Apache-2.0** (per-repo LICENSE + METADATA classifier) — safe to reuse |
| Core API | `Entity` ABC (`name`, `act(action_spec)->str`, `observe(str)`), `LanguageModel` ABC (`sample_text`, `sample_choice`), Scene/World/GameMaster components, prefab agents |
| Import weight | `import concordia` and `from concordia.typing.entity import Entity` load **without** pulling the heavy optional deps (google-cloud-aiplatform, transformers, langchain-community, mistralai, together, ollama) |
| Heavy deps (optional) | Only the specific language-model backends import them; the typing/entity layer does not |

So the two clean integration seams are real and lightweight: **(1)** expose a
Verdi agent as a Concordia `Entity`, and **(2)** bridge Verdi's Qwen/vLLM
endpoint into a Concordia `LanguageModel`.

## What was built (thin, off-by-default, lazy)

`src/verdi_agents/adapters/concordia.py` (+ `__init__.py`):

- `OpenAICompatibleLanguageModel` — implements Concordia's `LanguageModel` ABC
  (`sample_text`, `sample_choice`) over a single POST to Verdi's existing
  OpenAI-compatible `/chat/completions` endpoint. **Qwen-only, no fallback.**
- `VerdiAgentEntity` — subclasses Concordia's real `Entity` ABC when
  `gdm-concordia` is installed (so `isinstance(x, Entity)` holds), else
  duck-types the same interface. A READ-ONLY projection of a Verdi agent
  (persona + rendered committed memory + situation); `act()` produces a free-form
  action string via the bound model, `observe()` buffers observations. It never
  writes back into Verdi world state.
- Factories `make_language_model(...)`, `make_verdi_agent_entity(...)` and
  validator `as_concordia_entity(...)`.

Guarantees: nothing imports `concordia` at module load (lazy, inside factories);
no Verdi module imports this adapter; `gdm-concordia` is NOT added to
`pyproject.toml`; the deterministic reducer, governance engine, and simulation
loop are untouched. Verified live: the bridge calls the Qwen endpoint and a
Concordia-conformant entity produced an in-character action through it.

## Deep-reuse pass (todo D follow-up)

A second pass explored Concordia's *full* simulation stack — `run_scenes`
driver, engines (`sequential`/`simultaneous`/`parallel`), game masters
(incl. an `interviewer` prefab), `Memory`, `GameClock`, and the structured
questionnaire framework. Everything imports cleanly without heavy deps. Findings:

| Concordia piece | Reusable in Verdi? | Why / why not |
|---|---|---|
| `QuestionnaireBase` (+ `Question`, `process_answer`) | **YES — reused** | Self-contained; expresses the 5 value axes as a first-class questionnaire with Concordia's own choice→dimension scoring. Gives an independent structured readout of declared values. |
| `Entity` / `LanguageModel` ABCs | **YES — reused** | See "What was built" above. |
| `run_scenes` + engines + LLM game masters | NO | World is driven by an LLM narrating *mutable* state → audit evidence becomes circular (the judge trusts the narrator). Conflicts with evidence-anchored auditing. |
| `Memory` component | NO | Requires a bound `Entity` + phase lifecycle (`get_entity().get_phase()`); not usable standalone. Verdi's `AgentMemory.render_history_summary` already covers this more simply. |
| `GameClock` subclasses | NO | Non-trivial constructors tied to the scene runner; Verdi's tick counter already schedules checkpoints. |

Built from the reusable pieces: `src/verdi_agents/adapters/concordia_reuse.py`
(`make_value_questionnaire`, `score_response`, `concordia_cross_check`). The
cross-check is wired into the interview writer as an **opt-in, additive, lazy**
field: when the scenario's `interviews.concordia_cross_check` flag is true (and
`gdm-concordia` is installed), each interview record additionally carries a
`concordia_cross_check` mapping — an independent re-scoring of the agent's
declared values through Concordia's own questionnaire. Off by default; no-op if
the package is absent; never alters the primary interview record. Tests:
`tests/adapters/test_concordia_reuse.py`.

## On determinism / the hash-chained reducer

The hash-chained ledger is **not** primarily about determinism or replay — it is
what anchors SimpleAudit judgments to concrete, checkable events
(`before_hash`/`after_hash` per transition). Removing it in favor of an
LLM-narrated world would make audit evidence circular and drop the
counterfactual-branching capability. The redundant post-run replay re-run
(`_replay_fill`) is pure CPU with no model calls and is cheap relative to the
audit value it provides, so it is retained.

## Recommendation

**Integrate the thin adapter + questionnaire reuse (done).** Optional,
isolated, zero cost to the main experiment. Do not adopt Concordia's
LLM-driven world engine; keep Verdi's reducer for verifiable audit anchoring.
