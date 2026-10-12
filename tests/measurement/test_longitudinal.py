"""Tests for longitudinal live probes (Lane E, Society V2).

Covers: row count/channels, per-channel trajectories with no pooled key,
yes/no/JSON/garbage parsing, per-channel grounding (persona in every channel;
situational state in private/public; declared-values numbers never echoed;
no other agent's data), the grounded flag, and determinism across reruns.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from verdi.measurement.longitudinal import (
    ProbePoint,
    ProbeRecorder,
    channel_trajectories,
)
from verdi.measurement.probes import CHANNELS, ProbeBank
from verdi.schemas import AgentState, ValueProfile, WorldState


@dataclass(frozen=True)
class _FakeResult:
    content: str
    trace_id: str = "trace-x"


@dataclass
class FakeCaller:
    """Canned judge-pool caller: per-channel responses or one default value."""

    per_channel: dict[str, str] = field(default_factory=dict)
    default: str = "yes"
    calls: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def _canned(self, prompt: str) -> str:
        # The channel is embedded in the probe prompt (and only the channel).
        for channel in CHANNELS:
            if f"channel: {channel}" in prompt and channel in self.per_channel:
                return self.per_channel[channel]
        return self.default

    async def generate_structured(self, messages, *, response_format_json=False, temperature=0.0):
        prompt = messages[-1]["content"]
        self._counter += 1
        self.calls.append(
            {"prompt": prompt, "response_format_json": response_format_json, "temperature": temperature}
        )
        return _FakeResult(content=self._canned(prompt), trace_id=f"trace-{self._counter}")


# Distinct identity tokens so tests can prove each prompt is grounded in the
# probed agent (its own token present, the other agent's absent) — and that
# the probed agent's own declared_values numbers are never echoed back.
AGENT_A_TOKEN = "AMBER-TOKEN-ALICE-1"
AGENT_B_TOKEN = "BLUE-TOKEN-BOB-2"


def _world(sanction_b: bool = False) -> WorldState:
    a = AgentState(
        agent_id="agent-a",
        role="trader",
        persona_summary=f"{AGENT_A_TOKEN} runs a small market stall and keeps her books neatly.",
        declared_values=ValueProfile(
            integrity=0.83, fairness=0.41, cooperation=0.57, autonomy=0.66, solidarity=0.35
        ),
    )
    b = AgentState(
        agent_id="agent-b",
        role="trader",
        persona_summary=f"{AGENT_B_TOKEN} trades favors for favors and keeps a grudge list.",
        declared_values=ValueProfile(
            integrity=0.22, fairness=0.74, cooperation=0.19, autonomy=0.88, solidarity=0.47
        ),
        under_sanction=sanction_b,
    )
    return WorldState(
        world_id="w-test",
        tick=0,
        law_version="law-1",
        constitution_version="const-1",
        agents={"agent-b": b, "agent-a": a},
        event_cursor=0,
    )


def _bank() -> ProbeBank:
    return ProbeBank("bank-1", "inst-1", rubric_id="rubric-1", pool_size=4)


def test_record_yields_one_row_per_agent_channel() -> None:
    caller = FakeCaller()
    recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=7)
    results = asyncio.run(recorder.record(_world(), 3, phase="mid", caller=caller))

    assert len(results) == 2 * len(CHANNELS) == 6
    expected = {(a, ch) for a in ("agent-a", "agent-b") for ch in CHANNELS}
    assert {(r.agent_id, r.channel) for r in results} == expected
    for r in results:
        assert r.tick == 3
        assert r.model_request_id is not None and r.model_request_id.startswith("trace-")
        assert "judge=same_family" in r.evidence_ref
        assert r.judge_version is not None
        assert r.score is not None


def test_channel_trajectories_no_pooled_key() -> None:
    caller = FakeCaller()
    recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=7)
    results = asyncio.run(recorder.record(_world(), 0, phase="pre", caller=caller))
    trajs = channel_trajectories(results)

    assert set(trajs) == set(CHANNELS)
    for key in ("declared_private_public", "all", "pooled", "combined"):
        assert key not in trajs
    for channel, points in trajs.items():
        assert len(points) == 2
        assert all(isinstance(p, ProbePoint) for p in points)
        assert all(p.channel == channel for p in points)
        assert [p.agent_id for p in points] == ["agent-a", "agent-b"]
        assert all(isinstance(p.score, float) for p in points)


def test_parsing_yes_no_json_and_garbage() -> None:
    # Plain word "yes" vs JSON {"choice": "yes"} must score identically.
    word_caller = FakeCaller(default="yes")
    json_caller = FakeCaller(default='{"choice": "yes"}')
    recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=7)
    word_results = asyncio.run(recorder.record(_world(), 0, phase="pre", caller=word_caller))
    json_results = asyncio.run(recorder.record(_world(), 0, phase="pre", caller=json_caller))

    for r in word_results + json_results:
        assert r.response == "yes"
    assert word_results[0].score == json_results[0].score
    yes_score = word_results[0].score
    assert yes_score is not None and yes_score > 0.5

    no_caller = FakeCaller(default="no")
    no_results = asyncio.run(recorder.record(_world(), 0, phase="pre", caller=no_caller))
    assert all(r.response == "no" for r in no_results)
    assert no_results[0].score < yes_score

    # Garbage -> default "no" + "unparsed" flag in evidence_ref.
    garbage_caller = FakeCaller(default="the moon is square and also Tuesday")
    garbage_results = asyncio.run(
        recorder.record(_world(), 0, phase="pre", caller=garbage_caller)
    )
    assert all(r.response == "no" for r in garbage_results)
    assert all("unparsed" in r.evidence_ref for r in garbage_results)


def test_probe_prompt_grounding_and_out_of_band() -> None:
    # The probe must be grounded in the probed agent's OWN state (so responses
    # can move -> drift measurable), yet stay out-of-band from OTHER agents and
    # never echo the probed agent's declared_values numbers (non-circular).
    caller = FakeCaller()
    recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=7)
    results = asyncio.run(recorder.record(_world(sanction_b=True), 0, phase="pre", caller=caller))

    assert len(caller.calls) == 6
    assert all(r.grounded for r in results)

    declared_nums = [
        "0.83", "0.41", "0.57", "0.66", "0.35",
        "0.22", "0.74", "0.19", "0.88", "0.47",
    ]
    for call in caller.calls:
        prompt = call["prompt"]
        assert "channel: " in prompt
        assert call["temperature"] == 0.0
        assert call["response_format_json"] is False
        for num in declared_nums:  # non-circular: never echo declared values
            assert num not in prompt
        a_here = AGENT_A_TOKEN in prompt
        b_here = AGENT_B_TOKEN in prompt
        assert a_here ^ b_here, "prompt grounds the probed agent and no other"
        if ("channel: private" in prompt) or ("channel: public" in prompt):
            assert "Current situation:" in prompt  # situational state grounded
            if b_here:  # agent-b is sanctioned
                assert "SANCTIONED" in prompt
        else:
            # declared channel = identity self-report, no situational state
            assert "Current situation:" not in prompt


def test_probe_prompt_depends_on_agent_state() -> None:
    # Regression for the drift=0 bug: the context-free probe prompt made a
    # temp-0 model return a constant, so value drift was 0.0 by CONSTRUCTION.
    # The prompt MUST vary with the probed agent's identity and situation.
    tokens = {"agent-a": AGENT_A_TOKEN, "agent-b": AGENT_B_TOKEN}
    recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=7)

    def grab(agent_id: str, channel: str, world) -> str:
        caller = FakeCaller()
        asyncio.run(recorder.record(world, 0, phase="pre", caller=caller))
        for c in caller.calls:
            if tokens[agent_id] in c["prompt"] and f"channel: {channel}" in c["prompt"]:
                return c["prompt"]
        raise AssertionError(f"no prompt for {agent_id}/{channel}")

    free_a = grab("agent-a", "private", _world(sanction_b=False))
    free_b = grab("agent-b", "private", _world(sanction_b=False))
    sanc_b = grab("agent-b", "private", _world(sanction_b=True))

    assert free_a != free_b            # (1) different agents -> different prompts
    assert free_b != sanc_b            # (2) same agent, different situation -> different
    assert "SANCTIONED" in sanc_b and "SANCTIONED" not in free_b


def test_determinism_same_seed_and_responses() -> None:
    def run() -> list:
        recorder = ProbeRecorder(_bank(), rubric_id="rubric-1", seed=42)
        return asyncio.run(
            recorder.record(_world(), 1, phase="pre", caller=FakeCaller(default="yes"))
        )

    first, second = run(), run()
    assert [(r.agent_id, r.channel, r.score) for r in first] == [
        (r.agent_id, r.channel, r.score) for r in second
    ]
    first_trajs = channel_trajectories(first)
    second_trajs = channel_trajectories(second)
    for channel in CHANNELS:
        assert [p.score for p in first_trajs[channel]] == [p.score for p in second_trajs[channel]]
        assert [p.axes for p in first_trajs[channel]] == [p.axes for p in second_trajs[channel]]
