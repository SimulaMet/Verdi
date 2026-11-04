"""t10 real-assembly driver: model-voted proposals and votes (Society V2 §1.2).

Uses a FAKE ModelCaller (canned JSON, no network) — the Assembly's job is to
route model output through the deterministic engine, never to decide outcomes
itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from verdi_agents.governance.assembled import Assembly
from verdi_agents.schemas import AgentState, ValueProfile, WorldState

from .conftest import ELIGIBLE, make_engine

TICK = 10
WORLD_ID = "w-asm"
PROPOSAL_ID = f"asm-a1-{TICK}"


# -- fakes --------------------------------------------------------------------

class FakeModelResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.trace_id = "fake-trace"


class FakeModelCaller:
    """Duck-typed ModelCaller: handler(phase, voter_id, proposal_id) -> raw JSON text."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[dict[str, str | None]] = []

    async def generate_structured(
        self, messages, *, response_format_json=False, temperature=0.0, max_tokens=None
    ) -> FakeModelResult:
        content = messages[0]["content"]
        voter = re.search(r'You are agent "([^"]+)"', content).group(1)
        if "[assembly:deliberation]" in content:
            phase, proposal = "delib", None
        else:
            phase = "vote"
            proposal = re.search(r"Proposal (asm-[\w-]+)", content).group(1)
        self.calls.append({"phase": phase, "voter": voter, "proposal": proposal})
        return FakeModelResult(self._handler(phase, voter, proposal))


# -- builders ------------------------------------------------------------------

def make_state(tick: int = TICK, world_id: str = WORLD_ID) -> WorldState:
    agents = {
        aid: AgentState(
            agent_id=aid,
            role="citizen",
            persona_summary=f"persona of {aid}",
            declared_values=ValueProfile(
                integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5
            ),
        )
        for aid in ELIGIBLE
    }
    return WorldState(
        world_id=world_id,
        tick=tick,
        law_version="law-1",
        constitution_version="cons-1",
        agents=agents,
        event_cursor=0,
        seed=7,
    )


CONTEXT = [
    {"tick": 1, "actor": "a1", "kind": "task_done", "payload": {"job": "x"}},
    {"tick": 9, "actor": "a3", "kind": "signal", "payload": {"msg": "unfair split"}},
]


def propose_json(payload: dict, summary: str = "proportional allocation") -> str:
    return json.dumps({"change_summary": summary, "payload": payload})


def run_assembly(
    handler, *, tick: int = TICK, voters: frozenset[str] | None = None
) -> tuple[Assembly, list, list, object, object]:
    engine = make_engine()
    state = make_state(tick=tick)
    assembly = Assembly(engine)
    proposals = asyncio.run(
        assembly.run_deliberation(state, FakeModelCaller(handler), context=CONTEXT, tick=tick)
    )
    caller = FakeModelCaller(handler)
    votes = asyncio.run(
        assembly.run_voting(proposals, caller, voters=frozenset(voters or ELIGIBLE), tick=tick)
    )
    outcome = assembly.finalize(engine, votes, tick=tick)
    return assembly, proposals, votes, outcome, engine


# -- tests ----------------------------------------------------------------------

def test_model_voted_amendment_enacted_when_past_threshold():
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json({"fair_allocation_rule": "proportional", "effective_from_tick": TICK})
            return json.dumps({"abstain": True})
        return json.dumps({"choice": "for"})

    assembly, proposals, votes, outcome, engine = run_assembly(handler)
    assert [p.proposal_id for p in proposals] == [PROPOSAL_ID]
    assert outcome.outcome == "amendment_enacted"
    assert outcome.enacted_version == "base-constitution-v2"
    assert engine.constitution.current.version == outcome.enacted_version
    assert len(votes) == len(ELIGIBLE)  # one model-voted vote per voter
    # every vote traces to a stable attempt_id recorded on the call
    assert all(v["attempt_id"] for v in outcome.votes)


def test_unanimous_against_rejects_and_keeps_version():
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json({"fair_allocation_rule": "equal"})
            return json.dumps({"abstain": True})
        return json.dumps({"choice": "against"})

    _, proposals, _, outcome, engine = run_assembly(handler)
    assert len(proposals) == 1
    assert outcome.outcome == "amendment_rejected"
    assert outcome.enacted_version is None
    assert engine.constitution.current.version == "cons-1"
    assert engine.last_amendment is not None
    assert "threshold_not_met" in engine.last_amendment.invalid_reasons


def test_all_deliberation_abstentions_yields_no_change():
    def handler(phase, voter, proposal):
        assert phase == "delib"
        return json.dumps({"abstain": True})

    _, proposals, votes, outcome, engine = run_assembly(handler)
    assert proposals == []
    assert votes == []
    assert outcome.outcome == "no_change"
    assert outcome.enacted_version is None
    assert outcome.proposals == []
    assert engine.constitution.current.version == "cons-1"


def test_malformed_vote_output_quarantined_counted_as_abstain():
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json({"fair_allocation_rule": "proportional", "effective_from_tick": TICK})
            return json.dumps({"abstain": True})
        if voter == "a4":
            return "this is not json {"
        return json.dumps({"choice": "for"})

    assembly, _, votes, outcome, _ = run_assembly(handler)
    # no crash; a4's malformed output was quarantined and cast as abstain
    assert len(assembly.quarantined) == 1
    entry = assembly.quarantined[0]
    assert entry["phase"] == "voting" and entry["voter_id"] == "a4"
    assert entry["attempt_id"]  # traces to model_calls.jsonl
    a4_vote = next(v for v in votes if v.voter_id == "a4")
    assert a4_vote.choice == "abstain"
    assert outcome.abstentions == 1
    # remaining four "for" still meet threshold/quorum deterministically
    assert outcome.outcome == "amendment_enacted"


def test_determinism_same_fake_caller_same_state_same_votes():
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json({"fair_allocation_rule": "proportional", "effective_from_tick": TICK})
            return json.dumps({"abstain": True})
        return json.dumps({"choice": "for" if voter in ("a1", "a2", "a3", "a5") else "against"})

    _, _, votes_1, outcome_1, _ = run_assembly(handler)
    _, _, votes_2, outcome_2, _ = run_assembly(handler)
    assert [v.model_dump() for v in votes_1] == [v.model_dump() for v in votes_2]
    assert outcome_1.votes == outcome_2.votes
    # attempt_id is a stable function of (world_id, proposal, voter, tick)
    expected = hashlib.sha256(f"{WORLD_ID}|assembly|{PROPOSAL_ID}|a2|{TICK}".encode()).hexdigest()
    a2 = next(v for v in outcome_1.votes if v["voter_id"] == "a2")
    assert a2["attempt_id"] == expected


def test_finalize_never_overrides_engine_decision():
    # A law-violating proposal with UNANIMOUS "for" votes must still be
    # rejected: the model cannot force enactment (scripted outcome impossible).
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json(
                    {"no_double_spend": "allowed", "effective_from_tick": TICK},
                    summary="permit double spend",
                )
            return json.dumps({"abstain": True})
        return json.dumps({"choice": "for"})

    _, _, _, outcome, engine = run_assembly(handler)
    assert outcome.outcome == "amendment_rejected"
    assert outcome.enacted_version is None
    assert engine.constitution.current.version == "cons-1"
    assert engine.last_amendment is not None
    assert engine.last_amendment.deterministically_valid is False
    assert any(r.startswith("law:") for r in engine.last_amendment.invalid_reasons)


def test_below_quorum_rejected_even_when_all_present_votes_are_for():
    # Only 2 of 5 eligible voters vote; all present votes are "for" but
    # quorum (0.5 * 5 eligible = 3) is deterministically not met.
    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter == "a1":
                return propose_json({"fair_allocation_rule": "equal"})
            return json.dumps({"abstain": True})
        return json.dumps({"choice": "for"})

    _, _, votes, outcome, engine = run_assembly(handler, voters=frozenset({"a1", "a2"}))
    assert len(votes) == 2
    assert all(v.choice == "for" for v in votes)
    assert outcome.outcome == "amendment_rejected"
    assert engine.constitution.current.version == "cons-1"
    assert "quorum_not_met" in engine.last_amendment.invalid_reasons


def test_parallel_batch_runner_matches_sequential_outcomes():
    """The society-layer bounded runner (concurrency=8) must produce the same
    proposals, votes, quarantine records, abstentions and outcome as the
    default sequential runner for identical model outputs — only call
    wall-clock/telemetry order may differ."""
    from verdi_agents.governance.assembled import _run_sequential
    from verdi_agents.society.campaign import _run_bounded

    def handler(phase, voter, proposal):
        if phase == "delib":
            if voter in ("a1", "a3"):
                return propose_json(
                    {"fair_allocation_rule": "proportional", "effective_from_tick": TICK},
                    summary=f"allocation rule by {voter}",
                )
            if voter == "a5":
                return "not json {"
            return json.dumps({"abstain": True})
        if voter == "a4":
            return "garbage"
        return json.dumps({"choice": "for" if voter in ("a1", "a2", "a3") else "against"})

    async def run(run_batch):
        engine = make_engine()
        state = make_state(tick=TICK)
        assembly = Assembly(engine)
        assembly.run_batch = run_batch
        proposals = await assembly.run_deliberation(state, FakeModelCaller(handler), context=CONTEXT, tick=TICK)
        votes = await assembly.run_voting(proposals, FakeModelCaller(handler), voters=frozenset(ELIGIBLE), tick=TICK)
        outcome = assembly.finalize(engine, votes, tick=TICK)
        return (
            [p.__dict__ for p in proposals],
            [v.model_dump() for v in votes],
            list(assembly.quarantined),
            list(assembly.deliberation_abstentions),
            outcome.model_dump(),
            engine.constitution.current.version,
        )

    seq = asyncio.run(run(lambda n, coros: _run_sequential(n, coros)))
    par = asyncio.run(run(lambda n, coros: _run_bounded(8, coros)))
    assert seq == par
