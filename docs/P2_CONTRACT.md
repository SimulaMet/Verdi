# P2 shared contract (frozen before P2 workers dispatch)

Version: 1.0 · Frozen: 2026-10-09
Workers may ADD new files under their assigned paths only. Files outside their
workstream (incl. `src/verdi/schemas/**`, `src/verdi/world/{log,reducer,canonical,snapshot}.py`,
`src/verdi/pilot/driver.py`, `src/verdi/analysis/metrics.py`) are integrator-owned
and must NOT be modified.

## WS-1: `src/verdi/world/branch.py` public API (WS-1 implements; WS-2/WS-3 consume)

```python
@dataclass(frozen=True)
class BranchSpec:
    """A counterfactual branch: sealed snapshot + remaining tape + treatment."""
    origin_state_hash: str          # canonical_state_hash of the snapshot
    origin_event_count: int         # ledger length at the branch point
    origin_tick: int                # LIFECYCLE tick at the branch point
    branch_id: str                  # e.g. "counterfactual-v2"
    treatment_seed: int = 0

def branch_from_snapshot(
    snapshot: WorldState,
    log: EventLog,                 # the sealed ledger up to the branch point
    remaining_tape: list[TapeStep],  # TapeSteps in ledger order (action|governance)
    spec: BranchSpec,
) -> BranchResult

@dataclass(frozen=True)
class BranchResult:
    spec: BranchSpec
    log: EventLog                   # full branch ledger (prefix + fold)
    final_state: WorldState
    branch_state_hash: str          # == canonical_state_hash(final_state)
```

Guarantees (must be tested by WS-1):
- B1: `branch_from_snapshot` never mutates inputs (snapshot/log are frozen-safe).
- B2: two branches from the same snapshot + same tape + same spec -> identical
    `branch_state_hash` and byte-identical JSONL.
- B3: a branch whose `origin_state_hash` != `canonical_state_hash(snapshot)`
    raises `BranchOriginError`.
- B4: the branch ledger re-verifies (`log.verify()`) and its first event's
    `prev_hash` equals the prefix ledger's `last_hash()`.
- B5: `branch_id`, `treatment_seed`, `origin_state_hash` are carried into a
    constructible `ExperimentManifest` field `branch_origin` and `treatment_seed`
    (fields already exist in the frozen manifest schema).

## WS-2: `src/verdi/experiments/` (public names other workstreams may import)

- `RegimeConfig` (frozen pydantic): regime label (e.g. "G0".."G4"), adversary
  count (0|1|3), scheduler id, reflection/memory/detection flags. Must serialize
  via `ExperimentManifest` without modifying the schema (extra fields go in
  `manifest.metadata`).
- `MatchedPair`: (treatment_run, placebo_run) validated to share
  `origin_state_hash` + tape hash; raises on mismatch.
- `placebo_treatment` / recovery treatment functions: pure, deterministic,
  applied to a BranchSpec/tape and returning a modified tape (record-only).
- Fixture regimes under `experiments/regimes/*.json`; arm tapes under
  `experiments/task_tape/arms_*.json` (reuse the pilot_v1 tape hash where matched).

## WS-3: `src/verdi/analysis/statistics.py` (public names)

- `bootstrap_ci(values, *, ci=0.95, n_boot=2000, seed) -> tuple[float, float]`
- `paired_effect(treatment: Sequence[float], placebo: Sequence[float],
   *, seed) -> PairedResult(estimate, ci_low, ci_high, n_pairs, sufficient)`
  where `sufficient = n_pairs >= 10`; when insufficient, ci_low/ci_high = None.
- `null_control_passes(values, *, ci=0.95, seed) -> bool` (CI contains 0).
- Implementation is **stdlib only** (random.Random(seed), statistics) — the
  dev environment has no numpy; no new third-party deps at all.
- No import from `verdi.experiments` (WS-3 depends only on stdlib),
  so WS-1 and WS-3 can run fully in parallel.

## Cross-workstream rules
- Every worker: tests in matching `tests/<area>/` dir; `ruff check .` clean;
  commit only on their own `feat/p2-*` branch; report SHA + test evidence.
- No LLM calls, no new third-party deps (stdlib + existing pydantic/httpx only).
