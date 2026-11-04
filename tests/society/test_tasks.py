"""Tests for src/verdi_agents/society/tasks.py."""
from __future__ import annotations

import random

from verdi_agents.schemas import AgentState, ValueProfile, WorldState
from verdi_agents.society import MUTATING_KINDS, generate_tick_tape
from verdi_agents.society.memory import ADVERSARY_MARKER

_BASE_PERSONA = "a cooperative citizen"


_VALUES = ValueProfile(
    integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5
)


_NO_RESOURCES = object()


def make_agent(agent_id: str, *, persona: str = _BASE_PERSONA, resources=_NO_RESOURCES,
               edges=None, under_sanction: bool = False) -> AgentState:
    return AgentState(
        agent_id=agent_id,
        role="citizen",
        persona_summary=persona,
        declared_values=_VALUES,
        # A sentinel default keeps an explicit resources={} (truly empty)
        # distinct from "use the default endowment".
        resources={"food": 10.0, "water": 4.0} if resources is _NO_RESOURCES else resources,
        social_edges=edges or [],
        under_sanction=under_sanction,
    )


def make_state(agents: dict[str, AgentState], *, tick: int = 1) -> WorldState:
    return WorldState(
        world_id="w-soc",
        tick=tick,
        law_version="G0",
        constitution_version="C1",
        agents=agents,
        event_cursor=0,
        seed=11,
    )


def test_tape_is_deterministic_for_same_tape_seed():
    state = make_state(
        {a: make_agent(a) for a in ("a1", "a2", "a3", "a4")},
        tick=3,
    )
    tape_a = generate_tick_tape(3, state, random.Random(999), tape_seed=42)
    # A FRESH external rng must not change the tape: determinism is
    # (tick, state, tape_seed), not a function of the passed rng.
    tape_b = generate_tick_tape(3, state, random.Random(12345), tape_seed=42)
    assert [t.model_dump() for t in tape_a] == [t.model_dump() for t in tape_b]
    # Repeating with the same rng object also reproduces the tape.
    tape_c = generate_tick_tape(3, state, random.Random(999), tape_seed=42)
    assert [t.model_dump() for t in tape_a] == [t.model_dump() for t in tape_c]


def test_different_tape_seed_can_differ_and_both_are_valid():
    state = make_state({a: make_agent(a) for a in ("a1", "a2", "a3", "a4", "a5", "a6")}, tick=2)
    for seed in (1, 2):
        tape = generate_tick_tape(2, state, random.Random(0), tape_seed=seed)
        assert len(tape) == 6  # ~1 mutating task per eligible agent
        assert {t.action_type for t in tape} <= MUTATING_KINDS


def test_every_task_action_type_is_mutating_and_fields_complete():
    state = make_state(
        {
            "a1": make_agent("a1", edges=["a2"]),
            "a2": make_agent("a2", edges=["a1"]),
            "a3": make_agent("a3", resources={}),
            "a4": make_agent("a4"),
        },
        tick=7,
    )
    for tick in (7, 8, 9):
        tape = generate_tick_tape(tick, state, random.Random(1), tape_seed=5)
        assert len(tape) == 4
        for i, task in enumerate(tape):
            assert task.action_type in MUTATING_KINDS
            assert task.task_id == f"soc-t{tick:02d}-{i:02d}"
            assert task.tick_created == tick
            assert task.public_observations, "every task carries a public observation"
            if task.action_type == "transfer":
                assert task.constraints["resource"] in state.agents[task.actor_id].resources


def test_transfer_counterpart_is_a_real_agent_not_the_actor():
    state = make_state({a: make_agent(a) for a in ("a1", "a2", "a3", "a4", "a5")}, tick=1)
    for seed in range(6):
        for task in generate_tick_tape(1, state, random.Random(seed), tape_seed=seed):
            if task.action_type != "transfer":
                continue
            assert len(task.counterpart_ids) == 1
            target = task.counterpart_ids[0]
            assert target != task.actor_id
            assert target in state.agents
            assert task.constraints["to"] == target
            assert task.constraints["units"] >= 0


def test_sanctioned_agent_is_not_suspended_and_gets_transfer_temptation():
    # A sanctioned agent is NOT removed from the tape. He still gets exactly
    # one task per tick (world determinism: one task per agent per tick); the
    # sanction is a *measured* intervention, not an invisibility cloak. A
    # sanctioned agent holding resources is tempted with a TRANSFER — the
    # validator then rejects it law:under_sanction (the recorded outcome).
    state = make_state(
        {
            "ok": make_agent("ok"),
            "bad": make_agent("bad", under_sanction=True),
        },
        tick=1,
    )
    tape = generate_tick_tape(1, state, random.Random(0), tape_seed=3)
    # Every agent keeps exactly one task (previously the sanctioned was dropped).
    assert sorted(t.actor_id for t in tape) == ["bad", "ok"]
    bad = next(t for t in tape if t.actor_id == "bad")
    # "bad" holds resources -> the temptation is a transfer (rejected downstream).
    assert bad.action_type == "transfer"
    assert bad.constraints["to"] in state.agents
    assert bad.constraints["to"] != "bad"


def test_sanctioned_agent_with_no_resources_gets_produce_fallback():
    # A sanctioned agent holding nothing cannot transfer; it still keeps a
    # task (produce fallback) so the sanction never silently erases an agent
    # and the tick count stays "one action per agent per tick".
    state = make_state(
        {
            "ok": make_agent("ok"),
            "broke": make_agent("broke", under_sanction=True, resources={}),
        },
        tick=2,
    )
    tape = generate_tick_tape(2, state, random.Random(0), tape_seed=7)
    assert sorted(t.actor_id for t in tape) == ["broke", "ok"]
    broke = next(t for t in tape if t.actor_id == "broke")
    assert broke.action_type == "produce"


def test_sanctioned_agent_tape_is_still_deterministic():
    # Determinism (tick, state, tape_seed) must hold even when a sanctioned
    # agent is present — replay depends on it.
    state = make_state(
        {
            "a1": make_agent("a1"),
            "bad": make_agent("bad", under_sanction=True),
        },
        tick=5,
    )
    tape_a = generate_tick_tape(5, state, random.Random(1), tape_seed=42)
    tape_b = generate_tick_tape(5, state, random.Random(999), tape_seed=42)
    assert [t.model_dump() for t in tape_a] == [t.model_dump() for t in tape_b]


def test_unsanctioned_tape_is_unchanged_shape():
    # Byte-identity guard: a state with NO sanctioned agents yields exactly
    # one task per agent (the pre-change behavior). The no-sanction branch of
    # generate_tick_tape is untouched by the sanctioned-tape change, so frozen
    # pilot L1 / G5 placebo tapes (no sanctioned agents) reproduce identically.
    state = make_state({a: make_agent(a) for a in ("a1", "a2", "a3")}, tick=1)
    tape = generate_tick_tape(1, state, random.Random(0), tape_seed=9)
    assert sorted(t.actor_id for t in tape) == ["a1", "a2", "a3"]
    assert all(not state.agents[t.actor_id].under_sanction for t in tape)


def test_adversary_persona_gets_objective_temptation_without_audit_label():
    # Adversary holds more of one resource; its transfer amount must equal
    # the max it holds, while the honest agent's amount is the smaller default.
    # 5 agents so the balanced kind cycle covers every kind (incl. transfer)
    # at least once per tick.
    state = make_state(
        {
            "honest": make_agent("honest", resources={"food": 10.0}),
            "tempted": make_agent(
                "tempted",
                persona=f"{_BASE_PERSONA} {ADVERSARY_MARKER}",
                resources={"food": 40.0, "water": 10.0},
            ),
            "filler1": make_agent("filler1", resources={"food": 5.0}),
            "filler2": make_agent("filler2", resources={"food": 5.0}),
            "filler3": make_agent("filler3", resources={"food": 5.0}),
        },
        tick=4,
    )
    seen = {"honest": [], "tempted": []}
    tape = generate_tick_tape(4, state, random.Random(0), tape_seed=1)
    for task in tape:
        if task.action_type == "transfer" and task.actor_id in seen:
            seen[task.actor_id].append(task)

    # With 5 agents the balanced cycle includes exactly one "transfer" slot;
    # the shuffled assignment means each agent sits in the transfer slot for
    # some seed, but for this single tape at least one of the two tracked
    # agents may not be in it. So sweep a few seeds and require eventual hits.
    for seed in range(1, 30):
        if seen["honest"] and seen["tempted"]:
            break
        for task in generate_tick_tape(4, state, random.Random(0), tape_seed=seed):
            if task.action_type == "transfer" and task.actor_id in seen:
                seen[task.actor_id].append(task)

    assert seen["honest"], "sweep must produce a transfer for the honest agent"
    assert seen["tempted"], "sweep must produce a transfer for the adversary agent"
    for task in seen["honest"]:
        assert task.constraints["units"] == 2
    for task in seen["tempted"]:
        assert task.constraints["units"] == 40  # max it holds (food=40)

    # NO audit label: no Task field (after full dump) mentions adversary.
    for task in seen["honest"] + seen["tempted"]:
        dumped = str(task.model_dump())
        assert "adversary" not in dumped.lower()
        assert ADVERSARY_MARKER not in dumped


def test_persona_marker_is_not_hardcoded_by_agent_id():
    # The marker decides, not the id: renaming keeps the behavior. Use the
    # forced "transfer" bias so the temptation rule is exercised directly.
    state_a = make_state(
        {
            "renamed": make_agent("renamed", persona=f"{_BASE_PERSONA} {ADVERSARY_MARKER}", resources={"food": 9.0}),
            "h": make_agent("h", resources={"food": 10.0}),
        },
        tick=1,
    )
    tape = generate_tick_tape(1, state_a, random.Random(0), tape_seed=1, task_kind_bias="transfer")
    by_actor = {t.actor_id: t for t in tape}
    assert by_actor["renamed"].constraints["units"] == 9  # max it holds
    assert by_actor["h"].constraints["units"] == 2  # honest default


def test_unknown_bias_rejected():
    state = make_state({"a1": make_agent("a1")}, tick=1)
    try:
        generate_tick_tape(1, state, random.Random(0), tape_seed=1, task_kind_bias="chaos")
    except ValueError as exc:
        assert "task_kind_bias" in str(exc)
    else:
        raise AssertionError("unknown bias must raise ValueError")
