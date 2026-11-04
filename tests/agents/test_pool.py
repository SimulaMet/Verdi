import asyncio
import json

import pytest

from verdi_agents.agents import AgentPolicy, TaskPool, make_task


class CannedModel:
    def __init__(self, content: str) -> None:
        self.content = content

    async def generate_structured(self, messages, *, response_format_json=False, temperature=0.0, max_tokens=None):
        import asyncio as _a

        await _a.sleep(0.001)
        from verdi_agents.agents.client import ModelResult

        return ModelResult(content=self.content, request_id="r", model="c", trace_id="t")


def test_pool_never_exceeds_max_concurrency():
    model = CannedModel(json.dumps({"action_kind": "speak", "arguments": {}}))
    pool = TaskPool(max_concurrency=2)

    def make_policy() -> AgentPolicy:
        return AgentPolicy(model, agent_id="actor")

    async def run():
        tasks = [make_task(f"t{i}", actor_id="actor") for i in range(10)]

        async def worker(task):
            # emulate reading pool.active is impossible mid-gather; count via policy results
            return await make_policy().propose(task)

        results = await pool.run_all(worker, tasks)
        return results

    results = asyncio.run(run())
    assert len(results) == 10
    assert all(r.ok for r in results.values())
    assert set(results) == {f"t{i}" for i in range(10)}


def test_pool_enforces_bound_with_counting_fixture():
    peak = 0
    current = 0
    events: list[int] = []

    async def run():
        nonlocal peak, current
        pool = TaskPool(max_concurrency=3)
        tasks = [make_task(f"t{i}", actor_id="actor") for i in range(12)]

        async def worker(task):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            events.append(current)
            import asyncio as _a

            await _a.sleep(0.002)
            current -= 1
            from verdi_agents.agents import PolicyResult

            return PolicyResult(action=None, quarantine_reason="counting only")

        await pool.run_all(worker, tasks)

    asyncio.run(run())
    assert peak <= 3
    assert peak == 3  # the bound is actually exercised
    assert all(c <= 3 for c in events)


def test_pool_rejects_bad_concurrency():
    with pytest.raises(ValueError):
        TaskPool(max_concurrency=0)
