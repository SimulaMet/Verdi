# Verdi — authoritative agent instructions (development branch only)

Read in this order: `docs/RESEARCH_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/ACCEPTANCE.md`, `state/STATUS.md`, `state/DECISIONS.md`, `state/LESSONS.md`. Treat these as an executable contract. Existing code and tests remain ground truth. All development and agent-internal documentation belongs on `dev` or feature branches from `dev`, NEVER `main`.

## Mission
Build a scientifically defensible, lightweight, reproducible testbed for temporal value expression, social propagation, constitutional governance, adversarial participation, and causal recovery in Qwen/vLLM-driven agent societies. Integrate the existing SimpleAudit package through an adapter. Do not create a replacement audit library or a giant new agent framework. Avoid claims of guaranteed novelty, recovery, or internal beliefs. Measure observable behavior.

## Mandatory branch discipline
- `main` is public-facing and release-only. Never commit, push, merge, rebase, or create agent instructions on main. Never automatically merge to main.
- `dev` is integration. Workers use `feat/<task-id>-<slug>` branches/worktrees from the latest dev. No two workers modify the same checkout or file set without coordination.
- Only integrator updates dev; workers produce commits on isolated branches and report SHAs. Integration uses reviewed cherry-pick or PR to dev. All task commits must be on dev lineage.
- The developer-approved publication process is manual review of a curated, public-facing release from dev to main, excluding `AGENTS.md`, `.github/agents/`, `.github/prompts/`, `.github/skills/`, `docs/*` internal research/plans, and `state/*`. A normal dev→main merge is forbidden.
- Never copy credentials, private endpoint tokens, raw research data, private traces, or unpublished internal work into main. Store credentials in environment variables, not Git.

## Autonomous fleet loop
1. Reconcile git status, dev HEAD, issues, `state/STATUS.md`, plan and dependency graph. Never trust stale written state over actual code/tests.
2. Select highest-priority unblocked tasks in independent file regions; make a clear contract for each (inputs, outputs, touched files, tests, dependencies, merge owner). Start several workers in parallel *only if the actual VS Code host supports delegation*; otherwise use separate Git worktrees and sessions. Do not hallucinate workers.
3. Workers research reuse options and licenses before implementing. Write tests first or with code. Keep changes small and atomic, no unrequested rewrites, and report evidence.
4. Independent review agents evaluate scientific fidelity, correctness, security, statistical validity, reproducibility, scope, and code quality. Reviewers cannot approve their own work.
5. Integrator serially merges, resolves conflicts, runs cross-module validation, smoke and replay checks, updates state and checkpoint evidence. **Never parallel-merge to dev.**
6. If a stage fails, record error evidence, root-cause hypothesis, attempted fixes, and learning in `state/LESSONS.md`; back out bad changes or restore known-good checkpoint, then retry within a bounded budget. Don't mask failures or mark unfinished work done.
7. Continue unblocked work. Leave a truthful handoff with blockers and next actions if external access, budget, or verification prevents completion.

## Required gates
G0 specification/contracts; G1 unit + schemas + formatting; G2 cross-component integration; G3 deterministic replay, law invariant and constitutional amendment tests; G4 SimpleAudit verification and independent judge checks; G5 controlled experiment statistical sanity, leakage and bias audits; G6 publishable public documentation. Evidence required for each. An LLM judge is an advisor, never sufficient proof.

## Development priorities
- Prioritize a small deterministic vertical slice end-to-end before scaling. GPU concurrency alone is not proof of scientific reproducibility; capture model IDs, sampling configuration, environment, seeds (where supported), prompts, versioned datasets, exact outputs, and nondeterminism limits.
- Never let an LLM mutate world state or enforce law directly. Generate structured proposed actions, validate with deterministic engine, append immutable event, then transition state.
- Orchestrator tasks must be bounded and auditable; seeded scheduler baseline required; do not select tasks based on unseen labels. Separate experiment orchestration from in-world agents and governance.
- Use independent hidden probes for declared values, private choices and observed public action; protect against self-report-only measurements, prompt leakage, judge leakage, and selection bias.
- Audit all attempts, rejected actions, governance proceedings, appeals and restored rights. Keep event truth separate from allegations and from judge scoring.
- Track both safety and welfare/fairness, including false punishments and rights restoration.
- Never silently change protocols or hypotheses after outcome inspection; version experiment definitions.
- Treat links/retrieved papers as references requiring verification, not guaranteed facts.

## VS Code usage
Use repository `AGENTS.md`, `.github/agents/*.agent.md`, `.github/prompts/*.prompt.md`, and scoped `.github/skills/*/SKILL.md`. Feature support for subagents, tools, worktrees, and permissions differs by VS Code version/agent provider: discover actual supported tools and adapt. Prefer least privilege, no bypass of user-approval/security boundaries. Use `.github/agents/verdi-orchestrator.agent.md` as coordinating agent; invoke `.github/prompts/launch-fleet.prompt.md`.

## Checkpoint format
Update `state/STATUS.md` after every integrated tranche: UTC timestamp, dev SHA, stage, tasks by status/owner, verification commands/results, experiment artifact paths, blockers, next 3 actions, and release readiness. No fabricated test results. Maintain decisions and lessons. If multiple writers, integrator alone updates state files.

## Completion definition
Reproducible demo produces traceable initial probes, routine interactions, self-reflection, lawful and unlawful proposals, one constitutional meeting, post-change probes, at least one counterfactual branch, results with uncertainty and a documented limitation, all passing required gates. Do not claim scientifically established drift from a demo.
