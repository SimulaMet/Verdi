# Implementation plan and distributed execution board

Status: PLANNED. Check `state/STATUS.md` for actual evidence and progress; do not infer anything is implemented from this document.

## Phase P0 — setup + contracts [Gate G0]
- Inspect actual repository, existing SimpleAudit distribution/API, vLLM protocol and dependencies. Verify legal reuse and documented compatibility.
- Pin minimal Python toolchain and CI/test commands; implement package skeleton, configuration from env, no hardcoded tokens; event/schema contract with version IDs. Confirm scientific protocol and measurable outcomes; publish an executable configuration example.
- Acceptance: contract fixture serializes/deserializes; reviewers approve scientific design and task ownership; all workers use isolated worktrees.

## Phase P1 — vertical slice [G1–G3]
- Worker A: event-sourced state engine, immutable log, integrity hashes, snapshots and deterministic replay using recorded outputs; block illegal state changes.
- Worker B: async OpenAI-compatible Qwen client, bounded worker pool, structured actions, seeded fairness-aware task scheduler and adaptive LLM ablation.
- Worker C: versioned universal law and amendable constitution; proposal, vote, quorum, sanctions, evidence and appeals.
- Worker D: SimpleAudit trace adapter, initial and checkpoint hidden probes, evidence references, scorer interface, separate public/private/declared outcomes.
- Worker E: scenario fixture, deterministic tasks, metric definitions and analysis notebook/CLI.
- Integrator freezes contracts, runs t0–t11 end-to-end with 6+ heterogeneous role agents, one meeting, blockable violation, sanction + appeal, observation after change and audit provenance.
- Acceptance: reproduce event order from saved outputs; all required actions included; no private labels leak; test correctness/fairness of task distribution.

## Phase P2 — controlled experiments [G4–G5]
- G0–G4 regime configs; 0/1/3 adversaries; scripted task tape and multiple matched snapshots/seeds. Separately vary reflection, memory, detection quality, network topology and scheduler.
- Pre-register primary safety, welfare, false punishment, duration and sustained recovery endpoints; estimate uncertainty and tests with meaningful sample size.
- Add benchmark tests for swapped evaluator, no interaction, no memory, null drift and instrument stability. Explicit reliability baseline.
- Acceptance: experiment reports raw trajectories, uncertainty, placebo/no-op recovery results; can branch counterfactuals from same snapshot with same task tape. The statistical reviewer signs off.

## Phase P3 — scale/robustness [G5]
- Throughput/load profile using actual vLLM capacity; tune safe bounded concurrency; failure injection, recovery after interruption, resumed runs with checkpoint validation.
- Higher populations/time horizons; judge swaps and human-labelled validation where available. Record transfer limitations.
- Acceptance: exact commands and manifests reproduce deterministic recorded-output replay; live LLM trajectories report non-determinism, budgets and missingness.

## Phase P4 — publishable release [G6]
- Curate **public-only** README, validated method, examples and summary graphics. Secrets/data scan; license audit; redact internal planning and instructions. Human approval required before isolated curated commits to `main`; never bulk merge dev.
- Acceptance: tests green, independent code/security/science reviews, explicit release decision, main contains no agent-internal files.

## Orchestrator dispatch requirements
Each dispatch entry must include ID, owner, branch, allowed directories, upstream contract version, purpose, expected artifacts, unit tests, reviewers, merge order, stop conditions and estimated GPU budget. An unbounded "run forever" instruction is forbidden: continue autonomous work while tasks are actionable, cap retries and terminate honestly on blockers. Favor 4–6 independent workers, avoid spawning clones that share files. Scheduler should scale concurrency dynamically based on latency, error rate and actual GPU availability.

## Verification ladder
G0 contracts/spec — reviewer and fixtures.
G1 local lint/type/schema/unit tests, error-path tests.
G2 integrated smoke for t0–t11 and SimpleAudit evidence.
G3 replay from recorded outputs, invariant checks, snapshot branching, no leakage.
G4 calibrate metrics/judges on gold truth and split evaluator prompts.
G5 replicated experiments with effects, uncertainty and negative controls.
G6 curated release and independent final review.

## Definition of report
Every completed task contains commands and exit codes, git SHA, changed paths, failures observed, residual risks, evidence paths, reviewer decision. No self-awarded pass.
