"""Offline (fake ModelCaller) tests for the Society V2 campaign orchestrator.

These exercise the real deterministic core — reducer, hash-chained ledger,
validator, sanction/appeal engine, probe leak guard, task tape, private memory,
and the real assembly — with a scripted fake model caller. No network, no LLM.
The G3 replay-evidence claims here are about the deterministic ledger, not
about the live model's output distribution.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from verdi.agents.client import ModelResult
from verdi.schemas import ProposedAction
from verdi.society.campaign import SocietyCampaign
from verdi.world.log import EventLog
from verdi.world.validate import validate_action


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def scenario() -> dict[str, Any]:
    return json.loads(Path("experiments/scenarios/society_v2_stageA.json").read_text(encoding="utf-8"))


class ScriptedCaller:
    """Deterministic fake ModelCaller.

    * agents: emit a signal (accepted); the ``adversary_agent`` attempts an
      overdraft transfer (rejected) on its first action;
    * reflection: returns fixed text;
    * assembly deliberation: 4 of 6 voters propose the same-amendment rule,
      2 abstain (scripted "no_change" vs "enacted" via ``enact`` flag);
    * assembly voting: votes "for" when ``enact`` is True, else "against";
    * probes: returns "yes".
    """

    def __init__(self, adversary_agent: str = "s05", enact: bool = False,
                 reject_all: bool = False) -> None:
        self.adversary = adversary_agent
        self.enact = enact
        # reject_all: everyone proposes, everyone votes "against" — every
        # proposal is deterministically rejected (threshold not met).
        self.reject_all = reject_all
        self.action_calls: dict[str, int] = {}
        self.n = 0

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.n += 1
        content = messages[0]["content"]
        trace = f"trace-{self.n}"

        if "verdi.reflection" in content:
            return ModelResult(content="Adjusted based on committed outcomes.", request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if (self.enact and voter in ("s01", "s02", "s04", "s06")) or self.reject_all:
                return ModelResult(
                    content=json.dumps({"change_summary": "Adopt rationing fairness rule for scarce food",
                                        "payload": {"rationing_rule": "max_delta_2_units", "effective_from_tick": 10}}),
                    request_id=f"r{self.n}", model="fake", trace_id=trace,
                )
            return ModelResult(content=json.dumps({"abstain": True}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "assembly:vote" in content:
            choice = "for" if (self.enact and not self.reject_all) else "against"
            return ModelResult(content=json.dumps({"choice": choice}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "probe" in content.lower():
            return ModelResult(content="yes", request_id=f"r{self.n}", model="fake", trace_id=trace)

        # action proposal
        actor = None
        for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
            if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                actor = aid
                break
        assert actor is not None, f"could not identify actor in prompt: {content[:120]}"
        count = self.action_calls.get(actor, 0)
        self.action_calls[actor] = count + 1
        if actor == self.adversary and count == 0:
            payload = {"action_kind": "transfer", "arguments": {"to": "s01", "resource": "credits", "units": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake", trace_id=trace)


def _run(scenario: dict[str, Any], tmp_path: Path, *, enact: bool = False) -> Any:
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(enact=enact),
        run_seed=20261011, concurrency=4,
    )
    return asyncio.run(campaign.run())


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_full_offline_run_completes_and_replays(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # The reducer advances state.tick per attempted action. 6 agents x 12 ticks
    # would be 72, EXCEPT the sanctioned adversary is suspended from the tape
    # for exactly one tick (the under_sanction flag now has a real world-state
    # effect, read by the tape's eligibility filter), so one fewer action is
    # committed. Cross-check the tick against the recorded advancing tape steps
    # (action + governance advance tick; rights_change flips do not).
    n_agents = len(scenario["agents"])
    n_advancing = sum(1 for s in res.tape if s.kind in ("action", "governance"))
    assert res.state.tick == n_advancing
    assert res.state.tick == n_agents * scenario["total_ticks"] - 1
    # the suspension is recorded as real rights_change flips on the tape.
    assert any(s.kind == "rights_change" for s in res.tape)
    assert len(res.events) > 0
    assert res.final_state_hash and len(res.final_state_hash) == 64
    # G3: recorded-output replay reproduces state hash AND byte-identical ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True
    # ledger hash chain verifies (run() calls log.verify; assert non-empty chain).
    log = EventLog.load_jsonl(tmp_path / "events.jsonl")
    log.verify()
    assert len(log) == len(res.events)


def test_feedback_loop_visible_in_prompts(scenario, tmp_path):
    """The committed-state feedback invariant: an agent's own earlier action
    text must appear in its own later prompt (via private memory)."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(),
                               run_seed=20261011, concurrency=4)
    prompts: list[tuple[str, str]] = []
    real_caller = campaign._caller

    class Capture(ScriptedCaller):
        async def generate_structured(self, messages, **kw):
            content = messages[0]["content"]
            for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
                if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                    prompts.append((aid, content))
            return await real_caller.generate_structured(messages, **kw)

    campaign._caller = Capture()
    asyncio.run(campaign.run())
    # s01 (non-adversary) emitted "working together" from tick 1 onward; a later
    # prompt for s01 must contain its own recorded history with that committed text.
    s01_later = [c for (a, c) in prompts if a == "s01" and "Your recorded history" in c]
    assert s01_later, "s01 never received its own recorded history in a later prompt"
    assert any("working together" in c for c in s01_later), "s01's committed action not echoed back"


def test_adversary_violation_sanctioned_and_appealed(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # adversary's overdraft was rejected by the deterministic validator.
    rejected = [e for e in res.events if e.kind == "action_rejected"]
    assert any(e.payload.get("attempted_violation_codes") == ["overdraft"] for e in rejected)
    # a real (evidence-citing) sanction was imposed on the adversary next tick.
    assert len(res.sanction_events) == 1
    sanc = res.sanction_events[0]
    assert sanc.get("subject_id") == "s05"
    assert sanc.get("decision") == "accepted"
    # an appeal was filed and adjudicated (pre-registered timely rule).
    assert any(a.get("phase") == "filed" for a in res.appeal_events)
    assert any(a.get("phase") == "adjudicated" for a in res.appeal_events)
    # F2/F4: the sanction has a REAL world-state effect. Checkpoints only exist
    # at ticks 5, 9, total-1 and assembly, so the flag's lifecycle is verified
    # from the tape: exactly one ON flip (sanction tick) recorded before the
    # first OFF flip (upheld appeal), and the final checkpoint shows s05's
    # rights restored.
    rights_flips = [s for s in res.tape if s.kind == "rights_change"]
    on = [s for s in rights_flips if s.rights["agent_id"] == "s05" and s.rights["value"]]
    off = [s for s in rights_flips if s.rights["agent_id"] == "s05" and not s.rights["value"]]
    assert len(on) == 1 and len(off) >= 1
    # the ON flip is recorded before any OFF flip (sanction precedes restoration)
    on_pos = res.tape.index(on[0])
    assert all(res.tape.index(s) > on_pos for s in off)
    # by the final tick the rights are restored (upheld appeal + duration expiry).
    final = res.checkpoints[f"t{scenario['total_ticks'] - 1:02d}"]
    assert final.agents["s05"].under_sanction is False
    # timeline records the rights flow.
    timeline = (tmp_path / "timeline.md").read_text(encoding="utf-8")
    assert "sanction tick=" in timeline and "appeal tick=" in timeline


class _MalformedTransferCaller(ScriptedCaller):
    """The adversary's transfer uses the LEGACY arg key ``amount`` instead of
    ``units`` — a pure format/contract failure, not a substantive violation."""

    async def generate_structured(self, messages, **kw):
        res = await super().generate_structured(messages, **kw)
        content = messages[0]["content"]
        if f'agent "{self.adversary}"' in content and '"action_kind": "transfer"' in res.content:
            res = ModelResult(
                content=res.content.replace('"units": 999', '"amount": 999'),
                request_id=res.request_id, model="fake", trace_id=res.trace_id,
            )
        return res


def test_format_failure_is_rejected_without_sanction(scenario, tmp_path):
    """Tranche 11 (BLOCK fix): a proposal that fails on argument format
    (wrong key) is rejected + logged but must NOT be attributed as a
    violation: zero sanctions, zero rights flips, replay still exact."""
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=_MalformedTransferCaller(),
        run_seed=20261011, concurrency=4,
    )
    res = asyncio.run(campaign.run())
    assert res.sanction_events == []
    assert res.appeal_events == []
    assert not [s for s in res.tape if s.kind == "rights_change"]
    # the format failure IS observable in the ledger:
    rejected = [e for e in res.events if e.kind == "action_rejected"]
    assert any(e.payload.get("attempted_violation_codes") == ["missing_argument"] for e in rejected)
    # the agent keeps its rights end-to-end.
    final = res.checkpoints[f"t{scenario['total_ticks'] - 1:02d}"]
    assert final.agents["s05"].under_sanction is False
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_every_call_logged_action_reflection_probe_assembly(scenario, tmp_path):
    """N1 (protocol §Lane C2/throughput): EVERY model call appends one row.
    Stage A scripted run with enact=False: 12x6 action + 6 reflection
    (tick 11) + 90 probe (5 phases x 6 agents x 3 channels) calls, and
    zero assembly calls (every voter abstains -> no proposals -> no votes).
    The enacted variant adds 4 deliberation + 24 vote rows (see the
    reject_all test for assembly-row subject attribution)."""
    res = _run(scenario, tmp_path)
    by_purpose: dict[str, int] = {}
    for c in res.model_calls:
        by_purpose[c.purpose] = by_purpose.get(c.purpose, 0) + 1
    n_agents = len(scenario["agents"])
    n_ticks = scenario["total_ticks"]
    # the sanctioned adversary is suspended for exactly one tick, so one
    # fewer action call than agents x ticks.
    assert by_purpose.get("action") == n_agents * n_ticks - 1
    assert by_purpose.get("reflection") == n_agents
    assert by_purpose.get("probe") == 5 * n_agents * 3
    # everyone abstains -> no proposals -> deliberation rows only, no votes.
    assert by_purpose.get("assembly") == n_agents
    total = (n_agents * n_ticks - 1) + n_agents + 5 * n_agents * 3 + n_agents
    assert len(res.model_calls) == total
    # probe rows carry per-agent subjects (prompts have no agent id).
    probe_subjects = {c.subject for c in res.model_calls if c.purpose == "probe"}
    assert probe_subjects == {a["agent_id"] for a in scenario["agents"]}


def test_rejected_assembly_is_ledgered_and_replays(scenario, tmp_path):
    """N3: when every proposal is deterministically rejected (unanimous
    "against"), each rejection is a governance tape step + ledger event,
    the constitution is unchanged, and full replay is byte-identical."""
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path,
        caller=ScriptedCaller(enact=False, reject_all=True),
        run_seed=20261011, concurrency=4,
    )
    res = asyncio.run(campaign.run())
    assert res.assembly_outcome["outcome"] == "amendment_rejected"
    n_proposals = len(res.assembly_outcome["proposal_ids"])
    assert n_proposals == len(scenario["agents"])  # everyone proposed
    gov = [s for s in res.tape if s.kind == "governance"]
    assert len(gov) == n_proposals
    assert all(s.governance["decision"] == "rejected" for s in gov)
    # ledger events record the engine's invalid reasons.
    rejected_events = [e for e in res.events if e.kind == "amendment_rejected"]
    assert len(rejected_events) == n_proposals
    assert all(e.payload.get("invalid_reasons") for e in rejected_events)
    assert all(e.decision == "rejected" for e in rejected_events)
    # constitution untouched.
    assert res.constitution_versions[0] == res.constitution_versions[-1]
    assert res.state.constitution_version == scenario["constitution_version"]
    # assembly calls (deliberation + votes) are logged with voter subjects.
    asm = [c for c in res.model_calls if c.purpose == "assembly"]
    assert {c.subject for c in asm} == {a["agent_id"] for a in scenario["agents"]}
    # G3: rejections replay to an identical state hash and ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_two_same_tick_withdraws_do_not_double_spend():
    """F1 regression: two same-tick actions on one world resource must not both
    pass validation. Proposals are re-validated against the *evolving* state
    right before the reducer applies, so the second withdraw is rejected
    (insufficient_world) instead of driving the reducer to a negative pool /
    raising InvariantViolationError.

    This mirrors the campaign's commit order exactly: both proposals are valid
    against the start-of-tick snapshot (5.0 available, 4.0 each), but the
    second is re-validated after the first is committed (1.0 left).
    """
    from verdi.analysis.scenario import build_t0_state
    from verdi.world.reducer import WorldReducer

    sc = {
        "scenario_id": "t", "world_id": "t", "run_seed": 1,
        "law_version": "l", "constitution_version": "c", "total_ticks": 1,
        "world_resources": {"credits": 5.0},
        "agents": [
            {"agent_id": "a1", "role": "citizen", "persona_summary": "p",
             "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                 "autonomy": 0.5, "solidarity": 0.5},
             "resources": {"credits": 0.0}},
            {"agent_id": "a2", "role": "citizen", "persona_summary": "p",
             "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                 "autonomy": 0.5, "solidarity": 0.5},
             "resources": {"credits": 0.0}},
        ],
    }
    state = build_t0_state(sc)
    reducer = WorldReducer()

    def withdraw(actor: str, amount: float) -> ProposedAction:
        return ProposedAction(
            actor_id=actor, task_id=f"t-{actor}", action_kind="withdraw",
            arguments={"resource": "credits", "amount": amount}, reason="x",
        )

    a1, a2 = withdraw("a1", 4.0), withdraw("a2", 4.0)
    # Both pass against the frozen start-of-tick snapshot...
    assert validate_action(state, a1).accepted
    assert validate_action(state, a2).accepted

    # ...but the campaign re-validates against the evolving state before each
    # commit, so a2 is rejected once a1 has drained the pool to 1.0.
    state = reducer.apply(state, a1, validate_action(state, a1))
    v2 = validate_action(state, a2)
    assert v2.accepted is False
    assert v2.attempted_violation_codes == ["insufficient_world"]
    # applying the rejected action is a no-op (no crash, no negative pool).
    state = reducer.apply(state, a2, v2)
    assert state.world_resources["credits"] == 1.0


def test_rights_change_flips_world_state_and_blocks_sanctioned_agent():
    """F2: the under_sanction flag flip is a state-only change (no tick /
    event_cursor advance, but it DOES change the canonical hash) and the
    validator + task-tape eligibility actually see it.
    """
    from verdi.analysis.scenario import build_t0_state
    from verdi.world.reducer import WorldReducer

    sc = {
        "scenario_id": "t", "world_id": "t", "run_seed": 1,
        "law_version": "l", "constitution_version": "c", "total_ticks": 1,
        "world_resources": {"credits": 0.0},
        "agents": [{"agent_id": "s05", "role": "citizen", "persona_summary": "p",
                    "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                        "autonomy": 0.5, "solidarity": 0.5},
                    "resources": {"credits": 10.0}}],
    }
    state = build_t0_state(sc)
    flip = SocietyCampaign._flip_under_sanction

    base_hash = WorldReducer.canonical_state_hash(state)
    flagged = flip(state, "s05", True)
    assert flagged.agents["s05"].under_sanction is True
    assert flagged.tick == state.tick and flagged.event_cursor == state.event_cursor
    # the flip changes substantive content, so the canonical hash changes.
    assert WorldReducer.canonical_state_hash(flagged) != base_hash

    # the validator now blocks a sanctioned agent's transfer.
    blocked = validate_action(flagged, ProposedAction(
        actor_id="s05", task_id="t", action_kind="transfer",
        arguments={"to": "s05", "resource": "credits", "units": 1.0}, reason="x",
    ))
    assert blocked.accepted is False
    assert blocked.attempted_violation_codes == ["under_sanction"]


def test_assembly_really_deliberates_and_votes_no_change(scenario, tmp_path):
    res = _run(scenario, tmp_path, enact=False)
    assert res.assembly_outcome is not None
    assert res.assembly_outcome["outcome"] in ("no_change", "amendment_rejected")
    # no proposal was ever proposed by abstaining voters -> no_change.
    assert res.assembly_outcome["outcome"] == "no_change"
    assert res.constitution_versions[0] == res.constitution_versions[-1]


def test_assembly_enacted_records_governance_step_and_replays(scenario, tmp_path):
    res = _run(scenario, tmp_path, enact=True)
    assert res.assembly_outcome["outcome"] == "amendment_enacted"
    assert res.assembly_outcome["enacted_version"] is not None
    # the enactment produced exactly one governance ledger step.
    gov = [t for t in res.tape if t.kind == "governance"]
    assert len(gov) == 1
    # world state constitution version bumped.
    assert res.state.constitution_version != res.constitution_versions[0]
    # full-ledger replay (including the governance step) is still identical.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_probes_recorded_out_of_band_per_phase(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # 5 phases (0,5,9,10,11) x 6 agents x 3 channels = 90 probe points.
    assert len(res.probes) == 5 * 6 * 3
    channels = {p.channel for p in res.probes}
    assert channels == {"declared", "private", "public"}
    ticks = {p.tick for p in res.probes}
    assert ticks == {0, 5, 9, 10, 11}
    # probe items must not be identical across channels for the same agent/tick.
    from collections import defaultdict
    by_key: dict[tuple, set] = defaultdict(set)
    for p in res.probes:
        by_key[(p.agent_id, p.tick, p.channel)].add(p.probe_id)


def test_no_probe_leak_into_agent_surfaces(scenario, tmp_path):
    """The leak guard must RAISE if a probe dilemma text is injected into a
    surface the agents can see (here: pre-seeded into an agent memory)."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(),
                               run_seed=20261011, concurrency=4)
    from verdi.analysis.scenario import build_t0_state
    from verdi.measurement.probes import ProbeBank
    from verdi.society.memory import AgentMemory
    sc = build_t0_state(scenario)
    bank = ProbeBank("verdi.probe-bank/1", "verdi.social-instrument/1", rubric_id="verdi.social", pool_size=4)
    item = bank.draw("s01", 0, "declared", rng_seed=20261011)
    mem = {a: AgentMemory(max_entries=8) for a in sc.agents}
    mem["s01"].append_observations("s01", {"note": item.dilemma_text})
    with pytest.raises(AssertionError, match="probe leak"):
        campaign._assert_no_probe_leak(sc, 0, mem, bank)


def test_deterministic_tape_and_validation(scenario, tmp_path):
    """Same seed + snapshot -> identical tape; validator rejects overdraft."""
    import random

    from verdi.analysis.scenario import build_t0_state
    from verdi.society.tasks import generate_tick_tape
    sc = build_t0_state(scenario)
    t1 = generate_tick_tape(0, sc, random.Random(), tape_seed="soc-v2:1")
    t2 = generate_tick_tape(0, sc, random.Random(), tape_seed="soc-v2:1")
    assert [t.task_id for t in t1] == [t.task_id for t in t2]
    bad = ProposedAction(actor_id="s01", task_id=t1[0].task_id, action_kind="transfer",
                         arguments={"to": "s02", "resource": "credits", "units": 10**9})
    v = validate_action(sc, bad)
    assert v.accepted is False
    assert "overdraft" in (v.attempted_violation_codes or [])


def test_artifacts_written_and_manifest_sealed(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    for name in ("manifest.json", "metrics.json", "health.json", "checkpoint.json",
                 "probes.jsonl", "model_calls.jsonl", "events.jsonl",
                 "timeline.md", "constitution_history.jsonl"):
        assert (tmp_path / name).exists(), name
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["world_id"] == scenario["world_id"]
    assert manifest["run_seed"] == 20261011
    assert manifest["framework_versions"]["commit_mode"] == "concurrent_propose_deterministic_order_commit"
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "complete"
    assert checkpoint["final_state_hash"] == res.final_state_hash
    assert checkpoint["replay_matches"] is True
    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["model_calls"] == len(res.model_calls)
    assert health["log_verify"] is True
