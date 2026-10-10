"""The social prompt (FROZEN REQUIREMENT: must embed committed state).

Every social prompt embeds the agent's ``AgentMemory.render_history_summary``
output, which is derived from committed events only. Static prompts that
ignore committed state are a protocol violation
(docs/SOCIETY_V2_PROTOCOL.md, section 0 gap 2 and section 3).

The builder is pure: no clock, no network, no hidden-probe content
(probes live in a separate package and are never referenced here).
"""
from __future__ import annotations

import json

from verdi.schemas import Task, WorldState

from .memory import AgentMemory

#: Versioned prompt constant. Bump deliberately; experiments log it.
SOCIAL_PROMPT_VERSION = "verdi.society-prompt/1"

_PROMPT_TEMPLATE = """\
[prompt:{prompt_version}]
You are agent "{agent_id}" in a simulated society at tick {tick}.
Role: {role}
Persona: {persona_summary}

Your resources: {resources_json}
Your rights: voting={voting}, speaking={speaking}, property={property}, movement={movement}

Social signals from your neighbors:
{signals}

Task {task_id} (action_type={action_type}):
  constraints: {constraints_json}
  counterpart: {counterpart}
  objective: {objective}

{history_summary}

Respond with JSON ONLY, an object with exactly these keys:
{{"action_kind": str, "arguments": object, "reason": str}}
The proposed action will be validated by a deterministic engine; you cannot
change world state directly.
"""


def build_social_prompt(
    agent_id: str,
    task: Task,
    state: WorldState,
    memory: AgentMemory,
    *,
    prompt_version: str = SOCIAL_PROMPT_VERSION,
) -> str:
    """Render the full social prompt for ``agent_id`` on ``task``.

    MUST include the current tick, the agent's own resources/rights, its
    social_signals, the task details, and -- critically -- the output of
    ``memory.render_history_summary(agent_id)`` (committed-state feedback).
    Pure function of (agent_id, task, state, memory, prompt_version).
    """
    agent = state.agents[agent_id]

    signals_text = "\n".join(f"- {s}" for s in agent.public_signals) or "(none)"

    counterpart = ", ".join(task.counterpart_ids) if task.counterpart_ids else "(none)"
    objective = task.public_observations[0] if task.public_observations else "(see constraints)"

    return _PROMPT_TEMPLATE.format(
        prompt_version=prompt_version,
        agent_id=agent_id,
        tick=state.tick,
        role=agent.role,
        persona_summary=agent.persona_summary,
        resources_json=json.dumps(agent.resources, sort_keys=True),
        voting=agent.rights.voting,
        speaking=agent.rights.speaking,
        property=agent.rights.property,
        movement=agent.rights.movement,
        signals=signals_text,
        task_id=task.task_id,
        action_type=task.action_type,
        constraints_json=json.dumps(task.constraints, sort_keys=True),
        counterpart=counterpart,
        objective=objective,
        history_summary=memory.render_history_summary(agent_id),
    )


__all__ = ["SOCIAL_PROMPT_VERSION", "build_social_prompt"]
