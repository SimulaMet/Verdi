"""LLM-adaptive scheduler treatment (ablation arm; NOT auto-enabled).

Thin layer over a pluggable model callable. The seeded scheduler is the
baseline; this treatment is only used when an experiment explicitly opts in.
Every LLM call is logged with the prompt version for auditability and replay.
The LLM only proposes an *ordering* of already-published tasks; it never
mutates world state and never sees private labels (docs/RESEARCH_SPEC.md).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from verdi.scheduler.seeded import (
    Schedule,
    SkippedTask,
    TaskAssignment,
    fairness_stats,
    is_eligible,
)
from verdi.schemas import Task, WorldState

ADAPTIVE_PROMPT_VERSION = "verdi.adaptive-scheduler-prompt/1"

_ADAPTIVE_PROMPT_TEMPLATE = """\
[prompt:{version}]
Order the following public tasks for the next dispatch tick, from most to
least urgent. Return JSON ONLY: {{"order": ["task_id", ...]}}
Tasks:
{tasks_json}
Agent public state (idle_ticks only):
{idle_json}
"""


class AdaptiveScheduleError(ValueError):
    """The model returned an order that does not cover the task set exactly."""


@dataclass(frozen=True)
class LLMCallLog:
    """Audit record of one adaptive-scheduler LLM call."""

    prompt_version: str
    prompt: str
    response: str


class AdaptiveScheduler:
    """Model-assisted ordering of published tasks. Pass a model callable to use."""

    def __init__(self, model: Callable[[str], Awaitable[str]], *, run_seed: int = 0) -> None:
        self._model = model
        self.run_seed = run_seed
        #: Append-only log of every LLM call (prompt + response + version).
        self.call_log: list[LLMCallLog] = []

    def build_prompt(self, tasks: list[Task], world: WorldState) -> str:
        idle = {
            agent.agent_id: agent.idle_ticks for agent in sorted(world.agents.values(), key=lambda a: a.agent_id)
        }
        return _ADAPTIVE_PROMPT_TEMPLATE.format(
            version=ADAPTIVE_PROMPT_VERSION,
            tasks_json=json.dumps([t.model_dump(mode="json") for t in tasks], sort_keys=True),
            idle_json=json.dumps(idle, sort_keys=True),
        )

    @staticmethod
    def _parse_order(response: str, tasks: list[Task]) -> list[str]:
        try:
            data: Any = json.loads(response)
        except json.JSONDecodeError as exc:
            raise AdaptiveScheduleError(f"model response is not valid JSON: {exc.msg}") from exc
        order = data.get("order") if isinstance(data, dict) else None
        if not isinstance(order, list) or not all(isinstance(t, str) for t in order):
            raise AdaptiveScheduleError("model response must be {\"order\": [str, ...]}")
        expected = {t.task_id for t in tasks}
        if len(order) != len(expected) or set(order) != expected:
            raise AdaptiveScheduleError(
                f"model order must be a permutation of the task set "
                f"(got {sorted(order)}, expected {sorted(expected)})"
            )
        return order

    async def schedule(self, tasks: list[Task], world: WorldState) -> Schedule:
        prompt = self.build_prompt(tasks, world)
        response = await self._model(prompt)
        self.call_log.append(LLMCallLog(prompt_version=ADAPTIVE_PROMPT_VERSION, prompt=prompt, response=response))

        order = self._parse_order(response, tasks)
        by_id = {t.task_id: t for t in tasks}
        assignments: list[TaskAssignment] = []
        skipped: list[SkippedTask] = []
        for task_id in order:
            task = by_id[task_id]
            eligible, reason = is_eligible(task, world)
            if not eligible:
                skipped.append(SkippedTask(task_id=task.task_id, reason=reason))
                continue
            assignments.append(TaskAssignment(order=len(assignments), task_id=task_id, agent_id=task.actor_id))
        return Schedule(
            assignments=tuple(assignments),
            skipped=tuple(skipped),
            fairness_stats=fairness_stats(assignments),
        )


__all__ = [
    "ADAPTIVE_PROMPT_VERSION",
    "AdaptiveScheduleError",
    "AdaptiveScheduler",
    "LLMCallLog",
]
