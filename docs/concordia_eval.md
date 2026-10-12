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

`src/verdi/adapters/concordia.py` (+ `__init__.py`):

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

## Scope limit (deliberate)

Concordia's mutable-world / game-master model is structurally different from
Verdi's deterministic hash-chained reducer. The adapter maps only the two clean
seams above and **does not** wrap or replace the whole simulation. Routing Verdi
world mutations through Concordia would break the determinism/replay guarantees
G3/G4 depend on, so that is intentionally out of scope. If future work wants
shared vocabulary with DeepMind's framework, use these seams for cross-framework
inspection or co-simulation experiments, not as a replacement engine.

## Recommendation

**Integrate the thin adapter only (done).** It is optional, isolated, and adds
zero cost to the main experiment. Do not attempt full-engine interop.
