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
