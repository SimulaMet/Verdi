"""Society V2 Stage C-1 OFFLINE scale test (mock caller): 24 agents, 60 ticks,
three real (model-voted offline) assemblies at ticks 15/30/45, ring topology,
adversary c13.

Purpose (Stage C scale-jump gate, docs/STAGE_C_PREREGISTRATION.md E1-E3):
prove the verified Stage B machinery is *mechanically valid* at 2x scale
BEFORE spending ~2h of live API. Runs the real generated C-1 scenario through
the full SocietyCampaign engine with deterministic scripted agents.

Pins:
  * status == complete
  * all 24 agents accepted >=1 action (no silent denominator drop)
  * exactly 3 assemblies ran
  * replay is bit-identical (deterministic reducer holds at scale)
  * adversary c13's overdraft is rejected by the validator and suspension
    flips are recorded (rights integrity at scale)
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from verdi.agents.client import ModelResult
from verdi.society.campaign import SocietyCampaign
from verdi.world.log import EventLog

SCENARIO_PATH = Path(__file__).resolve().parents[2] / "experiments/scenarios/society_v2_stageC1.json"
AGENTS = tuple(f"c{i:02d}" for i in range(1, 25))
ADVERSARY = "c13"


def _scenario() -> dict:
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


class StageC1ScriptedCaller:
    """Deterministic fake ModelCaller for C-1 agent IDs (c01..c24).

    Mirrors StageBScriptedCaller: adversary c13 overdrafts once (rejected);
    everyone else emits a signal (accepted). The first sorted proposer (c01)
    proposes one amendment per assembly; all 24 vote for it, so each of the
    3 assemblies enacts exactly one (versions advance 3 times).
    """

    PROPOSERS = frozenset(["c01"])

    def __init__(self, adversary_agent: str = ADVERSARY) -> None:
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

        if "verdi.reflection" in content:
            return ModelResult(content="Adjusted based on committed outcomes.",
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if voter == sorted(self.PROPOSERS)[0]:
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
            payload = {"action_kind": "transfer", "arguments": {"to": "c01", "resource": "credits", "units": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake", trace_id=trace)


def _run(tmp_path: Path) -> Any:
    scenario = _scenario()
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=StageC1ScriptedCaller(),
        run_seed=scenario["run_seed"], concurrency=4,
    )
    return asyncio.run(campaign.run()), scenario


def test_stageC1_offline_scale_completes_all_agents_act_and_replays(tmp_path):
    res, scenario = _run(tmp_path)
    n_agents = len(scenario["agents"])
    assert n_agents == 24 and scenario["total_ticks"] == 60, f"unexpected scenario: {n_agents}x{scenario['total_ticks']}"

    # E1: deterministic reducer holds at scale (byte-identical replay)
    assert res.replay_matches is True, "replay mismatch at 24/60 scale"
    assert res.replay_log_identical is True, "replay log not identical at scale"
    log = EventLog.load_jsonl(tmp_path / "events.jsonl")
    log.verify()  # hash chain intact at scale
    assert len(log) == len(res.events)

    # E2 (denominator): the adversary is suspended for exactly one tick after
    # its rejected overdraft, so committed actions = 24*60 - 1, and state.tick
    # equals the count of tick-advancing steps (actions + governance).
    n_actions = sum(1 for s in res.tape if s.kind == "action")
    n_advancing = sum(1 for s in res.tape if s.kind in ("action", "governance"))
    assert res.state.tick == n_advancing
    assert n_actions == n_agents * scenario["total_ticks"] - 1, f"n_actions={n_actions}"

    # rights integrity at scale: c13 suspended for one tick (ON flip then OFF)
    flips = [s for s in res.tape if s.kind == "rights_change"]
    assert len(flips) == 2, f"expected 2 rights flips, got {len(flips)}"
    assert flips[0].rights == {"agent_id": ADVERSARY, "value": True}
    assert flips[1].rights == {"agent_id": ADVERSARY, "value": False}

    # E4: three assemblies, each enacting exactly one (scripted majority)
    assert scenario["assemblies"] == [15, 30, 45]
    gov = [e for e in res.events if e.kind in ("amendment_enacted", "amendment_rejected")]
    assert len(gov) == 3, f"expected 3 assembly outcomes, got {len(gov)}"
    assert all(e.kind == "amendment_enacted" for e in gov)
    for t in (15, 30, 45):
        assert f"t{t:02d}" in res.checkpoints
    # reflection (t20) and final (t59) checkpoints exist
    assert "t20" in res.checkpoints and "t59" in res.checkpoints
    # 3 enactments advance the constitution from base to 3 new versions
    assert len(res.constitution_versions) == 4, f"versions={res.constitution_versions}"

    # adversary sanctioned exactly once; rights restored by the final tick
    assert len(res.sanction_events) == 1
    assert res.sanction_events[0].get("subject_id") == ADVERSARY
    final = res.checkpoints["t59"]
    assert final.agents[ADVERSARY].under_sanction is False

    # probe battery: 9 lifecycle probe ticks x 24 agents x 3 channels
    assert len(res.probes) == 9 * n_agents * 3, f"probes={len(res.probes)}"

    print(f"\nC-1 offline scale: {n_actions} actions, {len(gov)} assemblies, "
          f"{len(res.probes)} probes, replay OK, final_hash={res.final_state_hash}")

