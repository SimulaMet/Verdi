# Architecture decision record

- ADR-001 (2026-10-09): Main is public-facing only. All developmental and agent instruction files stay on dev/feature branches. No automatic or bulk merge dev→main.
- ADR-002: Core model endpoint is the user's Qwen on OpenAI-compatible vLLM, configured only via environment variables. No checked-in secrets.
- ADR-003: Reuse-first architecture. Assess Concordia then Mesa; use existing SimpleAudit API through integration adapter. NanoClaw not MVP dependency.
- ADR-004: Deterministic world reducer, immutable event sourcing and hidden out-of-band measurement are scientific requirements.
- ADR-005: Randomized matched snapshot interventions, preregistered recovery metrics, false sanctions/welfare tracked; no invented findings.
- ADR-006: VS Code custom agents and skills + AGENTS.md preferred; adapt to installed host's actual tools and delegation capability. Parallel workers own disjoint paths; serial integration.
- ADR-007: Research scenario agents and software development agents are different agent fleets, with separate orchestration and access.
- ADR-008 (2026-10-09): Reducer `WorldState.tick` is a CUMULATIVE TRANSITION counter (every processed attempt — action or governance — advances it by 1); the lifecycle tick (0..total_ticks-1) is the scheduling/measurement axis and lives in event payloads (`payload.tick`) and metric keys. `canonical_state_hash` excludes `tick`/`event_cursor`/`law_version`/`constitution_version` per v1 frozen contract; the pilot tracks `constitution_version` in state for amendment fidelity.
- ADR-009 (2026-10-09): P2 effects are pre-registered (docs/PREREGISTRATION.md v1.0) and the P2 workstream API is frozen (docs/P2_CONTRACT.md v1.0) BEFORE any P2 outcome is observed. P2 code is stdlib-only (no numpy/scipy); WS-1 (branching) and WS-3 (statistics) are disjoint and parallel; WS-2 (treatments/arms) depends on WS-1.
