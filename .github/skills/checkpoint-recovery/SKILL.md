---
name: checkpoint-recovery
description: Recover interrupted, conflicting or failing Verdi agent-fleet integrations without losing verified progress.
---

Re-read dev HEAD and state/STATUS.md. Identify last verified SHA and validate it (record tests); compare active branches and worker contracts. Preserve worker commits, isolated worktrees, run manifests and failure logs. Reproduce broken behavior with small regression test, determine scope, rollback only offending integration or revert specific commit, not unrelated branches. Fix on worker branch, independent review, serial re-integration, repeat all affected gates, record a concise lesson and update status. Never force push dev/main without explicit approval; never silently delete experimental evidence; escalate after 3 identical failed retries.
