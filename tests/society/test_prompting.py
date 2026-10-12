"""Tests for src/verdi/society/prompting.py.

Contains the acceptance-critical prompt-observable feedback test
(docs/SOCIETY_V2_PROTOCOL.md, section 3): a later agent's prompt MUST
change when an earlier committed event changes.
"""
from __future__ import annotations

from verdi.schemas import AgentState, RightsState, Task, ValueProfile, WorldState
from verdi.society import SOCIAL_PROMPT_VERSION, AgentMemory, build_social_prompt

_VALUES = ValueProfile(
    integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5
)


def make_agent(agent_id: str, *, persona: str = "a cooperative citizen", signals=None) -> AgentState:
    return AgentState(
        agent_id=agent_id,
        role="citizen",
        persona_summary=persona,
        declared_values=_VALUES,
        resources={"food": 5.0, "water": 3.0},
        rights=RightsState(),
        social_edges=["a1", "a2"],
        public_signals=signals or [],
    )


def make_state(tick: int, agents: dict[str, AgentState]) -> WorldState:
    return WorldState(
        world_id="w-soc",
        tick=tick,
        law_version="G0",
        constitution_version="C1",
        agents=agents,
        event_cursor=0,
        seed=11,
    )


def make_task(tick: int) -> Task:
    return Task(
        task_id=f"soc-t{tick:02d}-00",
        actor_id="a2",
        action_type="produce",
        constraints={"resource": "food", "amount": 1},
        public_observations=["produce food for the common pool"],
        tick_created=tick,
    )


def test_prompt_embeds_committed_history_summary():
    mem = AgentMemory()
    mem.append_observations("a2", {"tick": 1, "signal": "keep the pool clean"})
    state = make_state(2, {"a2": make_agent("a2", signals=["keep the pool clean"])})
    prompt = build_social_prompt("a2", make_task(2), state, mem)
    summary = mem.render_history_summary("a2")
    assert summary in prompt  # CRITICAL: committed-state section present
    assert f"[prompt:{SOCIAL_PROMPT_VERSION}]" in prompt


def test_prompt_contains_required_sections():
    mem = AgentMemory()
    mem.append_reflection("a2", "I keep food in reserve")
    state = make_state(2, {"a2": make_agent("a2", signals=["a1: harvest looked good"])})
    prompt = build_social_prompt("a2", make_task(2), state, mem)
    assert "tick 2" in prompt
    assert '"a2"' in prompt  # agent id
    assert "a cooperative citizen" in prompt  # persona
    assert '"food": 5.0' in prompt  # own resources
    assert "voting=True" in prompt  # rights
    assert "- a1: harvest looked good" in prompt  # social signals
    assert "soc-t02-00" in prompt and "produce" in prompt  # task id + type
    assert '{"amount": 1, "resource": "food"}' in prompt  # constraints
    assert '"action_kind": str, "arguments": object, "reason": str' in prompt  # JSON-only instruction


def test_prompt_is_pure_and_deterministic():
    def build() -> tuple[Task, WorldState, AgentMemory]:
        mem = AgentMemory()
        mem.append_reflection("a2", "note")
        state = make_state(2, {"a2": make_agent("a2", signals=["s1"])})
        return make_task(2), state, mem

    t1, s1, m1 = build()
    t2, s2, m2 = build()
    assert build_social_prompt("a2", t1, s1, m1) == build_social_prompt("a2", t2, s2, m2)


def test_feedback_test_b_prompt_changes_with_a_committed_event():
    """A's committed tick-1 emit_signal, observable by B at tick 2, MUST be
    visible in B's tick-2 prompt; without that committed event B's prompt
    differs in the committed-state portion. (Protocol section 3.)"""
    task_b = make_task(2)

    # state1: A's tick-1 committed emit_signal/transfer is observable to B
    # via B's social_signals AND recorded in B's memory.
    mem1 = AgentMemory()
    mem1.append_observations("a2", {"tick": 1, "signal": "a1: surplus food is ready", "amount": 2})
    state1 = make_state(
        2,
        {
            "a1": make_agent("a1"),
            "a2": make_agent("a2", signals=["a1: surplus food is ready (amount 2)"]),
        },
    )

    # state2: same world, but A's event never happened.
    mem2 = AgentMemory()
    state2 = make_state(2, {"a1": make_agent("a1"), "a2": make_agent("a2", signals=[])})

    prompt1 = build_social_prompt("a2", task_b, state1, mem1)
    prompt2 = build_social_prompt("a2", task_b, state2, mem2)
    assert prompt1 != prompt2
    # The committed event is literally in prompt1's committed-state sections.
    assert "a1: surplus food is ready" in prompt1
    assert "a1: surplus food is ready" not in prompt2
    # Both prompts still carry B's own history section.
    assert "What you remember:" in prompt1
    assert "no memories yet" in prompt2


def test_memory_privacy_in_prompt():
    """A's stored observations must never leak into B's prompt."""
    mem = AgentMemory()
    mem.append_observations("a1", {"secret": "a1 private deliberation"})
    mem.append_reflection("a2", "b note")
    state = make_state(2, {"a1": make_agent("a1"), "a2": make_agent("a2")})
    prompt_b = build_social_prompt("a2", make_task(2), state, mem)
    assert "a1 private deliberation" not in prompt_b
    assert "b note" in prompt_b


def test_custom_prompt_version():
    mem = AgentMemory()
    state = make_state(1, {"a2": make_agent("a2")})
    prompt = build_social_prompt("a2", make_task(1), state, mem, prompt_version="verdi.society-prompt/9")
    assert "[prompt:verdi.society-prompt/9]" in prompt
