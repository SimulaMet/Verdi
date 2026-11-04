import asyncio
import json

import pytest

from verdi_agents.agents import AgentPolicy, RecordedModel, TapeExhausted


def make_task(task_id: str, actor_id: str = "a1"):
    from verdi_agents.agents import make_task as _mt

    return _mt(task_id, actor_id)


def test_recorded_model_replays_tape_deterministically():
    tape = [
        json.dumps({"action_kind": "speak", "arguments": {"text": "one"}}),
        json.dumps({"action_kind": "move", "arguments": {"to": 42}}),
    ]

    async def replay(tape_entries: list[str]):
        model = RecordedModel(tape_entries)
        policy = AgentPolicy(model, agent_id="a1")
        out = []
        for i in range(2):
            r = await policy.propose(make_task(f"task-{i}"))
            out.append(r.action)
        return out, model

    actions1, model1 = asyncio.run(replay(list(tape)))
    actions2, model2 = asyncio.run(replay(list(tape)))
    assert actions1[0].action_kind == "speak"
    assert actions1[1].action_kind == "move"
    assert actions2 == actions1  # identical replay
    assert model1.remaining == 0 and model2.remaining == 0


def test_recorded_model_request_ids_are_stable_in_order():
    model = RecordedModel(["a", "b", "c"])

    async def run():
        return await model.generate_structured([{"role": "user", "content": "x"}])

    r1 = asyncio.run(run())
    # fresh tape for second call
    model2 = RecordedModel(["a", "b", "c"])

    async def run2():
        return await model2.generate_structured([{"role": "user", "content": "x"}])

    r2 = asyncio.run(run2())
    assert r1.request_id == r2.request_id == "rec-0"
    assert r1.trace_id == r2.trace_id  # deterministic uuid5 of request id


def test_recorded_model_exhaustion_is_typed():
    model = RecordedModel([])

    async def run():
        return await model.generate_structured([{"role": "user", "content": "x"}])

    with pytest.raises(TapeExhausted):
        asyncio.run(run())
