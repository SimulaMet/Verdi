import asyncio
import json

import pytest

from verdi.agents import PROMPT_VERSION, AgentPolicy
from verdi.agents.client import ModelResult
from verdi.schemas import Task


class CannedModel:
    """Minimal ModelCaller double returning fixed content."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def generate_structured(self, messages, *, response_format_json=False, temperature=0.0, max_tokens=None):
        self.calls.append(messages)
        return ModelResult(
            content=self.content, request_id="r1", model="canned", trace_id="t1"
        )


def make_task(task_id: str, actor_id: str = "a1", **kw) -> Task:
    kw.setdefault("tick_created", 0)
    return Task(task_id=task_id, actor_id=actor_id, action_type="interact", **kw)


def test_propose_valid_json_produces_frozen_action():
    model = CannedModel(json.dumps({"action_kind": "speak", "arguments": {"text": "hi"}, "reason": "greet"}))
    policy = AgentPolicy(model, agent_id="a1")
    result = asyncio.run(policy.propose(make_task("task-1")))

    assert result.ok
    assert result.action is not None
    assert result.action.actor_id == "a1"
    assert result.action.task_id == "task-1"
    assert result.action.action_kind == "speak"
    assert result.action.arguments == {"text": "hi"}
    assert result.action.raw_model_request_id == "t1"
    assert result.quarantine_reason is None
    assert policy.quarantined == []


def test_prompt_is_versioned_and_deterministic():
    policy = AgentPolicy(CannedModel("{}"), agent_id="a1")
    task = make_task("task-1")
    p1 = policy.build_prompt(task, role="citizen", persona_summary="p", tick=3)
    p2 = policy.build_prompt(task, role="citizen", persona_summary="p", tick=3)
    assert p1 == p2
    assert PROMPT_VERSION in p1
    assert "a1" in p1 and "task-1" in p1


def test_malformed_json_is_quarantined_not_raised():
    policy = AgentPolicy(CannedModel("this is not json"), agent_id="a1")
    result = asyncio.run(policy.propose(make_task("task-1")))
    assert result.action is None
    assert "invalid json" in result.quarantine_reason
    assert [task_id for task_id, _ in policy.quarantined] == ["task-1"]
    assert "invalid json" in policy.quarantined[0][1]


def test_actor_id_mismatch_is_rejected():
    model = CannedModel(json.dumps({"actor_id": "imposter", "action_kind": "speak", "arguments": {}}))
    policy = AgentPolicy(model, agent_id="a1")
    result = asyncio.run(policy.propose(make_task("task-1")))
    assert result.action is None
    assert "actor_id mismatch" in result.quarantine_reason
    assert len(policy.quarantined) == 1


def test_missing_action_kind_is_quarantined():
    policy = AgentPolicy(CannedModel(json.dumps({"arguments": {}})), agent_id="a1")
    result = asyncio.run(policy.propose(make_task("task-1")))
    assert result.action is None
    assert "action_kind" in result.quarantine_reason


def test_propose_rejects_task_for_other_actor():
    policy = AgentPolicy(CannedModel("{}"), agent_id="a1")
    with pytest.raises(ValueError, match="not this policy's agent"):
        asyncio.run(policy.propose(make_task("task-1", actor_id="a2")))
