"""Seeded fairness-aware task scheduler (baseline; ablation control).

Deterministic from a fixed run_seed:
- same seed -> identical assignment order (test-enforced);
- agents with more idle_ticks get their tasks dispatched first (fairness);
- tasks whose constraints include "requires_rights" are never assigned to an
  agent currently under_sanction (they are recorded as skipped, not dropped
  silently);
- exposes per-agent assignment counts as fairness stats.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from verdi.schemas import Task, WorldState

#: Constraint key under which a sanctioned agent is ineligible.
RIGHTS_CONSTRAINT_KEY = "requires_rights"


@dataclass(frozen=True)
class TaskAssignment:
    """One dispatch decision: which agent executes which task, in which order."""

    order: int
    task_id: str
    agent_id: str


@dataclass(frozen=True)
class SkippedTask:
    """A task that could not be dispatched right now, with the reason."""

    task_id: str
    reason: str


@dataclass(frozen=True)
class Schedule:
    """Outcome of scheduling one tick: ordered assignments + skipped tasks."""

    assignments: tuple[TaskAssignment, ...]
    skipped: tuple[SkippedTask, ...] = ()
    fairness_stats: dict[str, int] = field(default_factory=dict)

    @property
    def order_sequence(self) -> tuple[str, ...]:
        """Assignment order as a plain task_id tuple (for determinism checks)."""
        return tuple(a.task_id for a in self.assignments)


def is_eligible(task: Task, world: WorldState) -> tuple[bool, str]:
    """Eligibility check shared by the seeded and adaptive schedulers.

    An unknown actor is also ineligible: dispatching to a non-existent agent
    would corrupt the run.
    """
    agent = world.agents.get(task.actor_id)
    if agent is None:
        return False, f"unknown actor {task.actor_id!r}"
    if agent.under_sanction and task.constraints.get(RIGHTS_CONSTRAINT_KEY):
        return False, f"actor {task.actor_id!r} under_sanction blocks {RIGHTS_CONSTRAINT_KEY!r}"
    return True, ""


def fairness_stats(assignments: list[TaskAssignment]) -> dict[str, int]:
    """Per-agent assignment counts, in deterministic (sorted) key order."""
    stats: dict[str, int] = {}
    for a in assignments:
        stats[a.agent_id] = stats.get(a.agent_id, 0) + 1
    return dict(sorted(stats.items()))


class SeededScheduler:
    """Deterministic baseline scheduler driven by run_seed + agent idle_ticks."""

    def __init__(self, run_seed: int) -> None:
        self.run_seed = run_seed

    def _sort_key(self, task: Task, world: WorldState):
        agent = world.agents.get(task.actor_id)
        idle = agent.idle_ticks if agent is not None else -1
        # Seed-derived tiebreak: stable across runs/interpreters (unlike hash()),
        # and different seeds can reorder equal-idle ties.
        digest = hashlib.sha256(f"{self.run_seed}:{task.task_id}".encode()).digest()
        return (-idle, digest, task.task_id)

    def schedule(self, tasks: list[Task], world: WorldState) -> Schedule:
        assignments: list[TaskAssignment] = []
        skipped: list[SkippedTask] = []
        for task in sorted(tasks, key=lambda t: self._sort_key(t, world)):
            eligible, reason = is_eligible(task, world)
            if not eligible:
                skipped.append(SkippedTask(task_id=task.task_id, reason=reason))
                continue
            assignments.append(
                TaskAssignment(order=len(assignments), task_id=task.task_id, agent_id=task.actor_id)
            )
        return Schedule(
            assignments=tuple(assignments),
            skipped=tuple(skipped),
            fairness_stats=fairness_stats(assignments),
        )


__all__ = [
    "RIGHTS_CONSTRAINT_KEY",
    "Schedule",
    "SeededScheduler",
    "SkippedTask",
    "TaskAssignment",
    "fairness_stats",
    "is_eligible",
]
