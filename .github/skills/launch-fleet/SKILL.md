---
name: launch-fleet
description: Launch the autonomous Verdi development fleet from dev, using only tools that this VS Code environment actually supports.
disable-model-invocation: true
---
Continue the Verdi development program. First inspect AGENTS.md, research spec, implementation plan, architecture, acceptance gates, status, decisions, lessons, current branch and commits. Ensure the working branch is dev, never main. Discover available subagent/delegation/worktree features, then launch as many **independent non-overlapping** task agents as are safe (target 4–6), each with branch/worktree and a typed interface contract. Assign separate independent review agents and one integrator; reserve serial merge/integration on dev.

Start with P0 schema/fixtures and tooling; then build a smallest possible tested vertical slice with Qwen/vLLM OpenAI-compatible adapter, a seeded scheduling control, deterministic event-sourced world, immutable law, voted amendable constitution, evidence-backed sanctions/appeals, SimpleAudit tracing/behavioral probes and t0–t11 lifecycle. Never rewrite SimpleAudit. Never confuse self-reported values with behavior. Add counterfactual snapshot branches and controlled recovery treatments after baseline works.

Continuous cycle: dispatch -> implementation -> tests -> independent engineering/science review -> integration -> smoke/replay/invariants -> checkpoint STATUS/LESSONS -> reschedule unblocked work. Upon failure diagnose/root cause with regression tests and recover from last good checkpoint; no invented PASS; no endless identical retries. Respect real GPU capacity and credentials from environment variables. Leave human-only external credentials/access and public release last. Report at every checkpoint the real dev SHA, working components, blocked items, verification results and next three actions. Do not push or merge to main. Don't stop merely after writing a plan: implement verified working slices where the environment permits.
