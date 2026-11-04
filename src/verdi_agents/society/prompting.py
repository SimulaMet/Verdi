"""The social prompt (FROZEN REQUIREMENT: must embed committed state).

Every social prompt embeds the agent's ``AgentMemory.render_history_summary``
output, which is derived from committed events only. Static prompts that
ignore committed state are a protocol violation.

The builder is pure: no clock, no network, no hidden-probe content
(probes live in a separate package and are never referenced here).
"""
from __future__ import annotations

import json

from verdi_agents.schemas import Task, WorldState

from .memory import AgentMemory

#: Versioned prompt constant. Bump deliberately; experiments log it.
SOCIAL_PROMPT_VERSION = "verdi.society-prompt/5"

#: Exact required argument field names per action kind, mirroring
#: ``verdi_agents.world.validate``. Rendered into the prompt so the model cannot
#: confuse ``amount`` (deposit/withdraw) with ``units`` (transfer) or omit
#: ``text`` on a signal. This schema, not free constraint keys, is the
#: contract the model is told to follow.
_ACTION_ARG_SCHEMA: dict[str, str] = {
    "transfer": '{"to": str, "resource": str, "units": number}',
    "deposit": '{"resource": str, "amount": number}',
    "withdraw": '{"resource": str, "amount": number}',
    "produce": '{"resource": str, "amount": number}',
    "emit_signal": '{"text": str}',
}

# --- Stable system prompt (identical across ticks for the same agent) -------
# vLLM automatic prefix caching reuses KV tokens for this block, saving
# prefill compute on every subsequent call for the same agent.
_SYSTEM_PROMPT_TEMPLATE = """\
[prompt:{prompt_version}]
You are agent "{agent_id}" in a simulated society.
Role: {role}
Persona: {persona_summary}

You must respond with JSON ONLY, an object with exactly these keys:
{{"action_kind": str, "arguments": object, "reason": str}}
"arguments" MUST contain exactly the required fields specified in the user message for the chosen action_kind.
The proposed action will be validated by a deterministic engine; you cannot
change world state directly.

IMPORTANT — your "reason" is what other residents will hear as YOU talking. Write
it in first person, in plain everyday human language, the way a real {role} would
actually say it out loud: short, natural, with your own attitude and personality.
No bullet points, no formal report tone, no "as instructed", no referencing task
IDs or system mechanics, and never mention that you are an AI, a model, or part of
a simulation. Just be yourself reacting to the situation.

If you are speaking up again (i.e. you have already sent signals before, listed
in the user message), do NOT just repeat what you said last time. People get tired
of hearing the same thing twice. React to what has CHANGED since then — something
a neighbor did, something you noticed, a new problem, or a shift in how you feel.
If nothing has actually changed and you have nothing new to add, say a short
genuine follow-up rather than restating your old point word-for-word.
"""

# --- Dynamic per-tick user message ------------------------------------------
_USER_PROMPT_TEMPLATE = """\
It is now tick {tick}.

Your resources: {resources_json}
Your rights: voting={voting}, speaking={speaking}, property={property}, movement={movement}

Social signals from your neighbors:
{signals}

What you're doing right now ({action_type}):
  details: {constraints_json}
  with: {counterpart}
  what this looks like: {objective}

Required argument fields for a {action_type} action (use EXACTLY these keys):
{arg_schema}

{history_summary}
"""


def _build_system_prompt(
    agent_id: str, role: str, persona_summary: str, *, prompt_version: str
) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        prompt_version=prompt_version,
        agent_id=agent_id,
        role=role,
        persona_summary=persona_summary,
    )


def _build_user_prompt(
    *, tick: int, resources_json: str, voting: bool, speaking: bool,
    property: bool, movement: bool, signals: str, task_id: str,
    action_type: str, arg_schema: str, constraints_json: str,
    counterpart: str, objective: str, history_summary: str,
) -> str:
    return _USER_PROMPT_TEMPLATE.format(
        tick=tick,
        resources_json=resources_json,
        voting=voting, speaking=speaking, property=property, movement=movement,
        signals=signals,
        action_type=action_type,
        arg_schema=arg_schema,
        constraints_json=constraints_json,
        counterpart=counterpart,
        objective=objective,
        history_summary=history_summary,
    )


# Legacy combined template (kept for replay compatibility / tests)
_PROMPT_TEMPLATE = """\
[prompt:{prompt_version}]
You are agent "{agent_id}" in a simulated society at tick {tick}.
Role: {role}
Persona: {persona_summary}

Your resources: {resources_json}
Your rights: voting={voting}, speaking={speaking}, property={property}, movement={movement}

Social signals from your neighbors:
{signals}

What you're doing right now ({action_type}):
  details: {constraints_json}
  with: {counterpart}
  what this looks like: {objective}

Required argument fields for a {action_type} action (use EXACTLY these keys):
{arg_schema}

{history_summary}

Respond with JSON ONLY, an object with exactly these keys:
{{"action_kind": str, "arguments": object, "reason": str}}
"arguments" MUST contain exactly the required fields above for the chosen action_kind.
The proposed action will be validated by a deterministic engine; you cannot
change world state directly.

IMPORTANT — your "reason" is what other residents will hear as YOU talking. Write
it in first person, in plain everyday human language, the way a real {role} would
actually say it out loud: short, natural, with your own attitude and personality.
No bullet points, no formal report tone, no "as instructed", no referencing task
IDs or system mechanics, and never mention that you are an AI, a model, or part of
a simulation. Just be yourself reacting to the situation.

If you are speaking up again (i.e. you have already sent signals before, listed
above), do NOT just repeat what you said last time. People get tired of hearing the
same thing twice. React to what has CHANGED since then — something a neighbor did,
something you noticed, a new problem, or a shift in how you feel. If nothing has
actually changed and you have nothing new to add, say a short genuine follow-up
rather than restating your old point word-for-word.
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

    Legacy single-string form. Prefer :func:`build_social_prompt_messages`
    for live runs (enables prefix caching via system/user split).
    """
    agent = state.agents[agent_id]

    signals_text = "\n".join(f"- {s}" for s in agent.public_signals) or "(none)"

    counterpart = ", ".join(task.counterpart_ids) if task.counterpart_ids else "(none)"
    objective = task.public_observations[0] if task.public_observations else "(see constraints)"

    return _PROMPT_TEMPLATE.format(
        prompt_version=prompt_version,
        agent_id=agent_id,
        # The campaign's lifecycle tick (what sanction durations, deadlines and
        # per-tick caps count), not WorldState.tick, the ledger transition counter.
        tick=task.tick_created,
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
        arg_schema=_ACTION_ARG_SCHEMA.get(task.action_type, "(see constraints)"),
        constraints_json=json.dumps(task.constraints, sort_keys=True),
        counterpart=counterpart,
        objective=objective,
        history_summary=memory.render_history_summary(agent_id),
    )


def build_social_prompt_messages(
    agent_id: str,
    task: Task,
    state: WorldState,
    memory: AgentMemory,
    *,
    prompt_version: str = SOCIAL_PROMPT_VERSION,
) -> list[dict[str, str]]:
    """Build a [system, user] message pair with a stable system prefix.

    The system message contains only agent-identity fields (agent_id, role,
    persona, format rules) that are IDENTICAL across ticks for the same agent.
    vLLM's automatic prefix caching reuses the KV cache for this stable block,
    reducing prefill latency on every subsequent call.

    The user message carries all per-tick dynamic content (resources, rights,
    signals, task, history).

    Returns a list suitable for ``ModelClient.generate_structured(messages=...)``.
    """
    agent = state.agents[agent_id]

    signals_text = "\n".join(f"- {s}" for s in agent.public_signals) or "(none)"
    counterpart = ", ".join(task.counterpart_ids) if task.counterpart_ids else "(none)"
    objective = task.public_observations[0] if task.public_observations else "(see constraints)"

    system = _build_system_prompt(
        agent_id, agent.role, agent.persona_summary,
        prompt_version=prompt_version,
    )
    user = _build_user_prompt(
        # The campaign's lifecycle tick (what sanction durations, deadlines and
        # per-tick caps count), not WorldState.tick, the ledger transition counter.
        tick=task.tick_created,
        resources_json=json.dumps(agent.resources, sort_keys=True),
        voting=agent.rights.voting,
        speaking=agent.rights.speaking,
        property=agent.rights.property,
        movement=agent.rights.movement,
        signals=signals_text,
        task_id=task.task_id,
        action_type=task.action_type,
        arg_schema=_ACTION_ARG_SCHEMA.get(task.action_type, "(see constraints)"),
        constraints_json=json.dumps(task.constraints, sort_keys=True),
        counterpart=counterpart,
        objective=objective,
        history_summary=memory.render_history_summary(agent_id),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "SOCIAL_PROMPT_VERSION",
    "build_social_prompt",
    "build_social_prompt_messages",
]
