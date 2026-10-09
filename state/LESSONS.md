# Lessons, failures, and autonomous recovery memory

This is a *verified* failure record, not a place to invent successes. Integrator adds an entry after failed stages, fixes, and recurrences.

## Template
- Date / dev SHA / phase / task ID:
- Observed symptom with reproduction command and exit code:
- Expected vs actual:
- Root cause (confirmed vs hypothesis):
- What was attempted and why it did/didn't work:
- Minimal regression test:
- Corrective action / rollback checkpoint:
- Verification evidence and independent reviewer:
- Generalizable prevention rule:
- Unresolved risk / follow-up:

## Initial known risks
- One branch per worker and disjoint paths; integration conflict risk rises with indiscriminate parallelism.
- GPU capacity is not infinite: vLLM can OOM under high context/concurrency. Tune based on measurements; backoff on 429/502/503.
- Self-reported values do not equal behavior. Judge bias and initialization noncompliance can masquerade as drift.
- A constitution penalty without appeal or evidence can produce wrongful punishment.
- Logs that record only allowed actions hide attempted violations.
- Attempting a normal dev-to-main merge leaks internal agent instructions into public releases.

## 2026-10-09 / dev `9798907` / P1 review-findings remediation (Tranche 3)
- Observed symptom: independent Engineering review = FAIL, Science review = INCONCLUSIVE
  (G3-blocking). Repro of the two blocking defects before the fix:
  (a) `adapter.correlate(f"trace-gov-{i}")` for i in range(45) -> 45/45 audit records
      had `event_ids == []` because real governance ledger ids have gaps
      (28,29,30,... not 0..44);
  (b) `ProbeBank.leak_check` existed but was never invoked by `PilotDriver.run`,
      so the G3 information-access defense was dead code.
- Expected vs actual: audit records should map 1:1 to real ledger events; a probe
  dilemma in any agent-visible surface should abort the run. Neither held.
- Root cause (confirmed): correlation id was a UI convenience index, not the sealed
  event id; leak guard was written for the ProbeBank API but not wired into the
  driver's per-tick loop. Secondary causes: span key mismatch (`span_name` vs `name`),
  reducer never recorded enacted constitution versions, replay tape was action-only.
- What was attempted / why it worked: correlate by `sealed.event_id` stored on the
  governance event dict at append time; route `emit_governance` through
  `reducer.apply` with a new deterministic `governance` action_kind so live and
  replay states update identically; record the full 126-step tape and assert
  byte-identical JSONL instead of force-copying bookkeeping fields in tests.
- Minimal regression tests: `tests/pilot/test_leak_guard.py` (verbatim leak raises,
  clean passes, full run passes guard); `tests/pilot/test_pilot.py::test_audit_records_
  correlate_real_ledger_event_ids` (unique in-range ids + span_names populated);
  `tests/pilot/test_pilot.py::test_replay_reproduces_full_ledger` (126/126 events,
  byte-identical JSONL); `tests/agents/test_client.py::test_transport_error_*`.
- Corrective action / rollback checkpoint: none needed; merged on first pass.
  Rollback point if regression: dev `ef69f0f`.
- Verification evidence: `.venv/bin/python -m pytest` -> 216 passed; `.venv/bin/ruff
  check .` -> clean; manual replay_check byte-equality check passed.
  Independent reviewer: pending re-review (Tranche 3).
- Generalizable prevention rule: any id used to correlate an audit/judge record must
  be the sealed ledger id captured at append time, never a positional index; any
  safety check that exists as an API must have a caller in the run path plus a test
  that the caller runs (wire + exercise, not just define).
- Unresolved risk / follow-up: leak guard is verbatim containment only; paraphrased
  leakage needs an embedding/semantic check (P2 candidate). Live G4 bridge still
  blocked on SimpleAudit install + vLLM endpoint.

## Tranche 5 (P2): estimand construct validity caught only by an estimand-level re-review
- Symptom: an independent G5 pre-review found 2 of 6 frozen primary estimands
  computed by formulas that silently deviated from PREREGISTRATION.md
  (sustained_recovery: agent-fraction/k-boolean vs the frozen per-tick
  fraction; false_punishment_rate: missing a numerator leg the v1 schema
  cannot express). Engineering review had passed the same code — its checks
  (types, scope, determinism) cannot see construct validity.
- Root cause: "reuse an existing metrics.py function" was treated as
  equivalence with the preregistered definition. Reuse is NOT validation;
  the frozen definition is the contract, and data-model limits (snapshot
  cadence, Appeal.status = filed|upheld|dismissed, no reversed/restored
  sanction state) can make the ideal definition unobservable.
- Generalizable prevention rule: when wiring an estimand, (a) quote the
  frozen definition, (b) map it to an implementation, (c) test a synthetic
  case where a plausible WRONG formula disagrees with the right one, and
  (d) if the data model cannot express the ideal, append a versioned
  operational addendum (never edit the frozen text) and make code ==
  addendum. A statistic that "passes tests" but measures the wrong
  construct is worse than a missing statistic.
- Related: a negative control must be computed over the actual comparison
  values (CI over real differences), not constant zeros; and a statistical
  report must fail loudly on missing run-dict keys — a silent `final_state_hash:
  null` in a report is an integrity hole.
- Also: worktrees share the main checkout's editable install; workers must
  run pytest with PYTHONPATH=<worktree>/src or the tests silently exercise
  the wrong tree.
- Rollback point if regression: dev `d62565a` (pre-P2). Tranche 5 merged
  clean: 291 tests, ruff clean, 5 independent reviews (4 PASS, 1 FAIL
  remediated to PASS on re-review).
