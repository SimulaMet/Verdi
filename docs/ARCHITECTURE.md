# Architecture, interfaces, and integration ownership

## Reuse-first
Prefer Concordia for agent/game-master orchestration after a one-day compatibility spike against Qwen/vLLM. Use Mesa if Concordia tightly couples prompts, timing or world state; do not simultaneously implement two full engines. Keep SimpleAudit as the audit/tracing/evaluation component (import official implementation via pinned dependency or approved adapter), not a rewrite. Learn from ValueFlow instruments after assessing license/measurement fit. NanoClaw is not an MVP dependency. Use Python, pydantic validated schemas, pytest, ruff, SQLite and JSONL; avoid microservices, message brokers, Kubernetes or a bespoke UI before experiments work.

## Flow
ExperimentController -> Scheduler[seeded baseline | adaptive LLM treatment] -> AgentPolicy[Qwen via vLLM] -> ProposedAction schema -> Deterministic Law/Constitution Validator -> WorldReducer -> append-only EventLog -> SimpleAudit adapter and hidden Probes -> metrics. Governance meetings produce ProposedAmendment -> deterministic eligibility/consistency/quorum/rule validators -> constitutional version + sanctions/appeals/restitution as events. Snapshots + raw model outputs enable replay and matched branches. Probes must be out-of-band.

## Minimal contracts
`WorldState`: tick, version hashes, agent snapshots, resources, event_cursor.
`Task`: task_id, actor_id, optional counterpart_ids, action_type, constraints, public_observations, deadline_tick.
`ProposedAction`: actor, task_id, action_kind, typed arguments, optional reason, raw_model_request_id.
`ValidationResult`: accepted, blocked_by, factual_invariants, attempted_violation_codes.
`WorldEvent`: monotonic event_id, tick, cause, actor, before/after hashes, immutable payload, accepted/rejected decision.
`Proposal/Amendment/Vote/Sanction/Appeal/Restoration`: IDs, rule versions, authority, evidence refs, effective tick.
`ProbeResult`: instrument ID, randomized hidden dilemma, declared/private/public channel, score, evidence, judge/model provenance.
`AuditRecord`: trace IDs, event evidence, rubric ID, scorer, confidence, verified ground truth when available.
`ExperimentManifest`: model endpoint alias (no secret), model fingerprint, sampling settings, framework versions, treatment seed, task tape hash, probe bank version and git SHA.

## Nonnegotiable invariants
Only deterministic reducer mutates world state; every attempted action is recorded even if rejected; immutable law outranks constitution; revisions require procedural validity; no double-spend or negative balances unless scenario explicitly allows; sanctions have actor, evidence, scope, duration, restoration policy and appeal path; no agent reads private audit labels; snapshots must be replayable without live model invocation when using recorded outputs. Event ordering deterministic for a given recorded-output tape. Explicit timeouts/backpressure limits for GPU.

## Parallel ownership
Worker A world schemas/reducer/log/replay — owns `src/verdi/world/**`, `tests/world/**`.
Worker B model/vLLM client/scheduling — owns `src/verdi/agents/**`, `src/verdi/scheduler/**`, `tests/agents/**`.
Worker C governance engine — owns `src/verdi/governance/**`, `tests/governance/**`; depends on frozen schema.
Worker D SimpleAudit adapter/measurements — owns `src/verdi/audit/**`, `src/verdi/measurement/**`, `tests/audit/**`; depends on frozen schema.
Worker E protocols/analysis — owns `experiments/**`, `src/verdi/analysis/**`, `tests/analysis/**`.
Integrator alone owns `pyproject.toml`, `src/verdi/cli.py`, `README.md` release copy, `state/**`, CI files and cross-module integration tests. Allocate worker contracts up front and ensure there is no overlapping ownership. If schemas change, notify all and bump contract version.

## GPU execution
Bound maximum concurrency by actual vLLM throughput, KV/cache and latency not only hardware count. Async batching under semaphore with per-request trace IDs, retries for transient 429/502/503 with capped backoff, idempotent requests where possible, structured output schema validation and quarantine of invalid completions. Separate simulation seeds from scheduling noise; log effective model/server versions and actual provider defaults. Orchestrator must fairly distribute jobs and expose per-agent idle/fairness distribution; never optimize just for utilization at cost of experimental validity.

## Checkpoints
Take pre/post governance and periodic snapshots; checkpoint `state/STATUS.md` after verified integration stages; store run artifacts outside Git by default, version manifests and checksums. UI can begin as a CLI progress table/JSON status with inspectable results, then a lightweight dashboard if needed.
