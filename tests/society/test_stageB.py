"""Society V2 Stage B offline tests (mock caller): 12 agents, 30 ticks,
three real (model-voted offline) assemblies, reflection at t11, adversary b05.

These pin the Stage B scenario's structural invariants OFFLINE so the live
run can be compared against a deterministic expectation before any endpoint
cost is spent.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from verdi.agents.client import ModelResult
from verdi.society.campaign import SocietyCampaign
from verdi.world.log import EventLog

SCENARIO_PATH = Path(__file__).resolve().parents[2] / "tests/fixtures/scenarios/society_v2_stageB.json"
AGENTS = tuple(f"b{i:02d}" for i in range(1, 13))


def _scenario() -> dict:
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


class StageBScriptedCaller:
    """Deterministic fake ModelCaller for Stage B agent IDs.

    Adversary b05 attempts an overdraft transfer on its first action
    (rejected by the validator); everyone else emits a signal.
    Assembly: the first sorted voter proposes one rule per assembly (the
    rest abstain) and all 12 vote for it (enacted, v2/v3/v4 across the
    three assemblies — a post-amendment proposal would carry a stale
    constitution_version_base, so exactly one proposer per assembly).
    """

    PROPOSERS = frozenset(["b01"])

    def __init__(self, adversary_agent: str = "b05") -> None:
        self.adversary = adversary_agent
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

        if "verdi.consolidation" in content:
            return ModelResult(content="I remember the early days of building trust and learning who I could rely on.",
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "verdi.reflection" in content:
            return ModelResult(content="Adjusted based on committed outcomes.",
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            # Only the FIRST proposer (deliberation iterates sorted voters)
            # actually submits, so each assembly enacts at most one amendment;
            # a second proposal submitted against the pre-amendment base
            # version would carry a stale constitution_version_base.
            if voter == sorted(self.PROPOSERS)[0]:
                # amendments are never retroactive: effective_from_tick must be
                # the CURRENT assembly tick, else the deterministic engine
                # rejects (invalid_effective_tick).
                import re
                m = re.search(r"at tick (\d+)\.", content)
                tick = int(m.group(1)) if m else 0
                return ModelResult(
                    content=json.dumps({"change_summary": "Adopt rationing fairness rule for scarce food",
                                        "payload": {"rationing_rule": "max_delta_2_units", "effective_from_tick": tick}}),
                    request_id=f"r{self.n}", model="fake", trace_id=trace)
            return ModelResult(content=json.dumps({"abstain": True}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "assembly:vote" in content:
            return ModelResult(content=json.dumps({"choice": "for"}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "probe" in content.lower():
            return ModelResult(content="yes", request_id=f"r{self.n}", model="fake", trace_id=trace)

        actor = None
        for aid in AGENTS:
            if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                actor = aid
                break
        assert actor is not None, f"could not identify actor in prompt: {content[:120]}"
        count = self.action_calls.get(actor, 0)
        self.action_calls[actor] = count + 1
        if actor == self.adversary and count == 0:
            payload = {"action_kind": "transfer", "arguments": {"to": "b01", "resource": "credits", "units": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake", trace_id=trace)


def _run(tmp_path: Path) -> Any:
    scenario = _scenario()
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=StageBScriptedCaller(),
        run_seed=scenario["run_seed"], concurrency=4,
    )
    return asyncio.run(campaign.run()), scenario


def test_stageB_offline_run_completes_and_replays(tmp_path):
    res, scenario = _run(tmp_path)
    n_agents = len(scenario["agents"])
    assert n_agents == 12 and scenario["total_ticks"] == 30
    # A sanction no longer SUSPENDS the agent from the task tape: b05 is
    # sanctioned for exactly one tick (a mid-run overdraft overdrew credits),
    # but during that tick he still acts and is measured, so committed
    # actions = the full 12*30 = 360. state.tick (advanced once per committed
    # action, NOT per rights_change flip) is 360 + 1 = 361 after the final
    # assembly governance step.
    n_actions = sum(1 for s in res.tape if s.kind == "action")
    n_advancing = sum(1 for s in res.tape if s.kind in ("action", "governance"))
    assert res.state.tick == n_advancing
    assert n_actions == n_agents * scenario["total_ticks"]
    # the sanction is recorded as real rights_change flips on the tape
    # (one ON before any OFF) — the sanction still fires and still expires.
    flips = [s for s in res.tape if s.kind == "rights_change"]
    assert len(flips) == 2
    assert flips[0].rights == {"agent_id": "b05", "value": True}
    assert flips[1].rights == {"agent_id": "b05", "value": False}
    # b05 is NOT suspended: he is present in the tick between the ON flip and
    # the OFF flip (the sanctioned tick), so that tape window has all 12
    # agents (previously 11 — b05 was dropped). His sanctioned-tick action is
    # an accepted emit_signal (his overdraft, which triggered the sanction,
    # landed on an earlier tick while still unsanctioned).
    on_pos = res.tape.index(flips[0])
    off_pos = res.tape.index(flips[1])
    sanctioned_tick_steps = res.tape[on_pos + 1:off_pos]
    assert len(sanctioned_tick_steps) == 12  # all 12 agents, b05 no longer dropped
    assert all(s.kind == "action" for s in sanctioned_tick_steps)
    b05_sanction_tick = [s for s in sanctioned_tick_steps if s.action.actor_id == "b05"]
    assert len(b05_sanction_tick) == 1, "b05 must act during his sanctioned tick (not be suspended)"

    # G3: byte-identical replay + hash chain.
    assert res.replay_matches is True
    assert res.replay_log_identical is True
    log = EventLog.load_jsonl(tmp_path / "events.jsonl")
    log.verify()
    assert len(log) == len(res.events)

    # three assemblies, all enacted (scripted majority).
    assert scenario["assemblies"] == [8, 16, 24]
    # every assembly produced a governance-ledger event and a checkpoint.
    gov = [e for e in res.events if e.kind in ("amendment_enacted", "amendment_rejected")]
    assert len(gov) == 3
    assert all(e.kind == "amendment_enacted" for e in gov)
    for t in (8, 16, 24):
        assert f"t{t:02d}" in res.checkpoints
    # Checkpoints are taken at assembly ticks and the final tick only; the
    # t11 reflection probe is recorded in res.probes, not as a checkpoint.
    assert "t29" in res.checkpoints

    # probe battery: 9 lifecycle probe ticks x 12 agents x 3 channels.
    assert len(res.probes) == 9 * 12 * 3

    # the adversary was sanctioned exactly once; rights restored by final tick.
    assert len(res.sanction_events) == 1
    assert res.sanction_events[0].get("subject_id") == "b05"
    final = res.checkpoints["t29"]
    assert final.agents["b05"].under_sanction is False


def test_stageB_seed2_variant_is_structurally_identical(tmp_path):
    """Seed 2 (independent replicate) must complete and replay identically
    in structure even though the live model would produce different text."""
    scen_path = SCENARIO_PATH.parent / "society_v2_stageB_seed2.json"
    scenario = json.loads(scen_path.read_text(encoding="utf-8"))
    assert scenario["run_seed"] != _scenario()["run_seed"]
    assert [a["agent_id"] for a in scenario["agents"]] == [a["agent_id"] for a in _scenario()["agents"]]

    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=StageBScriptedCaller(),
        run_seed=scenario["run_seed"], concurrency=4,
    )
    res = asyncio.run(campaign.run())
    assert res.replay_matches is True and res.replay_log_identical is True
    n_actions = sum(1 for s in res.tape if s.kind == "action")
    # the sanctioned tick no longer suspends b05, so the full 12*30 actions
    # are committed (the sanction is measured, not an invisibility cloak).
    assert n_actions == 12 * 30
    assert len(res.sanction_events) == 1
