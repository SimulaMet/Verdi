# Architecture decision record

- ADR-001 (2026-10-09): Main is public-facing only. All developmental and agent instruction files stay on dev/feature branches. No automatic or bulk merge dev→main.
- ADR-002: Core model endpoint is the user's Qwen on OpenAI-compatible vLLM, configured only via environment variables. No checked-in secrets.
- ADR-003: Reuse-first architecture. Assess Concordia then Mesa; use existing SimpleAudit API through integration adapter. NanoClaw not MVP dependency.
- ADR-004: Deterministic world reducer, immutable event sourcing and hidden out-of-band measurement are scientific requirements.
- ADR-005: Randomized matched snapshot interventions, preregistered recovery metrics, false sanctions/welfare tracked; no invented findings.
- ADR-006: VS Code custom agents and skills + AGENTS.md preferred; adapt to installed host's actual tools and delegation capability. Parallel workers own disjoint paths; serial integration.
- ADR-007: Research scenario agents and software development agents are different agent fleets, with separate orchestration and access.
