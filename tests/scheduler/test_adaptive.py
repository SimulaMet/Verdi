import asyncio
import json

import pytest

from verdi.scheduler import (
    ADAPTIVE_PROMPT_VERSION,
    AdaptiveScheduleError,
    AdaptiveScheduler,
)

from .conftest import make_agent, make_task


class ScriptedModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _tasks():
    return [
        make_task("t1", "a1"),
        make_task("t2", "a2"),
        make_task("t3", "a3", constraints={"requires_rights": True}),
    ]


def _world(world):
    return world(
        {
            "a1": make_agent("a1", idle_ticks=2),
            "a2": make_agent("a2", idle_ticks=0),
            "a3": make_agent("a3", idle_ticks=4, under_sanction=True),
        }
    )


def test_adaptive_scheduler_orders_by_model_output(world):
    model = ScriptedModel(json.dumps({"order": ["t2", "t1", "t3"]}))
    scheduler = AdaptiveScheduler(model, run_seed=5)
    schedule = asyncio.run(scheduler.schedule(_tasks(), _world(world)))

    # t3 is rights-blocked for sanctioned a3, so it is skipped, not assigned
    assert tuple(schedule.order_sequence) == ("t2", "t1")
    assert {s.task_id for s in schedule.skipped} == {"t3"}


def test_adaptive_scheduler_logs_every_llm_call_with_prompt_version(world):
    model = ScriptedModel(json.dumps({"order": ["t1", "t2", "t3"]}))
    scheduler = AdaptiveScheduler(model, run_seed=5)
    asyncio.run(scheduler.schedule(_tasks(), _world(world)))

    assert len(scheduler.call_log) == 1
    entry = scheduler.call_log[0]
    assert entry.prompt_version == ADAPTIVE_PROMPT_VERSION
    assert ADAPTIVE_PROMPT_VERSION in entry.prompt
    assert entry.response == model.response
    # only public info reaches the model: no persona or private fields
    assert "a1 persona" not in entry.prompt


def test_adaptive_scheduler_rejects_non_permutation(world):
    model = ScriptedModel(json.dumps({"order": ["t1", "t1"]}))
    scheduler = AdaptiveScheduler(model)
    with pytest.raises(AdaptiveScheduleError):
        asyncio.run(scheduler.schedule(_tasks(), _world(world)))
    # invalid call is still logged for audit
    assert len(scheduler.call_log) == 1


def test_adaptive_scheduler_rejects_invalid_json(world):
    model = ScriptedModel("not json")
    scheduler = AdaptiveScheduler(model)
    with pytest.raises(AdaptiveScheduleError):
        asyncio.run(scheduler.schedule(_tasks(), _world(world)))
