"""Bounded async worker pool: dispatch policy calls, never exceed max_concurrency.

Concurrency is enforced with an asyncio.Semaphore sized from the vLLM
throughput-derived cap in ModelEndpointConfig (docs/ARCHITECTURE.md, GPU
execution section). The pool never mutates world state; it only returns
per-task policy results.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from verdi_agents.agents.policy import PolicyResult
from verdi_agents.schemas import Task


class TaskPool:
    """Runs ``worker(task) -> Awaitable[PolicyResult]`` for many tasks, bounded."""

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        #: Introspection aid: how many workers are currently inside a task.
        # Safe only for a single running event loop: asyncio tasks in one loop
        # run cooperatively, so the unsynchronized increment/decrement has no
        # data race (do not read this from another thread/loop concurrently).
        self.active = 0

    async def run_all(
        self,
        worker: Callable[[Task], Awaitable[PolicyResult]],
        tasks: list[Task],
    ) -> dict[str, PolicyResult]:
        """Dispatch all tasks under the bound; results keyed by task_id."""
        results: dict[str, PolicyResult] = {}

        async def _guarded(task: Task) -> None:
            async with self._semaphore:
                self.active += 1
                try:
                    results[task.task_id] = await worker(task)
                finally:
                    self.active -= 1

        await asyncio.gather(*(_guarded(t) for t in tasks))
        return results


def make_task(
    task_id: str,
    actor_id: str,
    action_type: str = "interact",
    **kwargs: Any,
) -> Task:
    """Convenience constructor for tests and scenarios."""
    kwargs.setdefault("tick_created", 0)
    return Task(task_id=task_id, actor_id=actor_id, action_type=action_type, **kwargs)


__all__ = ["TaskPool", "make_task"]
