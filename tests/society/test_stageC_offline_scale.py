"""Society V2 Stage C OFFLINE scale tests (mock caller): the 24x60 and 48x100
scenario rungs of the scale ladder, three/five real (model-voted offline)
assemblies, ring topology, 1 adversary each (c13 / d25).

Purpose (Stage C scale-jump gate, docs/STAGE_C_PREREGISTRATION.md E1-E4):
prove the verified Stage B machinery is *mechanically valid* at 2x and 4x
Stage B scale BEFORE spending live API (C-2 live is additionally gated on the
C-1 live run passing). Runs the real generated scenarios through the full
SocietyCampaign engine with deterministic scripted agents.

Pins per rung:
  * byte-identical replay (deterministic reducer holds at scale)
  * denominator: n*total committed actions (the sanctioned tick no longer
    suspends the adversary — he still acts and is measured that tick)
  * every assembly enacts exactly one amendment (versions = base + n)
  * adversary sanctioned exactly one tick (ON then OFF flip); rights restored
    by the final tick — but NOT suspended from the task tape
  * probe battery = 9 probe ticks x n agents x 3 channels
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from verdi.agents.client import ModelResult
from verdi.society.campaign import SocietyCampaign
from verdi.world.log import EventLog

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "experiments/scenarios"


def _scenario(name: str) -> dict:
    return json.loads((SCENARIOS_DIR / name).read_text(encoding="utf-8"))


def _agents(prefix: str, n: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{i:02d}" for i in range(1, n + 1))


class StageCScriptedCaller:
    """Deterministic fake ModelCaller for C-rung agent IDs.

    Mirrors StageBScriptedCaller: the designated adversary overdrafts once
    (rejected -> a one-tick sanction that restricts his transfers/deposits, but
    does NOT suspend him from the tape); everyone else emits a signal. The
    first sorted proposer proposes one amendment per assembly; everyone votes
    for it, so each assembly enacts exactly one.
    """

    def __init__(self, agent_ids: tuple[str, ...], adversary: str) -> None:
        self.agent_ids = agent_ids
        self.adversary = adversary
        self.proposer = sorted(agent_ids)[0]
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
            if voter == self.proposer:
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
        for aid in self.agent_ids:
            if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                actor = aid
                break
        assert actor is not None, f"could not identify actor in prompt: {content[:120]}"
        count = self.action_calls.get(actor, 0)
        self.action_calls[actor] = count + 1
        if actor == self.adversary and count == 0:
            payload = {"action_kind": "transfer", "arguments": {"to": sorted(self.agent_ids)[0],
                                                                "resource": "credits", "units": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake", trace_id=trace)


def _run(scenario: dict, tmp_path: Path) -> Any:
    n = len(scenario["agents"])
    prefix = scenario["agents"][0]["agent_id"][0]
    adversary = next(a["agent_id"] for a in scenario["agents"] if "[adversary]" in a["persona_summary"])
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path,
        caller=StageCScriptedCaller(_agents(prefix, n), adversary),
        run_seed=scenario["run_seed"], concurrency=4,
    )
    return asyncio.run(campaign.run())


# (scenario file, n agents, total ticks, assemblies, adversary, probe ticks)
RUNGS = [
    ("society_v2_stageC1.json", 24, 60, [15, 30, 45], "c13", 9),
    # C-2 reflection lands at 33 (total//3), adding two more probe ticks
    # (baseline, 4 pre-meeting, 5 assembly, reflection, post = 13).
    ("society_v2_stageC2.json", 48, 100, [20, 40, 60, 80, 95], "d25", 13),
]


@pytest.mark.parametrize("name,n,total,assemblies,adversary,probe_ticks", RUNGS,
                         ids=["C1-24x60", "C2-48x100"])
def test_stageC_offline_scale_invariants(tmp_path, name, n, total, assemblies, adversary, probe_ticks):
    scenario = _scenario(name)
    assert len(scenario["agents"]) == n and scenario["total_ticks"] == total
    assert scenario["assemblies"] == assemblies

    # F3 (Tranche 12d): probe_points must stay Stage-B compatible (list of
    # string labels). Probing is driven by lifecycle KEYS, but the field must
    # not drift into a format the engine/protocol docs do not define.
    for tick_key, meta in scenario["lifecycle"].items():
        pts = meta.get("probe_points", [])
        assert isinstance(pts, list) and all(isinstance(p, str) for p in pts), \
            f"{name}: probe_points at tick {tick_key} must be a list of str: {pts!r}"

    res = _run(scenario, tmp_path)

    # E1: deterministic reducer holds at scale (byte-identical replay + chain)
    assert res.replay_matches is True
    assert res.replay_log_identical is True
    log = EventLog.load_jsonl(tmp_path / "events.jsonl")
    log.verify()
    assert len(log) == len(res.events)

    # E2: denominator = n*total (the sanctioned tick no longer suspends the
    # adversary — he still acts and is measured that tick, so the full
    # agents x ticks actions are committed).
    n_actions = sum(1 for s in res.tape if s.kind == "action")
    n_advancing = sum(1 for s in res.tape if s.kind in ("action", "governance"))
    assert res.state.tick == n_advancing
    assert n_actions == n * total, f"{name}: n_actions={n_actions}"

    # rights integrity: the sanction still fires for exactly one tick
    # (ON flip then OFF), restored by end — but the agent is not suspended.
    flips = [s for s in res.tape if s.kind == "rights_change"]
    assert len(flips) == 2
    assert flips[0].rights == {"agent_id": adversary, "value": True}
    assert flips[1].rights == {"agent_id": adversary, "value": False}
    final = res.checkpoints[f"t{total - 1:02d}"]
    assert final.agents[adversary].under_sanction is False
    assert len(res.sanction_events) == 1
    assert res.sanction_events[0].get("subject_id") == adversary

    # E4: every assembly enacts exactly one amendment
    gov = [e for e in res.events if e.kind in ("amendment_enacted", "amendment_rejected")]
    assert len(gov) == len(assemblies)
    assert all(e.kind == "amendment_enacted" for e in gov)
    for t in assemblies:
        assert f"t{t:02d}" in res.checkpoints
    assert len(res.constitution_versions) == 1 + len(assemblies)

    # probe battery: 9 lifecycle probe ticks x n agents x 3 channels
    assert len(res.probes) == probe_ticks * n * 3, f"{name}: probes={len(res.probes)}"

    print(f"\n{name}: {n_actions} actions, {len(gov)} assemblies enacted, "
          f"{len(res.probes)} probes, replay OK, final_hash={res.final_state_hash}")
