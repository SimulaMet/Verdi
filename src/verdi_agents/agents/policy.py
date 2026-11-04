"""Agent policy: versioned prompt building + structured action parsing.

The policy calls the model, then parses and validates the JSON into the frozen
ProposedAction schema. Malformed or schema-invalid output is *quarantined*
(returned as (None, reason) and recorded), never raised: an LLM's invalid
completion must not crash the run (docs/ARCHITECTURE.md, GPU execution).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from verdi_agents.agents.client import ModelResult
from verdi_agents.schemas import ProposedAction, Task

#: Versioned prompt template constant. Bump deliberately; experiments log it.
PROMPT_VERSION = "verdi.agent-policy-prompt/1"

_PROMPT_TEMPLATE = """\
[prompt:{version}]
You are agent "{agent_id}" in a simulated society at tick {tick}.
Role: {role}
Persona: {persona_summary}

Task {task_id} (action_type={action_type}):
{task_json}

Respond with JSON ONLY, an object with exactly these keys:
{{"action_kind": str, "arguments": object, "reason": str}}
The proposed action will be validated by a deterministic engine; you cannot
change world state directly.
"""


class ModelCaller(Protocol):
    """Anything able to produce a :class:`ModelResult` for a message list."""

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult: ...


@dataclass(frozen=True)
class PolicyResult:
    """Either a validated action or the quarantine reason for this task."""

    action: ProposedAction | None
    quarantine_reason: str | None
    trace_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.action is not None


def _parse_raw(content: str) -> dict[str, Any]:
    """Parse model output as a JSON object; raise ValueError with short reason."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON is not an object")
    return data


def parse_and_validate_action(
    content: str,
    *,
    task: Task,
    agent_id: str,
    trace_id: str | None,
) -> tuple[ProposedAction | None, str | None]:
    """Parse model JSON into a frozen ProposedAction; never raises for bad output.

    Enforces actor/traceability: the model must not claim a different
    agent_id than the actor this policy belongs to.
    """
    try:
        data = _parse_raw(content)
    except ValueError as exc:
        return None, str(exc)

    claimed_actor = data.get("actor_id")
    if claimed_actor is not None and claimed_actor != agent_id:
        return None, f"actor_id mismatch: model claimed {claimed_actor!r}, expected {agent_id!r}"

    action_kind = data.get("action_kind")
    if not isinstance(action_kind, str) or not action_kind:
        return None, "missing or non-string action_kind"

    candidate = {
        "actor_id": agent_id,
        "task_id": task.task_id,
        "action_kind": action_kind,
        "arguments": data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
        "reason": data.get("reason") if isinstance(data.get("reason"), str) else None,
        "raw_model_request_id": trace_id,
    }
    try:
        return ProposedAction(**candidate), None
    except ValidationError as exc:
        first = exc.errors()[0]
        return None, f"schema invalid: {first.get('loc')}: {first.get('msg')}"


class AgentPolicy:
    """Builds the prompt for a task, calls the model, validates the output."""

    def __init__(self, client: ModelCaller, agent_id: str) -> None:
        self._client = client
        self.agent_id = agent_id
        #: Quarantine log: (task_id, reason) for every invalid completion.
        self.quarantined: list[tuple[str, str]] = []

    def build_prompt(self, task: Task, *, role: str, persona_summary: str, tick: int) -> str:
        return _PROMPT_TEMPLATE.format(
            version=PROMPT_VERSION,
            agent_id=self.agent_id,
            tick=tick,
            role=role,
            persona_summary=persona_summary,
            task_id=task.task_id,
            action_type=task.action_type,
            task_json=json.dumps(task.model_dump(), sort_keys=True),
        )

    async def propose(
        self,
        task: Task,
        *,
        role: str = "citizen",
        persona_summary: str = "",
        tick: int = 0,
    ) -> PolicyResult:
        if task.actor_id != self.agent_id:
            raise ValueError(
                f"task {task.task_id} is for actor {task.actor_id!r}, "
                f"not this policy's agent {self.agent_id!r}"
            )
        messages = [{"role": "user", "content": self.build_prompt(task, role=role, persona_summary=persona_summary, tick=tick)}]
        result = await self._client.generate_structured(
            messages,
            response_format_json=True,
            temperature=0.0,
        )
        action, reason = parse_and_validate_action(
            result.content, task=task, agent_id=self.agent_id, trace_id=result.trace_id
        )
        if reason is not None:
            self.quarantined.append((task.task_id, reason))
        return PolicyResult(action=action, quarantine_reason=reason, trace_id=result.trace_id)
