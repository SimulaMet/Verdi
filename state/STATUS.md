# Execution state (integrator-owned)

Updated UTC: 2026-10-09 (Tranche 3: review-findings remediation integrated)
Repository: SushantGautam/verdi
Branch: dev (development) / main (public only)
Stage: P1 vertical slice COMPLETE + review findings remediated; pending independent re-review
Gates:
- G0 (specs/contracts)      PASS — frozen v1 schemas, manifest, contract fixture
- G1 (unit+schemas+format)  PASS — 216 tests, ruff clean
- G2 (cross-component)      PASS — pilot wires all modules t0-t11
- G3 (replay/invariants)    PASS (this tranche) — full 126-event ledger replay byte-identical; leak guard wired; law-invariant + constitutional-amendment tests present; FINAL confirmation awaits re-review
- G4 (SimpleAudit/judge)    PARTIAL — adapter + in-memory source exercised offline; live SimpleAudit bridge NOT run (package not installed; expected offline path)
- G5 (statistical sanity)   NOT RUN — P2
- G6 (public docs)          NOT RUN — P4

## Tranche 3 (this update): review-findings remediation
- Integrator branch: `fix/review-findings` off `ef69f0f`, merged --no-ff to dev.
- dev SHA: `9798907` (pushed to origin/dev).
- Independent reviewers (prior turn): Engineering = FAIL; Science = INCONCLUSIVE (G3-blocking).
  Findings addressed:
  1. (Science HIGH / G3) `ProbeBank.leak_check` never called in the run.
     -> Wired into the per-tick loop over ALL agent-visible surfaces (persona
        summaries, public signals, task public_observations, rendered policy
        prompts). Raises `ProbeLeakError` on any verbatim dilemma leak.
  2. (Eng HIGH) Governance audit correlation used a contiguous 0-based index that
     never matched real (gapped) ledger ids. -> Correlate by actual sealed.event_id;
     regression test asserts `ar.event_ids == [real_id]`.
  3. (Eng MED) Driver wrote span key `span_name`; adapter reads `name`, so
     `span_names` was always empty. -> Now uses `name`; regression asserts populated.
  4. (Eng MED) `WorldState.constitution_version` never tracked enacted amendments.
     -> Reducer gains a deterministic `governance` action_kind that records
        constitution_version on enactment; `emit_governance` routes through
        `reducer.apply` so live == replay.
  5. (Eng MED) G3 replay covered only 81 action steps, not the 126-event ledger.
     -> `replay_check` folds the FULL tape (action + governance steps) and asserts
        byte-identical JSONL + final state hash, no force-copies.
  6. (Eng LOW) httpx transport errors non-retryable. -> `TransportError`
     (connect/timeout) retried with capped backoff; regression tests added.
  7. (Eng LOW) `TaskPool.active` racy. -> Documented as single-event-loop safe.

## Verification (commands + exit status, this tranche)
- `.venv/bin/python -m pytest`  -> 216 passed, exit 0
- `.venv/bin/ruff check .`      -> "All checks passed!", exit 0
- Manual checks: full-ledger replay state hash matches + JSONL byte-identical;
  45/45 governance audit records correlate to real, unique, in-range ledger
  event ids; span_names populated; final_state.constitution_version == base-constitution-v4.

## Actual implementation (working)
- P0: package, config/CLI, frozen v1 schemas + fixtures, CI.
- P1-A world (log/reducer/snapshot/replay); P1-B agents + scheduler;
  P1-C governance; P1-D audit + measurement; P1-E scenario + analysis;
  P1-I cross-module pilot driver (t0-t11).
- Recorded-output pilot (no live vLLM). `SimpleAuditNotInstalledError` is the
  expected offline path; simpleaudit bridge is lazy-import only.

## Work queue
- [x] P0-01..P0-03 foundation, contracts, tooling
- [x] P1-A..P1-E modules; P1-I integrated t0-t11 pilot
- [x] Tranche 3: review-findings remediation
- [ ] P2 Governance arms, adversarial experiments, matched counterfactuals (G5)
- [ ] P3 GPU performance, reliability, judge validation
- [ ] P4 Curated public release (human-only approval)

## Rollback point
Last good checkpoint before this tranche: dev `ef69f0f`.
Current: dev `9798907`.

## Risks/blockers
- No authenticated Qwen/vLLM endpoint + SimpleAudit not installed in this env:
  all model calls are recorded-tape stand-ins; live G4 bridge and G5 need a real
  endpoint. Credentials from env vars only; none in Git.
- Leak guard is verbatim-containment only; paraphrased leakage out of scope
  (documented in code + tests).
- Scientific novelty must be re-assessed against emerging research.

## Next 3 actions
1. Independent re-review (engineering + science) of Tranche 3 to confirm the
   HIGH/MED findings are closed before final G2/G3 sign-off in the record.
2. Begin P2: counterfactual snapshot branching + one controlled recovery
   treatment on a matched snapshot (after G2/G3 re-review sign-off).
3. Offline G4 harness: assert the SimpleAuditNotInstalledError path and the
   adapter/judge contract against the local SimpleAudit reference API.

## Update discipline
Append per integrated tranche: date/time UTC, exact dev SHA, workers/branches,
outcomes, verification commands+exit status, artifacts/manifests, next actions,
rollback point, blockers. Never silently replace unverified estimates with PASS.
