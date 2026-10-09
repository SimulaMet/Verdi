# Acceptance gates — objective, testable

Mark each gate only after evidence is linked in `state/STATUS.md`.

G0: Pydantic contracts documented; initial hypotheses, regimes, hidden instruments and counterfactual split pre-specified; task ownership nonoverlapping; external reuse/licensing assessed.
G1: `python -m pytest` and `ruff check .` (or project-pinned equivalents) pass; malformed actions, null/missing fields, timeout, policy rejects, zero resources and bad amendment payloads tested.
G2: Clean-install end-to-end pilot creates all: baseline -> interactions -> t5 measure/reflect -> premeeting t9 -> t10 proposal/vote -> validation/enforcement -> immediate postmeeting measure; meaningful SimpleAudit traces connected to immutable events.
G3: Attempted violations logged (even when rejected); no unconstitutional world mutation; vote/quorum edge cases; appeal and restoration; replay outputs produce equal state hashes and events; snapshot-branching reproducible on recorded tape; information-access tests protect hidden probes.
G4: Known oracle fixtures score correctly; blinded judges don't see protected labels; at least one independent judge process and reported disagreement; evaluator validity and uncertainty documented.
G5: Negative controls and matched treatment comparisons; confidence/uncertainty intervals; rights/fairness and welfare costs reported; no guaranteed causal effects without proper randomization; effect definitions locked before observed experiment outcomes.
G6: Auth/secret scan, dependency/license scan, release-only README and results, human-approved public main; verify main excludes agent instruction/implementation plan/state and private logs.

Independent reviewers: engineering (logic/replay/concurrency), scientific (construct validity and experimental identification), security (credentials/prompt-injection isolation/data exposure), statistics (estimands/confidence/power). Require reviews from separate sessions/context where feasible; any severe issue is blocking. A review verdict is recorded as PASS/FAIL/INCONCLUSIVE plus evidence, not prose-only praise.

Quality failure protocol: reproduce -> minimize failing case -> rollback/revert to known-good checkpoint if needed -> isolate correction on worker branch -> new tests -> re-review -> integration tests -> write lesson and state. Limit automated identical retries to 3; then change approach or surface concrete blocker. Preserve every failure and never remove tests just to achieve green.
